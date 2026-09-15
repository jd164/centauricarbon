"""Main launcher for Elegoo Centauri Carbon Filament Dryer.

Runs Web Dashboard (default) or Interactive CLI.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import webbrowser

import uvicorn

from centauri_dryer.cli import run_cli_dashboard
from centauri_dryer.server import create_app

import json
import os
from pathlib import Path
from typing import Any

CONFIG_FILE = Path("config.json")


def load_config() -> dict[str, Any]:
    """Load configuration from config.json if available, or environment variables."""
    cfg: dict[str, Any] = {
        "host": os.getenv("CENTAURI_PRINTER_IP", "192.168.1.100"),
        "port": int(os.getenv("CENTAURI_PRINTER_PORT", "3030")),
        "mainboard": os.getenv("CENTAURI_MAINBOARD_ID", "auto"),
        "web_port": int(os.getenv("CENTAURI_WEB_PORT", "8000")),
    }
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "printer_ip" in data:
                    cfg["host"] = data["printer_ip"]
                if "printer_port" in data:
                    cfg["port"] = int(data["printer_port"])
                if "mainboard_id" in data:
                    cfg["mainboard"] = data["mainboard_id"]
                if "web_port" in data:
                    cfg["web_port"] = int(data["web_port"])
        except Exception as e:
            logging.warning(f"Could not load config.json: {e}")
    return cfg


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> None:
    cfg = load_config()

    parser = argparse.ArgumentParser(
        description="Filament Drying Controller and Monitor for Elegoo Centauri Carbon (SDCP v3)"
    )
    parser.add_argument("--host", default=cfg["host"], help=f"Printer IP address (default: {cfg['host']})")
    parser.add_argument("--port", type=int, default=cfg["port"], help=f"Printer WebSocket port (default: {cfg['port']})")
    parser.add_argument("--mainboard", default=cfg["mainboard"], help="Printer MainboardID ('auto' for auto-detection)")
    parser.add_argument("--web-host", default="0.0.0.0", help="Web server bind host (default: 0.0.0.0)")
    parser.add_argument("--web-port", type=int, default=cfg["web_port"], help=f"Web Dashboard port (default: {cfg['web_port']})")
    parser.add_argument("--cli", action="store_true", help="Run in interactive terminal / CLI mode")
    parser.add_argument("--preset", choices=["petg", "pla", "asa", "tpu", "nylon"], help="Automatically start cycle with specified preset")
    parser.add_argument("--no-browser", action="store_true", help="Do not open browser automatically")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose debug logging")

    args = parser.parse_args()
    setup_logging(args.verbose)

    if args.cli:
        # CLI Mode
        try:
            asyncio.run(
                run_cli_dashboard(
                    ip=args.host,
                    port=args.port,
                    mainboard_id=args.mainboard,
                    auto_preset=args.preset,
                )
            )
        except KeyboardInterrupt:
            print("\nOperation terminated.")
    else:
        # Web Dashboard Mode
        app = create_app(ip=args.host, port=args.port, mainboard_id=args.mainboard)
        url = f"http://localhost:{args.web_port}"

        print("=======================================================================")
        print("  ELEGOO CENTAURI CARBON — FILAMENT DRYING MANAGER")
        print("=======================================================================")
        print(f"  Target Printer  : ws://{args.host}:{args.port}/websocket")
        print(f"  MainboardID     : {args.mainboard}")
        print(f"  Web Dashboard   : {url}")
        print(f"  Network Access  : http://<PC_LOCAL_IP>:{args.web_port}")
        print("=======================================================================")
        print("  Press CTRL+C to safely terminate the server.\n")

        if not args.no_browser:
            async def _open_browser():
                await asyncio.sleep(1.2)
                webbrowser.open(url)
            asyncio.run(_open_browser())

        uvicorn.run(
            app,
            host=args.web_host,
            port=args.web_port,
            log_level="info" if not args.verbose else "debug",
        )


if __name__ == "__main__":
    main()
