"""
Settings Panel UI Component
"""

import customtkinter as ctk

from src.state import AppState


class SettingsPanel(ctk.CTkScrollableFrame):
    """Settings panel view"""

    def __init__(self, parent, state: AppState):
        super().__init__(parent, fg_color="#0f172a")
        self.state = state

        self._create_ui()

    def _create_ui(self):
        """Create the UI components"""
        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", pady=(0, 20))

        title = ctk.CTkLabel(
            header, text="Settings", font=ctk.CTkFont(size=32, weight="bold")
        )
        title.pack(anchor="w")

        subtitle = ctk.CTkLabel(
            header,
            text="Configure application settings",
            font=ctk.CTkFont(size=14),
            text_color="#60a5fa",
        )
        subtitle.pack(anchor="w", pady=(5, 0))

        # FFmpeg Settings Section
        self._create_ffmpeg_section()

        # CPU Usage Section
        self._create_cpu_section()

        # Parallel Processing Section
        self._create_parallel_section()

        # About Section
        self._create_about_section()

    def _create_ffmpeg_section(self):
        """Create FFmpeg settings section"""
        from src.state import PROCESSING_PROFILES

        ffmpeg_frame = ctk.CTkFrame(self, fg_color="#0f172a")
        ffmpeg_frame.pack(fill="x", pady=(0, 20))

        title = ctk.CTkLabel(
            ffmpeg_frame,
            text="FFmpeg Settings",
            font=ctk.CTkFont(size=18, weight="bold"),
        )
        title.pack(anchor="w", padx=20, pady=(20, 15))

        content = ctk.CTkFrame(ffmpeg_frame, fg_color="transparent")
        content.pack(fill="x", padx=20, pady=(0, 20))

        # Get current profile
        profile = PROCESSING_PROFILES[self.state.processing_profile]

        # Build dynamic info text
        info_text = f"Current Processing Profile: {profile.name}\n"
        info_text += f"{profile.description}\n\n"
        info_text += f"• Video Codec: {profile.video_codec}\n"
        info_text += f"• Preset: {profile.video_preset}\n"

        if profile.video_crf is not None:
            info_text += f"• CRF (Quality): {profile.video_crf}\n"
        elif profile.video_bitrate is not None:
            info_text += f"• Video Bitrate: {profile.video_bitrate}\n"

        info_text += f"• Pixel Format: {profile.pixel_format}\n"

        if profile.x264_profile:
            info_text += f"• H.264 Profile: {profile.x264_profile}\n"
        if profile.x264_level:
            info_text += f"• H.264 Level: {profile.x264_level}\n"

        info_text += f"• Audio Codec: {profile.audio_codec}\n"
        info_text += f"• Audio Bitrate: {profile.audio_bitrate}\n"
        info_text += (
            f"• Faststart: {'Enabled' if profile.use_faststart else 'Disabled'}\n"
        )

        if profile.max_width or profile.max_height:
            info_text += f"• Max Resolution: {profile.max_width or 'unlimited'}x{profile.max_height or 'unlimited'}\n"

        info_text += "\n💡 You can change the processing profile in the Batch Processor or Single File view."

        info_label = ctk.CTkLabel(
            content,
            text=info_text,
            font=ctk.CTkFont(size=13),
            text_color="#60a5fa",
            justify="left",
            anchor="w",
        )
        info_label.pack(anchor="w", pady=10)

    def _create_cpu_section(self):
        """Create CPU usage control section"""
        cpu_frame = ctk.CTkFrame(self, fg_color="#0f172a")
        cpu_frame.pack(fill="x", pady=(0, 20))

        title = ctk.CTkLabel(
            cpu_frame,
            text="CPU Usage",
            font=ctk.CTkFont(size=18, weight="bold"),
        )
        title.pack(anchor="w", padx=20, pady=(20, 15))

        content = ctk.CTkFrame(cpu_frame, fg_color="transparent")
        content.pack(fill="x", padx=20, pady=(0, 20))

        # ── FFmpeg threads per job ──
        threads_row = ctk.CTkFrame(content, fg_color="transparent")
        threads_row.pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(
            threads_row,
            text="FFmpeg threads per job:",
            font=ctk.CTkFont(size=13),
        ).pack(side="left", padx=(0, 12))

        self.threads_slider = ctk.CTkSlider(
            threads_row, from_=1, to=8, number_of_steps=7, width=180
        )
        self.threads_slider.set(self.state.ffmpeg_threads)
        self.threads_slider.pack(side="left", padx=(0, 8))

        self.threads_label = ctk.CTkLabel(
            threads_row,
            text=str(self.state.ffmpeg_threads),
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#60a5fa",
            width=20,
        )
        self.threads_label.pack(side="left")

        def update_threads(value):
            val = int(value)
            self.state.ffmpeg_threads = val
            self.threads_label.configure(text=str(val))

        self.threads_slider.configure(command=update_threads)

        # ── Process priority ──
        prio_row = ctk.CTkFrame(content, fg_color="transparent")
        prio_row.pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(
            prio_row,
            text="Process priority:",
            font=ctk.CTkFont(size=13),
        ).pack(side="left", padx=(0, 12))

        self.priority_menu = ctk.CTkOptionMenu(
            prio_row,
            values=["low", "normal"],
            width=120,
            command=lambda choice: setattr(self.state, "process_priority", choice),
        )
        self.priority_menu.set(self.state.process_priority)
        self.priority_menu.pack(side="left")

        # ── Quick presets ──
        preset_row = ctk.CTkFrame(content, fg_color="transparent")
        preset_row.pack(fill="x", pady=(0, 4))

        ctk.CTkLabel(
            preset_row,
            text="Quick presets:",
            font=ctk.CTkFont(size=13),
        ).pack(side="left", padx=(0, 8))

        def apply_preset(preset_name):
            if preset_name == "Low":
                vals = {"threads": 1, "priority": "low"}
            elif preset_name == "Medium":
                vals = {"threads": 2, "priority": "low"}
            else:
                vals = {"threads": 4, "priority": "normal"}
            self.threads_slider.set(vals["threads"])
            self.threads_label.configure(text=str(vals["threads"]))
            self.priority_menu.set(vals["priority"])
            self.state.ffmpeg_threads = vals["threads"]
            self.state.process_priority = vals["priority"]

        for label in ["Low", "Medium", "High"]:
            ctk.CTkButton(
                preset_row,
                text=label,
                width=70,
                height=28,
                font=ctk.CTkFont(size=12),
                fg_color="#334155",
                hover_color="#475569",
                command=lambda n=label: apply_preset(n),
            ).pack(side="left", padx=(0, 6))

        info_text = (
            "Lower threads = less CPU per file but slower encoding.\n"
            "Low priority keeps the system responsive while encoding.\n"
            f"Total CPU load ≈ (parallel jobs) × (threads per job).\n"
            f"Current max: {self.state.parallel_config.max_workers} × {self.state.ffmpeg_threads} "
            f"= {self.state.parallel_config.max_workers * self.state.ffmpeg_threads} threads"
        )
        info_label = ctk.CTkLabel(
            content,
            text=info_text,
            font=ctk.CTkFont(size=11),
            text_color="#64748b",
            justify="left",
            anchor="w",
        )
        info_label.pack(anchor="w", pady=(8, 0))

    def _create_parallel_section(self):
        """Create parallel processing settings section"""
        parallel_frame = ctk.CTkFrame(self, fg_color="#0f172a")
        parallel_frame.pack(fill="x", pady=(0, 20))

        title = ctk.CTkLabel(
            parallel_frame,
            text="Parallel Processing",
            font=ctk.CTkFont(size=18, weight="bold"),
        )
        title.pack(anchor="w", padx=20, pady=(20, 15))

        content = ctk.CTkFrame(parallel_frame, fg_color="transparent")
        content.pack(fill="x", padx=20, pady=(0, 20))

        # Max workers slider
        workers_row = ctk.CTkFrame(content, fg_color="transparent")
        workers_row.pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(
            workers_row,
            text="Max parallel jobs:",
            font=ctk.CTkFont(size=13),
            text_color="#60a5fa",
            width=150,
        ).pack(side="left", padx=(0, 12))

        self.workers_slider = ctk.CTkSlider(
            workers_row, from_=1, to=4, number_of_steps=3, width=200
        )
        self.workers_slider.set(self.state.parallel_config.max_workers)
        self.workers_slider.pack(side="left", padx=(0, 12))

        self.workers_label = ctk.CTkLabel(
            workers_row,
            text=str(self.state.parallel_config.max_workers),
            font=ctk.CTkFont(size=13),
            text_color="#0f172a",
            width=30,
        )
        self.workers_label.pack(side="left")

        def update_workers(value):
            workers = int(value)
            self.state.parallel_config.max_workers = workers
            self.workers_label.configure(text=str(workers))

        self.workers_slider.configure(command=update_workers)

        info_text = "Process multiple files simultaneously for faster batch encoding.\n"
        info_text += "Recommended: 2-3 jobs for most systems. Higher values may slow down individual files."

        info_label = ctk.CTkLabel(
            content,
            text=info_text,
            font=ctk.CTkFont(size=12),
            text_color="#64748B",
            justify="left",
            anchor="w",
        )
        info_label.pack(anchor="w", pady=(0, 0))

    def _create_about_section(self):
        """Create about section"""
        about_frame = ctk.CTkFrame(self, fg_color="#0f172a")
        about_frame.pack(fill="x", pady=(0, 20))

        title = ctk.CTkLabel(
            about_frame, text="About", font=ctk.CTkFont(size=18, weight="bold")
        )
        title.pack(anchor="w", padx=20, pady=(20, 15))

        content = ctk.CTkFrame(about_frame, fg_color="transparent")
        content.pack(fill="x", padx=20, pady=(0, 20))

        about_text = (
            "VideoForge - FFmpeg Video Automation Dashboard\n\n"
            "Version: 2.0.0\n"
            "A modern desktop application for automating video processing tasks.\n\n"
            "Features:\n"
            "• Batch processing of video files\n"
            "• Flexible trim/cut options with seconds precision\n"
            "• Multiple processing profiles (Universal, High Quality, Small File, iOS)\n"
            "• Clean feed generation — on-screen graphics (OSG) removal\n"
            "• Universal streaming compatibility (iPhone, Android, TVs, web)\n"
            "• Real-time progress tracking\n"
            "• Modern dark-themed UI\n\n"
            "Built with Python and CustomTkinter\n\n"
            "Developer: fmamdoh504@gmail.com"
        )

        about_label = ctk.CTkLabel(
            content,
            text=about_text,
            font=ctk.CTkFont(size=13),
            text_color="#60a5fa",
            justify="left",
            anchor="w",
        )
        about_label.pack(anchor="w", pady=10)
