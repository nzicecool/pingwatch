# 📡 PingWatch

Modern network latency monitoring for Raspberry Pi — a lightweight alternative to Smokeping.

Measures, stores, and visualises latency, jitter, and packet loss across your network with smoke-style graphs, a REST API, and a built-in dashboard.

## What It Does

- **5 probe types** — ICMP, fping (batch), HTTP, DNS, TCP
- **SQLite storage** — zero-config time-series with auto-rollup (5min/1hr/1day) and pruning
- **Web dashboard** — Plotly smoke-style latency graphs with sparkline overview
- **REST API** — JSON endpoints for targets, measurements, rollups, and health
- **Single binary feel** — one process, one config, one command

## Quick Start

### 1. Install Dependencies

```bash
# Python 3.11+ required
python3 --version

# System packages for ICMP/fping probes
sudo apt update
sudo apt install -y fping iputils-ping

# Optional: DNS probe dependency
pip install dnspython
```

### 2. Clone & Install

```bash
git clone https://github.com/nzicecool/pingwatch.git
cd pingwatch

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install PingWatch (editable mode)
pip install -e .
```

### 3. Configure

```bash
cp config.yaml my-config.yaml
# Edit targets, probes, intervals as needed
nano my-config.yaml
```

See [config.yaml](config.yaml) for the full reference. Key sections:

```yaml
general:
  step: 60          # seconds between probe runs
  pings: 20         # pings per target per step

targets:
  - name: DNS Servers
    probe: fping
    hosts:
      - label: Cloudflare DNS
        address: 1.1.1.1
      - label: Google DNS
        address: 8.8.8.8

  - name: Web Services
    probe: http_check
    hosts:
      - label: Google
        address: https://www.google.com

storage:
  path: ./data/pingwatch.db
  retention_days: 365
```

### 4. Run

```bash
# Validate config
pingwatch check -c my-config.yaml

# Run once (test all probes)
pingwatch run -c my-config.yaml --once

# Start monitoring daemon (no dashboard)
pingwatch run -c my-config.yaml

# Start with web dashboard + API
pingwatch serve -c my-config.yaml --host 0.0.0.0 --port 8080
```

Open `http://<pi-ip>:8080` in your browser for the dashboard.

### 5. Run as a Service (systemd)

Create `/etc/systemd/system/pingwatch.service`:

```ini
[Unit]
Description=PingWatch Network Monitor
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/pingwatch
ExecStart=/home/pi/pingwatch/.venv/bin/pingwatch serve -c /home/pi/pingwatch/config.yaml --host 0.0.0.0 --port 8080
Restart=always
RestartSec=10

# fping needs CAP_NET_RAW — or run as root
# AmbientCapabilities=CAP_NET_RAW
# Alternatively: sudo setcap cap_net_raw+ep /usr/bin/fping

[Install]
WantedBy=multi-user.target
```

> **ICMP note:** fping requires `CAP_NET_RAW`. Either run the service as root, grant the capability (uncomment line above), or `sudo setcap cap_net_raw+ep /usr/bin/fping`.

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable pingwatch
sudo systemctl start pingwatch

# Check status
sudo systemctl status pingwatch

# View logs
journalctl -u pingwatch -f
```

## REST API

All endpoints return JSON. Available when running in `serve` mode.

| Endpoint | Description |
|----------|-------------|
| `GET /api/targets` | All targets with latest stats |
| `GET /api/targets/{name}/measurements?since=&until=&limit=` | Raw measurements |
| `GET /api/targets/{name}/rollup?period=5min\|1hour\|1day&since=&until=` | Aggregated data |
| `GET /api/summary` | Overview of all targets |
| `GET /api/health` | Service health check |

Example:

```bash
curl http://localhost:8080/api/summary | python3 -m json.tool
```

## Dashboard

The built-in dashboard at `/` provides:

- **Overview** — sparkline cards for all monitored targets
- **Detail view** — click any target for a smoke-style latency graph
  - Median line with min/max shading
  - Packet loss overlay
  - Time range selector: 1H / 6H / 24H / 7D / 30D
- **Auto-refresh** — data updates every 30 seconds
- **Mobile-friendly** — responsive layout

## Probes

| Probe | Type | Description |
|-------|------|-------------|
| `fping` | ICMP batch | Efficient multi-target pinging via fping binary |
| `icmp` | ICMP single | Direct ICMP echo (needs CAP_NET_RAW) |
| `http` | HTTP/HTTPS | Measures round-trip to HTTP endpoints |
| `dns` | DNS | DNS query latency |
| `tcp` | TCP | TCP handshake timing to any port |

## Project Structure

```
pingwatch/
├── config.yaml              # Sample configuration
├── pyproject.toml           # Python package config
├── src/pingwatch/
│   ├── __init__.py
│   ├── cli.py               # CLI entry point
│   ├── config/              # YAML → Pydantic config loader
│   ├── probes/              # Probe implementations
│   │   ├── icmp.py
│   │   ├── fping.py
│   │   ├── http.py
│   │   ├── dns.py
│   │   └── tcp.py
│   ├── scheduler/           # Async tick-based scheduler
│   ├── storage/             # SQLite storage + rollups
│   ├── api/                 # FastAPI REST API
│   │   └── app.py
│   └── templates/
│       └── dashboard.html   # Plotly dashboard
└── tests/                   # 36 tests, all passing
```

## Requirements

- **Python** 3.11+
- **OS** Linux (tested on Raspberry Pi OS / Debian)
- **Packages:** `fping`, `iputils-ping` (for ICMP probes)
- **RAM:** ~50MB idle (Pi 4 friendly)
- **Disk:** Minimal — SQLite auto-prunes based on retention

## Roadmap

- [x] **Phase 1** — Probe engine, SQLite storage, scheduler, CLI
- [x] **Phase 2** — FastAPI REST API + Plotly smoke-style dashboard
- [ ] **Phase 3** — Alerting + AI Network Briefer
- [ ] **Phase 4** — Master/Slave distributed mode
- [ ] **Phase 5** — Docker + Helm chart
- [ ] **Phase 6** — Polish, PyPI package, docs site

## License

MIT
