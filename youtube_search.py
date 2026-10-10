#!/usr/bin/env python3
"""
YouTube Search & Watch Bot (Process-Isolated, Watch Lock System, Smart Pre-Check)
- Fast Socket Pre-Check: Checks proxy in ~2s before opening browser (No ghost window popups).
- System Watch Lock: Once video playback begins, locks the session and prevents premature close.
- True Process Isolation: Independent OS process (PID) and persistent profile directory per worker.
- Search strictly: 'horrornologi' on channel Horrornologi.
- Dynamic watch: 30% - 70% of video duration with ad-skipping.
- Ultra-lightweight: Audio muted, low video quality (240p/360p), minimal Chrome flags.
- Clean exit on Ctrl+C.
"""

import sys
import os
import time
import random
import socket
import argparse
import json
import shutil
import tempfile
import multiprocessing
import traceback
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# ANSI Colors
GREEN = "\033[92m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
RED = "\033[91m"
MAGENTA = "\033[95m"
PEACH = "\033[38;5;215m"
BOLD = "\033[1m"
RESET = "\033[0m"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0"
]

VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1280, "height": 720}
]

def format_time(seconds: float) -> str:
    s = int(seconds)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

_GLOBAL_TELEMETRY_QUEUE = None

def set_global_telemetry_queue(q):
    global _GLOBAL_TELEMETRY_QUEUE
    _GLOBAL_TELEMETRY_QUEUE = q

def send_telemetry(worker_id: int | str, event: str, **data):
    if _GLOBAL_TELEMETRY_QUEUE is not None:
        try:
            _GLOBAL_TELEMETRY_QUEUE.put_nowait({
                "worker_id": worker_id,
                "event": event,
                "timestamp": time.time(),
                **data
            })
        except Exception:
            pass

def safe_log(prefix: str, msg: str, color: str = CYAN):
    ts = time.strftime("%H:%M:%S")
    sys.stdout.write(f"{color}[{ts}][{prefix}]{RESET} {msg}\n")
    sys.stdout.flush()
    if _GLOBAL_TELEMETRY_QUEUE is not None:
        try:
            _GLOBAL_TELEMETRY_QUEUE.put_nowait({
                "worker_id": prefix,
                "event": "log",
                "timestamp": ts,
                "prefix": prefix,
                "message": msg,
                "color": color
            })
        except Exception:
            pass

def is_proxy_alive(proxy_url: str | None, timeout: float = 2.0) -> bool:
    """Pre-check proxy YouTube 443 handshake before opening browser (avoids ghost/blank windows on dead proxies)."""
    if not proxy_url:
        return True
    try:
        from yt_proxy_checker import check_proxy
        res = check_proxy(proxy_url, connect_timeout=min(1.2, timeout), handshake_timeout=min(1.5, timeout))
        return bool(res.get("alive"))
    except Exception:
        return False

def load_proxies(filepath: str | None = None) -> list:
    """Load proxies sorted by latency if JSON, or clean list."""
    candidates = []
    if filepath:
        candidates.append(filepath)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        candidates.extend([
            os.path.join(base_dir, "working_yt_proxies.json"),
            os.path.join(base_dir, "working_yt_proxies.txt"),
            os.path.join(base_dir, "alive_proxies.json"),
            "working_yt_proxies.json",
            "working_yt_proxies.txt",
            "alive_proxies.json"
        ])

    for cf in candidates:
        if os.path.exists(cf):
            proxies = []
            try:
                with open(cf, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read().strip()
                if content.startswith("[") and content.endswith("]"):
                    data = json.loads(content)
                    data.sort(key=lambda x: x.get("latency_ms", 9999) if isinstance(x, dict) else 9999)
                    for item in data:
                        p = item.get("proxy") if isinstance(item, dict) else str(item)
                        if p and p.strip():
                            proxies.append(p.strip())
                else:
                    for line in content.splitlines():
                        line = line.strip()
                        if line and not line.startswith("#"):
                            if "://" not in line:
                                line = "http://" + line
                            proxies.append(line)
                if proxies:
                    safe_log("INIT", f"Memuat {len(proxies)} proxy dari '{cf}'.", GREEN)
                    return proxies
            except Exception as e:
                safe_log("INIT", f"Gagal membaca {cf}: {e}", YELLOW)
    return []

def generate_unique_fingerprint() -> dict:
    """
    Menghasilkan profil browser (fingerprint) yang 100% UNIK, realistis, dan koheren untuk setiap sesi browser baru.
    Mencegah deteksi sidik jari berulang dari YouTube/Google.
    """
    os_choice = random.choices(["windows", "macos", "linux"], weights=[0.65, 0.20, 0.15])[0]
    chrome_ver = random.choice([
        ("128.0.6613.138", "128"),
        ("129.0.6668.101", "129"),
        ("130.0.6723.117", "130"),
        ("131.0.6778.86", "131")
    ])
    full_ver, major_ver = chrome_ver

    if os_choice == "windows":
        ua = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{full_ver} Safari/537.36"
        platform = "Win32"
        uadata_platform = "Windows"
        gpus = [
            ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
            ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
            ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 SUPER Direct3D11 vs_5_0 ps_5_0, D3D11)"),
            ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)"),
            ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 770 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
            ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 6700 XT Direct3D11 vs_5_0 ps_5_0, D3D11)"),
        ]
        dpr = random.choice([1.0, 1.25, 1.5])
        resolutions = [
            (1920, 1080, 1040),
            (2560, 1440, 1400),
            (1536, 864, 824),
            (1440, 900, 860),
            (1366, 768, 728),
            (1680, 1050, 1010)
        ]
    elif os_choice == "macos":
        ua = f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{full_ver} Safari/537.36"
        platform = "MacIntel"
        uadata_platform = "macOS"
        gpus = [
            ("Apple", "ANGLE (Apple, Apple M1, OpenGL 4.1)"),
            ("Apple", "ANGLE (Apple, Apple M2, OpenGL 4.1)"),
            ("Apple", "ANGLE (Apple, Apple M3 Pro, OpenGL 4.1)"),
            ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon Pro 5500M OpenGL Engine, OpenGL 4.1)")
        ]
        dpr = 2.0
        resolutions = [
            (1440, 900, 875),
            (1680, 1050, 1025),
            (1920, 1080, 1055),
            (2560, 1440, 1415)
        ]
    else:  # linux
        ua = f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{full_ver} Safari/537.36"
        platform = "Linux x86_64"
        uadata_platform = "Linux"
        gpus = [
            ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060/PCIe/SSE2, OpenGL 4.5)"),
            ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 6600, OpenGL 4.6)"),
            ("Google Inc. (Intel)", "ANGLE (Intel, Mesa Intel(R) UHD Graphics 630 (CML GT2), OpenGL 4.6)")
        ]
        dpr = 1.0
        resolutions = [
            (1920, 1080, 1040),
            (1366, 768, 738),
            (1440, 900, 870)
        ]

    gpu_vendor, gpu_renderer = random.choice(gpus)
    screen_w, screen_h, avail_h = random.choice(resolutions)
    cores = random.choice([4, 6, 8, 12, 16])
    memory = random.choice([4, 8, 16, 32])
    languages = random.choice([
        ["en-US", "en"],
        ["id-ID", "id", "en-US", "en"],
        ["en-GB", "en-US", "en"]
    ])

    noise_r = random.randint(180, 255)
    noise_g = random.randint(180, 255)
    noise_b = random.randint(180, 255)
    audio_jitter = f"{random.uniform(0.0000001, 0.0000009):.9f}"

    return {
        "ua": ua,
        "platform": platform,
        "uadata_platform": uadata_platform,
        "major_ver": major_ver,
        "full_ver": full_ver,
        "webgl_vendor": gpu_vendor,
        "webgl_renderer": gpu_renderer,
        "screen_w": screen_w,
        "screen_h": screen_h,
        "avail_h": avail_h,
        "vp": {"width": screen_w, "height": screen_h},
        "dpr": dpr,
        "cores": cores,
        "memory": memory,
        "languages": languages,
        "noise_r": noise_r,
        "noise_g": noise_g,
        "noise_b": noise_b,
        "audio_jitter": audio_jitter
    }

