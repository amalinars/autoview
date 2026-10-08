#!/usr/bin/env python3
"""
YouTube Watch & Search Studio - Modern Desktop GUI (Tkinter Native)
-------------------------------------------------------------------
Features:
- Full pre-start argument configuration (Workers, Batches, Durations, Proxies, Headless/Headed, Audio Mute)
- Real-time multi-worker state monitoring (Active Proxy, Current Video, Watch Progress Bar, Ad Skip Counter)
- Global Batch Metrics (Completed Views, Total Ads Skipped, Active Proxies, Elapsed Timer)
- Live color-coded terminal log stream
- Process-safe multiprocessing queue telemetry
"""

import os
import sys
import time
import math
import random
import threading
import multiprocessing
from dataclasses import dataclass
import tkinter as tk
from tkinter import ttk, messagebox

from geonode_fetcher import (
    get_and_verify_proxies,
    DEFAULT_OUTPUT_JSON,
    start_background_proxy_replenisher,
    HIGH_CPM_COUNTRIES,
    TIER1_COUNTRIES,
    trigger_refill,
    reset_proxy_storage,
)
from youtube_search import worker_process_main, load_proxies, safe_log

# =====================================================================
# THEME PALETTE (Catppuccin Mocha / VS Code Dark)
# =====================================================================
C_BG_DEEP     = "#11111b"  # Deepest dark for terminal / background
C_BG_MAIN     = "#181825"  # Window main background
C_CARD_BG     = "#1e1e2e"  # Card / container background
C_CARD_BORDER = "#313244"  # Subtle borders
C_CARD_HOVER  = "#2a2b3d"  # Hover background

C_TEXT_MAIN   = "#cdd6f4"  # Crisp primary text
C_TEXT_MUTED  = "#a6adc8"  # Subtitle / placeholder text
C_TEXT_DARK   = "#11111b"  # Text on bright badges

C_ACCENT_BLUE = "#89b4fa"  # Primary accent / Links
C_SUCCESS     = "#a6e3a1"  # Success green
C_WARNING     = "#f9e2af"  # Warning yellow
C_DANGER      = "#f38ba8"  # Error / Stop red
C_PEACH       = "#fab387"  # Ad / Highlight orange
C_PURPLE      = "#cba6f7"  # Lock / Special badge

# Font presets
FONT_TITLE = ("DejaVu Sans", 14, "bold")
FONT_SUBTITLE = ("DejaVu Sans", 9)
FONT_HEADING = ("DejaVu Sans", 11, "bold")
FONT_BOLD = ("DejaVu Sans", 9, "bold")
FONT_NORMAL = ("DejaVu Sans", 9)
FONT_SMALL = ("DejaVu Sans", 8)
FONT_SMALL_BOLD = ("DejaVu Sans", 8, "bold")
FONT_MONO = ("DejaVu Sans Mono", 8)

def format_time(seconds: float) -> str:
    s = int(seconds)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

# =====================================================================
# CUSTOM CANVAS PROGRESS BAR (Smooth, rounded-look)
# =====================================================================
class SmoothProgressBar(tk.Canvas):
    def __init__(self, parent, height=14, bg_color=C_CARD_BORDER, fill_color=C_SUCCESS, **kwargs):
        super().__init__(parent, height=height, bg=C_CARD_BG, highlightthickness=0, **kwargs)
        self.height = height
        self.bg_color = bg_color
        self.fill_color = fill_color
        self.progress = 0.0  # 0.0 to 1.0
        self.bind("<Configure>", self._draw)

    def set_progress(self, val: float):
        self.progress = max(0.0, min(1.0, val))
        self._draw()

    def set_color(self, color: str):
        self.fill_color = color
        self._draw()

    def _draw(self, event=None):
        self.delete("all")
        w = self.winfo_width()
        h = self.height
        if w <= 1:
            return

        # Track background
        r = h / 2
        self.create_rectangle(0, 0, w, h, fill=self.bg_color, outline="")

        # Fill bar
        fill_w = int(w * self.progress)
        if fill_w > 0:
            self.create_rectangle(0, 0, fill_w, h, fill=self.fill_color, outline="")

