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