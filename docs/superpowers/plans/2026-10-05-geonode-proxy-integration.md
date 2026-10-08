# Geonode Proxy Integration & Auto-Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automate proxy ingestion from Geonode API and concurrent TLS handshake validation directly inside `youtube_search.py` prior to launching worker bots.

**Architecture:** A lightweight module `geonode_fetcher.py` fetches 500 fast proxies from Geonode API with a browser User-Agent, then tests them concurrently against `www.youtube.com:443` using the existing TLS handshake engine from `yt_proxy_checker.py`. Verified proxies are saved to `working_yt_proxies.json` / `working_yt_proxies.txt` and immediately passed to `youtube_search.py` workers.

**Tech Stack:** Python 3.12, `urllib.request`, `concurrent.futures.ThreadPoolExecutor`, `socket`, `ssl`, Playwright.

## Global Constraints
- Target URL: `https://proxylist.geonode.com/api/proxy-list?page=1&limit=500&sort_by=responseTime&sort_type=asc`
- Always include `User-Agent: Mozilla/5.0...` to prevent HTTP 403 Forbidden.
- Graceful fallback: If Geonode API fails or offline, fall back to existing `working_yt_proxies.json` / `working_yt_proxies.txt`.
- No disruption to existing Playwright watcher or isolated worker logic.

---

### Task 1: Build Geonode API Fetcher (`geonode_fetcher.py`)

**Files:**
- Create: `geonode_fetcher.py`
- Test: Manual execution verification script `test_fetcher.py`

**Interfaces:**
- Produces: `fetch_geonode_proxies(limit: int = 500, timeout: float = 10.0) -> list[str]`

- [ ] **Step 1: Write test script to verify fetcher output**
Create `test_fetcher.py`:
```python
from geonode_fetcher import fetch_geonode_proxies

proxies = fetch_geonode_proxies(limit=10)
print(f"Fetched {len(proxies)} proxies.")
assert len(proxies) > 0, "No proxies fetched!"
for p in proxies:
    assert "://" in p, f"Invalid proxy format: {p}"
print("Task 1 verification passed.")
```

- [ ] **Step 2: Implement `geonode_fetcher.py`**
Implement `fetch_geonode_proxies`:
```python
import json
import urllib.request
import urllib.error

GEONODE_API_URL = "https://proxylist.geonode.com/api/proxy-list?page=1&limit={limit}&sort_by=responseTime&sort_type=asc"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"

def fetch_geonode_proxies(limit: int = 500, timeout: float = 10.0) -> list[str]:
    url = GEONODE_API_URL.format(limit=limit)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            items = data.get("data", [])
            proxy_urls = []
            for item in items:
                ip = item.get("ip")
                port = item.get("port")
                protocols = item.get("protocols", ["http"])
                if not ip or not port:
                    continue
                proto = protocols[0].lower() if protocols else "http"
                if proto not in ("socks4", "socks5", "http", "https"):
                    proto = "http"
                proxy_urls.append(f"{proto}://{ip}:{port}")
            return proxy_urls
    except Exception as e:
        print(f"[WARN] Gagal fetch dari Geonode API: {e}")
        return []
```

- [ ] **Step 3: Run verification test**
Run: `python3 test_fetcher.py`
Expected: "Task 1 verification passed."

---

### Task 2: Implement Concurrent YouTube Proxy Verification in `geonode_fetcher.py`

**Files:**
- Modify: `geonode_fetcher.py`
- Consumes: `check_proxy`, `parse_proxy` from `yt_proxy_checker.py`
- Produces: `get_and_verify_proxies(limit: int = 500, threads: int = 80, timeout: float = 4.0) -> list[str]`

