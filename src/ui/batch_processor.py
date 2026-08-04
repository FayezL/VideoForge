"""
Batch Processor UI Component
"""

import customtkinter as ctk
import tkinter.filedialog as filedialog
from tkinter import messagebox
from typing import List, Dict, Optional
from datetime import datetime
import threading
import os
import glob

from src.state import AppState, ProcessingFile, FileStatus, CutMode, CutUnit
from src.video_processor import VideoProcessor
from src.ui.drag_drop import DragDropHandler
from src.ui.logo_picker import LogoPickerDialog
from src.logo_detector import LogoDetector
from src.logo_detector_temporal import TemporalLogoDetector
from src.logo_position_utils import parse_logo_coordinates
from src.data_models import DetectionConfig, DetectionSession

try:
    from src.logo_detector_vision import (
        VisionLogoDetector,
        is_available as vision_detector_available,
    )
except ImportError:
    VisionLogoDetector = None

    def vision_detector_available():
        return False


# Video file extensions for scanning
VIDEO_EXTENSIONS = ("*.mp4", "*.mkv", "*.avi", "*.mov", "*.m4v", "*.webm")


class BatchProcessorFrame(ctk.CTkScrollableFrame):
    """Batch processor view with expanded options. task_index 0 = Task 1, 1 = Task 2."""

    def __init__(
        self, parent, state: AppState, processor: VideoProcessor, task_index: int = 0
    ):
        super().__init__(parent, fg_color="#0f172a")
        self.state = state
        self.processor = processor
        self.task_index = task_index
        self.drag_drop_handler = None  # Will be initialized after UI creation
        self.current_detection_session = None  # Current logo detection session
        self._detection_cancelled = False  # Flag to cancel detection

        # Persistent per-file progress widget refs (file.id -> widgets dict).
        # Populated by _create_file_item; consumed by _tick_progress.
        self._row_widgets: Dict[str, Dict[str, object]] = {}
        # Last-seen status per file id, used by _tick_progress to detect
        # transitions (e.g. PENDING->PROCESSING) that require a row rebuild.
        self._last_statuses: Dict[str, FileStatus] = {}
        # Aggregate header widgets (created in _create_file_section).
        self.batch_progress_bar = None
        self.batch_summary_label = None
        self._tick_after_id: Optional[str] = None

        self._create_ui()
        self.state.register_log_callback(self._on_log_update)

        # Initialize drag-drop after UI is created
        self._init_drag_drop()

    def _slot(self):
        """Task slot data for this task (for task_index >= 2)."""
        if self.task_index < 2:
            return None
        idx = self.task_index - 2
        slots = getattr(self.state, "extra_task_slots", [])
        if idx >= len(slots):
            return None
        return slots[idx]

    def _files(self):
        """Files list for this task."""
        if self.task_index == 0:
            return self.state.selected_files
        if self.task_index == 1:
            return self.state.task2_files
        slot = self._slot()
        return slot["files"] if slot else []

    def _output_folder_raw(self):
        if self.task_index == 0:
            return self.state.output_folder
        if self.task_index == 1:
            return self.state.task2_output_folder
        slot = self._slot()
        return slot.get("output_folder") if slot else None

    def _output_folder(self):
        """Output folder for this task (for display)."""
        return self._output_folder_raw() or "Not selected"

    def _set_output_folder(self, folder: str):
        if self.task_index == 0:
            self.state.output_folder = folder
        elif self.task_index == 1:
            self.state.task2_output_folder = folder
        else:
            slot = self._slot()
            if slot is not None:
                slot["output_folder"] = folder

    def _is_processing(self):
        if self.task_index == 0:
            return self.state.is_processing
        if self.task_index == 1:
            return self.state.task2_processing
        slot = self._slot()
        return slot.get("processing", False) if slot else False

    def _set_processing(self, value: bool):
        if self.task_index == 0:
            self.state.is_processing = value
        elif self.task_index == 1:
            self.state.task2_processing = value
        else:
            slot = self._slot()
            if slot is not None:
                slot["processing"] = value

    def _create_ui(self):
        """Create the UI components"""
        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", pady=(0, 16))

        task_name = "Task 1" if self.task_index == 0 else "Task 2"
        title = ctk.CTkLabel(
            header,
            text=f"Batch Processor — {task_name}",
            font=ctk.CTkFont(size=28, weight="bold"),
        )
        title.pack(anchor="w")

        subtitle = ctk.CTkLabel(
            header,
            text=f"Process multiple videos ({task_name}). Separate file list and output from the other task.",
            font=ctk.CTkFont(size=13),
            text_color="#60a5fa",
        )
        subtitle.pack(anchor="w", pady=(4, 0))

        # === INPUT SECTION ===
        self._create_input_section()

        # === TRIM/CUT SECTION ===
        self._create_trim_section()

        # === PROCESSING PROFILE SECTION ===
        self._create_profile_section()

        # === LOGO DETECTION SECTION (hidden — will be re-enabled in future release) ===
        # self._create_detection_section()

        # === PROCESSING OPTIONS (Delogo) ===
        self._create_delogo_section()

        # === OUTPUT SECTION ===
        self._create_output_section()

        # === FILE LIST ===
        self._create_file_section()

        # === ACTION BUTTONS ===
        self.progress_frame = None
        self._create_action_buttons()

    def _create_input_section(self):
        """Input source section - files or folder"""
        input_frame = ctk.CTkFrame(self, fg_color="#0f172a", corner_radius=12)
        input_frame.pack(fill="x", pady=(0, 12))

        # Section header
        section_header = ctk.CTkFrame(input_frame, fg_color="transparent")
        section_header.pack(fill="x", padx=16, pady=(16, 12))

        ctk.CTkLabel(
            section_header,
            text="◨  Input Source",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w")

        content = ctk.CTkFrame(input_frame, fg_color="transparent")
        content.pack(fill="x", padx=16, pady=(0, 16))

        btn_row = ctk.CTkFrame(content, fg_color="transparent")
        btn_row.pack(fill="x", pady=(0, 8))

        ctk.CTkButton(
            btn_row,
            text="Select Files",
            command=self._select_files,
            width=130,
            height=36,
            fg_color="#1e40af",
            hover_color="#60a5fa",
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            btn_row,
            text="Select Folder",
            command=self._select_folder,
            width=130,
            height=36,
            fg_color="#1e40af",
            hover_color="#60a5fa",
        ).pack(side="left", padx=(0, 8))

        # Pattern filter
        pattern_row = ctk.CTkFrame(content, fg_color="transparent")
        pattern_row.pack(fill="x", pady=(4, 0))

        ctk.CTkLabel(
            pattern_row,
            text="File pattern:",
            font=ctk.CTkFont(size=12),
            text_color="#60a5fa",
            width=80,
        ).pack(side="left", padx=(0, 8))

        self.pattern_entry = ctk.CTkEntry(
            pattern_row,
            placeholder_text="e.g. *.mkv or S01E*.mp4 (leave empty for all)",
            width=320,
            height=32,
        )
        self.pattern_entry.pack(side="left")
        self.pattern_entry.insert(0, "*")

    def _create_trim_section(self):
        trim_frame = ctk.CTkFrame(self, fg_color="#0f172a", corner_radius=12)
        trim_frame.pack(fill="x", pady=(0, 12))

        section_header = ctk.CTkFrame(trim_frame, fg_color="transparent")
        section_header.pack(fill="x", padx=16, pady=(16, 8))

        ctk.CTkLabel(
            section_header,
            text="✀  Trim Options",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w")

        hint_label = ctk.CTkLabel(
            section_header,
            text="Works on any video length - no need to know exact duration",
            font=ctk.CTkFont(size=11),
            text_color="#64748B",
        )
        hint_label.pack(anchor="w", pady=(2, 0))

        content = ctk.CTkFrame(trim_frame, fg_color="transparent")
        content.pack(fill="x", padx=16, pady=(8, 16))

        # --- Unit selector row (TIME / SPLIT / MARKERS) ---
        unit_row = ctk.CTkFrame(content, fg_color="transparent")
        unit_row.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(
            unit_row, text="Unit:", font=ctk.CTkFont(size=13), text_color="#60a5fa"
        ).pack(side="left", padx=(0, 8))
        self.cut_unit_var = ctk.StringVar(value=self.state.cut_unit.value)
        self.cut_unit_menu = ctk.CTkOptionMenu(
            unit_row,
            values=["time", "split", "markers"],
            variable=self.cut_unit_var,
            command=self._on_cut_unit_change,
            width=120,
            height=28,
        )
        self.cut_unit_menu.pack(side="left")

        # --- TIME inputs (existing, shown when unit=time) ---
        self._time_rows_frame = ctk.CTkFrame(content, fg_color="transparent")
        self._time_rows_frame.pack(fill="x")

        row1 = ctk.CTkFrame(self._time_rows_frame, fg_color="transparent")
        row1.pack(fill="x", pady=6)
        self.cut_start_enabled_cb = ctk.CTkCheckBox(
            row1,
            text="Remove from START (skip intro):",
            font=ctk.CTkFont(size=13),
            command=self._on_trim_change,
        )
        self.cut_start_enabled_cb.pack(side="left")
        if self.state.cut_start_enabled:
            self.cut_start_enabled_cb.select()

        self.cut_start_hours_entry = ctk.CTkEntry(row1, width=50, height=28)
        self.cut_start_hours_entry.insert(0, str(int(self.state.cut_start_hours)))
        self.cut_start_hours_entry.pack(side="left", padx=(8, 2))
        ctk.CTkLabel(
            row1, text="h", font=ctk.CTkFont(size=11), text_color="#64748B"
        ).pack(side="left", padx=(0, 6))

        self.cut_start_minutes_entry = ctk.CTkEntry(row1, width=50, height=28)
        self.cut_start_minutes_entry.insert(0, str(int(self.state.cut_start_minutes)))
        self.cut_start_minutes_entry.pack(side="left", padx=(0, 2))
        ctk.CTkLabel(
            row1, text="m", font=ctk.CTkFont(size=11), text_color="#64748B"
        ).pack(side="left", padx=(0, 6))

        self.cut_start_seconds_entry = ctk.CTkEntry(row1, width=50, height=28)
        self.cut_start_seconds_entry.insert(0, str(int(self.state.cut_start_seconds)))
        self.cut_start_seconds_entry.pack(side="left", padx=(0, 2))
        ctk.CTkLabel(
            row1, text="s", font=ctk.CTkFont(size=11), text_color="#64748B"
        ).pack(side="left")

        row2 = ctk.CTkFrame(self._time_rows_frame, fg_color="transparent")
        row2.pack(fill="x", pady=6)
        self.cut_end_enabled_cb = ctk.CTkCheckBox(
            row2,
            text="Remove from END (cut outro):",
            font=ctk.CTkFont(size=13),
            command=self._on_trim_change,
        )
        self.cut_end_enabled_cb.pack(side="left")
        if self.state.cut_end_enabled:
            self.cut_end_enabled_cb.select()

        self.cut_end_hours_entry = ctk.CTkEntry(row2, width=50, height=28)
        self.cut_end_hours_entry.insert(
            0, str(int(self.state.cut_end_hours_amount or 0))
        )
        self.cut_end_hours_entry.pack(side="left", padx=(8, 2))
        ctk.CTkLabel(
            row2, text="h", font=ctk.CTkFont(size=11), text_color="#64748B"
        ).pack(side="left", padx=(0, 6))

        self.cut_end_minutes_entry = ctk.CTkEntry(row2, width=50, height=28)
        self.cut_end_minutes_entry.insert(
            0, str(int(self.state.cut_end_minutes_amount or 0))
        )
        self.cut_end_minutes_entry.pack(side="left", padx=(0, 2))
        ctk.CTkLabel(
            row2, text="m", font=ctk.CTkFont(size=11), text_color="#64748B"
        ).pack(side="left", padx=(0, 6))

        self.cut_end_seconds_entry = ctk.CTkEntry(row2, width=50, height=28)
        self.cut_end_seconds_entry.insert(
            0, str(int(self.state.cut_end_seconds_amount or 0))
        )
        self.cut_end_seconds_entry.pack(side="left", padx=(0, 2))
        ctk.CTkLabel(
            row2, text="s", font=ctk.CTkFont(size=11), text_color="#64748B"
        ).pack(side="left")

        self.cut_mode_var = ctk.StringVar(value="cut_last")
        self.cut_hours_entry = self.cut_end_hours_entry
        self.cut_minutes_entry = self.cut_end_minutes_entry
        self.cut_seconds_entry = self.cut_end_seconds_entry
        self.cut_start_entry = self.cut_start_minutes_entry
        self.cut_start_sec_entry = self.cut_start_seconds_entry
        self.cut_end_entry = self.cut_end_minutes_entry
        self.cut_end_sec_entry = self.cut_end_seconds_entry
        self.cut_start_hours = self.cut_start_hours_entry
        self.cut_first_hours = self.cut_start_hours_entry
        self.cut_first_minutes = self.cut_start_minutes_entry
        self.cut_first_seconds = self.cut_start_seconds_entry
        self.cut_last_hours = self.cut_end_hours_entry
        self.cut_last_minutes = self.cut_end_minutes_entry
        self.cut_last_seconds = self.cut_end_seconds_entry

        # --- MARKERS inputs (shown when unit=markers) ---
        self._markers_rows_frame = ctk.CTkFrame(content, fg_color="transparent")

        markers_start_row = ctk.CTkFrame(self._markers_rows_frame, fg_color="transparent")
        markers_start_row.pack(fill="x", pady=6)
        ctk.CTkLabel(
            markers_start_row,
            text="Start time:",
            font=ctk.CTkFont(size=13),
            width=90,
            anchor="w",
        ).pack(side="left", padx=(0, 8))
        self.markers_start_entry = ctk.CTkEntry(markers_start_row, width=120, height=28)
        self.markers_start_entry.insert(0, self.state.cut_markers_start)
        self.markers_start_entry.pack(side="left", padx=(0, 6))
        ctk.CTkLabel(
            markers_start_row,
            text="HH:MM:SS",
            font=ctk.CTkFont(size=11),
            text_color="#64748B",
        ).pack(side="left")

        markers_end_row = ctk.CTkFrame(self._markers_rows_frame, fg_color="transparent")
        markers_end_row.pack(fill="x", pady=6)
        ctk.CTkLabel(
            markers_end_row,
            text="End time:",
            font=ctk.CTkFont(size=13),
            width=90,
            anchor="w",
        ).pack(side="left", padx=(0, 8))
        self.markers_end_entry = ctk.CTkEntry(markers_end_row, width=120, height=28)
        self.markers_end_entry.insert(0, self.state.cut_markers_end)
        self.markers_end_entry.pack(side="left", padx=(0, 6))
        ctk.CTkLabel(
            markers_end_row,
            text="HH:MM:SS (blank = to end)",
            font=ctk.CTkFont(size=11),
            text_color="#64748B",
        ).pack(side="left")

        # --- SPLIT inputs (shown when unit=split) ---
        self._split_rows_frame = ctk.CTkFrame(content, fg_color="transparent")

        split_row = ctk.CTkFrame(self._split_rows_frame, fg_color="transparent")
        split_row.pack(fill="x", pady=6)
        ctk.CTkLabel(
            split_row,
            text="Number of parts:",
            font=ctk.CTkFont(size=13),
        ).pack(side="left", padx=(0, 8))
        self.split_parts_entry = ctk.CTkEntry(split_row, width=60, height=28)
        self.split_parts_entry.insert(0, str(self.state.split_parts))
        self.split_parts_entry.pack(side="left", padx=(0, 6))
        ctk.CTkLabel(
            split_row,
            text="(trim above applies first, then splits remaining video)",
            font=ctk.CTkFont(size=11),
            text_color="#64748B",
        ).pack(side="left")

        # Sync initial visibility based on the current unit
        self._sync_unit_visibility()

    def _on_cut_unit_change(self, _value: str = ""):
        """Handle unit dropdown change — swap visible input rows."""
        self.state.cut_unit = CutUnit(self.cut_unit_var.get())
        self._sync_unit_visibility()

    def _sync_unit_visibility(self):
        """Show/hide time/markers/split rows based on the selected unit."""
        unit = self.state.cut_unit
        if unit == CutUnit.TIME:
            self._time_rows_frame.pack(fill="x")
            self._markers_rows_frame.pack_forget()
            self._split_rows_frame.pack_forget()
        elif unit == CutUnit.MARKERS:
            self._time_rows_frame.pack_forget()
            self._markers_rows_frame.pack(fill="x")
            self._split_rows_frame.pack_forget()
        elif unit == CutUnit.SPLIT:
            self._time_rows_frame.pack(fill="x")
            self._markers_rows_frame.pack_forget()
            self._split_rows_frame.pack(fill="x")

    def _on_markers_change(self):
        """Sync markers entry values to AppState."""
        self.state.cut_markers_start = self.markers_start_entry.get()
        self.state.cut_markers_end = self.markers_end_entry.get()

    def _on_split_change(self):
        """Sync split parts entry value to AppState."""
        try:
            self.state.split_parts = max(1, int(self.split_parts_entry.get() or 2))
        except ValueError:
            self.state.split_parts = 2

    def _on_trim_change(self):
        self.state.cut_start_enabled = self.cut_start_enabled_cb.get() == 1
        self.state.cut_end_enabled = self.cut_end_enabled_cb.get() == 1

    def _create_profile_section(self):
        """Processing profile/quality section"""
        from src.state import PROCESSING_PROFILES

        profile_frame = ctk.CTkFrame(self, fg_color="#0f172a", corner_radius=12)
        profile_frame.pack(fill="x", pady=(0, 12))

        section_header = ctk.CTkFrame(profile_frame, fg_color="transparent")
        section_header.pack(fill="x", padx=16, pady=(16, 12))

        ctk.CTkLabel(
            section_header,
            text="⚡ Processing Profile",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w")

        content = ctk.CTkFrame(profile_frame, fg_color="transparent")
        content.pack(fill="x", padx=16, pady=(0, 16))

        # Profile selector row
        profile_row = ctk.CTkFrame(content, fg_color="transparent")
        profile_row.pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(
            profile_row,
            text="Quality preset:",
            font=ctk.CTkFont(size=13),
            width=120,
            anchor="w",
        ).pack(side="left", padx=(0, 12))

        self.profile_var = ctk.StringVar(value=self.state.processing_profile)

        profile_menu = ctk.CTkOptionMenu(
            profile_row,
            values=list(PROCESSING_PROFILES.keys()),
            variable=self.profile_var,
            command=self._on_profile_change,
            width=250,
            height=36,
        )
        profile_menu.pack(side="left")

        # Profile description
        self.profile_desc_label = ctk.CTkLabel(
            content,
            text=PROCESSING_PROFILES[self.state.processing_profile].description,
            font=ctk.CTkFont(size=12),
            text_color="#60a5fa",
            wraplength=600,
            anchor="w",
            justify="left",
        )
        self.profile_desc_label.pack(anchor="w", pady=(8, 0), padx=(120, 0))

    def _create_detection_section(self):
        """AI Logo Detection section (Phase 4)"""
        detection_frame = ctk.CTkFrame(self, fg_color="#0f172a", corner_radius=12)
        detection_frame.pack(fill="x", pady=(0, 12))

        # Section header
        section_header = ctk.CTkFrame(detection_frame, fg_color="transparent")
        section_header.pack(fill="x", padx=16, pady=(16, 12))

        header_row = ctk.CTkFrame(section_header, fg_color="transparent")
        header_row.pack(fill="x")

        ctk.CTkLabel(
            header_row,
            text="⌕  AI Logo Detection",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(side="left")

        ctk.CTkLabel(
            header_row,
            text="Fewer frames, results by confidence. Choose method below.",
            font=ctk.CTkFont(size=12),
            text_color="#64748B",
        ).pack(side="left", padx=(12, 0))

        # Content
        content = ctk.CTkFrame(detection_frame, fg_color="transparent")
        content.pack(fill="x", padx=16, pady=(0, 16))

        # Detection method: Temporal (default), legacy edges, or Cloud Vision if available
        method_row = ctk.CTkFrame(content, fg_color="transparent")
        method_row.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            method_row, text="Method:", font=ctk.CTkFont(size=12), text_color="#60a5fa"
        ).pack(side="left", padx=(0, 8))
        method_values = [
            "Temporal Stability (recommended)",
            "OpenCV Edges (legacy)",
        ]
        if vision_detector_available():
            method_values.append("Google Cloud Vision (AI)")
        self.detection_method_var = ctk.StringVar(value=method_values[0])
        self.detection_method_menu = ctk.CTkOptionMenu(
            method_row,
            values=method_values,
            variable=self.detection_method_var,
            width=220,
            height=28,
        )
        self.detection_method_menu.pack(side="left")

        # Button row
        button_row = ctk.CTkFrame(content, fg_color="transparent")
        button_row.pack(fill="x", pady=(0, 12))

        self.detect_button = ctk.CTkButton(
            button_row,
            text="🔍 Detect Logo",
            command=self._on_detect_logo,
            width=150,
            height=36,
            fg_color="#2563eb",
            hover_color="#2563eb",
        )
        self.detect_button.pack(side="left", padx=(0, 8))

        # Cancel button (initially hidden)
        self.cancel_detect_button = ctk.CTkButton(
            button_row,
            text="✕ Cancel",
            command=self._on_cancel_detection,
            width=100,
            height=36,
            fg_color="#EF4444",
            hover_color="#DC2626",
        )

        # Sensitivity slider
        sensitivity_frame = ctk.CTkFrame(button_row, fg_color="transparent")
        sensitivity_frame.pack(side="left")

        ctk.CTkLabel(
            sensitivity_frame,
            text="Sensitivity:",
            font=ctk.CTkFont(size=12),
            text_color="#60a5fa",
        ).pack(side="left", padx=(0, 8))

        self.sensitivity_slider = ctk.CTkSlider(
            sensitivity_frame,
            from_=0.0,
            to=1.0,
            number_of_steps=20,
            width=150,
            height=16,
        )
        self.sensitivity_slider.set(0.75)  # Default
        self.sensitivity_slider.pack(side="left", padx=(0, 8))

        self.sensitivity_label = ctk.CTkLabel(
            sensitivity_frame,
            text="75%",
            font=ctk.CTkFont(size=11),
            text_color="#0f172a",
            width=40,
        )
        self.sensitivity_label.pack(side="left")

        # Update sensitivity label when slider changes
        self.sensitivity_slider.configure(
            command=lambda v: self.sensitivity_label.configure(text=f"{int(v * 100)}%")
        )

        # Profile management row
        profile_row = ctk.CTkFrame(content, fg_color="transparent")
        profile_row.pack(fill="x", pady=(0, 12))

        # Save Profile button
        self.save_profile_button = ctk.CTkButton(
            profile_row,
            text="💾 Save Profile",
            command=self._on_save_profile,
            width=120,
            height=28,
            fg_color="#6366F1",
            hover_color="#4F46E5",
        )
        self.save_profile_button.pack(side="left", padx=(0, 8))

        # Load Profile dropdown
        ctk.CTkLabel(
            profile_row,
            text="Load Profile:",
            font=ctk.CTkFont(size=12),
            text_color="#60a5fa",
        ).pack(side="left", padx=(0, 8))

        self.profile_var = ctk.StringVar(value="Select profile...")
        self.profile_dropdown = ctk.CTkOptionMenu(
            profile_row,
            variable=self.profile_var,
            values=["Select profile..."],
            command=self._on_load_profile,
            width=180,
            height=28,
        )
        self.profile_dropdown.pack(side="left", padx=(0, 8))

        # Refresh profiles button
        self.refresh_profiles_button = ctk.CTkButton(
            profile_row,
            text="🔄",
            command=self._refresh_profile_list,
            width=32,
            height=28,
            fg_color="#64748B",
            hover_color="#475569",
        )
        self.refresh_profiles_button.pack(side="left")

        # Populate profile list on startup
        self._refresh_profile_list()

        # Status label
        self.detection_status_label = ctk.CTkLabel(
            content,
            text="Click 'Detect Logo' to analyze the first video in the queue",
            font=ctk.CTkFont(size=12),
            text_color="#64748B",
            anchor="w",
        )
        self.detection_status_label.pack(anchor="w", pady=(0, 12))

        # Results area (initially hidden)
        self.detection_results_frame = ctk.CTkFrame(
            content, fg_color="#0f172a", corner_radius=10
        )
        self.detection_results_frame.pack(fill="x", pady=(0, 0))
        self.detection_results_frame.pack_forget()  # Hide initially

        # Store current detection session
        self.current_detection_session = None

    def _create_delogo_section(self):
        """Delogo filter section"""
        delogo_frame = ctk.CTkFrame(self, fg_color="#0f172a", corner_radius=12)
        delogo_frame.pack(fill="x", pady=(0, 12))

        section_header = ctk.CTkFrame(delogo_frame, fg_color="transparent")
        section_header.pack(fill="x", padx=16, pady=(16, 12))

        self.delogo_checkbox = ctk.CTkCheckBox(
            section_header,
            text="✧  Remove On-Screen Graphics (OSG)",
            command=self._on_delogo_toggle,
            font=ctk.CTkFont(size=14),
        )
        self.delogo_checkbox.pack(anchor="w")
        if self.state.apply_delogo:
            self.delogo_checkbox.select()

        self.delogo_params_frame = ctk.CTkFrame(delogo_frame, fg_color="transparent")
        self.delogo_params_frame.pack(fill="x", padx=16, pady=(0, 16))

        params = self.state.delogo_params
        labels = ["X", "Y", "Width", "Height"]
        values = [params.x, params.y, params.w, params.h]

        self.delogo_inputs = []
        for i, (label, value) in enumerate(zip(labels, values)):
            f = ctk.CTkFrame(self.delogo_params_frame, fg_color="transparent")
            f.pack(side="left", padx=(0, 16))
            ctk.CTkLabel(
                f, text=label, font=ctk.CTkFont(size=11), text_color="#60a5fa"
            ).pack(anchor="w")
            e = ctk.CTkEntry(f, width=70, height=28)
            e.insert(0, str(value))
            e.pack(anchor="w", pady=(2, 0))
            e.bind("<KeyRelease>", lambda ev, idx=i: self._on_delogo_param_change(idx))
            self.delogo_inputs.append(e)

        # ── Quick-position tools row ──
        tools_frame = ctk.CTkFrame(self.delogo_params_frame, fg_color="transparent")
        tools_frame.pack(fill="x", pady=(12, 0))

        self.pick_logo_btn = ctk.CTkButton(
            tools_frame,
            text="🖼  Pick from Video",
            command=self._on_pick_logo_area,
            width=140,
            height=28,
            font=ctk.CTkFont(size=12),
            fg_color="#3b82f6",
            hover_color="#2563eb",
        )
        self.pick_logo_btn.pack(side="left", padx=(0, 12))

        ctk.CTkLabel(
            tools_frame, text="or paste coords:",
            font=ctk.CTkFont(size=11), text_color="#64748b",
        ).pack(side="left", padx=(0, 4))

        self.paste_coords_entry = ctk.CTkEntry(
            tools_frame, width=160, height=28,
            placeholder_text="x, y, w, h",
        )
        self.paste_coords_entry.pack(side="left", padx=(0, 4))
        self.paste_coords_entry.bind("<Return>", lambda ev: self._on_parse_pasted_coords())

        self.parse_coords_btn = ctk.CTkButton(
            tools_frame,
            text="Parse",
            command=self._on_parse_pasted_coords,
            width=60, height=28,
            font=ctk.CTkFont(size=12),
            fg_color="#64748b",
            hover_color="#475569",
        )
        self.parse_coords_btn.pack(side="left")

        if not self.state.apply_delogo:
            self.delogo_params_frame.pack_forget()

    def _create_output_section(self):
        """Output options section"""
        output_frame = ctk.CTkFrame(self, fg_color="#0f172a", corner_radius=12)
        output_frame.pack(fill="x", pady=(0, 12))

        section_header = ctk.CTkFrame(output_frame, fg_color="transparent")
        section_header.pack(fill="x", padx=16, pady=(16, 12))

        ctk.CTkLabel(
            section_header,
            text="⇪  Output Options",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w")

        content = ctk.CTkFrame(output_frame, fg_color="transparent")
        content.pack(fill="x", padx=16, pady=(0, 16))

        # Output folder row
        folder_row = ctk.CTkFrame(content, fg_color="transparent")
        folder_row.pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(
            folder_row,
            text="Output folder:",
            font=ctk.CTkFont(size=12),
            text_color="#60a5fa",
            width=100,
        ).pack(side="left", padx=(0, 8))

        self.output_label = ctk.CTkLabel(
            folder_row,
            text=self._output_folder(),
            font=ctk.CTkFont(size=12),
            text_color="#0f172a",
        )
        self.output_label.pack(side="left", fill="x", expand=True)

        ctk.CTkButton(
            folder_row,
            text="Browse",
            command=self._select_output_folder,
            width=90,
            height=32,
        ).pack(side="right")

        # Output format & naming row
        opts_row = ctk.CTkFrame(content, fg_color="transparent")
        opts_row.pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(
            opts_row,
            text="Format:",
            font=ctk.CTkFont(size=12),
            text_color="#60a5fa",
            width=100,
        ).pack(side="left", padx=(0, 8))
        self.format_var = ctk.StringVar(value=self.state.output_format)
        format_menu = ctk.CTkOptionMenu(
            opts_row,
            values=["mp4", "mkv"],
            variable=self.format_var,
            command=self._on_format_change,
            width=100,
            height=32,
        )
        format_menu.pack(side="left", padx=(0, 24))

        ctk.CTkLabel(
            opts_row,
            text="Prefix:",
            font=ctk.CTkFont(size=12),
            text_color="#60a5fa",
            width=60,
        ).pack(side="left", padx=(0, 8))
        self.prefix_entry = ctk.CTkEntry(opts_row, width=100, height=28)
        self.prefix_entry.insert(0, self.state.output_prefix)
        self.prefix_entry.pack(side="left", padx=(0, 16))

        ctk.CTkLabel(
            opts_row,
            text="Suffix:",
            font=ctk.CTkFont(size=12),
            text_color="#60a5fa",
            width=60,
        ).pack(side="left", padx=(0, 8))
        self.suffix_entry = ctk.CTkEntry(opts_row, width=100, height=28)
        self.suffix_entry.insert(0, self.state.output_suffix)
        self.suffix_entry.pack(side="left")

        # Checkboxes row
        cb_row = ctk.CTkFrame(content, fg_color="transparent")
        cb_row.pack(fill="x", pady=(8, 0))

        self.subfolder_cb = ctk.CTkCheckBox(
            cb_row,
            text="Create 'output' subfolder",
            command=self._on_subfolder_toggle,
            font=ctk.CTkFont(size=12),
        )
        self.subfolder_cb.pack(side="left", padx=(100, 24))
        if self.state.create_output_subfolder:
            self.subfolder_cb.select()

        self.overwrite_cb = ctk.CTkCheckBox(
            cb_row,
            text="Overwrite existing files",
            command=self._on_overwrite_toggle,
            font=ctk.CTkFont(size=12),
        )
        self.overwrite_cb.pack(side="left")
        if self.state.overwrite_existing:
            self.overwrite_cb.select()

        # Rename Plan sub-section (inside Output Options card)
        self._create_rename_section(output_frame)

    def _create_rename_section(self, parent_frame):
        """Rename Plan sub-section — sequential episode numbering for any batch size.

        Examples:
            1 file  → hamo01.mp4
            3 files → hamo01.mp4, hamo02.mp4, hamo03.mp4
            30 eps  → hamo01.mp4 … hamo30.mp4
            50 eps  → hamo01.mp4 … hamo50.mp4
        """
        # Divider
        divider = ctk.CTkFrame(parent_frame, fg_color="#1e40af", height=1)
        divider.pack(fill="x", padx=16, pady=(4, 0))

        rename_content = ctk.CTkFrame(parent_frame, fg_color="transparent")
        rename_content.pack(fill="x", padx=16, pady=(10, 16))

        # Header row with enable checkbox
        header_row = ctk.CTkFrame(rename_content, fg_color="transparent")
        header_row.pack(fill="x", pady=(0, 8))

        self.rename_checkbox = ctk.CTkCheckBox(
            header_row,
            text="◇ Rename Plan  (sequential episode numbering)",
            command=self._on_rename_toggle,
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.rename_checkbox.pack(side="left")
        if self.state.rename_enabled:
            self.rename_checkbox.select()

        # Options row (hidden when disabled)
        self.rename_opts_frame = ctk.CTkFrame(rename_content, fg_color="transparent")
        self.rename_opts_frame.pack(fill="x")

        # Base name
        ctk.CTkLabel(
            self.rename_opts_frame,
            text="Base name:",
            font=ctk.CTkFont(size=12),
            text_color="#60a5fa",
            width=80,
        ).pack(side="left", padx=(0, 6))

        self.rename_base_entry = ctk.CTkEntry(
            self.rename_opts_frame, placeholder_text="e.g. hamo", width=120, height=28
        )
        self.rename_base_entry.insert(0, self.state.rename_base)
        self.rename_base_entry.pack(side="left", padx=(0, 16))

        # Start number
        ctk.CTkLabel(
            self.rename_opts_frame,
            text="Start №:",
            font=ctk.CTkFont(size=12),
            text_color="#60a5fa",
        ).pack(side="left", padx=(0, 6))

        self.rename_start_entry = ctk.CTkEntry(
            self.rename_opts_frame, width=55, height=28
        )
        self.rename_start_entry.insert(0, str(self.state.rename_start))
        self.rename_start_entry.pack(side="left", padx=(0, 16))

        # Padding digits
        ctk.CTkLabel(
            self.rename_opts_frame,
            text="Digits:",
            font=ctk.CTkFont(size=12),
            text_color="#60a5fa",
        ).pack(side="left", padx=(0, 6))

        self.rename_pad_entry = ctk.CTkEntry(
            self.rename_opts_frame, width=45, height=28
        )
        self.rename_pad_entry.insert(0, str(self.state.rename_pad))
        self.rename_pad_entry.pack(side="left", padx=(0, 20))

        # Live preview label
        self.rename_preview_label = ctk.CTkLabel(
            self.rename_opts_frame,
            text=self._build_rename_preview(),
            font=ctk.CTkFont(size=11),
            text_color="#64748B",
        )
        self.rename_preview_label.pack(side="left")

        # Wire up live preview updates
        for entry in (
            self.rename_base_entry,
            self.rename_start_entry,
            self.rename_pad_entry,
        ):
            entry.bind("<KeyRelease>", lambda _e: self._update_rename_preview())

        # Show/hide opts based on current state
        if not self.state.rename_enabled:
            self.rename_opts_frame.pack_forget()

    # ------------------------------------------------------------------
    # Rename Plan helpers
    # ------------------------------------------------------------------

    def _build_rename_preview(self) -> str:
        """Build the live preview string for rename plan."""
        base = (
            self.state.rename_base.strip() if hasattr(self, "rename_base_entry") else ""
        )
        if hasattr(self, "rename_base_entry"):
            base = self.rename_base_entry.get().strip()
        if not base:
            return "(enter a base name to see preview)"
        try:
            start = int(self.rename_start_entry.get() or 1)
        except (ValueError, AttributeError):
            start = 1
        try:
            pad = max(1, int(self.rename_pad_entry.get() or 2))
        except (ValueError, AttributeError):
            pad = 2
        ext = self.state.output_format
        names = [f"{base}{str(start + i).zfill(pad)}.{ext}" for i in range(min(3, 50))]
        preview = ", ".join(names)
        file_count = len(self._files())
        if file_count > 3:
            last = f"{base}{str(start + file_count - 1).zfill(pad)}.{ext}"
            preview += f" … {last} ({file_count} files)"
        return f"→ {preview}"

    def _update_rename_preview(self):
        """Refresh the preview label (called on every keystroke)."""
        if hasattr(self, "rename_preview_label"):
            self.rename_preview_label.configure(text=self._build_rename_preview())

    def _on_rename_toggle(self):
        """Show/hide rename options and update state."""
        self.state.rename_enabled = self.rename_checkbox.get() == 1
        if self.state.rename_enabled:
            self.rename_opts_frame.pack(fill="x")
            self._update_rename_preview()
        else:
            self.rename_opts_frame.pack_forget()

    def _create_file_section(self):
        """File list section"""
        file_frame = ctk.CTkFrame(self, fg_color="#0f172a", corner_radius=12)
        file_frame.pack(fill="both", expand=True, pady=(0, 12))

        header = ctk.CTkFrame(file_frame, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(16, 12))

        ctk.CTkLabel(
            header, text="≣  Files to Process", font=ctk.CTkFont(size=16, weight="bold")
        ).pack(side="left")

        self.file_count_label = ctk.CTkLabel(
            header, text="0 files", font=ctk.CTkFont(size=12), text_color="#60a5fa"
        )
        self.file_count_label.pack(side="left", padx=(12, 0))

        clear_btn = ctk.CTkButton(
            header,
            text="Clear List",
            command=self._clear_files,
            width=100,
            height=32,
            fg_color="#B91C1C",
            hover_color="#B91C1C",
        )
        clear_btn.pack(side="right")

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

        self.file_list_frame = ctk.CTkScrollableFrame(
            file_frame, fg_color="#0f172a", height=220, corner_radius=10
        )
        self.file_list_frame.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        self._update_file_list()

    def _create_action_buttons(self):
        """Action buttons"""
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", pady=(0, 16))

        self.start_btn = ctk.CTkButton(
            btn_frame,
            text="▷ Start Processing",
            command=self._start_processing,
            height=48,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color="#2563eb",
            hover_color="#3b82f6",
        )
        self.start_btn.pack(side="left", padx=(0, 12))

        self.stop_btn = ctk.CTkButton(
            btn_frame,
            text="◻ Stop",
            command=self._stop_processing,
            height=48,
            font=ctk.CTkFont(size=16),
            fg_color="#B91C1C",
            hover_color="#B91C1C",
        )
        self.stop_btn.pack_forget()

    def _select_files(self):
        """Select video files"""
        files = filedialog.askopenfilenames(
            title="Select Video Files",
            filetypes=[
                ("Video Files", "*.mp4 *.mkv *.avi *.mov *.m4v *.webm"),
                ("All Files", "*.*"),
            ],
        )
        if files:
            self._add_files_from_paths(list(files))

    def _select_folder(self):
        """Select folder and scan for videos"""
        folder = filedialog.askdirectory(title="Select Folder with Videos")
        if folder:
            self.state.input_folder = folder
            pattern = self.pattern_entry.get().strip() or "*"
            self._scan_folder_for_videos(folder, pattern)

    def _scan_folder_for_videos(self, folder: str, pattern: str):
        """Scan folder for video files matching pattern"""
        found = []
        if not pattern or pattern.strip() == "*":
            for ext in VIDEO_EXTENSIONS:
                found.extend(glob.glob(os.path.join(folder, ext)))
        else:
            p = pattern.strip()
            if not p.startswith("*"):
                p = "*" + p
            found.extend(glob.glob(os.path.join(folder, p)))
        found = list(dict.fromkeys(found))
        found.sort()
        if found:
            self._add_files_from_paths(found)
            self.state.add_log(f"Scanned folder: found {len(found)} video(s)")
        else:
            self.state.add_log(f"No videos found matching '{pattern}' in {folder}")

    def _add_files_from_paths(self, paths: List[str]):
        """Add files to the list from paths"""
        for path in paths:
            if not os.path.isfile(path):
                continue
            name = os.path.basename(path)
            if any(f.path == path for f in self._files()):
                continue
            self._files().append(
                ProcessingFile(id=f"file-{len(self._files())}", path=path, name=name)
            )
        self.state.add_log(f"Added {len(paths)} file(s)")
        self._update_file_list()

    def _clear_files(self):
        """Clear selected files"""
        self._files().clear()
        self._update_file_list()
        self.state.clear_logs()

    def _select_output_folder(self):
        """Select output folder"""
        folder = filedialog.askdirectory(title="Select Output Folder")
        if folder:
            self._set_output_folder(folder)
            self.output_label.configure(text=folder)
            self.state.add_log(f"Output folder: {folder}")

    def _update_file_list(self):
        """Update file list display"""
        for w in self.file_list_frame.winfo_children():
            w.destroy()
        # Full rebuild invalidates persistent row refs. Do NOT reset
        # _last_statuses here — _tick_progress manages it after rebuild.
        self._row_widgets = {}

        count = len(self._files())
        self.file_count_label.configure(text=f"{count} file{'s' if count != 1 else ''}")

        if not self._files():
            ctk.CTkLabel(
                self.file_list_frame,
                text="No files selected. Use 'Select Files' or 'Select Folder' to add videos.",
                font=ctk.CTkFont(size=13),
                text_color="#64748B",
            ).pack(pady=40)
            return

        for file in self._files():
            self._create_file_item(file)

    def _create_file_item(self, file: ProcessingFile):
        """Create a file item widget with per-file cut time inputs"""
        colors = {
            FileStatus.PENDING: "#1e40af",
            FileStatus.PROCESSING: "#2563eb",
            FileStatus.COMPLETED: "#16a34a",
            FileStatus.ERROR: "#B91C1C",
        }
        item = ctk.CTkFrame(
            self.file_list_frame,
            fg_color=colors.get(file.status, "#1e40af"),
            corner_radius=10,
        )
        item.pack(fill="x", pady=4)

        row = ctk.CTkFrame(item, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=8)

        ctk.CTkLabel(
            row, text=file.name, font=ctk.CTkFont(size=13, weight="bold"), anchor="w"
        ).pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(
            row,
            text=file.status.value.upper(),
            font=ctk.CTkFont(size=11),
            text_color="#e2e8f0",
        ).pack(side="right", padx=(8, 0))

        # Per-file cut times row (only show when not processing/completed)
        if file.status == FileStatus.PENDING:
            cut_row = ctk.CTkFrame(item, fg_color="transparent")
            cut_row.pack(fill="x", padx=12, pady=(0, 8))

            use_custom = ctk.CTkCheckBox(
                cut_row, text="Custom cut:", font=ctk.CTkFont(size=11), width=100
            )
            use_custom.pack(side="left", padx=(0, 8))
            if file.use_custom_cut:
                use_custom.select()

            start_label = ctk.CTkLabel(
                cut_row, text="Start:", font=ctk.CTkFont(size=10), width=40
            )
            start_label.pack(side="left", padx=(0, 4))
            start_entry = ctk.CTkEntry(
                cut_row, width=80, height=24, placeholder_text="0:00"
            )
            start_entry.pack(side="left", padx=(0, 8))
            if file.custom_cut_start_seconds is not None:
                start_entry.insert(
                    0, self._seconds_to_time(file.custom_cut_start_seconds)
                )

            end_label = ctk.CTkLabel(
                cut_row, text="End:", font=ctk.CTkFont(size=10), width=30
            )
            end_label.pack(side="left", padx=(0, 4))
            end_entry = ctk.CTkEntry(
                cut_row, width=80, height=24, placeholder_text="(end)"
            )
            end_entry.pack(side="left")
            if file.custom_cut_end_seconds is not None:
                end_entry.insert(0, self._seconds_to_time(file.custom_cut_end_seconds))

            def update_cut():
                file.use_custom_cut = use_custom.get() == 1
                try:
                    start_text = start_entry.get().strip()
                    file.custom_cut_start_seconds = (
                        self._time_to_seconds(start_text) if start_text else None
                    )
                except Exception:
                    file.custom_cut_start_seconds = None
                try:
                    end_text = end_entry.get().strip()
                    file.custom_cut_end_seconds = (
                        self._time_to_seconds(end_text) if end_text else None
                    )
                except Exception:
                    file.custom_cut_end_seconds = None

            use_custom.configure(command=update_cut)
            start_entry.bind("<KeyRelease>", lambda e: update_cut())
            end_entry.bind("<KeyRelease>", lambda e: update_cut())

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

        if file.error:
            err_display = (
                file.error[:200] + "..." if len(file.error) > 200 else file.error
            )
            ctk.CTkLabel(
                item,
                text=f"Error: {err_display}",
                font=ctk.CTkFont(size=11),
                text_color="#fca5a5",
                wraplength=500,
                anchor="w",
                justify="left",
            ).pack(anchor="w", padx=12, pady=(0, 8), fill="x")

    def _on_cut_mode_change(self):
        mode = self.cut_mode_var.get()
        self.state.cut_mode = CutMode(mode)

    def _on_delogo_toggle(self):
        """Toggle delogo section visibility"""
        self.state.apply_delogo = self.delogo_checkbox.get() == 1
        if self.state.apply_delogo:
            self.delogo_params_frame.pack(fill="x", padx=16, pady=(0, 16))
        else:
            self.delogo_params_frame.pack_forget()

    def _on_delogo_param_change(self, index: int):
        """Update delogo params"""
        try:
            v = int(self.delogo_inputs[index].get())
            p = self.state.delogo_params
            if index == 0:
                p.x = v
            elif index == 1:
                p.y = v
            elif index == 2:
                p.w = v
            elif index == 3:
                p.h = v
        except ValueError:
            pass

    def _on_pick_logo_area(self):
        """Open the visual logo picker dialog."""
        files = self._files()
        if not files:
            messagebox.showwarning(
                "No Video Selected",
                "Add a video file first so the picker can show a frame.",
            )
            return
        video_path = files[0].path
        initial = (
            self.state.delogo_params.x,
            self.state.delogo_params.y,
            self.state.delogo_params.w,
            self.state.delogo_params.h,
        )
        LogoPickerDialog(
            parent=self,
            video_path=video_path,
            on_apply=self._apply_logo_rect,
            initial_rect=initial,
        )

    def _on_parse_pasted_coords(self):
        """Parse pasted coordinate text and fill the delogo fields."""
        text = self.paste_coords_entry.get().strip()
        if not text:
            return
        parsed = parse_logo_coordinates(text)
        if parsed is None:
            messagebox.showwarning(
                "Could Not Parse",
                "Paste 4 numbers in one of these formats:\n"
                "  100, 200, 50, 30\n"
                "  x=100 y=200 w=50 h=30",
            )
            return
        self._apply_logo_rect(*parsed)
        self.paste_coords_entry.delete(0, "end")

    def _apply_logo_rect(self, x: int, y: int, w: int, h: int):
        """Fill the delogo X/Y/W/H fields and enable the filter."""
        print(f"[LogoPicker] Applying: x={x}, y={y}, w={w}, h={h}")
        self.state.delogo_params.x = x
        self.state.delogo_params.y = y
        self.state.delogo_params.w = w
        self.state.delogo_params.h = h

        for idx, val in enumerate([x, y, w, h]):
            self.delogo_inputs[idx].delete(0, "end")
            self.delogo_inputs[idx].insert(0, str(val))

        self.state.apply_delogo = True
        self.delogo_checkbox.select()
        self.delogo_params_frame.pack(fill="x", padx=16, pady=(0, 16))
        print(f"[LogoPicker] Done. State now: {self.state.delogo_params}")

    def _on_detect_logo(self):
        """Start logo detection on the first video in queue"""
        # Check if we have files
        if not self._files():
            messagebox.showwarning(
                "No Video Selected",
                "Please add at least one video file before detecting logos.",
            )
            return

        # Get first video (ProcessingFile)
        first_video = self._files()[0]

        # Check if file exists
        if not os.path.exists(first_video.path):
            messagebox.showerror(
                "File Not Found", f"Video file not found:\n{first_video.path}"
            )
            return

        # Disable button during detection
        self.detect_button.configure(state="disabled", text="Detecting...")
        self.cancel_detect_button.pack(side="left", padx=(0, 12))
        self._detection_cancelled = False
        self.detection_status_label.configure(
            text="Starting detection...", text_color="#2563eb"
        )

        # Run detection in background thread
        selected_method = self.detection_method_var.get()
        use_vision = (
            vision_detector_available()
            and selected_method == "Google Cloud Vision (AI)"
        )
        use_temporal = selected_method == "Temporal Stability (recommended)"

        def run_detection():
            try:
                config = DetectionConfig(
                    sensitivity=self.sensitivity_slider.get(),
                    frame_sampling=30,
                )

                if use_vision and VisionLogoDetector is not None:
                    detector = VisionLogoDetector(config)
                elif use_temporal:
                    detector = TemporalLogoDetector(config)
                else:
                    detector = LogoDetector(config)

                def progress_callback(progress: float, status: str):
                    def update():
                        self.detection_status_label.configure(text=status)

                    self.after(0, update)

                def cancel_check():
                    return self._detection_cancelled

                session = detector.detect_in_video(
                    first_video.path,
                    progress_callback=progress_callback,
                    cancel_check=cancel_check,
                )

                # Store session
                self.current_detection_session = session

                # Update UI with results on main thread
                self.after(0, lambda: self._show_detection_results(session))

            except Exception as exc:
                # Show error on main thread (bind as default arg so the lambda
                # captures the value rather than the late-bound closure variable)
                self.after(0, lambda e=exc: self._on_detection_error(str(e)))

        # Start detection thread
        detection_thread = threading.Thread(target=run_detection, daemon=True)
        detection_thread.start()

    def _show_detection_results(self, session: DetectionSession):
        """Display detection results in the UI"""
        # Re-enable button and hide cancel button
        self.detect_button.configure(state="normal", text="🔍 Detect Logo")
        self.cancel_detect_button.pack_forget()

        if session.status == "cancelled":
            self.detection_status_label.configure(
                text="Detection cancelled", text_color="#F59E0B"
            )
            return

        if session.status == "error":
            self.detection_status_label.configure(
                text=f"Detection failed: {session.error_message}", text_color="#B91C1C"
            )
            return

        # Show results
        num_results = len(session.results)

        if num_results == 0:
            self.detection_status_label.configure(
                text="No on-screen graphics detected — video appears clean. You can leave OSG removal off.",
                text_color="#10B981",
            )
            # Show results area so user sees detection ran and concluded "no logos"
            for widget in self.detection_results_frame.winfo_children():
                widget.destroy()
            self.detection_results_frame.pack(fill="x", pady=(0, 0))
            ctk.CTkLabel(
                self.detection_results_frame,
                text="Detection complete: no logo regions found in sampled frames.",
                font=ctk.CTkFont(size=12),
                text_color="#64748B",
            ).pack(anchor="w", padx=12, pady=12)
            return

        # Update status
        self.detection_status_label.configure(
            text=f"Found {num_results} logo region(s). Click to apply:",
            text_color="#10B981",
        )

        # Clear previous results
        for widget in self.detection_results_frame.winfo_children():
            widget.destroy()

        # Show results frame
        self.detection_results_frame.pack(fill="x", pady=(0, 0))

        # Results header
        results_header = ctk.CTkFrame(
            self.detection_results_frame, fg_color="transparent"
        )
        results_header.pack(fill="x", padx=12, pady=(12, 8))

        ctk.CTkLabel(
            results_header,
            text="Detected Regions (highest score first):",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#0f172a",
        ).pack(anchor="w")

        # List results sorted by confidence (most sure first)
        sorted_results = sorted(
            session.results, key=lambda r: r.confidence, reverse=True
        )
        for i, result in enumerate(sorted_results[:10]):  # Limit to 10 results
            result_row = ctk.CTkFrame(
                self.detection_results_frame, fg_color="#0f172a", corner_radius=8
            )
            result_row.pack(fill="x", padx=12, pady=4)

            # Result info with confidence emphasis
            # For temporal detector, the score reflects stability, not edge density.
            conf_pct = result.confidence * 100
            score_label = "Stability" if result.detection_method == "temporal" else "Confidence"
            info_text = f"Region #{i + 1}: ({result.x}, {result.y}) {result.width}x{result.height}  |  {score_label}: {conf_pct:.0f}%"

            result_label = ctk.CTkLabel(
                result_row,
                text=info_text,
                font=ctk.CTkFont(size=12),
                text_color="#0f172a",
                anchor="w",
            )
            result_label.pack(side="left", fill="x", expand=True, padx=12, pady=8)

            # Apply button
            apply_btn = ctk.CTkButton(
                result_row,
                text="Apply",
                command=lambda r=result: self._apply_detection_result(r),
                width=80,
                height=28,
                fg_color="#10B981",
                hover_color="#059669",
            )
            apply_btn.pack(side="right", padx=12)

        if num_results > 10:
            ctk.CTkLabel(
                self.detection_results_frame,
                text=f"...and {num_results - 10} more",
                font=ctk.CTkFont(size=11),
                text_color="#64748B",
            ).pack(anchor="w", padx=12, pady=(0, 12))

    def _apply_detection_result(self, result):
        """Apply a detection result to delogo parameters (with slight padding for full coverage)."""
        # Get delogo params from result
        x, y, w, h = result.to_delogo_params()
        # Add small padding so FFmpeg delogo blur fully covers logo edges
        padding = 4
        w = max(1, w + padding)
        h = max(1, h + padding)

        # Update state
        self.state.delogo_params.x = x
        self.state.delogo_params.y = y
        self.state.delogo_params.w = w
        self.state.delogo_params.h = h

        # Update UI inputs
        self.delogo_inputs[0].delete(0, "end")
        self.delogo_inputs[0].insert(0, str(x))
        self.delogo_inputs[1].delete(0, "end")
        self.delogo_inputs[1].insert(0, str(y))
        self.delogo_inputs[2].delete(0, "end")
        self.delogo_inputs[2].insert(0, str(w))
        self.delogo_inputs[3].delete(0, "end")
        self.delogo_inputs[3].insert(0, str(h))

        # Enable delogo filter
        self.state.apply_delogo = True
        self.delogo_checkbox.select()
        self.delogo_params_frame.pack(fill="x", padx=16, pady=(0, 16))

        # Show confirmation
        self.detection_status_label.configure(
            text=f"✓ Applied region ({x}, {y}) {w}x{h} to OSG removal",
            text_color="#10B981",
        )

    def _on_detection_error(self, error_msg: str):
        """Handle detection error"""
        self.detect_button.configure(state="normal", text="🔍 Detect Logo")
        self.cancel_detect_button.pack_forget()
        self.detection_status_label.configure(
            text=f"Detection failed: {error_msg}", text_color="#B91C1C"
        )
        messagebox.showerror(
            "Detection Error", f"Logo detection failed:\n\n{error_msg}"
        )

    def _on_cancel_detection(self):
        """Cancel ongoing logo detection"""
        self._detection_cancelled = True
        self.detection_status_label.configure(
            text="Cancelling detection...", text_color="#F59E0B"
        )
        self.cancel_detect_button.configure(state="disabled")

    def _on_save_profile(self):
        """Save current detection settings as a profile"""
        # Create simple dialog for profile name
        dialog = ctk.CTkInputDialog(
            text="Enter profile name:", title="Save Detection Profile"
        )

        profile_name = dialog.get_input()

        if not profile_name or not profile_name.strip():
            return

        profile_name = profile_name.strip()

        try:
            from src.detection_profiles import save_profile, profile_exists
            from src.data_models import DetectionProfile

            # Check if profile exists
            if profile_exists(profile_name):
                result = messagebox.askyesno(
                    "Profile Exists",
                    f"Profile '{profile_name}' already exists. Overwrite?",
                )
                if not result:
                    return

            # Create profile with current settings
            config = DetectionConfig(
                sensitivity=self.sensitivity_slider.get(),
            )

            profile = DetectionProfile(
                name=profile_name,
                description=f"Custom profile created {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                config=config,
            )

            # Save profile
            save_profile(profile)

            # Refresh profile list
            self._refresh_profile_list()

            # Show success message
            self.detection_status_label.configure(
                text=f"✓ Profile '{profile_name}' saved", text_color="#10B981"
            )

        except Exception as e:
            messagebox.showerror("Save Failed", f"Failed to save profile:\n\n{e}")

    def _on_load_profile(self, profile_name: str):
        """Load a detection profile"""
        if profile_name == "Select profile...":
            return

        try:
            from src.detection_profiles import load_profile

            # Load profile
            profile = load_profile(profile_name)

            # Apply settings to UI
            self.sensitivity_slider.set(profile.config.sensitivity)
            self.sensitivity_label.configure(
                text=f"{int(profile.config.sensitivity * 100)}%"
            )

            # Show success message
            self.detection_status_label.configure(
                text=f"✓ Profile '{profile_name}' loaded", text_color="#10B981"
            )

        except Exception as e:
            messagebox.showerror("Load Failed", f"Failed to load profile:\n\n{e}")

    def _refresh_profile_list(self):
        """Refresh the profile dropdown list"""
        try:
            from src.detection_profiles import list_profiles

            profiles = list_profiles()
            profile_names = [p["name"] for p in profiles]

            if not profile_names:
                profile_names = ["Select profile..."]
            else:
                profile_names = ["Select profile..."] + profile_names

            self.profile_dropdown.configure(values=profile_names)
            self.profile_var.set("Select profile...")

        except Exception:
            # Silently fail - profiles are optional
            pass

    def _on_format_change(self, value: str):
        """Update output format"""
        self.state.output_format = value

    def _on_subfolder_toggle(self):
        """Toggle create output subfolder"""
        self.state.create_output_subfolder = self.subfolder_cb.get() == 1

    def _on_overwrite_toggle(self):
        """Toggle overwrite existing"""
        self.state.overwrite_existing = self.overwrite_cb.get() == 1

    def _on_profile_change(self, value: str):
        """Update processing profile and description"""
        from src.state import PROCESSING_PROFILES

        self.state.processing_profile = value
        self.profile_desc_label.configure(text=PROCESSING_PROFILES[value].description)

    def _on_cut_mode_change(self):
        pass

    def _sync_state_from_ui(self):
        self.state.cut_start_enabled = self.cut_start_enabled_cb.get() == 1
        self.state.cut_end_enabled = self.cut_end_enabled_cb.get() == 1

        try:
            self.state.cut_start_hours = float(self.cut_start_hours_entry.get() or "0")
        except ValueError:
            self.state.cut_start_hours = 0.0
        try:
            self.state.cut_start_minutes = float(
                self.cut_start_minutes_entry.get() or "0"
            )
        except ValueError:
            self.state.cut_start_minutes = 0.0
        try:
            self.state.cut_start_seconds = float(
                self.cut_start_seconds_entry.get() or "0"
            )
        except ValueError:
            self.state.cut_start_seconds = 0.0

        try:
            self.state.cut_end_hours_amount = float(
                self.cut_end_hours_entry.get() or "0"
            )
        except ValueError:
            self.state.cut_end_hours_amount = 0.0
        try:
            self.state.cut_end_minutes_amount = float(
                self.cut_end_minutes_entry.get() or "0"
            )
        except ValueError:
            self.state.cut_end_minutes_amount = 0.0
        try:
            self.state.cut_end_seconds_amount = float(
                self.cut_end_seconds_entry.get() or "0"
            )
        except ValueError:
            self.state.cut_end_seconds_amount = 0.0

        # Sync markers and split inputs
        self.state.cut_markers_start = self.markers_start_entry.get()
        self.state.cut_markers_end = self.markers_end_entry.get()
        try:
            self.state.split_parts = max(1, int(self.split_parts_entry.get() or 2))
        except ValueError:
            self.state.split_parts = 2

        self.state.output_format = self.format_var.get()
        self.state.output_prefix = self.prefix_entry.get()
        self.state.output_suffix = self.suffix_entry.get()

        self.state.rename_enabled = self.rename_checkbox.get() == 1
        self.state.rename_base = self.rename_base_entry.get().strip()
        try:
            self.state.rename_start = max(0, int(self.rename_start_entry.get() or 1))
        except ValueError:
            self.state.rename_start = 1
        try:
            self.state.rename_pad = max(1, int(self.rename_pad_entry.get() or 2))
        except ValueError:
            self.state.rename_pad = 2

    def _check_ffmpeg(self) -> bool:
        """Verify FFmpeg is available. Returns True if OK."""
        import subprocess

        try:
            subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
            return True
        except FileNotFoundError:
            messagebox.showerror(
                "FFmpeg Not Found",
                "FFmpeg is not installed or not in your PATH.\n\n"
                "Please install FFmpeg from https://ffmpeg.org/download.html\n"
                "and add it to your system PATH.",
            )
            return False
        except Exception as e:
            messagebox.showerror("Error", f"Could not run FFmpeg: {e}")
            return False

    def _validate_inputs(self) -> tuple[bool, str]:
        """Validate user inputs before processing"""
        # Validate time inputs are numeric
        try:
            hours = float(self.cut_hours_entry.get() or "0")
            mins = float(self.cut_minutes_entry.get() or "0")
            secs = float(self.cut_seconds_entry.get() or "0")
            if hours < 0 or mins < 0 or secs < 0:
                return False, "Time values cannot be negative"
        except ValueError:
            return False, "Hours, minutes and seconds must be valid numbers"

        # Validate range mode
        if self.state.cut_mode == CutMode.CUT_RANGE:
            try:
                start_hours = float(self.cut_start_hours_entry.get() or "0")
                start_mins = float(self.cut_start_entry.get() or "0")
                start_secs = float(self.cut_start_sec_entry.get() or "0")
                end_hours_str = self.cut_end_hours_entry.get().strip()
                end_mins_str = self.cut_end_entry.get().strip()
                end_secs_str = self.cut_end_sec_entry.get().strip()

                if start_hours < 0 or start_mins < 0 or start_secs < 0:
                    return False, "Start time cannot be negative"

                if end_hours_str or end_mins_str or end_secs_str:
                    end_hours = float(end_hours_str) if end_hours_str else 0
                    end_mins = float(end_mins_str) if end_mins_str else 0
                    end_secs = float(end_secs_str) if end_secs_str else 0

                    if end_hours < 0 or end_mins < 0 or end_secs < 0:
                        return False, "End time cannot be negative"

                    start_total = (start_hours * 3600) + (start_mins * 60) + start_secs
                    end_total = (end_hours * 3600) + (end_mins * 60) + end_secs

                    if end_total <= start_total:
                        return False, "End time must be after start time"
            except ValueError:
                return False, "Range times must be valid numbers"

        return True, ""

    def _start_processing(self):
        """Start batch processing"""
        if not self._files():
            messagebox.showerror(
                "Error",
                "No files selected.\n\nAdd files using 'Select Files' or 'Select Folder'.",
            )
            self.state.add_log("Error: No files selected")
            return
        if not self._output_folder_raw():
            messagebox.showerror(
                "Error",
                "No output folder selected.\n\nClick 'Browse' next to Output folder to choose where to save processed videos.",
            )
            self.state.add_log("Error: Select an output folder")
            return
        if not self._check_ffmpeg():
            return

        # Validate inputs
        valid, error_msg = self._validate_inputs()
        if not valid:
            messagebox.showerror("Invalid Input", error_msg)
            self.state.add_log(f"Validation error: {error_msg}")
            return

        self._sync_state_from_ui()

        thread = threading.Thread(target=self._process_thread, daemon=True)
        thread.start()

        self.start_btn.pack_forget()
        self.stop_btn.pack(side="left", padx=(0, 12))
        # Reveal the aggregate batch progress header and start the tick loop.
        try:
            self._batch_wrap.pack(fill="x", padx=16, pady=(0, 10), before=self.file_list_frame)
        except Exception:
            self._batch_wrap.pack(fill="x", padx=16, pady=(0, 10))
        if self.batch_progress_bar is not None:
            self.batch_progress_bar.set(0.0)
        if self.batch_summary_label is not None:
            self.batch_summary_label.configure(text="0 / 0  •  0%")
        # Reset status cache so the first tick detects the initial PENDING state
        # and only rebuilds when a real transition (PENDING->PROCESSING) occurs.
        self._last_statuses = {}
        # Cancel any stale tick from a previous run, then prime a fresh one.
        if self._tick_after_id is not None:
            try:
                self.after_cancel(self._tick_after_id)
            except Exception:
                pass
        self._tick_after_id = self.after(150, self._tick_progress)

    def _format_batch_summary(self, overall, avg_speed, completed, total):
        pct = overall * 100.0
        if avg_speed is not None:
            return f"{completed} / {total}  •  {pct:.0f}%  •  avg {avg_speed:.1f}x"
        return f"{completed} / {total}  •  {pct:.0f}%"

    def _tick_progress(self):
        """Periodic UI refresh (runs on the GUI thread via after()).

        Updates persistent per-row widgets in place and the aggregate header.
        Rebuilds the file list only when a file's status transitions (e.g.
        PENDING->PROCESSING or PROCESSING->COMPLETED), so the per-row progress
        sub-frame is created/removed at exactly the right moments.
        """
        from src.state import compute_batch_progress  # local import avoids cycle

        files = self._files()

        # Detect status transitions. Build the new status map first, then
        # compare against the PREVIOUS map. The rebuild must NOT reset this map.
        current_statuses = {f.id: f.status for f in files}
        changed = current_statuses != self._last_statuses
        if changed:
            self._update_file_list()
        # Persist AFTER rebuild so it survives across ticks.
        self._last_statuses = current_statuses

        # Per-row in-place updates (only for PROCESSING rows with refs).
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

    def _stop_processing(self):
        """Stop processing"""
        # Cancel the tick loop.
        if self._tick_after_id is not None:
            try:
                self.after_cancel(self._tick_after_id)
            except Exception:
                pass
            self._tick_after_id = None
        self._set_processing(False)
        self.state.add_log("Processing stopped by user")
        self.stop_btn.pack_forget()
        self.start_btn.pack(side="left", padx=(0, 12))

    def _process_thread(self):
        """Process queue in background"""
        failed_errors = []
        try:
            output_override = (
                self._output_folder_raw() if self.task_index != 0 else None
            )

            def on_done():
                self._set_processing(False)
                self.after(0, lambda: self._on_processing_complete(failed_errors))

            self._set_processing(True)
            self.processor.process_queue(
                self._files(),
                on_file_error=lambda name, err: failed_errors.append((name, err)),
                output_folder_override=output_override,
                on_complete=lambda: self.after(0, on_done),
            )
        except Exception as e:
            self.state.add_log(f"Processing error: {str(e)}")
            failed_errors.append(("", str(e)))
            self._set_processing(False)
            self.after(0, lambda: self._on_processing_complete(failed_errors))

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

        if failed_errors:
            name, err = failed_errors[0]
            summary = f"{err}" if not name else f"{name}:\n\n{err}"
            if len(failed_errors) > 1:
                summary += f"\n\n(... and {len(failed_errors) - 1} more - see Logs for details)"
            messagebox.showerror("Processing Failed", summary)

    def _on_log_update(self, message: str):
        pass

    def _time_to_seconds(self, time_str: str) -> float:
        """Convert MM:SS or HH:MM:SS to seconds"""
        parts = time_str.split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        return float(time_str)

    def _seconds_to_time(self, seconds: float) -> str:
        """Convert seconds to MM:SS or HH:MM:SS"""
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = seconds % 60
        if h > 0:
            return f"{h}:{m:02d}:{s:05.2f}"
        return f"{m}:{s:05.2f}"

    def _init_drag_drop(self):
        """Initialize drag-drop handler for the file list frame"""
        try:
            self.drag_drop_handler = DragDropHandler(
                self.file_list_frame, self._on_files_dropped
            )
            self.drag_drop_handler.enable()
            self.state.add_log(
                "Drag-drop enabled - drag video files onto the file list"
            )
        except Exception as e:
            self.state.add_log(f"Warning: Could not enable drag-drop: {e}")
            # Drag-drop is optional, continue without it

    def _on_files_dropped(self, file_paths: List[str]):
        """
        Handle files dropped onto the file list.

        Args:
            file_paths: List of video file paths from drag-drop
        """
        if not file_paths:
            return

        # Check for duplicates
        existing_paths = {f.path for f in self._files()}
        new_files = [p for p in file_paths if p not in existing_paths]
        duplicate_count = len(file_paths) - len(new_files)

        if duplicate_count > 0:
            messagebox.showinfo(
                "Duplicate Files",
                f"Skipped {duplicate_count} duplicate file(s). "
                f"Added {len(new_files)} new file(s).",
            )

        # Add new files to state
        for path in new_files:
            file_obj = ProcessingFile(
                id=str(len(self._files()) + 1), path=path, name=os.path.basename(path)
            )
            self._files().append(file_obj)

        # Update UI
        self._update_file_list()

        if new_files:
            self.state.add_log(f"Added {len(new_files)} file(s) via drag-drop")