def generate_stealth_script(fp: dict) -> str:
    langs_json = json.dumps(fp["languages"])
    primary_lang = fp["languages"][0]
    return f"""
        // 1. Remove navigator.webdriver & Automation Flags
        try {{
            delete Object.getPrototypeOf(navigator).webdriver;
        }} catch(e) {{}}
        Object.defineProperty(navigator, 'webdriver', {{ get: () => false }});
        Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {fp["cores"]} }});
        Object.defineProperty(navigator, 'deviceMemory', {{ get: () => {fp["memory"]} }});
        Object.defineProperty(navigator, 'platform', {{ get: () => '{fp["platform"]}' }});
        Object.defineProperty(navigator, 'languages', {{ get: () => {langs_json} }});
        Object.defineProperty(navigator, 'language', {{ get: () => '{primary_lang}' }});

        // 2. Realistic userAgentData
        if (navigator.userAgentData) {{
            try {{
                Object.defineProperty(navigator.userAgentData, 'platform', {{ get: () => '{fp["uadata_platform"]}' }});
            }} catch(e) {{}}
        }}

        // 3. Realistic Plugins
        Object.defineProperty(navigator, 'plugins', {{
            get: () => [
                {{ name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' }},
                {{ name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' }},
                {{ name: 'Native Client', filename: 'internal-nacl-plugin', description: '' }}
            ]
        }});

        // 4. Mock window.chrome runtime
        window.chrome = {{
            runtime: {{}},
            loadTimes: function() {{}},
            csi: function() {{}},
            app: {{}}
        }};

        // 5. Screen & Viewport Matching
        Object.defineProperty(window, 'devicePixelRatio', {{ get: () => {fp["dpr"]} }});
        Object.defineProperty(screen, 'width', {{ get: () => {fp["screen_w"]} }});
        Object.defineProperty(screen, 'height', {{ get: () => {fp["screen_h"]} }});
        Object.defineProperty(screen, 'availWidth', {{ get: () => {fp["screen_w"]} }});
        Object.defineProperty(screen, 'availHeight', {{ get: () => {fp["avail_h"]} }});
        Object.defineProperty(screen, 'colorDepth', {{ get: () => 24 }});
        Object.defineProperty(screen, 'pixelDepth', {{ get: () => 24 }});

        // 6. WebGL Vendor & Renderer Spoofing
        const getParamOrig = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(param) {{
            if (param === 37445) return '{fp["webgl_vendor"]}';
            if (param === 37446) return '{fp["webgl_renderer"]}';
            return getParamOrig.apply(this, arguments);
        }};
        if (typeof WebGL2RenderingContext !== 'undefined') {{
            const getParam2Orig = WebGL2RenderingContext.prototype.getParameter;
            WebGL2RenderingContext.prototype.getParameter = function(param) {{
                if (param === 37445) return '{fp["webgl_vendor"]}';
                if (param === 37446) return '{fp["webgl_renderer"]}';
                return getParam2Orig.apply(this, arguments);
            }};
        }}

        // 7. Subtle Canvas Noise (Micro-jitter unik per sesi)
        const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
        HTMLCanvasElement.prototype.toDataURL = function(type) {{
            const ctx = this.getContext('2d');
            if (ctx) {{
                const style = ctx.fillStyle;
                ctx.fillStyle = 'rgba({fp["noise_r"]},{fp["noise_g"]},{fp["noise_b"]},0.003)';
                ctx.fillRect(0, 0, 1, 1);
                ctx.fillStyle = style;
            }}
            return origToDataURL.apply(this, arguments);
        }};

        // 8. Subtle AudioContext Frequency Jitter
        if (typeof AudioBuffer !== 'undefined') {{
            const origGetChannelData = AudioBuffer.prototype.getChannelData;
            AudioBuffer.prototype.getChannelData = function(channel) {{
                const data = origGetChannelData.apply(this, arguments);
                if (data && data.length > 0) {{
                    data[0] = data[0] + {fp["audio_jitter"]};
                }}
                return data;
            }};
        }}

        // 9. Permissions Mock
        try {{
            const origQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (param) => (
                param.name === 'notifications' ?
                    Promise.resolve({{ state: Notification.permission }}) :
                    origQuery(param)
            );
        }} catch(e) {{}}

        // 10. Auto-hide residual GDPR consent bump
        // 10. Auto-hide residual GDPR consent bump & YouTube Premium / promo modals
        window.addEventListener('DOMContentLoaded', () => {{
            try {{
                const s = document.createElement('style');
                s.innerHTML = `
                    ytd-consent-bump-v2-lightbox,
                    tp-yt-paper-dialog:has(#consent-bump),
                    .opened:has(#consent-bump),
                    ytd-popup-container:has(ytd-consent-bump-v2-lightbox),
                    ytd-mealbar-promo-renderer,
                    #mealbar-promo-renderer,
                    ytd-popup-container:has(ytd-mealbar-promo-renderer),
                    tp-yt-paper-dialog:has(ytd-mealbar-promo-renderer),
                    yt-mealbar-promo-renderer,
                    ytd-modal-with-title-and-button-renderer,
                    ytd-popup-container:has(ytd-modal-with-title-and-button-renderer),
                    tp-yt-iron-overlay-backdrop {{
                        display: none !important;
                    }}
                `;
                (document.head || document.documentElement).appendChild(s);
            }} catch(e) {{}}

            // Auto-click dismiss buttons in background
            setInterval(() => {{
                try {{
                    const dismissSelectors = [
                        '#dismiss-button button',
                        '#dismiss-button',
                        'button[aria-label*="Dismiss"]',
                        'button[aria-label*="Batal"]',
                        'ytd-button-renderer:has-text("Lain kali") button',
                        'yt-button-shape:has-text("Lain kali") button'
                    ];
                    for (const sel of dismissSelectors) {{
                        const el = document.querySelector(sel);
                        if (el && typeof el.click === 'function') {{
                            el.click();
                        }}
                    }}
                }} catch(e) {{}}
            }}, 1500);
        }});
    """

def launch_isolated_context(p, headless: bool, proxy_url: str | None, user_data_dir: str, fp: dict, mute_audio: bool = True, worker_id: int = 1):
    """Launch isolated persistent context with dynamic fingerprint, anti-block flags, and auto-tiling in headed mode."""
    win_w = 640
    win_h = 480
    browser_args = [
        "--disable-blink-features=AutomationControlled",
        "--test-type",
        "--disable-infobars",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-background-timer-throttling",
        "--disable-client-side-phishing-detection",
        "--disable-default-apps",
        "--disable-hang-monitor",
        "--disable-popup-blocking",
        "--disable-sync",
        "--disable-translate",
        "--no-first-run",
        "--safebrowsing-disable-auto-update",
        "--ignore-certificate-errors",
        "--autoplay-policy=no-user-gesture-required",
        # ANTI-CDN BLOCK: Paksa TCP (Non-QUIC). Proxy HTTP/SOCKS tidak mendukung UDP/QUIC ke googlevideo.com
        "--disable-quic",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        # MEMORY & CPU OPTIMIZATIONS (Ultra Ringan untuk VPS)
        "--js-flags=--max-old-space-size=128",
        "--disable-gpu",
        "--disable-software-rasterizer",
        "--disk-cache-size=10485760",
        "--media-cache-size=10485760",
        "--disable-component-update",
        "--disable-domain-reliability",
        "--disable-features=AudioServiceOutOfProcess,IsolateOrigins,site-per-process,MediaRouter,Translate,OptimizationHints"
    ]

    if not headless:
        # Auto-tiling grid: 3 kolom x N baris, tertata rapi di layar
        cols = 3
        col = (worker_id - 1) % cols
        row = (worker_id - 1) // cols
        pos_x = col * (win_w + 10) + 15
        pos_y = row * (win_h + 35) + 35
        browser_args.append(f"--window-size={win_w},{win_h}")
        browser_args.append(f"--window-position={pos_x},{pos_y}")
        actual_vp = {"width": win_w, "height": win_h - 60}
    else:
        browser_args.append(f"--window-size={fp['screen_w']},{fp['screen_h']}")
        actual_vp = fp["vp"]

    if mute_audio:
        browser_args.append("--mute-audio")

    proxy_cfg = None
    if proxy_url:
        parsed = urlparse(proxy_url)
        server_str = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
        proxy_cfg = {"server": server_str}
        if parsed.username:
            proxy_cfg["username"] = parsed.username
        if parsed.password:
            proxy_cfg["password"] = parsed.password

    launch_opts = {
        "user_data_dir": user_data_dir,
        "headless": headless,
        "args": browser_args,
        "ignore_default_args": ["--enable-automation"],
        "proxy": proxy_cfg,
        "viewport": actual_vp,
        "user_agent": fp["ua"],
        "locale": fp["languages"][0],
        "timezone_id": "UTC",
        "ignore_https_errors": True
    }

    try:
        ctx = p.chromium.launch_persistent_context(channel="chrome", **launch_opts)
    except Exception:
        ctx = p.chromium.launch_persistent_context(**launch_opts)

    return ctx

def human_type(page, selector: str, text: str, min_delay_ms: int = 50, max_delay_ms: int = 120):
    element = page.locator(selector).first
    element.click()
    time.sleep(random.uniform(0.1, 0.2))
    for char in text:
        element.press_sequentially(char)
        time.sleep(random.uniform(min_delay_ms / 1000.0, max_delay_ms / 1000.0))

def handle_consent_popup(page):
    """Handle and auto-dismiss any Google/YouTube GDPR consent or YouTube Premium / promo dialogs."""
    # 0. Cek & dismiss YouTube Premium / Mealbar Promo modal
    try:
        promo_selectors = [
            "#dismiss-button button",
            "#dismiss-button",
            "ytd-button-renderer:has-text('Lain kali')",
            "button:has-text('Lain kali')",
            "button:has-text('No thanks')",
            "button:has-text('Dismiss')",
            "button:has-text('Batal')",
            "button:has-text('Not now')",
            "ytd-mealbar-promo-renderer #dismiss-button",
            "tp-yt-paper-dialog #dismiss-button"
        ]
        for p_sel in promo_selectors:
            p_btn = page.locator(p_sel).first
            if p_btn.is_visible(timeout=150):
                p_btn.click()
                time.sleep(0.3)
                break
    except Exception:
        pass

    # 1. Cek redirect halaman penuh ke consent.youtube.com / consent.google.com
    try:
        cur_url = page.url.lower()
        if "consent.youtube.com" in cur_url or "consent.google.com" in cur_url:
            for c_sel in [
                "button[aria-label*='Accept']",
                "button[aria-label*='Reject']",
                "button:has-text('Accept all')",
                "button:has-text('Reject all')",
                "button:has-text('I agree')",
                "form[action*='consent'] button",
                "form button"
            ]:
                try:
                    c_btn = page.locator(c_sel).first
                    if c_btn.is_visible(timeout=400):
                        c_btn.click()
                        time.sleep(1.0)
                        return True
                except Exception:
                    pass
    except Exception:
        pass

    # 2. Cek iframes jika consent dimuat dalam iframe
    for frame in page.frames:
        for sel in ["button:has-text('Reject all')", "button:has-text('Accept all')", "button:has-text('Alle akzeptieren')", "button:has-text('Setuju semua')"]:
            try:
                btn = frame.locator(sel).first
                if btn.is_visible(timeout=300):
                    btn.click()
                    time.sleep(0.5)
                    return True
            except Exception:
                pass

    # 3. Cek halaman utama
    possible_selectors = [
        "button:has-text('Reject all')",
        "button:has-text('Accept all')",
        "button:has-text('I agree')",
        "button:has-text('Accept the use of cookies')",
        "button[aria-label*='Accept']",
        "button[aria-label*='Reject']",
        "button[aria-label*='Setuju']",
        "button[aria-label*='Tolak']",
        "button:has-text('Alle akzeptieren')",
        "button:has-text('Tout accepter')",
        "button:has-text('Aceptar todo')",
        "button:has-text('Setuju semua')",
        "button:has-text('Saya setuju')",
        "button:has-text('Terima semua')",
        "form[action*='consent'] button",
        "ytd-button-renderer:has-text('Accept all')",
        "ytd-button-renderer:has-text('Reject all')",
        "ytd-button-renderer:has-text('Setuju semua')",
        "tp-yt-paper-dialog button"
    ]
    for sel in possible_selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=300):
                btn.click()
                time.sleep(0.5)
                return True
        except Exception:
            continue
    return False

def safe_evaluate(page, script: str, arg=None, retries: int = 3, delay: float = 1.0):
    """
    Menjalankan page.evaluate dengan penanganan tahan banting terhadap:
    'Execution context was destroyed, most likely because of a navigation'.
    Jika terjadi perpindahan navigasi halaman saat evaluasi berjalan,
    fungsi ini akan menunggu context baru siap dan mengulanginya otomatis.
    """
    for attempt in range(retries):
        try:
            if arg is not None:
                return page.evaluate(script, arg)
            return page.evaluate(script)
        except Exception as e:
            err_msg = str(e).lower()
            if "execution context was destroyed" in err_msg or "navigat" in err_msg or "context destroyed" in err_msg:
                time.sleep(delay)
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=4000)
                except Exception:
                    pass
                if attempt < retries - 1:
                    continue
            return None
    return None

