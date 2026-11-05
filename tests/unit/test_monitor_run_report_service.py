"""运行报表服务单测：区分「任务没跑」和「跑了但没有新商品」。"""
import pytest

from src.services.monitor_run_report_service import (
    STATUS_FAILED,
    STATUS_OK,
    STATUS_SKIPPED,
    build_task_key,
    load_monitor_run_reports,
    record_monitor_run_sync,
)


@pytest.fixture()
def report_db(tmp_path, monkeypatch):
    db_path = tmp_path / "app.sqlite3"
    monkeypatch.setenv("APP_DATABASE_FILE", str(db_path))
    return db_path


def test_record_and_load_run_report(report_db):
    record_monitor_run_sync(
        platform="kongfz",
        keyword="毛主席语录",
        task_name="小红书",
        status=STATUS_OK,
        message="窗口 30 条 · 新上架 0 · 递补 0 · 移除 0",
        window_size=30,
        new_listing_count=0,
        backfill_count=0,
        removed_count=0,
        run_at="2026-11-04T00:08:04.520542",
    )

    reports = load_monitor_run_reports("kongfz")
    report = reports["毛主席语录"]
    assert report["last_run_at"] == "2026-11-04T00:08:04.520542"
    assert report["last_status"] == STATUS_OK
    assert report["window_size"] == 30
    assert report["new_listing_count"] == 0
    assert report["task_name"] == "小红书"


def test_new_run_overwrites_previous_report(report_db):
    record_monitor_run_sync(
        platform="kongfz",
        keyword="英语",
        task_name="英语",
        status=STATUS_OK,
        new_listing_count=2,
        run_at="2026-11-04T00:06:00",
    )
    record_monitor_run_sync(
        platform="kongfz",
        keyword="英语",
        task_name="英语",
        status=STATUS_OK,
        new_listing_count=0,
        run_at="2026-11-04T00:07:00",
    )

    reports = load_monitor_run_reports("kongfz")
    assert len(reports) == 1
    assert reports["英语"]["last_run_at"] == "2026-11-04T00:07:00"
    assert reports["英语"]["new_listing_count"] == 0


def test_failed_run_is_recorded_with_message(report_db):
    record_monitor_run_sync(
        platform="kongfz",
        keyword="红楼梦",
        task_name="孔网-红楼梦",
        status=STATUS_FAILED,
        message="登录态失效：errType=102",
    )

    report = load_monitor_run_reports("kongfz")["红楼梦"]
    assert report["last_status"] == STATUS_FAILED
    assert "登录态失效" in report["last_message"]
    assert report["last_run_at"]


def test_skipped_run_is_recorded(report_db):
    record_monitor_run_sync(
        platform="kongfz",
        keyword="红楼梦",
        task_name="孔网-红楼梦",
        status=STATUS_SKIPPED,
        message="任务暂停中",
    )
    assert load_monitor_run_reports("kongfz")["红楼梦"]["last_status"] == STATUS_SKIPPED


def test_platforms_and_keywords_are_isolated(report_db):
    record_monitor_run_sync(platform="kongfz", keyword="红楼梦", task_name="A")
    record_monitor_run_sync(platform="xianyu", keyword="红楼梦", task_name="B")

    kongfz = load_monitor_run_reports("kongfz")
    xianyu = load_monitor_run_reports("xianyu")
    assert set(kongfz) == {"红楼梦"} and kongfz["红楼梦"]["task_name"] == "A"
    assert set(xianyu) == {"红楼梦"} and xianyu["红楼梦"]["task_name"] == "B"
    assert build_task_key("kongfz", "红楼梦") != build_task_key("xianyu", "红楼梦")


def test_empty_keyword_is_ignored(report_db):
    record_monitor_run_sync(platform="kongfz", keyword="   ", task_name="空")
    assert load_monitor_run_reports("kongfz") == {}