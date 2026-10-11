#!/usr/bin/env python3
"""
AutoView Web Telemetry Monitor & Studio Controller
--------------------------------------------------
Starlette + Uvicorn + Server-Sent Events (SSE) Real-Time Telemetry Dashboard.
Provides 1-to-1 parity with Desktop Python GUI (gui_app.py) for process lifecycle,
metrics stability, worker cards, terminal logging, and full CLI execution support.
"""

import os
import sys
import time
import json
import asyncio
import argparse
import threading
import collections
from typing import Dict, Any, Optional
from starlette.applications import Starlette
from starlette.responses import JSONResponse, HTMLResponse, StreamingResponse
from starlette.routing import Route
import uvicorn

from bot_controller import BotConfig, BotController

def format_elapsed(seconds: float) -> str:
    s = int(seconds)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

def format_proxy_display(proxy_str: str) -> str:
    if not proxy_str or proxy_str.lower() in ("direct", "none", ""):
        return "Direct"
    short = proxy_str.replace("http://", "").replace("socks5://", "").replace("socks4://", "")
    if "://" in proxy_str:
        proto = proxy_str.split("://")[0].upper()
        return f"{proto}: {short}"
    return f"HTTP: {short}" if ":" in short else short

def map_worker_status(raw_status: str) -> tuple[str, str]:
    """Maps status to human-readable label and color pill matching gui_app.py."""
    s = (raw_status or "").upper()
    if "MENONTON" in s or "LOCK" in s:
        return ("🟢 MENONTON (LOCKED)", "green")
    elif "MENCARI" in s:
        return ("🟡 MENCARI VIDEO", "amber")
    elif "NAVIGASI" in s or "MASUK_CHANNEL" in s:
        return ("🔵 NAVIGASI", "blue")
    elif "COOLDOWN" in s or "JEDA" in s:
        return ("🟣 JEDA / COOLDOWN", "purple")
    elif "SUKSES" in s:
        return ("✔ SUKSES", "green")
    elif "ERROR" in s or "GAGAL" in s or "TIMEOUT" in s or "BOT_BLOCKED" in s or "DITUTUP" in s:
        return ("❌ KENDALA", "red")
    elif "REFILL" in s:
        return ("🔄 REFILL PROXY", "blue")
    elif "PROXY" in s:
        return ("🌐 PROXY TERPILIH", "blue")
    elif "BERHENTI" in s:
        return ("BERHENTI", "muted")
    return (raw_status or "SIAP (IDLE)", "blue")

def get_live_proxy_count(filepath: Optional[str] = None) -> int:
    """Returns the true number of working proxies in the active storage file."""
    try:
        from geonode_fetcher import DEFAULT_OUTPUT_JSON, get_dead_proxies
        target_path = filepath or DEFAULT_OUTPUT_JSON
        if not os.path.exists(target_path):
            return 0
        dead = get_dead_proxies()
        with open(target_path, "r", encoding="utf-8", errors="ignore") as f:
            data = json.load(f)
        if isinstance(data, list):
            return len([
                x for x in data
                if (x.get("proxy") if isinstance(x, dict) else str(x)).strip() not in dead
            ])
    except Exception:
        pass
    return 0

