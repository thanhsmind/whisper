# translator
Install python 
winget install --id Python.Python.3.12 -e
winget install --id Gyan.FFmpeg -e

CLI batch-transcriber for MP4 files using `whisper.cpp` built with Vulkan
(real GPU acceleration on AMD Radeon under Windows).

Output: one `.txt` per input MP4, grouped into 1-minute buckets, podcast-style:

```
# my-recording.mp4 -- Language: vi

[00:00 - 01:00]
<all speech in minute 0, joined>

[01:00 - 02:00]
<all speech in minute 1, joined>
```

---

## One-time setup

### 1. Install prerequisites

Open an **elevated PowerShell** and run whichever of these you don't already have:

```powershell
winget install --id Git.Git -e
winget install --id Kitware.CMake -e
winget install --id Python.Python.3.11 -e
winget install --id Gyan.FFmpeg -e
winget install --id KhronosGroup.VulkanSDK -e
winget install --id Microsoft.VisualStudio.2022.BuildTools -e --override "--add Microsoft.VisualStudio.Workload.VCTools --includeRecommended --quiet --wait"
```

Then **close and reopen** the terminal so the new PATH and `VULKAN_SDK` env var
are picked up.

### 2. Build whisper.cpp + download the model

From this directory:

```powershell
.\setup.ps1
```

This will:
- Verify all prereqs (and tell you exactly what's missing if not)
- Clone `ggml-org/whisper.cpp` into `_src/`
- Build `whisper-cli.exe` with the Vulkan backend (`-DGGML_VULKAN=1`)
- Copy the binary to `bin/`
- Download `ggml-large-v3-turbo.bin` (~1.6 GB) into `models/`

First build takes 5--15 minutes depending on your machine. Re-running `setup.ps1`
later just pulls + rebuilds incrementally.

---

## Usage

```powershell
python transcribe.py "C:\path\to\videos"
```

Options:

| Flag | Default | What it does |
|---|---|---|
| `--out PATH` | `./out` | Where to write `.txt` transcripts |
| `--model PATH` | `models/ggml-large-v3-turbo.bin` | ggml model file |
| `--bin PATH` | `bin/whisper-cli.exe` | whisper.cpp CLI binary |
| `--language CODE` | `auto` | e.g. `vi`, `en`, or `auto` to detect |
| `--threads N` | `8` | CPU threads for non-GPU work |
| `--device N` | `1` | Vulkan GPU index. On this machine: `0` = AMD Radeon 780M (currently unstable on the AMD driver -- crashes mid-inference), `1` = NVIDIA RTX 4060 Laptop. Run `bin\whisper-cli.exe --help` to see the live list. |
| `--keep-intermediate` | off | Keep the temp `.wav` + `.json` files |

The folder is scanned **recursively** for `*.mp4`. One `.txt` is produced per
input file. Existing transcripts are overwritten.

---

## How it works

```
   <folder>/*.mp4
        |
        v
   ffmpeg -ar 16000 -ac 1  -->  16 kHz mono WAV (temp)
        |
        v
   whisper-cli.exe (Vulkan)  --> JSON with per-segment timestamps
        |
        v
   group segments by start_time // 60s
        |
        v
   <out>/<name>.txt   (podcast format)
```

GPU usage is automatic: when the binary is built with `-DGGML_VULKAN=1`, it
picks up your Radeon via the Vulkan loader (`vulkan-1.dll` from your AMD
driver). No CUDA, no cuDNN.

---

## Troubleshooting

**`whisper-cli.exe` opens but says "no Vulkan device found"**
Update your GPU graphics driver to expose Vulkan 1.3.

**Process exits with `0xC0000409` / `STATUS_STACK_BUFFER_OVERRUN` mid-inference**
This is the AMD Vulkan driver crashing on `large-v3-turbo` with
flash-attention + matrix cores. Workaround: pass `--device 1` to use the
NVIDIA GPU instead (the default in this CLI is already `1`). If you only
have an AMD GPU, try `--model models/ggml-medium.bin` or open
`transcribe.py` and add `-fa 0` to the `run_whisper` cmd to disable flash
attention.

**Build fails at `cmake -B build -DGGML_VULKAN=1`**
The Vulkan SDK environment variable `VULKAN_SDK` isn't set. After installing
the SDK, log out / back in (or restart) and reopen the terminal.

**Build fails saying `cl.exe` not found**
You don't have the C++ workload installed. Re-run the VS Build Tools install
with the `Microsoft.VisualStudio.Workload.VCTools` component.

**Transcript is wrong language**
Pass `--language vi` (or any other ISO code) explicitly instead of `auto`.

**Want a smaller / faster model**
Replace the model file with another from
<https://huggingface.co/ggerganov/whisper.cpp/tree/main> --
e.g. `ggml-medium.bin` (~1.5 GB, ~2x faster) or `ggml-small.bin` (~470 MB).
Then point `--model` at it.
