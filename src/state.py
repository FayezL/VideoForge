"""
Application state management
"""

from typing import List, Dict, Optional, Callable, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
import multiprocessing


class FileStatus(Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    ERROR = "error"


class CutMode(Enum):
    """Video trimming mode"""

    NONE = "none"
    CUT_LAST = "cut_last"  # Cut last X minutes
    CUT_FIRST = "cut_first"  # Cut first X minutes
    CUT_RANGE = "cut_range"  # Cut from start_time to end_time


class CutUnit(Enum):
    """Unit for expressing cut values. Applies to all cut modes."""

    TIME = "time"        # Value is in seconds (existing behavior)
    SPLIT = "split"      # Split video into N equal parts (after optional trim)
    MARKERS = "markers"  # Cut by typing start/end timestamps (HH:MM:SS)


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


@dataclass
class DelogoParams:
    """Delogo filter parameters"""

    x: int = 1635
    y: int = 240
    w: int = 176
    h: int = 147


@dataclass
class ProcessingProfile:
    """Video encoding profile settings"""

    name: str
    description: str

    # Video settings
    video_codec: str = "libx264"
    video_preset: str = "fast"
    video_crf: Optional[int] = 23
    video_bitrate: Optional[str] = None  # Use CRF or bitrate, not both
    pixel_format: str = "yuv420p"

    # Audio settings
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"

    # Container settings
    use_faststart: bool = True

    # Compatibility flags
    x264_profile: Optional[str] = None  # baseline, main, high
    x264_level: Optional[str] = None  # 3.0, 3.1, 4.0, etc.
    max_width: Optional[int] = None
    max_height: Optional[int] = None


@dataclass
class ParallelProcessingConfig:
    """Configuration for parallel batch processing"""

    max_workers: int = 2
    auto_adjust: bool = True
    memory_limit_per_worker_mb: int = 2048

    @staticmethod
    def calculate_optimal_workers() -> int:
        """
        Calculate optimal number of workers based on CPU cores.

        Formula: max(1, min(4, (cores-1)//2))
        Ensures system remains responsive while maximizing throughput.

        Returns:
            Recommended worker count (1-4)
        """
        try:
            cores = multiprocessing.cpu_count()
            optimal = max(1, min(4, (cores - 1) // 2))
            return optimal
        except Exception:
            return 2  # Safe default

    def estimate_memory_usage(self) -> int:
        """
        Estimate total memory usage in MB.

        Returns:
            Estimated memory usage in megabytes
        """
        return self.max_workers * self.memory_limit_per_worker_mb


@dataclass
class HardwareEncoder:
    """Detected hardware video encoder"""

    name: str  # e.g., "NVENC", "QuickSync", "VideoToolbox"
    codec: str  # e.g., "h264_nvenc", "h264_qsv"
    description: str
    is_available: bool = False
    is_tested: bool = False


@dataclass
class VideoFilter:
    """Single video filter with parameters"""

    filter_type: str  # rotate, crop, scale, brightness, etc.
    enabled: bool = False
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FilterChain:
    """Ordered chain of video filters"""

    filters: List[VideoFilter] = field(default_factory=list)

    def is_empty(self) -> bool:
        """Check if filter chain has any enabled filters"""
        return not any(f.enabled for f in self.filters)


# Predefined processing profiles
PROCESSING_PROFILES = {
    "universal": ProcessingProfile(
        name="Universal Compatibility",
        description="Maximum compatibility - works on all devices (iPhone, Android, Smart TVs, web browsers)",
        video_codec="libx264",
        video_preset="slow",
        video_crf=23,
        pixel_format="yuv420p",
        audio_codec="aac",
        audio_bitrate="192k",
        use_faststart=True,
        x264_profile="baseline",
        x264_level="3.1",
        max_width=1920,
        max_height=1080,
    ),
    "high_quality": ProcessingProfile(
        name="High Quality",
        description="Larger file size, better visual quality - ideal for archiving or high-quality streaming",
        video_preset="slow",
        video_crf=18,
        x264_profile="high",
        audio_bitrate="256k",
    ),
    "small_file": ProcessingProfile(
        name="Smaller File Size",
        description="Faster streaming, lower quality - good for quick uploads or bandwidth-constrained scenarios",
        video_preset="fast",
        video_crf=28,
        audio_bitrate="128k",
    ),
    "ios_optimized": ProcessingProfile(
        name="iOS Optimized",
        description="Optimized for iPhone, iPad, and Apple TV with best compatibility and quality balance",
        video_preset="slow",
        video_crf=22,
        x264_profile="main",
        x264_level="4.0",
        audio_codec="aac",
        audio_bitrate="192k",
        use_faststart=True,
    ),
}


class AppState:
    def __init__(self):
        self.selected_files: List[ProcessingFile] = []
        self.input_folder: Optional[str] = None
        self.output_folder: Optional[str] = None

        self.cut_start_enabled: bool = False
        self.cut_end_enabled: bool = True
        self.cut_start_hours: float = 0.0
        self.cut_start_minutes: float = 2.0
        self.cut_start_seconds: float = 30.0
        self.cut_end_hours_amount: float = 0.0
        self.cut_end_minutes_amount: float = 3.0
        self.cut_end_seconds_amount: float = 0.0

        self.cut_mode: CutMode = CutMode.CUT_LAST
        self.cut_hours: float = 0.0
        self.cut_minutes: float = 5.0
        self.cut_seconds: float = 0.0
        self.cut_start_hours_range: float = 0.0
        self.cut_start_minutes_range: float = 0.0
        self.cut_start_seconds_range: float = 0.0
        self.cut_end_hours: Optional[float] = None
        self.cut_end_minutes: Optional[float] = None
        self.cut_end_seconds: Optional[float] = None

        self.cut_last_5_minutes: bool = True

        # Cut unit selector (TIME / SPLIT / MARKERS) — applies to all modes.
        # Default TIME preserves existing behavior.
        self.cut_unit: CutUnit = CutUnit.TIME

        # Markers inputs (used when cut_unit == MARKERS)
        self.cut_markers_start: str = "00:00:00"
        self.cut_markers_end: str = ""  # Empty = to end

        # Split inputs (used when cut_unit == SPLIT)
        self.split_parts: int = 2

        self.apply_delogo: bool = False
        self.delogo_params: DelogoParams = DelogoParams()
        self.processing_profile: str = "universal"

        self.output_format: str = "mp4"
        self.output_suffix: str = ""
        self.output_prefix: str = ""
        self.create_output_subfolder: bool = False
        self.overwrite_existing: bool = True

        self.rename_enabled: bool = False
        self.rename_base: str = ""
        self.rename_start: int = 1
        self.rename_pad: int = 2

        self.is_processing: bool = False
        self.current_file_index: int = 0

        self.task2_files: List[ProcessingFile] = []
        self.task2_output_folder: Optional[str] = None
        self.task2_processing: bool = False

        self.extra_task_slots: List[Dict[str, Any]] = []

        self.logs: List[str] = []
        self.log_callbacks: List[Callable[[str], None]] = []

        self.current_template: Optional[str] = None
        self.template_modified: bool = False

        self.parallel_config: ParallelProcessingConfig = ParallelProcessingConfig()
        self.active_processes: List[str] = []

        # CPU usage controls — prevent FFmpeg from saturating all cores.
        # ffmpeg_threads caps threads PER ffmpeg process (passed via -threads).
        # process_priority sets the OS scheduling priority ("low" or "normal").
        self.ffmpeg_threads: int = 2
        self.process_priority: str = "low"

        self.detected_encoders: List[HardwareEncoder] = []
        self.use_hardware_encoding: bool = False
        self.selected_encoder: Optional[HardwareEncoder] = None

        self.filter_chain: FilterChain = FilterChain()

        self._metadata_cache: Dict[str, Any] = {}

        self.current_batch_state: Optional[Any] = None

        self.detection_enabled: bool = False
        self.detection_results: List[Any] = []
        self.active_profile: Optional[str] = None
        self.detection_progress: float = 0.0
        self.detection_status: str = "idle"

    def add_log(self, message: str):
        self.logs.append(message)
        for callback in self.log_callbacks:
            callback(message)

    def clear_logs(self):
        self.logs.clear()

    def register_log_callback(self, callback: Callable[[str], None]):
        self.log_callbacks.append(callback)

    def unregister_log_callback(self, callback: Callable[[str], None]):
        if callback in self.log_callbacks:
            self.log_callbacks.remove(callback)

    @property
    def cut_total_seconds(self) -> float:
        return (self.cut_hours * 3600) + (self.cut_minutes * 60) + self.cut_seconds

    @property
    def cut_start_total_seconds_trim(self) -> float:
        return (
            (self.cut_start_hours * 3600)
            + (self.cut_start_minutes * 60)
            + self.cut_start_seconds
        )

    @property
    def cut_end_total_seconds_trim(self) -> float:
        return (
            (self.cut_end_hours_amount or 0.0) * 3600
            + (self.cut_end_minutes_amount or 0.0) * 60
            + (self.cut_end_seconds_amount or 0.0)
        )

    @property
    def cut_start_total_seconds(self) -> float:
        return (
            (self.cut_start_hours * 3600)
            + (self.cut_start_minutes * 60)
            + self.cut_start_seconds
        )

    @property
    def cut_end_total_seconds(self) -> Optional[float]:
        if (
            self.cut_end_hours is None
            and self.cut_end_minutes is None
            and self.cut_end_seconds is None
        ):
            return None
        hours = self.cut_end_hours or 0.0
        mins = self.cut_end_minutes or 0.0
        secs = self.cut_end_seconds or 0.0
        return (hours * 3600) + (mins * 60) + secs
