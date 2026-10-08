#!/usr/bin/env python3
"""
Geonode Proxy Fetcher & YouTube Fast Validator (Smart Rotation & Pagination)
---------------------------------------------------------------------------
Rotates proxy pagination dynamically across pages (Items 1-250, 251-500, 501-750...),
fetches the freshest verified IPs (sort_by=lastChecked desc),
and seamlessly falls back to High-CPM mirrors if rate-limited.
"""

import os
import sys
import json
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from yt_proxy_checker import check_proxy, parse_proxy

# ANSI Colors
GREEN = "\033[92m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"

HIGH_CPM_COUNTRIES = ["US", "GB", "DE", "CA", "FR", "AU", "JP", "NL", "IT", "ES", "SG", "SE", "NO", "CH"]
TIER1_COUNTRIES = ["US", "GB", "DE", "CA", "FR", "AU"]
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUT_JSON = os.path.join(BASE_DIR, "working_yt_proxies.json")
DEAD_PROXIES_FILE = os.path.join(BASE_DIR, ".dead_proxies.json")
REFILL_TRIGGER_FILE = os.path.join(BASE_DIR, ".refill_trigger")
PAGINATION_STATE_FILE = os.path.join(BASE_DIR, ".pagination_state.json")

SORT_MODES = [
    ("lastChecked", "desc", "Terbaru (Freshly Checked)"),
    ("responseTime", "asc", "Tercepat (Lowest Latency)"),
    ("upTime", "desc", "Paling Stabil (Highest Uptime)")
]

def load_pagination_state() -> dict:
    default_state = {
        "page": 1,
        "mode_index": 0,
        "per_page": 250,
        "total_available": 1200,
        "geonode_cooldown_until": 0,
        "last_fetch_time": 0
    }
    if os.path.exists(PAGINATION_STATE_FILE):
        try:
            with open(PAGINATION_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {**default_state, **data}
        except Exception:
            pass
    return default_state

def save_pagination_state(state: dict):
    try:
        tmp = PAGINATION_STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, PAGINATION_STATE_FILE)
    except Exception:
        pass

