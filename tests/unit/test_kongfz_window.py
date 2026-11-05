"""监控窗口计算单测（纯逻辑，不联网）。"""
from src.kongfz.window import (
    build_window_plan,
    describe_removal_reason,
    resolve_page_size,
    resolve_scan_limit,
    summarize_plan,
)


def _item(item_id: str, *, sold: bool = False, title: str = "") -> dict:
    return {
        "itemId": item_id,
        "title": title or f"书 {item_id}",
        "isSoldOut": sold,
        "link": {"pc": f"https://book.kongfz.com/1/{item_id}"},
    }


def test_window_keeps_newest_n_in_sale_items():
    items = [_item(str(100 + i)) for i in range(10)]
    plan = build_window_plan(items, [], 3)

    assert plan.window_keys == ["kfz:100", "kfz:101", "kfz:102"]
    assert plan.removed_keys == []
    assert plan.complete is True
    assert plan.new_listing_keys == []
    assert plan.backfill_keys == []


def test_new_listing_pushes_out_tail_item():
    """新商品上架 → 进入窗口顶部，末尾商品被挤出并移除。"""
    existing = ["kfz:101", "kfz:102", "kfz:103"]
    items = [_item("100"), _item("101"), _item("102"), _item("103")]

    plan = build_window_plan(items, existing, 3)

    assert plan.window_keys == ["kfz:100", "kfz:101", "kfz:102"]
    assert plan.new_listing_keys == ["kfz:100"]
    assert plan.backfill_keys == []
    assert plan.removed_keys == ["kfz:103"]


def test_sold_item_is_backfilled_by_later_item():
    """商品下架/售出 → 后面的商品补位进窗口（递补，不算新上架）。"""
    existing = ["kfz:100", "kfz:101", "kfz:102"]
    items = [_item("100"), _item("101", sold=True), _item("102"), _item("103")]

    plan = build_window_plan(items, existing, 3)

    assert plan.window_keys == ["kfz:100", "kfz:102", "kfz:103"]
    assert plan.new_listing_keys == []          # 没有新上架
    assert plan.backfill_keys == ["kfz:103"]     # 103 是递补
    assert plan.removed_keys == ["kfz:101"]
    assert plan.sold_keys == ["kfz:101"]
    assert describe_removal_reason("kfz:101", plan.sold_keys) == "sold"
    assert describe_removal_reason("kfz:103", plan.sold_keys) == "left_window"


def test_mixed_run_separates_new_listing_and_backfill():
    """同时出现新上架与下架递补时，两者被准确分开。"""
    existing = ["kfz:101", "kfz:102", "kfz:103", "kfz:104"]
    items = [
        _item("100"),                       # 新上架（在锚点 101 之前）
        _item("101"),
        _item("102", sold=True),            # 下架
        _item("103"),
        _item("105"),                        # 递补（在锚点之后，窗口外）
        _item("104"),
        _item("106"),                        # 也递补，但窗口已满
    ]

    plan = build_window_plan(items, existing, 4)

    assert plan.window_keys == ["kfz:100", "kfz:101", "kfz:103", "kfz:105"]
    assert plan.new_listing_keys == ["kfz:100"]
    assert plan.backfill_keys == ["kfz:105"]
    assert sorted(plan.removed_keys) == ["kfz:102", "kfz:104"]


def test_first_run_treats_everything_as_baseline():
    """上一轮窗口为空 → 全部按基线入库（不通知）。"""
    items = [_item("100"), _item("101")]
    plan = build_window_plan(items, [], 5)

    assert plan.window_keys == ["kfz:100", "kfz:101"]
    assert plan.new_listing_keys == []
    assert plan.backfill_keys == []
    assert len(plan.baseline_items) == 2
    assert plan.is_baseline_run is True


def test_anchor_disappearance_without_cap_means_backfill_only():
    """扫完整轮都没见到旧窗口商品（旧窗口整体消失）→ 陌生商品按递补处理。"""
    items = [_item("200"), _item("201")]
    plan = build_window_plan(items, ["kfz:999"], 2, scan_capped=False)

    assert plan.new_listing_keys == []
    assert plan.backfill_keys == ["kfz:200", "kfz:201"]
    assert plan.removed_keys == ["kfz:999"]


def test_anchor_disappearance_with_cap_means_new_listing():
    """扫描被截断且未见旧窗口商品（新上架爆量）→ 陌生商品按新上架处理。"""
    items = [_item("200"), _item("201")]
    plan = build_window_plan(items, ["kfz:999"], 2, scan_capped=True)

    assert plan.new_listing_keys == ["kfz:200", "kfz:201"]
    assert plan.backfill_keys == []


def test_incomplete_fetch_is_flagged_for_caller():
    plan = build_window_plan([_item("1")], ["kfz:1", "kfz:2"], 5, complete=False)

    assert plan.complete is False
    # 上层据此跳过窗口同步，因此这里仍会算出 removed，但不应被应用
    assert plan.removed_keys == ["kfz:2"]


def test_duplicate_items_do_not_shrink_window():
    items = [_item("100"), _item("100"), _item("101")]
    plan = build_window_plan(items, [], 3)

    assert plan.window_keys == ["kfz:100", "kfz:101"]
    assert plan.scanned == 2


def test_limit_is_at_least_one():
    plan = build_window_plan([_item("100"), _item("101")], [], 0)
    assert plan.window_keys == ["kfz:100"]


def test_scan_limit_and_page_size_are_bounded():
    assert resolve_scan_limit(10) == 30
    assert resolve_scan_limit(200) == 500
    # 窗口 30 → 扫描 90 → 一页 90（单请求完成一整轮）
    assert resolve_page_size(resolve_scan_limit(30)) == 90
    # 窗口 100 → 扫描 300 → 一页封顶 100（3 个请求）
    assert resolve_page_size(resolve_scan_limit(100)) == 100
    assert resolve_page_size(1) == 20


def test_summarize_plan_reports_buckets():
    existing = ["kfz:101", "kfz:102", "kfz:103"]
    items = [_item("100"), _item("101", sold=True), _item("102"), _item("103")]
    plan = build_window_plan(items, existing, 3)

    summary = summarize_plan(plan, 3)
    assert "新上架 1" in summary
    assert "递补 0" in summary
    assert "移除 1" in summary
