import os
import sys
import time
import json
import asyncio
import threading
import collections
from typing import Dict, Any, Optional
from starlette.applications import Starlette
from starlette.responses import JSONResponse, HTMLResponse, StreamingResponse
from starlette.routing import Route
import uvicorn

def format_elapsed(seconds: float) -> str:
    s = int(seconds)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

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
    def __init__(self, initial_config: Optional[Dict[str, Any]] = None):
        cfg = initial_config or {}
        self.start_time = time.time()
        self.proxy_file = cfg.get("proxy_file")
        self.direct = cfg.get("direct", False)
        
        initial_proxies = cfg.get("proxy_count", 0)
        if not self.direct and initial_proxies == 0:
            initial_proxies = get_live_proxy_count(self.proxy_file)

        self.summary = {
            "batch_target": cfg.get("batch_count", 0),
            "is_infinite": cfg.get("infinite", False),
            "keyword": cfg.get("keyword", ""),
            "channel": cfg.get("channel", ""),
            "total_views": 0,
            "total_ads_skipped": 0,
            "active_workers": cfg.get("workers", 0),
            "active_proxies": initial_proxies,
            "uptime_seconds": 0,
            "uptime_str": "00:00",
            "bot_status": "RUNNING",
        }
        self.workers: Dict[int, Dict[str, Any]] = {}
        self.logs = collections.deque(maxlen=300)
        self._listeners: list[asyncio.Queue] = []
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def get_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            self.summary["uptime_seconds"] = int(time.time() - self.start_time)
            self.summary["uptime_str"] = format_elapsed(self.summary["uptime_seconds"])
            if not self.direct:
                live_p = get_live_proxy_count(self.proxy_file)
                if live_p > 0 or self.summary["active_proxies"] == 0:
                    self.summary["active_proxies"] = live_p
            return {
                "summary": dict(self.summary),
                "workers": {w_id: dict(data) for w_id, data in self.workers.items()},
                "logs": list(self.logs)
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

        # 1. Log event: simpan di circular buffer dan broadcast ke stream web
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
            # 2. Proxy count update
            if event == "proxy_count_update":
                self.summary["active_proxies"] = data.get("count", self.summary["active_proxies"])

            # 3. Worker telemetry events
            if isinstance(worker_id, int):
                if worker_id not in self.workers:
                    self.workers[worker_id] = {
                        "worker_id": worker_id,
                        "pid": 0,
                        "status": "INIT",
                        "status_color": "blue",
                        "details": "",
                        "video_title": "",
                        "video_url": "",
                        "proxy": "Direct",
                        "elapsed_sec": 0.0,
                        "duration_sec": 0.0,
                        "percent": 0.0,
                        "ads_skipped": 0,
                    }
                w = self.workers[worker_id]

                if event == "init":
                    w["pid"] = data.get("pid", 0)
                    w["status"] = data.get("status", "SIAP")
                    w["details"] = data.get("details", "")
                    w["status_color"] = "blue"

                elif event == "proxy_selected":
                    w["proxy"] = data.get("proxy", "Direct")
                    w["status"] = "PROXY_TERPILIH"
                    w["details"] = data.get("details", "")
                    w["status_color"] = "blue"

                elif event == "status_change":
                    w["status"] = data.get("status", w["status"])
                    w["details"] = data.get("details", "")
                    st = w["status"]
                    if st in ("NAVIGASI", "MASUK_CHANNEL", "MENCARI"): w["status_color"] = "amber"
                    elif st == "MENONTON": w["status_color"] = "green"
                    elif st in ("COOLDOWN", "JEDA"): w["status_color"] = "purple"
                    elif st in ("ERROR", "GAGAL", "TIMEOUT", "ERROR_KONEKSI", "BOT_BLOCKED"): w["status_color"] = "red"
                    elif st == "SUKSES": w["status_color"] = "green"

                elif event == "video_chosen":
                    w["video_title"] = data.get("title", "")
                    w["video_url"] = data.get("url", "")
                    w["status"] = data.get("status", "NAVIGASI")
                    w["status_color"] = "amber"
                    w["details"] = data.get("details", "")

                elif event == "watch_start":
                    w["video_title"] = data.get("title", w["video_title"])
                    w["status"] = "MENONTON"
                    w["status_color"] = "green"
                    w["elapsed_sec"] = 0.0
                    w["duration_sec"] = 0.0
                    w["percent"] = 0.0

                elif event == "watch_progress":
                    elapsed_sec = float(data.get("elapsed", 0.0))
                    target_sec = float(data.get("target", 0.0)) or float(data.get("duration", 0.0))
                    w["elapsed_sec"] = elapsed_sec
                    w["duration_sec"] = target_sec
                    if target_sec > 0:
                        w["percent"] = round(min(100.0, (elapsed_sec / target_sec) * 100.0), 1)
                    else:
                        w["percent"] = round(float(data.get("percent", 0.0)), 1)
                    w["status"] = data.get("status", "MENONTON")
                    w["status_color"] = "green"
                    w["details"] = data.get("details", "")
                    if "skippable_ads" in data:
                        w["ads_skipped"] = int(data.get("skippable_ads", 0))
                        total_ads = sum(wk["ads_skipped"] for wk in self.workers.values())
                        self.summary["total_ads_skipped"] = total_ads

                elif event == "ad_status":
                    w["ads_skipped"] = int(data.get("skippable", 0))
                    total_ads = sum(wk["ads_skipped"] for wk in self.workers.values())
                    self.summary["total_ads_skipped"] = total_ads

                elif event in ("watch_complete", "session_success"):
                    w["status"] = "SUKSES"
                    w["status_color"] = "green"
                    w["details"] = data.get("details", "Selesai")
                    if "total_success" in data:
                        self.summary["total_views"] = data["total_success"]
                    else:
                        self.summary["total_views"] += 1

                elif event == "session_failed":
                    w["status"] = data.get("status", "GAGAL")
                    w["status_color"] = "red"
                    w["details"] = data.get("details", "Gagal")

        # Broadcast snapshot update to web clients
        self._broadcast("update", self.get_snapshot())

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

    async def refresh_proxies(request):
        try:
            from geonode_fetcher import trigger_refill
            trigger_refill()
            return JSONResponse({"status": "ok", "message": "Auto-refill proxy dipicu di background."})
        except Exception as e:
            return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

    async def sse_stream(request):
        loop = asyncio.get_running_loop()
        state_manager.set_loop(loop)
        q = state_manager.subscribe()

        async def event_generator():
            try:
                # Send initial snapshot immediately on connect
                init_msg = f"event: init\ndata: {json.dumps(state_manager.get_snapshot())}\n\n"
                yield init_msg.encode("utf-8")
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=2.0)
                        yield msg.encode("utf-8")
                    except asyncio.TimeoutError:
                        # Heartbeat ping
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
        Route("/api/refresh-proxies", endpoint=refresh_proxies, methods=["POST", "GET"]),
        Route("/api/stream", endpoint=sse_stream, methods=["GET"]),
    ]
    return Starlette(routes=routes)

def start_web_dashboard(telemetry_queue, host: str = "0.0.0.0", port: int = 5000, initial_config: Optional[Dict[str, Any]] = None) -> tuple[DashboardStateManager, threading.Thread]:
    state_manager = DashboardStateManager(initial_config)

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
