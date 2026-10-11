#!/usr/bin/env python3
"""
Bot Controller Engine
---------------------
Unified multiprocessing controller for YouTube Search & Watch Bot.
Shares the exact same architecture, state transitions, proxy lifecycle,
and telemetry streaming between Python Desktop GUI, CLI, and Web Dashboard.
"""

import os
import sys
import time
import json
import random
import threading
import multiprocessing
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any, List, Callable

from geonode_fetcher import (
    DEFAULT_OUTPUT_JSON,
    get_and_verify_proxies,
    reset_proxy_storage,
    start_background_proxy_replenisher,
    trigger_refill,
    get_dead_proxies,
)
from youtube_search import (
    worker_process_main,
    load_proxies,
    safe_log,
    CYAN,
    GREEN,
    YELLOW,
    RED,
)

_GEO_CACHE_FILE = os.path.join(os.path.dirname(__file__), ".proxy_geo_cache.json")
_GEO_CACHE: Dict[str, str] = {}

def _load_geo_cache():
    global _GEO_CACHE
    if not _GEO_CACHE and os.path.exists(_GEO_CACHE_FILE):
        try:
            with open(_GEO_CACHE_FILE, "r", encoding="utf-8") as f:
                _GEO_CACHE = json.load(f)
        except Exception:
            _GEO_CACHE = {}

def _save_geo_cache():
    try:
        with open(_GEO_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_GEO_CACHE, f, ensure_ascii=False)
    except Exception:
        pass

def country_code_to_flag(country_code: Optional[str]) -> str:
    """Converts 2-letter ISO country code into Unicode flag emoji, fallback to globe."""
    if not country_code or len(country_code) != 2:
        return "🌐"
    try:
        cc = country_code.upper()
        return chr(ord(cc[0]) + 127397) + chr(ord(cc[1]) + 127397)
    except Exception:
        return "🌐"

def extract_ip_from_proxy(proxy_str: str) -> Optional[str]:
    if not proxy_str or proxy_str.lower() in ("direct", "none", ""):
        return None
    s = proxy_str.replace("http://", "").replace("https://", "").replace("socks5://", "").replace("socks4://", "")
    if "@" in s:
        s = s.split("@")[-1]
    host = s.split(":")[0].strip()
    return host if host else None

