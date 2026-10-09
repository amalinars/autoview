# autoview

YouTube Multi-Process Isolated Batch Bot with Real-Time Web Telemetry Monitor & Triple-Source Dynamic Proxy Engine.

## Fitur Utama
- **Process Isolation**: Proses worker OS terpisah untuk stabilitas dan konkurensi tinggi.
- **Triple-Source Proxy Engine**: Pengambilan proxy dari 3 provider sekaligus (**Geonode**, **ProxyScrape**, dan **Proxifly** 50.000+ proxy) dengan auto-replenish di background dan verifikasi latency live.
- **Real-Time Web Dashboard**: Web UI bertenaga Starlette + Server-Sent Events (SSE) untuk memantau status worker, durasi tonton, proxy aktif, dan views secara live.
- **Smart Ad Skipping**: Otomatis mendeteksi dan melewati iklan (skippable & non-skippable).
- **Humanized Interaction**: Simulasi pergerakan mouse mikro, kecepatan mengetik natural, dan jeda tonton dinamis.

---

## 🚀 Quick Setup di VPS (Ubuntu / Debian)

### 1. Update & Install Paket Sistem
```bash
sudo dpkg --configure -a
sudo apt update && sudo apt install -y python3 python3-pip python3-venv git tmux
```

### 2. Clone Repository & Setup Virtual Environment
```bash
git clone https://github.com/amalinars/autoview.git
cd autoview
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependensi & Browser Playwright
```bash
pip install -r requirements.txt
playwright install chromium
playwright install-deps chromium
```

### 4. Buka Port Firewall untuk Web Dashboard
```bash
sudo ufw allow 5000/tcp
```

---

## 📟 Panduan Menggunakan Tmux (Jalan 24 Jam di Background)

| Aksi | Perintah / Shortcut |
| :--- | :--- |
| **Buat sesi tmux baru** | `tmux new -s autoview` |
| **Keluar layar (Detach) tanpa matikan bot** | Tekan `Ctrl + B`, lalu lepas dan tekan tombol `D` |
| **Masuk kembali ke layar bot (Attach)** | `tmux attach -t autoview` |
| **Lihat daftar sesi tmux** | `tmux ls` |
| **Matikan sesi tmux** | `tmux kill-session -t autoview` |
| **Scroll log ke atas/bawah** | Tekan `Ctrl + B` lalu `[` (Gunakan panah, tekan `q` untuk keluar mode scroll) |

---

## 🎯 Perintah Menjalankan Bot

Pastikan berada di folder `autoview` dan venv aktif (`source venv/bin/activate`). Masuk ke tmux:
```bash
tmux new -s autoview
```

### 1. Mode Unlimited (24 Jam Nonstop - Direkomendasikan di VPS)
```bash
python3 youtube_search.py -w 3 --infinite --keyword "horrornologi"
```

### 2. Mode Target Jumlah View (Misal 50 View)
```bash
python3 youtube_search.py -w 3 -b 50 --keyword "horrornologi"
```

### 3. Mode Custom Channel & Parameter Tambahan
```bash
python3 youtube_search.py -w 4 --infinite --keyword "video viral" --channel "namachannel"
```

*Setelah bot berjalan di tmux, tekan **`Ctrl + B`** lalu **`D`** untuk melepas terminal. Kamu bisa menutup terminal / mematikan laptop.*

---

## 🌐 Memantau Live Web Dashboard
Buka browser di HP atau laptop kamu:
👉 **`http://IP_VPS_KAMU:5000`**