def find_channel_videos(page, target_chan: str = "horrornologi"):
    """
    Filter hasil pencarian YouTube secara KETAT:
    - Hanya mengambil video ASLI dari channel target ('horrornologi').
    - Mengabaikan video channel lain (seperti NOICE, Liminal Spaces, Info Giraffe, dll).
    - Mengabaikan YouTube Shorts dan video pendek (< 5 menit).
    - Memverifikasi link handle @horrornologi dan teks nama channel secara presisi.
    """
    target = target_chan.lower().strip()
    videos_info = safe_evaluate(page, """(targetChannel) => {
        const results = [];
        const items = document.querySelectorAll('ytd-video-renderer');
        
        // Judul resmi 5 video recap Horrornologi
        const knownHorrorKeywords = [
            'i am a hero',
            'bitten by a zombie',
            'badarawuhi',
            'never step inside here',
            'jackie chan'
        ];
        
        items.forEach((item, index) => {
            const titleEl = item.querySelector('a#video-title');
            if (!titleEl) return;
            
            let title = (titleEl.getAttribute('title') || titleEl.innerText || '').trim();
            title = title.replace(/\\n+/g, ' ').trim();
            if (title.length < 5 || title.includes('Now playing')) return;
            
            const href = titleEl.getAttribute('href') || '';
            if (!href.includes('/watch?v=')) return;
            if (href.includes('/shorts/')) return;
            
            // Periksa identifier channel dari kartu video
            let channelName = '';
            let channelHandle = '';
            
            const channelLinks = item.querySelectorAll('ytd-channel-name a, #channel-info a, #channel-name a, #byline-container a, a[href*="/@"]');
            channelLinks.forEach(a => {
                const h = (a.getAttribute('href') || '').toLowerCase();
                const t = (a.innerText || '').trim();
                if (h.includes('/@')) {
                    channelHandle = h;
                }
                if (t) {
                    channelName = t;
                }
            });
            
            if (!channelName) {
                const textEl = item.querySelector('ytd-channel-name #text, #channel-name #text, yt-formatted-string.ytd-channel-name, #byline');
                if (textEl) {
                    channelName = (textEl.innerText || '').trim();
                }
            }
            
            const channelLower = channelName.toLowerCase();
            
            // CEK ANTI-SALAH CHANNEL: Tolak keras channel lain
            const isExcluded = channelLower.includes('noice') ||
                               channelLower.includes('liminal') ||
                               channelLower.includes('giraffe') ||
                               channelLower.includes('sarjana') ||
                               channelLower.includes('ewing');
            if (isExcluded) return;
            
            // WAJIB channel Horrornologi! Tolak video orang lain tanpa terkecuali.
            const isChannelMatch = channelHandle.includes(targetChannel) || channelLower.includes(targetChannel);
            if (!isChannelMatch) {
                return;
            }
            
            const titleLower = title.toLowerCase();
            const isKnownRecap = knownHorrorKeywords.some(k => titleLower.includes(k));
            
            // Ekstrak badge durasi di thumbnail (misal: "17:23", "22:08", "1:05:41")
            let durationStr = '';
            const badgeEl = item.querySelector('ytd-thumbnail-overlay-time-status-renderer span#text, .badge-shape-wiz__text, badge-shape .badge-shape-wiz__text, #time-status');
            if (badgeEl) {
                durationStr = (badgeEl.innerText || '').trim();
            }
            
            let durationSec = 0;
            if (durationStr) {
                const parts = durationStr.split(':').map(p => parseInt(p, 10)).filter(p => !isNaN(p));
                if (parts.length === 2) {
                    durationSec = parts[0] * 60 + parts[1];
                } else if (parts.length === 3) {
                    durationSec = parts[0] * 3600 + parts[1] * 60 + parts[2];
                }
            }
            
            if (durationSec > 0 && durationSec < 300) {
                return;
            }
            
            results.push({
                index: index,
                title: title,
                channel: channelName || 'HORRORNOLOGI',
                href: href,
                duration_str: durationStr,
                duration_sec: durationSec,
                is_recap: isKnownRecap
            });
        });
        
        return results;
    }""", target)

    if videos_info:
        # Prioritaskan video recap resmi Horrornologi
        videos_info.sort(key=lambda v: (0 if v.get("is_recap") else 1))
        register_discovered_videos(videos_info, target_chan)

    return videos_info or []

RECENT_VIDEOS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".recent_videos.json")
VIDEO_STATS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "video_watch_stats.json")

def load_video_stats() -> dict:
    """Ambil data statistik frekuensi tontonan video dari file JSON."""
    if os.path.exists(VIDEO_STATS_FILE):
        try:
            with open(VIDEO_STATS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            pass
    return {}

def register_discovered_videos(videos: list, channel_name: str = "horrornologi"):
    """Daftarkan seluruh video yang terdeteksi dari channel ke database statistik agar terpantau."""
    if not videos:
        return
    try:
        stats = load_video_stats()
        changed = False
        for v in videos:
            href = v.get("href", "")
            title = v.get("title", "")
            if not href or not title:
                continue
            vid_id = href.split("&")[0].split("=")[-1] if "v=" in href else href
            if vid_id not in stats:
                stats[vid_id] = {
                    "id": vid_id,
                    "title": title,
                    "href": href,
                    "channel": channel_name,
                    "duration_str": v.get("duration_str", ""),
                    "duration_sec": v.get("duration_sec", 0),
                    "watch_count": 0,
                    "total_watch_seconds": 0.0,
                    "last_watched": 0,
                    "status": "BELUM"
                }
                changed = True
        if changed:
            tmp = VIDEO_STATS_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(stats, f, indent=2)
            os.replace(tmp, VIDEO_STATS_FILE)
    except Exception:
        pass

def record_video_watch_success(video_href: str, title: str, duration_sec: float = 0):
    """Update hitungan tontonan sukses untuk video tertentu."""
    if not video_href:
        return
    try:
        vid_id = video_href.split("&")[0].split("=")[-1] if "v=" in video_href else video_href
        stats = load_video_stats()
        cur = stats.get(vid_id, {
            "id": vid_id,
            "title": title,
            "href": video_href,
            "channel": "horrornologi",
            "duration_str": "",
            "duration_sec": 0,
            "watch_count": 0,
            "total_watch_seconds": 0.0,
            "last_watched": 0,
            "status": "BELUM"
        })
        cur["title"] = title or cur.get("title", vid_id)
        cur["href"] = video_href
        cur["watch_count"] = cur.get("watch_count", 0) + 1
        cur["total_watch_seconds"] = cur.get("total_watch_seconds", 0.0) + duration_sec
        cur["last_watched"] = time.time()
        cur["status"] = "SUKSES"

        stats[vid_id] = cur
        tmp = VIDEO_STATS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2)
        os.replace(tmp, VIDEO_STATS_FILE)
    except Exception:
        pass

