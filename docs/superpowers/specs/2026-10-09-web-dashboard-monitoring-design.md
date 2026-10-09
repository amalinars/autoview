# Design Specification: AutoView Real-Time Web Dashboard

- **Date:** 2026-10-09
- **Author:** Antigravity & User
- **Status:** Approved
- **Topic:** Real-Time Web UI/UX for AutoView Multi-Worker Monitoring

---

## 1. Overview & Goals

AutoView currently runs isolated multi-worker processes via `youtube_search.py` and has a desktop Tkinter GUI (`gui_app.py`). To enable lightweight, cross-device, and modern browser-based observation, this feature adds a built-in real-time Web Dashboard.

The Web Dashboard is strictly **read-only monitoring** focusing on:
1. Real-time worker activity status (Worker ID, PID, Status badge, Active Proxy, Current Video, Watch Progress bar, Ads skipped).
2. Global session summary (Completed Views, Target Views, Active Proxies, Total Ads Skipped, Running Uptime).
3. Zero overhead / zero new dependency setup by leveraging already-installed `starlette` + `uvicorn` and Server-Sent Events (SSE).

---

## 2. Architecture & Data Flow

```
[Worker Process 1] ──┐
[Worker Process 2] ──┼──> [telemetry_queue] ──> [DashboardStateManager] (web_dashboard.py)
[Worker Process N] ──┘                                    │
                                                          ├──> REST /api/status (Snapshot)
                                                          └──> SSE /api/stream (Live Broadcast)
                                                                       │
                                                                       ▼
                                                          [Browser Web Dashboard (index.html)]
```

### 2.1 Backend (`web_dashboard.py`)
- **Server Framework:** `Starlette` running on `uvicorn` in a daemon background thread.
- **State Store (`DashboardStateManager`):**
  - Thread-safe in-memory store for:
    - Global session stats (`total_views`, `target_views`, `active_workers`, `total_ads_skipped`, `active_proxies`, `start_time`, `is_infinite`).
    - Per-worker dictionary keyed by `worker_id` (1..N):
      - `worker_id`: int
      - `pid`: int
      - `status`: str (`SIAP`, `NAVIGASI`, `MENONTON`, `COOLDOWN`, `SUKSES`, `ERROR`, `BERHENTI`)
      - `status_color`: str (css color or semantic badge type)
      - `details`: str
      - `video_title`: str
      - `video_url`: str
      - `proxy`: str
      - `elapsed_sec`: float
      - `duration_sec`: float
      - `percent`: float (0.0 to 100.0)
      - `ads_skipped`: int
- **Queue Consumer Thread:**
  - Non-blocking loop draining `telemetry_queue.get_nowait()` every 50ms.
  - Updates `DashboardStateManager`.
  - Dispatches updates to connected SSE clients.
- **Endpoints:**
  - `GET /`: Serves the modern HTML5 dashboard (`web/index.html` or internal template).
  - `GET /api/status`: Returns full JSON state snapshot.
  - `GET /api/stream`: Server-Sent Events (`text/event-stream`) pushing event updates:
    - `worker_update`: individual worker state changes or progress tick.
    - `summary_update`: global metrics update.
    - `ping`: heartbeat every 15s to maintain active connection.
  - Optional: `GET /api/health`: Healthcheck endpoint returning `{"status": "ok"}`.

### 2.2 Integration with Runner (`youtube_search.py`)
- CLI Arguments:
  - `--web`: Enable the Web Dashboard (enabled by default or via flag).
  - `--web-port`: Port number to serve on (default: `5000`).
  - `--web-host`: Host to bind (default: `0.0.0.0` or `127.0.0.1`).
- Execution Flow:
  1. If `--web` is active:
     - Instantiate `multiprocessing.Queue()` as `telemetry_queue`.
     - Initialize and start `start_web_dashboard(telemetry_queue, host, port, config)`.
     - Print user-friendly banner with link `http://localhost:<port>`.
     - Pass `telemetry_queue` into each `worker_process_main(..., telemetry_queue=telemetry_queue)`.
  2. Workers emit existing telemetry events (`init`, `proxy_selected`, `video_chosen`, `watch_start`, `watch_progress`, `ad_status`, `watch_complete`, `status_change`).

---

## 3. Frontend UI/UX Design

### 3.1 Design Aesthetic
- **Theme:** Modern Dark Mode / Cyber Glassmorphism.
  - Background: Deep slate/dark `#0b0f19` with subtle gradient and mesh accent.
  - Card background: `#131b2e` / `rgba(19, 27, 46, 0.85)` with backdrop blur and border `#1e293b`.
  - Accent colors:
    - `SIAP`: Blue `#38bdf8`
    - `NAVIGASI`: Amber/Orange `#fbbf24`
    - `MENONTON`: Emerald Green `#34d399`
    - `COOLDOWN`: Purple `#a78bfa`
    - `SUKSES`: Green `#4ade80`
    - `ERROR`: Rose/Red `#f87171`
- **Typography:** Modern clean sans-serif (`Inter`, `system-ui`, `-apple-system`, `sans-serif`).

### 3.2 Key Components
1. **Header Bar:**
   - App Logo & Title: `AutoView Monitor`
   - Connection Status Pill: Pulsing green dot with `LIVE` or amber `RECONNECTING`.
   - Global Stats Summary Grid:
     - Views Selesai (e.g. `12 / 50` or `12 / ∞`)
     - Ads Terlewati (e.g. `8 ads`)
     - Worker Aktif (e.g. `3 worker`)
     - Waktu Berjalan (Elapsed timer: `00:14:32`)
2. **Worker Cards Grid:**
   - Auto-responsive CSS Grid (`grid-template-columns: repeat(auto-fill, minmax(320px, 1fr))`).
   - Card structure:
     - **Card Top:** Worker ID badge (`#01`), Process PID badge (`PID 18420`), Status Pill with glow effect.
     - **Video Section:** YouTube icon + Video title (truncated with tooltip on hover) + status details.
     - **Progress Bar:** Custom smooth rounded bar showing percentage, elapsed time (`01:23 / 03:45`), and percent badge (`36.8%`).
     - **Card Footer:** Active proxy badge (IP/Port or Direct) and Ads count badge.
3. **Resilience & Offline Handling:**
   - Native JavaScript `EventSource` with auto-reconnection.
   - If SSE drops, fallback to periodic fetch of `/api/status` until stream re-establishes.

---

## 4. Verification & Testing Strategy

1. **Unit / Component Test:**
   - Start `web_dashboard.py` in standalone test mode with dummy mock telemetry events.
   - Verify `/api/status` returns expected JSON structure.
   - Verify `/api/stream` yields valid SSE messages (`event: worker_update`, `data: {...}`).
2. **End-to-End Integration Test:**
   - Run `python youtube_search.py --web --workers 2 --batch 2 --keyword "test" ...` in dry/test or headless mode.
   - Access `http://localhost:5000` via curl / browser check to verify worker card updates dynamically.
   - Test graceful shutdown on `Ctrl+C` (clean exit of uvicorn thread and workers).