class DashboardStateManager:
    def __init__(self, initial_config: Optional[Dict[str, Any]] = None, bot_controller: Optional[BotController] = None):
        cfg = initial_config or {}
        self.config = dict(cfg)
        self.bot_controller = bot_controller
        self.start_time = time.time()
        self.proxy_file = cfg.get("proxy_file")
        self.direct = cfg.get("direct", False)
        self.default_mode = "HEADED" if cfg.get("headed") else "HEADLESS"

        initial_proxies = cfg.get("proxy_count", 0)
        if not self.direct and initial_proxies == 0:
            initial_proxies = get_live_proxy_count(self.proxy_file)

        self.summary = {
            "batch_target": cfg.get("batch_count", 0),
            "is_infinite": cfg.get("infinite", False),
            "keyword": cfg.get("keyword", "horrornologi"),
            "channel": cfg.get("channel", "horrornologi"),
            "total_views": 0,
            "total_ads_skipped": 0,
            "active_workers": cfg.get("workers", 0),
            "active_proxies": initial_proxies,
            "uptime_seconds": 0,
            "uptime_str": "00:00",
            "bot_status": "RUNNING" if (self.bot_controller and self.bot_controller.is_running()) else cfg.get("bot_status", "RUNNING"),
            "mode": self.default_mode,
            "min_percent": cfg.get("min_percent", 30.0),
            "max_percent": cfg.get("max_percent", 70.0),
            "unmute": cfg.get("unmute", False),
            "direct": self.direct,
        }
        self.workers: Dict[int, Dict[str, Any]] = {}
        self.logs = collections.deque(maxlen=400)
        self._listeners: list[asyncio.Queue] = []
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def set_bot_controller(self, controller: BotController):
        with self._lock:
            self.bot_controller = controller
            if controller.is_running():
                self.summary["bot_status"] = "RUNNING"

    def get_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            self.summary["uptime_seconds"] = int(time.time() - self.start_time)
            self.summary["uptime_str"] = format_elapsed(self.summary["uptime_seconds"])
            if self.bot_controller:
                self.summary["bot_status"] = self.bot_controller.get_status()
            if not self.direct:
                live_p = get_live_proxy_count(self.proxy_file)
                if live_p > 0 or self.summary["active_proxies"] == 0:
                    self.summary["active_proxies"] = live_p
            return {
                "summary": dict(self.summary),
                "workers": {w_id: dict(data) for w_id, data in self.workers.items()},
                "logs": list(self.logs),
                "config": dict(self.config)
            }

    def subscribe(self) -> asyncio.Queue:
        q = asyncio.Queue()
        with self._lock:
            self._listeners.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            if q in self._listeners:
                self._listeners.remove(q)

    def _broadcast(self, event_type: str, payload: Dict[str, Any]):
        msg = f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"
        with self._lock:
            listeners = list(self._listeners)
        for q in listeners:
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(q.put_nowait, msg)
            else:
                try:
                    q.put_nowait(msg)
                except Exception:
                    pass

    def process_event(self, data: Dict[str, Any]):
        event = data.get("event")
        worker_id = data.get("worker_id")

        # 1. Log event
        if event == "log":
            log_item = {
                "timestamp": data.get("timestamp") or time.strftime("%H:%M:%S"),
                "prefix": data.get("prefix", str(worker_id or "SYSTEM")),
                "message": data.get("message", ""),
                "color": data.get("color", "cyan")
            }
            with self._lock:
                self.logs.append(log_item)
            self._broadcast("log", log_item)
            return

        with self._lock:
            # 2. Master Bot Status update
            if event == "bot_status_change":
                self.summary["bot_status"] = data.get("status", self.summary["bot_status"])

            # 3. Proxy count update
            elif event == "proxy_count_update":
                self.summary["active_proxies"] = data.get("count", self.summary["active_proxies"])

            # 4. Ad skipped event (Global Lifetime Monotonic Tracker)
            elif event == "ad_skipped":
                self.summary["total_ads_skipped"] += 1
                if isinstance(worker_id, int):
                    if worker_id not in self.workers:
                        self._ensure_worker_entry(worker_id)
                    w = self.workers[worker_id]
                    w["ads_skipped"] = w.get("ads_skipped", 0) + 1
                    w["session_ads_skipped"] = int(data.get("ad_count", 0))

            # 5. Worker lifecycle & progress telemetry events
            if isinstance(worker_id, int):
                if worker_id not in self.workers:
                    self._ensure_worker_entry(worker_id)
                w = self.workers[worker_id]

                if event == "init":
                    w["pid"] = data.get("pid", 0)
                    raw_st = data.get("status", "SIAP")
                    lbl, col = map_worker_status(raw_st)
                    w["status"] = raw_st
                    w["status_display"] = lbl
                    w["status_color"] = col
                    w["details"] = data.get("details", "")

                elif event == "proxy_selected":
                    raw_proxy = data.get("proxy", "Direct")
                    w["proxy"] = raw_proxy
                    w["country"] = data.get("country", "")
                    w["country_flag"] = data.get("country_flag", "")
                    w["persona"] = data.get("persona", w.get("persona", ""))

                    if "proxy_display" in data:
                        w["proxy_display"] = data["proxy_display"]
                    else:
                        from bot_controller import format_proxy_with_flag
                        w["proxy_display"] = format_proxy_with_flag(raw_proxy, w["country"], w["country_flag"])

                    w["raw_proxy"] = raw_proxy
                    lbl, col = map_worker_status("PROXY_TERPILIH")
                    w["status"] = "PROXY_TERPILIH"
                    w["status_display"] = lbl
                    w["status_color"] = col
                    w["details"] = data.get("details", "")

                elif event == "status_change":
                    raw_st = data.get("status", w["status"])
                    lbl, col = map_worker_status(raw_st)
                    w["status"] = raw_st
                    w["status_display"] = lbl
                    w["status_color"] = col
                    w["details"] = data.get("details", "")

                elif event == "video_chosen":
                    w["video_title"] = data.get("title", "")
                    w["video_url"] = data.get("url", w.get("video_url", ""))
                    w["channel"] = data.get("channel", w.get("channel", ""))
                    if data.get("persona"):
                        w["persona"] = data["persona"]
                    raw_st = data.get("status", "NAVIGASI")
                    lbl, col = map_worker_status(raw_st)
                    w["status"] = raw_st
                    w["status_display"] = lbl
                    w["status_color"] = col
                    w["details"] = data.get("details", "")

                elif event == "watch_start":
                    w["video_title"] = data.get("title", w["video_title"])
                    if data.get("url"):
                        w["video_url"] = data["url"]
                    if data.get("channel"):
                        w["channel"] = data["channel"]
                    if data.get("persona"):
                        w["persona"] = data["persona"]
                    lbl, col = map_worker_status("MENONTON")
                    w["status"] = "MENONTON"
                    w["status_display"] = lbl
                    w["status_color"] = col
                    w["elapsed_sec"] = 0.0
                    w["duration_sec"] = float(data.get("target_duration", 0.0))
                    w["target_sec"] = float(data.get("target_duration", 0.0))
                    w["percent"] = 0.0
                    w["session_ads_skipped"] = 0
                    w["details"] = data.get("details", "")

                elif event == "watch_progress":
                    elapsed_sec = float(data.get("elapsed", 0.0))
                    target_sec = float(data.get("target", 0.0)) or float(data.get("duration", 0.0))
                    w["elapsed_sec"] = elapsed_sec
                    w["duration_sec"] = target_sec
                    w["target_sec"] = target_sec
                    if data.get("total_duration"):
                        w["total_duration"] = float(data["total_duration"])
                    if data.get("video_url"):
                        w["video_url"] = data["video_url"]
                    if data.get("channel"):
                        w["channel"] = data["channel"]
                    if "current_play_time" in data:
                        w["current_play_time"] = float(data["current_play_time"])

                    if target_sec > 0:
                        w["percent"] = round(min(100.0, (elapsed_sec / target_sec) * 100.0), 1)
                    else:
                        w["percent"] = round(float(data.get("percent", 0.0)), 1)

                    lbl, col = map_worker_status("MENONTON")
                    w["status"] = "MENONTON"
                    w["status_display"] = lbl
                    w["status_color"] = col
                    w["details"] = data.get("details", "")

                    # Non-decreasing stable ads metric sync
                    session_ads = int(data.get("ad_count", 0))
                    w["session_ads_skipped"] = session_ads
                    # Sync if worker cumulative ads is behind
                    if session_ads > w.get("ads_skipped", 0):
                        w["ads_skipped"] = session_ads
                    # Guard total_ads_skipped from ever decreasing
                    worker_sum = sum(wk.get("ads_skipped", 0) for wk in self.workers.values())
                    if worker_sum > self.summary["total_ads_skipped"]:
                        self.summary["total_ads_skipped"] = worker_sum

                elif event == "ad_status":
                    sess_ads = int(data.get("skippable", 0))
                    w["session_ads_skipped"] = sess_ads
                    if sess_ads > w.get("ads_skipped", 0):
                        w["ads_skipped"] = sess_ads
                    worker_sum = sum(wk.get("ads_skipped", 0) for wk in self.workers.values())
                    if worker_sum > self.summary["total_ads_skipped"]:
                        self.summary["total_ads_skipped"] = worker_sum

                elif event in ("watch_complete", "session_success"):
                    raw_st = "SUKSES"
                    lbl, col = map_worker_status(raw_st)
                    w["status"] = raw_st
                    w["status_display"] = lbl
                    w["status_color"] = col
                    w["details"] = data.get("details", "Selesai")
                    if "total_success" in data:
                        self.summary["total_views"] = max(self.summary["total_views"], int(data["total_success"]))
                    else:
                        self.summary["total_views"] += 1

                elif event == "session_failed":
                    raw_st = data.get("status", "GAGAL")
                    lbl, col = map_worker_status(raw_st)
                    w["status"] = raw_st
                    w["status_display"] = lbl
                    w["status_color"] = col
                    w["details"] = data.get("details", "Gagal")

        # Broadcast state snapshot update to web clients
        self._broadcast("update", self.get_snapshot())

    def _ensure_worker_entry(self, worker_id: int):
        self.workers[worker_id] = {
            "worker_id": worker_id,
            "pid": 0,
            "status": "SIAP",
            "status_display": "SIAP (IDLE)",
            "status_color": "blue",
            "details": "",
            "video_title": "",
            "video_url": "",
            "channel": self.config.get("channel", "horrornologi"),
            "proxy": "Direct",
            "proxy_display": "🌐 Direct",
            "country": "DIRECT",
            "country_flag": "🌐",
            "persona": "",
            "mode": self.default_mode,
            "elapsed_sec": 0.0,
            "duration_sec": 0.0,
            "current_play_time": 0.0,
            "percent": 0.0,
            "ads_skipped": 0,
            "session_ads_skipped": 0,
        }

