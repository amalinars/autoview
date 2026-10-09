# autoview

YouTube Multi-Process Isolated Batch Bot with Real-Time Web Telemetry Monitor & Dual-Source Dynamic Proxy Engine.

## Features
- **Process Isolation**: Independent OS worker processes for high concurrency and resilience.
- **Dynamic Proxy Rotation**: Dual-source ingestion (Geonode + ProxyScrape) with automatic background replenishment and live health verification.
- **Real-Time Web Dashboard**: Lightweight Starlette + Server-Sent Events (SSE) web UI for live monitoring of worker states, watch progress, active proxies, and batch statistics.
- **Smart Ad Skipping**: Automated detection and handling for both skippable and non-skippable YouTube ads.
- **Humanized Actions**: Micro-mouse movements, human typing simulation, and realistic video interaction.

## Quick Start
```bash
# Run with 3 concurrent workers, 50 batch target
python3 youtube_search.py -w 3 -b 50 --keyword "horrornologi"
```

Open your browser at:
`http://localhost:5000` to monitor live progress.
