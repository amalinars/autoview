#!/usr/bin/env python3
"""
YouTube HTTPS Proxy Checker & Auto-Manager
------------------------------------------
Fitur Utama:
1. High-Speed Verification: 60-100 threads dengan 2-stage timeout (connect 2.5s + handshake 3.0s).
2. Continuous Loop / Daemon Mode: Mengecek dan memperbarui proxy otomatis tiap X menit (--loop --interval 5).
3. Anti-Nyampah / Auto-Purge: Otomatis membuang proxy mati dari proxies.txt agar file tidak menumpuk sampah.
4. Auto-Ingest: Deteksi otomatis file 'raw_proxies.txt' jika Anda menempelkan proxy baru kapan saja.
5. Hot-Reload Ready: Langsung sinkron dengan youtube_search.py tanpa perlu restart bot.
"""

import socket
import ssl
import time
import os
import sys
import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

# ANSI Colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
BOLD = "\033[1m"
RESET = "\033[0m"

TEST_HOST = "www.youtube.com"
TEST_PORT = 443

def clean_proxy_url(raw: str) -> str:
    """Normalize raw proxy string into a proper URL."""
    line = raw.strip()
    if not line or line.startswith("#"):
        return ""
    
    # Common prefix fixes
    if line.startswith("ocks4://"):
        line = "socks4://" + line[8:]
    elif line.startswith("ocks5://"):
        line = "socks5://" + line[8:]
    
    if "://" not in line:
        line = "http://" + line
        
    return line

def parse_proxy(raw: str):
    """Parse proxy string into dictionary with scheme, host, port, credentials."""
    cleaned = clean_proxy_url(raw)
    if not cleaned:
        return None
    
    try:
        parsed = urlparse(cleaned)
        scheme = parsed.scheme.lower()
        host = parsed.hostname
        port = parsed.port
        username = parsed.username
        password = parsed.password
        
        if not host or not port:
            return None
            
        return {
            "proxy": cleaned,
            "raw": raw.strip(),
            "protocol": scheme,
            "host": host,
            "port": port,
            "username": username,
            "password": password
        }
    except Exception:
        return None

def test_socks4(host: str, port: int, connect_timeout: float = 2.5, handshake_timeout: float = 3.0):
    """Fast 2-stage SOCKS4 proxy CONNECT to YouTube."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(connect_timeout)
    t0 = time.time()
    try:
        s.connect((host, port))
        s.settimeout(handshake_timeout)
        target_ip = socket.gethostbyname(TEST_HOST)
        ip_bytes = socket.inet_aton(target_ip)
        port_bytes = TEST_PORT.to_bytes(2, "big")
        
        # SOCKS4 request: VN=4, CD=1 (CONNECT), DSTPORT, DSTIP, NULL
        req = b"\x04\x01" + port_bytes + ip_bytes + b"\x00"
        s.sendall(req)
        
        resp = s.recv(8)
        if len(resp) >= 2 and resp[0] == 0x00 and resp[1] == 0x5a:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            ss = ctx.wrap_socket(s, server_hostname=TEST_HOST)
            ss.settimeout(2.5)
            ss.sendall(b"GET /generate_204 HTTP/1.1\r\nHost: www.youtube.com\r\nUser-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36\r\nConnection: close\r\n\r\n")
            h_resp = ss.recv(256)
            ss.close()
            if not h_resp:
                return False, 0, "No HTTP response from YouTube"
            status_line = h_resp.split(b"\r\n")[0].decode("utf-8", "ignore")
            if "204" in status_line or "200" in status_line:
                latency = int((time.time() - t0) * 1000)
                return True, latency, f"OK ({status_line[:12]})"
            return False, 0, f"YouTube Blocked ({status_line[:20]})"
        return False, 0, "SOCKS4 Rejected"
    except Exception as e:
        return False, 0, str(e)
    finally:
        try:
            s.close()
        except Exception:
            pass

def test_socks5(host: str, port: int, user: str = None, pwd: str = None, connect_timeout: float = 2.5, handshake_timeout: float = 3.0):
    """Fast 2-stage SOCKS5 proxy CONNECT to YouTube."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(connect_timeout)
    t0 = time.time()
    try:
        s.connect((host, port))
        s.settimeout(handshake_timeout)
        s.sendall(b"\x05\x01\x00")
        resp = s.recv(2)
        if len(resp) < 2 or resp[0] != 0x05 or resp[1] != 0x00:
            return False, 0, "SOCKS5 Auth Failed"
        
        domain_bytes = TEST_HOST.encode("utf-8")
        req = b"\x05\x01\x00\x03" + bytes([len(domain_bytes)]) + domain_bytes + TEST_PORT.to_bytes(2, "big")
        s.sendall(req)
        
        resp = s.recv(10)
        if len(resp) >= 4 and resp[1] == 0x00:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            ss = ctx.wrap_socket(s, server_hostname=TEST_HOST)
            ss.settimeout(2.5)
            ss.sendall(b"GET /generate_204 HTTP/1.1\r\nHost: www.youtube.com\r\nUser-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36\r\nConnection: close\r\n\r\n")
            h_resp = ss.recv(256)
            ss.close()
            if not h_resp:
                return False, 0, "No HTTP response from YouTube"
            status_line = h_resp.split(b"\r\n")[0].decode("utf-8", "ignore")
            if "204" in status_line or "200" in status_line:
                latency = int((time.time() - t0) * 1000)
                return True, latency, f"OK ({status_line[:12]})"
            return False, 0, f"YouTube Blocked ({status_line[:20]})"
        return False, 0, "SOCKS5 Connection Refused"
    except Exception as e:
        return False, 0, str(e)
    finally:
        try:
            s.close()
        except Exception:
            pass

