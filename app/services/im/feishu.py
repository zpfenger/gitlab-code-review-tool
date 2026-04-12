"""飞书通知服务"""
import requests
from loguru import logger


class FeishuNotifier:
    """飞书机器人消息推送（支持卡片消息）"""

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
                data = {
                    "msg_type": "interactive",
                    "card": {
                        "schema": "2.0",
                        "config": {"update_multi": True},
                        "body": {
                            "direction": "vertical",
                            "padding": "12px 12px 12px 12px",
                            "elements": [
                                {
                                    "tag": "markdown",
                                    "content": content,
                                    "text_align": "left",
                                    "text_size": "normal_v2",
                                }
                            ],
                        },
                        "header": {
                            "title": {"tag": "plain_text", "content": title},
                            "template": "blue",
                            "padding": "12px 12px 12px 12px",
                        },
                    },
                }
            else:
                data = {"msg_type": "text", "content": {"text": content}}

            resp = requests.post(
                url=self.webhook_url,
                json=data,
                headers={"Content-Type": "application/json"},
            )

            if resp.status_code != 200:
                logger.error(f"飞书消息发送失败: {resp.text}")
                return

            result = resp.json()
            if result.get("msg") != "success":
                logger.error(f"飞书消息发送失败: {result}")
            else:
                logger.info("飞书消息发送成功")
        except Exception as e:
            logger.error(f"飞书消息发送异常: {e}")
