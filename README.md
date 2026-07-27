<div align="center">

# VideoForge

**A desktop application that transforms FFmpeg into an intelligent, point-and-click video processing pipeline — featuring a custom computer-vision logo detector, multi-task batch processing, and [...]

[![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB?logo=python&logoColor=white)](https://python.org)
[![FFmpeg](https://img.shields.io/badge/FFmpeg-4.0%2B-007808?logo=ffmpeg&logoColor=white)](https://ffmpeg.org)
[![OpenCV](https://img.shields.io/badge/OpenCV-4.8%2B-5C3EE8?logo=opencv&logoColor=white)](https://opencv.org)
[![Tests](https://img.shields.io/badge/tests-216%20passed-brightgreen)]()
[![Coverage](https://img.shields.io/badge/ruff-0%20errors-success)]()
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)]()

</div>

---

## The Problem

Video editors, archivists, and media teams often need to batch-process hundreds of recordings: trim intros, remove broadcaster watermarks, re-encode for compatibility, and rename files sequentiall[...]

- **Error-prone** — one wrong flag ruins hours of encoding
- **Repetitive** — the same operations run again and again
- **Inaccessible** — non-technical team members can't use CLI tools
- **Unobservable** — no progress tracking, no batch management, no error recovery

## The Solution

VideoForge wraps FFmpeg's raw power into a clean, dark-themed desktop GUI built with **CustomTkinter**. Instead of memorizing flags and writing Bash scripts, users drag-drop files, toggle options,[...]

> Originally built to replace a pile of personal Bash scripts, VideoForge grew into a full desktop application demonstrating **classical computer vision, concurrent processing, and clean software [...]

---

## Key Features

### Computer-Vision Logo Detection
The standout feature. Instead of manually specifying watermark coordinates, VideoForge **finds them automatically** using a temporal-stability algorithm:

1. **Sample 15 frames** evenly across the video (skipping intro/outro fade regions)
2. **Stack frames into a NumPy array** and compute per-pixel temporal variance via `np.var(axis=0)`
3. **Threshold the variance map** — static pixels (logos) have near-zero variance; dynamic content changes constantly
4. **Morphological cleanup** (close gaps, remove noise via connected-components analysis)
5. **Contour detection + scoring** — candidates ranked by 70% stability, 20% corner-position fit, 10% size fit

**No deep learning, no API calls, no model weights** — just classical CV with OpenCV and NumPy. Detects a logo on a 1-hour episode in under 10 seconds.

Three detection backends are supported:
| Method | Approach | Use Case |
|---|---|---|
| **Temporal Stability** (default) | Per-pixel variance across frame stack | Broadcaster watermarks, news tickers |
| OpenCV Edges (legacy) | Canny edge detection + contour filter | Fallback / comparison |
| Google Cloud Vision (optional) | ML-based object detection | Complex multi-logo scenes |

### Flexible Trimming & Splitting
Three cut modes, all working on any video length — no need to know exact durations upfront:

| Mode | How it works |
|---|---|
| **Time** | Skip intro / cut outro with hours/minutes/seconds fields |
| **Markers** | Type start/end timestamps directly (`HH:MM:SS`, `MM:SS`, or plain seconds). Blank end = to end of video |
| **Split** | Divide video into N equal parts after optional trim. Each part becomes a separate output file (`video_part1.mp4`, `video_part2.mp4`, …) with zero-padded numbering |

Split mode is useful for breaking long recordings into episode-length segments or fitting files under size limits.

### Multi-Task Batch Processing
Run multiple task tabs simultaneously — each with its own file list, trim settings, encoding profile, and output folder. A worker pool processes files concurrently with per-file progress bars an[...]

### Visual Logo Picker
Click-drag a rectangle on a preview frame to manually select a watermark region. Coordinates convert from display resolution to original video resolution automatically. Copy-paste support for appl[...]

### Sequential Rename Plan
Automatically renames output files with zero-padded sequential numbering (`episode01.mp4`, `episode02.mp4`, …) — works for any batch size.

### Parallel Encoding + CPU Controls
A configurable thread pool (`1–8` workers) runs multiple FFmpeg encodes concurrently. Per-process thread limiting and OS priority control prevent CPU saturation while keeping the system responsi[...]

### One-Click `.exe` Packaging
PyInstaller bundles everything — including the FFmpeg binary — into a standalone Windows executable. No Python install required for end users.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    main.py (Entry Point)                 │
│              VideoForgeApp — 1400×900 window             │
│         Single-instance lock · FFmpeg availability       │
└──────────────────────┬──────────────────────────────────┘
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
┌─────────────┐ ┌────────────┐ ┌────────────┐
│   UI Layer  │ │   State    │ │ Processing │
│ (CustomTk)  │ │ Container  │ │   Engine   │
└──────┬──────┘ └──────┬─────┘ └──────┬─────┘
       │               │              │
       │     ┌─────────┴──────────┐   │
       │     │  Data Models       │   │
       │     │  (dataclasses)     │   │
       │     └────────────────────┘   │
       │                              │
┌──────┴──────────────────────────────┴──────┐
│              Core Modules                   │
├────────────┬────────────┬───────────────────┤
│  Video     │  Logo      │   Parallel        │
│  Processor │  Detectors │   Processor       │
│ (FFmpeg)   │ (OpenCV)   │  (ThreadPool)     │
└────────────┴────────────┴───────────────────┘
```

### Design Principles
- **Single responsibility** — each module handles one concern (encoding, detection, UI, state)
- **Graceful degradation** — FFmpeg/ffprobe fallbacks, optional AI detector, drag-drop optional
- **Thread-safe state** — central `AppState` with pub/sub log callbacks
- **Testable** — CV detectors tested with synthetic NumPy frame stacks (no real video needed)
- **Clean exception hierarchy** — domain-specific errors (`VideoReadError`, `DetectionFailedError`, `DetectionCancelledError`)

---

## Encoding Profiles

Four predefined profiles tuned for real-world use cases:

| Profile | Preset | CRF | Audio | Target |
|---|---|---|---|---|
| **Universal Compatibility** | slow | 23 | AAC 192k | All devices — baseline H.264, level 3.1 |
| **High Quality** | slow | 18 | AAC 256k | Archival quality |
| **Smaller File Size** | fast | 28 | AAC 128k | Bandwidth-constrained sharing |
| **iOS Optimized** | slow | 22 | AAC 192k | iPhone, iPad, Apple TV |

All profiles use `libx264`, `yuv420p` pixel format, and `+faststart` for web streaming.

---

## Performance

VideoForge's parallel worker pool delivers measurable speedups over sequential processing. The benchmark below processes **8 video clips** (12s each) with identical encoding settings (`libx264 -p[...]

| Approach | Time | Speedup | Faster |
|---|---:|---:|---:|
| Manual sequential (1 file at a time) | 4.5s | 1.00x | — |
| **VideoForge (4 parallel workers)** | **1.0s** | **4.4x** | **77%** |
| **VideoForge (8 parallel workers)** | **0.9s** | **5.0x** | **80%** |

> Benchmark run on a 12-core CPU, 2 threads per FFmpeg process (capped via `-threads`). Run it yourself: `python benchmark.py`

**Real-world impact:** For a typical 50-episode batch at ~3 min/episode encoding time, this translates to **~2 hours instead of ~2.5 hours** — and the user doesn't need to babysit the terminal.

---

## Tech Stack

| Area | Technology | Why |
|---|---|---|
| **Language** | Python 3.8+ | Cross-platform, rich media ecosystem |
| **GUI** | CustomTkinter + tkinterdnd2 | Modern dark UI with native drag-and-drop |
| **Video Engine** | FFmpeg (via subprocess) | Industry-standard encoder, maximum format support |
| **Logo Detection** | OpenCV + NumPy | Classical CV — no ML model weights needed |
| **Imaging** | Pillow | Frame extraction for logo picker |
| **Packaging** | PyInstaller 6.x | Standalone `.exe` with bundled FFmpeg |
| **Testing** | pytest + pytest-cov | 216 tests (unit + integration) |
| **Linting** | ruff | Zero errors enforced |

---

## Getting Started

### Prerequisites
- **Python 3.8+**
- **FFmpeg** installed and on your system `PATH`

```bash
# macOS
brew install ffmpeg

# Debian / Ubuntu
sudo apt install ffmpeg

# Windows
choco install ffmpeg
# or download from https://ffmpeg.org/download.html
```

Verify: `ffmpeg -version`

### Installation

```bash
git clone https://github.com/FayezL/-FFmpeg-Video-Automation-Dashboard.git
cd -FFmpeg-Video-Automation-Dashboard

python -m venv venv
# Windows:  venv\Scripts\activate
# macOS/Linux:  source venv/bin/activate

pip install -r requirements.txt
```

### Run

```bash
python main.py
```

Or install as a package and use the console entry point:

```bash
pip install -e .
videoforge
```

### Optional: AI Logo Detection

For the Google Cloud Vision detector backend:

```bash
pip install -r requirements-ai.txt
```

Set `GOOGLE_APPLICATION_CREDENTIALS` to your service-account JSON. See [`docs/LOGO_DETECTION_AI_OPTIONS.md`](docs/LOGO_DETECTION_AI_OPTIONS.md) for setup.

---

## Usage

1. **Add task tabs** — start with Task 1 and Task 2; click "Add task tab" for more
2. **Drop or select files** — drag video files directly into the window, or browse
3. **Configure options** per task:
   - Cut mode: Time (skip intro/cut outro), Markers (type timestamps), or Split (divide into N parts)
   - Encoding profile (Universal, High Quality, Small File, iOS)
   - Delogo filter (auto-detect, visual picker, or manual coordinates)
   - Output folder, format, filename prefix/suffix
   - Sequential rename plan
4. **Click Start** — live progress per file, FFmpeg log output, stop button for cancellation

---

## Gallery

### Task-Based Batch Workflow
Configure multiple independent processing tasks with separate file lists, trim modes, and encoding profiles.

![Batch Processor — Task 1](docs/screenshots/task1-config.png)

### Real-Time Progress Tracking
Live FFmpeg output, per-file progress bars, and concurrent encoding with worker pool management.

![FFmpeg Encoding in Progress](docs/screenshots/ffmpeg-progress.png)

### Visual Logo Picker
Click-drag a rectangle to manually select watermark regions. Coordinates automatically convert from display resolution to original video resolution. Tested on everything from indie films to classic cinema.

![Logo Picker — Manual Selection](docs/screenshots/logo-picker.png)

---

## Packaging a Windows Executable

```bash
pyinstaller --clean --noconfirm src/packaging/VideoForge.spec
# → dist/VideoForge.exe  (standalone, no Python required)
```

The build bundles the FFmpeg binary, applies UPX compression, and excludes unused heavy packages (matplotlib, scipy, pandas). See [`docs/BUILDING.md`](docs/BUILDING.md) for details.

---

## Testing & Quality

```bash
# Full test suite (unit + integration)
pytest

# Lint
ruff check .
```

| Metric | Value |
|---|---|
| Test functions | **216 passed**, 1 skipped |
| Test files | 23 (19 unit + 4 integration) |
| Lines of test code | ~2,583 |
| Lint errors | **0** |
| Source files | 22 modules + entry point |

Integration tests run real FFmpeg encodes to verify actual output. The CV detector is tested with **synthetic NumPy frame stacks** — deterministic, fast, and no video files required.

---

## Project Structure

```
main.py                          # Entry point — VideoForgeApp
src/
├── video_processor.py           # FFmpeg orchestration, filters, progress tracking
├── parallel_processor.py        # Thread-pool concurrent encoder
├── logo_detector_temporal.py    # Temporal-stability CV detector (default)
├── logo_detector.py             # Legacy edge-based detector
├── logo_detector_vision.py      # Optional Google Cloud Vision detector
├── logo_detection_utils.py      # Shared CV filter helpers
├── logo_position_utils.py       # Coordinate parsing & frame extraction
├── detection_profiles.py        # JSON profile persistence
├── templates.py                 # Processing-template manager
├── data_models.py               # Detection/config/profile dataclasses
├── state.py                     # Central application-state container
├── exceptions.py                # Domain exception hierarchy
├── packaging/                   # PyInstaller spec + build script
└── ui/                          # CustomTkinter frames
    ├── batch_processor.py       # Multi-task batch workflow (largest module)
    ├── single_processor.py      # Single-file processing
    ├── logo_picker.py           # Visual click-drag logo selector
    ├── settings_panel.py        # CPU/parallel/FFmpeg configuration
    ├── logs_panel.py            # Live FFmpeg output
    └── drag_drop.py             # tkinterdnd2 file handler
tests/                           # 23 test files — unit + integration
docs/                            # Guides and design specs
specs/                           # Historical feature design records
```

---

## Engineering Highlights

This project demonstrates several skills relevant to professional software engineering:

- **Classical Computer Vision** — designed and implemented a temporal-variance logo-detection algorithm from scratch using NumPy array operations and OpenCV morphology, replacing a legacy edge-[...]
- **Concurrent Processing** — built a thread-safe worker pool with queue-based task distribution, graceful shutdown, and active-process tracking
- **Subprocess Orchestration** — real-time FFmpeg progress parsing via stderr regex monitoring, dual execution paths (ffmpeg-python + raw subprocess) with automatic fallback
- **Test-Driven Development** — 216 tests including integration tests that verify actual FFmpeg output, and synthetic-frame CV tests that run deterministically without video files
- **Cross-Platform Packaging** — PyInstaller spec that bundles FFmpeg, applies UPX compression, and ships a double-clickable `.exe` with no runtime dependencies
- **Clean Architecture** — single-responsibility modules, domain-specific exception hierarchy, pub/sub logging, and a central state container with computed properties

---

## Documentation

- [`docs/BUILDING.md`](docs/BUILDING.md) — Building the standalone executable
- [`docs/PACKAGE_GUIDE.md`](docs/PACKAGE_GUIDE.md) — Packaging walkthrough
- [`docs/TRIM_MODES_GUIDE.md`](docs/TRIM_MODES_GUIDE.md) — Trim modes reference
- [`docs/LOGO_DETECTION_AI_OPTIONS.md`](docs/LOGO_DETECTION_AI_OPTIONS.md) — Detection methods & Cloud Vision setup
- [`specs/`](specs/) — Historical design documents for each feature

---

## License

[MIT](LICENSE) — Copyright © 2026 FayezL
