"""孔夫子 runner 集成测试：滚动窗口的增/删/补位与「只通知真·新上架」。"""
import asyncio
import json

import pytest

from src.infrastructure.persistence.sqlite_connection import sqlite_connection
from src.kongfz import runner as kongfz_runner
from src.kongfz.client import FetchResult
from src.services.result_storage_service import load_monitor_window


def _item(item_id: str, *, sold: bool = False) -> dict:
    return {
        "itemId": item_id,
        "title": f"书 {item_id}",
        "isSoldOut": sold,
        "priceText": "30.00",
        "link": {"pc": f"https://book.kongfz.com/1/{item_id}"},
        "shopName": "测试书店",
        "showTimeText": "2026-11-03 21:57 上书",
    }


class FakeClient:
    """按调用顺序消费共享队列中的抓取结果（跨 client 实例）。"""

    def __init__(self, queue, **_kwargs):
        self._queue = queue
        self.calls = 0
        self.warmup_calls = 0
        self.decoy_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def warmup(self, keyword=""):
        self.warmup_calls += 1

    async def decoy_actions(self, items):
        self.decoy_calls += 1

    async def fetch_items(self, keyword, *, scan_limit, page_size):
        self.calls += 1
        assert self._queue, "FakeClient 队列已耗尽"
        return self._queue.pop(0)


