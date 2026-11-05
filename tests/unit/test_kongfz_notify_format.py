"""孔夫子通知消息格式单测。"""
from src.kongfz.notify_format import build_kongfz_notification


def _new_listing_payload() -> dict:
    return {
        "商品标题": "红楼梦新证 竖版繁体 1976年一版一印",
        "当前售价": "30.00",
        "商品链接": "https://book.kongfz.com/1335084/10632895130",
        "来源": "孔夫子旧书网",
        "上书时间": "2026-11-03 21:57",
        "卖家": "天沐书摊",
        "品相": "八五品",
        "商品标签": ["八五品", "平装"],
    }


def test_new_listing_message_contains_core_fields():
    content = build_kongfz_notification(_new_listing_payload(), "新上架商品")

    assert "📗 新上架" in content
    assert "红楼梦新证" in content
    assert "**¥30.00**" in content
    assert "八五品" in content
    assert "上书时间：2026-11-03 21:57" in content
    assert "卖家：天沐书摊" in content
    assert "[👉 点此查看商品](https://book.kongfz.com/1335084/10632895130)" in content
    # 一条消息一个商品，不出现列表/分隔符
    assert "\n---\n" not in content


def test_long_title_is_truncated():
    payload = _new_listing_payload()
    payload["商品标题"] = "超长书名" * 20
    content = build_kongfz_notification(payload, "新上架商品")
    assert "…" in content
    assert len(content.splitlines()[0]) < 40


def test_removal_message_uses_delisting_header():
    payload = {
        "商品标题": "旧版连环画：红楼梦之十四查抄贾府",
        "当前售价": "18.00",
        "商品链接": "https://book.kongfz.com/1/10632843437",
        "来源": "孔夫子旧书网",
        "监控关键词": "红宝书",
        "任务": "孔网-红楼梦",
    }
    content = build_kongfz_notification(
        payload, "商品已移除监控：已售出/已下架"
    )

    assert "📕 下架" in content
    assert "上书时间" not in content          # 下架消息不显示上书时间
    assert "关键词：红宝书" in content
    assert "任务：孔网-红楼梦" in content
    assert "[👉 点此查看商品](https://book.kongfz.com/1/10632843437)" in content


def test_missing_fields_fall_back_to_placeholder():
    content = build_kongfz_notification({"商品标题": "", "当前售价": ""}, "")
    assert "📗 新上架" in content
    assert "-" in content
    assert "查看商品" not in content            # 无链接时不输出跳转按钮


def test_wecom_client_uses_kongfz_format_for_kongfz_source():
    """企业微信渠道收到孔夫子的 payload 时应走专用格式。"""
    from src.infrastructure.external.notification_clients.wecom_bot_client import WeComBotClient

    client = WeComBotClient("https://example.invalid/hook")
    content = client._render_markdown(_new_listing_payload(), "新上架商品")

    assert "📗 新上架" in content
    assert "**¥30.00**" in content


def test_wecom_client_keeps_generic_format_for_other_sources():
    """闲鱼等其它来源保持原有通用格式，不受影响。"""
    from src.infrastructure.external.notification_clients.wecom_bot_client import WeComBotClient

    client = WeComBotClient("https://example.invalid/hook")
    content = client._render_markdown(
        {"商品标题": "MacBook Air M1", "当前售价": "4500", "商品链接": "https://www.goofish.com/item?id=1"},
        "符合任务要求",
    )

    assert "🚨 新推荐!" in content
    assert "电脑端链接" in content
    assert "📗 新上架" not in content
