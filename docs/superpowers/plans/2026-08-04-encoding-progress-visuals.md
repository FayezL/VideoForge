# Encoding Progress Visuals + Portfolio Contact — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make batch video encoding feel alive with a live per-file progress bar, an aggregate batch bar + summary, per-file percent and ffmpeg `speed=` readout — and add a developer contact email to the About section.

**Architecture:** Reuse the existing percent plumbing that already runs from `monitor_progress` → `on_progress` → `ProcessingFile.progress`. Extend it to also carry ffmpeg's `speed=` value, fix a latent multi-`\r` under-report bug, normalize `ProcessingFile.progress` to a 0.0–1.0 fraction at the `process_queue` boundary, then add a GUI-side tick loop (`after(150ms)`) that updates persistent per-row widgets in place and an aggregate header bar. Pure helpers (ffmpeg-stats parser, batch-aggregate math) are extracted to module scope for unit testing.

**Tech Stack:** Python 3.8+ (use `typing.Tuple`, not PEP 585), CustomTkinter, pytest, ruff. FFmpeg is driven via `subprocess.Popen`.

**Spec:** `docs/superpowers/specs/2026-08-04-encoding-progress-visuals-design.md`

---

## File Structure

| File | Responsibility | Change type |
|---|---|---|
| `src/video_processor.py` | FFmpeg orchestration; module-level stats parser; `monitor_progress` rewrite | Modify |
| `src/parallel_processor.py` | Worker pool; forward `speed` through progress callback | Modify |
| `src/state.py` | `ProcessingFile` dataclass (+ `speed` field, normalized semantics); `compute_batch_progress` helper | Modify |
| `src/ui/batch_processor.py` | Aggregate header bar+label; persistent per-row widget refs; `_tick_progress` loop | Modify |
| `src/ui/single_processor.py` | Add `speed` label; thread speed through `_update_progress` | Modify |
| `src/ui/settings_panel.py` | Append developer email to About text | Modify |
| `tests/test_progress_parsing.py` | Unit tests for ffmpeg-stats parser | Create |
| `tests/test_batch_progress.py` | Unit tests for `compute_batch_progress` | Create |
| `tests/test_parallel_processor.py` | Extend `test_callbacks_invoked` to cover speed + fraction storage | Modify |

**Normalization rule (applies everywhere):**
- `ProcessingFile.progress` is **always** a fraction in `[0.0, 1.0]`.
- The `on_progress` *wire* between `monitor_progress` and the storage boundary carries `percent` in `0–100` plus an optional `speed` float. Conversion `percent / 100.0` happens in exactly one place: the `on_progress` closure inside `VideoProcessor.process_queue`.

---

## Task 1: FFmpeg stats parser (TDD)

**Files:**
- Modify: `src/video_processor.py:1-9` (imports), `src/video_processor.py:14-35` (add module-level helper block near other module constants)
- Create: `tests/test_progress_parsing.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_progress_parsing.py`:

```python
"""Unit tests for the FFmpeg stats-line parser."""
from src.video_processor import parse_ffmpeg_progress, parse_ffmpeg_line


SAMPLE = (
    "frame=  123 fps= 25 q=23.0 size=     256kB time=00:00:04.92 bitrate="
    " 425.2kbits/s speed=2.3x"
)


def test_parse_basic_line():
    # 4.92s of a 10s source -> 49.2%
    pct, spd = parse_ffmpeg_progress(SAMPLE, total_duration=10.0)
    assert abs(pct - 49.2) < 0.01
    assert spd == 2.3


def test_speed_na_returns_none():
    line = SAMPLE.replace("speed=2.3x", "speed=N/A")
    pct, spd = parse_ffmpeg_progress(line, total_duration=10.0)
    assert pct is not None
    assert spd is None


def test_no_time_returns_none_percent():
    line = "Press [q] to stop, [?] for help"
    pct, spd = parse_ffmpeg_progress(line, total_duration=10.0)
    assert pct is None
    assert spd is None


def test_zero_duration_returns_none_percent():
    pct, spd = parse_ffmpeg_progress(SAMPLE, total_duration=0.0)
    assert pct is None
    assert spd == 2.3  # speed is independent of duration


def test_percent_clamped_to_100():
    # time exceeds duration (can happen with minor drift)
    line = SAMPLE.replace("time=00:00:04.92", "time=00:00:12.00")
    pct, _ = parse_ffmpeg_progress(line, total_duration=10.0)
    assert pct == 100.0


def test_parse_line_splits_carriage_returns_and_keeps_latest():
    # A single stdout "line" can contain multiple \r-separated updates.
    # The whole run only advances past 4.92s and 6.00s; latest wins.
    blob = SAMPLE + "\r" + SAMPLE.replace("time=00:00:04.92", "time=00:00:06.00").replace(
        "speed=2.3x", "speed=1.5x"
    )
    pct, spd = parse_ffmpeg_line(blob, total_duration=10.0)
    assert abs(pct - 60.0) < 0.01
    assert spd == 1.5


def test_parse_line_returns_none_when_no_stats():
    pct, spd = parse_ffmpeg_line("nothing useful here", total_duration=10.0)
    assert pct is None
    assert spd is None
```

