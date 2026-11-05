"""孔夫子登录态（账号栏）与平台推送开关单测。"""
import json

from src.infrastructure.config.settings import NotificationSettings
from src.kongfz.accounts import (
    build_state_path,
    default_state_path,
    describe_state_file,
    list_state_files,
    resolve_task_state_file,
)
from src.kongfz.client import load_cookie_header
from src.services.notification_config_service import is_platform_notification_enabled


def _write_state(path, cookies):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"cookies": cookies, "origins": []}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_state_path_helpers_normalize_names(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCOUNT_STATE_DIR", str(tmp_path))
    assert build_state_path("kongfz_2").endswith("kongfz_2.json")
    assert build_state_path("2").endswith("kongfz_2.json")
    assert build_state_path("").endswith("kongfz_1.json")
    assert default_state_path(1) == build_state_path("kongfz_1")


def test_load_cookie_header_filters_kongfz_domain(tmp_path):
    state_file = tmp_path / "kongfz_1.json"
    _write_state(
        state_file,
        [
            {"domain": ".kongfz.com", "name": "PHPSESSID", "value": "abc"},
            {"domain": "search.kongfz.com", "name": "kfz_uuid", "value": "xyz"},
            {"domain": ".goofish.com", "name": "_m_h5_tk", "value": "should-be-ignored"},
            {"domain": ".kongfz.com", "name": "empty", "value": ""},
        ],
    )

    header = load_cookie_header(str(state_file))
    assert "PHPSESSID=abc" in header
    assert "kfz_uuid=xyz" in header
    assert "goofish" not in header
    assert "empty=" not in header


def test_load_cookie_header_handles_missing_or_broken_file(tmp_path):
    assert load_cookie_header(str(tmp_path / "nope.json")) == ""
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert load_cookie_header(str(broken)) == ""


def test_describe_state_file_reports_validity(tmp_path):
    valid = tmp_path / "kongfz_1.json"
    _write_state(valid, [{"domain": ".kongfz.com", "name": "a", "value": "1"}])
    info = describe_state_file(str(valid))
    assert info["valid"] is True
    assert info["cookie_count"] == 1
    assert info["updated_at"]

    invalid = tmp_path / "kongfz_2.json"
    _write_state(invalid, [{"domain": ".goofish.com", "name": "a", "value": "1"}])
    info = describe_state_file(str(invalid))
    assert info["valid"] is False
    assert "kongfz" in info["error"]


def test_list_and_resolve_state_files(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCOUNT_STATE_DIR", str(tmp_path))
    _write_state(
        tmp_path / "kongfz_1.json",
        [{"domain": ".kongfz.com", "name": "a", "value": "1"}],
    )

    entries = list_state_files()
    assert [entry["name"] for entry in entries] == ["kongfz_1.json"]
    assert resolve_task_state_file({}) == str(tmp_path / "kongfz_1.json")
    assert resolve_task_state_file({"account_state_file": str(tmp_path / "kongfz_1.json")}) == str(
        tmp_path / "kongfz_1.json"
    )


def test_platform_notification_switches_are_independent():
    both_on = NotificationSettings.model_construct(notify_xianyu=True, notify_kongfz=True)
    assert is_platform_notification_enabled("xianyu", settings=both_on) is True
    assert is_platform_notification_enabled("kongfz", settings=both_on) is True

    only_kongfz = NotificationSettings.model_construct(notify_xianyu=False, notify_kongfz=True)
    assert is_platform_notification_enabled("xianyu", settings=only_kongfz) is False
    assert is_platform_notification_enabled("kongfz", settings=only_kongfz) is True

    only_xianyu = NotificationSettings.model_construct(notify_xianyu=True, notify_kongfz=False)
    assert is_platform_notification_enabled("xianyu", settings=only_xianyu) is True
    assert is_platform_notification_enabled("kongfz", settings=only_xianyu) is False

    both_off = NotificationSettings.model_construct(notify_xianyu=False, notify_kongfz=False)
    assert is_platform_notification_enabled("xianyu", settings=both_off) is False
    assert is_platform_notification_enabled("kongfz", settings=both_off) is False