def resolve_proxy_country(proxy_str: str, timeout: float = 1.5) -> tuple[str, str]:
    """
    Returns (country_code, flag_emoji), e.g. ('ID', '🇮🇩') or ('GL', '🌐').
    Uses local cache and fast lookup.
    """
    if not proxy_str or proxy_str.lower() in ("direct", "none", ""):
        return ("DIRECT", "🌐")

    ip = extract_ip_from_proxy(proxy_str)
    if not ip:
        return ("GL", "🌐")

    _load_geo_cache()
    if ip in _GEO_CACHE:
        cc = _GEO_CACHE[ip]
        return (cc, country_code_to_flag(cc))

    # Fast lookup via api.country.is
    try:
        import urllib.request
        req = urllib.request.Request(f"https://api.country.is/{ip}", headers={"User-Agent": "AutoView/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            cc = (data.get("country") or "").upper()
            if cc and len(cc) == 2:
                _GEO_CACHE[ip] = cc
                _save_geo_cache()
                return (cc, country_code_to_flag(cc))
    except Exception:
        pass

    return ("GL", "🌐")

def format_proxy_with_flag(proxy_str: str, country_code: Optional[str] = None, flag: Optional[str] = None) -> str:
    """Formats proxy string with country flag and protocol, e.g. '🇮🇩 ID • SOCKS5: 147.139.133.251:10080'."""
    if not proxy_str or proxy_str.lower() in ("direct", "none", ""):
        return "🌐 Direct"

    if not country_code or not flag or flag == "🌐":
        cc, fl = resolve_proxy_country(proxy_str)
        country_code = country_code or cc
        flag = flag or fl

    short = proxy_str.replace("http://", "").replace("https://", "").replace("socks5://", "").replace("socks4://", "")
    proto = "HTTP"
    if "://" in proxy_str:
        proto = proxy_str.split("://")[0].upper()
    elif ":" in short:
        proto = "HTTP"

    if country_code and country_code not in ("DIRECT", "GL", ""):
        return f"{flag} {country_code} • {proto}: {short}"
    return f"{proto}: {short}"

@dataclass
class BotConfig:
    workers: int = 3
    batch_count: int = 50
    infinite: bool = False
    min_percent: float = 30.0
    max_percent: float = 70.0
    max_cap: Optional[int] = None
    sleep_between: int = 3
    proxy_mode: str = "geonode"  # "geonode", "local", "direct"
    proxy_file: Optional[str] = None
    proxy_limit: int = 500
    proxy_threads: int = 80
    proxy_reset_interval: int = 3600
    headless: bool = True
    headed: bool = False
    unmute: bool = False
    min_delay: int = 50
    max_delay: int = 120
    direct: bool = False
    skip_fetch: bool = False
    keyword: str = "horrornologi"
    channel: str = "horrornologi"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BotConfig":
        valid_keys = cls.__dataclass_fields__.keys()
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        # Sync headed & headless
        if "headed" in filtered:
            filtered["headless"] = not bool(filtered["headed"])
        elif "headless" in filtered:
            filtered["headed"] = not bool(filtered["headless"])
        return cls(**filtered)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class BotController:
    """
    Central Bot Execution Controller.
    Manages OS processes, proxy lifecycle, supervision, and clean shutdowns.
    """
    def __init__(
        self,
        config: Optional[BotConfig] = None,
        telemetry_queue: Optional[multiprocessing.Queue] = None,
        on_status_change: Optional[Callable[[str], None]] = None,
        on_finish: Optional[Callable[[int, int], None]] = None
    ):
        self.config = config or BotConfig()
        self.telemetry_queue = telemetry_queue
        self.on_status_change = on_status_change
        self.on_finish = on_finish

        self._lock = threading.Lock()
        self._is_running = False
        self._controller_thread: Optional[threading.Thread] = None

        self.stop_event: Optional[multiprocessing.Event] = None
        self.success_counter: Optional[multiprocessing.Value] = None
        self.processes: List[multiprocessing.Process] = []
        self.start_timestamp: float = 0.0
        self.total_ads_skipped: int = 0
        self.status: str = "IDLE"  # "IDLE", "RUNNING", "STOPPING", "STOPPED"

    def is_running(self) -> bool:
        with self._lock:
            return self._is_running

    def get_status(self) -> str:
        with self._lock:
            return self.status

    def _set_status(self, new_status: str):
        with self._lock:
            self.status = new_status
        if self.on_status_change:
            try:
                self.on_status_change(new_status)
            except Exception:
                pass
        if self.telemetry_queue:
            try:
                self.telemetry_queue.put({
                    "event": "bot_status_change",
                    "status": new_status,
                    "timestamp": time.time()
                })
            except Exception:
                pass

    def log(self, prefix: str, message: str, color: str = "cyan"):
        if self.telemetry_queue:
            try:
                c_code = "\033[96m"
                if color == "green": c_code = "\033[92m"
                elif color == "yellow": c_code = "\033[93m"
                elif color == "red": c_code = "\033[91m"
                elif color == "magenta": c_code = "\033[95m"

                self.telemetry_queue.put({
                    "event": "log",
                    "prefix": prefix,
                    "message": message,
                    "color": c_code,
                    "timestamp": time.strftime("%H:%M:%S")
                })
            except Exception:
                pass
        # Also print to stdout for CLI visibility
        safe_log(prefix, message, CYAN if color == "cyan" else (GREEN if color == "green" else (YELLOW if color == "yellow" else RED)))

    def get_live_proxy_count(self) -> int:
        try:
            target_file = self.config.proxy_file or DEFAULT_OUTPUT_JSON
            if not os.path.exists(target_file):
                return 0
            dead = get_dead_proxies()
            with open(target_file, "r", encoding="utf-8", errors="ignore") as f:
                p_data = json.load(f)
            if isinstance(p_data, list):
                return len([
                    x for x in p_data
                    if (x.get("proxy") if isinstance(x, dict) else str(x)).strip() not in dead
                ])
        except Exception:
            pass
        return 0

    def start(self, override_config: Optional[BotConfig] = None) -> bool:
        with self._lock:
            if self._is_running:
                return False
            if override_config:
                self.config = override_config
            self._is_running = True
            self.status = "RUNNING"

        self.start_timestamp = time.time()
        self.total_ads_skipped = 0

        # Initialize multiprocessing primitives
        self.stop_event = multiprocessing.Event()
        self.success_counter = multiprocessing.Value('i', 0)
        if self.telemetry_queue is None:
            self.telemetry_queue = multiprocessing.Queue()

        self._set_status("RUNNING")
        self._controller_thread = threading.Thread(target=self._run_controller, daemon=True, name="BotControllerWorker")
        self._controller_thread.start()
        return True

    def _run_controller(self):
        config = self.config
        self.log("BOT", f"Memulai {config.workers} worker proses. Mode: {'Infinite' if config.infinite else f'Target {config.batch_count}'}.", "cyan")

        # 1. Resolve Proxies
        proxy_list = []
        if config.direct:
            self.log("INIT", "Menggunakan koneksi Direct (Tanpa Proxy).", "yellow")
        else:
            self.log("INIT", "🔄 Mereset file proxy menjadi 0 (Fresh Start)...", "cyan")
            reset_proxy_storage(clear_dead=True, clear_pagination=True, output_json=config.proxy_file)
            if self.telemetry_queue:
                self.telemetry_queue.put({"event": "proxy_count_update", "count": 0})

            min_needed = max(15, config.workers)
            if config.skip_fetch:
                self.log("INIT", "Mode Cache Lokal: Memuat dari file yang ada...", "cyan")
                proxy_list = load_proxies(config.proxy_file)
            else:
                self.log("INIT", "Mengambil batch awal proxy fresh Global (Geonode + ProxyScrape + Proxifly + TheSpeedX + IPLocate + Databay)...", "cyan")
                try:
                    fresh = get_and_verify_proxies(
                        limit=config.proxy_limit,
                        threads=config.proxy_threads,
                        countries=None,
                        output_json=config.proxy_file or DEFAULT_OUTPUT_JSON,
                        merge_existing=False
                    )
                    if fresh:
                        proxy_list = load_proxies(config.proxy_file)
                except Exception as e:
                    self.log("INIT", f"Gagal fetch proxy ({e}), melanjutkan.", "yellow")
                    proxy_list = load_proxies(config.proxy_file)

            if len(proxy_list) == 0 and not config.skip_fetch:
                self.log("INIT", "Stok proxy masih kosong, memicu auto-refill...", "yellow")
                trigger_refill()
            elif len(proxy_list) < min_needed and not config.skip_fetch:
                self.log("INIT", f"Stok proxy lokal ({len(proxy_list)}) < kebutuhan worker ({min_needed}), memulai background auto-refill...", "cyan")
                trigger_refill()
            else:
                self.log("INIT", f"Memanfaatkan seluruh {len(proxy_list)} proxy fresh untuk proses worker.", "green")

        if self.telemetry_queue:
            live_cnt = len(proxy_list) if not config.direct else 0
            self.telemetry_queue.put({"event": "proxy_count_update", "count": live_cnt})

        # Start continuous background replenishment
        if not config.direct and not config.skip_fetch:
            min_needed = max(15, config.workers)
            self.log("INIT", f"🔄 Background Replenisher aktif: Auto-refill jika stok < {min_needed}, rotasi tiap 3 menit, auto-reset tiap 1 jam.", "cyan")
            try:
                start_background_proxy_replenisher(
                    interval_sec=180,
                    stop_event=self.stop_event,
                    limit=250,
                    threads=60,
                    output_json=config.proxy_file or DEFAULT_OUTPUT_JSON,
                    min_threshold=min_needed,
                    countries=None,
                    hourly_reset_sec=config.proxy_reset_interval
                )
            except Exception as e:
                self.log("INIT", f"Gagal memulai proxy replenisher: {e}", "yellow")

        # 2. Spawn Worker Processes
        self.processes = []
        mode_label = "HEADED" if config.headed else "HEADLESS"
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

        # 3. Supervise loop with hot-reload proxy count
        last_proxy_check = 0.0
        last_reported_proxy_count = len(proxy_list)

        while not self.stop_event.is_set():
            time.sleep(0.5)
            now = time.time()

            # Hot-reload proxy count tracking
            if not config.direct and (now - last_proxy_check >= 3.0):
                last_proxy_check = now
                try:
                    cur_count = self.get_live_proxy_count()
                    if cur_count != last_reported_proxy_count:
                        if cur_count > last_reported_proxy_count:
                            self.log("PROXIES", f"🔄 Pool proxy bertambah: {cur_count} proxy aktif siap (+{cur_count - last_reported_proxy_count})", "green")
                        else:
                            self.log("PROXIES", f"⚠️ Proxy tereliminasi: Sisa {cur_count} proxy aktif (-{last_reported_proxy_count - cur_count})", "yellow")
                        last_reported_proxy_count = cur_count
                        if self.telemetry_queue:
                            self.telemetry_queue.put({"event": "proxy_count_update", "count": cur_count})
                except Exception:
                    pass

            with self.success_counter.get_lock():
                current = self.success_counter.value
            if not config.infinite and current >= config.batch_count:
                self.log("BOT", f"🎯 Target {config.batch_count} view berhasil tercapai!", "green")
                self.stop_event.set()
                break

        # Terminate workers cleanly
        for p in self.processes:
            if p.is_alive():
                p.terminate()
        for p in self.processes:
            p.join(timeout=1.0)

        final_views = 0
        if self.success_counter:
            with self.success_counter.get_lock():
                final_views = self.success_counter.value

        self.log("SELESAI", f"Sesi bot berakhir. Total View Sukses: {final_views} | Total Iklan Di-skip: {self.total_ads_skipped}", "green")

        with self._lock:
            self._is_running = False
            self.status = "STOPPED"

        self._set_status("STOPPED")

        if self.on_finish:
            try:
                self.on_finish(final_views, self.total_ads_skipped)
            except Exception:
                pass

    def stop(self):
        with self._lock:
            if not self._is_running:
                return
            self.status = "STOPPING"
        self._set_status("STOPPING")
        self.log("BOT", "Sinyal stop diterima, mematikan seluruh proses worker...", "yellow")
        if self.stop_event:
            self.stop_event.set()