- [ ] **Step 2: Run tests to verify they fail (ImportError)**

Run: `pytest tests/test_progress_parsing.py -v`
Expected: FAIL — `ImportError: cannot import name 'parse_ffmpeg_progress'`

- [ ] **Step 3: Add the parser to `src/video_processor.py`**

At the top of `src/video_processor.py`, add `import re` and `import time` to the existing import block (currently lines 5–9):

```python
import subprocess
import json
import os
import re
import time
from typing import Optional, Dict, Callable, List, Tuple
import threading
```

Then, after the imports / `try: import ffmpeg` block (around line 35, after the `HAS_FFMPEG_PYTHON` machinery), add the module-level patterns and helpers:

```python
_TIME_PATTERN = re.compile(r"time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})")
_SPEED_PATTERN = re.compile(r"speed=\s*([\d.]+)x")


def parse_ffmpeg_progress(
    text: str, total_duration: Optional[float]
) -> Tuple[Optional[float], Optional[float]]:
    """Parse a single ffmpeg stats chunk.

    Args:
        text: One stats chunk. Must NOT contain embedded carriage returns —
            split on ``\\r`` and call this per chunk (see :func:`parse_ffmpeg_line`).
        total_duration: Total source duration in seconds, used to compute percent.
            If ``None`` or ``<= 0``, percent is ``None``.

    Returns:
        ``(percent, speed)`` where ``percent`` is in 0–100 (or ``None`` if no
        ``time=`` was found or duration is unknown) and ``speed`` is the ffmpeg
        multiplier (or ``None``; ``speed=N/A`` does not match the regex).
    """
    percent: Optional[float] = None
    speed: Optional[float] = None

    time_match = _TIME_PATTERN.search(text)
    if time_match and total_duration and total_duration > 0:
        hours, minutes, seconds, centiseconds = map(int, time_match.groups())
        current = hours * 3600 + minutes * 60 + seconds + centiseconds / 100.0
        percent = min(100.0, (current / total_duration) * 100.0)

    speed_match = _SPEED_PATTERN.search(text)
    if speed_match:
        try:
            speed = float(speed_match.group(1))
        except ValueError:
            speed = None

    return percent, speed


def parse_ffmpeg_line(
    line: str, total_duration: Optional[float]
) -> Tuple[Optional[float], Optional[float]]:
    """Parse a full stdout line that may contain multiple ``\\r``-separated chunks.

    ffmpeg emits progress stats using carriage returns within a single line;
    iterating ``for line in process.stdout`` yields the whole run joined by
    ``\\r``. Returns the latest non-None percent and speed seen across chunks.
    """
    latest_percent: Optional[float] = None
    latest_speed: Optional[float] = None
    for chunk in line.split("\r"):
        pct, spd = parse_ffmpeg_progress(chunk, total_duration)
        if pct is not None:
            latest_percent = pct
        if spd is not None:
            latest_speed = spd
    return latest_percent, latest_speed
```

Also **remove** the now-duplicate `import re` that currently lives inside `_process_with_subprocess` at line 436 (it becomes unused there once Step 3 of Task 2 lands — but remove it now since the module-level import covers it):

```python
    def _process_with_subprocess(
        self,
        input_path: str,
        output_path: str,
        duration: Optional[float],
        start_time: float,
        on_progress: Optional[Callable[[float], None]],
        on_log: Optional[Callable[[str], None]],
    ) -> Tuple[bool, Optional[str]]:
        """Process using subprocess with progress tracking"""
        # (the old `import re` line is removed — re is now imported at module scope)

        cmd = ["ffmpeg", "-i", input_path]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_progress_parsing.py -v`
Expected: PASS — all 7 tests green.

- [ ] **Step 5: Lint**

Run: `ruff check src/video_processor.py tests/test_progress_parsing.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/video_processor.py tests/test_progress_parsing.py
git commit -m "feat(video): add ffmpeg stats parser with speed and multi-\\r handling"
```

---

## Task 2: Wire `monitor_progress` to the new parser (speed + throttle + \r fix)

**Files:**
- Modify: `src/video_processor.py:426-434` (signature), `src/video_processor.py:504-528` (`monitor_progress` + final flush)

- [ ] **Step 1: Widen the `on_progress` type on `_process_with_subprocess`**

In `src/video_processor.py`, change the signature at line 432 from:

```python
        on_progress: Optional[Callable[[float], None]],
```

to:

```python
        on_progress: Optional[Callable[[float, Optional[float]], None]],
```

Do the same for `process_video`'s own `on_progress` parameter (search the file for its signature and update the type annotation identically). The callable may always be invoked as `on_progress(percent, speed)`.

- [ ] **Step 2: Replace `monitor_progress` (lines 504–519)**

Replace the existing inner function:

