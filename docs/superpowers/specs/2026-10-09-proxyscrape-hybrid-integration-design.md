# Design Specification: Dual-Source Proxy Ingestion (Geonode + ProxyScrape) & Fast YouTube Validator

**Date:** 2026-10-09  
**Topic:** ProxyScrape Mirror & Geonode Dual-Source Concurrent Proxy Ingestion  
**Target:** `geonode_fetcher.py`, `gui_app.py`, `youtube_search.py`

---

## 1. Objective

Integrate the official ProxyScrape free proxy list mirror (`https://github.com/proxyscrape/free-proxy-list`) alongside Geonode API to create a resilient, high-volume **Dual-Source Hybrid Proxy Ingestion** system for `autoview`. This ensures continuous proxy availability for multi-worker YouTube playback, eliminating downtime when Geonode experiences Cloudflare rate limits (HTTP 429 / 100 requests per hour).

---

## 2. Architecture & Components

```
                   ┌───────────────────────────────────────────┐
                   │   Dual-Source Proxy Ingestion Engine      │
                   └─────────────────────┬─────────────────────┘
                                         │
                 ┌───────────────────────┴───────────────────────┐
                 ▼                                               ▼
     ┌────────────────────────┐                     ┌────────────────────────┐
     │  Geonode API Client    │                     │ ProxyScrape Ingestion  │
     │  - Smart pagination    │                     │  - jsDelivr CDN JSON   │
     │  - Rate-limit aware    │                     │  - Failover v4 API     │
     └───────────┬────────────┘                     └───────────┬────────────┘
                 │                                               │
                 └───────────────────────┬───────────────────────┘
                                         ▼
                 ┌───────────────────────────────────────────────┐
                 │       Candidate Merge & Deduplication         │
                 │   - Remove duplicate (protocol, ip, port)     │
                 │   - Exclude .dead_proxies.json                │
                 └───────────────────────┬───────────────────────┘
                                         ▼
                 ┌───────────────────────────────────────────────┐
                 │    Fast YouTube TLS Handshake Validator       │
                 │   - ThreadPoolExecutor (60-80 workers)        │
                 │   - SOCKS5, SOCKS4, HTTP CONNECT to port 443  │
                 └───────────────────────┬───────────────────────┘
                                         ▼
                 ┌───────────────────────────────────────────────┐
                 │          Persisted Proxy Storage              │
                 │   - working_yt_proxies.json (sorted latency)  │
                 │   - working_yt_proxies.txt (plain URLs)       │
                 └───────────────────────────────────────────────┘
```

### 2.1 ProxyScrape Ingestion (`fetch_proxyscrape_proxies`)
- **Primary Source:** jsDelivr CDN JSON mirror:
  `https://cdn.jsdelivr.net/gh/proxyscrape/free-proxy-list@main/proxies/all/data.json`
- **Filtering & Enrichment:**
  - Protocols: `socks5`, `http`, `https`, `socks4`.
  - Geographic Targeting: High-CPM countries (`US, GB, DE, CA, FR, AU, JP, NL, IT, ES, SG, ...`).
  - Quality Threshold: Filters on `uptime_percent` and latency if available.
- **Failover / Fallback:** If CDN request fails or times out, fallback to ProxyScrape v4 Live API:
  `https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&proxy_format=protocolipport&format=text`

### 2.2 Concurrent Dual-Fetch Ingestion (`get_and_verify_proxies`)
- In `geonode_fetcher.py`:
  - Run Geonode fetch (`fetch_geonode_proxies`) and ProxyScrape fetch concurrently in parallel worker threads to avoid latency bottlenecks.
  - Merge candidates:
    $$\text{Candidates} = \text{Deduplicate}(\text{Geonode} \cup \text{ProxyScrape}) \setminus \text{DeadProxies}$$
  - Cap candidates to a reasonable batch size (e.g., 300–500 proxies) before validation.
  - If Geonode is in active cooldown (`geonode_cooldown_until > now`), ProxyScrape supplies 100% of the candidates without blocking.

### 2.3 YouTube 443 TLS Handshake Validator
- Reuses existing high-throughput socket handshakes:
  - SOCKS5 handshake + TLS handshake
  - SOCKS4 handshake + TLS handshake
  - HTTP CONNECT tunnel + TLS handshake
- Sorts verified alive proxies by response latency in ascending order.
- Writes to `working_yt_proxies.json` and `working_yt_proxies.txt`.

### 2.4 GUI & CLI Enhancements
- **GUI (`gui_app.py`):**
  - Radiobutton label: `Auto Hybrid (Geonode + ProxyScrape)` (replaces `Auto Geonode`).
  - Action button: `🔄 UPDATE PROXY (HYBRID)`.
  - Activity log: Logs detailed statistics from both sources (e.g. `[HYBRID] Fetched 150 Geonode, 250 ProxyScrape. 380 unique candidates`).
- **CLI (`youtube_search.py`):**
  - Updated help strings and log messages for initial fetch and auto-refill triggers.

---

## 3. Resilience & Edge Cases

1. **Geonode 429 / Cloudflare Block:** ProxyScrape seamlessly fulfills the proxy pool while Geonode waits out its cooldown timer.
2. **Network Disconnection / Both Sources Fail:** Gracefully falls back to existing cached `working_yt_proxies.json` and `.txt`.
3. **Dead Proxies Re-appearance:** Verified proxies that fail during video playback are marked via `mark_proxy_dead()` into `.dead_proxies.json` and pruned immediately.
4. **Auto-Refill Threshold:** When working pool drops below 15 proxies, `trigger_refill()` executes the dual-fetch pipeline in a background daemon thread.

---

## 4. Verification Plan

1. **Unit Testing ProxyScrape Ingestion:**
   - Execute standalone test fetching from jsDelivr CDN and fallback API v4.
   - Verify returned proxy URL formats (`protocol://ip:port`).
2. **Dual-Fetch Pipeline Testing:**
   - Execute `get_and_verify_proxies(limit=50)` and confirm both providers contribute candidates.
   - Confirm verified proxies write successfully to `working_yt_proxies.json` and `working_yt_proxies.txt`.
3. **GUI & CLI Dry-run:**
   - Verify GUI launches with updated radiobutton and update button without errors.
   - Run `python3 youtube_search.py --help` to confirm CLI parameters and descriptions are clean.