def get_recent_videos() -> list:
    """Ambil riwayat video yang baru saja ditonton untuk mencegah pengulangan video yang sama."""
    if os.path.exists(RECENT_VIDEOS_FILE):
        try:
            with open(RECENT_VIDEOS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception:
            pass
    return []

def record_watched_video(video_href: str, title: str):
    """Simpan ID video yang baru saja ditonton ke riwayat rotasi."""
    if not video_href:
        return
    try:
        recents = get_recent_videos()
        vid_key = video_href.split("&")[0].split("=")[-1] if "v=" in video_href else video_href
        recents = [r for r in recents if r.get("id") != vid_key]
        recents.append({"id": vid_key, "title": title[:50], "time": time.time()})
        if len(recents) > 4:
            recents = recents[-4:]
        tmp = RECENT_VIDEOS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(recents, f, indent=2)
        os.replace(tmp, RECENT_VIDEOS_FILE)
    except Exception:
        pass

def choose_least_recent_video(candidates: list) -> dict | None:
    """
    Pilih video secara pintar agar frekuensi tayang seimbang & adil:
    1. Ambil data frekuensi watch_count dari database video_watch_stats.json.
    2. Utamakan video dengan watch_count paling sedikit (video yang belum pernah/jarang ditonton).
    3. Di antara kandidat dengan watch_count terendah, hindari 4 video yang baru saja ditonton.
    """
    if not candidates:
        return None
    stats = load_video_stats()
    recents = get_recent_videos()
    recent_ids = {r.get("id") for r in recents}

    def score(c):
        href = c.get("href", "")
        v_id = href.split("&")[0].split("=")[-1] if "v=" in href else href
        watch_cnt = stats.get(v_id, {}).get("watch_count", 0)
        # Penalti tinggi jika baru saja ditonton di 4 sesi terakhir
        is_recent = 1000 if v_id in recent_ids else 0
        # Tambahkan sedikit random jitter agar video dengan frekuensi sama tidak dipilih secara statis
        return watch_cnt + is_recent + random.uniform(0.0, 0.4)

    sorted_candidates = sorted(candidates, key=score)
    return sorted_candidates[0]

def find_videos_on_channel_page(page, target_chan: str = "horrornologi") -> list:
    """
    Ekstrak seluruh katalog video resmi di tab Videos channel Horrornologi.
    Mendukung komponen modern YouTube (yt-lockup-view-model) dan layout klasik.
    Memfilter video pendek (< 5 menit) dan hanya mengambil film recap panjang.
    Memverifikasi URL halaman adalah channel target.
    """
    target = target_chan.lower().strip()
    if target not in (page.url or "").lower():
        return []

    try:
        page.wait_for_selector('yt-lockup-view-model, ytd-rich-item-renderer, a[href*="/watch?v="]', state='visible', timeout=6000)
    except Exception:
        pass

    videos = safe_evaluate(page, """(targetChan) => {
        const list = [];
        const seen = new Set();
        
        const cards = document.querySelectorAll('yt-lockup-view-model, ytd-rich-item-renderer, ytd-grid-video-renderer');
        cards.forEach(card => {
            const watchLinks = card.querySelectorAll('a[href*="/watch?v="]');
            if (!watchLinks || watchLinks.length === 0) return;
            
            let href = '';
            let title = '';
            let durStr = '';
            
            watchLinks.forEach(a => {
                const h = a.getAttribute('href') || '';
                const t = (a.getAttribute('title') || a.getAttribute('aria-label') || a.innerText || '').trim();
                if (h.includes('/watch?v=')) {
                    href = h.split('&')[0];
                }
                if (t && t.length > 5 && !/^[0-9:]+$/.test(t)) {
                    title = t;
                } else if (/^[0-9:]+$/.test(t)) {
                    durStr = t;
                }
            });
            
            if (!href || seen.has(href)) return;
            
            title = title.replace(/\\s+[0-9]+\\s+(minutes|detik|menit|hours|jam).*$/i, '').trim();
            title = title.replace(/\\n+/g, ' ').trim();
            if (title.length < 5) return;
            
            seen.add(href);
            
            if (!durStr) {
                const badge = card.querySelector('ytd-thumbnail-overlay-time-status-renderer span#text, .badge-shape-wiz__text, badge-shape div, #time-status');
                if (badge) durStr = (badge.innerText || '').trim();
            }
            
            let durSec = 0;
            if (durStr) {
                const parts = durStr.split(':').map(p => parseInt(p, 10)).filter(p => !isNaN(p));
                if (parts.length === 2) durSec = parts[0] * 60 + parts[1];
                else if (parts.length === 3) durSec = parts[0] * 3600 + parts[1] * 60 + parts[2];
            }
            
            if (durSec === 0 || durSec >= 300) {
                list.push({
                    title: title,
                    href: href,
                    duration_str: durStr,
                    duration_sec: durSec,
                    channel: 'HORRORNOLOGI'
                });
            }
        });
        return list;
    }""", target, retries=3, delay=1.2)
    if videos:
        register_discovered_videos(videos, target_chan)
    return videos or []

def navigate_to_channel_from_search(page, target_chan: str = "horrornologi") -> bool:
    """
    Berpindah ke halaman channel creator resmi Horrornologi (Tab Videos).
    Memverifikasi secara ketat bahwa channel yang dikunjungi adalah benar-benar Horrornologi.
    """
    target = target_chan.lower().strip()
    
    # 1. Cari kartu channel resmi di bagian atas hasil pencarian
    clicked = False
    try:
        loc = page.locator(f"ytd-channel-renderer a[href*='{target}'], ytd-channel-renderer:has-text('{target}') a").first
        if loc.is_visible(timeout=1500):
            loc.scroll_into_view_if_needed()
            time.sleep(0.4)
            loc.click()
            for _ in range(8):
                time.sleep(0.5)
                if target in (page.url or "").lower():
                    clicked = True
                    break
    except Exception:
        pass

    # 2. Jika tidak terklik atau URL saat ini bukan channel target, buka langsung tab /videos channel target
    curr_url = (page.url or "").lower()
    if not clicked or target not in curr_url:
        try:
            page.goto(f"https://www.youtube.com/@{target}/videos", wait_until="domcontentloaded", timeout=25000)
            time.sleep(1.5)
        except Exception:
            return False

    # 3. VERIFIKASI KETAT: Pastikan URL dan konten halaman adalah benar channel Horrornologi!
    curr_url = (page.url or "").lower()
    if target not in curr_url:
        return False

    # Pastikan berada di tab /videos
    if "/videos" not in curr_url:
        try:
            tab_loc = page.locator("yt-tab-shape[tab-title='Videos'], tp-yt-paper-tab:has-text('Videos'), a[href*='/videos']").first
            if tab_loc.is_visible(timeout=1500):
                tab_loc.click()
                time.sleep(1.5)
            else:
                page.goto(f"https://www.youtube.com/@{target}/videos", wait_until="domcontentloaded", timeout=20000)
                time.sleep(1.5)
        except Exception:
            page.goto(f"https://www.youtube.com/@{target}/videos", wait_until="domcontentloaded", timeout=20000)
            time.sleep(1.5)

    return target in (page.url or "").lower()

def verify_watch_page_channel(page, target_chan: str = "horrornologi", max_retries: int = 5) -> tuple[bool, str]:
    """
    Verifikasi KETAT channel pemilik video langsung di halaman tontonan (/watch?v=...).
    100% Memastikan video HANYA milik channel Horrornologi.
    Menolak keras video dari channel lain (Liminal Spaces, Info Giraffe, NOICE, dll).
    """
    target = target_chan.lower().strip()
    for _ in range(max_retries):
        try:
            res = page.evaluate("""(target) => {
                // 1. Cek dari window.ytInitialPlayerResponse (paling cepat dan akurat)
                try {
                    const pr = window.ytInitialPlayerResponse;
                    if (pr && pr.videoDetails && pr.videoDetails.author) {
                        const author = pr.videoDetails.author.trim();
                        const isMatch = author.toLowerCase().includes(target);
                        return { found: true, name: author, match: isMatch };
                    }
                } catch(e) {}

                // 2. Cek dari schema/meta tag di head
                try {
                    const metaAuthor = document.querySelector('span[itemprop="author"] link[itemprop="name"], meta[itemprop="name"]');
                    if (metaAuthor && metaAuthor.getAttribute('content')) {
                        const author = metaAuthor.getAttribute('content').trim();
                        const isMatch = author.toLowerCase().includes(target);
                        return { found: true, name: author, match: isMatch };
                    }
                } catch(e) {}

                // 3. Cek dari DOM #owner di bawah video player
                const ownerEl = document.querySelector('ytd-watch-metadata #owner, #above-the-fold #owner, ytd-video-owner-renderer');
                if (ownerEl) {
                    const nameEl = ownerEl.querySelector('ytd-channel-name #text, #channel-name a, a[href*="/@"]');
                    const name = (nameEl ? nameEl.innerText : ownerEl.innerText || '').trim();
                    const handleEl = ownerEl.querySelector('a[href*="/@"]');
                    const handle = (handleEl ? handleEl.getAttribute('href') : '').toLowerCase();
                    
                    if (name || handle) {
                        const isMatch = name.toLowerCase().includes(target) || handle.includes(target);
                        return { found: true, name: name.split('\\n')[0] || handle, match: isMatch };
                    }
                }
                
                return { found: false, name: '', match: false };
            }""", target)

            if res.get("found"):
                channel_name = str(res.get("name", "Unknown"))
                is_match = bool(res.get("match"))
                return is_match, channel_name
                
        except Exception:
            pass

        time.sleep(1.0)

    # Jika setelah retries tetap tidak terverifikasi sebagai channel target, TOLAK!
    return False, "Unverified/Non-Target Channel"

def check_bot_block(page) -> bool:
    """Detect YouTube's 'Sign in to confirm you're not a bot' enforcement screen."""
    try:
        return bool(page.evaluate("""() => {
            const body = (document.body ? document.body.innerText : '').toLowerCase();
            if (body.includes("confirm you're not a bot") ||
                body.includes("sign in to confirm you're not a bot") ||
                body.includes("our systems have detected unusual traffic") ||
                body.includes("unusual traffic from your computer network") ||
                body.includes("this helps protect our community") ||
                body.includes("bukan bot") ||
                body.includes("lalu lintas tidak biasa")) {
                return true;
            }
            const el = document.querySelector('ytd-enforcement-message-view-model, yt-playability-error-supported-renderers, #player-error-message-container');
            if (el && (el.innerText || '').toLowerCase().includes('not a bot')) {
                return true;
            }
            return false;
        }"""))
    except Exception:
        return False

def check_and_skip_ads(page, ad_state: dict | None = None) -> tuple[bool, str | None]:
    """
    AdSense Natural Retention & Type Classifier:
    - Mendeteksi 2 macam tipe iklan YouTube:
      1. SKIPPABLE: Memiliki tombol skip atau countdown lewati ("Skip in 5s").
         Dilewati secara alami setelah 7 - 14 detik agar pengiklan ditagih (billable CPV).
      2. NON-SKIPPABLE: Iklan bumper (6s) atau non-skip (15s) tanpa tombol lewati ("Video will play after ads").
         Ditonton penuh sampai selesai.
    Mengembalikan (True, "skippable") atau (True, "nonskippable") saat iklan tuntas,
    atau (False, None) saat tidak ada iklan selesai di detik ini.
    """
    if ad_state is None:
        ad_state = {}

    ad_skip_selectors = [
        ".ytp-skip-ad-button",
        ".ytp-ad-skip-button-modern",
        "button.ytp-ad-skip-button",
        ".ytp-ad-skip-button-container button",
        ".ytp-ad-skip-button-slot button",
        "button[id*='skip-button']"
    ]

    ad_info = {"active": False, "skippable": False}
    try:
        ad_info = page.evaluate("""() => {
            const player = document.querySelector('#movie_player');
            if (!player) return { active: false, skippable: false };
            const active = player.classList.contains('ad-showing') || player.classList.contains('ad-interrupting');
            if (!active) return { active: false, skippable: false };

            const skipBtn = document.querySelector('.ytp-skip-ad-button, .ytp-ad-skip-button-modern, button.ytp-ad-skip-button, .ytp-ad-skip-button-slot button, .ytp-ad-skip-button-container button, button[id*="skip-button"]');
            const preview = document.querySelector('.ytp-ad-preview-text, .ytp-ad-preview-container, .ytp-ad-text');
            const previewText = preview ? (preview.innerText || '').toLowerCase() : '';
            const hasSkipCountdown = previewText.includes('skip') || previewText.includes('lewati') || previewText.includes('you can skip');

            return {
                active: true,
                skippable: Boolean(skipBtn || hasSkipCountdown)
            };
        }""")
    except Exception:
        pass

    now = time.time()
    is_ad_active = ad_info.get("active", False)

    if is_ad_active:
        if "ad_start_time" not in ad_state:
            ad_state["ad_start_time"] = now
            ad_state["target_ad_watch"] = random.uniform(7.0, 14.0)
            ad_state["allow_full_play"] = random.random() < 0.20
            ad_state["is_skippable"] = ad_info.get("skippable", False)

        if ad_info.get("skippable"):
            ad_state["is_skippable"] = True

        # Cek tombol lewati iklan
        for sel in ad_skip_selectors:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=100):
                    ad_state["is_skippable"] = True
                    elapsed_ad = now - ad_state.get("ad_start_time", now)
                    target_watch = ad_state.get("target_ad_watch", 8.0)

                    # Jika mode allow_full_play aktif, biarkan iklan pendek selesai kecuali sudah > 25 detik
                    if ad_state.get("allow_full_play", False) and elapsed_ad < 25.0:
                        return False, None

                    if elapsed_ad >= target_watch:
                        btn.click()
                        time.sleep(0.3)
                        ad_state.pop("ad_start_time", None)
                        ad_state.pop("is_skippable", None)
                        return True, "skippable"
                    else:
                        # Sedang menonton iklan untuk validasi AdSense
                        return False, None
            except Exception:
                continue

        return False, None
    else:
        # Jika iklan baru saja selesai berputar secara alami
        if "ad_start_time" in ad_state:
            ad_state.pop("ad_start_time", None)
            was_skippable = ad_state.pop("is_skippable", False)
            return True, ("skippable" if was_skippable else "nonskippable")

    return False, None