def get_next_pagination_slice(per_page: int = 250) -> tuple[int, str, str, str, int, int]:
    """
    Returns (page, sort_by, sort_type, mode_desc, start_item, end_item)
    and rotates to the next page for subsequent calls.
    Example:
    - Call 1: Page 1 (Items 1 - 250) | Mode: Terbaru
    - Call 2: Page 2 (Items 251 - 500) | Mode: Terbaru
    - Call 3: Page 3 (Items 501 - 750) | Mode: Terbaru
    - Call 4: Page 4 (Items 751 - 1000) | Mode: Terbaru
    - Call 5: Page 5 (Items 1001 - 1200) | Mode: Terbaru
    - Call 6: Page 1 (Items 1 - 250) | Mode: Tercepat (rotasi kriteria baru)
    """
    state = load_pagination_state()
    page = state.get("page", 1)
    mode_idx = state.get("mode_index", 0) % len(SORT_MODES)
    total = state.get("total_available", 1200)

    max_pages = max(1, (total + per_page - 1) // per_page)
    if page > max_pages:
        page = 1
        mode_idx = (mode_idx + 1) % len(SORT_MODES)

    sort_by, sort_type, mode_desc = SORT_MODES[mode_idx]
    start_item = (page - 1) * per_page + 1
    end_item = min(page * per_page, total)

    # Next state advance
    next_page = page + 1
    if next_page > max_pages:
        next_page = 1
        next_mode_idx = (mode_idx + 1) % len(SORT_MODES)
    else:
        next_mode_idx = mode_idx

    state["page"] = next_page
    state["mode_index"] = next_mode_idx
    state["per_page"] = per_page
    state["last_fetch_time"] = time.time()
    save_pagination_state(state)

    return page, sort_by, sort_type, mode_desc, start_item, end_item

def mark_proxy_dead(proxy_url: str):
    """
    Mark a dead/blocked proxy so it is excluded from future refills,
    and IMMEDIATELY prune it from active working_yt_proxies.json and .txt
    so that all workers and the GUI reflect real-time live proxy counts.
    """
    if not proxy_url:
        return
    try:
        p_clean = proxy_url.strip()
        dead_set = set()
        if os.path.exists(DEAD_PROXIES_FILE):
            try:
                with open(DEAD_PROXIES_FILE, "r", encoding="utf-8") as f:
                    dead_set = set(json.load(f))
            except Exception:
                pass
        dead_set.add(p_clean)
        if len(dead_set) > 1500:
            dead_set = set(list(dead_set)[-1000:])
        tmp = DEAD_PROXIES_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(list(dead_set), f)
        os.replace(tmp, DEAD_PROXIES_FILE)

        # 1. Prune immediately from working_yt_proxies.json
        if os.path.exists(DEFAULT_OUTPUT_JSON):
            try:
                with open(DEFAULT_OUTPUT_JSON, "r", encoding="utf-8") as f:
                    current_items = json.load(f)
                if isinstance(current_items, list):
                    filtered = [
                        item for item in current_items
                        if (item.get("proxy") if isinstance(item, dict) else str(item)).strip() != p_clean
                    ]
                    if len(filtered) != len(current_items):
                        tmp_j = DEFAULT_OUTPUT_JSON + ".tmp"
                        with open(tmp_j, "w", encoding="utf-8") as f:
                            json.dump(filtered, f, indent=2)
                        os.replace(tmp_j, DEFAULT_OUTPUT_JSON)
            except Exception:
                pass

        # 2. Prune immediately from working_yt_proxies.txt
        txt_path = DEFAULT_OUTPUT_JSON.replace(".json", ".txt")
        if os.path.exists(txt_path):
            try:
                with open(txt_path, "r", encoding="utf-8") as f:
                    lines = [line.strip() for line in f if line.strip()]
                filtered_lines = [l for l in lines if l != p_clean]
                if len(filtered_lines) != len(lines):
                    tmp_t = txt_path + ".tmp"
                    with open(tmp_t, "w", encoding="utf-8") as f:
                        for l in filtered_lines:
                            f.write(l + "\n")
                    os.replace(tmp_t, txt_path)
            except Exception:
                pass

        # 3. Check remaining active proxy count; trigger refill if low (< 15)
        if os.path.exists(DEFAULT_OUTPUT_JSON):
            try:
                with open(DEFAULT_OUTPUT_JSON, "r", encoding="utf-8") as f:
                    rem = json.load(f)
                if isinstance(rem, list) and len(rem) < 15:
                    trigger_refill()
            except Exception:
                pass
    except Exception:
        pass

def get_dead_proxies() -> set:
    """Retrieve set of known dead/blocked proxies."""
    if os.path.exists(DEAD_PROXIES_FILE):
        try:
            with open(DEAD_PROXIES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data) if isinstance(data, list) else set()
        except Exception:
            pass
    return set()

def reset_proxy_storage(
    clear_dead: bool = True,
    clear_pagination: bool = True,
    output_json: str | None = None
) -> None:
    """
    Mereset file penyimpanan proxy menjadi 0 (kosong):
    - working_yt_proxies.json -> []
    - working_yt_proxies.txt -> kosong ("")
    - .dead_proxies.json -> [] (jika clear_dead=True)
    - .pagination_state.json -> reset ke state awal (jika clear_pagination=True)
    """
    if output_json is None:
        json_path = DEFAULT_OUTPUT_JSON
        txt_path = DEFAULT_OUTPUT_JSON.replace(".json", ".txt")
    else:
        if output_json.endswith(".json"):
            json_path = output_json
            txt_path = output_json[:-5] + ".txt"
        elif output_json.endswith(".txt"):
            txt_path = output_json
            json_path = output_json[:-4] + ".json"
        else:
            json_path = output_json + ".json"
            txt_path = output_json + ".txt"

    targets_json = {json_path, DEFAULT_OUTPUT_JSON}
    targets_txt = {txt_path, DEFAULT_OUTPUT_JSON.replace(".json", ".txt")}

    for jf in targets_json:
        try:
            tmp_j = jf + ".tmp"
            with open(tmp_j, "w", encoding="utf-8") as f:
                json.dump([], f, indent=2)
            os.replace(tmp_j, jf)
        except Exception as e:
            pass

    for tf in targets_txt:
        try:
            tmp_t = tf + ".tmp"
            with open(tmp_t, "w", encoding="utf-8") as f:
                f.write("")
            os.replace(tmp_t, tf)
        except Exception as e:
            pass

    if clear_dead and os.path.exists(DEAD_PROXIES_FILE):
        try:
            tmp_dead = DEAD_PROXIES_FILE + ".tmp"
            with open(tmp_dead, "w", encoding="utf-8") as f:
                json.dump([], f)
            os.replace(tmp_dead, DEAD_PROXIES_FILE)
        except Exception:
            pass

    if clear_pagination:
        try:
            default_state = {
                "page": 1,
                "mode_index": 0,
                "per_page": 250,
                "total_available": 1200,
                "geonode_cooldown_until": 0,
                "last_fetch_time": 0
            }
            save_pagination_state(default_state)
        except Exception:
            pass

    print(f"{CYAN}[RESET-STORAGE]{RESET} File proxy ({json_path} & {txt_path}) berhasil di-reset menjadi {BOLD}0 proxy{RESET}.")


def fetch_proxyscrape_proxies(countries: list[str] | None = None, timeout: float = 8.0) -> list[str]:
    """Secondary fallback proxy provider using ProxyScrape free API."""
    c_str = ",".join(countries) if countries else "US,GB,DE,CA,FR,AU,JP,NL,IT,ES,SG"
    url = f"https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http,socks4,socks5&timeout=3000&country={c_str}&ssl=yes"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    urls = []
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="ignore")
            for line in text.splitlines():
                line = line.strip()
                if line and ":" in line:
                    if not line.startswith(("http://", "https://", "socks4://", "socks5://")):
                        urls.append(f"http://{line}")
                    else:
                        urls.append(line)
    except Exception:
        pass
    return urls

