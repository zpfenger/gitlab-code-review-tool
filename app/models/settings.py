import json
from sqlalchemy import Column, String, Integer, Boolean, Text
from app.models.base import BaseModel


class Settings(BaseModel):
    """系统设置"""
    __tablename__ = "settings"

    # 全局 GitLab 配置
    global_gitlab_url = Column(String(500), nullable=False, default="", comment="GitLab 地址")
    global_gitlab_token = Column(String(500), nullable=True, comment="GitLab Token (加密存储)")

    # 大模型配置
    llm_api_url = Column(String(500), nullable=False, default="", comment="大模型 API 地址")
    llm_api_key = Column(String(500), nullable=True, comment="大模型 API Key (加密存储)")
    llm_model = Column(String(100), nullable=False, default="", comment="模型名称")
    llm_max_tokens = Column(Integer, default=4096, comment="最大 Token 数")
    llm_temperature = Column(String(10), default="0.7", comment="Temperature")
    llm_timeout = Column(Integer, default=240, comment="LLM 调用超时时间（秒）")
    llm_max_retries = Column(Integer, default=5, comment="最大重试次数")
    llm_retry_delay = Column(Integer, default=60, comment="重试间隔（秒）")

    # 提示词配置
    review_prompt_template = Column(Text, nullable=True, comment="代码审查提示词模板")
    weekly_review_prompt = Column(Text, nullable=True, comment="周报汇总提示词模板")
    stats_prompt = Column(Text, nullable=True, comment="代码统计提示词模板")

    # 全局 SVN 配置
    global_svn_url = Column(String(500), nullable=True, comment="SVN 仓库地址")
    global_svn_username = Column(String(100), nullable=True, comment="SVN 用户名")
    global_svn_password = Column(String(500), nullable=True, comment="SVN 密码 (加密存储)")

    # 每日调度配置
    daily_schedule_times = Column(Text, nullable=False, default='["09:00"]', comment="每日调度时间列表 (JSON 数组)")
    daily_review_days = Column(Integer, default=1, comment="每日审查天数范围（往前推N天）")
    daily_enabled = Column(Boolean, default=True, comment="是否启用每日定时任务")

    # 每周调度配置
    weekly_schedule_time = Column(String(5), nullable=True, default="09:00", comment="每周调度时间 (HH:MM)")
    weekly_weekday = Column(Integer, default=0, comment="每周执行日 (0=周一, 6=周日)")
    weekly_review_days = Column(Integer, default=7, comment="每周审查天数范围（往前推N天）")
    weekly_enabled = Column(Boolean, default=False, comment="是否启用每周定时任务")

    # 全局调度开关
    scheduler_enabled = Column(Boolean, default=True, comment="是否启用定时任务总开关")

    # 外部 API 配置
    external_api_key = Column(String(500), nullable=True, comment="外部 API Key (加密存储)")

    # 人员能效配置
    efficiency_enabled = Column(Boolean, default=True, comment="是否启用人员能效聚合")
    efficiency_work_summary_top_n = Column(Integer, default=5, comment="LLM 工作总结条目上限")
    efficiency_prompt_template = Column(Text, nullable=True, comment="能效评分提示词模板")
    efficiency_monthly_prompt_template = Column(Text, nullable=True, comment="月度能效提示词模板")
    efficiency_excluded_emails = Column(Text, nullable=True, comment="人员能效排除邮箱列表 (JSON 数组)")

    # 任务限制
    max_commits_per_run = Column(Integer, default=100, comment="单次最大处理提交数")
    diff_max_lines = Column(Integer, default=10000, comment="单次 diff 最大行数限制")

    # GitLab 同步配置
    gitlab_sync_enabled = Column(Boolean, default=True, nullable=True, comment="是否启用 GitLab 项目及成员自动同步")
    gitlab_sync_schedule_time = Column(String(5), nullable=True, default="03:00", comment="每日同步时间 (HH:MM)")
    gitlab_sync_default_password = Column(String(500), nullable=True, comment="新建同步用户的初始密码（加密存储）")

    # 报告配置
    report_output_dir = Column(String(500), nullable=False, default="./data/reports", comment="报告输出目录")

    # Webhook 配置
    webhook_enabled = Column(Boolean, default=False, comment="Webhook 总开关")
    push_review_enabled = Column(Boolean, default=False, comment="Push 事件审查开关")
    supported_extensions = Column(Text, nullable=False,
                                  default=".java,.py,.js,.ts,.go,.c,.cpp,.cs,.php,.rb,.rs,.kt,.swift,.vue,.jsx,.tsx",
                                  comment="支持审查的文件扩展名（逗号分隔）")
    review_max_tokens = Column(Integer, default=10000, comment="Webhook 单次审查最大 Token")
    review_style = Column(String(20), default="professional", comment="审查风格: professional/sarcastic/gentle/humorous")
    webhook_review_prompt = Column(Text, nullable=True, comment="Webhook 审查提示词模板")

    # 钉钉通知配置
    dingtalk_enabled = Column(Boolean, default=False, comment="钉钉通知开关")
    dingtalk_webhook_url = Column(String(500), nullable=True, comment="钉钉 Webhook URL")

    # 企业微信通知配置
    wecom_enabled = Column(Boolean, default=False, comment="企业微信通知开关")
    wecom_webhook_url = Column(String(500), nullable=True, comment="企业微信 Webhook URL")

    # 飞书通知配置
    feishu_enabled = Column(Boolean, default=False, comment="飞书通知开关")
    feishu_webhook_url = Column(String(500), nullable=True, comment="飞书 Webhook URL")

    @property
    def excluded_emails_list(self) -> list:
        """获取排除的邮箱列表（已规范化为小写）"""
        if not self.efficiency_excluded_emails:
            return []
        try:
            emails = json.loads(self.efficiency_excluded_emails)
            return [e.lower() for e in emails if isinstance(e, str)]
        except (json.JSONDecodeError, TypeError):
            return []

    def __repr__(self):
        return f"<Settings(id={self.id})>"
