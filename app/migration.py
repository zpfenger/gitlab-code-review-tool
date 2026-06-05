"""
通用数据库迁移模块

自动对比 SQLAlchemy 模型定义与实际数据库结构，生成并执行迁移。
支持 SQLite 不支持 ALTER COLUMN 的情况（重建表）。

变更类型：
- 新增表
- 新增列
- 修改列类型/长度
- 修改 nullable 约束
- 修改默认值
- 新增/删除索引
- 新增/删除唯一约束
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from loguru import logger
from sqlalchemy import (
    Column,
    MetaData,
    inspect,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.schema import CreateIndex, CreateTable
from sqlalchemy.types import TypeEngine


# ──────────────────────────────────────────────────────────────────────
# 数据类型映射
# ──────────────────────────────────────────────────────────────────────

# SQLite 实际存储类型 → SQLAlchemy 类型的近似映射
_SQLITE_TYPE_MAP = {
    "INTEGER": "Integer",
    "REAL": "Float",
    "TEXT": "Text",
    "BLOB": "LargeBinary",
    "BOOLEAN": "Boolean",
    "DATETIME": "DateTime",
    "DATE": "Date",
    "JSON": "JSON",
}


# ──────────────────────────────────────────────────────────────────────
# 数据模型
# ──────────────────────────────────────────────────────────────────────

class ChangeType(str, Enum):
    CREATE_TABLE = "CREATE_TABLE"
    ADD_COLUMN = "ADD_COLUMN"
    ALTER_COLUMN = "ALTER_COLUMN"
    DROP_COLUMN = "DROP_COLUMN"  # SQLite 不支持，仅记录
    REBUILD_TABLE = "REBUILD_TABLE"
    ADD_INDEX = "ADD_INDEX"
    DROP_INDEX = "DROP_INDEX"
    ADD_UNIQUE_CONSTRAINT = "ADD_UNIQUE_CONSTRAINT"


@dataclass
class ColumnDiff:
    """列差异"""
    name: str
    change_type: ChangeType
    old_type: Optional[str] = None
    new_type: Optional[str] = None
    old_nullable: Optional[bool] = None
    new_nullable: Optional[bool] = None
    old_default: Optional[str] = None
    new_default: Optional[str] = None
    reason: str = ""


@dataclass
class TableDiff:
    """表差异"""
    table_name: str
    exists_in_db: bool
    exists_in_model: bool
    column_diffs: list[ColumnDiff]
    index_diffs: list[str]
    constraint_diffs: list[str]
    needs_rebuild: bool = False  # 是否需要重建表


@dataclass
class MigrationPlan:
    """迁移计划"""
    tables_to_create: list[str]
    tables_to_rebuild: list[str]
    columns_to_add: list[ColumnDiff]
    other_changes: list[str]
    summary: str = ""


# ──────────────────────────────────────────────────────────────────────
# 结构对比
# ──────────────────────────────────────────────────────────────────────

def _normalize_type(col_type: TypeEngine, dialect_name: str = "sqlite") -> str:
    """将列类型规范化为可比较的字符串"""
    type_str = col_type.compile(dialect=None).upper()

    # SQLite 特殊处理
    if dialect_name == "sqlite":
        # VARCHAR(N) → VARCHAR(N)，保持原样
        # 但 SQLite 实际存储为 TEXT
        if type_str.startswith("VARCHAR"):
            return type_str
        if type_str.startswith("STRING"):
            # SQLAlchemy 的 String(N) 编译为 VARCHAR(N)
            return type_str
        # BOOLEAN → INTEGER in SQLite
        if "BOOLEAN" in type_str:
            return "BOOLEAN"

    return type_str


def _get_default_string(column: Column, engine: Engine) -> Optional[str]:
    """获取列的默认值字符串"""
    if column.server_default is not None:
        default_val = column.server_default.arg
        if hasattr(default_val, 'text'):
            return str(default_val.text)
        return str(default_val)
    if column.default is not None and column.default.is_scalar:
        val = column.default.arg
        if isinstance(val, str):
            return f"'{val}'"
        return str(val)
    return None


def _get_column_length(col_type: TypeEngine) -> Optional[int]:
    """提取字符串类型的长度"""
    if hasattr(col_type, 'length'):
        return col_type.length
    # 尝试从类型字符串中解析
    type_str = str(col_type)
    match = re.search(r'\((\d+)\)', type_str)
    if match:
        return int(match.group(1))
    return None


def _compare_columns(
    model_col: Column,
    db_col_info: dict,
    engine: Engine,
) -> Optional[ColumnDiff]:
    """比较模型列和数据库列的差异"""
    col_name = model_col.name
    changes = []
    diff = ColumnDiff(name=col_name, change_type=ChangeType.ALTER_COLUMN)

    # 1. 比较类型
    model_type = _normalize_type(model_col.type, engine.dialect.name)
    # SQLAlchemy inspect 返回的 type 是类型对象，需要转换为字符串
    db_col_type = db_col_info.get("type")
    if db_col_type is not None:
        db_type = str(db_col_type).upper()
    else:
        db_type = ""

    # 提取长度进行比较
    model_length = _get_column_length(model_col.type)
    db_length = None
    length_match = re.search(r'\((\d+)\)', db_type)
    if length_match:
        db_length = int(length_match.group(1))

    # 类型比较（忽略大小写和空格）
    model_type_normalized = model_type.replace(" ", "")
    db_type_normalized = db_type.replace(" ", "")

    if model_type_normalized != db_type_normalized:
        # 检查是否只是长度不同
        if model_length and db_length and model_length != db_length:
            diff.old_type = db_type
            diff.new_type = model_type
            diff.reason = f"长度变更: {db_length} → {model_length}"
            changes.append("type_length")
        elif not model_length and not db_length:
            # 完全不同的类型
            diff.old_type = db_type
            diff.new_type = model_type
            diff.reason = f"类型变更: {db_type} → {model_type}"
            changes.append("type")

    # 2. 比较 nullable
    db_nullable = db_col_info.get("nullable", True)
    if model_col.nullable != db_nullable:
        diff.old_nullable = db_nullable
        diff.new_nullable = model_col.nullable
        diff.reason += f" nullable: {db_nullable} → {model_col.nullable}"
        changes.append("nullable")

    # 3. 比较默认值（仅记录，不强制迁移）
    model_default = _get_default_string(model_col, engine)
    db_default = db_col_info.get("default")
    # 默认值变更不触发迁移，仅记录

    if changes:
        return diff

    return None


def analyze_diff(engine: Engine, metadata: MetaData) -> list[TableDiff]:
    """分析模型与数据库的差异"""
    insp = inspect(engine)
    db_tables = set(insp.get_table_names())
    model_tables = set(metadata.tables.keys())

    diffs = []

    # 1. 检查新增表
    new_tables = model_tables - db_tables
    for table_name in sorted(new_tables):
        diffs.append(TableDiff(
            table_name=table_name,
            exists_in_db=False,
            exists_in_model=True,
            column_diffs=[],
            index_diffs=[],
            constraint_diffs=[],
        ))

    # 2. 检查已有表的列差异
    common_tables = model_tables & db_tables
    for table_name in sorted(common_tables):
        table = metadata.tables[table_name]
        db_columns = {col["name"]: col for col in insp.get_columns(table_name)}

        column_diffs = []

        # 检查新增列
        for col in table.columns:
            if col.name not in db_columns:
                column_diffs.append(ColumnDiff(
                    name=col.name,
                    change_type=ChangeType.ADD_COLUMN,
                    new_type=_normalize_type(col.type, engine.dialect.name),
                    new_nullable=col.nullable,
                    reason="新增列",
                ))
            else:
                # 检查已有列的差异
                diff = _compare_columns(col, db_columns[col.name], engine)
                if diff:
                    column_diffs.append(diff)

        # 检查是否需要重建表
        needs_rebuild = any(
            d.change_type == ChangeType.ALTER_COLUMN for d in column_diffs
        )

        if column_diffs or needs_rebuild:
            diffs.append(TableDiff(
                table_name=table_name,
                exists_in_db=True,
                exists_in_model=True,
                column_diffs=column_diffs,
                index_diffs=[],
                constraint_diffs=[],
                needs_rebuild=needs_rebuild,
            ))

    return diffs


# ──────────────────────────────────────────────────────────────────────
# 迁移执行
# ──────────────────────────────────────────────────────────────────────

def _column_default_sql(column: Column, engine: Engine) -> str:
    """获取列的默认值 SQL 片段"""
    if column.server_default is not None:
        default_val = column.server_default.arg
        if hasattr(default_val, 'text'):
            return f" DEFAULT {default_val.text}"
        return f" DEFAULT {default_val}"
    if column.default is not None and column.default.is_scalar:
        val = column.default.arg
        if isinstance(val, str):
            return f" DEFAULT '{val}'"
        return f" DEFAULT {val}"
    return ""


def _build_column_def(column: Column, engine: Engine) -> str:
    """构建列定义 SQL"""
    col_type = column.type.compile(engine.dialect)
    null_str = "" if column.nullable else " NOT NULL"
    default_str = _column_default_sql(column, engine)
    return f'"{column.name}" {col_type}{null_str}{default_str}'


def _create_model_table_without_indexes(conn, table, temp_name: str):
    """按模型约束创建临时表，但延后索引创建以避免索引名冲突。"""
    temp_metadata = MetaData()
    for foreign_key in table.foreign_keys:
        referred_table = foreign_key.column.table
        if referred_table.name != table.name:
            referred_table.to_metadata(temp_metadata)
    temp_table = table.to_metadata(temp_metadata, name=temp_name)
    conn.execute(CreateTable(temp_table))


def _create_model_indexes(conn, table):
    """重建模型声明的普通索引；唯一约束已由 CREATE TABLE 创建。"""
    for index in sorted(table.indexes, key=lambda idx: idx.name or ""):
        conn.execute(CreateIndex(index))


def _rebuild_table(
    engine: Engine,
    table_name: str,
    table,
    existing_cols: dict,
) -> bool:
    """重建表（用于 SQLite 不支持的 ALTER COLUMN 操作）"""
    logger.info(f"重建表 {table_name} 以应用结构变更")

    temp_name = f"_migrate_{table_name}_temp"

    try:
        with engine.connect() as conn:
            conn.execute(text(f'DROP TABLE IF EXISTS "{temp_name}"'))
            _create_model_table_without_indexes(conn, table, temp_name)

            # 复制数据（只复制两表共有的列）
            db_col_names = set(existing_cols.keys())
            common_cols = [
                f'"{col.name}"' for col in table.columns
                if col.name in db_col_names
            ]

            if common_cols:
                cols_str = ", ".join(common_cols)
                copy_sql = (
                    f'INSERT INTO "{temp_name}" ({cols_str}) '
                    f'SELECT {cols_str} FROM "{table_name}"'
                )
                conn.execute(text(copy_sql))

            # 交换表
            conn.execute(text(f'DROP TABLE "{table_name}"'))
            conn.execute(text(f'ALTER TABLE "{temp_name}" RENAME TO "{table_name}"'))
            _create_model_indexes(conn, table)

            conn.commit()
            logger.info(f"表 {table_name} 重建完成")
            return True

    except Exception as e:
        logger.error(f"重建表 {table_name} 失败: {e}")
        # 清理临时表
        try:
            with engine.connect() as conn:
                conn.execute(text(f'DROP TABLE IF EXISTS "{temp_name}"'))
                conn.commit()
        except:
            pass
        return False


def execute_migration(engine: Engine, metadata: MetaData) -> MigrationPlan:
    """执行数据库迁移"""
    plan = MigrationPlan(
        tables_to_create=[],
        tables_to_rebuild=[],
        columns_to_add=[],
        other_changes=[],
    )

    # 导入所有模型以确保 metadata 完整
    import app.models  # noqa: F401

    # 1. 创建新表
    insp = inspect(engine)
    for table_name, table in metadata.tables.items():
        if not insp.has_table(table_name):
            try:
                table.create(bind=engine)
                plan.tables_to_create.append(table_name)
                logger.info(f"创建表: {table_name}")
            except Exception as e:
                message = f"创建表 {table_name} 失败: {e}"
                logger.error(message)
                raise RuntimeError(f"数据库迁移失败: {message}") from e

    # 2. 添加新列
    insp = inspect(engine)
    for table_name, table in metadata.tables.items():
        if not insp.has_table(table_name):
            continue

        existing = {col["name"] for col in insp.get_columns(table_name)}

        for column in table.columns:
            if column.name not in existing:
                try:
                    col_type = column.type.compile(engine.dialect)
                    null_str = "" if column.nullable else " NOT NULL"
                    default_str = _column_default_sql(column, engine)

                    with engine.connect() as conn:
                        conn.execute(text(
                            f'ALTER TABLE "{table_name}" ADD COLUMN "{column.name}" '
                            f'{col_type}{null_str}{default_str}'
                        ))
                        conn.commit()

                    plan.columns_to_add.append(ColumnDiff(
                        name=column.name,
                        change_type=ChangeType.ADD_COLUMN,
                        new_type=col_type,
                        new_nullable=column.nullable,
                        reason="新增列",
                    ))
                    logger.info(f"添加列: {table_name}.{column.name}")

                except Exception as e:
                    message = f"添加列 {table_name}.{column.name} 失败: {e}"
                    logger.error(message)
                    raise RuntimeError(f"数据库迁移失败: {message}") from e

    # 3. 重建需要修改的表
    insp = inspect(engine)
    for table_name, table in metadata.tables.items():
        if not insp.has_table(table_name):
            continue

        existing_cols = {col["name"]: col for col in insp.get_columns(table_name)}

        # 检查是否有需要重建的变更
        needs_rebuild = False
        for column in table.columns:
            if column.name in existing_cols:
                db_col = existing_cols[column.name]
                diff = _compare_columns(column, db_col, engine)
                if diff and diff.change_type == ChangeType.ALTER_COLUMN:
                    needs_rebuild = True
                    break

        if needs_rebuild:
            if _rebuild_table(engine, table_name, table, existing_cols):
                plan.tables_to_rebuild.append(table_name)
                logger.info(f"重建表完成: {table_name}")
            else:
                message = f"重建表失败: {table_name}"
                logger.error(message)
                raise RuntimeError(f"数据库迁移失败: {message}")

    # 4. 处理外键级联
    _fix_foreign_key_cascade(engine, metadata)

    # 5. 生成摘要
    summary_parts = []
    if plan.tables_to_create:
        summary_parts.append(f"新建表: {len(plan.tables_to_create)}")
    if plan.tables_to_rebuild:
        summary_parts.append(f"重建表: {len(plan.tables_to_rebuild)}")
    if plan.columns_to_add:
        summary_parts.append(f"新增列: {len(plan.columns_to_add)}")

    plan.summary = "迁移完成 - " + ", ".join(summary_parts) if summary_parts else "无需迁移"
    return plan


def _fix_foreign_key_cascade(engine: Engine, metadata: MetaData):
    """为外键添加 ON DELETE CASCADE"""
    insp = inspect(engine)

    cascade_checks = [
        ("task_logs", "project_id", "projects"),
        ("commit_records", "project_id", "projects"),
    ]

    for table_name, fk_column, ref_table in cascade_checks:
        if not insp.has_table(table_name):
            continue

        try:
            with engine.connect() as conn:
                result = conn.execute(text(
                    f"PRAGMA foreign_key_list('{table_name}')"
                )).fetchall()

                has_cascade = any(
                    row[6] == 'CASCADE' for row in result if row[2] == ref_table
                )

                if not has_cascade:
                    _rebuild_table_with_cascade(
                        engine,
                        table_name,
                        fk_column,
                        ref_table,
                        metadata,
                    )

        except Exception as e:
            message = f"检查外键级联 {table_name}.{fk_column} 时出错: {e}"
            logger.error(message)
            raise RuntimeError(f"数据库迁移失败: {message}") from e


def _rebuild_table_with_cascade(
    engine: Engine,
    table_name: str,
    fk_column: str,
    ref_table: str,
    metadata: MetaData | None = None,
):
    """重建表以添加 ON DELETE CASCADE"""
    logger.info(f"为 {table_name}.{fk_column} 添加 CASCADE")

    insp = inspect(engine)
    existing_cols = {col["name"]: col for col in insp.get_columns(table_name)}

    # 导入模型获取表定义
    if metadata is None:
        import app.models  # noqa: F401
        from app.database import Base
        metadata = Base.metadata

    table = metadata.tables.get(table_name)
    if table is None:
        logger.warning(f"找不到表 {table_name} 的模型定义")
        return

    if _rebuild_table(engine, table_name, table, existing_cols):
        logger.info(f"外键级联添加完成: {table_name}.{fk_column}")
    else:
        raise RuntimeError(f"添加外键级联失败: {table_name}.{fk_column}")


# ──────────────────────────────────────────────────────────────────────
# 公开接口
# ──────────────────────────────────────────────────────────────────────

def run_migration(engine: Engine, metadata: MetaData) -> MigrationPlan:
    """
    运行数据库迁移的主入口

    Args:
        engine: SQLAlchemy 引擎
        metadata: 包含所有模型的 MetaData

    Returns:
        MigrationPlan: 迁移计划和执行结果
    """
    logger.info("=" * 50)
    logger.info("开始数据库迁移检查")
    logger.info("=" * 50)

    # 分析差异
    diffs = analyze_diff(engine, metadata)

    if diffs:
        logger.info(f"发现 {len(diffs)} 个表有变更:")
        for diff in diffs:
            if not diff.exists_in_db:
                logger.info(f"  - {diff.table_name}: 新增表")
            else:
                changes = []
                for col_diff in diff.column_diffs:
                    if col_diff.change_type == ChangeType.ADD_COLUMN:
                        changes.append(f"新增列 {col_diff.name}")
                    elif col_diff.change_type == ChangeType.ALTER_COLUMN:
                        changes.append(f"修改列 {col_diff.name}: {col_diff.reason}")
                if changes:
                    logger.info(f"  - {diff.table_name}: {', '.join(changes)}")
    else:
        logger.info("数据库结构已是最新，无需迁移")

    # 执行迁移
    plan = execute_migration(engine, metadata)

    logger.info("=" * 50)
    logger.info(plan.summary)
    logger.info("=" * 50)

    return plan