```python
        # Monitor progress and collect output
        def monitor_progress():
            nonlocal output_lines
            time_pattern = re.compile(r"time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})")
            for line in process.stdout:
                output_lines.append(line)
                if on_log:
                    on_log(line)
                if total_duration and on_progress:
                    match = time_pattern.search(line)
                    if match:
                        hours, minutes, seconds, centiseconds = map(int, match.groups())
                        current_time = (
                            hours * 3600 + minutes * 60 + seconds + centiseconds / 100.0
                        )
                        percent = min(100.0, (current_time / total_duration) * 100.0)
                        on_progress(percent)
```

with:

```python
        # Monitor progress and collect output. Runs on its own daemon thread.
        def monitor_progress():
            nonlocal output_lines
            last_emit_time = 0.0
            last_emitted_percent = -1.0
            last_emitted_speed: Optional[float] = None
            latest_percent: Optional[float] = None
            latest_speed: Optional[float] = None
            for line in process.stdout:
                output_lines.append(line)
                if on_log:
                    on_log(line)
                if not (on_progress and total_duration):
                    continue
                pct, spd = parse_ffmpeg_line(line, total_duration)
                if pct is not None:
                    latest_percent = pct
                if spd is not None:
                    latest_speed = spd
                if latest_percent is None and latest_speed is None:
                    continue
                now = time.monotonic()
                changed = (
                    latest_percent != last_emitted_percent
                    or latest_speed != last_emitted_speed
                )
                if changed or (now - last_emit_time) >= 0.25:
                    on_progress(
                        latest_percent if latest_percent is not None else 0.0,
                        latest_speed,
                    )
                    last_emitted_percent = latest_percent
                    last_emitted_speed = latest_speed
                    last_emit_time = now
```

- [ ] **Step 3: Update the final 100% flush (line 527–528)**

Replace:

```python
        if process.returncode == 0:
            if on_progress:
                on_progress(100.0)
```

with:

```python
        if process.returncode == 0:
            if on_progress:
                on_progress(100.0, None)
```

- [ ] **Step 4: Verify parser tests still pass and lint**

Run: `pytest tests/test_progress_parsing.py tests/test_split_mode.py tests/test_cpu_settings.py -v`
Expected: PASS (no behavioral regression in existing subprocess-path tests).

Run: `ruff check src/video_processor.py`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add src/video_processor.py
git commit -m "feat(video): emit speed in on_progress and throttle updates; fix multi-\\r under-report"
```

---

## Task 3: Data model + thread `speed` end-to-end, normalize to fraction

**Files:**
- Modify: `src/state.py:35-48` (`ProcessingFile`)
- Modify: `src/parallel_processor.py:46-48, 50-55, 194-196` (callback type + forward speed)
- Modify: `src/video_processor.py:587-591` (`process_queue` `on_progress` closure — the normalization boundary)
- Modify: `tests/test_parallel_processor.py:131-160` (extend `test_callbacks_invoked`)

- [ ] **Step 1: Add `speed` field to `ProcessingFile`**

In `src/state.py`, edit the dataclass (lines 35–48) to:

```python
@dataclass
class ProcessingFile:
    """Represents a file being processed.

    ``progress`` is always a fraction in [0.0, 1.0]. ``speed`` is ffmpeg's
    encoding-speed multiplier (e.g. 2.3 means 2.3x realtime) or ``None`` when
    not currently known.
    """

    id: str
    path: str
    name: str
    status: FileStatus = FileStatus.PENDING
    progress: float = 0.0
    speed: Optional[float] = None
    error: Optional[str] = None
    # Per-file cut times (override global settings if set)
    use_custom_cut: bool = False
    custom_cut_start_seconds: Optional[float] = None  # Start time for this file
    custom_cut_end_seconds: Optional[float] = None  # End time (None = to end)
```

- [ ] **Step 2: Widen the progress-callback type in `ParallelProcessor`**

In `src/parallel_processor.py`, change the stored-callback type at line 48 from:

```python
        self._on_progress: Optional[Callable[[str, float], None]] = None
```

to:

```python
        self._on_progress: Optional[Callable[[str, float, Optional[float]], None]] = None
```

And the `process_batch` parameter at line 55 from:

```python
        on_progress: Optional[Callable[[str, float], None]] = None
```

to:

```python
        on_progress: Optional[Callable[[str, float, Optional[float]], None]] = None
```

(Update the docstring at line 64 to: `on_progress: Callback for progress updates (file_id, percent, speed)`.)

- [ ] **Step 3: Forward `speed` from the worker's `progress_callback`**

In `src/parallel_processor.py`, replace the `progress_callback` definition at lines 194–196:

```python
                try:
                    # Create progress callback for this file
                    def progress_callback(percent: float):
                        if self._on_progress:
                            self._on_progress(file.id, percent)
```

with:

```python
                try:
                    # Create progress callback for this file
                    def progress_callback(percent: float, speed: Optional[float] = None):
                        if self._on_progress:
                            self._on_progress(file.id, percent, speed)
```

- [ ] **Step 4: Normalize + store at the `process_queue` boundary**

In `src/video_processor.py`, replace the `on_progress` closure inside `process_queue` (lines 587–591):

```python
        def on_progress(file_id: str, percent: float):
            for f in files:
                if f.id == file_id:
                    f.progress = percent
                    break
