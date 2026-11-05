"""孔夫子旧书网数据解析。

把搜索接口返回的 item 字典转换成项目内部统一的商品字典，
从而复用现有的存储、通知、结果展示与价格历史能力。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

KONGFZ_PLATFORM = "kongfz"
KONGFZ_PLATFORM_LABEL = "孔夫子旧书网"
ITEM_URL_TEMPLATE = "https://book.kongfz.com/{shop_id}/{item_id}"
SHOW_TIME_SUFFIX = "上书"
UNKNOWN_TEXT = "未知"

# 详情/商品字段里存放唯一键的保留字段（storages 层优先使用）。
UNIQUE_KEY_FIELD = "唯一键"


def _pick_link(raw_link: Any) -> str:
    if isinstance(raw_link, dict):
        for key in ("pc", "h5", "mp", "app"):
            value = raw_link.get(key)
            if value:
                return str(value).strip()
        return ""
    return str(raw_link or "").strip()


def build_item_link(item: dict) -> str:
    link = _pick_link(item.get("link"))
    if link:
        return link
    shop_id = item.get("shopId")
    item_id = item.get("itemId")
    if shop_id and item_id:
        return ITEM_URL_TEMPLATE.format(shop_id=shop_id, item_id=item_id)
    return ""


def build_item_unique_key(item: dict) -> str:
    """稳定的商品唯一键，优先使用 itemId。"""
    item_id = str(item.get("itemId") or "").strip()
    if item_id:
        return f"kfz:{item_id}"
    return build_item_link(item)


def normalize_show_time(value: Any) -> str:
    text = str(value or "").strip()
    if text.endswith(SHOW_TIME_SUFFIX):
        text = text[: -len(SHOW_TIME_SUFFIX)].strip()
    return text or "未知时间"


def build_price_text(item: dict) -> str:
    price_text = str(item.get("priceText") or "").strip()
    if price_text:
        return price_text
    price = item.get("price")
    if price is None:
        return "价格异常"
    try:
        return f"{float(price):.2f}"
    except (TypeError, ValueError):
        return str(price)


def build_item_images(item: dict) -> list[str]:
    images: list[str] = []
    for key in ("imgBigUrl", "imgUrl"):
        value = str(item.get(key) or "").strip()
        if value and value not in images:
            images.append(value)
    return images


def build_item_tags(item: dict) -> list[str]:
    tags: list[str] = []
    quality = str(item.get("qualityText") or "").strip()
    if quality:
        tags.append(quality)
    postage = item.get("postage") or {}
    if isinstance(postage, dict) and postage.get("sellerPayFreight"):
        tags.append("包邮")
    for record in item.get("tplRecords") or []:
        if not isinstance(record, dict):
            continue
        if str(record.get("key") or "") == "装帧":
            value = str(record.get("value") or "").strip()
            if value:
                tags.append(value)
    return tags


def build_tpl_value(item: dict, key: str) -> str:
    for record in item.get("tplRecords") or []:
        if not isinstance(record, dict):
            continue
        if str(record.get("key") or "") == key:
            return str(record.get("value") or "").strip()
    return ""


def parse_kongfz_item(item: dict) -> dict:
    """把接口 item 转成项目统一的「商品信息」字典。"""
    item_id = str(item.get("itemId") or "").strip()
    images = build_item_images(item)
    return {
        "商品ID": item_id,
        "商品标题": str(item.get("title") or "未知标题").strip(),
        "当前售价": build_price_text(item),
        "商品原价": "",
        "商品链接": build_item_link(item),
        UNIQUE_KEY_FIELD: build_item_unique_key(item),
        "商品图片列表": images,
        "商品主图链接": images[0] if images else "",
        "商品标签": build_item_tags(item),
        "发货地区": str(item.get("shopAreaText") or "").strip(),
        "卖家昵称": str(item.get("shopName") or "").strip(),
        "发布时间": normalize_show_time(item.get("showTimeText")),
        "平台": KONGFZ_PLATFORM,
        "品相": str(item.get("qualityText") or "").strip(),
        "作者": str(item.get("author") or "").strip(),
        "出版社": str(item.get("press") or "").strip(),
        "出版时间": str(item.get("pubDateText") or "").strip(),
        "ISBN": str(item.get("isbn") or "").strip(),
        "是否已售": bool(item.get("isSoldOut")),
        "店铺ID": str(item.get("shopId") or "").strip(),
        "店主ID": str(item.get("shopkeeperId") or "").strip(),
        "店铺链接": _pick_link(item.get("shopLink")),
    }


def parse_kongfz_seller(item: dict) -> dict:
    return {
        "卖家昵称": str(item.get("shopName") or "").strip(),
        "卖家店铺ID": str(item.get("shopId") or "").strip(),
        "卖家注册时长": "未知",
        "卖家芝麻信用": None,
        "卖家在售/已售商品数": "未知",
        "卖家收到的评价总数": "未知",
        "店主链接": _pick_link(item.get("shopkeeperLink")),
        "店铺信用链接": _pick_link(item.get("shopCreditLink")),
        "卖家成交描述": str(item.get("shopSuccessOrderRate") or "").strip(),
        "卖家发货描述": str(item.get("shopAvgShippingTime") or "").strip(),
        "卖家所在地": str(item.get("shopAreaText") or "").strip(),
    }


def build_notification_payload(item: dict) -> dict:
    """与闲鱼监控保持一致的通知字段结构（并补充孔夫子特有字段）。"""
    info = parse_kongfz_item(item)
    tags = info.get("商品标签") or []
    return {
        "商品标题": info["商品标题"],
        "当前售价": info["当前售价"],
        "商品链接": info["商品链接"],
        "来源": KONGFZ_PLATFORM_LABEL,
        "上书时间": info["发布时间"],
        "卖家": info["卖家昵称"],
        "品相": info.get("品相") or "未知",
        "商品标签": "、".join(str(tag) for tag in tags) if tags else "",
    }


def build_record(
    item: dict,
    *,
    keyword: str,
    task_name: str,
    crawl_time: Optional[str] = None,
) -> dict:
    """构造写入结果库的完整记录（分析与通知由 runner 补充）。"""
    return {
        "爬取时间": crawl_time or datetime.now().isoformat(),
        "搜索关键字": keyword,
        "任务名称": task_name,
        "平台": KONGFZ_PLATFORM,
        "商品信息": parse_kongfz_item(item),
        "卖家信息": parse_kongfz_seller(item),
    }