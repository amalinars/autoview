# AutoView Web Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a lightweight, real-time modern dark mode Web Dashboard for monitoring AutoView multi-worker processes, live watch progress, active proxies, and global batch metrics via Starlette and Server-Sent Events (SSE).

**Architecture:** A lightweight Starlette app running on Uvicorn inside a background daemon thread consumes multiprocessing telemetry events from worker processes, maintains an in-memory state snapshot, and broadcasts state updates to the browser via SSE. A single-page HTML5/CSS/JS frontend renders responsive worker cards and summary statistics with auto-reconnection.

**Tech Stack:** Python 3.12, Starlette, Uvicorn, Server-Sent Events (SSE), Vanilla HTML5/CSS3/JavaScript (Modern Dark Mode).

## Global Constraints

- No external npm/node build steps; frontend must be served directly as a standalone single-file asset.
- Zero new package dependencies; utilize pre-installed `starlette`, `uvicorn`, and standard Python libraries.
- Read-only telemetry: workers push events into `telemetry_queue` without altering worker execution logic or adding latency.
- Tests must execute cleanly via `python3 -m unittest`.

---

### Task 1: Core State Management & Starlette Backend (`web_dashboard.py`)

**Files:**
- Create: `web_dashboard.py`
- Create: `tests/test_web_dashboard.py`

**Interfaces:**
- Produces:
  - `class DashboardStateManager`:
    - `process_event(data: dict) -> None`
    - `get_snapshot() -> dict`
    - `subscribe() -> asyncio.Queue`
    - `unsubscribe(queue: asyncio.Queue) -> None`
  - `def create_app(state_manager: DashboardStateManager, static_html_path: str = None) -> Starlette`
  - `def start_web_dashboard(telemetry_queue, host: str = "127.0.0.1", port: int = 5000, initial_config: dict = None) -> threading.Thread`

- [ ] **Step 1: Write failing tests for DashboardStateManager and Starlette API**

Create `tests/test_web_dashboard.py`:
```python
import unittest
import json
from starlette.testclient import TestClient

class TestWebDashboard(unittest.TestCase):
    def setUp(self):
        from web_dashboard import DashboardStateManager, create_app
        self.state = DashboardStateManager(initial_config={"batch_count": 50, "workers": 2, "keyword": "test"})
        self.app = create_app(self.state)
        self.client = TestClient(self.app)

    def test_initial_snapshot(self):
        snapshot = self.state.get_snapshot()
        self.assertIn("summary", snapshot)
        self.assertIn("workers", snapshot)
        self.assertEqual(snapshot["summary"]["batch_target"], 50)
        self.assertEqual(snapshot["summary"]["total_views"], 0)

    def test_process_worker_lifecycle_events(self):
        # 1. Init event
        self.state.process_event({
            "worker_id": 1,
            "event": "init",
            "pid": 12345,
            "status": "SIAP",
            "details": "PID: 12345"
        })
        snap = self.state.get_snapshot()
        self.assertIn(1, snap["workers"])
        self.assertEqual(snap["workers"][1]["pid"], 12345)
        self.assertEqual(snap["workers"][1]["status"], "SIAP")

        # 2. Proxy selected
        self.state.process_event({
            "worker_id": 1,
            "event": "proxy_selected",
            "proxy": "http://1.2.3.4:8080",
            "details": "Testing proxy"
        })
        snap = self.state.get_snapshot()
        self.assertEqual(snap["workers"][1]["proxy"], "http://1.2.3.4:8080")

        # 3. Watch progress
        self.state.process_event({
            "worker_id": 1,
            "event": "watch_progress",
            "elapsed": 45.0,
            "duration": 100.0,
            "percent": 45.0,
            "details": "00:45 (45.0%)"
        })
        snap = self.state.get_snapshot()
        self.assertEqual(snap["workers"][1]["elapsed_sec"], 45.0)
        self.assertEqual(snap["workers"][1]["duration_sec"], 100.0)
        self.assertEqual(snap["workers"][1]["percent"], 45.0)

        # 4. Watch complete
        self.state.process_event({
            "worker_id": 1,
            "event": "watch_complete",
            "status": "SUKSES",
            "details": "Target selesai"
        })
        snap = self.state.get_snapshot()
        self.assertEqual(snap["workers"][1]["status"], "SUKSES")
        self.assertEqual(snap["summary"]["total_views"], 1)

    def test_api_status_endpoint(self):
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("summary", data)
        self.assertIn("workers", data)

    def test_api_health_endpoint(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests/test_web_dashboard.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'web_dashboard'`