# =====================================================================
# WORKER CARD WIDGET
# =====================================================================
class WorkerCard(tk.Frame):
    """Real-time monitoring card for an individual worker process."""
    def __init__(self, parent, worker_id: int, mode_str: str = "HEADED"):
        super().__init__(parent, bg=C_CARD_BG, highlightbackground=C_CARD_BORDER, highlightthickness=1, padx=12, pady=10)
        self.worker_id = worker_id

        # Row 1: Header (Worker ID + PID Badge + Mode Pill + Status Pill + Proxy Pill)
        header_frame = tk.Frame(self, bg=C_CARD_BG)
        header_frame.pack(fill="x", expand=True)

        self.lbl_id = tk.Label(header_frame, text=f"Worker #{worker_id:02d}", font=FONT_HEADING, fg=C_ACCENT_BLUE, bg=C_CARD_BG)
        self.lbl_id.pack(side="left")

        self.lbl_pid = tk.Label(header_frame, text="PID: ---", font=FONT_SMALL, fg=C_TEXT_MUTED, bg=C_CARD_BG)
        self.lbl_pid.pack(side="left", padx=(8, 0))

        # Mode pill (Headed vs Headless)
        is_headed = (mode_str.upper() == "HEADED")
        mode_text = "👁️ HEADED" if is_headed else "⚡ HEADLESS"
        mode_color = C_WARNING if is_headed else C_ACCENT_BLUE
        self.lbl_mode = tk.Label(header_frame, text=mode_text, font=FONT_SMALL_BOLD, fg=mode_color, bg=C_CARD_BORDER, padx=8, pady=2)
        self.lbl_mode.pack(side="left", padx=(10, 0))

        # Proxy pill on the right
        self.lbl_proxy = tk.Label(header_frame, text="Proxy: Direct", font=FONT_SMALL_BOLD, fg=C_TEXT_MAIN, bg=C_CARD_BORDER, padx=8, pady=2)
        self.lbl_proxy.pack(side="right")

        # Status badge
        self.lbl_status = tk.Label(header_frame, text="SIAP (IDLE)", font=FONT_SMALL_BOLD, fg=C_TEXT_DARK, bg=C_TEXT_MUTED, padx=8, pady=2)
        self.lbl_status.pack(side="right", padx=(0, 8))

        # Row 2: Video Title & Target info
        info_frame = tk.Frame(self, bg=C_CARD_BG)
        info_frame.pack(fill="x", expand=True, pady=(8, 4))

        self.lbl_video = tk.Label(info_frame, text="Menunggu tugas pencarian...", font=FONT_BOLD, fg=C_TEXT_MAIN, bg=C_CARD_BG, anchor="w")
        self.lbl_video.pack(side="left", fill="x", expand=True)

        self.lbl_ads = tk.Label(info_frame, text="🛡️ 0 Iklan", font=FONT_SMALL_BOLD, fg=C_PEACH, bg=C_CARD_BG)
        self.lbl_ads.pack(side="right")

        # Row 3: Progress Bar & Duration timer
        prog_frame = tk.Frame(self, bg=C_CARD_BG)
        prog_frame.pack(fill="x", expand=True, pady=(2, 0))

        self.pbar = SmoothProgressBar(prog_frame, height=12, bg_color=C_CARD_BORDER, fill_color=C_SUCCESS)
        self.pbar.pack(side="left", fill="x", expand=True)

        self.lbl_progress_text = tk.Label(prog_frame, text="00:00 / 00:00 (0%)", font=FONT_SMALL_BOLD, fg=C_TEXT_MUTED, bg=C_CARD_BG, width=18, anchor="e")
        self.lbl_progress_text.pack(side="right", padx=(10, 0))

    def update_pid(self, pid: int):
        self.lbl_pid.config(text=f"PID: {pid}")

    def update_proxy(self, proxy_str: str):
        short = proxy_str.replace("http://", "").replace("socks5://", "").replace("socks4://", "")
        if "://" in proxy_str:
            proto = proxy_str.split("://")[0].upper()
            self.lbl_proxy.config(text=f"{proto}: {short}", fg=C_ACCENT_BLUE)
        else:
            self.lbl_proxy.config(text="Direct", fg=C_TEXT_MAIN)

    def update_status(self, status: str, details: str = ""):
        s_upper = status.upper()
        if "MENONTON" in s_upper or "LOCK" in s_upper:
            self.lbl_status.config(text="🟢 MENONTON (LOCKED)", bg=C_SUCCESS, fg=C_TEXT_DARK)
            self.pbar.set_color(C_SUCCESS)
        elif "MENCARI" in s_upper:
            self.lbl_status.config(text="🟡 MENCARI VIDEO", bg=C_WARNING, fg=C_TEXT_DARK)
            self.pbar.set_color(C_WARNING)
        elif "NAVIGASI" in s_upper:
            self.lbl_status.config(text="🔵 NAVIGASI", bg=C_ACCENT_BLUE, fg=C_TEXT_DARK)
            self.pbar.set_color(C_ACCENT_BLUE)
        elif "COOLDOWN" in s_upper or "JEDA" in s_upper:
            self.lbl_status.config(text="🟣 JEDA / COOLDOWN", bg=C_PURPLE, fg=C_TEXT_DARK)
        elif "SUKSES" in s_upper:
            self.lbl_status.config(text="✔ SUKSES", bg=C_SUCCESS, fg=C_TEXT_DARK)
        elif "ERROR" in s_upper or "GAGAL" in s_upper:
            self.lbl_status.config(text="❌ KENDALA", bg=C_DANGER, fg=C_TEXT_DARK)
        else:
            self.lbl_status.config(text=status, bg=C_CARD_BORDER, fg=C_TEXT_MAIN)

        if details and not self.lbl_video.cget("text").startswith("Horror"):
            self.lbl_video.config(text=details)

    def update_video(self, title: str):
        display_title = title if len(title) <= 65 else title[:62] + "..."
        self.lbl_video.config(text=f"🎬 {display_title}", fg=C_TEXT_MAIN)

    def update_ads(self, count: int):
        self.lbl_ads.config(text=f"🛡️ {count} Iklan Di-skip", fg=C_PEACH)

    def update_watch_progress(self, elapsed: float, target: float, percent: float, total_dur: float, ads: int = 0):
        ratio = (elapsed / target) if target > 0 else 0.0
        self.pbar.set_progress(ratio)
        self.lbl_progress_text.config(text=f"{format_time(elapsed)} / {format_time(target)} ({percent:.1f}%)")
        self.lbl_ads.config(text=f"🛡️ {ads} Iklan")

    def reset_for_session(self):
        self.pbar.set_progress(0.0)
        self.lbl_progress_text.config(text="00:00 / 00:00 (0%)")
        self.lbl_video.config(text="Memulai sesi baru...")

# =====================================================================
# BOT ARGS DATA HOLDER
# =====================================================================
@dataclass
class BotConfig:
    workers: int = 3
    batch_count: int = 50
    infinite: bool = False
    min_percent: float = 30.0
    max_percent: float = 70.0
    max_cap: int | None = None
    sleep_between: int = 3
    proxy_mode: str = "geonode"  # "geonode", "local", "direct"
    proxy_file: str | None = None
    proxy_limit: int = 500
    proxy_threads: int = 80
    headless: bool = True
    headed: bool = False
    unmute: bool = False
    min_delay: int = 50
    max_delay: int = 120
    direct: bool = False
    skip_fetch: bool = False
    keyword: str = "horrornologi"
    channel: str = "horrornologi"

