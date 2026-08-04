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