def optimize_playback(page):
    try:
        page.evaluate("""() => {
            const player = document.querySelector('#movie_player');
            if (player) {
                if (typeof player.setPlaybackQualityRange === 'function') {
                    player.setPlaybackQualityRange('tiny', 'tiny');
                }
                if (typeof player.setPlaybackQuality === 'function') {
                    player.setPlaybackQuality('tiny');
                }
            }
        }""")
    except Exception:
        pass

def check_player_error(page) -> bool:
    """Check if YouTube player encountered a fatal error (e.g. 'Something went wrong')."""
    if check_bot_block(page):
        return True
    try:
        return bool(page.evaluate("""() => {
            const errorSelectors = [
                '.ytp-error',
                '.ytp-error-content',
                'ytd-player-error-message-renderer',
                'div[class*="ytp-error"]',
                'ytd-enforcement-message-view-model',
                'yt-playability-error-supported-renderers'
            ];
            for (const sel of errorSelectors) {
                const el = document.querySelector(sel);
                if (el && (el.offsetParent !== null || window.getComputedStyle(el).display !== 'none')) {
                    return true;
                }
            }
            const player = document.querySelector('#movie_player');
            if (player) {
                const txt = (player.innerText || '').toLowerCase();
                if (txt.includes('something went wrong') ||
                    txt.includes('terjadi kesalahan') ||
                    txt.includes('terjadi error') ||
                    txt.includes('id pemutaran') ||
                    txt.includes('coba lagi nanti') ||
                    txt.includes('pelajari lebih lanjut') ||
                    txt.includes('refresh or try again') ||
                    txt.includes('playback error') ||
                    txt.includes('an error occurred')) {
                    return true;
                }
            }
            const video = document.querySelector('video.html5-main-video') || document.querySelector('video');
            if (video && video.error) {
                return true;
            }
            return false;
        }"""))
    except Exception:
        return False

def watch_video_with_lock(page, worker_name: str, video_title: str, min_percent: float, max_percent: float, max_cap_sec: int | None, worker_id: int = 1, expected_duration: float = 0, target_chan: str = "horrornologi"):
    """
    SYSTEM WATCH LOCK:
    Locks the browser in playback mode.
    Auto-detects YouTube player errors ('Something went wrong' / Google Video CDN blocks).
    Recovers or triggers instant proxy rotation so no time is wasted on dead streams.
    """
    safe_log(worker_name, f"🔒 [SYSTEM LOCK AKTIF] Memulai sesi tonton video...", GREEN)

    # 1. Tunggu video element ready
    try:
        page.wait_for_selector("video.html5-main-video", timeout=12000)
    except Exception:
        pass

    # 2. Cek apakah layar diblokir oleh Bot Challenge ('Sign in to confirm you're not a bot')
    if check_bot_block(page):
        safe_log(worker_name, "🚨 Terdeteksi blokir bot YouTube ('Sign in to confirm you're not a bot')! Melewati proxy...", RED)
        return False, 0, 0, 0, "BOT_BLOCKED"

    # 3. VERIFIKASI KETAT CHANNEL PEMILIK VIDEO (WAJIB HORRORNOLOGI)
    is_our_chan, found_chan = verify_watch_page_channel(page, target_chan=target_chan)
    if not is_our_chan:
        safe_log(worker_name, f"❌ DITOLAK! Video ini dimiliki oleh '{found_chan}', BUKAN {target_chan.upper()}! Menolak menonton video sembarang.", RED)
        return False, 0, 0, 0, "WRONG_CHANNEL"

    # 4. Cek apakah langsung terkena error 'Something went wrong'
    if check_player_error(page):
        safe_log(worker_name, "⚠️ Terdeteksi error player ('Something went wrong'). Coba auto-recover...", YELLOW)
        try:
            retry_btn = page.locator(".ytp-error button, .ytp-retry-button").first
            if retry_btn.is_visible(timeout=1000):
                retry_btn.click()
                time.sleep(1.5)

            # Lompat maju 3 detik untuk melewati segmen buffer yang rusak
            page.evaluate("""() => {
                const v = document.querySelector('video');
                if (v) { v.currentTime += 3; v.play().catch(() => {}); }
            }""")
            time.sleep(1.5)

            if check_player_error(page):
                page.reload(wait_until="domcontentloaded")
                time.sleep(3.0)
        except Exception:
            pass

        if check_player_error(page):
            safe_log(worker_name, "❌ Proxy diblokir Google Video CDN (Gagal stream media / 'Something went wrong').", RED)
            return False, 0, 0, 0, "STREAMING_ERROR"

    # Play video
    try:
        page.evaluate("""() => {
            const v = document.querySelector('video.html5-main-video') || document.querySelector('video');
            if (v && v.paused) v.play().catch(() => {});
        }""")
    except Exception:
        pass

    optimize_playback(page)

    # Tunggu total duration terbaca
    total_duration = 0
    ad_state = {}
    skippable_ads_count = 0
    nonskippable_ads_count = 0
    for _ in range(15):
        had_ad, ad_type = check_and_skip_ads(page, ad_state)
        if had_ad:
            if ad_type == "skippable":
                skippable_ads_count += 1
            else:
                nonskippable_ads_count += 1

        # Cek player error di tengah persiapan
        if check_player_error(page):
            safe_log(worker_name, "❌ Player mengalami error saat buffering stream ('Something went wrong').", RED)
            return False, 0, 0, 0, "STREAMING_ERROR"

        try:
            duration_val = page.evaluate("""() => {
                const player = document.querySelector('#movie_player');
                if (player && typeof player.getDuration === 'function') {
                    const d = player.getDuration();
                    if (d && !isNaN(d) && d > 0) return d;
                }
                const v = document.querySelector('video.html5-main-video') || document.querySelector('video');
                return (v && !isNaN(v.duration) && v.duration > 0) ? v.duration : 0;
            }""")
            if duration_val and duration_val > 0:
                d = float(duration_val)
                # Jika expected_duration diketahui > 300 detik dan d < 90 detik, ini kemungkinan durasi iklan
                if expected_duration > 300 and d < 90:
                    time.sleep(1)
                    continue
                total_duration = d
                break
        except Exception:
            pass
        time.sleep(1)

    if total_duration <= 0:
        if expected_duration and expected_duration > 0:
            total_duration = float(expected_duration)
            safe_log(worker_name, f"Durasi diambil dari badge thumbnail video: {format_time(total_duration)}", CYAN)
        else:
            # Fallback realistis untuk video recap Horrornologi (18 - 22 menit), BUKAN 3 menit!
            total_duration = random.uniform(1080, 1320)
            safe_log(worker_name, f"Menggunakan estimasi rata-rata video Horrornologi: {format_time(total_duration)}", CYAN)

    chosen_percent = random.uniform(min_percent, max_percent)
    target_watch_sec = total_duration * (chosen_percent / 100.0)

    if max_cap_sec and target_watch_sec > max_cap_sec:
        target_watch_sec = max_cap_sec
        chosen_percent = (target_watch_sec / total_duration) * 100.0

    target_watch_sec = max(15.0, target_watch_sec)

    safe_log(worker_name, f"🔒 Terkunci Menonton: '{video_title[:38]}...' | Durasi: {format_time(total_duration)} | Target: {format_time(target_watch_sec)} ({chosen_percent:.1f}%)", MAGENTA)

    start_time = time.time()
    last_action = start_time
    next_action_delay = random.uniform(30, 60)
    consecutive_stall = 0
    last_cur_time = -1
    last_cur_time_stuck_start = None

    send_telemetry(
        worker_id,
        "watch_start",
        title=video_title,
        total_duration=total_duration,
        target_duration=target_watch_sec,
        target_percent=chosen_percent,
        status="MENONTON",
        details=f"Target: {format_time(target_watch_sec)} ({chosen_percent:.1f}%)"
    )

    # Strict watching loop: protected against minor exceptions
    while True:
        elapsed = time.time() - start_time
        if elapsed >= target_watch_sec:
            break

        if page.is_closed():
            safe_log(worker_name, f"Browser ditutup manual sebelum target ({format_time(elapsed)}/{format_time(target_watch_sec)}).", YELLOW)
            send_telemetry(worker_id, "status_change", status="DITUTUP", details="Browser ditutup manual")
            return False, elapsed, chosen_percent, total_duration, "CLOSED"

        # Safely check ads & play status with natural AdSense retention (Skippable vs Non-Skippable)
        try:
            had_ad, ad_type = check_and_skip_ads(page, ad_state)
            if had_ad:
                if ad_type == "skippable":
                    skippable_ads_count += 1
                    safe_log(worker_name, f"⏭️ Iklan SKIPPABLE dilewati sah! (Total: {skippable_ads_count} Skip | {nonskippable_ads_count} Non-Skip)", YELLOW)
                else:
                    nonskippable_ads_count += 1
                    safe_log(worker_name, f"⏳ Iklan NON-SKIPPABLE ditonton penuh! (Total: {skippable_ads_count} Skip | {nonskippable_ads_count} Non-Skip)", PEACH)

                send_telemetry(
                    worker_id,
                    "ad_skipped",
                    ad_count=skippable_ads_count + nonskippable_ads_count,
                    ad_type=ad_type
                )
        except Exception:
            pass

        # Update total_duration jika awalnya membaca durasi iklan atau estimasi, dan sekarang video utama sudah jalan
        if int(elapsed) % 5 == 0 and total_duration < 600:
            try:
                real_dur = page.evaluate("""() => {
                    const player = document.querySelector('#movie_player');
                    if (player && typeof player.getDuration === 'function') {
                        const d = player.getDuration();
                        if (d && !isNaN(d) && d > 300) return d;
                    }
                    return 0;
                }""")
                if real_dur and float(real_dur) > total_duration:
                    total_duration = float(real_dur)
                    target_watch_sec = total_duration * (chosen_percent / 100.0)
                    if max_cap_sec and target_watch_sec > max_cap_sec:
                        target_watch_sec = max_cap_sec
                        chosen_percent = (target_watch_sec / total_duration) * 100.0
                    safe_log(worker_name, f"Durasi video terverifikasi dari player: {format_time(total_duration)} | Target: {format_time(target_watch_sec)}", CYAN)
            except Exception:
                pass

        # LIVE WATCHDOG & ANTI-FREEZE GUARD: Pantau pergerakan detik video asli
        current_play_time = 0
        try:
            play_info = page.evaluate("""() => {
                const v = document.querySelector('video.html5-main-video') || document.querySelector('video');
                const player = document.querySelector('#movie_player');
                const isAd = player ? (player.classList.contains('ad-showing') || player.classList.contains('ad-interrupting')) : false;
                return {
                    currentTime: v ? v.currentTime : 0,
                    paused: v ? v.paused : false,
                    isAd: isAd
                };
            }""")
            if play_info:
                current_play_time = play_info.get("currentTime", 0)
                is_ad = play_info.get("isAd", False)
                if not is_ad and current_play_time > 0:
                    if abs(current_play_time - last_cur_time) < 0.2:
                        if last_cur_time_stuck_start is None:
                            last_cur_time_stuck_start = time.time()
                        elif time.time() - last_cur_time_stuck_start > 8.0:
                            safe_log(worker_name, f"⚠️ Video freeze di {format_time(current_play_time)}. Memicu unfreeze...", YELLOW)
                            page.evaluate("""() => {
                                const v = document.querySelector('video');
                                if (v) { v.currentTime += 3; v.play().catch(()=>{}); }
                            }""")
                            last_cur_time_stuck_start = time.time()
                    else:
                        last_cur_time = current_play_time
                        last_cur_time_stuck_start = None
        except Exception:
            pass

        # Check player health periodically (e.g. proxy dropped connection mid-stream)
        if int(elapsed) % 4 == 0:
            handle_consent_popup(page)
            if check_bot_block(page):
                safe_log(worker_name, "🚨 Terdeteksi blokir bot YouTube di tengah pemutaran! Mengganti proxy...", RED)
                return False, elapsed, chosen_percent, total_duration, "BOT_BLOCKED"

            if check_player_error(page):
                consecutive_stall += 1
                safe_log(worker_name, f"⚠️ Buffer/player macet (percobaan pulih {consecutive_stall}/4)...", YELLOW)
                try:
                    # 1. Klik retry button jika muncul
                    r_btn = page.locator(".ytp-error button, .ytp-retry-button").first
                    if r_btn.is_visible(timeout=300):
                        r_btn.click()
                    # 2. Lompat maju 4 detik untuk melewati segmen DASH yang rusak/dropped
                    page.evaluate("""() => {
                        const v = document.querySelector('video');
                        if (v) {
                            v.currentTime += 4;
                            if (v.paused) v.play().catch(() => {});
                        }
                    }""")
                    optimize_playback(page)
                except Exception:
                    pass

                if consecutive_stall >= 4:
                    safe_log(worker_name, f"❌ Stream terputus di tengah pemutaran ('Something went wrong').", RED)
                    send_telemetry(worker_id, "status_change", status="ERROR", details="Stream terputus")
                    return False, elapsed, chosen_percent, total_duration, "STREAMING_ERROR"
            else:
                consecutive_stall = 0

        # Ensure video keeps playing (auto-resume if buffering paused it)
        try:
            page.evaluate("""() => {
                const v = document.querySelector('video.html5-main-video') || document.querySelector('video');
                if (v && v.paused) v.play().catch(() => {});
            }""")
        except Exception:
            pass

        # Subtle micro actions
        if time.time() - last_action > next_action_delay:
            last_action = time.time()
            next_action_delay = random.uniform(30, 60)
            try:
                page.mouse.move(random.randint(200, 600), random.randint(200, 450))
            except Exception:
                pass

        play_pos_str = f"Play: {format_time(current_play_time)} / {format_time(total_duration)}"
        total_ads = skippable_ads_count + nonskippable_ads_count
        send_telemetry(
            worker_id,
            "watch_progress",
            elapsed=elapsed,
            target=target_watch_sec,
            percent=chosen_percent,
            total_duration=total_duration,
            ad_count=total_ads,
            skippable_ads=skippable_ads_count,
            nonskippable_ads=nonskippable_ads_count,
            status="MENONTON",
            details=f"{play_pos_str} ({min(100.0, (elapsed/target_watch_sec)*100):.1f}%) [Ads: {skippable_ads_count} Skip | {nonskippable_ads_count} Non-Skip]"
        )

        time.sleep(1.0)

    safe_log(worker_name, f"✔ Target tonton ({chosen_percent:.1f}% / {format_time(target_watch_sec)}) tercapai! Melepas lock & auto-close.", GREEN)
    send_telemetry(worker_id, "watch_complete", status="SUKSES", details=f"Target selesai ({format_time(target_watch_sec)})")
    return True, elapsed, chosen_percent, total_duration, "SUCCESS"

