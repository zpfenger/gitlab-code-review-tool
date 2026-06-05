#!/usr/bin/env python3
"""
通用数据库迁移脚本

用法:
    python scripts/migrate.py                    # 检查并执行迁移
    python scripts/migrate.py --dry-run          # 仅检查，不执行
    python scripts/migrate.py --check            # 检查是否有变更
    python scripts/migrate.py --backup           # 迁移前自动备份

特性:
    - 自动检测表结构差异（新增表、新增列、修改列类型/长度、修改约束）
    - SQLite 不支持 ALTER COLUMN 时自动重建表
    - 迁移前可选备份
    - 支持 dry-run 模式预览变更
    - 详细的迁移日志

适用场景:
    - 生产环境部署新版本时同步表结构
    - 开发环境切换分支后同步数据库
    - CI/CD 流程中自动迁移
"""

import argparse
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger


def setup_logging(verbose: bool = False):
    """配置日志"""
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
        level="DEBUG" if verbose else "INFO",
    )


def backup_database(db_path: str, backup_dir: str = None) -> str:
    """备份数据库文件"""
    if not os.path.exists(db_path):
        logger.warning(f"数据库文件不存在: {db_path}")
        return ""

    # 确定备份目录
    if backup_dir is None:
        backup_dir = os.path.join(os.path.dirname(db_path), "backups")

    os.makedirs(backup_dir, exist_ok=True)

    # 生成备份文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    db_name = os.path.basename(db_path)
    backup_name = f"{db_name}.backup.{timestamp}"
    backup_path = os.path.join(backup_dir, backup_name)

    # 执行备份
    shutil.copy2(db_path, backup_path)
    logger.info(f"数据库已备份到: {backup_path}")

    return backup_path


def check_changes(engine, metadata) -> bool:
    """检查是否有需要迁移的变更"""
    from app.migration import analyze_diff

    diffs = analyze_diff(engine, metadata)

    if not diffs:
        logger.info("✅ 数据库结构已是最新，无需迁移")
        return False

    logger.info(f"📋 发现 {len(diffs)} 个表有变更:")
    for diff in diffs:
        if not diff.exists_in_db:
            logger.info(f"  📦 {diff.table_name}: 新增表")
        else:
            for col_diff in diff.column_diffs:
                if col_diff.change_type.value == "ADD_COLUMN":
                    logger.info(f"  ➕ {diff.table_name}.{col_diff.name}: 新增列")
                elif col_diff.change_type.value == "ALTER_COLUMN":
                    logger.info(f"  🔄 {diff.table_name}.{col_diff.name}: {col_diff.reason}")

    return True


def dry_run(engine, metadata):
    """仅预览变更，不执行"""
    from app.migration import analyze_diff

    logger.info("=" * 60)
    logger.info("🔍 DRY RUN 模式 - 仅预览变更")
    logger.info("=" * 60)

    diffs = analyze_diff(engine, metadata)

    if not diffs:
        logger.info("✅ 无需迁移")
        return

    logger.info(f"\n📋 将执行以下变更:\n")

    for diff in diffs:
        logger.info(f"📊 表: {diff.table_name}")

        if not diff.exists_in_db:
            logger.info(f"   → 将创建新表")
        else:
            for col_diff in diff.column_diffs:
                if col_diff.change_type.value == "ADD_COLUMN":
                    logger.info(f"   → 新增列: {col_diff.name} ({col_diff.new_type})")
                elif col_diff.change_type.value == "ALTER_COLUMN":
                    logger.info(f"   → 修改列: {col_diff.name}")
                    if col_diff.old_type and col_diff.new_type:
                        logger.info(f"     类型: {col_diff.old_type} → {col_diff.new_type}")
                    if col_diff.old_nullable is not None:
                        logger.info(f"     nullable: {col_diff.old_nullable} → {col_diff.new_nullable}")

            if diff.needs_rebuild:
                logger.info(f"   ⚠️  需要重建表（SQLite 不支持 ALTER COLUMN）")

        logger.info("")

    logger.info("=" * 60)
    logger.info("💡 执行迁移请去掉 --dry-run 参数")
    logger.info("=" * 60)


def run_migration(engine, metadata, with_backup: bool = False, db_path: str = None):
    """执行迁移"""
    from app.migration import run_migration

    logger.info("=" * 60)
    logger.info("🚀 开始数据库迁移")
    logger.info("=" * 60)

    # 可选备份
    if with_backup and db_path:
        backup_database(db_path)

    # 执行迁移
    plan = run_migration(engine, metadata)

    # 输出结果
    logger.info("\n" + "=" * 60)
    logger.info("📊 迁移结果:")
    logger.info("=" * 60)

    if plan.tables_to_create:
        logger.info(f"✅ 新建表: {', '.join(plan.tables_to_create)}")
    if plan.tables_to_rebuild:
        logger.info(f"🔄 重建表: {', '.join(plan.tables_to_rebuild)}")
    if plan.columns_to_add:
        logger.info(f"➕ 新增列: {len(plan.columns_to_add)} 个")
        for col in plan.columns_to_add:
            logger.info(f"   - {col.name}")

    if not plan.tables_to_create and not plan.tables_to_rebuild and not plan.columns_to_add:
        logger.info("✅ 无需迁移")

    logger.info("=" * 60)
    return plan


def main():
    parser = argparse.ArgumentParser(
        description="通用数据库迁移脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python scripts/migrate.py                    # 检查并执行迁移
    python scripts/migrate.py --dry-run          # 仅预览变更
    python scripts/migrate.py --backup           # 迁移前自动备份
    python scripts/migrate.py --check            # 仅检查是否有变更
    python scripts/migrate.py --verbose          # 显示详细日志
        """,
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅预览变更，不执行迁移",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="仅检查是否有变更，不执行",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="迁移前自动备份数据库",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="显示详细日志",
    )
    parser.add_argument(
        "--db-path",
        help="指定数据库文件路径（默认从环境变量读取）",
    )

    args = parser.parse_args()

    # 配置日志
    setup_logging(args.verbose)

    try:
        # 设置数据库路径
        if args.db_path:
            os.environ["DATABASE_URL"] = f"sqlite:///{args.db_path}"

        # 导入项目模块（在设置环境变量之后）
        from app.database import Base, engine
        import app.models  # noqa: F401 - 导入所有模型

        db_path = args.db_path or str(project_root / "data" / "config.db")

        # 执行相应操作
        if args.check:
            has_changes = check_changes(engine, Base.metadata)
            sys.exit(0 if has_changes else 1)

        elif args.dry_run:
            dry_run(engine, Base.metadata)

        else:
            run_migration(
                engine,
                Base.metadata,
                with_backup=args.backup,
                db_path=db_path,
            )

    except Exception as e:
        logger.error(f"数据库迁移命令失败: {e}")
        sys.exit(2 if args.check else 1)


if __name__ == "__main__":
    main()
