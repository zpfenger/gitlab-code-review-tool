"""IM 通知调度器 — 从数据库配置分发消息到各通知渠道"""
from loguru import logger

from app.services.im.dingtalk import DingTalkNotifier
from app.services.im.wecom import WeComNotifier
from app.services.im.feishu import FeishuNotifier


class Notifier:
    """统一通知调度器"""

    def __init__(
        self,
        dingtalk_enabled: bool = False,
        dingtalk_webhook_url: str = "",
        wecom_enabled: bool = False,
        wecom_webhook_url: str = "",
        feishu_enabled: bool = False,
        feishu_webhook_url: str = "",
    ):
        self.dingtalk = DingTalkNotifier(
            webhook_url=dingtalk_webhook_url, enabled=dingtalk_enabled
        )
        self.wecom = WeComNotifier(
            webhook_url=wecom_webhook_url, enabled=wecom_enabled
        )
        self.feishu = FeishuNotifier(
            webhook_url=feishu_webhook_url, enabled=feishu_enabled
        )

    def send_notification(
        self,
        content: str,
        msg_type: str = "text",
        title: str = "通知",
        is_at_all: bool = False,
    ):
        """向所有启用的通知渠道发送消息"""
        for name, notifier in [
            ("钉钉", self.dingtalk),
            ("企业微信", self.wecom),
            ("飞书", self.feishu),
        ]:
            try:
                notifier.send_message(
                    content=content,
                    msg_type=msg_type,
                    title=title,
                    is_at_all=is_at_all,
                )
            except Exception as e:
                logger.error(f"{name}通知发送失败: {e}")

    @classmethod
    def from_settings(cls, settings) -> "Notifier":
        """从 Settings ORM 对象构建 Notifier 实例"""
        return cls(
            dingtalk_enabled=bool(settings.dingtalk_enabled),
            dingtalk_webhook_url=settings.dingtalk_webhook_url or "",
            wecom_enabled=bool(settings.wecom_enabled),
            wecom_webhook_url=settings.wecom_webhook_url or "",
            feishu_enabled=bool(settings.feishu_enabled),
            feishu_webhook_url=settings.feishu_webhook_url or "",
        )

    @classmethod
    def from_project(cls, project, settings=None) -> "Notifier":
        """从项目配置构建 Notifier，项目级配置优先，无则回退全局配置。

        Args:
            project: Project ORM 对象，可能包含项目级企业微信配置
            settings: Settings ORM 对象，用于回退全局通知配置
        """
        wecom_enabled = False
        wecom_webhook_url = ""

        # 项目级企业微信配置优先：仅在可成功解密 URL 时才启用
        if project and getattr(project, 'wecom_enabled', False):
            if project.wecom_webhook_url:
                try:
                    from app.security import security_service
                    wecom_webhook_url = security_service.decrypt(project.wecom_webhook_url)
                    wecom_enabled = bool(wecom_webhook_url)
                except Exception:
                    logger.warning(f"项目 {project.name} 企业微信 Webhook URL 解密失败，回退全局配置")
            else:
                logger.warning(f"项目 {project.name} 已启用企业微信但未配置 Webhook URL，回退全局配置")

        # 回退到全局配置
        if not wecom_enabled and settings:
            wecom_enabled = bool(settings.wecom_enabled)
            if settings.wecom_webhook_url:
                try:
                    from app.security import security_service
                    wecom_webhook_url = security_service.decrypt(settings.wecom_webhook_url)
                    wecom_enabled = wecom_enabled and bool(wecom_webhook_url)
                except Exception:
                    logger.warning("全局企业微信 Webhook URL 解密失败")
                    wecom_enabled = False
            else:
                wecom_enabled = False


        # 全局钉钉/飞书（保持兼容）
        dingtalk_enabled = False
        dingtalk_webhook_url = ""
        feishu_enabled = False
        feishu_webhook_url = ""
        if settings:
            dingtalk_enabled = bool(settings.dingtalk_enabled)
            if settings.dingtalk_webhook_url:
                try:
                    from app.security import security_service
                    dingtalk_webhook_url = security_service.decrypt(settings.dingtalk_webhook_url)
                except Exception:
                    pass
            feishu_enabled = bool(settings.feishu_enabled)
            if settings.feishu_webhook_url:
                try:
                    from app.security import security_service
                    feishu_webhook_url = security_service.decrypt(settings.feishu_webhook_url)
                except Exception:
                    pass

        return cls(
            dingtalk_enabled=dingtalk_enabled,
            dingtalk_webhook_url=dingtalk_webhook_url,
            wecom_enabled=wecom_enabled,
            wecom_webhook_url=wecom_webhook_url,
            feishu_enabled=feishu_enabled,
            feishu_webhook_url=feishu_webhook_url,
        )
