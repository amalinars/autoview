# Design Specification: Geonode Proxy Integration & Auto-Validation in `youtube_search.py`

**Date:** 2026-10-05  
**Topic:** Geonode API Proxy Ingestion & Automated YouTube Validation  
**Target:** `youtube_search.py` & `yt_proxy_checker.py`

---

## 1. Objective
Enable `youtube_search.py` to automatically fetch up to 500 fast proxies from Geonode API (`https://proxylist.geonode.com/api/proxy-list?page=1&limit=500&sort_by=responseTime&sort_type=asc`), validate them concurrently against YouTube's HTTPS/TLS endpoint, cache the working proxies, and immediately launch worker processes to watch videos without manual proxy management.

---

## 2. Architecture & Components

### 2.1 Component Overview
1. **`fetch_geonode_proxies(limit=500, timeout=10) -> list[dict]`**:
   - Fetches proxy data via HTTP GET to `https://proxylist.geonode.com/api/proxy-list?page=1&limit={limit}&sort_by=responseTime&sort_type=asc`.
   - Headers: Includes standard browser `User-Agent` to bypass 403 Forbidden responses.
   - Extracts `ip`, `port`, and `protocols` (e.g. `socks5`, `socks4`, `http`).
   - Normalizes to proxy URLs: `socks5://{ip}:{port}`, `socks4://{ip}:{port}`, or `http://{ip}:{port}`.
   - Handles network errors gracefully (returns empty list on network/API failure).

2. **Fast Concurrent YouTube Validator**:
   - Reuses socket/TLS handshake validation logic from `yt_proxy_checker.py`:
     - SOCKS4: `test_socks4`
     - SOCKS5: `test_socks5`
     - HTTP/HTTPS: `test_http_connect`
   - Uses `ThreadPoolExecutor` with 60–80 threads, connection timeout ~2.0s and handshake timeout ~2.5s.
   - Returns sorted list of verified alive proxies with latency.

3. **Persistence & Cache**:
   - Saves verified alive proxies to:
     - `working_yt_proxies.json` (with metadata, protocol, latency)
     - `working_yt_proxies.txt` (plain URL per line)
   - Fallback mechanism: If Geonode API is unreachable, automatically fall back to existing local `working_yt_proxies.json` / `working_yt_proxies.txt`.

4. **Integration Point in `youtube_search.py`**:
   - CLI flags:
     - `--skip-fetch`: Skip fetching from Geonode and load local cache directly.
     - `--proxy-limit`: Number of proxies to request from Geonode (default: 500).
   - In `main()`:
     - If not `--direct`:
       - If not `--skip-fetch`: Run Geonode fetch & quick verification cycle.
       - Load alive proxies into worker proxy pool.
     - Start multi-worker browser tasks immediately.

---

## 3. Error Handling & Edge Cases
- **Geonode 403 / Rate Limit / Timeout:** Log warning and fallback to existing cached `working_yt_proxies.json`. If no cache exists, prompt or fall back to `--direct` mode if configured.
- **Zero Proxies Alive:** Warn user clearly and check if fallback local proxies exist before failing or switching to direct.
- **Ctrl+C during proxy checking:** Cleanly shut down thread pool and exit.

---

## 4. Verification Plan
- Unit/Smoke test: Fetch Geonode API and verify returned proxy format.
- Run proxy validator with a sample batch to confirm TLS handshakes against `www.youtube.com:443`.
- Execute `python3 youtube_search.py --batch 1 --workers 1` to verify end-to-end integration.
