from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from src.services import token_usage_service as svc


@pytest.fixture()
def token_db(tmp_path, monkeypatch):
    """把 token 用量写到临时库，并重置进程内的建表缓存。"""
    db_path = tmp_path / "tokens.sqlite3"
    monkeypatch.setenv("APP_DATABASE_FILE", str(db_path))
    monkeypatch.setattr(svc, "_schema_ready", False)
    return db_path


def test_extract_token_usage_supports_chat_completions_usage():
    response = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=120,
            completion_tokens=30,
            total_tokens=150,
        )
    )

    assert svc.extract_token_usage(response) == {
        "prompt_tokens": 120,
        "completion_tokens": 30,
        "total_tokens": 150,
    }


def test_extract_token_usage_supports_responses_api_usage():
    response = {"usage": {"input_tokens": 40, "output_tokens": 5}}

    assert svc.extract_token_usage(response) == {
        "prompt_tokens": 40,
        "completion_tokens": 5,
        "total_tokens": 45,
    }


def test_extract_token_usage_uses_local_sum_when_gateway_total_undercounts():
    response = {"usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 120}}

    assert svc.extract_token_usage(response)["total_tokens"] == 150


def test_extract_token_usage_returns_none_without_usage():
    assert svc.extract_token_usage(SimpleNamespace(output_text="ok")) is None
    assert svc.extract_token_usage(None) is None


def test_record_response_usage_without_usage_does_not_touch_database(monkeypatch):
    def explode(*_args, **_kwargs):
        raise AssertionError("没有用量信息时不应访问数据库")

    monkeypatch.setattr(svc, "sqlite_connection", explode)

    assert svc.record_response_usage(SimpleNamespace(output_text="ok")) is False


def test_record_token_usage_swallows_database_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DATABASE_FILE", str(tmp_path / "broken.sqlite3"))
    monkeypatch.setattr(svc, "_schema_ready", False)

    @contextmanager
    def broken_connection(*_args, **_kwargs):
        raise RuntimeError("database is locked")
        yield  # pragma: no cover

    monkeypatch.setattr(svc, "sqlite_connection", broken_connection)

    assert svc.record_token_usage(prompt_tokens=10, completion_tokens=5) is False


def test_snapshot_aggregates_today_hourly_daily_and_breakdowns(token_db):
    now = datetime(2026, 11, 2, 15, 30, 0)
    svc.record_token_usage(
        prompt_tokens=1000,
        completion_tokens=200,
        model="deepseek-v4.1-flash",
        source=svc.SOURCE_ANALYSIS,
        task_name="mac mini",
        recorded_at=now.replace(hour=9, minute=5),
    )
    svc.record_token_usage(
        prompt_tokens=500,
        completion_tokens=100,
        model="deepseek-v4.1-flash",
        source=svc.SOURCE_ANALYSIS,
        task_name="mac mini",
        recorded_at=now.replace(hour=9, minute=40),
    )
    svc.record_token_usage(
        prompt_tokens=300,
        completion_tokens=50,
        model="gpt-4o-mini",
        source=svc.SOURCE_PROMPT,
        recorded_at=now.replace(hour=15, minute=1),
    )
    # 前一天的数据不应计入当日
    svc.record_token_usage(
        prompt_tokens=9999,
        completion_tokens=9999,
        source=svc.SOURCE_ANALYSIS,
        recorded_at=now - timedelta(days=1),
    )

    snapshot = svc.build_token_usage_snapshot(days=3, now=now)

    assert snapshot["generated_at"] == now.isoformat()
    assert snapshot["today"]["day"] == "2026-11-02"
    assert snapshot["today"] == {
        "day": "2026-11-02",
        "prompt_tokens": 1800,
        "completion_tokens": 350,
        "total_tokens": 2150,
        "requests": 3,
    }

    assert len(snapshot["hourly"]) == 24
    nine = snapshot["hourly"][9]
    assert nine["label"] == "09:00"
    assert nine["total_tokens"] == 1800
    assert nine["requests"] == 2
    assert snapshot["hourly"][15]["total_tokens"] == 350
    assert snapshot["hourly"][8]["total_tokens"] == 0

    assert len(snapshot["daily"]) == 3
    assert [item["day"] for item in snapshot["daily"]] == [
        "2026-10-31",
        "2026-11-01",
        "2026-11-02",
    ]
    assert snapshot["daily"][-1]["total_tokens"] == 2150
    assert snapshot["daily"][-1]["label"] == "11-02"
    assert snapshot["daily"][1]["total_tokens"] == 19998
    assert snapshot["daily"][0]["total_tokens"] == 0

    assert snapshot["models"][0]["name"] == "deepseek-v4.1-flash"
    assert snapshot["models"][0]["total_tokens"] == 1800
    assert snapshot["models"][0]["requests"] == 2
    assert snapshot["models"][1]["name"] == "gpt-4o-mini"

    assert snapshot["sources"][0]["name"] == svc.SOURCE_ANALYSIS
    assert snapshot["sources"][0]["total_tokens"] == 1800
    assert snapshot["sources"][1]["name"] == svc.SOURCE_PROMPT

    assert snapshot["all_time"]["total_tokens"] == 2150 + 19998
    assert snapshot["all_time"]["requests"] == 4

    assert len(snapshot["recent"]) == 4
    assert snapshot["recent"][0]["source"] == svc.SOURCE_PROMPT
    assert snapshot["recent"][0]["model"] == "gpt-4o-mini"
    assert snapshot["recent"][1]["task_name"] == "mac mini"
    assert snapshot["recent"][3]["day"] == "2026-11-01"
    assert snapshot["recent"][3]["task_name"] is None


def test_snapshot_zero_fills_empty_window(token_db):
    snapshot = svc.build_token_usage_snapshot(days=7)

    assert snapshot["today"]["total_tokens"] == 0
    assert snapshot["today"]["requests"] == 0
    assert len(snapshot["daily"]) == 7
    assert all(item["total_tokens"] == 0 for item in snapshot["daily"])
    assert len(snapshot["hourly"]) == 24
    assert snapshot["models"] == []
    assert snapshot["sources"] == []
    assert snapshot["all_time"]["total_tokens"] == 0
    assert snapshot["recent"] == []


def test_clamp_trend_days_keeps_window_in_supported_range():
    assert svc.clamp_trend_days(7) == 7
    assert svc.clamp_trend_days(0) == 1
    assert svc.clamp_trend_days(1000) == svc.MAX_TREND_DAYS
    assert svc.clamp_trend_days("bad") == svc.DEFAULT_TREND_DAYS