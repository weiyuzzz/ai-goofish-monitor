"""窗口「本次新增 / 递补」标注逻辑单测。"""
from datetime import datetime, timedelta

from src.services.result_storage_service import (
    NEW_ITEM_TOLERANCE_SECONDS,
    annotate_new_flags,
    count_backfill_window_rows,
    count_new_window_rows,
    latest_crawl_time,
)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def test_annotate_new_flags_marks_only_recent_run():
    now = datetime(2026, 11, 3, 22, 0, 0)
    records = [
        {"爬取时间": _iso(now), "入库方式": "new_listing"},         # 本次新上架
        {"爬取时间": _iso(now - timedelta(seconds=30)), "入库方式": "backfill"},  # 本次递补
        {"爬取时间": _iso(now - timedelta(seconds=45))},            # 本次入库（历史数据无标记）
        {"爬取时间": _iso(now - timedelta(hours=3)), "入库方式": "new_listing"},  # 上一轮的新上架
        {"爬取时间": "not-a-date"},                                   # 非法时间 → 不标
        {"爬取时间": ""},                                             # 空 → 不标
    ]

    result = annotate_new_flags(records, _iso(now))

    assert result["new_count"] == 3
    assert records[0]["is_new"] is True
    assert records[0]["is_new_listing"] is True
    assert records[0]["is_backfill"] is False
    assert records[1]["is_new"] is True
    assert records[1]["is_new_listing"] is False
    assert records[1]["is_backfill"] is True
    assert records[2]["is_new"] is True
    assert records[2]["is_new_listing"] is False
    assert records[2]["is_backfill"] is False
    # 上一轮入库的新上架：还在窗口里，但不再算「本次新增」
    assert records[3]["is_new"] is False
    assert records[3]["is_new_listing"] is False
    assert records[4]["is_new"] is False
    assert records[5]["is_new"] is False
    assert records[0]["first_seen_at"] == _iso(now)


def test_annotate_new_flags_without_latest_run():
    records = [{"爬取时间": _iso(datetime.now())}]
    result = annotate_new_flags(records, None)
    assert result["new_count"] == 0
    assert records[0]["is_new"] is False


def test_annotate_new_flags_tolerates_clock_skew():
    now = datetime(2026, 11, 3, 22, 0, 0)
    later = now + timedelta(seconds=5)
    records = [{"爬取时间": _iso(now)}]
    result = annotate_new_flags(records, _iso(later))
    assert result["new_count"] == 1


def test_count_helpers_only_count_matching_entry_type():
    now = datetime(2026, 11, 3, 22, 0, 0)
    rows = [
        {"crawl_time": _iso(now), "entry_type": "new_listing"},
        {"crawl_time": _iso(now - timedelta(seconds=NEW_ITEM_TOLERANCE_SECONDS)), "entry_type": "new_listing"},
        {"crawl_time": _iso(now), "entry_type": "backfill"},
        {"crawl_time": _iso(now), "entry_type": None},                # 基线/历史数据
        {"crawl_time": _iso(now - timedelta(hours=5)), "entry_type": "new_listing"},
        {"crawl_time": ""},
    ]

    assert latest_crawl_time(rows) == _iso(now)
    assert count_new_window_rows(rows) == 2
    assert count_backfill_window_rows(rows) == 1
    assert count_new_window_rows([]) == 0
    assert count_new_window_rows([{"crawl_time": "bad"}]) == 0
    assert count_backfill_window_rows([{"crawl_time": "bad"}]) == 0