- [ ] **Step 1: Add verification and saving logic to `geonode_fetcher.py`**
Add `get_and_verify_proxies`:
```python
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from yt_proxy_checker import check_proxy, parse_proxy

def get_and_verify_proxies(limit: int = 500, threads: int = 80, timeout: float = 4.0, output_json: str = "working_yt_proxies.json") -> list[str]:
    print(f"\n[GEONODE] Mengambil {limit} proxy dari Geonode API...")
    raw_list = fetch_geonode_proxies(limit=limit)
    if not raw_list:
        print("[GEONODE] Tidak dapat mengambil proxy baru dari API. Menggunakan cadangan lokal...")
        return []

    print(f"[GEONODE] Berhasil mengambil {len(raw_list)} proxy. Mulai verifikasi kilat ke YouTube (443)...")
    parsed_list = []
    for r in raw_list:
        p = parse_proxy(r)
        if p:
            parsed_list.append(p)

    connect_to = 2.0
    handshake_to = 2.5
    working = []
    
    with ThreadPoolExecutor(max_workers=threads) as executor:
        future_map = {executor.submit(check_proxy, p, connect_to, handshake_to): p for p in parsed_list}
        for future in as_completed(future_map):
            res = future.result()
            if res.get("alive"):
                working.append(res)
                print(f"  ✔ ALIVE: {res['proxy']} ({res['protocol'].upper()}) - {res['latency_ms']}ms")

    if working:
        working.sort(key=lambda x: x["latency_ms"])
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(working, f, indent=2)
        txt_path = output_json.replace(".json", ".txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            for item in working:
                f.write(item["proxy"] + "\n")
        print(f"[GEONODE] Total {len(working)} proxy aktif disimpan ke '{output_json}' dan '{txt_path}'.\n")
        return [item["proxy"] for item in working]
    else:
        print("[GEONODE] Tidak ada proxy aktif yang lolos verifikasi dari batch ini.")
        return []
```

- [ ] **Step 2: Test with small limit to verify end-to-end proxy verification**
Run quick test: `python3 -c "from geonode_fetcher import get_and_verify_proxies; res = get_and_verify_proxies(limit=20, threads=20); print('Alive count:', len(res))"`
Expected: Output shows testing and results.

---

### Task 3: Integrate Pipeline into `youtube_search.py`

**Files:**
- Modify: `youtube_search.py`
- Consumes: `get_and_verify_proxies` from `geonode_fetcher`

- [ ] **Step 1: Add `--skip-fetch` and `--proxy-limit` CLI arguments to `main()` in `youtube_search.py`**
```python
    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Lewati download dan pengecekan proxy Geonode (gunakan cache file yang ada)"
    )
    parser.add_argument(
        "--proxy-limit",
        type=int,
        default=500,
        help="Jumlah proxy yang diambil dari Geonode API (default: 500)"
    )
```

- [ ] **Step 2: Update proxy initialization in `main()` of `youtube_search.py`**
In `main()`:
```python
    if args.direct:
        proxy_list = []
    elif args.skip_fetch:
        safe_log("INIT", "Mode --skip-fetch aktif, memuat proxy dari cache lokal...", YELLOW)
        proxy_list = load_proxies(args.proxy_file)
    else:
        try:
            from geonode_fetcher import get_and_verify_proxies
            fresh_proxies = get_and_verify_proxies(limit=args.proxy_limit)
            if fresh_proxies:
                proxy_list = fresh_proxies
            else:
                safe_log("INIT", "Tidak ada proxy aktif baru dari Geonode, fallback ke cache lokal...", YELLOW)
                proxy_list = load_proxies(args.proxy_file)
        except Exception as e:
            safe_log("INIT", f"Gagal auto-fetch Geonode ({e}), fallback ke cache lokal...", YELLOW)
            proxy_list = load_proxies(args.proxy_file)
```

---

### Task 4: End-to-End Verification

**Files:**
- Test: Run `youtube_search.py --help`
- Test: Run `youtube_search.py` with `--batch 1 --workers 1 --max-cap 5` to verify execution flow.
- Cleanup: Remove temporary test files (`test_fetcher.py`).