@pytest.fixture()
def kongfz_env(tmp_path, monkeypatch):
    db_path = tmp_path / "app.sqlite3"
    monkeypatch.setenv("APP_DATABASE_FILE", str(db_path))
    monkeypatch.setenv("ACCOUNT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("KONGFZ_LIST_SCENE", raising=False)
    # 测试里不要真的随机等待
    monkeypatch.setenv("KONGFZ_START_JITTER_SECONDS", "0")

    notifications: list[tuple[dict, str, str]] = []

    async def fake_notify(payload, reason, platform="xianyu"):
        notifications.append((payload, reason, platform))
        return {}

    monkeypatch.setattr(kongfz_runner, "send_ntfy_notification", fake_notify)
    monkeypatch.setattr(kongfz_runner, "cleanup_task_images", lambda _name: None)
    monkeypatch.setattr(kongfz_runner, "resolve_task_state_file", lambda _cfg: None)

    return {"db_path": db_path, "notifications": notifications, "monkeypatch": monkeypatch}


def _task_config(**overrides) -> dict:
    config = {
        "task_name": "孔网-红楼梦",
        "keyword": "红楼梦",
        "platform": "kongfz",
        "latest_count": 3,
        "notify_all_new": True,
        "notify_removal": False,
        "decision_mode": "ai",
        "keyword_rules": [],
    }
    config.update(overrides)
    return config


def _rows(db_path: str) -> list[dict]:
    with sqlite_connection(str(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT link_unique_key, item_id, platform, window_rank, entry_type, result_filename
            FROM result_items ORDER BY window_rank ASC, id ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _fetch(items: list[dict]) -> FetchResult:
    return FetchResult(
        items=items,
        pages=1,
        scanned=len(items),
        total_found=len(items),
        reached_end=False,
        scene="latest",
    )


def test_first_run_is_baseline_and_sends_no_notifications(kongfz_env):
    queue = [_fetch([_item(str(101 + i)) for i in range(5)])]
    kongfz_env["monkeypatch"].setattr(
        kongfz_runner, "KongfzClient", lambda **kw: FakeClient(queue, **kw)
    )

    async def run():
        config = _task_config()
        first = await kongfz_runner.run_kongfz_task(config)
        window = await load_monitor_window("红楼梦", "kongfz")
        return first, window

    first, window = asyncio.run(run())

    # 首轮：全部按基线入库（窗口取前 3 条），不发送任何通知
    assert first == 0
    assert [row["link_unique_key"] for row in window] == ["kfz:101", "kfz:102", "kfz:103"]
    assert kongfz_env["notifications"] == []
    assert {row["entry_type"] for row in _rows(kongfz_env["db_path"])} == {None}


def test_only_genuine_new_listings_are_notified_backfill_is_silent(kongfz_env):
    """用户场景：已有 10 个样本 → 新上架 1 个 + 下架 2 个 → 只通知新上架的那 1 个。"""
    baseline = [_item(str(101 + i)) for i in range(10)]
    # 第二轮：100 新上架；105 / 109 售出下架；111 / 112 从窗口外递补
    second_round = (
        [_item("100")]
        + baseline[:4]                                  # 101 102 103 104
        + [_item("105", sold=True)]                     # 下架
        + baseline[5:8]                                 # 106 107 108
        + [_item("109", sold=True)]                     # 下架
        + [baseline[9]]                                  # 110
        + [_item("111"), _item("112")]                  # 递补
    )
    queue = [_fetch(baseline), _fetch(second_round)]
    kongfz_env["monkeypatch"].setattr(
        kongfz_runner, "KongfzClient", lambda **kw: FakeClient(queue, **kw)
    )

    async def run():
        config = _task_config(latest_count=10)
        await kongfz_runner.run_kongfz_task(config)     # 首轮基线
        count = await kongfz_runner.run_kongfz_task(config)
        window = await load_monitor_window("红楼梦", "kongfz")
        return count, window

    count, window = asyncio.run(run())

    # 第二轮窗口：100 顶到最前，105/109 移除，111 递补补位（112 仍在窗口外）
    assert count == 1
    assert [row["link_unique_key"] for row in window] == [
        "kfz:100", "kfz:101", "kfz:102", "kfz:103", "kfz:104",
        "kfz:106", "kfz:107", "kfz:108", "kfz:110", "kfz:111",
    ]
    assert [row["window_rank"] for row in window] == list(range(10))

    # 通知：首轮 0 条（基线）+ 第二轮仅 100（新上架），111 递补不通知
    assert len(kongfz_env["notifications"]) == 1
    payload, _reason, platform = kongfz_env["notifications"][0]
    assert payload["商品标题"] == "书 100"
    assert payload["来源"] == "孔夫子旧书网"
    assert platform == "kongfz"

    # 入库标记：100 = new_listing，111 = backfill，其余为基线
    rows = _rows(kongfz_env["db_path"])
    marks = {row["link_unique_key"]: row["entry_type"] for row in rows}
    assert marks["kfz:100"] == "new_listing"
    assert marks["kfz:111"] == "backfill"
    assert marks["kfz:101"] is None
    assert {row["platform"] for row in rows} == {"kongfz"}
    assert {row["result_filename"] for row in rows} == {"kongfz_红楼梦_full_data.jsonl"}


def test_runner_skips_removal_when_fetch_is_incomplete(kongfz_env):
    queue: list[FetchResult] = [
        _fetch([_item("201"), _item("202")]),
        FetchResult(
            items=[],
            pages=0,
            scanned=0,
            total_found=0,
            reached_end=False,
            scene="latest",
        ),
    ]
    kongfz_env["monkeypatch"].setattr(
        kongfz_runner, "KongfzClient", lambda **kw: FakeClient(queue, **kw)
    )

    async def run():
        config = _task_config(latest_count=2)
        await kongfz_runner.run_kongfz_task(config)
        before = await load_monitor_window("红楼梦", "kongfz")
        # 第二次抓取失败（pages=0 → complete=False），不应删除任何记录
        await kongfz_runner.run_kongfz_task(config)
        after = await load_monitor_window("红楼梦", "kongfz")
        return before, after

    before, after = asyncio.run(run())

    assert [row["link_unique_key"] for row in before] == ["kfz:201", "kfz:202"]
    assert [row["link_unique_key"] for row in after] == ["kfz:201", "kfz:202"]
    assert queue == []


def test_keyword_mode_analysis_applies_to_new_listing_only(kongfz_env):
    queue = [
        _fetch([_item("301")]),          # 首轮基线
        _fetch([_item("300"), _item("301")]),  # 300 新上架（在锚点之前）
    ]
    kongfz_env["monkeypatch"].setattr(
        kongfz_runner, "KongfzClient", lambda **kw: FakeClient(queue, **kw)
    )

    async def run():
        config = _task_config(
            latest_count=1,
            notify_all_new=False,
            decision_mode="keyword",
            keyword_rules=["书"],
        )
        await kongfz_runner.run_kongfz_task(config)
        await kongfz_runner.run_kongfz_task(config)

    asyncio.run(run())

    with sqlite_connection(str(kongfz_env["db_path"])) as conn:
        rows = conn.execute(
            "SELECT raw_json, entry_type FROM result_items ORDER BY id"
        ).fetchall()
    assert len(rows) == 1  # 窗口只有 1 条，301 已被 300 挤出并移除
    record = json.loads(rows[0]["raw_json"])
    assert record["商品信息"]["商品ID"] == "300"
    assert rows[0]["entry_type"] == "new_listing"
    assert record["入库方式"] == "new_listing"
    assert record["ai_analysis"]["is_recommended"] is True
    assert record["ai_analysis"]["analysis_source"] == "keyword"
    assert record["平台"] == "kongfz"