```

with:

```python
        def on_progress(file_id: str, percent: float, speed: Optional[float] = None):
            for f in files:
                if f.id == file_id:
                    # Normalize percent (0-100 on the wire) to a 0.0-1.0 fraction
                    # at the single storage boundary.
                    f.progress = max(0.0, min(1.0, percent / 100.0))
                    f.speed = speed
                    break
```

- [ ] **Step 5: Extend `test_callbacks_invoked` to cover speed + fraction**

In `tests/test_parallel_processor.py`, replace the body of `test_callbacks_invoked` (lines 131–160) with:

```python
    def test_callbacks_invoked(self, mock_state, mock_video_processor):
        """Test that callbacks are invoked correctly, including speed."""
        processor = ParallelProcessor(mock_state, mock_video_processor, max_workers=1)

        file = ProcessingFile(id="1", path="/path/video.mp4", name="video.mp4")

        # Set up callbacks
        start_callback = Mock()
        complete_callback = Mock()
        progress_callback = Mock()

        # Mock quick processing
        mock_video_processor.process_video.return_value = None

        processor.process_batch(
            [file],
            on_file_start=start_callback,
            on_file_complete=complete_callback,
            on_progress=progress_callback,
        )

        # Wait for processing to complete
        time.sleep(0.5)

        # Verify callbacks were called
        start_callback.assert_called_once_with(file)
        complete_callback.assert_called_once()

        # Clean up
        processor.stop()

    def test_progress_callback_carries_speed_and_normalizes(
        self, mock_state, mock_video_processor
    ):
        """on_progress forwards speed, and the process_queue closure stores a fraction."""
        from src.video_processor import VideoProcessor

        # Simulate the on_progress closure that process_queue builds.
        files = [ProcessingFile(id="1", path="/p/a.mp4", name="a.mp4")]

        captured = {}

        def on_progress(file_id, percent, speed=None):
            captured["percent"] = percent
            captured["speed"] = speed
            for f in files:
                if f.id == file_id:
                    f.progress = max(0.0, min(1.0, percent / 100.0))
                    f.speed = speed

        # Wire through a real ParallelProcessor using a fake processor that
        # invokes the per-file progress callback with speed.
        class FakeVP:
            def _get_output_path(self, *a, **k):
                return "/out.mp4"

            def process_video(self, *args, on_progress=None, **kwargs):
                on_progress(47.0, 2.3)  # percent + speed
                return True, None

        processor = ParallelProcessor(mock_state, FakeVP(), max_workers=1)
        processor.process_batch(files, on_progress=on_progress)
        time.sleep(0.4)
        processor.stop()

        assert captured["percent"] == 47.0
        assert captured["speed"] == 2.3
        assert abs(files[0].progress - 0.47) < 1e-6  # normalized to fraction
        assert files[0].speed == 2.3
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_parallel_processor.py -v`
Expected: PASS (both the existing and the new test).

Run: `ruff check src/state.py src/parallel_processor.py src/video_processor.py tests/test_parallel_processor.py`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/state.py src/parallel_processor.py src/video_processor.py tests/test_parallel_processor.py
git commit -m "feat(state): add ProcessingFile.speed; thread speed through callbacks; normalize progress to fraction"
```

---

## Task 4: Batch aggregate helper (TDD)

**Files:**
- Modify: `src/state.py` (append `compute_batch_progress` after the `ProcessingFile` dataclass, before `CutMode`/etc. usages — anywhere at module scope is fine)
- Create: `tests/test_batch_progress.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_batch_progress.py`:

```python
"""Unit tests for compute_batch_progress."""
from src.state import ProcessingFile, FileStatus, compute_batch_progress


def _file(status, progress=0.0, speed=None):
    return ProcessingFile(
        id=status.name + str(progress),
        path="/p",
        name="x",
        status=status,
        progress=progress,
        speed=speed,
    )


def test_empty_list():
    overall, avg, completed, total = compute_batch_progress([])
    assert overall == 0.0
    assert avg is None
    assert completed == 0
    assert total == 0


def test_all_pending():
    files = [_file(FileStatus.PENDING) for _ in range(4)]
    overall, avg, completed, total = compute_batch_progress(files)
    assert overall == 0.0
    assert avg is None
    assert completed == 0
    assert total == 4


def test_all_completed():
    files = [_file(FileStatus.COMPLETED, progress=1.0) for _ in range(4)]
    overall, avg, completed, total = compute_batch_progress(files)
    assert overall == 1.0
    assert completed == 4
    assert total == 4


def test_mixed_average():
    # 2 completed (1.0 each), 1 processing at 0.5, 1 pending (0.0) -> 2.5/4 = 0.625
    files = [
        _file(FileStatus.COMPLETED, progress=1.0),
        _file(FileStatus.COMPLETED, progress=1.0),
        _file(FileStatus.PROCESSING, progress=0.5, speed=2.0),
        _file(FileStatus.PENDING),
    ]
    overall, avg, completed, total = compute_batch_progress(files)
    assert abs(overall - 0.625) < 1e-6
    assert avg == 2.0
    assert completed == 2
    assert total == 4


def test_error_counts_as_finished_for_overall():
    # An error file is "done" (no more progress coming) -> contributes 1.0
    files = [
        _file(FileStatus.COMPLETED, progress=1.0),
        _file(FileStatus.ERROR, progress=1.0),
    ]
    overall, avg, completed, total = compute_batch_progress(files)
    assert overall == 1.0
    assert completed == 1  # only successes counted here
    assert total == 2


def test_avg_speed_ignores_none_and_non_processing():
    files = [
        _file(FileStatus.PROCESSING, progress=0.3, speed=2.0),
        _file(FileStatus.PROCESSING, progress=0.4, speed=None),  # ignored for avg
        _file(FileStatus.COMPLETED, progress=1.0, speed=5.0),   # ignored
    ]
    _, avg, _, _ = compute_batch_progress(files)
    assert avg == 2.0


def test_avg_speed_none_when_no_active_speeds():
    files = [_file(FileStatus.PROCESSING, progress=0.3, speed=None)]
    _, avg, _, _ = compute_batch_progress(files)
    assert avg is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_batch_progress.py -v`
