"""PingWatch CLI — command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys

import structlog

from pingwatch import __version__
from pingwatch.config import load_config
from pingwatch.scheduler import ProbeScheduler
from pingwatch.storage import Storage

logger = structlog.get_logger()


def setup_logging(debug: bool = False) -> None:
    """Configure structured logging."""
    level = "DEBUG" if debug else "INFO"
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
    )


def build_parser() -> argparse.ArgumentParser:
    """Build argument parser."""
    parser = argparse.ArgumentParser(
        prog="pingwatch",
        description="Modern Smokeping-like network latency monitor",
    )
    parser.add_argument("--version", action="version", version=f"pingwatch {__version__}")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # run
    run_parser = subparsers.add_parser("run", help="Start monitoring daemon")
    run_parser.add_argument(
        "-c", "--config", required=True, help="Path to config YAML file"
    )
    run_parser.add_argument(
        "--once", action="store_true", help="Run once and exit (for testing)"
    )

    # check
    check_parser = subparsers.add_parser("check", help="Validate config file")
    check_parser.add_argument(
        "-c", "--config", required=True, help="Path to config YAML file"
    )

    # serve
    serve_parser = subparsers.add_parser("serve", help="Start API + dashboard server")
    serve_parser.add_argument(
        "-c", "--config", required=True, help="Path to config YAML file"
    )
    serve_parser.add_argument(
        "--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)"
    )
    serve_parser.add_argument(
        "--port", type=int, default=8080, help="Bind port (default: 8080)"
    )

    return parser


async def run_daemon(config_path: str, once: bool = False) -> None:
    """Run the PingWatch daemon."""
    config = load_config(config_path)
    storage = Storage(config.storage.path)

    await storage.connect()
    logger.info("storage.connected", path=config.storage.path)

    scheduler = ProbeScheduler(config, storage)
    scheduler.initialise()

    if once:
        results = await scheduler.run_once()
        for r in results:
            logger.info(
                "result",
                target=r.target,
                probe=r.probe_name,
                median=f"{r.median:.2f}ms" if r.median else "N/A",
                loss=f"{r.loss_pct:.1f}%",
                jitter=f"{r.jitter:.2f}ms",
            )
        await storage.close()
        return

    # Setup signal handlers for graceful shutdown
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def signal_handler():
        logger.info("shutdown.signal_received")
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    await scheduler.start()

    # Wait for shutdown signal
    await shutdown_event.wait()
    await scheduler.stop()
    await storage.close()
    logger.info("shutdown.complete")


async def run_serve(config_path: str, host: str = "0.0.0.0", port: int = 8080) -> None:
    """Run the API server with background scheduler."""
    import uvicorn

    config = load_config(config_path)
    storage = Storage(config.storage.path)
    await storage.connect()
    logger.info("storage.connected", path=config.storage.path)

    # Start scheduler in background
    scheduler = ProbeScheduler(config, storage)
    scheduler.initialise()
    await scheduler.start()
    logger.info("scheduler.started_in_background")

    # Create FastAPI app with storage
    from pingwatch.api.app import create_app
    app = create_app(storage)

    # Configure uvicorn
    uv_config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(uv_config)

    logger.info("api.starting", host=host, port=port)

    try:
        await server.serve()
    except Exception:
        pass
    finally:
        await scheduler.stop()
        await storage.close()
        logger.info("shutdown.complete")


def main() -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    setup_logging(debug=args.debug)

    if args.command == "run":
        asyncio.run(run_daemon(args.config, once=args.once))
    elif args.command == "check":
        try:
            config = load_config(args.config)
            print(f"✅ Config valid: {len(config.probes)} probes, {len(config.targets)} target groups")
        except Exception as e:
            print(f"❌ Config error: {e}")
            sys.exit(1)
    elif args.command == "serve":
        asyncio.run(run_serve(args.config, host=args.host, port=args.port))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
