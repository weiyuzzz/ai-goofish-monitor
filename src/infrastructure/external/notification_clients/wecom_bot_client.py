"""
企业微信机器人通知客户端
"""
import asyncio
from typing import Dict

import requests

from .base import NotificationClient


class WeComBotClient(NotificationClient):
    """企业微信机器人通知客户端"""

    channel_key = "wecom"
    display_name = "企业微信"

    def __init__(self, bot_url: str | None = None, pcurl_to_mobile: bool = True):
        super().__init__(enabled=bool(bot_url), pcurl_to_mobile=pcurl_to_mobile)
        self.bot_url = bot_url

    async def send(self, product_data: Dict, reason: str) -> None:
        if not self.is_enabled():
            raise RuntimeError("企业微信 未启用")

        payload = {
            "msgtype": "markdown",
            "markdown": {"content": self._render_markdown(product_data, reason)},
        }
        headers = {"Content-Type": "application/json"}
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None,
            lambda: requests.post(
                self.bot_url,
                json=payload,
                headers=headers,
                timeout=10,
            ),
        )
        response.raise_for_status()
        result = response.json()
        if result.get("errcode", 0) != 0:
            raise RuntimeError(result.get("errmsg", "企业微信返回未知错误"))

    def _render_markdown(self, product_data: Dict, reason: str) -> str:
        """孔夫子走专用卡片格式，其它来源沿用通用格式。"""
        source = str(product_data.get("来源") or "").strip()
        if source == "孔夫子旧书网":
            from src.kongfz.notify_format import build_kongfz_notification

            return build_kongfz_notification(product_data, reason)

        message = self._build_message(product_data, reason)
        markdown_lines = [f"## {message.notification_title}", ""]
        markdown_lines.append(f"- 价格: {message.price}")
        markdown_lines.append(f"- 原因: {message.reason}")
        if message.mobile_link:
            markdown_lines.append(f"- 手机端链接: [{message.mobile_link}]({message.mobile_link})")
        markdown_lines.append(f"- 电脑端链接: [{message.desktop_link}]({message.desktop_link})")
        return "\n".join(markdown_lines)
