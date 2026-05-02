"""
Batch-transcribe MP4 files in a folder using whisper.cpp (Vulkan/AMD GPU).

Output: one .txt per input MP4, grouped into 1-minute buckets, podcast-style:

    # filename.mp4 -- Language: vi

    [00:00 - 01:00]
    <all speech in minute 0, joined>

    [01:00 - 02:00]
    <all speech in minute 1, joined>

Usage:
    python transcribe.py "C:\\path\\to\\videos"
    python transcribe.py ./videos --out ./out --threads 8
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_MODEL = ROOT / "models" / "ggml-large-v3-turbo.bin"
DEFAULT_BIN   = ROOT / "bin" / "whisper-cli.exe"
DEFAULT_OUT   = ROOT / "out"


def find_mp4s(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*.mp4") if p.is_file())


def ascii_safe_key(mp4: Path) -> str:
    # whisper-cli.exe reads argv as the system ANSI codepage, so Unicode paths
    # (Vietnamese, Chinese, emoji, etc.) come through as mojibake and the file
    # lookup fails. Hand it an ASCII-only path for the WAV/JSON intermediates.
    # Note: '.' is intentionally NOT in the allowed set -- otherwise Path.with_suffix
    # may chop the stem at an embedded dot when we build the .wav/.json paths.
    h = hashlib.sha1(str(mp4.resolve()).encode("utf-8")).hexdigest()[:12]
    stem = "".join(c if c.isascii() and (c.isalnum() or c in "_-") else "_"
                   for c in mp4.stem)
    stem = stem.strip("_-")[:40] or "file"
    return f"{stem}_{h}"


def require_tool(name: str) -> None:
    if shutil.which(name) is None:
        sys.exit(f"ERROR: '{name}' not found on PATH. Run setup.ps1 first.")


def has_audio_stream(src: Path) -> bool:
    """True iff the file has at least one audio stream (per ffprobe)."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error",
             "-select_streams", "a",
             "-show_entries", "stream=codec_type",
             "-of", "csv=p=0",
             str(src)],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return "audio" in out


def to_wav(src: Path, dst: Path) -> None:
    """Convert any media file to 16 kHz mono PCM s16le WAV (what whisper.cpp expects).
    Explicit audio mapping so a video-only file fails loudly here, not with a confusing
    'output has no stream' inside ffmpeg.
    """
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(src),
        "-vn",                  # drop video
        "-map", "0:a:0",        # take the first audio stream (errors if none)
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        str(dst),
    ]
    subprocess.run(cmd, check=True)


def run_whisper(wav: Path, model: Path, out_basename: Path, whisper_cli: Path,
                threads: int, language: str, device: int) -> None:
    """Invoke whisper-cli; produces <out_basename>.json next to it."""
    cmd = [
        str(whisper_cli),
        "-m", str(model),
        "-f", str(wav),
        "-l", language,
        "-oj",                       # JSON output
        "-of", str(out_basename),    # output base path (extension added by tool)
        "-t", str(threads),
        "-dev", str(device),         # Vulkan device index (run `whisper-cli -h` to see list)
        "-pp",                       # print progress
    ]
    subprocess.run(cmd, check=True)


def fmt_hms(total_seconds: int) -> str:
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def bucket_by_minute(segments: list[dict], bucket_ms: int = 60_000) -> dict[int, list[str]]:
    buckets: dict[int, list[str]] = defaultdict(list)
    for seg in segments:
        try:
            start_ms = seg["offsets"]["from"]
        except (KeyError, TypeError):
            continue
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        idx = int(start_ms) // bucket_ms
        buckets[idx].append(text)
    return buckets


def write_podcast_transcript(json_path: Path, txt_path: Path,
                             source_name: str, bucket_ms: int = 60_000) -> None:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    segments = data.get("transcription") or []
    lang = (data.get("result") or {}).get("language", "?")
    buckets = bucket_by_minute(segments, bucket_ms)

    lines: list[str] = [f"# {source_name} -- Language: {lang}", ""]
    for idx in sorted(buckets):
        start = idx * bucket_ms // 1000
        end = (idx + 1) * bucket_ms // 1000
        lines.append(f"[{fmt_hms(start)} - {fmt_hms(end)}]")
        lines.append(" ".join(buckets[idx]))
        lines.append("")
    txt_path.write_text("\n".join(lines), encoding="utf-8")


