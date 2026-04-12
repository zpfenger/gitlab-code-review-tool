#!/usr/bin/env python3
"""
初始化管理员账号脚本
用于在首次部署或重置后创建系统管理员账号
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import init_db, SessionLocal
from app.models import User, Role
from app.security import security_service


def create_admin_user(username: str = None, password: str = None):
    """创建系统管理员账号"""
    init_db()

    db = SessionLocal()
    try:
        # 检查是否已有系统管理员
        admin_role = db.query(Role).filter(Role.name == Role.SYSTEM_ADMIN).first()
        if not admin_role:
            print("错误: 系统管理员角色不存在，请先重启应用初始化数据库")
            return False

        # 检查是否已有管理员账号
        existing_admin = db.query(User).join(User.roles).filter(Role.name == Role.SYSTEM_ADMIN).first()
        if existing_admin:
            print(f"系统管理员账号已存在: {existing_admin.username}")
            return True

        # 交互式获取用户名密码
        if not username:
            username = input("请输入管理员用户名: ").strip()
        if not password:
            password = input("请输入管理员密码（至少6位）: ").strip()

        if not username:
            print("错误: 用户名不能为空")
            return False

        if len(password) < 6:
            print("错误: 密码长度不能少于6位")
            return False

        # 创建管理员账号
        user = User(
            username=username,
            password_hash=security_service.hash_password(password),
            is_active=True,
        )
        user.roles.append(admin_role)
        db.add(user)
        db.commit()

        print(f"成功创建系统管理员账号: {username}")
        return True

    except Exception as e:
        print(f"错误: {e}")
        db.rollback()
        return False
    finally:
        db.close()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='初始化系统管理员账号')
    parser.add_argument('--username', '-u', help='管理员用户名')
    parser.add_argument('--password', '-p', help='管理员密码（至少6位）')
    args = parser.parse_args()

    success = create_admin_user(args.username, args.password)
    sys.exit(0 if success else 1)
