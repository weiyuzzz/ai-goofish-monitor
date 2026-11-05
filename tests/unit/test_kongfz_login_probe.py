"""登录探测结果归类单测（不依赖浏览器/网络）。"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

# kongfz_login.py 位于仓库根目录
_MODULE_PATH = Path(__file__).resolve().parents[2] / "kongfz_login.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("kongfz_login_module", _MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["kongfz_login_module"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def login_module():
    return _load_module()


def test_probe_ok_when_status_is_one(login_module):
    state, detail = login_module._classify_probe_payload(
        json.dumps({"status": 1, "data": {"totalFound": 1}})
    )
    assert state == login_module.PROBE_OK
    assert "认可" in detail


def test_probe_not_logged_in_for_err_type_102(login_module):
    state, detail = login_module._classify_probe_payload(
        json.dumps(
            {
                "status": 0,
                "errType": "102",
                "message": "请登录后再进行访问。",
            }
        )
    )
    assert state == login_module.PROBE_NOT_LOGGED_IN
    assert "errType=102" in detail
    assert "请登录" in detail


def test_probe_not_logged_in_by_message_only(login_module):
    state, _detail = login_module._classify_probe_payload(
        json.dumps({"status": 0, "message": "请登录后再进行访问。"})
    )
    assert state == login_module.PROBE_NOT_LOGGED_IN


def test_probe_needs_verify_for_antibot_message(login_module):
    """站点风控文案（「类似计算机软件的自动请求」）不能被误判成「未登录」。"""
    message = "很抱歉，您的查询类似计算机软件的自动请求，请您进行验证后再继续搜索。"
    state, detail = login_module._classify_probe_payload(
        json.dumps({"status": 0, "errType": "102", "message": message})
    )
    assert state == login_module.PROBE_NEEDS_VERIFY
    assert "人机验证" in detail


def test_probe_not_logged_in_for_plain_login_message(login_module):
    state, _detail = login_module._classify_probe_payload(
        json.dumps({"status": 0, "errType": "102", "message": "请登录后再进行访问。"})
    )
    assert state == login_module.PROBE_NOT_LOGGED_IN


def test_probe_error_for_non_json_body(login_module):
    state, detail = login_module._classify_probe_payload(
        "<html><body>验证码</body></html>", http_status=403
    )
    assert state == login_module.PROBE_ERROR
    assert "非 JSON" in detail
    assert "403" in detail


def test_probe_error_for_other_api_error(login_module):
    state, detail = login_module._classify_probe_payload(
        json.dumps({"status": 0, "errType": "500", "message": "服务异常"})
    )
    assert state == login_module.PROBE_ERROR
    assert "服务异常" in detail


def test_probe_error_for_non_dict_payload(login_module):
    state, _detail = login_module._classify_probe_payload(json.dumps([1, 2, 3]))
    assert state == login_module.PROBE_ERROR


def test_fallback_save_delay_is_reasonable(login_module):
    # 兜底落盘不能太快（避免把半成品当成功），也不能太久（用户白等）
    assert 10 <= login_module.FALLBACK_SAVE_SECONDS <= 120