# =====================================================================
# MAIN DESKTOP GUI APPLICATION
# =====================================================================
class YouTubeStudioGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("YouTube Watch & Search Studio")
        self.root.geometry("1220x900")
        self.root.minsize(1050, 750)
        self.root.configure(bg=C_BG_MAIN)

        # Bot runtime process variables
        self.is_running = False
        self.bot_thread = None
        self.stop_event = None
        self.success_counter = None
        self.telemetry_queue = None
        self.processes = []
        self.start_timestamp = 0
        self.worker_cards: dict[int, WorkerCard] = {}
        self.worker_ad_counters: dict[int, int] = {}
        self.worker_success_counters: dict[int, int] = {}
        self.total_ads_skipped = 0

        # UI Setup
        self._setup_styles()
        self._build_header()
        self._build_config_card()
        self._build_metrics_bar()
        self._build_main_split()

        # Telemetry queue polling loop (every 60ms)
        self.root.after(60, self._poll_telemetry)
        # Timer update loop (every 1000ms)
        self.root.after(1000, self._update_timer)

        # Set graceful exit
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Dark.TFrame", background=C_BG_MAIN)
        style.configure("Card.TFrame", background=C_CARD_BG)
        style.configure("Dark.TLabel", background=C_BG_MAIN, foreground=C_TEXT_MAIN, font=FONT_NORMAL)
        style.configure("Card.TLabel", background=C_CARD_BG, foreground=C_TEXT_MAIN, font=FONT_NORMAL)

    def _build_header(self):
        header = tk.Frame(self.root, bg=C_BG_MAIN, padx=20, pady=12)
        header.pack(fill="x")

        left = tk.Frame(header, bg=C_BG_MAIN)
        left.pack(side="left")

        title = tk.Label(left, text="🎬 YouTube Watch & Search Studio", font=FONT_TITLE, fg=C_ACCENT_BLUE, bg=C_BG_MAIN)
        title.pack(anchor="w")

        sub = tk.Label(
            left,
            text="Process-Isolated Automation • Smart System Watch Lock • Real-Time Telemetry",
            font=FONT_SUBTITLE,
            fg=C_TEXT_MUTED,
            bg=C_BG_MAIN
        )
        sub.pack(anchor="w", pady=(2, 0))

        # Overall Status Badge
        self.lbl_master_status = tk.Label(
            header,
            text="● SIAP (IDLE)",
            font=FONT_BOLD,
            fg=C_TEXT_DARK,
            bg=C_SUCCESS,
            padx=14,
            pady=6
        )
        self.lbl_master_status.pack(side="right")

    def _build_config_card(self):
        container = tk.Frame(self.root, bg=C_BG_MAIN, padx=20)
        container.pack(fill="x", pady=(0, 10))

        # Row with 3 Thematic Cards
        cards_row = tk.Frame(container, bg=C_BG_MAIN)
        cards_row.pack(fill="x", expand=True)

        # ---------------- CARD 1: TAMPILAN BROWSER & AUDIO ----------------
        card_browser = tk.LabelFrame(
            cards_row,
            text="  🖥️ TAMPILAN BROWSER  ",
            font=FONT_HEADING,
            fg=C_ACCENT_BLUE,
            bg=C_CARD_BG,
            highlightbackground=C_CARD_BORDER,
            highlightthickness=1,
            padx=14,
            pady=10
        )
        card_browser.pack(side="left", fill="both", expand=True, padx=(0, 8))

        self.var_browser_mode = tk.StringVar(value="headed")
        self.var_headless = tk.BooleanVar(value=False)

        rb_headed = tk.Radiobutton(
            card_browser,
            text="👁️ Mode Headed (Buka Tab/Jendela)",
            variable=self.var_browser_mode,
            value="headed",
            font=FONT_BOLD,
            fg=C_WARNING,
            bg=C_CARD_BG,
            selectcolor=C_BG_DEEP,
            activebackground=C_CARD_BG,
            activeforeground=C_WARNING
        )
        rb_headed.pack(anchor="w", pady=(2, 0))

        lbl_headed_desc = tk.Label(
            card_browser,
            text="   ↳ Browser terbuka & auto-tiling rapi di layar.",
            font=FONT_SMALL,
            fg=C_TEXT_MUTED,
            bg=C_CARD_BG
        )
        lbl_headed_desc.pack(anchor="w", pady=(0, 6))

        rb_headless = tk.Radiobutton(
            card_browser,
            text="⚡ Mode Headless (Latar Belakang)",
            variable=self.var_browser_mode,
            value="headless",
            font=FONT_BOLD,
            fg=C_TEXT_MAIN,
            bg=C_CARD_BG,
            selectcolor=C_BG_DEEP,
            activebackground=C_CARD_BG,
            activeforeground=C_TEXT_MAIN
        )
        rb_headless.pack(anchor="w", pady=(0, 0))

        lbl_headless_desc = tk.Label(
            card_browser,
            text="   ↳ Berjalan tersembunyi, sangat hemat CPU/RAM.",
            font=FONT_SMALL,
            fg=C_TEXT_MUTED,
            bg=C_CARD_BG
        )
        lbl_headless_desc.pack(anchor="w", pady=(0, 8))

        # Audio mute toggle
        self.var_mute = tk.BooleanVar(value=True)
        chk_mute = tk.Checkbutton(
            card_browser,
            text="🔇 Mute Audio Browser (Disarankan)",
            variable=self.var_mute,
            font=FONT_NORMAL,
            fg=C_PEACH,
            bg=C_CARD_BG,
            selectcolor=C_BG_DEEP,
            activebackground=C_CARD_BG,
            activeforeground=C_PEACH
        )
        chk_mute.pack(anchor="w", pady=(2, 0))

        # ---------------- CARD 2: WORKER & PLAYBACK ----------------
        card_worker = tk.LabelFrame(
            cards_row,
            text="  ⚙️ WORKER & DURASI TONTON  ",
            font=FONT_HEADING,
            fg=C_ACCENT_BLUE,
            bg=C_CARD_BG,
            highlightbackground=C_CARD_BORDER,
            highlightthickness=1,
            padx=14,
            pady=10
        )
        card_worker.pack(side="left", fill="both", expand=True, padx=(0, 8))

        # Row 1: Worker & Batch
        w_r1 = tk.Frame(card_worker, bg=C_CARD_BG)
        w_r1.pack(fill="x", pady=2)

        tk.Label(w_r1, text="Jumlah Worker:", font=FONT_NORMAL, fg=C_TEXT_MAIN, bg=C_CARD_BG, width=13, anchor="w").pack(side="left")
        self.sp_workers = tk.Spinbox(w_r1, from_=1, to=200, width=5, font=FONT_BOLD, bg=C_BG_DEEP, fg=C_TEXT_MAIN, insertbackground=C_TEXT_MAIN)
        self.sp_workers.delete(0, "end")
        self.sp_workers.insert(0, "3")
        self.sp_workers.pack(side="left")

        tk.Label(w_r1, text="Target Batch:", font=FONT_NORMAL, fg=C_TEXT_MAIN, bg=C_CARD_BG, width=11, anchor="e").pack(side="left", padx=(6, 4))
        self.sp_batch = tk.Spinbox(w_r1, from_=1, to=1000, width=5, font=FONT_BOLD, bg=C_BG_DEEP, fg=C_TEXT_MAIN, insertbackground=C_TEXT_MAIN)
        self.sp_batch.delete(0, "end")
        self.sp_batch.insert(0, "50")
        self.sp_batch.pack(side="left")

        # Row 2: Infinite check
        w_r2 = tk.Frame(card_worker, bg=C_CARD_BG)
        w_r2.pack(fill="x", pady=2)
        self.var_infinite = tk.BooleanVar(value=False)
        self.chk_infinite = tk.Checkbutton(
            w_r2, text="♾️ Infinite Mode (Berjalan Terus Menerus)", variable=self.var_infinite, font=FONT_NORMAL,
            fg=C_TEXT_MAIN, bg=C_CARD_BG, selectcolor=C_BG_DEEP, activebackground=C_CARD_BG, activeforeground=C_TEXT_MAIN
        )
        self.chk_infinite.pack(anchor="w")

        # Row 3: Durasi Min/Max %
        w_r3 = tk.Frame(card_worker, bg=C_CARD_BG)
        w_r3.pack(fill="x", pady=3)
        tk.Label(w_r3, text="Durasi Tonton:", font=FONT_NORMAL, fg=C_TEXT_MAIN, bg=C_CARD_BG, width=13, anchor="w").pack(side="left")
        self.sp_min_pct = tk.Spinbox(w_r3, from_=10.0, to=90.0, increment=5.0, width=4, font=FONT_BOLD, bg=C_BG_DEEP, fg=C_TEXT_MAIN, insertbackground=C_TEXT_MAIN)
        self.sp_min_pct.delete(0, "end")
        self.sp_min_pct.insert(0, "30.0")
        self.sp_min_pct.pack(side="left")
        tk.Label(w_r3, text="%", font=FONT_SMALL, fg=C_TEXT_MUTED, bg=C_CARD_BG).pack(side="left", padx=(1, 3))

        tk.Label(w_r3, text="s/d", font=FONT_NORMAL, fg=C_TEXT_MUTED, bg=C_CARD_BG).pack(side="left", padx=2)

        self.sp_max_pct = tk.Spinbox(w_r3, from_=20.0, to=100.0, increment=5.0, width=4, font=FONT_BOLD, bg=C_BG_DEEP, fg=C_TEXT_MAIN, insertbackground=C_TEXT_MAIN)
        self.sp_max_pct.delete(0, "end")
        self.sp_max_pct.insert(0, "70.0")
        self.sp_max_pct.pack(side="left")
        tk.Label(w_r3, text="%", font=FONT_SMALL, fg=C_TEXT_MUTED, bg=C_CARD_BG).pack(side="left", padx=(1, 0))

        # Row 4: Max Cap & Sleep
        w_r4 = tk.Frame(card_worker, bg=C_CARD_BG)
        w_r4.pack(fill="x", pady=2)
        tk.Label(w_r4, text="Max Cap (detik):", font=FONT_NORMAL, fg=C_TEXT_MAIN, bg=C_CARD_BG, width=13, anchor="w").pack(side="left")
        self.ent_max_cap = tk.Entry(w_r4, width=5, font=FONT_BOLD, bg=C_BG_DEEP, fg=C_TEXT_MAIN, insertbackground=C_TEXT_MAIN)
        self.ent_max_cap.insert(0, "0")
        self.ent_max_cap.pack(side="left")

        tk.Label(w_r4, text="Jeda Sesi:", font=FONT_NORMAL, fg=C_TEXT_MAIN, bg=C_CARD_BG, width=9, anchor="e").pack(side="left", padx=(6, 4))
        self.sp_sleep = tk.Spinbox(w_r4, from_=1, to=30, width=4, font=FONT_BOLD, bg=C_BG_DEEP, fg=C_TEXT_MAIN, insertbackground=C_TEXT_MAIN)
        self.sp_sleep.delete(0, "end")
        self.sp_sleep.insert(0, "3")
        self.sp_sleep.pack(side="left")
        tk.Label(w_r4, text="dtk", font=FONT_SMALL, fg=C_TEXT_MUTED, bg=C_CARD_BG).pack(side="left", padx=(2, 0))

        # ---------------- CARD 3: TRAFFIC & TARGET SALURAN ----------------
        card_proxy = tk.LabelFrame(
            cards_row,
            text="  🌐 PROXY & TARGET SALURAN  ",
            font=FONT_HEADING,
            fg=C_ACCENT_BLUE,
            bg=C_CARD_BG,
            highlightbackground=C_CARD_BORDER,
            highlightthickness=1,
            padx=14,
            pady=10
        )
        card_proxy.pack(side="left", fill="both", expand=True)

        self.var_proxy_mode = tk.StringVar(value="geonode")
        self.var_proxy_mode.trace_add("write", self._on_proxy_mode_change)
        for mode_val, mode_title in [
            ("geonode", "Auto Hybrid (Geonode + ProxyScrape)"),
            ("local", "Cache Lokal (working_yt_proxies.txt)"),
            ("direct", "Direct (Tanpa Proxy)")
        ]:
            rb = tk.Radiobutton(
                card_proxy, text=mode_title, variable=self.var_proxy_mode, value=mode_val,
                font=FONT_BOLD if mode_val == "geonode" else FONT_NORMAL,
                fg=C_SUCCESS if mode_val == "geonode" else C_TEXT_MAIN,
                bg=C_CARD_BG, selectcolor=C_BG_DEEP, activebackground=C_CARD_BG, activeforeground=C_TEXT_MAIN
            )
            rb.pack(anchor="w", pady=1)

        # Keyword & Channel target
        p_row_kw = tk.Frame(card_proxy, bg=C_CARD_BG)
        p_row_kw.pack(fill="x", pady=(6, 2))
        tk.Label(p_row_kw, text="Keyword:", font=FONT_NORMAL, fg=C_TEXT_MUTED, bg=C_CARD_BG, width=8, anchor="w").pack(side="left")
        self.ent_keyword = tk.Entry(p_row_kw, font=FONT_BOLD, bg=C_BG_DEEP, fg=C_ACCENT_BLUE, insertbackground=C_TEXT_MAIN)
        self.ent_keyword.insert(0, "horrornologi")
        self.ent_keyword.pack(side="left", fill="x", expand=True)

        p_row_ch = tk.Frame(card_proxy, bg=C_CARD_BG)
        p_row_ch.pack(fill="x", pady=2)
        tk.Label(p_row_ch, text="Channel:", font=FONT_NORMAL, fg=C_TEXT_MUTED, bg=C_CARD_BG, width=8, anchor="w").pack(side="left")
        self.ent_channel = tk.Entry(p_row_ch, font=FONT_BOLD, bg=C_BG_DEEP, fg=C_ACCENT_BLUE, insertbackground=C_TEXT_MAIN)
        self.ent_channel.insert(0, "horrornologi")
        self.ent_channel.pack(side="left", fill="x", expand=True)

        # ---------------- ACTION BUTTONS BAR ----------------
        btn_bar = tk.Frame(container, bg=C_CARD_BG, highlightbackground=C_CARD_BORDER, highlightthickness=1, padx=14, pady=10)
        btn_bar.pack(fill="x", pady=(10, 0))

        self.btn_start = tk.Button(
            btn_bar, text="  🚀 JALANKAN BOT  ", font=FONT_HEADING,
            bg=C_SUCCESS, fg=C_TEXT_DARK, activebackground="#85cc80", activeforeground=C_TEXT_DARK,
            relief="flat", cursor="hand2", padx=20, pady=7, command=self._start_bot
        )
        self.btn_start.pack(side="left", padx=(0, 10))

        self.btn_stop = tk.Button(
            btn_bar, text="  ⏹ HENTIKAN BOT  ", font=FONT_HEADING,
            bg=C_DANGER, fg=C_TEXT_DARK, activebackground="#e06c75", activeforeground=C_TEXT_DARK,
            relief="flat", cursor="hand2", padx=18, pady=7, state="disabled", command=self._stop_bot
        )
        self.btn_stop.pack(side="left", padx=(0, 10))

        self.btn_refresh_proxy = tk.Button(
            btn_bar, text="  🔄 UPDATE PROXY (HYBRID)  ", font=FONT_BOLD,
            bg=C_CARD_BORDER, fg=C_ACCENT_BLUE, activebackground=C_CARD_HOVER, activeforeground=C_TEXT_MAIN,
            relief="flat", cursor="hand2", padx=14, pady=7, command=self._refresh_proxies_async
        )
        self.btn_refresh_proxy.pack(side="left", padx=(0, 10))

        self.btn_clear_log = tk.Button(
            btn_bar, text="🧹 Bersihkan Log", font=FONT_NORMAL,
            bg=C_CARD_BORDER, fg=C_TEXT_MUTED, activebackground=C_CARD_HOVER, activeforeground=C_TEXT_MAIN,
            relief="flat", cursor="hand2", padx=12, pady=7, command=self._clear_logs
        )
        self.btn_clear_log.pack(side="right")

    def _build_metrics_bar(self):
        bar = tk.Frame(self.root, bg=C_BG_MAIN, padx=20)
        bar.pack(fill="x", pady=(0, 10))

        # Hitung proxy yang tersimpan di file saat GUI dibuka
        initial_count = self._get_live_proxy_count()
        init_proxies_text = f"{initial_count} Aktif" if initial_count > 0 else "0 Aktif"

        # 4 Metric Cards
        self.metric_cards = {}
        items = [
            ("views", "📊 SESI SUKSES", "0 / 50 (0%)", C_SUCCESS),
            ("ads", "🛡️ TOTAL IKLAN DI-SKIP", "0 Iklan", C_PEACH),
            ("proxies", "🌐 PROXY TERSEDIA", init_proxies_text, C_ACCENT_BLUE),
            ("timer", "⏱️ WAKTU BERJALAN", "00:00:00", C_PURPLE)
        ]

        for key, label_text, init_val, color in items:
            c = tk.Frame(bar, bg=C_CARD_BG, highlightbackground=C_CARD_BORDER, highlightthickness=1, padx=14, pady=8)
            c.pack(side="left", fill="both", expand=True, padx=4)

            tk.Label(c, text=label_text, font=FONT_SMALL_BOLD, fg=C_TEXT_MUTED, bg=C_CARD_BG).pack(anchor="w")
            val_lbl = tk.Label(c, text=init_val, font=FONT_HEADING, fg=color, bg=C_CARD_BG)
            val_lbl.pack(anchor="w", pady=(2, 0))
            self.metric_cards[key] = val_lbl

    def _build_main_split(self):
        container = tk.Frame(self.root, bg=C_BG_MAIN, padx=20)
        container.pack(fill="both", expand=True, pady=(0, 12))

        # PanedWindow: Top for Worker Cards, Bottom for Terminal Logs
        paned = tk.PanedWindow(container, orient="vertical", bg=C_BG_MAIN, sashwidth=6, sashrelief="flat")
        paned.pack(fill="both", expand=True)

        # ---------------- TOP: Worker Monitoring Area ----------------
        worker_section = tk.Frame(paned, bg=C_CARD_BG, highlightbackground=C_CARD_BORDER, highlightthickness=1)
        paned.add(worker_section, minsize=220, stretch="always")

        # Title bar for workers
        w_title_bar = tk.Frame(worker_section, bg=C_CARD_BG, padx=12, pady=6)
        w_title_bar.pack(fill="x")
        tk.Label(w_title_bar, text="👥 MONITORING STATE WORKER (REAL-TIME)", font=FONT_BOLD, fg=C_ACCENT_BLUE, bg=C_CARD_BG).pack(side="left")

        # Scrollable Canvas for Worker Cards
        self.worker_canvas = tk.Canvas(worker_section, bg=C_CARD_BG, highlightthickness=0)
        self.worker_scrollbar = tk.Scrollbar(worker_section, orient="vertical", command=self.worker_canvas.yview)
        self.worker_frame = tk.Frame(self.worker_canvas, bg=C_CARD_BG)

        self.worker_frame.bind(
            "<Configure>",
            lambda e: self.worker_canvas.configure(scrollregion=self.worker_canvas.bbox("all"))
        )
        self.canvas_window = self.worker_canvas.create_window((0, 0), window=self.worker_frame, anchor="nw")

        self.worker_canvas.configure(yscrollcommand=self.worker_scrollbar.set)
        self.worker_canvas.bind("<Configure>", lambda e: self.worker_canvas.itemconfig(self.canvas_window, width=e.width))

        self.worker_scrollbar.pack(side="right", fill="y")
        self.worker_canvas.pack(side="left", fill="both", expand=True, padx=6, pady=4)

        # ---------------- BOTTOM: Terminal Logs Area ----------------
        log_section = tk.Frame(paned, bg=C_BG_DEEP, highlightbackground=C_CARD_BORDER, highlightthickness=1)
        paned.add(log_section, minsize=160, stretch="always")

        log_title_bar = tk.Frame(log_section, bg=C_BG_DEEP, padx=12, pady=4)
        log_title_bar.pack(fill="x")
        tk.Label(log_title_bar, text="📜 LIVE TERMINAL LOGS", font=FONT_BOLD, fg=C_TEXT_MUTED, bg=C_BG_DEEP).pack(side="left")

        # Text widget for colorized logs
        self.txt_log = tk.Text(
            log_section,
            bg=C_BG_DEEP,
            fg=C_TEXT_MAIN,
            insertbackground=C_TEXT_MAIN,
            font=FONT_MONO,
            wrap="word",
            relief="flat",
            padx=10,
            pady=8
        )
        log_scroll = tk.Scrollbar(log_section, command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=log_scroll.set)

        log_scroll.pack(side="right", fill="y")
        self.txt_log.pack(side="left", fill="both", expand=True)

        # Log color tags
        self.txt_log.tag_configure("green", foreground=C_SUCCESS)
        self.txt_log.tag_configure("cyan", foreground=C_ACCENT_BLUE)
        self.txt_log.tag_configure("yellow", foreground=C_WARNING)
        self.txt_log.tag_configure("red", foreground=C_DANGER)
        self.txt_log.tag_configure("magenta", foreground=C_PURPLE)
        self.txt_log.tag_configure("peach", foreground=C_PEACH)
        self.txt_log.tag_configure("muted", foreground=C_TEXT_MUTED)

        # Initial greeting log
        self._append_log("INFO", "Sistem siap. Pilih konfigurasi dan tekan 'Jalankan Bot'.", "cyan")

    # =====================================================================
    # LOGGING & METRICS HELPERS
    # =====================================================================
    def _append_log(self, prefix: str, msg: str, color_tag: str = "cyan"):
        ts = time.strftime("%H:%M:%S")
        self.txt_log.insert("end", f"[{ts}]", "muted")
        self.txt_log.insert("end", f"[{prefix}] ", color_tag)
        self.txt_log.insert("end", f"{msg}\n")
        self.txt_log.see("end")

    def _clear_logs(self):
        self.txt_log.delete("1.0", "end")

    def _update_timer(self):
        if self.is_running and self.start_timestamp > 0:
            elapsed = time.time() - self.start_timestamp
            self.metric_cards["timer"].config(text=format_time(elapsed))
        self.root.after(1000, self._update_timer)

    def _get_live_proxy_count(self) -> int:
        """Returns the real number of active/usable proxies currently in the configured proxy storage."""
        try:
            from geonode_fetcher import DEFAULT_OUTPUT_JSON
            active_set = set()

            # 1. Dari file JSON
            if os.path.exists(DEFAULT_OUTPUT_JSON):
                try:
                    with open(DEFAULT_OUTPUT_JSON, "r", encoding="utf-8", errors="ignore") as f:
                        data = json.load(f)
                        if isinstance(data, list):
                            for item in data:
                                p = item.get("proxy") if isinstance(item, dict) else str(item)
                                if p and p.strip():
                                    active_set.add(p.strip())
                except Exception:
                    pass

            # 2. Dari file TXT
            txt_path = DEFAULT_OUTPUT_JSON.replace(".json", ".txt")
            if os.path.exists(txt_path):
                try:
                    with open(txt_path, "r", encoding="utf-8", errors="ignore") as f:
                        for line in f:
                            p = line.strip()
                            if p and not p.startswith("#"):
                                active_set.add(p)
                except Exception:
                    pass

            return len(active_set)
        except Exception:
            return 0

    def _on_proxy_mode_change(self, *args):
        mode = self.var_proxy_mode.get()
        if mode == "direct":
            self.metric_cards["proxies"].config(text="Direct")
        else:
            cnt = self._get_live_proxy_count()
            self.metric_cards["proxies"].config(text=f"{cnt} Aktif")

    # =====================================================================
    # BOT RUNTIME MANAGEMENT
    # =====================================================================
    def _gather_config(self) -> BotConfig:
        cfg = BotConfig()
        try:
            cfg.workers = max(1, int(self.sp_workers.get()))
        except ValueError:
            cfg.workers = 3

        try:
            cfg.batch_count = max(1, int(self.sp_batch.get()))
        except ValueError:
            cfg.batch_count = 50

        cfg.infinite = self.var_infinite.get()

        try:
            cfg.min_percent = float(self.sp_min_pct.get())
            cfg.max_percent = float(self.sp_max_pct.get())
        except ValueError:
            cfg.min_percent = 30.0
            cfg.max_percent = 70.0

        try:
            val = int(self.ent_max_cap.get())
            cfg.max_cap = val if val > 0 else None
        except ValueError:
            cfg.max_cap = None

        try:
            cfg.sleep_between = max(1, int(self.sp_sleep.get()))
        except ValueError:
            cfg.sleep_between = 3

        cfg.proxy_mode = self.var_proxy_mode.get()
        cfg.direct = (cfg.proxy_mode == "direct")
        cfg.skip_fetch = (cfg.proxy_mode == "local")

        # Browser mode: "headless" vs "headed"
        is_headless = (self.var_browser_mode.get() == "headless")
        cfg.headless = is_headless
        cfg.headed = not is_headless
        self.var_headless.set(is_headless)
        cfg.unmute = not self.var_mute.get()

        kw = self.ent_keyword.get().strip()
        if kw:
            cfg.keyword = kw
        ch = self.ent_channel.get().strip()
        if ch:
            cfg.channel = ch

        return cfg

    def _start_bot(self):
        if self.is_running:
            return

        config = self._gather_config()
        self.is_running = True
        self.start_timestamp = time.time()
        self.total_ads_skipped = 0

        # UI state transitions
        self.lbl_master_status.config(text="● BERJALAN (RUNNING)", bg=C_SUCCESS, fg=C_TEXT_DARK)
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.btn_refresh_proxy.config(state="disabled")

        # Generate worker cards dynamically
        for child in self.worker_frame.winfo_children():
            child.destroy()
        self.worker_cards.clear()
        self.worker_ad_counters.clear()
        self.worker_success_counters.clear()

        mode_label = "HEADED" if config.headed else "HEADLESS"
        for w_id in range(1, config.workers + 1):
            card = WorkerCard(self.worker_frame, w_id, mode_str=mode_label)
            card.pack(fill="x", expand=True, pady=4, padx=4)
            self.worker_cards[w_id] = card
            self.worker_ad_counters[w_id] = 0
            self.worker_success_counters[w_id] = 0

        # Reset metric cards
        target_str = "Tak Terbatas" if config.infinite else str(config.batch_count)
        self.metric_cards["views"].config(text=f"0 / {target_str} (0%)")
        self.metric_cards["ads"].config(text="0 Iklan")

        # Multiprocessing primitives
        self.stop_event = multiprocessing.Event()
        self.success_counter = multiprocessing.Value('i', 0)
        self.telemetry_queue = multiprocessing.Queue()
        self.processes = []

        self._append_log("BOT", f"Memulai {config.workers} worker proses. Mode: {'Infinite' if config.infinite else f'Target {config.batch_count}'}.", "cyan")

        # Start background controller thread
        self.bot_thread = threading.Thread(target=self._run_bot_controller, args=(config,), daemon=True)
        self.bot_thread.start()

    def _run_bot_controller(self, config: BotConfig):
        # 1. Resolve Proxies
        proxy_list = []
        if config.direct:
            self._append_log("INIT", "Menggunakan koneksi Direct (Tanpa Proxy).", "yellow")
        else:
            # RESET ISIAN FILE PROXY MENJADI 0 SAAT MEMULAI (FRESH START)
            self._append_log("INIT", "🔄 Mereset file proxy menjadi 0 (Fresh Start)...", "cyan")
            reset_proxy_storage(clear_dead=True, clear_pagination=True, output_json=config.proxy_file)
            self.root.after(0, lambda: self.metric_cards["proxies"].config(text="0 Aktif"))

            min_needed = max(15, config.workers)
            if config.skip_fetch:
                self._append_log("INIT", "Mode Cache Lokal: Penyimpanan di-reset ke 0.", "cyan")
                proxy_list = load_proxies(config.proxy_file)
            else:
                self._append_log("INIT", "Mengambil batch awal proxy fresh (Geonode + ProxyScrape)...", "cyan")
                try:
                    fresh = get_and_verify_proxies(
                        limit=config.proxy_limit,
                        threads=config.proxy_threads,
                        countries=HIGH_CPM_COUNTRIES,
                        output_json=config.proxy_file or DEFAULT_OUTPUT_JSON,
                        merge_existing=False
                    )
                    if fresh:
                        proxy_list = load_proxies(config.proxy_file)
                except Exception as e:
                    self._append_log("INIT", f"Gagal fetch proxy ({e}), melanjutkan.", "yellow")
                    proxy_list = load_proxies(config.proxy_file)

            if len(proxy_list) == 0 and not config.skip_fetch:
                self._append_log("INIT", "Stok proxy masih kosong, memicu auto-refill...", "yellow")
                trigger_refill()
            elif len(proxy_list) < min_needed and not config.skip_fetch:
                self._append_log("INIT", f"Stok proxy lokal ({len(proxy_list)}) < kebutuhan worker ({min_needed}), memulai background auto-refill...", "cyan")
                trigger_refill()
            else:
                self._append_log("INIT", f"Memanfaatkan seluruh {len(proxy_list)} proxy fresh untuk proses worker.", "green")

        self.root.after(0, lambda: self.metric_cards["proxies"].config(text="Direct" if config.direct else f"{self._get_live_proxy_count()} Aktif"))

        # Start continuous background replenishment (rotates Geonode pages beyond 500 & resets every 1 hour)
        if not config.direct and not config.skip_fetch:
            min_needed = max(15, config.workers)
            self._append_log("INIT", f"🔄 Background Replenisher aktif: Auto-refill jika stok < {min_needed}, rotasi tiap 3 menit, auto-reset tiap 1 jam.", "cyan")
            start_background_proxy_replenisher(
                interval_sec=180,
                stop_event=self.stop_event,
                limit=250,
                threads=60,
                output_json=config.proxy_file or DEFAULT_OUTPUT_JSON,
                min_threshold=min_needed,
                countries=HIGH_CPM_COUNTRIES,
                hourly_reset_sec=3600
            )

        # 2. Spawn Worker Processes
        self.processes = []
        for w_id in range(1, config.workers + 1):
            if self.stop_event.is_set():
                break
            p = multiprocessing.Process(
                target=worker_process_main,
                args=(w_id, config, proxy_list, self.success_counter, self.stop_event, self.telemetry_queue),
                daemon=True
            )
            p.start()
            self.processes.append(p)
            time.sleep(1.2)  # Stagger launch

        # 3. Supervise until completion or stop requested
        while not self.stop_event.is_set():
            time.sleep(0.5)
            with self.success_counter.get_lock():
                current = self.success_counter.value
            if not config.infinite and current >= config.batch_count:
                self._append_log("BOT", f"🎯 Target {config.batch_count} view berhasil tercapai!", "green")
                self.stop_event.set()
                break

        # Terminate workers cleanly
        for p in self.processes:
            if p.is_alive():
                p.terminate()
        for p in self.processes:
            p.join(timeout=1.0)

        self.root.after(0, self._on_bot_stopped)

    def _stop_bot(self):
        if not self.is_running:
            return
        self.lbl_master_status.config(text="● MENGHENTIKAN...", bg=C_WARNING, fg=C_TEXT_DARK)
        self._append_log("BOT", "Sinyal stop diterima, mematikan seluruh proses worker...", "yellow")
        if self.stop_event:
            self.stop_event.set()

    def _on_bot_stopped(self):
        self.is_running = False
        self.lbl_master_status.config(text="● BERHENTI (STOPPED)", bg=C_TEXT_MUTED, fg=C_TEXT_DARK)
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.btn_refresh_proxy.config(state="normal")

        # Set all cards to IDLE
        for card in self.worker_cards.values():
            card.update_status("BERHENTI", "Worker dihentikan.")

        final_views = 0
        if self.success_counter:
            with self.success_counter.get_lock():
                final_views = self.success_counter.value
        self._append_log("SELESAI", f"Sesi bot berakhir. Total View Sukses: {final_views} | Total Iklan Di-skip: {self.total_ads_skipped}", "green")

    # =====================================================================
    # TELEMETRY CONSUMER (Non-blocking queue drain)
    # =====================================================================
    def _poll_telemetry(self):
        if self.telemetry_queue is not None:
            while True:
                try:
                    data = self.telemetry_queue.get_nowait()
                except Exception:
                    break

                event = data.get("event")
                worker_id = data.get("worker_id")

                # 1. Log events
                if event == "log":
                    color = "cyan"
                    c_val = data.get("color", "")
                    if "92m" in c_val: color = "green"
                    elif "93m" in c_val: color = "yellow"
                    elif "91m" in c_val: color = "red"
                    elif "95m" in c_val: color = "magenta"
                    self._append_log(str(data.get("prefix", "LOG")), data.get("message", ""), color)

                # 2. Worker state events
                elif isinstance(worker_id, int) and worker_id in self.worker_cards:
                    card = self.worker_cards[worker_id]

                    if event == "init":
                        card.update_pid(data.get("pid", 0))
                        card.update_status("SIAP", data.get("details", ""))

                    elif event == "proxy_selected":
                        card.update_proxy(data.get("proxy", "Direct"))
                        card.update_status("PROXY_TERPILIH", data.get("details", ""))

                    elif event == "status_change":
                        card.update_status(data.get("status", ""), data.get("details", ""))

                    elif event == "video_chosen":
                        card.update_video(data.get("title", ""))
                        card.update_status(data.get("status", "NAVIGASI"), data.get("details", ""))

                    elif event == "watch_start":
                        card.reset_for_session()
                        card.update_video(data.get("title", ""))
                        card.update_status("MENONTON", data.get("details", ""))

                    elif event == "watch_progress":
                        elapsed = data.get("elapsed", 0.0)
                        target = data.get("target", 1.0)
                        pct = data.get("percent", 0.0)
                        tot = data.get("total_duration", 0.0)
                        ads = data.get("ad_count", 0)
                        card.update_watch_progress(elapsed, target, pct, tot, ads)

                    elif event == "ad_skipped":
                        ad_cnt = data.get("ad_count", 0)
                        card.update_ads(ad_cnt)
                        self.total_ads_skipped += 1
                        self.metric_cards["ads"].config(text=f"{self.total_ads_skipped} Iklan")

                    elif event == "session_success":
                        card.update_status("SUKSES", data.get("details", ""))
                        current_total = data.get("total_success", 0)
                        cfg = self._gather_config()
                        target_str = "Tak Terbatas" if cfg.infinite else str(cfg.batch_count)
                        pct_str = f"{(current_total/cfg.batch_count)*100:.0f}%" if not cfg.infinite else ""
                        self.metric_cards["views"].config(text=f"{current_total} / {target_str} ({pct_str})")

        # Update proxy metric card secara dinamis
        self._proxy_poll_tick = getattr(self, "_proxy_poll_tick", 0) + 1
        if self._proxy_poll_tick >= 8:
            self._proxy_poll_tick = 0
            curr_mode = self.var_proxy_mode.get()
            if curr_mode == "direct":
                if self.metric_cards["proxies"].cget("text") != "Direct":
                    self.metric_cards["proxies"].config(text="Direct")
            else:
                cnt = self._get_live_proxy_count()
                target_text = f"{cnt} Aktif"
                old_text = self.metric_cards["proxies"].cget("text")
                if old_text != target_text:
                    old_cnt = getattr(self, "_last_gui_proxy_count", -1)
                    self._last_gui_proxy_count = cnt
                    self.metric_cards["proxies"].config(text=target_text)
                    if self.is_running and old_cnt != -1 and cnt != old_cnt:
                        if cnt > old_cnt:
                            self._append_log("PROXIES", f"🔄 Pool proxy bertambah: {cnt} proxy aktif siap (+{cnt - old_cnt})", "green")
                        else:
                            self._append_log("PROXIES", f"⚠️ Proxy tereliminasi: Sisa {cnt} proxy aktif (-{old_cnt - cnt})", "yellow")

        self.root.after(60, self._poll_telemetry)

    # =====================================================================
    # STANDALONE PROXY REFRESH (Background Thread)
    # =====================================================================
    def _refresh_proxies_async(self):
        self.btn_refresh_proxy.config(state="disabled")
        self._append_log("HYBRID", "Mengambil batch proxy simultan (Geonode + ProxyScrape High-CPM)...", "cyan")

        def _task():
            try:
                proxies = get_and_verify_proxies(limit=250, threads=60, countries=HIGH_CPM_COUNTRIES)
                alive_count = len(proxies)
                self.root.after(0, lambda: self._on_proxy_refresh_done(alive_count))
            except Exception as e:
                self.root.after(0, lambda: self._on_proxy_refresh_err(str(e)))

        threading.Thread(target=_task, daemon=True).start()

    def _on_proxy_refresh_done(self, count: int):
        self.btn_refresh_proxy.config(state="normal")
        live_cnt = self._get_live_proxy_count()
        self.metric_cards["proxies"].config(text=f"{live_cnt} Aktif")
        self._append_log("HYBRID", f"Pembaruan selesai! {live_cnt} proxy aktif siap digunakan.", "green")

    def _on_proxy_refresh_err(self, err_msg: str):
        self.btn_refresh_proxy.config(state="normal")
        self._append_log("HYBRID", f"Gagal memperbarui proxy: {err_msg}", "red")

    def _on_close(self):
        if self.is_running:
            if messagebox.askokcancel("Keluar Aplikasi", "Bot sedang berjalan. Apakah Anda yakin ingin mematikan bot dan keluar?"):
                self._stop_bot()
                time.sleep(0.5)
                self.root.destroy()
                os._exit(0)
        else:
            self.root.destroy()
            os._exit(0)

def main():
    root = tk.Tk()
    app = YouTubeStudioGUI(root)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            if getattr(app, "is_running", False):
                app._stop_bot()
        except Exception:
            pass
        os._exit(0)

if __name__ == "__main__":
    main()