- [ ] **Step 3: Implement DashboardStateManager and Starlette backend in `web_dashboard.py`**

Create `web_dashboard.py`:
```python
import os
import sys
import time
import json
import asyncio
import threading
from typing import Dict, Any, Optional
from starlette.applications import Starlette
from starlette.responses import JSONResponse, HTMLResponse, Response
from starlette.routing import Route
import uvicorn

def format_elapsed(seconds: float) -> str:
    s = int(seconds)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

class DashboardStateManager:
    def __init__(self, initial_config: Optional[Dict[str, Any]] = None):
        cfg = initial_config or {}
        self.start_time = time.time()
        self.summary = {
            "batch_target": cfg.get("batch_count", 0),
            "is_infinite": cfg.get("infinite", False),
            "keyword": cfg.get("keyword", ""),
            "total_views": 0,
            "total_ads_skipped": 0,
            "active_workers": cfg.get("workers", 0),
            "active_proxies": cfg.get("proxy_count", 0),
            "uptime_seconds": 0,
            "uptime_str": "00:00",
        }
        self.workers: Dict[int, Dict[str, Any]] = {}
        self._listeners: list[asyncio.Queue] = []
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def get_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            self.summary["uptime_seconds"] = int(time.time() - self.start_time)
            self.summary["uptime_str"] = format_elapsed(self.summary["uptime_seconds"])
            return {
                "summary": dict(self.summary),
                "workers": {w_id: dict(data) for w_id, data in self.workers.items()}
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

        with self._lock:
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
                    if st == "NAVIGASI": w["status_color"] = "amber"
                    elif st == "MENONTON": w["status_color"] = "green"
                    elif st == "COOLDOWN": w["status_color"] = "purple"
                    elif st in ("ERROR", "GAGAL"): w["status_color"] = "red"

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
                    w["elapsed_sec"] = float(data.get("elapsed", 0.0))
                    w["duration_sec"] = float(data.get("duration", 0.0))
                    w["percent"] = round(float(data.get("percent", 0.0)), 1)
                    w["details"] = data.get("details", "")

                elif event == "ad_status":
                    w["ads_skipped"] = int(data.get("skippable", 0))
                    total_ads = sum(wk["ads_skipped"] for wk in self.workers.values())
                    self.summary["total_ads_skipped"] = total_ads

                elif event == "watch_complete":
                    w["status"] = "SUKSES"
                    w["status_color"] = "green"
                    w["details"] = data.get("details", "Selesai")
                    self.summary["total_views"] += 1

            if event == "proxy_count_update":
                self.summary["active_proxies"] = data.get("count", self.summary["active_proxies"])

        # Broadcast update to web clients
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
        return JSONResponse(state_manager.get_snapshot())

    async def health(request):
        return JSONResponse({"status": "ok"})

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
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=15.0)
                        yield msg.encode("utf-8")
                    except asyncio.TimeoutError:
                        # Heartbeat ping
                        yield b": ping\n\n"
            finally:
                state_manager.unsubscribe(q)

        return Response(
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
        Route("/api/stream", endpoint=sse_stream, methods=["GET"]),
    ]
    return Starlette(routes=routes)

def start_web_dashboard(telemetry_queue, host: str = "127.0.0.1", port: int = 5000, initial_config: Optional[Dict[str, Any]] = None) -> tuple[DashboardStateManager, threading.Thread]:
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests/test_web_dashboard.py`
Expected: PASS with 4 tests OK.

