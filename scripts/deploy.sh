#!/bin/bash
# GitLab Code Review Tool - 生产环境部署脚本
# 包含代码更新、依赖安装、数据库迁移、服务重启

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 默认配置
INSTALL_DIR="${INSTALL_DIR:-/opt/gitlab-code-review-tool}"
SERVICE_NAME="gitlab-code-review"
BACKUP_DIR="${INSTALL_DIR}/data/backups"
MAX_BACKUPS=10

# 日志函数
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_step() {
    echo -e "${BLUE}[STEP]${NC} $1"
}

# 显示帮助
show_help() {
    cat << EOF
GitLab Code Review Tool - 部署脚本

用法: $0 [选项]

选项:
    --migrate-only      仅执行数据库迁移
    --no-backup         跳过数据库备份
    --no-migrate        跳过数据库迁移
    --force             强制执行（跳过确认）
    --help, -h          显示帮助

环境变量:
    INSTALL_DIR         安装目录（默认: /opt/gitlab-code-review-tool）

示例:
    $0                          # 完整部署
    $0 --migrate-only           # 仅迁移
    $0 --no-backup              # 跳过备份
    INSTALL_DIR=/custom/path $0 # 自定义安装目录

EOF
}

# 检查是否为 root 用户
check_root() {
    if [ "$EUID" -ne 0 ]; then
        log_error "请使用 root 或 sudo 运行此脚本"
        exit 1
    fi
}

# 检查安装目录
check_install_dir() {
    if [ ! -d "$INSTALL_DIR" ]; then
        log_error "安装目录不存在: $INSTALL_DIR"
        exit 1
    fi

    if [ ! -f "$INSTALL_DIR/run.py" ]; then
        log_error "无效的安装目录: $INSTALL_DIR/run.py 不存在"
        exit 1
    fi
}

# 停止服务
stop_service() {
    log_step "停止服务..."
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        systemctl stop "$SERVICE_NAME"
        log_info "服务已停止"
    else
        log_warn "服务未运行"
    fi
}

# 启动服务
start_service() {
    log_step "启动服务..."
    systemctl start "$SERVICE_NAME"
    sleep 2

    if systemctl is-active --quiet "$SERVICE_NAME"; then
        log_info "服务已启动"
    else
        log_error "服务启动失败"
        log_error "请检查日志: journalctl -u $SERVICE_NAME -f"
        exit 1
    fi
}

# 重启服务
restart_service() {
    log_step "重启服务..."
    systemctl restart "$SERVICE_NAME"
    sleep 2

    if systemctl is-active --quiet "$SERVICE_NAME"; then
        log_info "服务已重启"
    else
        log_error "服务重启失败"
        log_error "请检查日志: journalctl -u $SERVICE_NAME -f"
        exit 1
    fi
}

# 备份数据库
backup_database() {
    local db_path="${INSTALL_DIR}/data/config.db"

    if [ ! -f "$db_path" ]; then
        log_warn "数据库文件不存在，跳过备份"
        return
    fi

    log_step "备份数据库..."
    mkdir -p "$BACKUP_DIR"

    local timestamp=$(date +%Y%m%d_%H%M%S)
    local backup_file="${BACKUP_DIR}/config.db.backup.${timestamp}"

    cp "$db_path" "$backup_file"
    log_info "数据库已备份到: $backup_file"

    # 清理旧备份（保留最近 N 个）
    local backup_count=$(ls -1 "${BACKUP_DIR}"/config.db.backup.* 2>/dev/null | wc -l)
    if [ "$backup_count" -gt "$MAX_BACKUPS" ]; then
        log_info "清理旧备份（保留最近 ${MAX_BACKUPS} 个）..."
        ls -1t "${BACKUP_DIR}"/config.db.backup.* | tail -n +$((MAX_BACKUPS + 1)) | xargs rm -f
    fi
}

