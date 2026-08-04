# Design: Encoding Progress Visuals + Portfolio Contact

**Date:** 2026-08-04
**Status:** Approved → moving to implementation plan
**Scope owner:** frontend (UI) + backend (parser/data model)

## Goals

Make batch encoding feel alive: a live per-file bar that animates mid-encode,
an aggregate batch bar, a per-file percent, and the ffmpeg `speed=` readout.
Also surface a developer contact email in the About section for portfolio use.

## Background (current state, from codebase exploration)

- FFmpeg stderr `time=` is already parsed and percent is already plumbed
  end-to-end: `video_processor.py` `monitor_progress` (L504–519) →
  `on_progress(percent)` → `ParallelProcessor._on_progress`
  (`parallel_processor.py:194–196`) → `ProcessingFile.progress`.
- Single-file view already has a working bar:
  `single_processor.py:445–497` (`progress_bar`, `progress_percent`,
  `_update_progress`).
- Batch view has a per-file `CTkProgressBar` widget
  (`batch_processor.py:1271–1274`) but it never animates — the file list is
  only rebuilt on completion (`_update_file_list` at L1165 destroys and
  rebuilds every row, called from `_on_processing_complete` at L1929).
- **Latent bug:** `ProcessingFile.progress` is set to `0–100` during encoding
  (`video_processor.py:590`) but `0.0`/`1.0` on start/end
  (`video_processor.py:578`). Mixed scale.
- **Latent bug:** ffmpeg emits stats as `chunk1\rchunk2\rchunk3\n`. Iterating
  `for line in process.stdout` yields the whole run joined by `\r`, and the
  current single `re.search` only matches the *first* chunk → under-reports.
- No `speed=`, `fps=`, ETA, or elapsed-time parsing anywhere.
- Parser regex has no unit-test coverage.

## Scope

**In:**

- Batch view: aggregate header bar + `"X / N • P% • avg Mx"` summary; live
  per-file bar; per-file `%` and `speed` labels.
- Single-file view: add `speed` label next to the existing bar/`%`.
- Parser: capture `speed=Nx`; fix the multi-`\r` under-report bug; extract
  a pure, testable helper.
- Data model: normalize `ProcessingFile.progress` to 0.0–1.0 everywhere; add
  `speed: Optional[float]`.
- About section: append `Developer: fmamdoh504@gmail.com`.
- Unit tests for the parser; extend the parallel-processor callback test.

**Out:**

- ETA / time-remaining.
- Indeterminate spinner during the probe/setup phase.
- A processing overlay/modal ("processing dashboard").
- Parsing `fps=`, `bitrate=`, `frame=`.
- Refactoring the callback dispatch into an event bus.

## Changes by file

### `src/state.py` (`ProcessingFile`, L35–48)

- `progress: float = 0.0` semantics → uniformly **0.0–1.0** (was mixed
  0–100 / 0.0–1.0).
- Add `speed: Optional[float] = None`.

### `src/video_processor.py`

- Extract module-scope helper
  `parse_ffmpeg_progress(text: str) -> tuple[Optional[float], Optional[float]]`
  returning `(percent, speed)`. Importable by tests.
- Add `speed_pattern = re.compile(r"speed=\s*([\d.]+)x")`. Treat
  `speed=N/A` → `None`.
- `monitor_progress` (L504–519): iterate `for chunk in line.split("\r")`,
  feed each chunk to the helper, keep the latest non-None values. Throttle
  emit: call `on_progress` only when a value changed OR ≥0.25s since the
  last emit.
- Wire `on_progress(percent, speed)` through `process_queue` (L544–608) and
  the `_process_with_subprocess` call site. Percent stays `0–100` on the
  wire (the normalization boundary is in `ParallelProcessor`).

### `src/parallel_processor.py` (L194–196)

- `_on_progress(file_id, percent, speed=None)`: store
  `file.progress = percent / 100.0`, `file.speed = speed`. This is the
  single normalization boundary.

### `src/ui/batch_processor.py`

- `_create_file_section` (L1037): in the existing `header` (L1042), add
  `self.batch_progress_bar` (CTkProgressBar) + `self.batch_summary_label`
  (`CTkLabel`). Hidden by default; shown by `_start_processing`, hidden by
  `_on_processing_complete`.
- `_create_file_item` (L1185): for `PROCESSING` files, replace the
  throwaway `pb` (L1271–1274) with a stable progress sub-frame; keep refs
  in `self._row_widgets: Dict[str, dict]` mapping
  `file.id → {"bar", "pct_label", "speed_label"}`. Drop the entry when the
  row is destroyed/rebuilt.
- `_update_file_list` (L1165): behavior unchanged (still rebuilds on
  structural changes). Explicitly NOT called on mid-encode percent/speed
  changes.
- New `_tick_progress`: `self.after(150, …)` loop scheduled from
  `_start_processing`, reschedules while any file is `PROCESSING`, stops in
  `_on_processing_complete`. For each file with row refs:
  `bar.set(file.progress)`, update `%`/`speed` labels. Aggregate:
  `overall = Σ(completed→1.0, processing→file.progress, pending→0.0) / N`;
  `avg_speed = mean(speed)` over active files; update
  `batch_progress_bar.set(overall)` and `batch_summary_label`.
- L1274 `pb.set(file.progress / 100.0)` → `pb.set(file.progress)`
  (post-normalization).

### `src/ui/single_processor.py` (L445–497)

- Add `self.progress_speed` label below `self.progress_percent` (L452).
- Inner `on_progress` (L474) → accept `speed`, forward via
  `self.after(0, …)`.
- `_update_progress` (L494) → optional `speed` arg; update `progress_speed`
  text (`"{speed:.1f}x"`) or blank it.

### `src/ui/settings_panel.py` (L302–315)

- Append one line to `about_text`:
  `Developer: fmamdoh504@gmail.com`. Plain-text label, matches existing
  styling. No new widgets.

### Tests

- New `tests/test_progress_parsing.py`: feed captured ffmpeg stderr samples
  — single stats line, multi-`\r` line, `speed=N/A`, missing duration,
  malformed input — assert `(percent, speed)` tuples.
- Extend `tests/test_parallel_processor.py::test_callbacks_invoked`:
  assert `on_progress` may carry a 3rd arg and that `ProcessingFile.progress`
  is stored as a fraction (e.g. `percent=50` → `0.5`).

## Threading & safety

The tick loop runs via `after()` on the GUI thread; single-file updates via
`after(0, …)`. `ProcessingFile.progress`/`speed` are written by worker
threads and read by the tick loop — the same pattern already used app-wide.
No new locks required. (Optional hardening: wrap reads/writes in
`state.lock` — flagged optional, not required for this plan.)

## Risks / open questions

1. **`\r` handling assumption** — verified against the current code path;
   will be confirmed by the new parser tests using real captured stderr.
2. **CustomTkinter `CTkProgressBar` redraw cost at 150ms tick** — fine for
   ≤ ~50 visible rows. If batches grow huge, scope updates to only
   visible/active rows (deferred optimization).
3. **Email exposure** — plain-text email in a desktop app is visible to
   anyone running it. Intentional for portfolio; flagged in case
   obfuscation is desired later.

## Out-of-scope follow-ups (not in this plan)

- ETA / time-remaining (would build on the speed field added here).
- Indeterminate spinner during the probe phase.
- `fps=` / `bitrate=` parsing.
- Processing overlay/modal.