Expected: FAIL — `ImportError: cannot import name 'compute_batch_progress'`.

- [ ] **Step 3: Implement `compute_batch_progress` in `src/state.py`**

Add at module scope in `src/state.py` (immediately after the `ProcessingFile` dataclass):

```python
def compute_batch_progress(
    files: "List[ProcessingFile]",
) -> "Tuple[float, Optional[float], int, int]":
    """Compute aggregate batch progress.

    Returns ``(overall_fraction, avg_speed, completed_count, total_count)``.

    - ``overall_fraction`` is in [0.0, 1.0]. Each file contributes 1/N where
      N = len(files); COMPLETED and ERROR files contribute 1.0 (the work is
      done either way), PROCESSING files contribute ``file.progress``
      (expected to already be a 0.0–1.0 fraction), PENDING files contribute 0.0.
    - ``avg_speed`` is the mean of ``file.speed`` over PROCESSING files whose
      speed is not None, or None if there are none.
    - ``completed_count`` counts only COMPLETED files (successes).
    """
    total = len(files)
    if total == 0:
        return 0.0, None, 0, 0

    contribution = 0.0
    completed = 0
    active_speeds: List[float] = []
    for f in files:
        if f.status == FileStatus.COMPLETED or f.status == FileStatus.ERROR:
            contribution += 1.0
            if f.status == FileStatus.COMPLETED:
                completed += 1
        elif f.status == FileStatus.PROCESSING:
            contribution += max(0.0, min(1.0, f.progress))
            if f.speed is not None:
                active_speeds.append(f.speed)
        # PENDING contributes 0.0

    overall = contribution / total
    avg = sum(active_speeds) / len(active_speeds) if active_speeds else None
    return overall, avg, completed, total
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_batch_progress.py -v`
Expected: PASS — all 7 tests green.

Run: `ruff check src/state.py tests/test_batch_progress.py`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add src/state.py tests/test_batch_progress.py
git commit -m "feat(state): add compute_batch_progress aggregate helper"
```

---

## Task 5: Batch UI — aggregate header bar + summary label

**Files:**
- Modify: `src/ui/batch_processor.py:8` (imports), `src/ui/batch_processor.py:1037-1069` (`_create_file_section`)

- [ ] **Step 1: Widen typing imports**

In `src/ui/batch_processor.py`, change line 8 from:

```python
from typing import List
```

to:

```python
from typing import List, Dict, Optional
```

- [ ] **Step 2: Initialize per-row state in `__init__`**

Find the `BatchProcessorFrame.__init__` method. Add, near the other instance attribute initializations:

```python
        # Persistent per-file progress widget refs (file.id -> widgets dict).
        # Populated by _create_file_item; consumed by _tick_progress.
        self._row_widgets: Dict[str, Dict[str, object]] = {}
        # Aggregate header widgets (created in _create_file_section).
        self.batch_progress_bar = None
        self.batch_summary_label = None
        self._tick_after_id: Optional[str] = None
```

(If `__init__` is hard to locate precisely, place these four lines immediately before the `def _create_file_section(self):` line — they only need to exist before first use.)

- [ ] **Step 3: Add the aggregate bar + label to `_create_file_section`**

In `src/ui/batch_processor.py`, replace the header block at lines 1042–1052:

```python
        header = ctk.CTkFrame(file_frame, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(16, 12))

        ctk.CTkLabel(
            header, text="≣  Files to Process", font=ctk.CTkFont(size=16, weight="bold")
        ).pack(side="left")

        self.file_count_label = ctk.CTkLabel(
            header, text="0 files", font=ctk.CTkFont(size=12), text_color="#60a5fa"
        )
        self.file_count_label.pack(side="left", padx=(12, 0))
