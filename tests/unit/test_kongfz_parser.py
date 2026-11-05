"""孔夫子解析器单测。"""
from src.kongfz.parser import (
    build_item_unique_key,
    build_notification_payload,
    build_record,
    parse_kongfz_item,
)


def _sample_item() -> dict:
    """来自真实接口响应的裁剪样本（2026-11 实测）。"""
    return {
        "itemId": 10632895130,
        "title": "红楼梦新证 竖版繁体 1976年一版一印",
        "author": "周汝昌",
        "press": "人民文学出版社",
        "pubDateText": "1976-04",
        "isbn": "",
        "isSoldOut": False,
        "imgUrl": "https://www0.kfzimg.com/sw/is3/261103/1cWra/851857c91046ce5c-Se_s.jpg",
        "imgBigUrl": "https://www0.kfzimg.com/sw/is3/261103/1cWra/851857c91046ce5c-Se_n.jpg",
        "qualityText": "八五品",
        "price": 30.0,
        "priceText": "30.00",
        "showTimeText": "2026-11-03 21:57 上书",
        "link": {"pc": "https://book.kongfz.com/1335084/10632895130"},
        "shopName": "天沐书摊",
        "shopId": 1335084,
        "shopkeeperId": 23959130,
        "shopAreaText": "浙江省杭州市",
        "shopSuccessOrderRate": "成功完成 90% (700+笔)",
        "shopAvgShippingTime": "平均8小时发货",
        "postage": {
            "shippingList": [
                {"shippingName": "快递", "shippingFee": 6.0, "shippingText": "快递￥6.00"}
            ],
            "sellerPayFreight": False,
        },
        "tplRecords": [
            {"key": "作者", "value": "周汝昌"},
            {"key": "出版社", "value": "人民文学出版社"},
            {"key": "出版时间", "value": "1976-04"},
            {"key": "装帧", "value": "平装"},
        ],
    }


def test_parse_item_maps_core_fields():
    info = parse_kongfz_item(_sample_item())

    assert info["商品ID"] == "10632895130"
    assert info["商品标题"] == "红楼梦新证 竖版繁体 1976年一版一印"
    assert info["当前售价"] == "30.00"
    assert info["商品链接"] == "https://book.kongfz.com/1335084/10632895130"
    assert info["发布时间"] == "2026-11-03 21:57"  # 去掉“上书”后缀
    assert info["卖家昵称"] == "天沐书摊"
    assert info["发货地区"] == "浙江省杭州市"
    assert info["品相"] == "八五品"
    assert info["商品图片列表"][0].endswith("_n.jpg")
    assert info["唯一键"] == "kfz:10632895130"
    assert "八五品" in info["商品标签"]
    assert "平装" in info["商品标签"]


def test_unique_key_prefers_item_id():
    item = _sample_item()
    item["link"] = {}
    assert build_item_unique_key(item) == "kfz:10632895130"


def test_unique_key_falls_back_to_link_without_item_id():
    item = _sample_item()
    item["itemId"] = ""
    assert build_item_unique_key(item) == "https://book.kongfz.com/1335084/10632895130"


def test_link_is_built_from_shop_and_item_ids():
    item = _sample_item()
    item["link"] = {}
    info = parse_kongfz_item(item)
    assert info["商品链接"] == "https://book.kongfz.com/1335084/10632895130"


def test_price_text_falls_back_to_numeric_price():
    item = _sample_item()
    item["priceText"] = ""
    assert parse_kongfz_item(item)["当前售价"] == "30.00"


def test_record_shape_matches_storage_contract():
    record = build_record(_sample_item(), keyword="红楼梦", task_name="孔网-红楼梦")

    assert record["搜索关键字"] == "红楼梦"
    assert record["任务名称"] == "孔网-红楼梦"
    assert record["平台"] == "kongfz"
    assert "商品信息" in record and "卖家信息" in record
    assert record["卖家信息"]["卖家昵称"] == "天沐书摊"


def test_notification_payload_keeps_xianyu_compatible_keys():
    payload = build_notification_payload(_sample_item())

    assert payload["商品标题"].startswith("红楼梦新证")
    assert payload["当前售价"] == "30.00"
    assert payload["商品链接"].startswith("https://book.kongfz.com/")
    assert payload["来源"] == "孔夫子旧书网"
    assert payload["上书时间"] == "2026-11-03 21:57"
    assert payload["卖家"] == "天沐书摊"