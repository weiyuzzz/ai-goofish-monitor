"""孔夫子监控专用通知消息格式化。"""
from __future__ import annotations

from src.kongfz.parser import KONGFZ_PLATFORM_LABEL

TITLE_LIMIT = 26


def _clean(value) -> str:
    text = str(value or "").strip()
    return text or "-"


def _short(value, limit: int = TITLE_LIMIT) -> str:
    text = _clean(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…"


def build_kongfz_notification(
    product_data: dict,
    reason: str,
) -> str:
    """构建孔夫子「一条更新一条消息」的企业微信 Markdown 内容。

    product_data 由 src.kongfz.parser.build_notification_payload 产出：
    商品标题 / 当前售价 / 商品链接 / 来源 / 上书时间 / 卖家 / 品相 / 商品标签
    移除通知额外携带：监控关键词 / 任务 / 商品链接(唯一键)
    """
    title = _clean(product_data.get("商品标题"))
    price = _clean(product_data.get("当前售价"))
    link = str(product_data.get("商品链接") or "").strip() or "#"
    seller = _clean(product_data.get("卖家"))
    quality = _clean(product_data.get("品相"))
    listed_at = _clean(product_data.get("上书时间"))
    keyword = _clean(product_data.get("监控关键词"))
    task_name = _clean(product_data.get("任务"))
    tags = product_data.get("商品标签")

    is_removal = str(reason or "").startswith("商品已移除监控")

    lines: list[str] = []

    # ── 标题行：一条消息讲一件事 ──
    if is_removal:
        lines.append(f"### 📕 下架 · {_short(title)}")
    else:
        lines.append(f"### 📗 新上架 · {_short(title)}")

    # ── 关键信息：价格永远最醒目 ──
    lines.append(f"**¥{price}** · {quality}")

    # ── 详情字段 ──
    details: list[str] = []
    if not is_removal:
        if listed_at not in {"-", "未知时间"}:
            details.append(f"上书时间：{listed_at}")
    if seller != "-":
        details.append(f"卖家：{seller}")
    tag_text = "、".join(str(tag) for tag in tags) if tags else ""
    if tag_text and tag_text != "-":
        details.append(f"品相标签：{tag_text}")
    if keyword != "-":
        details.append(f"关键词：{keyword}")
    if task_name != "-":
        details.append(f"任务：{task_name}")
    if details:
        lines.append("\n".join(f"- {detail}" for detail in details))

    # ── 跳转链接 ──
    if link and link != "#":
        lines.append(f"\n[👉 点此查看商品]({link})")

    # ── 推送说明（为什么收到这条）──
    reason_text = str(reason or "").strip()
    if reason_text:
        lines.append(f"\n<font color=\"comment\">{reason_text}</font>")

    return "\n".join(lines)


__all__ = ["KONGFZ_PLATFORM_LABEL", "build_kongfz_notification"]