def fetch_backup_proxies_paginated(limit: int = 250, offset: int = 0, countries: list[str] | None = None) -> list[str]:
    """
    Backup paginated proxies from ProxyScrape and high-speed GitHub SOCKS/HTTP mirrors.
    Rotates through offset so duplicate proxies are avoided.
    """
    dead_proxies = get_dead_proxies()
    results = []
    seen = set()

    # 1. ProxyScrape High-CPM
    try:
        ps_list = fetch_proxyscrape_proxies(countries=countries, timeout=6.0)
        sliced_ps = ps_list[offset:offset + limit] if offset < len(ps_list) else ps_list[:limit]
        for p in sliced_ps:
            if p not in seen and p not in dead_proxies:
                seen.add(p)
                results.append(p)
    except Exception:
        pass

    # 2. SOCKS-List GitHub mirror jika masih butuh tambahan
    if len(results) < limit:
        needed = limit - len(results)
        mirror_urls = [
            "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/socks5.txt",
            "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt"
        ]
        for m_url in mirror_urls:
            if len(results) >= limit:
                break
            try:
                proto = "socks5" if "socks5" in m_url else "http"
                req = urllib.request.Request(m_url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    lines = resp.read().decode("utf-8", errors="ignore").splitlines()
                    chunk = lines[offset:offset + needed] if offset < len(lines) else lines[:needed]
                    for line in chunk:
                        line = line.strip()
                        if line and ":" in line:
                            p_str = f"{proto}://{line}"
                            if p_str not in seen and p_str not in dead_proxies:
                                seen.add(p_str)
                                results.append(p_str)
            except Exception:
                pass

    return results[:limit]

def fetch_geonode_proxies(
    limit: int = 250,
    page: int = 1,
    sort_by: str = "lastChecked",
    sort_type: str = "desc",
    countries: list[str] | None = None,
    timeout: float = 10.0
) -> tuple[list[str], int]:
    """
    Fetch exact page slice from Geonode API.
    Returns (proxy_urls, total_available).
    Handles 429 rate limit gracefully.
    """
    if countries is None:
        countries = HIGH_CPM_COUNTRIES

    state = load_pagination_state()
    cooldown_until = state.get("geonode_cooldown_until", 0)
    now = time.time()
    if now < cooldown_until:
        remaining_cd = int(cooldown_until - now)
        print(f"{YELLOW}[GEONODE COOLDOWN]{RESET} API sedang dalam masa jeda Cloudflare ({remaining_cd}s tersisa). Menggunakan mirror cadangan...")
        return [], 0

    country_query = ""
    if countries:
        country_query = "&" + "&".join([f"country={c.upper()}" for c in countries])

    url = f"https://proxylist.geonode.com/api/proxy-list?page={page}&limit={limit}&sort_by={sort_by}&sort_type={sort_type}{country_query}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    proxy_urls = []
    seen = set()
    dead_proxies = get_dead_proxies()
    total_available = 0

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            items = data.get("data", [])
            total_available = data.get("total", 0)
            if total_available > 0:
                state["total_available"] = total_available
                save_pagination_state(state)

            for item in items:
                ip = item.get("ip")
                port = item.get("port")
                protocols = item.get("protocols", ["http"])
                if not ip or not port:
                    continue
                proto = protocols[0].lower() if protocols else "http"
                scheme = proto if proto in ("socks4", "socks5") else "http"
                p_str = f"{scheme}://{ip}:{port}"
                if p_str not in seen and p_str not in dead_proxies:
                    seen.add(p_str)
                    proxy_urls.append(p_str)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            retry_after = 600
            try:
                ra_header = e.headers.get("Retry-After")
                if ra_header:
                    retry_after = int(ra_header)
            except Exception:
                pass
            state["geonode_cooldown_until"] = time.time() + retry_after
            save_pagination_state(state)
            print(f"{YELLOW}[RATE-LIMIT GEONODE (429)]{RESET} Kuota 100 req/jam tercapai. Beralih ke High-CPM Mirror (cooldown {retry_after}s)...")
        else:
            print(f"{YELLOW}[WARN] Gagal mengambil proxy Geonode (Hal {page}): HTTP {e.code}{RESET}")
    except Exception as e:
        print(f"{YELLOW}[WARN] Gagal koneksi ke Geonode (Hal {page}): {e}{RESET}")

    return proxy_urls, total_available

def get_and_verify_proxies(
    limit: int = 250,
    threads: int = 80,
    timeout: float = 3.5,
    page: int | None = None,
    sort_by: str | None = None,
    sort_type: str | None = None,
    countries: list[str] | None = None,
    output_json: str | None = None,
    merge_existing: bool = True
) -> list[str]:
    """
    Fetch up to `limit` proxies across pages using smart pagination,
    and verify each with YouTube 443 TLS handshake.
    Saves verified working proxies to `working_yt_proxies.json` & `.txt`.
    Returns list of working proxy URLs sorted by latency.
    """
    if output_json is None:
        output_json = DEFAULT_OUTPUT_JSON
    if countries is None:
        countries = HIGH_CPM_COUNTRIES

    # Jika page tidak ditentukan, gunakan paginasi pintar otomatis
    if page is None:
        p_num, s_by, s_type, mode_desc, start_item, end_item = get_next_pagination_slice(per_page=limit)
    else:
        p_num = page
        s_by = sort_by or "lastChecked"
        s_type = sort_type or "desc"
        mode_desc = "Kustom / Terpilih"
        start_item = (p_num - 1) * limit + 1
        end_item = p_num * limit

    c_str = ", ".join(countries[:6]) + ("..." if len(countries) > 6 else "")
    print(f"\n{BOLD}{CYAN}==================================================================={RESET}")
    print(f"{BOLD}    SMART PROXY ROTATION & PAGINATION (HIGH CPM AD-SENSE)           {RESET}")
    print(f"{BOLD}{CYAN}==================================================================={RESET}")
    print(f"{CYAN}[PAGINASI]{RESET} Halaman {BOLD}{p_num}{RESET} (Rentang Items: {BOLD}{start_item} - {end_item}{RESET})")
    print(f"{CYAN}[KRITERIA]{RESET} Prioritas: {BOLD}{mode_desc}{RESET}")
    print(f"{CYAN}[NEGARA]  {RESET} {c_str}")

    # 1. Fetch dari Geonode untuk rentang halaman tersebut
    raw_proxies, total_found = fetch_geonode_proxies(
        limit=limit,
        page=p_num,
        sort_by=s_by,
        sort_type=s_type,
        countries=countries
    )

    # 2. Jika Geonode kosong / terkena rate-limit, ambil dari Mirror Paginated
    if len(raw_proxies) < 30:
        offset = (p_num - 1) * limit
        print(f"{CYAN}[INFO]{RESET} Mengisi stok dari High-CPM Mirror Paginated (Offset {offset})...")
        backup = fetch_backup_proxies_paginated(limit=limit, offset=offset, countries=countries)
        dead_proxies = get_dead_proxies()
        for b in backup:
            if b not in raw_proxies and b not in dead_proxies:
                raw_proxies.append(b)

    # 3. Load existing proxies jika merge_existing aktif
    dead_proxies = get_dead_proxies()
    existing_candidates = []
    if merge_existing and os.path.exists(output_json):
        try:
            with open(output_json, "r", encoding="utf-8") as f:
                old_data = json.load(f)
            for item in old_data:
                p = item.get("proxy") if isinstance(item, dict) else str(item)
                if p and p not in dead_proxies:
                    existing_candidates.append(p)
        except Exception:
            pass

    combined_raw = list(dict.fromkeys(raw_proxies + existing_candidates))

    if not combined_raw:
        print(f"{YELLOW}[WARN]{RESET} Tidak ada kandidat proxy baru untuk diuji.")
        return []

    print(f"{GREEN}[OK]{RESET} Total {len(combined_raw)} kandidat proxy ({len(raw_proxies)} dari Halaman {p_num}, {len(existing_candidates)} existing).")
    print(f"{CYAN}[INFO]{RESET} Menguji handshake YouTube (443) dengan {threads} threads paralel...")

    parsed_list = []
    for r in combined_raw:
        if r in dead_proxies:
            continue
        p = parse_proxy(r)
        if p:
            parsed_list.append(p)

    connect_to = min(2.0, timeout * 0.5)
    handshake_to = max(2.0, timeout * 0.6)

    working = []
    checked_count = 0
    total = len(parsed_list)
    t_start = time.time()

    with ThreadPoolExecutor(max_workers=threads) as executor:
        future_map = {
            executor.submit(check_proxy, p, connect_to, handshake_to): p for p in parsed_list
        }
        for future in as_completed(future_map):
            checked_count += 1
            res = future.result()
            if res.get("alive"):
                working.append(res)
                print(
                    f"[{checked_count}/{total}] {GREEN}✔ ALIVE{RESET} {BOLD}{res['proxy']}{RESET} "
                    f"({res['protocol'].upper()}) - {res['latency_ms']}ms"
                )

    duration = time.time() - t_start
    print("-" * 67)
    print(
        f"{BOLD}Hasil Pengujian ({duration:.1f} detik): "
        f"{GREEN}{len(working)} Aktif{RESET} / {RED}{total - len(working)} Mati/Timeout{RESET}"
    )

    if working:
        dead_proxies = get_dead_proxies()
        txt_path = output_json.replace(".json", ".txt")

        # 1. BACA PROXY LAMA (Hanya jika merge_existing=True)
        accumulated_dict = {}

        if merge_existing:
            # Dari file JSON existing
            if os.path.exists(output_json):
                try:
                    with open(output_json, "r", encoding="utf-8") as f:
                        old_json = json.load(f)
                        if isinstance(old_json, list):
                            for item in old_json:
                                p = item.get("proxy") if isinstance(item, dict) else str(item).strip()
                                if p and p not in dead_proxies:
                                    accumulated_dict[p] = item if isinstance(item, dict) else {"proxy": p, "latency_ms": 9999}
                except Exception:
                    pass

            # Dari file TXT existing
            if os.path.exists(txt_path):
                try:
                    with open(txt_path, "r", encoding="utf-8") as f:
                        for line in f:
                            p = line.strip()
                            if p and not p.startswith("#") and p not in dead_proxies:
                                if p not in accumulated_dict:
                                    accumulated_dict[p] = {"proxy": p, "latency_ms": 9999}
                except Exception:
                    pass

        # 2. TAMBAHKAN PROXY BARU YANG LOLOS TES (APPEND / MERGE)
        new_added = 0
        for item in working:
            p = item.get("proxy") if isinstance(item, dict) else str(item).strip()
            if p and p not in dead_proxies:
                if p not in accumulated_dict:
                    new_added += 1
                # Perbarui latency dengan hasil tes terbaru
                accumulated_dict[p] = item if isinstance(item, dict) else {"proxy": p, "latency_ms": 9999}

        # 3. Urutkan seluruh proxy yang terkumpul berdasarkan latency tercepat
        all_working = list(accumulated_dict.values())
        all_working.sort(key=lambda x: x.get("latency_ms", 9999))

        # 4. Atomic Save ke JSON
        try:
            tmp_json = output_json + ".tmp"
            with open(tmp_json, "w", encoding="utf-8") as f:
                json.dump(all_working, f, indent=2)
            os.replace(tmp_json, output_json)

            # 5. Atomic Save ke TXT
            tmp_txt = txt_path + ".tmp"
            with open(tmp_txt, "w", encoding="utf-8") as f:
                for item in all_working:
                    p = item.get("proxy") if isinstance(item, dict) else str(item)
                    f.write(p + "\n")
            os.replace(tmp_txt, txt_path)

            if merge_existing:
                print(f"{GREEN}[HOT-UPDATE]{RESET} Berhasil menambahkan {BOLD}{new_added} proxy baru{RESET} (Tanpa Menimpa)! Total terkumpul: {BOLD}{len(all_working)} proxy aktif{RESET} di {BOLD}{txt_path}{RESET}.")
            else:
                print(f"{GREEN}[FRESH-POOL]{RESET} Pool proxy diperbarui secara fresh! Total: {BOLD}{len(all_working)} proxy aktif{RESET} di {BOLD}{txt_path}{RESET}.")
        except Exception as e:
            print(f"{YELLOW}[WARN] Gagal menyimpan akumulasi proxy: {e}{RESET}")

        return [item["proxy"] if isinstance(item, dict) else str(item) for item in all_working]

    # Jika tidak ada proxy baru yang lolos di putaran ini, tetap return daftar akumulasi existing jika merge_existing aktif
    if merge_existing and os.path.exists(output_json):
        try:
            with open(output_json, "r", encoding="utf-8") as f:
                saved = json.load(f)
                if isinstance(saved, list) and saved:
                    return [item["proxy"] if isinstance(item, dict) else str(item) for item in saved]
        except Exception:
            pass
    return []

def trigger_refill():
    """Trigger immediate proxy fetch & test without waiting for timer."""
    try:
        with open(REFILL_TRIGGER_FILE, "w") as f:
            f.write(str(time.time()))
    except Exception:
        pass

def start_background_proxy_replenisher(
    interval_sec: int = 180,
    stop_event = None,
    limit: int = 250,
    threads: int = 60,
    output_json: str | None = None,
    min_threshold: int = 15,
    countries: list[str] | None = None,
    hourly_reset_sec: int = 3600
):
    """
    Background daemon thread that continuously replenishes the proxy pool:
    - Auto-Refill Instan: Jika proxy aktif tersisa < min_threshold (default 15), LANGSUNG fetch & test dari Geonode/ProxyScrape.
    - Sinyal Trigger: Jika worker mendeteksi stok menipis, memicu fetch seketika tanpa delay.
    - Rotasi Berkala: Merotasi halaman dan kriteria (Items 1-250, 251-500, 501-750, dst.) setiap `interval_sec`.
    - Auto-Reset 1 Jam: Setiap 1 jam (`hourly_reset_sec`), mengosongkan file proxy menjadi 0 dan fetch fresh proxy.
    - Otomatis memicu hot-reload ke seluruh proses worker bot yang sedang berjalan!
    """
    import threading
    if output_json is None:
        output_json = DEFAULT_OUTPUT_JSON
    if countries is None:
        countries = HIGH_CPM_COUNTRIES

    last_refill_time = 0
    last_hourly_reset = time.time()
    is_fetching = False

    def replenisher_loop():
        nonlocal last_refill_time, last_hourly_reset, is_fetching
        if os.path.exists(REFILL_TRIGGER_FILE):
            try:
                os.remove(REFILL_TRIGGER_FILE)
            except Exception:
                pass

        while True:
            if stop_event is not None and stop_event.is_set():
                break

            time.sleep(2.0)  # Cek status setiap 2 detik
            if stop_event is not None and stop_event.is_set():
                break

            now = time.time()

            # 0. SIKLUS RESET 1 JAM: Kosongkan file proxy ke 0 dan ambil fresh pool
            is_hourly_due = (hourly_reset_sec > 0) and ((now - last_hourly_reset) >= hourly_reset_sec)
            if is_hourly_due and not is_fetching:
                is_fetching = True
                print(f"\n{BOLD}{CYAN}[RESET-1-JAM]{RESET} Siklus 1 jam ({hourly_reset_sec}s) tercapai! Mengosongkan file proxy menjadi 0 dan mengambil proxy baru yang fresh...")
                try:
                    reset_proxy_storage(clear_dead=True, clear_pagination=True, output_json=output_json)
                    get_and_verify_proxies(
                        limit=limit,
                        threads=threads,
                        countries=countries,
                        output_json=output_json,
                        merge_existing=False
                    )
                    last_hourly_reset = time.time()
                    last_refill_time = time.time()
                except Exception as e:
                    if stop_event is None or not stop_event.is_set():
                        print(f"{YELLOW}[WARN] Reset 1 jam & fetch proxy gagal: {e}{RESET}")
                finally:
                    is_fetching = False
                continue

            # 1. Cek apakah ada trigger darurat dari worker
            trigger_detected = False
            if os.path.exists(REFILL_TRIGGER_FILE):
                try:
                    os.remove(REFILL_TRIGGER_FILE)
                    trigger_detected = True
                except Exception:
                    pass

            # 2. Cek jumlah proxy aktif saat ini di file JSON (ekslusif yang bukan dead)
            current_active = 0
            dead_proxies = get_dead_proxies()
            if os.path.exists(output_json):
                try:
                    with open(output_json, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, list):
                            current_active = len([
                                x for x in data
                                if (x.get("proxy") if isinstance(x, dict) else str(x)).strip() not in dead_proxies
                            ])
                except Exception:
                    current_active = 0

            # 3. Tentukan apakah perlu refill sekarang
            is_low = current_active < min_threshold
            time_since_last = now - last_refill_time
            is_timer_due = time_since_last >= interval_sec

            # Berikan jeda cooldown minimal 40 detik antar-refill agar tidak spamming/looping ketat
            should_refill = trigger_detected or (is_low and time_since_last >= 40.0) or is_timer_due

            if should_refill and not is_fetching:
                is_fetching = True
                reason = "TRIGGER WORKER" if trigger_detected else ("STOK MENIPIS" if is_low else "ROTASI BERKALA")
                print(f"\n{CYAN}[AUTO-REFILL PROXY] ({reason} | Sisa Aktif: {current_active} proxy){RESET} Memulai rotasi paginasi stok baru...")
                try:
                    get_and_verify_proxies(
                        limit=limit,
                        threads=threads,
                        countries=countries,
                        output_json=output_json,
                        merge_existing=True
                    )
                    last_refill_time = time.time()
                except Exception as e:
                    if stop_event is None or not stop_event.is_set():
                        print(f"{YELLOW}[WARN] Auto-refill proxy gagal: {e}{RESET}")
                finally:
                    is_fetching = False

    t = threading.Thread(target=replenisher_loop, daemon=True, name="ProxyReplenisher")
    t.start()
    return t

if __name__ == "__main__":
    proxies = get_and_verify_proxies(limit=100, threads=50)
    print(f"\nSelesai! {len(proxies)} proxy High-CPM siap digunakan.")