def worker_process_main(worker_id: int, args, proxy_list: list, success_counter, stop_event, telemetry_queue=None):
    """Independent OS Process with fast pre-check and system watch lock."""
    if telemetry_queue is not None:
        set_global_telemetry_queue(telemetry_queue)

    worker_name = f"Process-{worker_id:02d}"
    pid = os.getpid()
    safe_log(worker_name, f"Worker berjalan di Proses PID: {pid}", CYAN)
    send_telemetry(worker_id, "init", pid=pid, status="SIAP", details=f"PID: {pid}")

    local_proxies = list(proxy_list)
    p_index = (worker_id * 11) % len(local_proxies) if local_proxies else 0
    last_proxy_mtime = 0

    while not stop_event.is_set():
        with success_counter.get_lock():
            if not args.infinite and success_counter.value >= args.batch_count:
                break

        # DYNAMIC HOT-RELOAD: Cek apakah file proxy diperbarui oleh proxy checker background
        if not args.direct:
            from geonode_fetcher import DEFAULT_OUTPUT_JSON, mark_proxy_dead, trigger_refill
            proxy_watch_file = os.path.abspath(args.proxy_file or DEFAULT_OUTPUT_JSON)
            if os.path.exists(proxy_watch_file):
                try:
                    curr_mtime = os.path.getmtime(proxy_watch_file)
                    if curr_mtime > last_proxy_mtime:
                        fresh = load_proxies(proxy_watch_file)
                        if fresh:
                            local_proxies = fresh
                            last_proxy_mtime = curr_mtime
                            safe_log(worker_name, f"🔄 Hot-reload: Proxy pool diperbarui ({len(local_proxies)} proxy aktif)", CYAN)
                except Exception:
                    pass

            # AUTO-REFILL TRIGGER: Jika stok proxy lokal menipis (< 15), picu refill instan dari Geonode/ProxyScrape
            if len(local_proxies) < 15:
                try:
                    trigger_refill()
                except Exception:
                    pass

            # Jika stok proxy lokal benar-benar habis, tunggu auto-refill stok baru
            if len(local_proxies) == 0:
                safe_log(worker_name, "⚠️ Proxy pool kosong! Memicu auto-refill & menunggu stok baru...", YELLOW)
                send_telemetry(worker_id, "status_change", status="REFILL_PROXY", details="Menunggu stok proxy baru...")
                trigger_refill()
                for _ in range(35):
                    if stop_event.is_set():
                        break
                    time.sleep(1.0)
                    if os.path.exists(proxy_watch_file):
                        try:
                            fresh = load_proxies(proxy_watch_file)
                            if fresh:
                                local_proxies = fresh
                                last_proxy_mtime = os.path.getmtime(proxy_watch_file)
                                safe_log(worker_name, f"🔄 Hot-reload berhasil: {len(local_proxies)} proxy baru siap!", GREEN)
                                break
                        except Exception:
                            pass

        # SMART PRE-CHECK: Cari proxy yang hidup via YouTube handshake SEBELUM membuka browser (Cepat & Non-Blocking)
        proxy_url = None
        if not args.direct and local_proxies:
            found_alive = False
            for _ in range(min(5, len(local_proxies))):
                candidate = local_proxies[p_index % len(local_proxies)]
                p_index += 1
                if is_proxy_alive(candidate, timeout=1.8):
                    proxy_url = candidate
                    found_alive = True
                    break
                else:
                    if candidate in local_proxies:
                        local_proxies.remove(candidate)
                    try:
                        from geonode_fetcher import mark_proxy_dead
                        mark_proxy_dead(candidate)
                    except Exception:
                        pass

            if not found_alive and local_proxies:
                proxy_url = local_proxies[p_index % len(local_proxies)]
                p_index += 1

        temp_profile_dir = tempfile.mkdtemp(prefix=f"yt_fresh_w{worker_id}_{os.getpid()}_{int(time.time()*1000)}_")
        fp = generate_unique_fingerprint()

        gpu_short = fp["webgl_vendor"].split(" ")[-1].replace("(", "").replace(")", "")
        safe_log(worker_name, f"Proxy: {proxy_url if proxy_url else 'Direct'} | 🎭 Persona: {fp['uadata_platform']} (Chrome {fp['major_ver']} | {gpu_short} | {fp['screen_w']}x{fp['screen_h']})", CYAN)
        send_telemetry(worker_id, "proxy_selected", proxy=proxy_url if proxy_url else "Direct", status="PROXY_TERPILIH", details=proxy_url if proxy_url else "Direct")

        session_success = False
        with sync_playwright() as p:
            context = launch_isolated_context(
                p, args.headless, proxy_url, temp_profile_dir, fp, mute_audio=not args.unmute, worker_id=worker_id
            )
            context.add_init_script(generate_stealth_script(fp))
            page = context.pages[0] if context.pages else context.new_page()

            # MEMORY OPTIMIZATION: Abort non-essential heavy fonts & external analytics
            def _filter_routes(route):
                req = route.request
                if req.resource_type == "font":
                    route.abort()
                elif any(d in req.url for d in ["google-analytics.com", "googletagmanager.com", "doubleclick.net"]):
                    route.abort()
                else:
                    route.continue_()

            try:
                page.route("**/*", _filter_routes)
            except Exception:
                pass

            try:
                search_kw = getattr(args, "keyword", "horrornologi")
                target_chan = getattr(args, "channel", "horrornologi")
                direct_search_url = f"https://www.youtube.com/results?search_query={search_kw}"

                if args.headed:
                    # Di mode Headed: coba beranda & ketik jika cepat, fallback ke search URL langsung jika lambat
                    try:
                        page.goto("https://www.youtube.com", wait_until="domcontentloaded", timeout=12000)
                        time.sleep(1.0)
                        handle_consent_popup(page)
                    except Exception:
                        pass

                    search_input_selectors = [
                        "input#search",
                        "input[name='search_query']",
                        "#search-input input"
                    ]
                    search_locator = None
                    for sel in search_input_selectors:
                        try:
                            loc = page.locator(sel).first
                            if loc.is_visible(timeout=500):
                                search_locator = sel
                                break
                        except Exception:
                            continue

                    if search_locator:
                        send_telemetry(worker_id, "status_change", status="MENCARI", details=f"Ketik keyword '{search_kw}'")
                        human_type(page, search_locator, search_kw, args.min_delay, args.max_delay)
                        time.sleep(0.3)
                        page.locator(search_locator).first.press("Enter")
                    else:
                        page.goto(direct_search_url, wait_until="domcontentloaded", timeout=20000)
                else:
                    # Headless mode: langsung ke hasil pencarian (cepat, hemat kuota & anti-timeout)
                    send_telemetry(worker_id, "status_change", status="MENCARI", details=f"Cari '{search_kw}'...")
                    page.goto(direct_search_url, wait_until="domcontentloaded", timeout=20000)
                    time.sleep(1.0)
                    handle_consent_popup(page)

                # Tunggu hasil pencarian selesai dimuat
                try:
                    page.wait_for_selector("ytd-video-renderer, ytd-two-column-search-results-renderer", state="visible", timeout=15000)
                except PlaywrightTimeoutError:
                    pass

                time.sleep(1.5)
                handle_consent_popup(page)

                if check_bot_block(page):
                    safe_log(worker_name, "🚨 IP Proxy diblokir bot challenge YouTube pada halaman pencarian. Mengganti proxy...", RED)
                    if proxy_url and proxy_url in local_proxies:
                        local_proxies.remove(proxy_url)
                    try:
                        from geonode_fetcher import mark_proxy_dead
                        mark_proxy_dead(proxy_url)
                    except Exception:
                        pass
                    send_telemetry(worker_id, "session_failed", status="BOT_BLOCKED", details="IP terdeteksi bot YouTube")
                    continue

                # =========================================================================
                # 2-WAY NAVIGATION STRATEGY (Search Langsung vs Search ➔ Masuk Channel Page)
                # Dilengkapi AUTO-FALLBACK agar SEMUA video resmi Horrornologi tertonton merata
                # dan TIDAK PERNAH membuka video milik channel lain!
                # =========================================================================
                use_channel_way = (random.random() < 0.55)
                chosen_video = None
                way_name = ""

                if use_channel_way:
                    way_name = "Jalur 2 (Search ➔ Masuk Channel Page ➔ Katalog Lengkap)"
                    safe_log(worker_name, f"🧭 Mencoba {way_name}...", CYAN)
                    send_telemetry(worker_id, "status_change", status="MASUK_CHANNEL", details=f"Masuk channel @{target_chan}...")
                    
                    nav_ok = navigate_to_channel_from_search(page, target_chan)
                    if nav_ok:
                        handle_consent_popup(page)
                        channel_videos = find_videos_on_channel_page(page, target_chan)
                        if channel_videos:
                            chosen_video = choose_least_recent_video(channel_videos)
                            safe_log(worker_name, f"Ditemukan {len(channel_videos)} video di channel. Memilih variasi yang belum ditonton...", GREEN)

                # Jika Jalur 1 terpilih ATAU Jalur 2 tidak menemukan video, gunakan Jalur 1 (Hasil Pencarian Langsung)
                if not chosen_video:
                    way_name = "Jalur 1 (Search Langsung ➔ Video Hasil Pencarian)"
                    safe_log(worker_name, f"🧭 Mencoba {way_name}...", CYAN)
                    if "/results" not in (page.url or ""):
                        try:
                            page.goto(f"https://www.youtube.com/results?search_query={search_kw}", wait_until="domcontentloaded", timeout=25000)
                            time.sleep(1.5)
                        except Exception:
                            pass
                    else:
                        time.sleep(1.0)

                    matching_videos = find_channel_videos(page, target_chan)
                    if not matching_videos:
                        for s in range(3):
                            safe_log(worker_name, f"Memuat lebih banyak hasil pencarian (scroll {s+1}/3)...", CYAN)
                            safe_evaluate(page, "() => window.scrollBy(0, 800)")
                            time.sleep(1.2)
                            matching_videos = find_channel_videos(page, target_chan)
                            if matching_videos:
                                break

                    if matching_videos:
                        chosen_video = choose_least_recent_video(matching_videos)

                # Jika Jalur 1 awalnya terpilih tapi gagal menemukan video, FALLBACK ke Jalur 2 (Masuk Channel Page Langsung)
                if not chosen_video and not use_channel_way:
                    safe_log(worker_name, f"Hasil pencarian tidak ada video resmi. Fallback ke Jalur 2 (Channel Page)...", CYAN)
                    nav_ok = navigate_to_channel_from_search(page, target_chan)
                    if nav_ok:
                        handle_consent_popup(page)
                        channel_videos = find_videos_on_channel_page(page, target_chan)
                        if channel_videos:
                            chosen_video = choose_least_recent_video(channel_videos)
                            way_name = "Jalur 2 (Fallback Channel Page)"
                            safe_log(worker_name, f"Ditemukan {len(channel_videos)} video di channel setelah fallback.", GREEN)

                # PERLINDUNGAN KETAT: Jika tidak ada video dari channel Horrornologi, JANGAN PERNAH klik video sembarang!
                if not chosen_video:
                    safe_log(worker_name, f"❌ Tidak ditemukan video resmi dari channel '{target_chan.upper()}'. Menolak membuka video sembarang!", RED)
                    send_telemetry(worker_id, "session_failed", status="GAGAL", details="Video channel tidak cocok")
                    continue

                chosen_title = chosen_video["title"]
                expected_dur = chosen_video.get("duration_sec", 0)
                dur_str = chosen_video.get("duration_str", "N/A")
                chosen_href = chosen_video["href"]

                # Catat ke riwayat rotasi agar tidak ditonton berturut-turut di sesi berikutnya
                record_watched_video(chosen_href, chosen_title)

                safe_log(worker_name, f"🎬 Video Terpilih [{way_name}]: '{chosen_title[:42]}...' [{dur_str}]", GREEN)
                send_telemetry(worker_id, "video_chosen", title=chosen_title, status="NAVIGASI", details=f"Membuka: {chosen_title[:32]}... [{dur_str}]")

                # Buka video yang dipilih secara presisi (HANYA link yang memiliki href video tersebut)
                try:
                    v_link = page.locator(f"a[href*='{chosen_href}']").first
                    if v_link.is_visible(timeout=1500):
                        v_link.scroll_into_view_if_needed()
                        time.sleep(0.3)
                        v_link.click()
                    else:
                        page.goto("https://www.youtube.com" + chosen_href, wait_until="domcontentloaded", timeout=20000)
                except Exception:
                    page.goto("https://www.youtube.com" + chosen_href, wait_until="domcontentloaded", timeout=20000)

                # MASUK KE SISTEM LOCK PENONTONAN
                success, elapsed, pct, total, status = watch_video_with_lock(
                    page, worker_name, chosen_title, args.min_percent, args.max_percent, args.max_cap,
                    worker_id=worker_id, expected_duration=expected_dur, target_chan=target_chan
                )

                if success:
                    session_success = True
                    record_video_watch_success(chosen_href, chosen_title, duration_sec=elapsed)
                    with success_counter.get_lock():
                        success_counter.value += 1
                        current_total = success_counter.value
                    send_telemetry(
                        worker_id, "session_success",
                        total_success=current_total,
                        status="SUKSES",
                        details=f"View berhasil ({format_time(elapsed)})",
                        video_title=chosen_title,
                        video_href=chosen_href
                    )
                    safe_log("PROGRESS", f"Total View Berhasil: {current_total} / Target: {args.batch_count}", GREEN)
                else:
                    send_telemetry(worker_id, "session_failed", status="GAGAL", details=status)
                    if status == "BOT_BLOCKED":
                        if proxy_url and proxy_url in local_proxies:
                            local_proxies.remove(proxy_url)
                        try:
                            from geonode_fetcher import mark_proxy_dead
                            mark_proxy_dead(proxy_url)
                        except Exception:
                            pass
                        safe_log(worker_name, f"IP terblokir bot YouTube. Proxy dicopot dari pool.", RED)
                    elif status == "STREAMING_ERROR":
                        if proxy_url and proxy_url in local_proxies:
                            local_proxies.remove(proxy_url)
                        if proxy_url:
                            try:
                                from geonode_fetcher import mark_proxy_dead
                                mark_proxy_dead(proxy_url)
                            except Exception:
                                pass
                        safe_log(worker_name, f"Streaming error pada proxy. Proxy dicopot & ditandai mati.", YELLOW)
                    elif status == "WRONG_CHANNEL":
                        safe_log(worker_name, f"❌ Ditolak: Video bukan milik {target_chan.upper()}! Melanjutkan ke sesi berikutnya...", YELLOW)

            except PlaywrightTimeoutError as e:
                err_str = str(e).split("\n")[0]
                if proxy_url and proxy_url in local_proxies:
                    local_proxies.remove(proxy_url)
                if proxy_url:
                    try:
                        from geonode_fetcher import mark_proxy_dead
                        mark_proxy_dead(proxy_url)
                    except Exception:
                        pass
                safe_log(worker_name, f"Timeout ({err_str[:35]}). Proxy dicopot & ditandai mati.", YELLOW)
                send_telemetry(worker_id, "status_change", status="TIMEOUT", details=err_str[:30])
            except Exception as e:
                err_str = str(e).split("\n")[0]
                is_net_err = any(k in err_str.lower() for k in ["net::err", "proxy", "connection", "socket", "reset", "closed", "timeout", "playwright"])
                if is_net_err:
                    if proxy_url and proxy_url in local_proxies:
                        local_proxies.remove(proxy_url)
                    if proxy_url:
                        try:
                            from geonode_fetcher import mark_proxy_dead
                            mark_proxy_dead(proxy_url)
                        except Exception:
                            pass
                    safe_log(worker_name, f"Kendala koneksi ({err_str[:35]}). Proxy dicopot & ditandai mati.", YELLOW)
                    send_telemetry(worker_id, "status_change", status="ERROR_KONEKSI", details=err_str[:30])
                else:
                    import traceback
                    safe_log(worker_name, f"🚨 Error internal: {err_str[:60]}\n{traceback.format_exc()}", RED)
                    send_telemetry(worker_id, "session_failed", status="ERROR_INTERNAL", details=err_str[:30])
            finally:
                try:
                    context.close()
                except Exception:
                    pass
                shutil.rmtree(temp_profile_dir, ignore_errors=True)
                import gc
                gc.collect()

        sleep_sec = (args.sleep_between + random.randint(1, 2)) if session_success else 0.5
        send_telemetry(worker_id, "status_change", status="COOLDOWN", details=f"Jeda {sleep_sec:.1f}s...")
        time.sleep(sleep_sec)

    send_telemetry(worker_id, "status_change", status="BERHENTI", details="Worker berhenti")