```

with:

```python
        header = ctk.CTkFrame(file_frame, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(16, 12))

        ctk.CTkLabel(
            header, text="≣  Files to Process", font=ctk.CTkFont(size=16, weight="bold")
        ).pack(side="left")

        self.file_count_label = ctk.CTkLabel(
            header, text="0 files", font=ctk.CTkFont(size=12), text_color="#60a5fa"
        )
        self.file_count_label.pack(side="left", padx=(12, 0))

        # Aggregate batch progress bar + summary. Lives in its own wrapper frame
        # so the whole block can be packed/unpacked to show/hide during a run.
        agg_wrap = ctk.CTkFrame(file_frame, fg_color="transparent")
        self._batch_wrap = agg_wrap  # hidden by default; revealed by _start_processing

        self.batch_progress_bar = ctk.CTkProgressBar(agg_wrap, height=14)
        self.batch_progress_bar.set(0.0)
        self.batch_progress_bar.pack(side="left", fill="x", expand=True, padx=(0, 10))

        self.batch_summary_label = ctk.CTkLabel(
            agg_wrap,
            text="",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#60a5fa",
            width=180,
            anchor="e",
        )
        self.batch_summary_label.pack(side="right")
```

(We keep the existing `self.file_count_label` exactly as-is so `_update_file_list`'s `self.file_count_label.configure(...)` at line 1171 keeps working. The aggregate UI lives in the separate `_batch_wrap` frame, packed into `file_frame` only while processing — never packed at create time, so it is hidden until `_start_processing`.)

- [ ] **Step 4: Smoke-test the import**

Run: `python -c "from src.ui import batch_processor"`
Expected: no `ImportError` / `SyntaxError`.

Run: `ruff check src/ui/batch_processor.py`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add src/ui/batch_processor.py
git commit -m "feat(ui): add aggregate batch progress bar + summary label (hidden by default)"
```

---

## Task 6: Batch UI — persistent per-row widget refs

**Files:**
- Modify: `src/ui/batch_processor.py:1165-1183` (`_update_file_list`), `src/ui/batch_processor.py:1271-1274` (per-file bar in `_create_file_item`)

- [ ] **Step 1: Clear `_row_widgets` on full rebuild**

In `_update_file_list` (around lines 1165–1168), change:

```python
    def _update_file_list(self):
        """Update file list display"""
        for w in self.file_list_frame.winfo_children():
            w.destroy()

        count = len(self._files())
```

to:

```python
    def _update_file_list(self):
        """Update file list display"""
        for w in self.file_list_frame.winfo_children():
            w.destroy()
        # Full rebuild invalidates all persistent row refs.
        self._row_widgets = {}

        count = len(self._files())
```

- [ ] **Step 2: Replace the throwaway per-file bar with a stable sub-frame that keeps refs**

In `_create_file_item`, replace the PROCESSING block at lines 1271–1274:

```python
        if file.status == FileStatus.PROCESSING:
            pb = ctk.CTkProgressBar(item)
            pb.pack(fill="x", padx=12, pady=(0, 8))
            pb.set(file.progress / 100.0)
```

with:

```python
        if file.status == FileStatus.PROCESSING:
            prog_frame = ctk.CTkFrame(item, fg_color="transparent")
            prog_frame.pack(fill="x", padx=12, pady=(0, 8))

            pb = ctk.CTkProgressBar(prog_frame)
            pb.pack(side="left", fill="x", expand=True, padx=(0, 8))
            pb.set(file.progress)  # progress is now a 0.0-1.0 fraction

            pct_label = ctk.CTkLabel(
                prog_frame,
                text=f"{file.progress * 100:.0f}%",
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color="#e2e8f0",
                width=44,
                anchor="e",
            )
            pct_label.pack(side="left", padx=(0, 8))

            speed_text = f"{file.speed:.1f}x" if file.speed is not None else ""
            speed_label = ctk.CTkLabel(
                prog_frame,
                text=speed_text,
                font=ctk.CTkFont(size=11),
                text_color="#60a5fa",
                width=48,
                anchor="e",
            )
            speed_label.pack(side="left")

            # Persist refs so _tick_progress can update in place without rebuild.
            self._row_widgets[file.id] = {
                "bar": pb,
                "pct_label": pct_label,
                "speed_label": speed_label,
            }
```

- [ ] **Step 3: Smoke-test imports and lint**

Run: `python -c "from src.ui import batch_processor"`
Expected: no errors.

