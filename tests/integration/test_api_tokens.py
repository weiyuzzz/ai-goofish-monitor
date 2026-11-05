from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import tokens
from src.services import token_usage_service as svc


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(tokens.router)
    return TestClient(app)


def test_token_summary_reports_today_usage_and_trend(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DATABASE_FILE", str(tmp_path / "app.sqlite3"))
    monkeypatch.setattr(svc, "_schema_ready", False)

    now = datetime.now()
    svc.record_token_usage(
        prompt_tokens=1200,
        completion_tokens=300,
        model="deepseek-v4.1-flash",
        source=svc.SOURCE_ANALYSIS,
        task_name="mac mini",
        recorded_at=now,
    )
    svc.record_token_usage(
        prompt_tokens=200,
        completion_tokens=100,
        model="deepseek-v4.1-flash",
        source=svc.SOURCE_PROMPT,
        recorded_at=now,
    )
    svc.record_token_usage(
        prompt_tokens=5000,
        completion_tokens=5000,
        source=svc.SOURCE_ANALYSIS,
        recorded_at=now - timedelta(days=2),
    )

    client = _build_client()
    response = client.get("/api/tokens/summary", params={"days": 5})

    assert response.status_code == 200
    payload = response.json()

    assert payload["today"]["day"] == now.date().isoformat()
    assert payload["today"]["prompt_tokens"] == 1400
    assert payload["today"]["completion_tokens"] == 400
    assert payload["today"]["total_tokens"] == 1800
    assert payload["today"]["requests"] == 2
    assert payload["trend_days"] == 5
    assert len(payload["daily"]) == 5
    assert len(payload["hourly"]) == 24
    assert payload["all_time"]["total_tokens"] == 11800

    assert payload["models"][0]["name"] == "deepseek-v4.1-flash"
    assert [item["name"] for item in payload["sources"]] == [
        svc.SOURCE_ANALYSIS,
        svc.SOURCE_PROMPT,
    ]
    assert payload["recent"][0]["source"] == svc.SOURCE_PROMPT
    assert payload["recent"][1]["task_name"] == "mac mini"


def test_token_summary_rejects_out_of_range_window(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DATABASE_FILE", str(tmp_path / "app.sqlite3"))
    monkeypatch.setattr(svc, "_schema_ready", False)

    client = _build_client()

    assert client.get("/api/tokens/summary", params={"days": 0}).status_code == 422
    assert client.get("/api/tokens/summary", params={"days": 999}).status_code == 422
    assert client.get("/api/tokens/summary").status_code == 200