def create_app(state_manager: DashboardStateManager, static_html_path: Optional[str] = None) -> Starlette:
    async def index(request):
        html_file = static_html_path or os.path.join(os.path.dirname(__file__), "web", "index.html")
        if os.path.exists(html_file):
            with open(html_file, "r", encoding="utf-8") as f:
                content = f.read()
            return HTMLResponse(content)
        return HTMLResponse("<h1>AutoView Dashboard</h1><p>Frontend template not found.</p>")

    async def get_status(request):
        loop = asyncio.get_running_loop()
        state_manager.set_loop(loop)
        return JSONResponse(state_manager.get_snapshot())

    async def health(request):
        return JSONResponse({"status": "ok"})

    async def get_logs(request):
        with state_manager._lock:
            return JSONResponse({"logs": list(state_manager.logs)})

    async def get_config(request):
        return JSONResponse({
            "status": "ok",
            "config": state_manager.config,
            "summary": state_manager.get_snapshot()["summary"]
        })

    async def refresh_proxies(request):
        try:
            from geonode_fetcher import trigger_refill
            trigger_refill()
            return JSONResponse({"status": "ok", "message": "Auto-refill proxy dipicu di background."})
        except Exception as e:
            return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

    async def stop_bot(request):
        if state_manager.bot_controller:
            state_manager.bot_controller.stop()
            state_manager.summary["bot_status"] = "STOPPING"
            state_manager._broadcast("update", state_manager.get_snapshot())
            return JSONResponse({"status": "ok", "message": "Sinyal stop berhasil dikirim ke bot."})
        return JSONResponse({"status": "error", "message": "Bot controller tidak terhubung."}, status_code=400)

    async def start_bot(request):
        if not state_manager.bot_controller:
            return JSONResponse({"status": "error", "message": "Bot controller tidak terhubung."}, status_code=400)

        if state_manager.bot_controller.is_running():
            return JSONResponse({"status": "error", "message": "Bot sedang berjalan."}, status_code=400)

        try:
            payload = await request.json()
        except Exception:
            payload = {}

        # Merge config
        current_cfg = state_manager.config
        new_cfg_dict = dict(current_cfg)
        new_cfg_dict.update(payload)
        new_config = BotConfig.from_dict(new_cfg_dict)

        state_manager.config = new_cfg_dict
        state_manager.summary["batch_target"] = new_config.batch_count
        state_manager.summary["is_infinite"] = new_config.infinite
        state_manager.summary["keyword"] = new_config.keyword
        state_manager.summary["channel"] = new_config.channel
        state_manager.summary["active_workers"] = new_config.workers
        state_manager.default_mode = "HEADED" if new_config.headed else "HEADLESS"
        state_manager.summary["mode"] = state_manager.default_mode
        state_manager.summary["bot_status"] = "RUNNING"
        state_manager.start_time = time.time()

        # Reset workers list
        with state_manager._lock:
            state_manager.workers.clear()

        success = state_manager.bot_controller.start(override_config=new_config)
        state_manager._broadcast("update", state_manager.get_snapshot())
        if success:
            return JSONResponse({"status": "ok", "message": "Bot berhasil dimulai."})
        return JSONResponse({"status": "error", "message": "Gagal memulai bot."}, status_code=500)

    async def sse_stream(request):
        loop = asyncio.get_running_loop()
        state_manager.set_loop(loop)
        q = state_manager.subscribe()

        async def event_generator():
            try:
                init_msg = f"event: init\ndata: {json.dumps(state_manager.get_snapshot())}\n\n"
                yield init_msg.encode("utf-8")
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=2.0)
                        yield msg.encode("utf-8")
                    except asyncio.TimeoutError:
                        yield b": ping\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                state_manager.unsubscribe(q)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )

    routes = [
        Route("/", endpoint=index, methods=["GET"]),
        Route("/api/status", endpoint=get_status, methods=["GET"]),
        Route("/api/health", endpoint=health, methods=["GET"]),
        Route("/api/logs", endpoint=get_logs, methods=["GET"]),
        Route("/api/config", endpoint=get_config, methods=["GET"]),
        Route("/api/refresh-proxies", endpoint=refresh_proxies, methods=["POST", "GET"]),
        Route("/api/start", endpoint=start_bot, methods=["POST"]),
        Route("/api/stop", endpoint=stop_bot, methods=["POST"]),
        Route("/api/stream", endpoint=sse_stream, methods=["GET"]),
    ]
    return Starlette(routes=routes)