def test_http_connect(host: str, port: int, user: str = None, pwd: str = None, connect_timeout: float = 2.5, handshake_timeout: float = 3.0):
    """Fast 2-stage HTTP CONNECT tunnel to YouTube 443."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(connect_timeout)
    t0 = time.time()
    try:
        s.connect((host, port))
        s.settimeout(handshake_timeout)
        connect_req = f"CONNECT {TEST_HOST}:{TEST_PORT} HTTP/1.1\r\nHost: {TEST_HOST}:{TEST_PORT}\r\n"
        if user and pwd:
            import base64
            auth = base64.b64encode(f"{user}:{pwd}".encode()).decode()
            connect_req += f"Proxy-Authorization: Basic {auth}\r\n"
        connect_req += "Proxy-Connection: Keep-Alive\r\n\r\n"
        
        s.sendall(connect_req.encode("utf-8"))
        
        buf = b""
        while b"\r\n\r\n" not in buf and len(buf) < 4096:
            chunk = s.recv(1024)
            if not chunk:
                break
            buf += chunk
            
        header = buf.decode("latin1", errors="ignore")
        if "200" in header.split("\r\n")[0]:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            ss = ctx.wrap_socket(s, server_hostname=TEST_HOST)
            ss.settimeout(2.5)
            ss.sendall(b"GET /generate_204 HTTP/1.1\r\nHost: www.youtube.com\r\nUser-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36\r\nConnection: close\r\n\r\n")
            h_resp = ss.recv(256)
            ss.close()
            if not h_resp:
                return False, 0, "No HTTP response from YouTube"
            status_line = h_resp.split(b"\r\n")[0].decode("utf-8", "ignore")
            if "204" in status_line or "200" in status_line:
                latency = int((time.time() - t0) * 1000)
                return True, latency, f"OK ({status_line[:12]})"
            return False, 0, f"YouTube Blocked ({status_line[:20]})"
        else:
            status_line = header.split("\r\n")[0] if header else "No response"
            return False, 0, f"Connect rejected: {status_line[:30]}"
    except Exception as e:
        return False, 0, str(e)
    finally:
        try:
            s.close()
        except Exception:
            pass

def check_proxy(proxy_info: dict | str, connect_timeout: float = 2.5, handshake_timeout: float = 3.0):
    """Route proxy check based on protocol."""
    if isinstance(proxy_info, str):
        parsed = parse_proxy(proxy_info)
        if not parsed:
            return {"alive": False, "latency_ms": 0, "message": "Invalid proxy format", "proxy": proxy_info}
        proxy_info = parsed

    proto = proxy_info["protocol"]
    host = proxy_info["host"]
    port = proxy_info["port"]
    user = proxy_info.get("username")
    pwd = proxy_info.get("password")
    
    if proto == "socks4":
        alive, latency, msg = test_socks4(host, port, connect_timeout, handshake_timeout)
    elif proto == "socks5":
        alive, latency, msg = test_socks5(host, port, user, pwd, connect_timeout, handshake_timeout)
    else: # http / https
        alive, latency, msg = test_http_connect(host, port, user, pwd, connect_timeout, handshake_timeout)
        
    return {
        **proxy_info,
        "alive": alive,
        "latency_ms": latency,
        "message": msg
    }

def load_proxies_from_file(filepath: str) -> list:
    """Load proxies from either JSON array or line-separated text."""
    proxies = []
    if not os.path.exists(filepath):
        return proxies
        
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read().strip()
            
        if content.startswith("[") and content.endswith("]"):
            try:
                data = json.loads(content)
                for item in data:
                    raw = item.get("proxy") or item.get("raw") if isinstance(item, dict) else str(item)
                    p = parse_proxy(raw)
                    if p:
                        proxies.append(p)
                return proxies
            except Exception:
                pass
                
        for line in content.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                p = parse_proxy(line)
                if p:
                    proxies.append(p)
                    
    except Exception as e:
        print(f"{RED}[ERROR] Gagal membaca {filepath}: {e}{RESET}")
        
    return proxies

def execute_check_cycle(cycle_num: int, input_file: str, output_json: str, threads: int, timeout: float):
    ts = time.strftime("%H:%M:%S")
    print(f"\n{BOLD}{CYAN}==================================================================={RESET}")
    print(f"{BOLD} [SIKLUS #{cycle_num}] PROXY CHECK & AUTO-UPDATE PIPELINE ({ts}) {RESET}")
    print(f"{BOLD}{CYAN}==================================================================={RESET}")

    # Koleksi seluruh sumber proxy: input_file, raw_proxies.txt, dan working_yt_proxies.txt
    sources = [input_file]
    if os.path.exists("raw_proxies.txt"):
        sources.append("raw_proxies.txt")
    if os.path.exists("working_yt_proxies.txt"):
        sources.append("working_yt_proxies.txt")

    all_proxies = []
    seen = set()
    for src in sources:
        loaded = load_proxies_from_file(src)
        for p in loaded:
            if p["proxy"] not in seen:
                seen.add(p["proxy"])
                all_proxies.append(p)

    if not all_proxies:
        print(f"{YELLOW}[WARN] Tidak ada proxy untuk diperiksa. Silakan isi 'proxies.txt' atau 'raw_proxies.txt'.{RESET}")
        return 0, 0

    print(f"{CYAN}[INFO]{RESET} Total proxy unik terkumpul : {BOLD}{len(all_proxies)}{RESET}")
    print(f"{CYAN}[INFO]{RESET} Concurrency : {threads} Threads | Fast Timeout : {timeout}s")
    print("-" * 67)

    connect_to = min(2.5, timeout * 0.5)
    handshake_to = max(2.5, timeout * 0.6)

    working_proxies = []
    checked_count = 0
    total = len(all_proxies)
    t_start = time.time()

    with ThreadPoolExecutor(max_workers=threads) as executor:
        future_map = {
            executor.submit(check_proxy, p, connect_to, handshake_to): p for p in all_proxies
        }
        
        for future in as_completed(future_map):
            checked_count += 1
            res = future.result()
            proxy_url = res["proxy"]
            
            if res["alive"]:
                working_proxies.append(res)
                print(f"[{checked_count}/{total}] {GREEN}✔ ALIVE{RESET} {BOLD}{proxy_url}{RESET} "
                      f"({res['protocol'].upper()}) - {res['latency_ms']}ms")
            else:
                if total <= 100 or checked_count % 15 == 0 or checked_count == total:
                    short_msg = res["message"][:28]
                    print(f"[{checked_count}/{total}] {RED}✘ DEAD{RESET}  {proxy_url} ({short_msg})")

    duration = time.time() - t_start
    alive_count = len(working_proxies)
    dead_count = total - alive_count

    print("-" * 67)
    print(f"{BOLD}Ringkasan Siklus #{cycle_num} (Selesai dalam {duration:.1f} detik):{RESET}")
    print(f"Total Diuji      : {total}")
    print(f"{GREEN}Aktif & Sehat    : {alive_count}{RESET}")
    print(f"{RED}Mati / Dibuang   : {dead_count} (Otomatis Dihapus / Anti-Nyampah){RESET}")

    if working_proxies:
        txt_output = output_json.replace(".json", ".txt")
        
        # Akumulasi dengan proxy existing (Ditambahkan, BUKAN ditimpa)
        accumulated_map = {}
        if os.path.exists(output_json):
            try:
                with open(output_json, "r", encoding="utf-8") as f:
                    old_data = json.load(f)
                    if isinstance(old_data, list):
                        for item in old_data:
                            p = item.get("proxy") if isinstance(item, dict) else str(item).strip()
                            if p:
                                accumulated_map[p] = item if isinstance(item, dict) else {"proxy": p, "latency_ms": 9999}
            except Exception:
                pass

        if os.path.exists(txt_output):
            try:
                with open(txt_output, "r", encoding="utf-8") as f:
                    for line in f:
                        p = line.strip()
                        if p and not p.startswith("#") and p not in accumulated_map:
                            accumulated_map[p] = {"proxy": p, "latency_ms": 9999}
            except Exception:
                pass

        for wp in working_proxies:
            p = wp["proxy"]
            accumulated_map[p] = wp

        all_working = list(accumulated_map.values())
        all_working.sort(key=lambda x: x.get("latency_ms", 9999))
        
        # 1. Update working_yt_proxies.json
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(all_working, f, indent=2)
            
        # 2. Update working_yt_proxies.txt (Ditambahkan secara akumulatif)
        with open(txt_output, "w", encoding="utf-8") as f:
            for wp in all_working:
                p = wp.get("proxy") if isinstance(wp, dict) else str(wp)
                f.write(p + "\n")

        # 3. AUTO-PURGE: Tulis ulang proxies.txt HANYA dengan proxy yang hidup
        # Ini mencegah proxies.txt menumpuk ratusan proxy bangkai (bebas nyampah!)
        if os.path.exists(input_file):
            with open(input_file, "w", encoding="utf-8") as f:
                for wp in working_proxies:
                    f.write(wp["proxy"] + "\n")

        # 4. Bersihkan raw_proxies.txt jika tadi ada
        if os.path.exists("raw_proxies.txt"):
            with open("raw_proxies.txt", "w", encoding="utf-8") as f:
                f.write("# Paste proxy baru di sini kapan saja (HTTP/SOCKS4/SOCKS5)\n")

        print(f"\n{GREEN}[HOT-UPDATE]{RESET} File proxy siap pakai berhasil diperbarui:")
        print(f"  👉 {BOLD}{output_json}{RESET} ({alive_count} proxy)")
        print(f"  👉 {BOLD}{txt_output}{RESET}")
        print(f"  👉 {BOLD}{input_file}{RESET} (Sudah dibersihkan dari proxy mati)")
        if os.path.exists("raw_proxies.txt"):
            print(f"  👉 {BOLD}raw_proxies.txt{RESET} (Siap untuk paste batch proxy baru)")
    else:
        print(f"\n{YELLOW}[WARN] Tidak ada proxy yang lolos uji pada siklus ini.{RESET}")

    print("=" * 67)
    return alive_count, dead_count

def main():
    parser = argparse.ArgumentParser(
        description="YouTube Proxy Engine - Fast Concurrent Checker & Daemon Manager"
    )
    parser.add_argument(
        "-i", "--input",
        default="proxies.txt",
        help="File input daftar proxy (default: proxies.txt)"
    )
    parser.add_argument(
        "-o", "--output-json",
        default="working_yt_proxies.json",
        help="File output JSON aktif (default: working_yt_proxies.json)"
    )
    parser.add_argument(
        "-t", "--threads",
        type=int,
        default=60,
        help="Jumlah thread concurrent checker (default: 60)"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=4.5,
        help="Batas waktu timeout total per proxy dalam detik (default: 4.5)"
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Jalankan terus menerus secara otomatis setiap X menit"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=5,
        help="Interval waktu pengulangan dalam menit jika memakai --loop (default: 5)"
    )

    args = parser.parse_args()

    # Pastikan raw_proxies.txt tersedia sebagai inbox penampungan proxy baru
    if not os.path.exists("raw_proxies.txt"):
        try:
            with open("raw_proxies.txt", "w", encoding="utf-8") as f:
                f.write("# Paste proxy baru di sini kapan saja (HTTP/SOCKS4/SOCKS5)\n")
        except Exception:
            pass

    cycle = 1
    try:
        while True:
            execute_check_cycle(cycle, args.input, args.output_json, args.threads, args.timeout)
            
            if not args.loop:
                break
                
            print(f"\n{MAGENTA}💤 Menunggu {args.interval} menit sebelum pengecekan berikutnya...{RESET}")
            print(f"{CYAN}💡 Tips:{RESET} Anda bisa menempelkan proxy baru kapan saja ke {BOLD}raw_proxies.txt{RESET}.")
            print(f"Tekan {BOLD}Ctrl + C{RESET} kapan saja untuk keluar.\n")
            
            # Interruptible countdown sleep
            sleep_seconds = args.interval * 60
            for _ in range(sleep_seconds):
                time.sleep(1)
                
            cycle += 1

    except KeyboardInterrupt:
        print(f"\n\n{YELLOW}[EXIT] Pengecekan proxy dihentikan oleh pengguna (Ctrl+C). Selesai.{RESET}\n")

if __name__ == "__main__":
    main()
