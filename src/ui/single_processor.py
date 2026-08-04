"""
Single File Processor UI Component
"""

import customtkinter as ctk
import tkinter.filedialog as filedialog
from tkinter import messagebox
import threading
import os

from src.state import AppState, CutMode
from src.video_processor import VideoProcessor


class SingleProcessorFrame(ctk.CTkScrollableFrame):
    """Single file processor view"""

    def __init__(self, parent, state: AppState, processor: VideoProcessor):
        super().__init__(parent, fg_color="#0f172a")
        self.state = state
        self.processor = processor
        self.selected_file = None

        self._create_ui()

        # Register for log updates
        self.state.register_log_callback(self._on_log_update)

    def _create_ui(self):
        """Create the UI components"""
        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", pady=(0, 20))

        title = ctk.CTkLabel(
            header,
            text="Single File Processor",
            font=ctk.CTkFont(size=32, weight="bold"),
        )
        title.pack(anchor="w")

        subtitle = ctk.CTkLabel(
            header,
            text="Process a single video file",
            font=ctk.CTkFont(size=14),
            text_color="#60a5fa",
        )
        subtitle.pack(anchor="w", pady=(5, 0))

        # Processing Options
        self._create_options_section()

        # File Selection
        self._create_file_section()

        # Progress Display
        self.progress_frame = None

        # Action Button
        self._create_action_button()

    def _create_options_section(self):
        options_frame = ctk.CTkFrame(self, fg_color="#0f172a")
        options_frame.pack(fill="x", pady=(0, 20))

        title = ctk.CTkLabel(
            options_frame,
            text="Processing Options",
            font=ctk.CTkFont(size=18, weight="bold"),
        )
        title.pack(anchor="w", padx=20, pady=(20, 15))

        options_content = ctk.CTkFrame(options_frame, fg_color="transparent")
        options_content.pack(fill="x", padx=20, pady=(0, 20))

        trim_label = ctk.CTkLabel(
            options_content,
            text="Trim / Cut Options",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        trim_label.pack(anchor="w", pady=(0, 4))

        hint_label = ctk.CTkLabel(
            options_content,
            text="Works on any video length - no need to know exact duration",
            font=ctk.CTkFont(size=11),
            text_color="#64748B",
        )
        hint_label.pack(anchor="w", pady=(0, 8))

        self.cut_mode_var = ctk.StringVar(value="cut_last")

        row1 = ctk.CTkFrame(options_content, fg_color="transparent")
        row1.pack(fill="x", pady=4)
        ctk.CTkRadioButton(
            row1,
            text="No trim (keep full video)",
            variable=self.cut_mode_var,
            value="none",
            command=self._on_cut_mode_change,
            font=ctk.CTkFont(size=13),
        ).pack(side="left")

        row2 = ctk.CTkFrame(options_content, fg_color="transparent")
        row2.pack(fill="x", pady=4)
        ctk.CTkRadioButton(
            row2,
            text="Remove from START:",
            variable=self.cut_mode_var,
            value="cut_first",
            command=self._on_cut_mode_change,
            font=ctk.CTkFont(size=13),
        ).pack(side="left", padx=(0, 8))
        self.cut_first_hours = ctk.CTkEntry(row2, width=50, height=28)
        self.cut_first_hours.insert(0, "0")
        self.cut_first_hours.pack(side="left", padx=(0, 2))
        ctk.CTkLabel(
            row2, text="h", font=ctk.CTkFont(size=11), text_color="#64748B"
        ).pack(side="left", padx=(0, 6))
        self.cut_first_minutes = ctk.CTkEntry(row2, width=50, height=28)
        self.cut_first_minutes.insert(0, "0")
        self.cut_first_minutes.pack(side="left", padx=(0, 2))
        ctk.CTkLabel(
            row2, text="m", font=ctk.CTkFont(size=11), text_color="#64748B"
        ).pack(side="left", padx=(0, 6))
        self.cut_first_seconds = ctk.CTkEntry(row2, width=50, height=28)
        self.cut_first_seconds.insert(0, "0")
        self.cut_first_seconds.pack(side="left", padx=(0, 2))
        ctk.CTkLabel(
            row2, text="s", font=ctk.CTkFont(size=11), text_color="#64748B"
        ).pack(side="left")

        row3 = ctk.CTkFrame(options_content, fg_color="transparent")
        row3.pack(fill="x", pady=4)
        ctk.CTkRadioButton(
            row3,
            text="Remove from END:",
            variable=self.cut_mode_var,
            value="cut_last",
            command=self._on_cut_mode_change,
            font=ctk.CTkFont(size=13),
        ).pack(side="left", padx=(0, 8))
        self.cut_last_hours = ctk.CTkEntry(row3, width=50, height=28)
        self.cut_last_hours.insert(0, "0")
        self.cut_last_hours.pack(side="left", padx=(0, 2))
        ctk.CTkLabel(
            row3, text="h", font=ctk.CTkFont(size=11), text_color="#64748B"
        ).pack(side="left", padx=(0, 6))
        self.cut_last_minutes = ctk.CTkEntry(row3, width=50, height=28)
        self.cut_last_minutes.insert(0, "5")
        self.cut_last_minutes.pack(side="left", padx=(0, 2))
        ctk.CTkLabel(
            row3, text="m", font=ctk.CTkFont(size=11), text_color="#64748B"
        ).pack(side="left", padx=(0, 6))
        self.cut_last_seconds = ctk.CTkEntry(row3, width=50, height=28)
        self.cut_last_seconds.insert(0, "0")
        self.cut_last_seconds.pack(side="left", padx=(0, 2))
        ctk.CTkLabel(
            row3, text="s", font=ctk.CTkFont(size=11), text_color="#64748B"
        ).pack(side="left")

        self.cut_hours_entry = self.cut_last_hours
        self.cut_minutes_entry = self.cut_last_minutes
        self.cut_seconds_entry = self.cut_last_seconds
        self.cut_time_frame = None

        from src.state import PROCESSING_PROFILES

        ctk.CTkLabel(options_content, text="", height=5).pack()

        profile_label = ctk.CTkLabel(
            options_content,
            text="Processing Profile:",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        profile_label.pack(anchor="w", pady=(10, 5))

        profile_frame = ctk.CTkFrame(options_content, fg_color="transparent")
        profile_frame.pack(anchor="w", padx=30, pady=(0, 5))

        self.profile_var = ctk.StringVar(value=self.state.processing_profile)

        profile_menu = ctk.CTkOptionMenu(
            profile_frame,
            values=list(PROCESSING_PROFILES.keys()),
            variable=self.profile_var,
            command=self._on_profile_change,
            width=300,
            height=36,
        )
        profile_menu.pack(side="left")

        self.profile_desc_label = ctk.CTkLabel(
            options_content,
            text=PROCESSING_PROFILES[self.state.processing_profile].description,
            font=ctk.CTkFont(size=11),
            text_color="#60a5fa",
            wraplength=450,
            anchor="w",
            justify="left",
        )
        self.profile_desc_label.pack(anchor="w", padx=30, pady=(0, 10))

        self.delogo_checkbox = ctk.CTkCheckBox(
            options_content,
            text="Remove On-Screen Graphics (OSG)",
            command=self._on_delogo_toggle,
            font=ctk.CTkFont(size=14),
        )
        self.delogo_checkbox.pack(anchor="w", pady=5)
        if self.state.apply_delogo:
            self.delogo_checkbox.select()

        self.delogo_params_frame = ctk.CTkFrame(options_content, fg_color="transparent")
        self.delogo_params_frame.pack(fill="x", padx=30, pady=(10, 0))

        params = self.state.delogo_params
        labels = ["X", "Y", "Width", "Height"]
        values = [params.x, params.y, params.w, params.h]

        self.delogo_inputs = []
        for i, (label, value) in enumerate(zip(labels, values)):
            frame = ctk.CTkFrame(self.delogo_params_frame, fg_color="transparent")
            frame.grid(row=0, column=i, padx=5, pady=5, sticky="ew")
            self.delogo_params_frame.grid_columnconfigure(i, weight=1)

            lbl = ctk.CTkLabel(frame, text=label, font=ctk.CTkFont(size=12))
            lbl.pack(anchor="w", padx=5)

            entry = ctk.CTkEntry(frame, width=80)
            entry.insert(0, str(value))
            entry.pack(fill="x", padx=5)
            entry.bind(
                "<KeyRelease>", lambda e, idx=i: self._on_delogo_param_change(idx)
            )
            self.delogo_inputs.append(entry)

        if not self.state.apply_delogo:
            self.delogo_params_frame.pack_forget()

    def _create_file_section(self):
        """Create file selection section"""
        file_frame = ctk.CTkFrame(self, fg_color="#0f172a")
        file_frame.pack(fill="x", pady=(0, 20))

        content = ctk.CTkFrame(file_frame, fg_color="transparent")
        content.pack(fill="x", padx=20, pady=20)

        # Input File
        input_frame = ctk.CTkFrame(content, fg_color="transparent")
        input_frame.pack(fill="x", pady=(0, 15))

        input_label = ctk.CTkLabel(
            input_frame,
            text="Input File",
            font=ctk.CTkFont(size=14),
            text_color="#60a5fa",
        )
        input_label.pack(anchor="w", pady=(0, 5))

        self.input_file_btn = ctk.CTkButton(
            input_frame,
            text="Select Video File",
            command=self._select_file,
            height=40,
            anchor="w",
            fg_color="#0f172a",
            hover_color="#0f172a",
        )
        self.input_file_btn.pack(fill="x")

        # Output Folder
        output_frame = ctk.CTkFrame(content, fg_color="transparent")
        output_frame.pack(fill="x")

        output_label = ctk.CTkLabel(
            output_frame,
            text="Output Folder",
            font=ctk.CTkFont(size=14),
            text_color="#60a5fa",
        )
        output_label.pack(anchor="w", pady=(0, 5))

        self.output_folder_btn = ctk.CTkButton(
            output_frame,
            text="Select Output Folder",
            command=self._select_output_folder,
            height=40,
            anchor="w",
            fg_color="#0f172a",
            hover_color="#0f172a",
        )
        self.output_folder_btn.pack(fill="x")

    def _create_action_button(self):
        """Create action button"""
        self.process_btn = ctk.CTkButton(
            self,
            text="Process Video",
            command=self._process_file,
            height=50,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color="#2563eb",
            hover_color="#3b82f6",
        )
        self.process_btn.pack(fill="x", pady=(0, 20))

    def _select_file(self):
        """Select input video file"""
        file = filedialog.askopenfilename(
            title="Select Video File",
            filetypes=[
                ("Video Files", "*.mp4 *.mkv *.avi *.mov *.m4v"),
                ("All Files", "*.*"),
            ],
        )

        if file:
            self.selected_file = file
            filename = os.path.basename(file)
            self.input_file_btn.configure(text=filename)
            self.state.add_log(f"Selected file: {file}")

    def _select_output_folder(self):
        """Select output folder"""
        folder = filedialog.askdirectory(title="Select Output Folder")
        if folder:
            self.state.output_folder = folder
            self.output_folder_btn.configure(text=folder)
            self.state.add_log(f"Selected output folder: {folder}")

    def _on_cut_mode_change(self):
        mode = self.cut_mode_var.get()
        self.state.cut_mode = CutMode(mode)
        if mode == "none":
            self.cut_time_frame.pack_forget()
        else:
            self.cut_time_frame.pack(anchor="w", padx=30, pady=(0, 10))
            try:
                self.state.cut_hours = float(self.cut_hours_entry.get() or "0")
            except ValueError:
                self.state.cut_hours = 0.0
            try:
                self.state.cut_minutes = float(self.cut_minutes_entry.get() or "5")
            except ValueError:
                self.state.cut_minutes = 5.0
            try:
                self.state.cut_seconds = float(self.cut_seconds_entry.get() or "0")
            except ValueError:
                self.state.cut_seconds = 0.0

    def _on_delogo_toggle(self):
        """Handle delogo checkbox toggle"""
        self.state.apply_delogo = self.delogo_checkbox.get() == 1
        if self.state.apply_delogo:
            self.delogo_params_frame.pack(fill="x", padx=30, pady=(10, 0))
        else:
            self.delogo_params_frame.pack_forget()

    def _on_delogo_param_change(self, index: int):
        """Handle delogo parameter change"""
        try:
            value = int(self.delogo_inputs[index].get())
            params = self.state.delogo_params
            if index == 0:
                params.x = value
            elif index == 1:
                params.y = value
            elif index == 2:
                params.w = value
            elif index == 3:
                params.h = value
        except ValueError:
            pass

    def _on_profile_change(self, value: str):
        """Update processing profile and description"""
        from src.state import PROCESSING_PROFILES

        self.state.processing_profile = value
        self.profile_desc_label.configure(text=PROCESSING_PROFILES[value].description)

    def _validate_inputs(self) -> tuple[bool, str]:
        if self.cut_mode_var.get() != "none":
            try:
                hours = float(self.cut_hours_entry.get() or "0")
                mins = float(self.cut_minutes_entry.get() or "0")
                secs = float(self.cut_seconds_entry.get() or "0")
                if hours < 0 or mins < 0 or secs < 0:
                    return False, "Time values cannot be negative"
                total = hours * 3600 + mins * 60 + secs
                if total == 0:
                    return False, "Cut time cannot be zero when trim is enabled"
            except ValueError:
                return False, "Hours, minutes and seconds must be valid numbers"
        return True, ""

    def _process_file(self):
        if not self.selected_file:
            self.state.add_log("Error: No file selected")
            return

        if not self.state.output_folder:
            self.state.add_log("Error: No output folder selected")
            return

        valid, error_msg = self._validate_inputs()
        if not valid:
            from tkinter import messagebox

            messagebox.showerror("Invalid Input", error_msg)
            self.state.add_log(f"Validation error: {error_msg}")
            return

        self.state.cut_mode = CutMode(self.cut_mode_var.get())
        try:
            self.state.cut_hours = float(self.cut_hours_entry.get() or "0")
        except ValueError:
            self.state.cut_hours = 0.0
        try:
            self.state.cut_minutes = float(self.cut_minutes_entry.get() or "0")
        except ValueError:
            self.state.cut_minutes = 0.0
        try:
            self.state.cut_seconds = float(self.cut_seconds_entry.get() or "0")
        except ValueError:
            self.state.cut_seconds = 0.0

        if self.progress_frame:
            self.progress_frame.destroy()

        self.progress_frame = ctk.CTkFrame(self, fg_color="#0f172a")
        self.progress_frame.pack(fill="x", pady=(0, 20))

        progress_content = ctk.CTkFrame(self.progress_frame, fg_color="transparent")
        progress_content.pack(fill="x", padx=20, pady=20)

        progress_label = ctk.CTkLabel(
            progress_content,
            text="Processing...",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        progress_label.pack(anchor="w", pady=(0, 10))

        self.progress_bar = ctk.CTkProgressBar(progress_content)
        self.progress_bar.pack(fill="x")
        self.progress_bar.set(0)

        self.progress_percent = ctk.CTkLabel(
            progress_content, text="0%", font=ctk.CTkFont(size=12), text_color="#60a5fa"
        )
        self.progress_percent.pack(anchor="w", pady=(5, 0))

        self.progress_speed = ctk.CTkLabel(
            progress_content,
            text="",
            font=ctk.CTkFont(size=12),
            text_color="#60a5fa",
        )
        self.progress_speed.pack(anchor="w", pady=(2, 0))

        # Disable button
        self.process_btn.configure(state="disabled")

        # Process in separate thread
        thread = threading.Thread(target=self._process_thread, daemon=True)
        thread.start()

    def _process_thread(self):
        """Process file in a separate thread"""
        output_folder = self.state.output_folder
        if self.state.create_output_subfolder:
            output_folder = os.path.join(output_folder, "output")
            os.makedirs(output_folder, exist_ok=True)
        base_name = os.path.splitext(os.path.basename(self.selected_file))[0]
        ext = f".{self.state.output_format}"
        output_name = (
            f"{self.state.output_prefix}{base_name}{self.state.output_suffix}{ext}"
        )
        output_path = os.path.join(output_folder, output_name)

        def on_progress(percent: float, speed=None):
            self.after(0, lambda p=percent, s=speed: self._update_progress(p, s))

        def on_log(message: str):
            self.state.add_log(message)

        try:
            success, error_msg = self.processor.process_video(
                self.selected_file, output_path, on_progress=on_progress, on_log=on_log
            )

            if success:
                self.after(0, lambda: self._on_complete(True, None))
            else:
                self.after(0, lambda: self._on_complete(False, error_msg))
        except Exception as e:
            err_msg = str(e)
            self.state.add_log(f"Error: {err_msg}")
            self.after(0, lambda: self._on_complete(False, err_msg))

    def _update_progress(self, percent: float, speed=None):
        """Update progress bar, percent label, and optional speed label."""
        self.progress_bar.set(percent / 100.0)
        self.progress_percent.configure(text=f"{percent:.1f}%")
        if self.progress_speed is not None:
            self.progress_speed.configure(
                text=f"{speed:.1f}x" if speed is not None else ""
            )

    def _on_complete(self, success: bool, error_msg: str = None):
        """Handle processing completion"""
        if success:
            self.progress_bar.set(1.0)
            self.progress_percent.configure(text="100%")
            self.state.add_log("✓ Processing completed successfully!")
        else:
            msg = error_msg or "Processing failed"
            self.state.add_log(f"✗ Processing failed: {msg}")
            messagebox.showerror("Processing Failed", msg)

        self.process_btn.configure(state="normal")

    def _on_log_update(self, message: str):
        """Handle log updates"""
        pass
