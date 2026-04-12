"""钉钉通知服务"""
import json
import requests
from loguru import logger


class DingTalkNotifier:
    """钉钉机器人消息推送"""

    def __init__(self, webhook_url: str, enabled: bool = False):
        self.webhook_url = webhook_url
        self.enabled = enabled

    def send_message(
        self,
        content: str,
        msg_type: str = "text",
        title: str = "通知",
        is_at_all: bool = False,
    ):
        if not self.enabled or not self.webhook_url:
            return

        try:
            if msg_type == "markdown":
                message = {
                    "msgtype": "markdown",
                    "markdown": {"title": title, "text": content},
                    "at": {"isAtAll": is_at_all},
                }
            else:
                message = {
                    "msgtype": "text",
                    "text": {"content": content},
                    "at": {"isAtAll": is_at_all},
                }

            resp = requests.post(
                url=self.webhook_url,
                data=json.dumps(message),
                headers={"Content-Type": "application/json; charset=UTF-8"},
            )
            data = resp.json()
            if data.get("errmsg") == "ok":
                logger.info("钉钉消息发送成功")
            else:
                logger.error(f"钉钉消息发送失败: {data.get('errmsg')}")
        except Exception as e:
            logger.error(f"钉钉消息发送异常: {e}")
