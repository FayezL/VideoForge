"""
Tests for parallel batch processing functionality.

This module tests the ParallelProcessor class, ensuring parallel video
processing works correctly with thread pooling and proper resource management.
"""

import pytest
import time
from unittest.mock import Mock
from src.parallel_processor import ParallelProcessor
from src.state import AppState, ProcessingFile, ParallelProcessingConfig


class TestParallelProcessingConfig:
    """Test suite for ParallelProcessingConfig"""

    def test_calculate_optimal_workers(self):
        """Test optimal worker calculation"""
        optimal = ParallelProcessingConfig.calculate_optimal_workers()

        # Should return between 1 and 4
        assert 1 <= optimal <= 4

    def test_estimate_memory_usage(self):
        """Test memory usage estimation"""
        config = ParallelProcessingConfig(max_workers=3, memory_limit_per_worker_mb=2048)
        estimated = config.estimate_memory_usage()

        assert estimated == 3 * 2048  # 6144 MB


class TestParallelProcessor:
    """Test suite for ParallelProcessor class"""

    @pytest.fixture
    def mock_state(self):
        """Create mock AppState"""
        state = AppState()
        state.active_processes = []
        return state

    @pytest.fixture
    def mock_video_processor(self):
        """Create mock VideoProcessor"""
        processor = Mock()
        processor.process_video = Mock()
        processor._get_output_path = Mock(return_value="/output/video.mp4")
        return processor

    @pytest.fixture
    def sample_files(self):
        """Create sample processing files"""
        return [
            ProcessingFile(id="1", path="/path/video1.mp4", name="video1.mp4"),
            ProcessingFile(id="2", path="/path/video2.mp4", name="video2.mp4"),
            ProcessingFile(id="3", path="/path/video3.mp4", name="video3.mp4"),
        ]

    def test_init(self, mock_state, mock_video_processor):
        """Test parallel processor initialization"""
        processor = ParallelProcessor(mock_state, mock_video_processor, max_workers=3)

        assert processor.max_workers == 3
        assert processor.state == mock_state
        assert processor.video_processor == mock_video_processor
        assert not processor.is_processing()

    def test_max_workers_clamping(self, mock_state, mock_video_processor):
        """Test that max_workers is clamped to 1-8"""
        # Test lower bound
        processor1 = ParallelProcessor(mock_state, mock_video_processor, max_workers=0)
        assert processor1.max_workers == 1

        # Test upper bound
        processor2 = ParallelProcessor(mock_state, mock_video_processor, max_workers=20)
        assert processor2.max_workers == 8

        # Test valid range
        processor3 = ParallelProcessor(mock_state, mock_video_processor, max_workers=4)
        assert processor3.max_workers == 4

    def test_get_active_count_initial(self, mock_state, mock_video_processor):
        """Test initial active count is zero"""
        processor = ParallelProcessor(mock_state, mock_video_processor)

        assert processor.get_active_count() == 0

    def test_get_queue_size_initial(self, mock_state, mock_video_processor):
        """Test initial queue size is zero"""
        processor = ParallelProcessor(mock_state, mock_video_processor)

        assert processor.get_queue_size() == 0

    def test_process_batch_starts_workers(self, mock_state, mock_video_processor, sample_files):
        """Test that process_batch starts worker threads"""
        processor = ParallelProcessor(mock_state, mock_video_processor, max_workers=2)

        # Set up mock to simulate quick processing
        mock_video_processor.process_video.return_value = None

        processor.process_batch(sample_files)

        # Give workers time to start
        time.sleep(0.1)

        assert processor.is_processing()

        # Clean up
        processor.stop()

    def test_stop_graceful_shutdown(self, mock_state, mock_video_processor, sample_files):
        """Test graceful stop with timeout"""
        processor = ParallelProcessor(mock_state, mock_video_processor, max_workers=2)

        # Set up mock to simulate processing
        def slow_process(*args, **kwargs):
            time.sleep(0.5)

        mock_video_processor.process_video.side_effect = slow_process

        processor.process_batch(sample_files)
        time.sleep(0.1)  # Let workers start

        # Stop should wait for workers
        processor.stop(timeout=2.0)

        assert not processor.is_processing()
        assert processor.get_active_count() == 0

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

    def test_cannot_start_while_processing(self, mock_state, mock_video_processor, sample_files):
        """Test that starting batch while already processing raises error"""
        processor = ParallelProcessor(mock_state, mock_video_processor, max_workers=1)

        # Set up mock to simulate long processing
        def slow_process(*args, **kwargs):
            time.sleep(2.0)

        mock_video_processor.process_video.side_effect = slow_process

        processor.process_batch(sample_files)
        time.sleep(0.1)  # Let first batch start

        # Try to start another batch
        with pytest.raises(RuntimeError, match="Already processing"):
            processor.process_batch(sample_files)

        # Clean up
        processor.stop()

    def test_queue_management(self, mock_state, mock_video_processor, sample_files):
        """Test queue size decreases as files are processed"""
        processor = ParallelProcessor(mock_state, mock_video_processor, max_workers=1)

        # Mock quick processing
        mock_video_processor.process_video.return_value = None

        initial_count = len(sample_files)
        processor.process_batch(sample_files)

        # Queue should decrease as files are processed
        time.sleep(0.2)

        final_queue_size = processor.get_queue_size()
        assert final_queue_size < initial_count

        # Clean up
        processor.stop()
