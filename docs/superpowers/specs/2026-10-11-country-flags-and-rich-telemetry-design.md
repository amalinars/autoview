# Design Spec: Country Flag Icons & Rich Video Telemetry for Web UI and Desktop GUI

**Date:** 2026-10-11  
**Status:** Approved  
**Author:** Antigravity Pairing Assistant & Jovan  

---

## 1. Executive Summary & Goals

The user requested adding country flag icons and comprehensive video/session telemetry to both the **Desktop Native GUI** (`gui_app.py`) and the **Web Dashboard UI** (`web/index.html`).

### Goals
1. **Country Flag Icons for Proxies**:
   - Display national flag emoji + ISO country code in proxy pills across both Web UI and GUI (e.g., `🇮🇩 ID • SOCKS5: 147.139.133.251:10080` or `🌐 Direct`).
   - Automatically resolve and cache proxy IP country codes.
2. **Comprehensive Video Information**:
   - Full Video Title (`🎬 Judul Video`).
   - Direct Video URL with clickable link in Web UI (`🔗 Buka Video di YouTube`).
   - Channel Name badge (`🎯 @horrornologi`).
   - Real-time video player position vs total duration (`▶ Putar: 01:23 / 05:40`).
   - Target watch duration & percent (`🎯 Target: 02:45 (50.0%)`).
3. **Stealth Device Persona Telemetry**:
   - Display each worker's active OS, Browser, and Resolution fingerprint (e.g., `🎭 Windows • Chrome 129 • 1920x1080`).

---

## 2. Architecture & Data Flow

```
[ Worker OS Process ] (youtube_search.py)
   │
   ├─ Resolves Proxy IP Country (Geo Resolver Cache / api.country.is)
   ├─ Generates Unique Stealth Fingerprint (Platform, Browser, GPU, Screen)
   ├─ Extracts Video Details (Title, URL, Target Channel, Duration, Player Position)
   │
   ▼
[ Multiprocessing Telemetry Queue ]
   │
   ├─ GUI Consumer (gui_app.py: _poll_telemetry)
   │    └─ Updates WorkerCard widgets (Proxy with Flag, Video URL, Channel, Persona, Player Pos)
   │
   └─ Web Dashboard Consumer (web_dashboard.py: queue_listener)
        └─ Broadcasts via SSE (/api/stream) to Web UI (web/index.html)
             └─ Renders Dynamic Cards (Clickable Links, Flag Badges, Persona Pills)
```

---

## 3. Detailed Component Designs

### 3.1 Proxy Geo-Location & Flag Helper
- **Module**: `bot_controller.py` (and re-exported for `youtube_search.py`, `gui_app.py`, `web_dashboard.py`).
- **Function**: `country_code_to_flag(code: str) -> str`:
  - Formula: `chr(ord(c) + 127397)` for two-letter ISO 3166-1 alpha-2 codes.
  - Fallback: `"🌐"` for Direct or unknown IPs.
- **Function**: `resolve_proxy_country(proxy_url: str) -> tuple[str, str]`:
  - Extracts host IP from proxy string.
  - Checks memory cache `_GEO_CACHE`.
  - If not cached and not private IP, queries `https://api.country.is/{ip}` (timeout 1.5s).
  - Persists to `.proxy_geo_cache.json`.
  - Returns `(country_code, flag_emoji)` (e.g. `("ID", "🇮🇩")`).

### 3.2 Telemetry Event Schema Enhancements
1. **`proxy_selected`**:
   - `country`: e.g. `"ID"`
   - `country_flag`: e.g. `"🇮🇩"`
   - `proxy_display`: e.g. `"🇮🇩 ID • SOCKS5: 147.139.133.251:10080"`
   - `persona`: e.g. `"Windows • Chrome 128 • 1920x1080"`
2. **`video_chosen` / `watch_start`**:
   - `title`: Video title string
   - `url`: Direct YouTube watch URL (`https://www.youtube.com/watch?v=...`)
   - `channel`: Target YouTube channel name
   - `total_duration`: Float seconds
   - `target_duration`: Float seconds
   - `target_percent`: Float percentage
3. **`watch_progress`**:
   - `current_play_time`: Float player position seconds
   - `total_duration`: Float total video seconds
   - `elapsed`: Float watch timer seconds
   - `target`: Float target watch seconds
   - `video_url`: Current active page URL
   - `channel`: Channel name

### 3.3 Desktop GUI (`gui_app.py`) Updates
- **WorkerCard Layout**:
  - **Header**:
    - Worker ID: `Worker #01`
    - PID: `PID: 1234`
    - Mode Pill: `👁️ HEADED` / `⚡ HEADLESS`
    - Status Badge: `🟢 MENONTON (LOCKED)`
    - Proxy Pill: `🇮🇩 ID • SOCKS5: 147.139.133.251:10080` (with flag emoji)
  - **Video Info Row**:
    - Title: `🎬 Judul Video`
    - Meta sub-row: `🎯 Channel: @horrornologi` | `🔗 URL: youtu.be/...`
  - **Persona Row**:
    - `🎭 Persona: Windows • Chrome 128 • 1920x1080`
  - **Progress & Playback Row**:
    - Progress Bar + `▶ Play: 01:20 / 05:30 • Watch: 01:20 / 02:45 (48.5%)`
    - Ads badge: `🛡️ X Iklan Di-skip`

### 3.4 Web UI (`web/index.html` & `web_dashboard.py`) Updates
- **WorkerCard Layout**:
  - **Header**: `#01`, `PID`, `Mode`, `Status`
  - **Video Box**:
    - Header with Channel badge (`🎯 @horrornologi`) and clickable YouTube link (`<a href="..." target="_blank">🔗 Buka di YouTube</a>`).
    - Title: `🎬 Judul Video`.
    - Live player position indicator (`▶ Putar: MM:SS / MM:SS`).
  - **Persona Pill**:
    - Subtle badge: `🎭 Windows • Chrome 128 • 1920x1080`.
  - **Progress Section**:
    - Timer: `01:20 / 02:45 (48.5%)`.
    - Dynamic color-changing smooth progress bar.
  - **Footer**:
    - Proxy Pill: `🇮🇩 ID • SOCKS5: ip:port` (with flag emoji and hover tooltip).
    - Ads badge: `🛡️ X Iklan Di-skip`.

---

## 4. Verification & Testing

1. **Unit Tests**:
   - Test `country_code_to_flag` with multiple country codes ("ID", "US", "DE", "SG", "JP").
   - Test telemetry processing in `DashboardStateManager` ensuring `country`, `country_flag`, `video_url`, `channel`, `persona` are preserved and broadcasted.
2. **Integration Verification**:
   - Run `python3 -m unittest discover tests` ensuring all tests pass.
   - Verify GUI and Web Dashboard endpoints.
