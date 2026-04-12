#!/usr/bin/env python3
"""
GitLab Code Review Tool - Application Runner

Usage:
    python run.py [--host HOST] [--port PORT] [--reload] [--workers N]
"""
import sys
import argparse
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

import uvicorn
from loguru import logger

# Remove default handler and add custom
logger.remove()
logger.add(sys.stderr, level="INFO", format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>")


def main():
    parser = argparse.ArgumentParser(description="GitLab Code Review Tool")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=5001, help="Port to bind to")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    parser.add_argument("--workers", type=int, default=1, help="Number of worker processes")
    parser.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"], help="Log level")

    args = parser.parse_args()

    # Ensure data directory exists
    data_dir = Path(__file__).parent / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "logs").mkdir(exist_ok=True)
    (data_dir / "reports").mkdir(exist_ok=True)
    (data_dir / "config").mkdir(exist_ok=True)

    # Setup file logging
    log_file = data_dir / "logs" / "app.log"
    logger.add(
        log_file,
        rotation="10 MB",
        retention="30 days",
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}"
    )

    logger.info(f"Starting GitLab Code Review Tool on {args.host}:{args.port}")
    logger.info(f"Data directory: {data_dir}")
    logger.info(f"Log file: {log_file}")

    # Run server
    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=args.workers if not args.reload else 1,  # Workers not compatible with reload
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