- [ ] **Step 5: Commit changes**

Run:
```bash
git add web_dashboard.py tests/test_web_dashboard.py
git commit -m "feat(web): add DashboardStateManager and Starlette SSE backend"
```

---

### Task 2: Modern Dark Mode Web UI Frontend (`web/index.html`)

**Files:**
- Create: `web/index.html`
- Test: `tests/test_web_dashboard.py` (Add test asserting `GET /` serves HTML containing dashboard elements)

**Interfaces:**
- Consumes:
  - `GET /api/status` for initial snapshot
  - `GET /api/stream` for live SSE events
- Produces:
  - Browser UI with dynamic header cards, live connection indicator, and reactive worker cards.

- [ ] **Step 1: Write test verifying `GET /` returns HTML dashboard template**

Add test method to `tests/test_web_dashboard.py`:
```python
    def test_index_page_serves_html(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("AutoView", response.text)
        self.assertIn("worker-grid", response.text)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests/test_web_dashboard.py`
Expected: FAIL (because `web/index.html` does not exist yet)

- [ ] **Step 3: Implement `web/index.html`**

Create `web/index.html` with:
- Glassmorphism dark aesthetic: CSS variables for theme colors (`--bg-primary: #0b0f19`, `--bg-card: #131b2e`, `--accent-blue: #38bdf8`, `--accent-green: #34d399`, `--accent-amber: #fbbf24`, `--accent-purple: #a78bfa`, `--accent-red: #f87171`).
- Top Bar:
  - Logo: `AutoView Monitor`
  - Pulsing live indicator pill: `● LIVE TELEMETRY` (green) / `● CONNECTING...` (yellow).
  - Summary Metrics Cards:
    - Target Views: `total_views / target`
    - Ads Skipped: `total_ads_skipped`
    - Workers: `active_workers`
    - Uptime: elapsed timer formatted `HH:MM:SS`
