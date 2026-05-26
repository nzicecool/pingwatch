# PingWatch — Modern Network Latency Monitor

A lightweight, modern alternative to Smokeping for measuring, storing, and alerting on network latency, jitter, and packet loss. Designed for Raspberry Pi, Docker, and Kubernetes.

## Quick Start

```bash
pip install -e .
pingwatch run --config config.yaml
```

## Features (Phase 1)

- **Multi-probe support** — ICMP, fping batch, HTTP, DNS, TCP
- **SQLite storage** — zero-config time-series on Pi
- **Tick scheduler** — configurable intervals, concurrent execution
- **YAML config** — simple, human-readable configuration

## Architecture

```
Config (YAML) → Scheduler → Probes → Storage (SQLite)
                                      ↓
                              Rollup / Prune
```
