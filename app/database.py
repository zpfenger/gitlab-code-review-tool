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

    # 使用通用迁移模块（处理所有类型的结构变更）
    from app.migration import run_migration
    plan = run_migration(engine, Base.metadata)

    # 兼容旧迁移：数据迁移和特殊处理
    _migrate_schedule_data()
    _migrate_webhook_unique_indexes()
    _init_system_roles()
    _migrate_remove_project_member_roles()


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


def _migrate_remove_project_member_roles():
    """DML 迁移：清理 user_roles 中关联到 project_member 的记录（幂等）"""
    from app.models.user import Role, user_roles
    insp = inspect(engine)
    if not insp.has_table('roles') or not insp.has_table('user_roles'):
        return
    db = SessionLocal()
    try:
        pm_role = db.query(Role).filter(Role.name == 'project_member').first()
        if pm_role:
            stmt = user_roles.delete().where(user_roles.c.role_id == pm_role.id)
            db.execute(stmt)
            db.commit()
            logger.info("已清理 user_roles 中的 project_member 关联")
    except Exception as e:
        logger.warning(f"清理 project_member 角色关联失败（可忽略）: {e}")
        db.rollback()
    finally:
        db.close()