def process_one(mp4: Path, out_dir: Path, model: Path, whisper_cli: Path,
                threads: int, language: str, device: int,
                keep_intermediate: bool) -> bool:
    out_dir.mkdir(parents=True, exist_ok=True)
    # Intermediate files use an ASCII-only stem (whisper-cli ANSI argv issue).
    # Final transcript keeps the original Unicode stem (Python writes it directly).
    work_base = out_dir / ascii_safe_key(mp4)
    wav = work_base.with_suffix(".wav")
    json_p = work_base.with_suffix(".json")
    txt = out_dir / f"{mp4.stem}.txt"

    print(f"\n>> {mp4}")
    t0 = time.time()
    try:
        if not has_audio_stream(mp4):
            print("   [SKIP] no audio stream in this file")
            return False

        print("   [1/3] Extracting audio with ffmpeg...")
        to_wav(mp4, wav)

        print(f"   [2/3] Transcribing with whisper.cpp (Vulkan device {device})...")
        run_whisper(wav, model, work_base, whisper_cli, threads, language, device)

        print("   [3/3] Writing per-minute transcript...")
        write_podcast_transcript(json_p, txt, mp4.name)

        elapsed = time.time() - t0
        print(f"   [OK] {txt}  ({elapsed:.1f}s)")
        return True
    except subprocess.CalledProcessError as e:
        print(f"   [FAIL] {e}")
        return False
    finally:
        if not keep_intermediate:
            for p in (wav, json_p):
                if p.exists():
                    p.unlink()


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Batch-transcribe MP4 files into per-minute podcast-style transcripts.")
    ap.add_argument("folder", help="Directory to scan recursively for .mp4 files")
    ap.add_argument("--model", type=Path, default=DEFAULT_MODEL,
                    help=f"Path to ggml model (default: {DEFAULT_MODEL})")
    ap.add_argument("--bin", type=Path, default=DEFAULT_BIN,
                    help=f"Path to whisper-cli.exe (default: {DEFAULT_BIN})")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT,
                    help=f"Output directory (default: {DEFAULT_OUT})")
    ap.add_argument("--language", default="auto",
                    help="Language code or 'auto' for auto-detect (default: auto)")
    ap.add_argument("--threads", type=int, default=8,
                    help="CPU threads for the parts that aren't GPU-bound (default: 8)")
    ap.add_argument("--device", type=int, default=1,
                    help="Vulkan GPU device index. On this machine: 0=AMD Radeon 780M "
                         "(unstable on AMD driver), 1=NVIDIA RTX 4060 (default, recommended). "
                         "Run `bin\\whisper-cli.exe --help` to see the device list.")
    ap.add_argument("--keep-intermediate", action="store_true",
                    help="Keep generated .wav and .json files (default: delete them)")
    args = ap.parse_args()

    folder = Path(args.folder).resolve()
    if not folder.is_dir():
        sys.exit(f"ERROR: not a directory: {folder}")

    require_tool("ffmpeg")
    if not args.bin.exists():
        sys.exit(f"ERROR: whisper-cli not found at {args.bin}\n"
                 f"Run .\\setup.ps1 first.")
    if not args.model.exists():
        sys.exit(f"ERROR: model not found at {args.model}\n"
                 f"Run .\\setup.ps1 first.")

    mp4s = find_mp4s(folder)
    if not mp4s:
        print(f"No .mp4 files found under {folder}")
        return 0

    print(f"Found {len(mp4s)} MP4 file(s) under {folder}")
    print(f"Model:  {args.model.name}")
    print(f"Output: {args.out}")

    ok = 0
    for mp4 in mp4s:
        if process_one(mp4, args.out, args.model, args.bin,
                       args.threads, args.language, args.device,
                       args.keep_intermediate):
            ok += 1

    print(f"\nDone. {ok}/{len(mp4s)} succeeded.")
    return 0 if ok == len(mp4s) else 1


if __name__ == "__main__":
    raise SystemExit(main())
