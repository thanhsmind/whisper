# One-time setup for translator: build whisper.cpp with Vulkan + download model.
# Run from PowerShell in this directory:  .\setup.ps1
#
# What this does:
#   1. Checks for required tools (git, cmake, python, ffmpeg, Vulkan SDK, MSVC).
#      If anything is missing, prints the exact `winget install` commands and exits.
#   2. Clones (or pulls) ggml-org/whisper.cpp into _src/whisper.cpp
#   3. Builds whisper-cli.exe with Vulkan backend (release, multi-core)
#   4. Copies the binary + any DLLs into ./bin
#   5. Downloads ggml-large-v3-turbo.bin (~1.6 GB) into ./models if not present
#
# After this, run:  python transcribe.py "C:\path\to\videos"

$ErrorActionPreference = "Stop"
$root   = $PSScriptRoot
$src    = Join-Path $root "_src\whisper.cpp"
$bin    = Join-Path $root "bin"
$models = Join-Path $root "models"

function Test-RealCommand($name) {
    $c = Get-Command $name -ErrorAction SilentlyContinue
    if (-not $c) { return $false }
    # Reject Microsoft Store stubs (they live under WindowsApps and just open the Store).
    if ($c.Source -like "*\WindowsApps\*") { return $false }
    return $true
}

# 1. Prereq check ----------------------------------------------------------
$wingetCmds = [ordered]@{
    "git"        = 'winget install --id Git.Git -e'
    "cmake"      = 'winget install --id Kitware.CMake -e'
    "python"     = 'winget install --id Python.Python.3.11 -e'
    "ffmpeg"     = 'winget install --id Gyan.FFmpeg -e'
    "vulkan-sdk" = 'winget install --id KhronosGroup.VulkanSDK -e'
}

$missing = @()
foreach ($t in @("git", "cmake", "python", "ffmpeg")) {
    if (-not (Test-RealCommand $t)) { $missing += $t }
}
if (-not $env:VULKAN_SDK) { $missing += "vulkan-sdk" }

# MSVC C++ compiler (cl.exe). cmake's VS generator finds it via VS install,
# so just check that VS Build Tools are installed by querying vswhere.
$vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
$vsFound = $false
if (Test-Path $vswhere) {
    $vsPath = & $vswhere -latest -products * `
        -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
        -property installationPath 2>$null
    if ($vsPath) { $vsFound = $true }
}

if ($missing.Count -gt 0 -or -not $vsFound) {
    Write-Host ""
    Write-Host "Missing prerequisites detected." -ForegroundColor Yellow
    Write-Host "Run these in an elevated PowerShell, then restart your terminal:" -ForegroundColor Yellow
    Write-Host ""
    foreach ($m in $missing) { Write-Host "  $($wingetCmds[$m])" }
    if (-not $vsFound) {
        Write-Host '  winget install --id Microsoft.VisualStudio.2022.BuildTools -e --override "--add Microsoft.VisualStudio.Workload.VCTools --includeRecommended --quiet --wait"'
    }
    Write-Host ""
    Write-Host "After install: open a NEW terminal and re-run .\setup.ps1" -ForegroundColor Yellow
    exit 1
}

Write-Host "[1/5] Prerequisites OK." -ForegroundColor Green
Write-Host "      VULKAN_SDK = $env:VULKAN_SDK"
Write-Host "      VS install = $vsPath"

# 2. Clone / update whisper.cpp -------------------------------------------
if (-not (Test-Path $src)) {
    New-Item -ItemType Directory -Force -Path (Split-Path $src) | Out-Null
    Write-Host "[2/5] Cloning whisper.cpp..."
    git clone --depth 1 https://github.com/ggml-org/whisper.cpp.git $src
} else {
    Write-Host "[2/5] Updating whisper.cpp..."
    git -C $src pull --ff-only
}

# 3. Configure + build with Vulkan ----------------------------------------
Write-Host "[3/5] Configuring (Vulkan backend)..."
Push-Location $src
try {
    cmake -B build -DGGML_VULKAN=1 -DWHISPER_BUILD_EXAMPLES=ON -DWHISPER_BUILD_TESTS=OFF
    if ($LASTEXITCODE -ne 0) { throw "cmake configure failed" }

    Write-Host "[3/5] Building (Release)..."
    cmake --build build --config Release -j
    if ($LASTEXITCODE -ne 0) { throw "cmake build failed" }
} finally {
    Pop-Location
}

# 4. Copy artifacts -------------------------------------------------------
Write-Host "[4/5] Copying binaries to .\bin"
New-Item -ItemType Directory -Force -Path $bin | Out-Null

# whisper.cpp's CMake puts the exe either at build\bin\whisper-cli.exe (newer,
# flat layout) or build\bin\Release\whisper-cli.exe (older). Probe both.
$candidates = @(
    (Join-Path $src "build\bin\whisper-cli.exe"),
    (Join-Path $src "build\bin\Release\whisper-cli.exe")
)
$exePath = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $exePath) {
    throw "whisper-cli.exe not found in either: $($candidates -join ' OR '). Build may have failed."
}
$buildOut = Split-Path $exePath
Copy-Item -Path $exePath -Destination $bin -Force
# Copy any DLLs the build produced (ggml*.dll, whisper.dll, etc.)
# A static build won't produce any -- that's fine.
Get-ChildItem -Path $buildOut -Filter "*.dll" -ErrorAction SilentlyContinue |
    Copy-Item -Destination $bin -Force

# 5. Download model -------------------------------------------------------
New-Item -ItemType Directory -Force -Path $models | Out-Null
$modelFile = Join-Path $models "ggml-large-v3-turbo.bin"
if (Test-Path $modelFile) {
    Write-Host "[5/5] Model already present: $modelFile"
} else {
    $url = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin"
    Write-Host "[5/5] Downloading ggml-large-v3-turbo.bin (~1.6 GB) ..."
    Write-Host "      $url"
    # BITS transfer is faster + resumable than Invoke-WebRequest for large files
    try {
        Start-BitsTransfer -Source $url -Destination $modelFile
    } catch {
        Write-Host "BITS failed, falling back to Invoke-WebRequest..." -ForegroundColor Yellow
        Invoke-WebRequest -Uri $url -OutFile $modelFile -UseBasicParsing
    }
}

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "Test:  python transcribe.py `"C:\path\to\videos`""
