"""
Parallel batch video processing with thread pooling.

This module provides parallel processing capability for batch video encoding,
allowing 2-4 videos to be processed simultaneously for significant speedup.
"""

import threading
import queue
from typing import List, Optional, Callable
from src.state import AppState, ProcessingFile
from src.video_processor import VideoProcessor


class ParallelProcessor:
    """Parallel video processing with worker pool"""

    def __init__(
        self,
        state: AppState,
        video_processor: VideoProcessor,
        max_workers: int = 2,
        output_folder_override: Optional[str] = None
    ):
        """
        Initialize parallel processor.

        Args:
            state: Application state
            video_processor: Video processor instance
            max_workers: Maximum concurrent workers
            output_folder_override: If set, use this folder for output paths (e.g. Task 2)
        """
        self.state = state
        self.video_processor = video_processor
        self.max_workers = max(1, min(8, max_workers))  # Clamp to 1-8
        self._output_folder_override = output_folder_override

        self._file_queue = queue.Queue()
        self._workers: List[threading.Thread] = []
        self._stop_event = threading.Event()
        self._active_count_lock = threading.Lock()
        self._active_count = 0

        # Callbacks
        self._on_file_start: Optional[Callable[[ProcessingFile], None]] = None
        self._on_file_complete: Optional[Callable[[ProcessingFile, bool, Optional[str]], None]] = None
        self._on_progress: Optional[Callable[[str, float, Optional[float]], None]] = None

    def process_batch(
        self,
        files: List[ProcessingFile],
        on_file_start: Optional[Callable[[ProcessingFile], None]] = None,
        on_file_complete: Optional[Callable[[ProcessingFile, bool, Optional[str]], None]] = None,
        on_progress: Optional[Callable[[str, float, Optional[float]], None]] = None
    ) -> None:
        """
        Process a batch of files in parallel.

        Args:
            files: List of files to process
            on_file_start: Callback when file processing starts (file)
            on_file_complete: Callback when file completes (file, success, error_msg)
            on_progress: Callback for progress updates (file_id, percent, speed)

        Note:
            Processing runs in background thread. Call stop() to terminate.
        """
        if self.is_processing():
            raise RuntimeError("Already processing a batch")

        # Store callbacks
        self._on_file_start = on_file_start
        self._on_file_complete = on_file_complete
        self._on_progress = on_progress

        # Clear stop event and queue
        self._stop_event.clear()
        while not self._file_queue.empty():
            try:
                self._file_queue.get_nowait()
            except queue.Empty:
                break

        # Add files to queue with their batch index so sequential rename works
        # correctly for any batch size (1 file, 30 episodes, 50+)
        for index, file in enumerate(files):
            self._file_queue.put((index, file))

        # Start worker threads
        self._workers = []
        for i in range(self.max_workers):
            worker = threading.Thread(
                target=self._worker_thread,
                name=f"Worker-{i+1}",
                daemon=True
            )
            worker.start()
            self._workers.append(worker)

        self.state.add_log(f"Started parallel processing with {self.max_workers} workers")

    def stop(self, timeout: float = 5.0) -> None:
        """
        Stop all active processing gracefully.

        Args:
            timeout: Maximum seconds to wait for workers to stop

        Note:
            Terminates all FFmpeg subprocesses and waits for threads to exit.
        """
        self._stop_event.set()
        self.state.add_log("Stopping parallel processing...")

        # Wait for workers to finish
        for worker in self._workers:
            worker.join(timeout=timeout / len(self._workers) if self._workers else timeout)

        # Clear queue
        while not self._file_queue.empty():
            try:
                self._file_queue.get_nowait()
            except queue.Empty:
                break

        self._workers.clear()
        self._active_count = 0
        self.state.add_log("Parallel processing stopped")

    def is_processing(self) -> bool:
        """
        Check if batch processing is active.

        Returns:
            True if workers are processing, False otherwise
        """
        return any(worker.is_alive() for worker in self._workers)

    def get_active_count(self) -> int:
        """
        Get number of currently active workers.

        Returns:
            Count of workers currently processing files
        """
        with self._active_count_lock:
            return self._active_count

    def get_queue_size(self) -> int:
        """
        Get number of files waiting in queue.

        Returns:
            Count of pending files not yet started
        """
        return self._file_queue.qsize()

    def _worker_thread(self) -> None:
        """
        Worker thread that processes files from the queue.

        Runs continuously until stop event is set or queue is empty.
        """
        while not self._stop_event.is_set():
            try:
                # Get next (index, file) pair with timeout to allow checking stop event
                try:
                    file_index, file = self._file_queue.get(timeout=0.5)
                except queue.Empty:
                    # No more files, exit
                    break

                if self._stop_event.is_set():
                    # Put item back and exit
                    self._file_queue.put((file_index, file))
                    break

                # Increment active count
                with self._active_count_lock:
                    self._active_count += 1
                    self.state.active_processes.append(file.id)

                # Call start callback
                if self._on_file_start:
                    self._on_file_start(file)

                # Process the file
                success = False
                error_msg = None

                try:
                    # Create progress callback for this file
                    def progress_callback(percent: float, speed: Optional[float] = None):
                        if self._on_progress:
                            self._on_progress(file.id, percent, speed)

                    # Process video with per-file cut times
                    def log_callback(message: str):
                        self.state.add_log(message)
                    
                    output_path = self.video_processor._get_output_path(
                        file.path,
                        output_folder_override=self._output_folder_override,
                        file_index=file_index,
                    )
                    success, error_msg = self.video_processor.process_video(
                        file.path,
                        output_path,
                        on_progress=progress_callback,
                        on_log=log_callback,
                        processing_file=file
                    )
                    if not success:
                        error_msg = error_msg or "Processing failed"

                except Exception as e:
                    error_msg = str(e)
                    self.state.add_log(f"Error processing {file.name}: {error_msg}")

                finally:
                    # Decrement active count
                    with self._active_count_lock:
                        self._active_count -= 1
                        if file.id in self.state.active_processes:
                            self.state.active_processes.remove(file.id)

                    # Call completion callback
                    if self._on_file_complete:
                        self._on_file_complete(file, success, error_msg)

                    # Mark task as done
                    self._file_queue.task_done()

            except Exception as e:
                self.state.add_log(f"Worker thread error: {str(e)}")
                with self._active_count_lock:
                    self._active_count -= 1
