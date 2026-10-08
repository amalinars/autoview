#!/usr/bin/env python3
"""
High-Performance Zero-Dependency Python Proxy Checker
Supports SOCKS4, HTTP, and HTTPS proxies.
Validates true forward-proxy behavior via api.ipify.org (rejects HTML/captive portals).
"""

import socket
import ssl
import struct
import time
import re
import sys
import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

# ANSI terminal colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

TARGET_HOST = "api.ipify.org"
TARGET_PORT = 80
TARGET_PATH = "/"

def clean_proxy_url(raw: str) -> str:
    """Normalize and fix common typos in proxy strings."""
    line = raw.strip()
    if not line or line.startswith("#"):
        return ""
    
    # Fix typo 'ocks4://' -> 'socks4://', 'ocks5://' -> 'socks5://'
    if line.startswith("ocks4://"):
        line = "socks4://" + line[len("ocks4://"):]
    elif line.startswith("ocks5://"):
        line = "socks5://" + line[len("ocks5://"):]
    
    # Default to http:// if no protocol scheme provided
    if "://" not in line:
        line = "http://" + line
        
    return line

def parse_proxy(raw: str):
    cleaned = clean_proxy_url(raw)
    if not cleaned:
        return None
    
    parsed = urlparse(cleaned)
    protocol = parsed.scheme.lower()
    host = parsed.hostname
    port = parsed.port
    
    if not host or not port:
        return None
        
    return {
        "raw": raw.strip(),
        "normalized": f"{protocol}://{host}:{port}",
        "protocol": protocol,
        "host": host,
        "port": port
    }

def extract_valid_ip(resp_bytes: bytes) -> str | None:
    """
    Validates that the HTTP response came from api.ipify.org:
    - HTTP 200 status code
    - Response body is not HTML, captive portal, or router login
    - Body contains a valid IPv4 address
    """
    try:
        resp_str = resp_bytes.decode("utf-8", errors="ignore")
    except Exception:
        return None

    if not resp_str.startswith("HTTP/"):
        return None

    first_line = resp_str.splitlines()[0]
    if "200" not in first_line:
        return None

    parts = resp_str.split("\r\n\r\n", 1)
    if len(parts) < 2:
        return None
    body = parts[1].strip()

    # Reject HTML pages (Zoraxy, captive portals, router web interfaces)
    lower_body = body.lower()
    if "<html" in lower_body or "<!doctype" in lower_body or "<head" in lower_body or "<title" in lower_body:
        return None

    match = re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", body)
    if not match:
        return None

    ip_candidate = match.group(0)
    octets = [int(x) for x in ip_candidate.split(".")]
    if all(0 <= o <= 255 for o in octets):
        return ip_candidate

    return None