# 更新代码
update_code() {
    log_step "更新代码..."
    cd "$INSTALL_DIR"

    # 检查是否有未提交的更改
    if [ -n "$(git status --porcelain)" ]; then
        log_warn "检测到未提交的更改:"
        git status --short
        echo ""
        read -p "是否继续？(y/N): " -n 1 -r
        echo ""
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            log_info "部署已取消"
            exit 0
        fi
    fi

    # 拉取最新代码
    git pull origin master
    log_info "代码已更新到最新版本"
}

# 更新依赖
update_dependencies() {
    log_step "更新依赖..."
    cd "$INSTALL_DIR"
    source venv/bin/activate
    pip install -r requirements.txt -q
    log_info "依赖已更新"
}

# 执行数据库迁移
run_migration() {
    log_step "执行数据库迁移..."
    cd "$INSTALL_DIR"
    source venv/bin/activate

    # 检查是否有变更
    set +e
    check_output=$(python scripts/migrate.py --check 2>&1)
    check_status=$?
    set -e

    if [ "$check_status" -eq 0 ]; then
        log_info "检测到数据库结构变更，执行迁移..."
        python scripts/migrate.py --verbose
    elif [ "$check_status" -eq 1 ]; then
        log_info "数据库结构已是最新，无需迁移"
    else
        log_error "检查数据库迁移失败"
        echo "$check_output"
        exit "$check_status"
    fi
}

# 显示部署信息
show_deployment_info() {
    echo ""
    echo "=========================================="
    echo -e "${GREEN}部署完成！${NC}"
    echo "=========================================="
    echo ""
    echo "服务状态:"
    systemctl status "$SERVICE_NAME" --no-pager | head -5
    echo ""
    echo "常用命令:"
    echo "  查看状态: systemctl status $SERVICE_NAME"
    echo "  查看日志: journalctl -u $SERVICE_NAME -f"
    echo "  重启服务: systemctl restart $SERVICE_NAME"
    echo "  停止服务: systemctl stop $SERVICE_NAME"
    echo ""
    echo "数据库备份:"
    echo "  备份目录: $BACKUP_DIR"
    echo "  回滚命令: cp $BACKUP_DIR/config.db.backup.<timestamp> $INSTALL_DIR/data/config.db"
    echo ""
    echo "=========================================="
}

# 确认部署
confirm_deployment() {
    echo ""
    echo "=========================================="
    echo "GitLab Code Review Tool - 部署确认"
    echo "=========================================="
    echo ""
    echo "安装目录: $INSTALL_DIR"
    echo "服务名称: $SERVICE_NAME"
    echo ""
    echo "将执行以下操作:"
    echo "  1. 停止服务"
    echo "  2. 备份数据库"
    echo "  3. 更新代码"
    echo "  4. 更新依赖"
    echo "  5. 执行数据库迁移"
    echo "  6. 启动服务"
    echo ""
    read -p "是否继续？(y/N): " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        log_info "部署已取消"
        exit 0
    fi
}

# 主函数
main() {
    local migrate_only=false
    local no_backup=false
    local no_migrate=false
    local force=false

    # 解析参数
    while [[ $# -gt 0 ]]; do
        case $1 in
            --migrate-only)
                migrate_only=true
                shift
                ;;
            --no-backup)
                no_backup=true
                shift
                ;;
            --no-migrate)
                no_migrate=true
                shift
                ;;
            --force)
                force=true
                shift
                ;;
            --help|-h)
                show_help
                exit 0
                ;;
            *)
                log_error "未知参数: $1"
                show_help
                exit 1
                ;;
        esac
    done

    # 检查环境
    check_root
    check_install_dir

    # 仅迁移模式
    if [ "$migrate_only" = true ]; then
        if [ "$no_backup" = false ]; then
            backup_database
        fi
        run_migration
        restart_service
        show_deployment_info
        exit 0
    fi

    # 确认部署
    if [ "$force" = false ]; then
        confirm_deployment
    fi

    # 执行部署
    stop_service

    if [ "$no_backup" = false ]; then
        backup_database
    fi

    update_code
    update_dependencies

    if [ "$no_migrate" = false ]; then
        run_migration
    fi

    start_service
    show_deployment_info
}

# 运行主函数
main "$@"
