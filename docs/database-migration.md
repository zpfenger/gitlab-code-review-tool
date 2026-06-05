# 数据库迁移指南

## 概述

本项目使用通用自动迁移模块，能够自动对比 SQLAlchemy 模型定义与实际数据库结构，并执行迁移。

### 支持的变更类型

| 变更类型 | 自动处理 | 说明 |
|----------|----------|------|
| 新增表 | ✅ | 自动创建 |
| 新增列 | ✅ | 自动添加，支持默认值 |
| 修改列类型/长度 | ✅ | 自动重建表（SQLite 限制） |
| 修改 nullable 约束 | ✅ | 自动重建表 |
| 修改默认值 | ⚠️ | 仅影响新记录 |
| 删除列 | ❌ | SQLite 不支持，需手动 |
| 重命名列 | ❌ | SQLite 不支持，需手动 |

## 使用方式

### 1. 自动迁移（应用启动时）

应用启动时会自动执行迁移，无需手动操作：

```bash
# 启动应用
python run.py

# 或使用 systemd
sudo systemctl start gitlab-code-review
```

日志中会显示迁移结果：
```
12:34:56 | INFO     | 开始数据库迁移检查
12:34:56 | INFO     | 发现 2 个表有变更:
12:34:56 | INFO     |   - settings: 新增列 external_api_key
12:34:56 | INFO     |   - employee_efficiency_daily: 新增表
12:34:56 | INFO     | 迁移完成 - 新建表: 1, 新增列: 1
```

### 2. 独立迁移脚本

#### 基本用法

```bash
# 检查并执行迁移
python scripts/migrate.py

# 仅预览变更（不执行）
python scripts/migrate.py --dry-run

# 仅检查是否有变更
python scripts/migrate.py --check

# 迁移前自动备份
python scripts/migrate.py --backup

# 显示详细日志
python scripts/migrate.py --verbose

# 指定数据库路径
python scripts/migrate.py --db-path /path/to/config.db
```

#### 命令行参数

| 参数 | 说明 |
|------|------|
| `--dry-run` | 仅预览变更，不执行迁移 |
| `--check` | 检查是否有变更（返回码 0=有变更，1=无变更） |
| `--backup` | 迁移前自动备份数据库 |
| `--verbose`, `-v` | 显示详细日志 |
| `--db-path` | 指定数据库文件路径 |

### 3. 生产环境部署

#### 方式一：使用部署脚本

```bash
# 完整部署（包含迁移）
sudo ./scripts/deploy.sh

# 仅执行迁移
sudo ./scripts/deploy.sh --migrate-only
```

#### 方式二：手动部署

```bash
# 1. 停止服务
sudo systemctl stop gitlab-code-review

# 2. 备份数据库
cp /opt/gitlab-code-review-tool/data/config.db \
   /opt/gitlab-code-review-tool/data/config.db.backup.$(date +%Y%m%d%H%M%S)

# 3. 更新代码
cd /opt/gitlab-code-review-tool
git pull origin master

# 4. 更新依赖
source venv/bin/activate
pip install -r requirements.txt -q

# 5. 执行迁移（可选，启动时会自动执行）
python scripts/migrate.py --backup

# 6. 启动服务
sudo systemctl start gitlab-code-review

# 7. 检查状态
sudo systemctl status gitlab-code-review
journalctl -u gitlab-code-review -f
```

## 迁移模块架构

### 文件结构

```
app/
├── migration.py      # 通用迁移模块
├── database.py       # 数据库配置和初始化
└── models/           # SQLAlchemy 模型定义

scripts/
├── migrate.py        # 独立迁移脚本
└── deploy.sh         # 部署脚本（可选）
```

### 迁移流程

```
1. 初始化
   └── 加载所有模型到 MetaData

2. 结构对比
   ├── 检查新增表
   ├── 检查新增列
   ├── 检查列类型/长度变更
   └── 检查约束变更

3. 执行迁移
   ├── 创建新表 (CREATE TABLE)
   ├── 添加新列 (ALTER TABLE ADD COLUMN)
   ├── 重建表 (CREATE temp → INSERT → DROP → RENAME)
   └── 修复外键级联

4. 兼容处理
   ├── 数据迁移（如旧字段迁移到新字段）
   ├── 唯一索引修复
   └── 系统角色初始化
```

### 核心函数

| 函数 | 说明 |
|------|------|
| `run_migration()` | 迁移主入口 |
| `analyze_diff()` | 分析结构差异 |
| `execute_migration()` | 执行迁移计划 |
| `_rebuild_table()` | 重建表（处理 ALTER COLUMN） |

## 常见问题

### Q: 为什么需要重建表？

SQLite 不支持 `ALTER COLUMN` 语句，无法直接修改列类型、长度或约束。因此需要：
1. 创建临时表（新结构）
2. 复制数据
3. 删除旧表
4. 将临时表重命名为原表名

### Q: 重建表会影响数据吗？

不会。重建表会保留所有数据，只是改变表结构。但建议在生产环境迁移前备份数据库。

### Q: 如何添加新表或新字段？

1. 在 `app/models/` 目录创建或修改模型文件
2. 在 `app/models/__init__.py` 中导入新模型
3. 提交代码并部署
4. 应用启动时会自动执行迁移

### Q: 如何处理删除列？

SQLite 不支持 `DROP COLUMN`（3.35.0 之前版本）。如果需要删除列：
1. 手动重建表（移除该列）
2. 或使用 `--dry-run` 预览后手动执行 SQL

### Q: 如何回滚迁移？

```bash
# 1. 停止服务
sudo systemctl stop gitlab-code-review

# 2. 恢复备份
cp data/config.db.backup.YYYYMMDDHHMMSS data/config.db

# 3. 回滚代码（可选）
git checkout <previous-commit>

# 4. 启动服务
sudo systemctl start gitlab-code-review
```

## 最佳实践

### 开发环境

```bash
# 切换分支后同步数据库
git checkout feature-branch
python scripts/migrate.py --dry-run  # 预览变更
python scripts/migrate.py            # 执行迁移
```

### 生产环境

```bash
# 部署新版本
python scripts/migrate.py --backup   # 迁移并备份
sudo systemctl restart gitlab-code-review
```

### CI/CD

```yaml
# GitHub Actions 示例
- name: Run migrations
  run: |
    python scripts/migrate.py --check
    if [ $? -eq 0 ]; then
      python scripts/migrate.py --backup
    fi
```

## 扩展：自定义迁移

如果需要执行自定义数据迁移，可以在 `app/database.py` 中添加：

```python
def _custom_data_migration():
    """自定义数据迁移"""
    insp = inspect(engine)
    if not insp.has_table('your_table'):
        return

    with engine.connect() as conn:
        # 执行自定义 SQL
        conn.execute(text("UPDATE your_table SET ..."))
        conn.commit()
        logger.info("Custom migration completed")

# 在 init_db() 中调用
def init_db():
    from app.migration import run_migration
    run_migration(engine, Base.metadata)
    
    # 自定义迁移
    _custom_data_migration()
```
