from sqlalchemy import create_engine, inspect, text, event
from sqlalchemy.orm import sessionmaker, declarative_base
from loguru import logger
import os

DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///./data/config.db')

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}  # SQLite 需要
)


@event.listens_for(engine, "connect")
def _enable_foreign_keys(dbapi_conn, connection_record):
    """每个新连接启用外键约束（SQLite 默认关闭）"""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """获取数据库会话（依赖注入）"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """初始化数据库（创建表 + 自动迁移）"""
    os.makedirs(os.path.dirname(DATABASE_URL.replace('sqlite:///', '')), exist_ok=True)
    Base.metadata.create_all(bind=engine)

    # 自动迁移：添加缺失列 + 数据迁移 + 修复约束变更 + 唯一索引修复 + 外键级联修复
    _migrate_columns()
    _migrate_schedule_data()
    _fix_nullable_mismatches()
    _migrate_webhook_unique_indexes()
    _fix_foreign_key_cascade()
    _init_system_roles()


def _column_default_sql(column):
    """获取列的默认值 SQL 片段"""
    if column.server_default is not None:
        return f" DEFAULT {column.server_default.arg}"
    if column.default is not None and column.default.is_scalar:
        val = column.default.arg
        if isinstance(val, str):
            return f" DEFAULT '{val}'"
        return f" DEFAULT {val}"
    return ""


def _migrate_columns():
    """检测并添加模型中定义但数据库中缺失的列"""
    with engine.connect() as conn:
        insp = inspect(engine)
        for table_name, table in Base.metadata.tables.items():
            if not insp.has_table(table_name):
                continue
            existing = {col['name'] for col in insp.get_columns(table_name)}
            for column in table.columns:
                if column.name not in existing:
                    col_type = column.type.compile(engine.dialect)
                    null_str = "" if column.nullable else " NOT NULL"
                    default_str = _column_default_sql(column)
                    conn.execute(text(
                        f'ALTER TABLE "{table_name}" ADD COLUMN "{column.name}" {col_type}{null_str}{default_str}'
                    ))
                    logger.info(f"Added column {table_name}.{column.name}")
        conn.commit()


def _migrate_schedule_data():
    """将旧的 schedule_times/review_days 数据迁移到新的 daily_* 字段"""
    insp = inspect(engine)
    if not insp.has_table('settings'):
        return

    existing = {col['name'] for col in insp.get_columns('settings')}

    # 旧字段存在且新字段也存在时进行数据迁移
    if 'schedule_times' in existing and 'daily_schedule_times' in existing:
        with engine.connect() as conn:
            # 检查是否已迁移（daily_schedule_times 有非默认值）
            result = conn.execute(text(
                "SELECT COUNT(*) FROM settings WHERE daily_schedule_times IS NOT NULL AND daily_schedule_times != '[\"09:00\"]'"
            )).scalar()
            if result and result > 0:
                return  # 已迁移过

            # 从旧字段迁移数据
            conn.execute(text(
                "UPDATE settings SET "
                "daily_schedule_times = COALESCE(schedule_times, '[\"09:00\"]'), "
                "daily_review_days = COALESCE(review_days, 1) "
                "WHERE schedule_times IS NOT NULL"
            ))
            conn.commit()
            logger.info("Migrated schedule_times → daily_schedule_times, review_days → daily_review_days")


def _fix_nullable_mismatches():
    """修复已有列的 nullable 约束变更（SQLite 需重建表）"""
    insp = inspect(engine)

    for table_name, table in Base.metadata.tables.items():
        if not insp.has_table(table_name):
            continue

        existing_cols = {col['name']: col for col in insp.get_columns(table_name)}

        # 检测 nullable 不匹配的列
        mismatches = []
        for column in table.columns:
            if column.name in existing_cols:
                if existing_cols[column.name]['nullable'] != column.nullable:
                    mismatches.append(column.name)

        if not mismatches:
            continue

        _rebuild_table(table_name, table, existing_cols)


def _rebuild_table(table_name, table, existing_cols):
    """重建 SQLite 表以修改列约束"""
    logger.info(f"Rebuilding table {table_name} to fix nullable: column mismatches")

    temp_name = "_temp_migrate_" + table_name

    with engine.connect() as conn:
        # 构建新表列定义
        col_defs = []
        for column in table.columns:
            col_type = column.type.compile(engine.dialect)
            null_str = "" if column.nullable else " NOT NULL"
            default_str = _column_default_sql(column)
            col_defs.append('"' + column.name + '" ' + col_type + null_str + default_str)

        # 主键
        pk_cols = [col.name for col in table.primary_key.columns]
        pk_str = ""
        if pk_cols:
            pk_str = ', PRIMARY KEY (' + ", ".join('"' + c + '"' for c in pk_cols) + ')'

        # 创建临时表
        create_sql = 'CREATE TABLE "' + temp_name + '" (' + ", ".join(col_defs) + pk_str + ')'
        conn.execute(text(create_sql))

        # 只复制两表共有的列（按模型顺序）
        db_col_names = set(existing_cols.keys())
        common = ['"' + col.name + '"' for col in table.columns if col.name in db_col_names]
        cols_str = ", ".join(common)

        copy_sql = (
            'INSERT INTO "' + temp_name + '" (' + cols_str + ') '
            'SELECT ' + cols_str + ' FROM "' + table_name + '"'
        )
        conn.execute(text(copy_sql))

        # 交换表
        conn.execute(text('DROP TABLE "' + table_name + '"'))
        conn.execute(text('ALTER TABLE "' + temp_name + '" RENAME TO "' + table_name + '"'))

        conn.commit()

    logger.info(f"Table {table_name} rebuilt successfully")


def _fix_foreign_key_cascade():
    """为 task_logs 和 commit_records 的外键添加 ON DELETE CASCADE（SQLite 需重建表）"""
    insp = inspect(engine)

    with engine.connect() as conn:
        # 检查 task_logs.project_id 是否已有 CASCADE
        if insp.has_table("task_logs"):
            result = conn.execute(text(
                "PRAGMA foreign_key_list('task_logs')"
            )).fetchall()
            # row[6] = on_delete: 'NO ACTION' | 'CASCADE' | 'SET NULL' | ...
            task_logs_has_cascade = any(
                row[6] == 'CASCADE' for row in result if row[2] == 'projects'
            )
            if not task_logs_has_cascade:
                _rebuild_table_with_cascade("task_logs", "project_id", "projects")

        # 检查 commit_records.project_id 是否已有 CASCADE
        if insp.has_table("commit_records"):
            result = conn.execute(text(
                "PRAGMA foreign_key_list('commit_records')"
            )).fetchall()
            commit_records_has_cascade = any(
                row[6] == 'CASCADE' for row in result if row[2] == 'projects'
            )
            if not commit_records_has_cascade:
                _rebuild_table_with_cascade("commit_records", "project_id", "projects")

        conn.commit()


def _rebuild_table_with_cascade(table_name: str, fk_column: str, ref_table: str):
    """重建 SQLite 表，为指定外键列添加 ON DELETE CASCADE"""
    logger.info(f"Rebuilding table {table_name} to add CASCADE on {fk_column}")

    # 导入模型使 Base.metadata 注册该表
    from app.models.task_log import TaskLog
    from app.models.commit_record import CommitRecord
    _ = TaskLog, CommitRecord

    insp = inspect(engine)
    existing_cols = {col['name']: col for col in insp.get_columns(table_name)}
    table = Base.metadata.tables[table_name]

    temp_name = "_temp_cascade_" + table_name
    with engine.connect() as conn:
        col_defs = []
        for column in table.columns:
            col_type = column.type.compile(engine.dialect)
            null_str = "" if column.nullable else " NOT NULL"
            default_str = _column_default_sql(column)
            col_defs.append('"' + column.name + '" ' + col_type + null_str + default_str)

        # 为目标外键追加 ON DELETE CASCADE
        for i, col_def in enumerate(col_defs):
            if table.columns[i].foreign_keys and any(
                fk.target_fullname == f'"{ref_table}".id' for fk in table.columns[i].foreign_keys
            ):
                col_defs[i] = col_def + " ON DELETE CASCADE"

        pk_cols = [col.name for col in table.primary_key.columns]
        pk_str = ", PRIMARY KEY (" + ", ".join('"' + c + '"' for c in pk_cols) + ")" if pk_cols else ""

        # 追加显式 CASCADE 外键约束（防重）
        col_defs.append(f'FOREIGN KEY ("{fk_column}") REFERENCES "{ref_table}"("id") ON DELETE CASCADE')

        create_sql = 'CREATE TABLE "' + temp_name + '" (' + ", ".join(col_defs) + pk_str + ')'
        conn.execute(text(create_sql))

        db_col_names = set(existing_cols.keys())
        common = ['"' + col.name + '"' for col in table.columns if col.name in db_col_names]
        cols_str = ", ".join(common)
        copy_sql = 'INSERT INTO "' + temp_name + '" (' + cols_str + ') SELECT ' + cols_str + ' FROM "' + table_name + '"'
        conn.execute(text(copy_sql))
        conn.execute(text('DROP TABLE "' + table_name + '"'))
        conn.execute(text('ALTER TABLE "' + temp_name + '" RENAME TO "' + table_name + '"'))
        conn.commit()

    logger.info(f"Table {table_name} rebuilt with CASCADE on {fk_column}")


def _migrate_webhook_unique_indexes():
    """为 webhook 审查日志补充唯一索引，并清理历史重复数据。"""
    insp = inspect(engine)

    with engine.connect() as conn:
        if insp.has_table("mr_review_log"):
            conn.execute(text(
                'DELETE FROM "mr_review_log" '
                'WHERE id IN ('
                'SELECT t1.id FROM "mr_review_log" t1 '
                'JOIN "mr_review_log" t2 '
                'ON t1.project_name = t2.project_name '
                'AND COALESCE(t1.source_branch, \'\') = COALESCE(t2.source_branch, \'\') '
                'AND COALESCE(t1.target_branch, \'\') = COALESCE(t2.target_branch, \'\') '
                'AND t1.last_commit_id = t2.last_commit_id '
                'AND t1.last_commit_id IS NOT NULL '
                'AND t1.id < t2.id'
                ')'
            ))
            conn.execute(text(
                'CREATE UNIQUE INDEX IF NOT EXISTS "uq_mr_review_log_dedup" '
                'ON "mr_review_log" ("project_name", "source_branch", "target_branch", "last_commit_id")'
            ))

        if insp.has_table("push_review_log"):
            conn.execute(text(
                'DELETE FROM "push_review_log" '
                'WHERE id IN ('
                'SELECT t1.id FROM "push_review_log" t1 '
                'JOIN "push_review_log" t2 '
                'ON t1.project_name = t2.project_name '
                'AND COALESCE(t1.branch, \'\') = COALESCE(t2.branch, \'\') '
                'AND t1.last_commit_id = t2.last_commit_id '
                'AND t1.last_commit_id IS NOT NULL '
                'AND t1.id < t2.id'
                ')'
            ))
            conn.execute(text(
                'CREATE UNIQUE INDEX IF NOT EXISTS "uq_push_review_log_dedup" '
                'ON "push_review_log" ("project_name", "branch", "last_commit_id")'
            ))

        conn.commit()


def _init_system_roles():
    """初始化系统内置角色"""
    from app.models.user import Role, User
    from app.security import security_service

    # 确保表已创建
    insp = inspect(engine)
    if not insp.has_table('roles'):
        return
    if not insp.has_table('users'):
        return

    db = SessionLocal()
    try:
        # 内置角色定义
        system_roles = [
            {
                'name': 'system_admin',
                'description': '系统管理员 - 拥有系统全部权限',
                'is_system_role': True,
            },
            {
                'name': 'project_admin',
                'description': '项目管理员 - 可管理授权的项目',
                'is_system_role': True,
            },
            {
                'name': 'project_member',
                'description': '项目成员 - 只能查看授权的项目',
                'is_system_role': True,
            },
        ]

        # 创建内置角色（如果不存在）
        for role_data in system_roles:
            existing = db.query(Role).filter(Role.name == role_data['name']).first()
            if not existing:
                role = Role(**role_data)
                db.add(role)
                logger.info(f"Created system role: {role_data['name']}")

        db.commit()

        # 如果没有任何用户，将旧的 admin.yaml 中的管理员迁移为系统管理员
        user_count = db.query(User).count()
        if user_count == 0:
            try:
                from app.config import config_manager
                admin_config = config_manager.get_admin_config()
                
                # 创建系统管理员账号
                admin_user = User(
                    username=admin_config.username,
                    password_hash=admin_config.password_hash,
                    nickname=admin_config.nickname,
                    email=admin_config.email,
                    is_active=True,
                )
                db.add(admin_user)
                db.commit()
                db.refresh(admin_user)

                # 分配系统管理员角色
                system_admin_role = db.query(Role).filter(Role.name == 'system_admin').first()
                if system_admin_role and admin_user:
                    admin_user.roles.append(system_admin_role)
                    db.commit()
                    logger.info(f"Migrated admin user to new system: {admin_config.username}")
            except Exception as e:
                logger.warning(f"Could not migrate admin user: {e}")
        else:
            # 确保已有用户中至少有一个人是系统管理员
            admins_with_system_role = db.query(User).join(User.roles).filter(
                Role.name == 'system_admin'
            ).count()
            
            if admins_with_system_role == 0:
                # 没有系统管理员，给第一个用户分配 system_admin 角色
                first_user = db.query(User).first()
                system_admin_role = db.query(Role).filter(Role.name == 'system_admin').first()
                
                if first_user and system_admin_role:
                    first_user.roles.append(system_admin_role)
                    db.commit()
                    logger.info(f"Assigned system_admin role to existing user: {first_user.username}")

    except Exception as e:
        logger.error(f"Error initializing system roles: {e}")
        db.rollback()
    finally:
        db.close()