def main():
    parser = argparse.ArgumentParser(
        description="YouTube Process-Isolated Multi-Worker Batch Bot (System Watch Lock)."
    )
    parser.add_argument(
        "-w", "--workers", "--concurrency",
        dest="workers",
        type=int,
        default=3,
        help="Jumlah proses browser yang berjalan bersamaan secara terisolasi (default: 3)"
    )
    parser.add_argument(
        "-b", "--batch", "--total",
        dest="batch_count",
        type=int,
        default=50,
        help="Total sesi view yang ingin dicapai (default: 50)"
    )
    parser.add_argument(
        "--infinite",
        action="store_true",
        help="Jalankan terus menerus tanpa batas sampai ditekan Ctrl+C"
    )
    parser.add_argument(
        "--min-percent",
        type=float,
        default=30.0,
        help="Persentase durasi tonton minimum (default: 30.0)"
    )
    parser.add_argument(
        "--max-percent",
        type=float,
        default=70.0,
        help="Persentase durasi tonton maksimum (default: 70.0)"
    )
    parser.add_argument(
        "--max-cap",
        type=int,
        default=None,
        help="Batas maksimal detik menonton per video (misal 180 untuk 3 menit)"
    )
    parser.add_argument(
        "--sleep-between",
        type=int,
        default=3,
        help="Jeda waktu sebelum sesi berikutnya dalam detik (default: 3)"
    )
    parser.add_argument(
        "--proxy-file",
        type=str,
        default=None,
        help="File daftar proxy (default: auto-detect working_yt_proxies.json/txt)"
    )
    parser.add_argument(
        "--direct",
        action="store_true",
        help="Pakai koneksi langsung tanpa proxy"
    )
    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Lewati download dan pengecekan proxy Geonode + ProxyScrape + Proxifly + TheSpeedX (gunakan cache file yang ada)"
    )
    parser.add_argument(
        "--proxy-limit",
        type=int,
        default=500,
        help="Jumlah proxy yang diambil dari provider API (default: 500)"
    )
    parser.add_argument(
        "--proxy-threads",
        type=int,
        default=80,
        help="Jumlah thread paralel untuk pengecekan proxy (default: 80)"
    )
    parser.add_argument(
        "--proxy-reset-interval",
        type=int,
        default=3600,
        help="Interval reset proxy pool ke 0 dalam detik (default: 3600 detik = 1 jam)"
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Tampilkan jendela browser di layar (default: Headless agar sangat ringan)"
    )
    parser.add_argument(
        "--unmute",
        action="store_true",
        help="Nyalakan suara browser (default: muted agar hemat CPU)"
    )
    parser.add_argument(
        "--min-delay",
        type=int,
        default=50,
        help="Delay ketik minimum dalam ms (default: 50)"
    )
    parser.add_argument(
        "--keyword",
        type=str,
        default="horrornologi",
        help="Keyword pencarian di YouTube (default: horrornologi)"
    )
    parser.add_argument(
        "--channel",
        type=str,
        default="horrornologi",
        help="Target channel YouTube yang wajib dicocokkan secara ketat (default: horrornologi)"
    )
    parser.add_argument(
        "--max-delay",
        type=int,
        default=120,
        help="Delay ketik maksimum dalam ms (default: 120)"
    )
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
        default="0.0.0.0",
        help="Host untuk Web Dashboard (default: 0.0.0.0)"
    )

    args = parser.parse_args()
    args.headless = not args.headed

    success_counter = multiprocessing.Value('i', 0)
    stop_event = multiprocessing.Event()

    proxy_list = []
    if args.direct:
        proxy_list = []
    else:
        from geonode_fetcher import (
            DEFAULT_OUTPUT_JSON,
            HIGH_CPM_COUNTRIES,
            get_and_verify_proxies,
            reset_proxy_storage,
            start_background_proxy_replenisher,
        )
        safe_log("INIT", "🔄 Mereset file proxy menjadi 0 (Fresh Start)...", CYAN)
        reset_proxy_storage(clear_dead=True, clear_pagination=True, output_json=args.proxy_file)

        if args.skip_fetch:
            safe_log("INIT", "Mode --skip-fetch aktif, memuat proxy dari cache lokal...", CYAN)
            proxy_list = load_proxies(args.proxy_file)
        else:
            safe_log("INIT", "Mengambil & menguji batch awal proxy fresh Global (Geonode + ProxyScrape + Proxifly + TheSpeedX)...", CYAN)
            try:
                fresh_proxies = get_and_verify_proxies(
                    limit=args.proxy_limit,
                    threads=args.proxy_threads,
                    countries=None,
                    output_json=args.proxy_file or DEFAULT_OUTPUT_JSON,
                    merge_existing=False
                )
                if fresh_proxies:
                    proxy_list = load_proxies(args.proxy_file)
            except Exception as e:
                safe_log("INIT", f"Gagal auto-fetch proxy ({e}), melanjutkan...", YELLOW)
                proxy_list = load_proxies(args.proxy_file)

        try:
            start_background_proxy_replenisher(
                interval_sec=180,
                stop_event=stop_event,
                limit=250,
                threads=60,
                output_json=args.proxy_file or DEFAULT_OUTPUT_JSON,
                min_threshold=15,
                countries=None,
                hourly_reset_sec=args.proxy_reset_interval
            )
        except Exception:
            pass

    # Inisialisasi Web Dashboard Real-Time jika diaktifkan
    telemetry_q = None
    if args.web:
        try:
            from web_dashboard import start_web_dashboard
            telemetry_q = multiprocessing.Queue()
            web_cfg = {
                "batch_count": args.batch_count,
                "infinite": args.infinite,
                "keyword": args.keyword,
                "channel": args.channel,
                "workers": args.workers,
                "proxy_count": len(proxy_list)
            }
            start_web_dashboard(telemetry_q, host=args.web_host, port=args.web_port, initial_config=web_cfg)
        except Exception as e:
            safe_log("WEB", f"Gagal memulai Web Dashboard: {e}", YELLOW)
            telemetry_q = None

    print(f"\n{BOLD}==================================================================={RESET}")
    print(f"{BOLD}    YOUTUBE MULTI-PROCESS ISOLATED BOT (SYSTEM WATCH LOCK)         {RESET}")
    print(f"{BOLD}==================================================================={RESET}")
    print(f"{CYAN}Keyword Pencarian :{RESET} '{args.keyword}'")
    print(f"{CYAN}Target Channel    :{RESET} {args.channel.capitalize()} (Filter Ketat)")
    print(f"{CYAN}Proses Terisolasi :{RESET} {BOLD}{args.workers} Proses OS Terpisah{RESET}")
    print(f"{CYAN}Target Selesai    :{RESET} {'Tak Terbatas (Infinite)' if args.infinite else args.batch_count}")
    print(f"{CYAN}Pool Proxy        :{RESET} {len(proxy_list)} Proxy" if proxy_list else "Direct (Tanpa Proxy)")
    print(f"{CYAN}Rentang Durasi    :{RESET} {args.min_percent}% - {args.max_percent}% dari durasi video")
    print(f"{CYAN}Mode Browser      :{RESET} {'Tampilan Layar (Headed)' if args.headed else 'Headless (Ultra Ringan, Low CPU/RAM)'}")
    if args.web and telemetry_q is not None:
        print(f"{CYAN}Web Dashboard     :{RESET} {BOLD}http://{args.web_host}:{args.web_port}{RESET} (Live Real-Time)")
    print("=" * 67 + "\n")

    processes = []
    for w_id in range(1, args.workers + 1):
        p = multiprocessing.Process(
            target=worker_process_main,
            args=(w_id, args, proxy_list, success_counter, stop_event, telemetry_q),
            daemon=True
        )
        p.start()
        processes.append(p)
        time.sleep(1.5) # Stagger worker launches

    try:
        last_proxy_check = 0.0
        last_reported_proxy_count = len(proxy_list)
        while not stop_event.is_set():
            time.sleep(1)
            now = time.time()
            if not args.direct and (now - last_proxy_check >= 3.0):
                last_proxy_check = now
                try:
                    from geonode_fetcher import DEFAULT_OUTPUT_JSON, get_dead_proxies
                    target_file = args.proxy_file or DEFAULT_OUTPUT_JSON
                    if os.path.exists(target_file):
                        dead = get_dead_proxies()
                        with open(target_file, "r", encoding="utf-8", errors="ignore") as f:
                            p_data = json.load(f)
                        if isinstance(p_data, list):
                            cur_count = len([
                                x for x in p_data
                                if (x.get("proxy") if isinstance(x, dict) else str(x)).strip() not in dead
                            ])
                            if cur_count != last_reported_proxy_count:
                                if cur_count > last_reported_proxy_count:
                                    safe_log("PROXIES", f"🔄 Pool proxy bertambah: {cur_count} proxy aktif siap (+{cur_count - last_reported_proxy_count})", GREEN)
                                else:
                                    safe_log("PROXIES", f"⚠️ Proxy tereliminasi: Sisa {cur_count} proxy aktif (-{last_reported_proxy_count - cur_count})", YELLOW)
                                last_reported_proxy_count = cur_count
                                if telemetry_q is not None:
                                    telemetry_q.put({"event": "proxy_count_update", "count": cur_count})
                except Exception:
                    pass

            with success_counter.get_lock():
                current = success_counter.value
            if not args.infinite and current >= args.batch_count:
                safe_log("MAIN", f"Target {args.batch_count} view berhasil dicapai!", GREEN)
                stop_event.set()
                break

    except KeyboardInterrupt:
        print(f"\n{YELLOW}[MAIN] Menerima sinyal Ctrl+C, mematikan seluruh proses worker...{RESET}")
        stop_event.set()
    finally:
        stop_event.set()
        if telemetry_q is not None:
            try:
                telemetry_q.put({"event": "SHUTDOWN"})
            except Exception:
                pass
        for p in processes:
            if p.is_alive():
                p.terminate()
        for p in processes:
            p.join(timeout=1.0)
        
        with success_counter.get_lock():
            final_views = success_counter.value
        print("\n" + "=" * 67)
        safe_log("SELESAI", f"Sesi Batch Berakhir. Total View Berhasil: {final_views}", GREEN)
        print("=" * 67 + "\n")
        os._exit(0)

if __name__ == "__main__":
    main()