Run: `ruff check src/ui/batch_processor.py`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add src/ui/batch_processor.py
git commit -m "feat(ui): keep persistent per-file progress widget refs in batch view"
```

---

## Task 7: Batch UI — `_tick_progress` loop + schedule from start/complete

**Files:**
- Modify: `src/ui/batch_processor.py:1863-1895` (`_start_processing`), `src/ui/batch_processor.py:1929-1933` (`_on_processing_complete`); add a new `_tick_progress` method.

- [ ] **Step 1: Add the `_tick_progress` method**

Add this new method to `BatchProcessorFrame` (place it immediately before `_on_processing_complete`):

```python
    def _format_batch_summary(self, overall, avg_speed, completed, total):
        pct = overall * 100.0
        if avg_speed is not None:
            return f"{completed} / {total}  •  {pct:.0f}%  •  avg {avg_speed:.1f}x"
        return f"{completed} / {total}  •  {pct:.0f}%"

    def _tick_progress(self):
        """Periodic UI refresh (runs on the GUI thread via after()).

        Updates persistent per-row widgets in place and the aggregate header.
        Never rebuilds the file list — that avoids flicker/scroll jumps.
        """
        from src.state import compute_batch_progress  # local import avoids cycle

        files = self._files()
        # Per-row in-place updates.
        for f in files:
            refs = self._row_widgets.get(f.id)
            if not refs:
                continue
            try:
                refs["bar"].set(max(0.0, min(1.0, f.progress)))
                refs["pct_label"].configure(text=f"{f.progress * 100:.0f}%")
                refs["speed_label"].configure(
                    text=f"{f.speed:.1f}x" if f.speed is not None else ""
                )
            except Exception:
                # Widget may have been destroyed mid-tick; drop stale refs.
                self._row_widgets.pop(f.id, None)

        # Aggregate header.
        overall, avg_speed, completed, total = compute_batch_progress(files)
        if self.batch_progress_bar is not None:
            try:
                self.batch_progress_bar.set(overall)
            except Exception:
                pass
        if self.batch_summary_label is not None:
            try:
                self.batch_summary_label.configure(
                    text=self._format_batch_summary(overall, avg_speed, completed, total)
                )
            except Exception:
                pass

        # Reschedule while any file is still processing.
        if any(f.status == FileStatus.PROCESSING for f in files):
            self._tick_after_id = self.after(150, self._tick_progress)
        else:
            self._tick_after_id = None
```

- [ ] **Step 2: Reveal the aggregate bar and kick off the tick loop in `_start_processing`**

In `_start_processing`, after the existing line `self.start_btn.pack_forget()` / `self.stop_btn.pack(...)` block (around lines 1894–1895), append:

```python
        # Reveal the aggregate batch progress header and start the tick loop.
        self._batch_wrap.pack(fill="x", padx=16, pady=(0, 10), before=self.file_list_frame)
        if self.batch_progress_bar is not None:
            self.batch_progress_bar.set(0.0)
        if self.batch_summary_label is not None:
            self.batch_summary_label.configure(text="0 / 0  •  0%")
        # Prime the tick loop (150ms cadence once processing is underway).
        if self._tick_after_id is None:
            self._tick_after_id = self.after(150, self._tick_progress)
```

- [ ] **Step 3: Stop the tick loop and hide the bar on completion**

In `_on_processing_complete`, change the start of the method (lines 1929–1933):

```python
    def _on_processing_complete(self, failed_errors: list = None):
        """Handle completion"""
        self.stop_btn.pack_forget()
        self.start_btn.pack(side="left", padx=(0, 12))
        self._update_file_list()
```

to:

```python
    def _on_processing_complete(self, failed_errors: list = None):
        """Handle completion"""
        # Stop the periodic tick loop.
        if self._tick_after_id is not None:
            try:
                self.after_cancel(self._tick_after_id)
            except Exception:
                pass
            self._tick_after_id = None
        # One last sync so final per-file bars/labels reflect completion state.
        self._tick_progress()

        self.stop_btn.pack_forget()
        self.start_btn.pack(side="left", padx=(0, 12))
        self._update_file_list()
        # Hide the aggregate header now that the run is over.
        try:
            self._batch_wrap.pack_forget()
        except Exception:
            pass
```

Note: `_tick_progress` is called once before the rebuild so the final 100%/completed states are reflected even though `_update_file_list` rebuilds from scratch immediately after.

- [ ] **Step 4: Smoke-test imports and lint**

Run: `python -c "from src.ui import batch_processor"`
Expected: no errors.

Run: `ruff check src/ui/batch_processor.py`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add src/ui/batch_processor.py
git commit -m "feat(ui): live batch progress via after()-tick loop; reveal/hide aggregate bar"
```

---

## Task 8: Single-file UI — speed label

**Files:**
- Modify: `src/ui/single_processor.py:445-452` (add label), `src/ui/single_processor.py:474-475` (forward speed), `src/ui/single_processor.py:494-497` (`_update_progress`), `src/ui/single_processor.py:499-503` (`_on_complete`)

- [ ] **Step 1: Add the speed label below the percent label**

In `src/ui/single_processor.py`, after the `self.progress_percent` block (lines 449–452):

```python
        self.progress_percent = ctk.CTkLabel(
            progress_content, text="0%", font=ctk.CTkFont(size=12), text_color="#60a5fa"
        )
        self.progress_percent.pack(anchor="w", pady=(5, 0))
```

add:

```python
        self.progress_speed = ctk.CTkLabel(
            progress_content,
            text="",
            font=ctk.CTkFont(size=12),
            text_color="#60a5fa",
        )
        self.progress_speed.pack(anchor="w", pady=(2, 0))
```

- [ ] **Step 2: Forward `speed` through the inner `on_progress`**

In `_process_thread`, change the inner callback (lines 474–475):

```python
        def on_progress(percent: float):
            self.after(0, lambda: self._update_progress(percent))
```

to:

```python
        def on_progress(percent: float, speed=None):
            self.after(0, lambda p=percent, s=speed: self._update_progress(p, s))
```