def test_socks4(host: str, port: int, timeout: float, target_ip: str) -> tuple[bool, int, str]:
    """Test SOCKS4 proxy connectivity and verify public IP retrieval."""
    start_time = time.perf_counter()
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        
        # SOCKS4 CONNECT Request packet:
        # VN: 0x04 | CD: 0x01 | DSTPORT: 2 bytes | DSTIP: 4 bytes | USERID: 0x00
        port_bytes = struct.pack(">H", TARGET_PORT)
        ip_bytes = socket.inet_aton(target_ip)
        req = b"\x04\x01" + port_bytes + ip_bytes + b"\x00"
        sock.sendall(req)
        
        # SOCKS4 Reply: 8 bytes
        reply = sock.recv(8)
        if len(reply) < 8:
            return False, 0, "Handshake failed (incomplete reply)"
        
        # Byte 1 is status code: 0x5A (90) = Request granted
        status = reply[1]
        if status != 0x5A:
            return False, 0, f"SOCKS4 rejected (code {hex(status)})"
        
        # Send HTTP GET request over the established tunnel
        http_req = (
            f"GET {TARGET_PATH} HTTP/1.1\r\n"
            f"Host: {TARGET_HOST}\r\n"
            f"User-Agent: Mozilla/5.0 (ProxyChecker)\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("utf-8")
        sock.sendall(http_req)
        
        response = b""
        while True:
            chunk = sock.recv(2048)
            if not chunk:
                break
            response += chunk
            if b"\r\n\r\n" in response and len(response) > 40:
                break
                
        latency = int((time.perf_counter() - start_time) * 1000)
        detected_ip = extract_valid_ip(response)
        
        if detected_ip:
            return True, latency, detected_ip
        else:
            return False, latency, "Invalid response / Non-proxy"
            
    except socket.timeout:
        return False, 0, "Timeout"
    except ConnectionRefusedError:
        return False, 0, "Connection refused"
    except Exception as e:
        return False, 0, str(e)
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass

def test_http_proxy(host: str, port: int, timeout: float, is_https_scheme: bool) -> tuple[bool, int, str]:
    """Test HTTP or HTTPS proxy connectivity and verify public IP retrieval."""
    start_time = time.perf_counter()
    sock = None
    try:
        raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw_sock.settimeout(timeout)
        raw_sock.connect((host, port))
        
        # If proxy specified https:// and port is 443, wrap client connection with TLS
        if is_https_scheme and port == 443:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            try:
                sock = ctx.wrap_socket(raw_sock, server_hostname=host)
            except Exception:
                raw_sock.close()
                raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                raw_sock.settimeout(timeout)
                raw_sock.connect((host, port))
                sock = raw_sock
        else:
            sock = raw_sock
            
        # 1. Try standard proxy GET request
        http_req = (
            f"GET http://{TARGET_HOST}{TARGET_PATH} HTTP/1.1\r\n"
            f"Host: {TARGET_HOST}\r\n"
            f"User-Agent: Mozilla/5.0 (ProxyChecker)\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("utf-8")
        sock.sendall(http_req)
        
        response = b""
        while True:
            chunk = sock.recv(2048)
            if not chunk:
                break
            response += chunk
            if b"\r\n\r\n" in response and len(response) > 40:
                break
                
        latency = int((time.perf_counter() - start_time) * 1000)
        detected_ip = extract_valid_ip(response)
        if detected_ip:
            return True, latency, detected_ip
            
        # 2. If direct GET returned non-200 or wasn't forwarded, attempt HTTP CONNECT tunnel
        sock.close()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        
        connect_req = (
            f"CONNECT {TARGET_HOST}:{TARGET_PORT} HTTP/1.1\r\n"
            f"Host: {TARGET_HOST}:{TARGET_PORT}\r\n"
            f"User-Agent: Mozilla/5.0 (ProxyChecker)\r\n\r\n"
        ).encode("utf-8")
        sock.sendall(connect_req)
        
        connect_resp = sock.recv(1024).decode("utf-8", errors="ignore")
        if "200" in connect_resp:
            sock.sendall(
                f"GET {TARGET_PATH} HTTP/1.1\r\nHost: {TARGET_HOST}\r\nConnection: close\r\n\r\n".encode("utf-8")
            )
            response = b""
            while True:
                chunk = sock.recv(2048)
                if not chunk:
                    break
                response += chunk
                if b"\r\n\r\n" in response and len(response) > 40:
                    break
            latency = int((time.perf_counter() - start_time) * 1000)
            detected_ip = extract_valid_ip(response)
            if detected_ip:
                return True, latency, detected_ip

        return False, latency, "Invalid response / Non-proxy"
        
    except socket.timeout:
        return False, 0, "Timeout"
    except ConnectionRefusedError:
        return False, 0, "Connection refused"
    except Exception as e:
        return False, 0, str(e)
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass

def test_proxy(proxy_info: dict, timeout: float, target_ip: str) -> dict:
    protocol = proxy_info["protocol"]
    host = proxy_info["host"]
    port = proxy_info["port"]
    norm = proxy_info["normalized"]
    
    if protocol == "socks4":
        alive, latency, message = test_socks4(host, port, timeout, target_ip)
    elif protocol in ("http", "https"):
        alive, latency, message = test_http_proxy(host, port, timeout, is_https_scheme=(protocol == "https"))
    else:
        alive, latency, message = False, 0, f"Unsupported protocol '{protocol}'"
        
    return {
        "proxy": norm,
        "raw": proxy_info["raw"],
        "protocol": protocol,
        "host": host,
        "port": port,
        "alive": alive,
        "latency_ms": latency,
        "public_ip": message if alive else None,
        "message": message
    }

def main():
    parser = argparse.ArgumentParser(description="Fast Zero-Dependency Python Proxy Checker")
    parser.add_argument("-f", "--file", default="proxies.txt", help="Path to proxy list file (default: proxies.txt)")
    parser.add_argument("-t", "--timeout", type=float, default=5.0, help="Socket timeout in seconds (default: 5.0)")
    parser.add_argument("-w", "--workers", type=int, default=20, help="Number of concurrent worker threads (default: 20)")
    parser.add_argument("-o", "--output", default="alive_proxies.txt", help="Path to save working proxies (default: alive_proxies.txt)")
    parser.add_argument("-j", "--json", default="alive_proxies.json", help="Path to save detailed JSON output (default: alive_proxies.json)")
    args = parser.parse_args()

    print(f"\n{BOLD}{CYAN}=== Zero-Dependency Multi-threaded Proxy Checker ==={RESET}")
    print(f"{CYAN}Input File :{RESET} {args.file}")
    print(f"{CYAN}Concurrency:{RESET} {args.workers} workers | {CYAN}Timeout:{RESET} {args.timeout}s")
    
    try:
        with open(args.file, "r") as f:
            lines = f.readlines()
    except FileNotFoundError:
        print(f"{RED}Error: File '{args.file}' not found.{RESET}")
        sys.exit(1)

    proxy_list = []
    for line in lines:
        p = parse_proxy(line)
        if p:
            proxy_list.append(p)

    if not proxy_list:
        print(f"{YELLOW}No valid proxies found in {args.file}.{RESET}")
        sys.exit(0)

    print(f"{CYAN}Loaded     :{RESET} {len(proxy_list)} proxies to test")
    
    print(f"{CYAN}Resolving  :{RESET} {TARGET_HOST} ... ", end="", flush=True)
    try:
        target_ip = socket.gethostbyname(TARGET_HOST)
        print(f"{GREEN}{target_ip}{RESET}\n")
    except Exception as e:
        print(f"{RED}Failed to resolve {TARGET_HOST}: {e}{RESET}")
        sys.exit(1)

    alive_results = []
    dead_count = 0
    total = len(proxy_list)

    print(f"{BOLD}{'STATUS':<9} {'LATENCY':<10} {'PROXY':<35} {'DETAILS'}{RESET}")
    print("-" * 75)

    start_all = time.perf_counter()

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(test_proxy, p, args.timeout, target_ip): p for p in proxy_list}
        
        for future in as_completed(futures):
            res = future.result()
            if res["alive"]:
                alive_results.append(res)
                lat_str = f"{res['latency_ms']}ms"
                print(f"{GREEN}[ALIVE]{RESET}   {lat_str:<10} {res['proxy']:<35} Detected IP: {res['public_ip']}")
            else:
                dead_count += 1
                msg = res['message']
                if len(msg) > 30:
                    msg = msg[:27] + "..."
                print(f"{RED}[DEAD]{RESET}    {'-':<10} {res['proxy']:<35} {msg}")

    # Sort alive proxies by latency (fastest first!)
    alive_results.sort(key=lambda x: x["latency_ms"])

    total_time = round(time.perf_counter() - start_all, 2)
    avg_lat = round(sum(r["latency_ms"] for r in alive_results) / len(alive_results), 1) if alive_results else 0

    # Save sorted alive proxies to file
    with open(args.output, "w") as f:
        for r in alive_results:
            f.write(f"{r['proxy']}\n")
            
    # Save JSON details
    with open(args.json, "w") as f:
        json.dump(alive_results, f, indent=2)

    print("-" * 75)
    print(f"{BOLD}Summary:{RESET}")
    print(f"  • Total Proxies : {total}")
    print(f"  • {GREEN}Alive Proxies{RESET} : {len(alive_results)} (sorted by fastest response)")
    print(f"  • {RED}Dead Proxies{RESET}  : {dead_count}")
    if alive_results:
        print(f"  • Avg Latency   : {avg_lat} ms (Min: {alive_results[0]['latency_ms']}ms, Max: {alive_results[-1]['latency_ms']}ms)")
    print(f"  • Total Time    : {total_time} s")
    print(f"  • Saved List    : {args.output}")
    print(f"  • Saved JSON    : {args.json}")
    print(f"{CYAN}==================================================={RESET}\n")

if __name__ == "__main__":
    main()