- Main Grid (`#worker-grid`):
  - Card per worker:
    - Worker Badge (#01, PID) + Status Pill (`SIAP`, `NAVIGASI`, `MENONTON`, `COOLDOWN`, `SUKSES`, `ERROR`)
    - Video Title (with YouTube icon & truncate)
    - Watch Progress Bar (with smooth transition, % badge, and elapsed/duration text)
    - Proxy Info Pill (with globe icon) + Ads skipped badge
- Embedded Vanilla JS:
  - Fetches `/api/status` on load.
  - Connects to `EventSource('/api/stream')`.
  - On event `init` or `update`: dynamically updates DOM (creates new worker cards if not yet in DOM, updates existing elements smoothly).
  - Handles reconnection gracefully with retry.

- [ ] **Step 4: Run tests to verify it passes**

Run: `python3 -m unittest tests/test_web_dashboard.py`
Expected: PASS with 5 tests OK.

- [ ] **Step 5: Commit changes**

Run:
```bash
git add web/index.html tests/test_web_dashboard.py
git commit -m "feat(web): create modern dark mode frontend dashboard"
```

---

### Task 3: Integrate Web Dashboard with `youtube_search.py` CLI Runner

**Files:**
- Modify: `youtube_search.py`
- Test: `tests/test_cli_web_integration.py`

**Interfaces:**
- Consumes:
  - `start_web_dashboard` from `web_dashboard.py`
  - `telemetry_queue` in `worker_process_main`
- Produces:
  - CLI flags: `--web`, `--web-port` (default 5000), `--web-host` (default "127.0.0.1")
  - Automatic background web dashboard launch when `--web` is enabled.

- [ ] **Step 1: Write integration test for CLI arguments and web initialization**

Create `tests/test_cli_web_integration.py`:
```python
import unittest
import multiprocessing
import time
from web_dashboard import start_web_dashboard
from starlette.testclient import TestClient

class TestCLIWebIntegration(unittest.TestCase):
    def test_web_dashboard_background_thread(self):
        q = multiprocessing.Queue()
        cfg = {"batch_count": 10, "workers": 2, "keyword": "integration test"}
        state, thread = start_web_dashboard(q, host="127.0.0.1", port=5099, initial_config=cfg)
        
        # Push mock event through queue
        q.put({
            "worker_id": 1,
            "event": "init",
            "pid": 9999,
            "status": "SIAP",
            "details": "Ready"
        })
        time.sleep(0.3)
        snap = state.get_snapshot()
        self.assertIn(1, snap["workers"])
        self.assertEqual(snap["workers"][1]["pid"], 9999)

        # Send shutdown event
        q.put({"event": "SHUTDOWN"})

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python3 -m unittest tests/test_cli_web_integration.py`
Expected: PASS

- [ ] **Step 3: Modify `youtube_search.py` to add `--web`, `--web-port`, `--web-host`**

In `youtube_search.py`:
1. In `main()`, add arguments to `parser`:
   ```python
   parser.add_argument(
       "--web",
       action="store_true",
       default=True,
       help="Aktifkan Web Dashboard real-time di browser (default: True)"
   )
   parser.add_argument(
       "--no-web",
       action="store_false",
       dest="web",
       help="Nonaktifkan Web Dashboard"
   )
   parser.add_argument(
       "--web-port",
       type=int,
       default=5000,
       help="Port lokal untuk Web Dashboard (default: 5000)"
   )
   parser.add_argument(
       "--web-host",
       type=str,
       default="127.0.0.1",
       help="Host untuk Web Dashboard (default: 127.0.0.1)"
   )
   ```
2. Before launching workers:
   ```python
   telemetry_q = None
   if args.web:
       try:
           from web_dashboard import start_web_dashboard
           telemetry_q = multiprocessing.Queue()
           web_cfg = {
               "batch_count": args.batch_count,
               "infinite": args.infinite,
               "keyword": args.keyword,
               "workers": args.workers,
               "proxy_count": len(proxy_list)
           }
           start_web_dashboard(telemetry_q, host=args.web_host, port=args.web_port, initial_config=web_cfg)
           print(f"{CYAN}🌐 Web Dashboard Aktif :{RESET} {BOLD}http://{args.web_host}:{args.web_port}{RESET}")
       except Exception as e:
           safe_log("WEB", f"Gagal memulai Web Dashboard: {e}", YELLOW)
           telemetry_q = None
   ```
3. Pass `telemetry_queue=telemetry_q` to `worker_process_main` inside `multiprocessing.Process`:
   ```python
   p = multiprocessing.Process(
       target=worker_process_main,
       args=(w_id, args, proxy_list, success_counter, stop_event, telemetry_q),
       daemon=True
   )
   ```
4. On shutdown in `finally:`, if `telemetry_q`:
   ```python
   try:
       telemetry_q.put({"event": "SHUTDOWN"})
   except Exception:
       pass
   ```

- [ ] **Step 4: Run all test suites**

Run: `python3 -m unittest discover tests`
Expected: ALL PASS.

- [ ] **Step 5: Commit changes**

Run:
```bash
git add youtube_search.py tests/test_cli_web_integration.py
git commit -m "feat(cli): integrate web dashboard into youtube_search runner"
```

---

### Task 4: End-to-End Verification

- [ ] **Step 1: Test CLI `--help` flags**
Run: `python3 youtube_search.py --help`
Verify `--web`, `--no-web`, `--web-port`, `--web-host` are listed.

- [ ] **Step 2: Test live browser dashboard via curl / headless sanity test**
Verify `GET http://127.0.0.1:5000/api/health` and `GET http://127.0.0.1:5000/api/status`.

- [ ] **Step 3: Final Git status check and cleanup**
Ensure no uncommitted artifacts or scratch files are left behind.