- [ ] **Step 3: Update `_update_progress` to accept and render speed**

Change lines 494–497:

```python
    def _update_progress(self, percent: float):
        """Update progress bar"""
        self.progress_bar.set(percent / 100.0)
        self.progress_percent.configure(text=f"{percent:.1f}%")
```

to:

```python
    def _update_progress(self, percent: float, speed=None):
        """Update progress bar, percent label, and optional speed label."""
        self.progress_bar.set(percent / 100.0)
        self.progress_percent.configure(text=f"{percent:.1f}%")
        if self.progress_speed is not None:
            self.progress_speed.configure(
                text=f"{speed:.1f}x" if speed is not None else ""
            )
```

- [ ] **Step 4: Clear the speed label on completion**

In `_on_complete`, change the success branch (lines 501–504):

```python
        if success:
            self.progress_bar.set(1.0)
            self.progress_percent.configure(text="100%")
            self.state.add_log("✓ Processing completed successfully!")
```

to:

```python
        if success:
            self.progress_bar.set(1.0)
            self.progress_percent.configure(text="100%")
            if self.progress_speed is not None:
                self.progress_speed.configure(text="")
            self.state.add_log("✓ Processing completed successfully!")
```

- [ ] **Step 5: Smoke-test imports and lint**

Run: `python -c "from src.ui import single_processor"`
Expected: no errors.

Run: `ruff check src/ui/single_processor.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/ui/single_processor.py
git commit -m "feat(ui): show encoding speed on the single-file view"
```

---

## Task 9: Developer email in About section

**Files:**
- Modify: `src/ui/settings_panel.py:302-315`

- [ ] **Step 1: Append the developer line to `about_text`**

In `src/ui/settings_panel.py`, change the `about_text` string (lines 302–315):

```python
        about_text = (
            "VideoForge - FFmpeg Video Automation Dashboard\n\n"
            "Version: 2.0.0\n"
            "A modern desktop application for automating video processing tasks.\n\n"
            "Features:\n"
            "• Batch processing of video files\n"
            "• Flexible trim/cut options with seconds precision\n"
            "• Multiple processing profiles (Universal, High Quality, Small File, iOS)\n"
            "• Delogo filter for removing watermarks\n"
            "• Universal streaming compatibility (iPhone, Android, TVs, web)\n"
            "• Real-time progress tracking\n"
            "• Modern dark-themed UI\n\n"
            "Built with Python and CustomTkinter"
        )
```

to:

```python
        about_text = (
            "VideoForge - FFmpeg Video Automation Dashboard\n\n"
            "Version: 2.0.0\n"
            "A modern desktop application for automating video processing tasks.\n\n"
            "Features:\n"
            "• Batch processing of video files\n"
            "• Flexible trim/cut options with seconds precision\n"
            "• Multiple processing profiles (Universal, High Quality, Small File, iOS)\n"
            "• Delogo filter for removing watermarks\n"
            "• Universal streaming compatibility (iPhone, Android, TVs, web)\n"
            "• Real-time progress tracking\n"
            "• Modern dark-themed UI\n\n"
            "Built with Python and CustomTkinter\n\n"
            "Developer: fmamdoh504@gmail.com"
        )
```

- [ ] **Step 2: Lint**

Run: `ruff check src/ui/settings_panel.py`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add src/ui/settings_panel.py
git commit -m "feat(ui): add developer contact email to About section"
```

---

## Task 10: Final verification

- [ ] **Step 1: Full test suite**

Run: `pytest -v`
Expected: all tests pass, including the new `tests/test_progress_parsing.py` (7) and `tests/test_batch_progress.py` (7), and the extended `tests/test_parallel_processor.py`.

- [ ] **Step 2: Full lint**

Run: `ruff check .`
Expected: no errors.

- [ ] **Step 3: Manual smoke run**

Run: `python main.py`
Then in the app:
1. Go to **Single** view, pick a short video, hit **Process** — verify the bar advances, the `%` label updates, and a `Mx` speed label appears beneath the percent.
2. Go to a **Batch** tab, add 3–4 videos, hit **Start Processing** — verify:
   - The aggregate bar appears at the top of the file section and advances smoothly.
   - The summary label cycles through `"X / N • P% • avg Mx"`.
   - Each processing row shows its own bar, `%`, and `Mx`, all animating during the encode (not just on completion).
   - On completion the aggregate bar hides and the file list shows final green/red rows.
3. Open **Settings → About** — verify `Developer: fmamdoh504@gmail.com` appears at the bottom of the About text.

- [ ] **Step 4: Final commit (if any fixups were made during manual testing)**

```bash
git status   # confirm clean, or commit any fixups with a clear message
```

---

## Out-of-scope follow-ups (do NOT implement in this plan)

- ETA / time-remaining (would build on the `speed` field added here).
- Indeterminate spinner during the probe/setup phase.
- Parsing `fps=`, `bitrate=`, `frame=`.
- A processing overlay/modal ("processing dashboard").
- `mailto:` hyperlink styling for the About email.
- Adding the email to README or PyInstaller exe metadata.