def start_web_dashboard(
    telemetry_queue,
    host: str = "0.0.0.0",
    port: int = 5000,
    initial_config: Optional[Dict[str, Any]] = None,
    bot_controller: Optional[BotController] = None
) -> tuple[DashboardStateManager, threading.Thread]:
    state_manager = DashboardStateManager(initial_config, bot_controller=bot_controller)

    def queue_listener():
        while True:
            try:
                data = telemetry_queue.get(timeout=0.2)
                if data is None or data.get("event") == "SHUTDOWN":
                    break
                state_manager.process_event(data)
            except Exception:
                continue

    q_thread = threading.Thread(target=queue_listener, daemon=True, name="TelemetryConsumerThread")
    q_thread.start()

    app = create_app(state_manager)
    config = uvicorn.Config(app=app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)

    server_thread = threading.Thread(target=server.run, daemon=True, name="WebDashboardServerThread")
    server_thread.start()

    return state_manager, server_thread

def main():
    """
    CLI Entrypoint for running Web Dashboard & Bot directly from terminal.
    Follows identical concepts, arguments, and architecture as Python Desktop GUI & youtube_search.py.
    """
    parser = argparse.ArgumentParser(
        description="AutoView Web Telemetry Monitor & Studio CLI Runner"
    )
    parser.add_argument("-w", "--workers", type=int, default=3, help="Jumlah proses worker bersamaan (default: 3)")
    parser.add_argument("-b", "--batch", dest="batch_count", type=int, default=50, help="Target total view (default: 50)")
    parser.add_argument("--infinite", action="store_true", help="Mode 24 jam nonstop tanpa batas")
    parser.add_argument("--min-percent", type=float, default=30.0, help="Min durasi tonton %% (default: 30.0)")
    parser.add_argument("--max-percent", type=float, default=70.0, help="Max durasi tonton %% (default: 70.0)")
    parser.add_argument("--max-cap", type=int, default=None, help="Batas maksimal detik menonton")
    parser.add_argument("--sleep-between", type=int, default=3, help="Jeda antar sesi dalam detik (default: 3)")
    parser.add_argument("--direct", action="store_true", help="Mode Direct tanpa proxy")
    parser.add_argument("--skip-fetch", action="store_true", help="Gunakan cache proxy lokal tanpa download")
    parser.add_argument("--proxy-file", type=str, default=None, help="File daftar proxy")
    parser.add_argument("--proxy-limit", type=int, default=500, help="Limit fetch proxy (default: 500)")
    parser.add_argument("--proxy-threads", type=int, default=80, help="Thread verifikasi proxy (default: 80)")
    parser.add_argument("--proxy-reset-interval", type=int, default=3600, help="Interval reset pool proxy (detik)")
    parser.add_argument("--headed", action="store_true", help="Buka jendela browser tampak (default: Headless)")
    parser.add_argument("--unmute", action="store_true", help="Nyalakan suara browser (default: Muted)")
    parser.add_argument("--keyword", type=str, default="horrornologi", help="Kata kunci pencarian (default: horrornologi)")
    parser.add_argument("--channel", type=str, default="horrornologi", help="Target channel filter ketat (default: horrornologi)")
    parser.add_argument("--web-host", type=str, default="0.0.0.0", help="Web Dashboard Host (default: 0.0.0.0)")
    parser.add_argument("--web-port", type=int, default=5000, help="Web Dashboard Port (default: 5000)")

    args = parser.parse_args()
    args.headless = not args.headed

    import multiprocessing
    telemetry_q = multiprocessing.Queue()

    config = BotConfig(
        workers=args.workers,
        batch_count=args.batch_count,
        infinite=args.infinite,
        min_percent=args.min_percent,
        max_percent=args.max_percent,
        max_cap=args.max_cap,
        sleep_between=args.sleep_between,
        direct=args.direct,
        skip_fetch=args.skip_fetch,
        proxy_file=args.proxy_file,
        proxy_limit=args.proxy_limit,
        proxy_threads=args.proxy_threads,
        proxy_reset_interval=args.proxy_reset_interval,
        headless=args.headless,
        headed=args.headed,
        unmute=args.unmute,
        keyword=args.keyword,
        channel=args.channel
    )

    controller = BotController(config=config, telemetry_queue=telemetry_q)

    initial_cfg = config.to_dict()
    initial_cfg["web_host"] = args.web_host
    initial_cfg["web_port"] = args.web_port

    state_mgr, server_thread = start_web_dashboard(
        telemetry_q,
        host=args.web_host,
        port=args.web_port,
        initial_config=initial_cfg,
        bot_controller=controller
    )

    print("\n" + "=" * 67)
    print("    AUTVIEW WEB TELEMETRY MONITOR & STUDIO ENGINE (CLI RUNNER)")
    print("=" * 67)
    print(f"Keyword Pencarian : '{args.keyword}'")
    print(f"Target Channel    : @{args.channel}")
    print(f"Proses Terisolasi : {args.workers} Worker")
    print(f"Target Selesai    : {'Tak Terbatas (Infinite)' if args.infinite else f'{args.batch_count} Views'}")
    print(f"Mode Browser      : {'Tampilan Layar (Headed)' if args.headed else 'Headless (Ultra Ringan)'}")
    print(f"Web Dashboard     : http://{args.web_host}:{args.web_port}")
    print("=" * 67 + "\n")

    # Start bot execution
    controller.start()

    try:
        while True:
            time.sleep(1)
            if not controller.is_running() and not args.infinite:
                break
    except KeyboardInterrupt:
        print("\n[MAIN] Menerima sinyal Ctrl+C, menghentikan bot...")
        controller.stop()
    finally:
        controller.stop()
        telemetry_q.put({"event": "SHUTDOWN"})
        time.sleep(0.5)
        print("\nSesi Web Dashboard CLI selesai.")

if __name__ == "__main__":
    main()
