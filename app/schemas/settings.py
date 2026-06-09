# app/schemas/settings.py
import json
import re
from pydantic import BaseModel, Field, field_validator
from typing import Optional
from datetime import datetime


class SettingsBase(BaseModel):
    global_gitlab_url: str = Field(..., max_length=500)
    global_gitlab_token: Optional[str] = None
    llm_api_url: str = Field(..., max_length=500)
    llm_api_key: Optional[str] = None
    llm_model: str = Field(..., max_length=100)
    llm_max_tokens: int = Field(default=4096, ge=1)
    llm_temperature: float = Field(default=0.7, ge=0, le=2)
    llm_timeout: int = Field(default=120, ge=10, le=600)
    llm_max_retries: int = Field(default=3, ge=0, le=10)
    llm_retry_delay: int = Field(default=5, ge=1, le=60)
    review_prompt_template: Optional[str] = None
    weekly_review_prompt: Optional[str] = None
    stats_prompt: Optional[str] = None
    global_svn_url: Optional[str] = None
    global_svn_username: Optional[str] = None
    global_svn_password: Optional[str] = None

    # 每日调度配置
    daily_schedule_times: str = Field(default='["09:00"]')
    daily_review_days: int = Field(default=1, ge=1, le=30)
    daily_enabled: bool = True

    # 每周调度配置
    weekly_schedule_time: str = Field(default="09:00")
    weekly_weekday: int = Field(default=0, ge=0, le=6)
    weekly_review_days: int = Field(default=7, ge=1, le=90)
    weekly_enabled: bool = False

    # 全局调度开关
    scheduler_enabled: bool = True

    # GitLab 项目及成员同步配置
    gitlab_sync_enabled: bool = True
    gitlab_sync_schedule_time: str = Field(default="03:00")
    gitlab_sync_default_password: Optional[str] = None

    # 外部 API 配置
    external_api_key: Optional[str] = None

    # 人员能效配置
    efficiency_enabled: bool = True
    efficiency_work_summary_top_n: int = Field(default=5, ge=1, le=20)
    efficiency_prompt_template: Optional[str] = None
    efficiency_monthly_prompt_template: Optional[str] = None

    # 任务限制
    max_commits_per_run: int = Field(default=100, ge=1, le=1000)
    diff_max_lines: int = Field(default=10000, ge=100, le=100000)
    report_output_dir: str = Field(default="./data/reports", max_length=500)

    # Webhook 配置
    webhook_enabled: bool = False
    push_review_enabled: bool = False
    supported_extensions: str = Field(
        default=".java,.py,.js,.ts,.go,.c,.cpp,.cs,.php,.rb,.rs,.kt,.swift,.vue,.jsx,.tsx",
        max_length=2000
    )
    review_max_tokens: int = Field(default=10000, ge=1000, le=100000)
    review_style: str = Field(default="professional", max_length=20)
    webhook_review_prompt: Optional[str] = None

    # 钉钉通知
    dingtalk_enabled: bool = False
    dingtalk_webhook_url: Optional[str] = None

    # 企业微信通知
    wecom_enabled: bool = False
    wecom_webhook_url: Optional[str] = None

    # 飞书通知
    feishu_enabled: bool = False
    feishu_webhook_url: Optional[str] = None

    @field_validator('daily_schedule_times')
    @classmethod
    def validate_daily_schedule_times(cls, v: str) -> str:
        """验证每日调度时间列表格式"""
        try:
            times = json.loads(v) if isinstance(v, str) else v
        except json.JSONDecodeError:
            raise ValueError('每日调度时间必须是有效的 JSON 数组')
        if not isinstance(times, list) or len(times) == 0:
            raise ValueError('至少需要一个每日调度时间')
        for t in times:
            if not re.match(r'^\d{2}:\d{2}$', t):
                raise ValueError(f'每日调度时间格式错误: {t}，需要 HH:MM 格式')
        return json.dumps(times)

    @field_validator('weekly_schedule_time')
    @classmethod
    def validate_weekly_schedule_time(cls, v: str) -> str:
        """验证每周调度时间格式"""
        if not re.match(r'^\d{2}:\d{2}$', v):
            raise ValueError(f'每周调度时间格式错误: {v}，需要 HH:MM 格式')
        return v

    @field_validator('gitlab_sync_schedule_time')
    @classmethod
    def validate_gitlab_sync_schedule_time(cls, v: str) -> str:
        """验证 GitLab 同步时间格式"""
        if not re.match(r'^\d{2}:\d{2}$', v):
            raise ValueError(f'GitLab 同步时间格式错误: {v}，需要 HH:MM 格式')
        return v


class SettingsCreate(SettingsBase):
    pass


class SettingsUpdate(BaseModel):
    global_gitlab_url: Optional[str] = None
    global_gitlab_token: Optional[str] = None
    llm_api_url: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_model: Optional[str] = None
    llm_max_tokens: Optional[int] = None
    llm_temperature: Optional[float] = None
    llm_timeout: Optional[int] = None
    llm_max_retries: Optional[int] = None
    llm_retry_delay: Optional[int] = None
    review_prompt_template: Optional[str] = None
    weekly_review_prompt: Optional[str] = None
    stats_prompt: Optional[str] = None
    global_svn_url: Optional[str] = None
    global_svn_username: Optional[str] = None
    global_svn_password: Optional[str] = None
    daily_schedule_times: Optional[str] = None
    daily_review_days: Optional[int] = None
    daily_enabled: Optional[bool] = None
    weekly_schedule_time: Optional[str] = None
    weekly_weekday: Optional[int] = None
    weekly_review_days: Optional[int] = None
    weekly_enabled: Optional[bool] = None
    scheduler_enabled: Optional[bool] = None
    # GitLab 项目及成员同步配置
    gitlab_sync_enabled: Optional[bool] = None
    gitlab_sync_schedule_time: Optional[str] = None
    gitlab_sync_default_password: Optional[str] = None
    # 外部 API 配置
    external_api_key: Optional[str] = None

    # 人员能效配置
    efficiency_enabled: Optional[bool] = None
    efficiency_work_summary_top_n: Optional[int] = Field(None, ge=1, le=20)
    efficiency_prompt_template: Optional[str] = None
    efficiency_monthly_prompt_template: Optional[str] = None
    max_commits_per_run: Optional[int] = None
    diff_max_lines: Optional[int] = None
    report_output_dir: Optional[str] = None
    # Webhook 配置
    webhook_enabled: Optional[bool] = None
    push_review_enabled: Optional[bool] = None
    supported_extensions: Optional[str] = None
    review_max_tokens: Optional[int] = None
    review_style: Optional[str] = None
    webhook_review_prompt: Optional[str] = None
    # 通知配置
    dingtalk_enabled: Optional[bool] = None
    dingtalk_webhook_url: Optional[str] = None
    wecom_enabled: Optional[bool] = None
    wecom_webhook_url: Optional[str] = None
    feishu_enabled: Optional[bool] = None
    feishu_webhook_url: Optional[str] = None

    @field_validator('gitlab_sync_schedule_time')
    @classmethod
    def validate_gitlab_sync_schedule_time(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not re.match(r'^\d{2}:\d{2}$', v):
            raise ValueError(f'GitLab 同步时间格式错误: {v}，需要 HH:MM 格式')
        return v


class GitlabTestRequest(BaseModel):
    """GitLab 连接测试请求"""
    gitlab_url: str = Field(..., min_length=1, max_length=500)
    token: str = Field(..., min_length=1, max_length=500)


class SettingsResponse(SettingsBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
