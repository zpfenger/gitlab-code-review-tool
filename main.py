#!/usr/bin/env python3
"""
GitLab Code Review Tool - 应用主入口

Usage:
    python main.py [--host HOST] [--port PORT] [--reload] [--workers N]
    python main.py --init-admin  # 初始化管理员账号
"""
import sys
import argparse
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

import uvicorn
from loguru import logger


def init_admin():
    """初始化管理员账号（首次使用时调用）"""
    from app.config import config_manager
    from app.database import init_db

    init_db()
    config = config_manager.get_admin_config()
    print(f"\n管理员账号信息:")
    print(f"  用户名: {config.username}")
    print(f"  密码哈希: {config.password_hash[:20]}...")
    print(f"\n如需重置密码，请删除 config/admin.yaml 后重新启动应用。\n")


def main():
    parser = argparse.ArgumentParser(description="GitLab Code Review Tool")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=7000, help="Port to bind to")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    parser.add_argument("--workers", type=int, default=1, help="Number of worker processes")
    parser.add_argument("--log-level", default="info",
                        choices=["debug", "info", "warning", "error"], help="Log level")
    parser.add_argument("--init-admin", action="store_true", help="初始化管理员账号")

    args = parser.parse_args()

    if args.init_admin:
        init_admin()
        return

    # Ensure data directory exists
    data_dir = Path(__file__).parent / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "logs").mkdir(exist_ok=True)
    (data_dir / "reports").mkdir(exist_ok=True)
    (data_dir / "config").mkdir(exist_ok=True)

    # Setup logging
    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
    )
    log_file = data_dir / "logs" / "app.log"
    logger.add(
        log_file,
        rotation="10 MB",
        retention="30 days",
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}"
    )

    logger.info(f"Starting GitLab Code Review Tool on {args.host}:{args.port}")

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=args.workers if not args.reload else 1,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
