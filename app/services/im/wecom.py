"""企业微信通知服务"""
import re
import requests
from loguru import logger


class WeComNotifier:
    """企业微信机器人消息推送（支持 Markdown 分块发送）"""

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
            # 企业微信 markdown 最大 4096 字节，text 最大 2048 字节
            max_bytes = 4000 if msg_type == "markdown" else 2000
            content_len = len(content.encode("utf-8"))

            if content_len <= max_bytes:
                data = self._build_message(content, title, msg_type, is_at_all)
                self._send(data)
            else:
                logger.warning(f"消息超过 {max_bytes} 字节限制，分块发送")
                self._send_in_chunks(content, title, msg_type, is_at_all, max_bytes)
        except Exception as e:
            logger.error(f"企业微信消息发送异常: {e}")

    # ── 内部方法 ──

    def _build_message(self, content: str, title: str, msg_type: str, is_at_all: bool) -> dict:
        if msg_type == "markdown":
            formatted = self._format_markdown(content, title)
            return {"msgtype": "markdown", "markdown": {"content": formatted}}
        return {
            "msgtype": "text",
            "text": {
                "content": content,
                "mentioned_list": ["@all"] if is_at_all else [],
            },
        }

    def _format_markdown(self, content: str, title: str = "") -> str:
        formatted = f"## {title}\n\n" if title else ""
        content = re.sub(r"#{5,}\s", "#### ", content)
        content = re.sub(r"\[(.*?)\]\((.*?)\)", r"[链接]\2", content)
        content = re.sub(r"<[^>]+>", "", content)
        return formatted + content

    def _send(self, data: dict):
        try:
            resp = requests.post(
                self.webhook_url,
                json=data,
                headers={"Content-Type": "application/json"},
            )
            result = resp.json()
            if result.get("errcode") != 0:
                logger.error(f"企业微信消息发送失败: {result}")
            else:
                logger.info("企业微信消息发送成功")
        except Exception as e:
            logger.error(f"企业微信请求异常: {e}")

    def _send_in_chunks(self, content: str, title: str, msg_type: str, is_at_all: bool, max_bytes: int):
        safe_max = max_bytes - 50
        chunks = self._split_content(content, safe_max)
        for i, chunk in enumerate(chunks):
            chunk_title = f"{title} ({i + 1}/{len(chunks)})" if title else f"消息 ({i + 1}/{len(chunks)})"
            data = self._build_message(chunk, chunk_title, msg_type, is_at_all)
            self._send(data)

    def _split_content(self, content: str, max_bytes: int) -> list:
        """按字节安全分割"""
        chunks = []
        content_bytes = content.encode("utf-8")
        total = len(content_bytes)
        start = 0
        while start < total:
            end = min(start + max_bytes, total)
            while end < total and end > start:
                try:
                    content_bytes[start:end].decode("utf-8")
                    break
                except UnicodeDecodeError:
                    end -= 1
            chunk = content_bytes[start:end].decode("utf-8")
            # 修复未闭合的代码块
            if chunk.count("```") % 2 == 1:
                chunk += "\n```"
            chunks.append(chunk)
            start = end
        return chunks or [content]
