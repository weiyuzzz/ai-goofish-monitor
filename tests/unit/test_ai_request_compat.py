from src.services.ai_request_compat import (
    add_thinking_disabled_param,
    is_json_output_unsupported_error,
    is_responses_api_unsupported_error,
    is_temperature_unsupported_error,
    remove_temperature_param,
)


def test_add_thinking_disabled_param_sets_both_gateway_flavours():
    params = {"model": "x", "messages": []}
    result = add_thinking_disabled_param(params, True)

    assert result["extra_body"]["enable_thinking"] is False
    assert result["extra_body"]["thinking"] == {"type": "disabled"}
    # 不修改原始入参
    assert "extra_body" not in params


def test_add_thinking_disabled_param_disabled_keeps_other_extra_body():
    params = {"model": "x", "extra_body": {"top_k": 5}}
    result = add_thinking_disabled_param(params, False)

    assert result["extra_body"] == {"top_k": 5}


def test_add_thinking_disabled_param_disabled_drops_empty_extra_body():
    params = {"model": "x", "extra_body": {"enable_thinking": False, "thinking": {"type": "disabled"}}}
    result = add_thinking_disabled_param(params, False)

    assert "extra_body" not in result


def test_is_temperature_unsupported_error_detects_unsupported_message():
    err = Exception("temperature is not supported by this gateway")
    assert is_temperature_unsupported_error(err) is True


def test_remove_temperature_param_removes_only_temperature():
    params = {"model": "x", "temperature": 0.5, "max_output_tokens": 128}
    result = remove_temperature_param(params)

    assert "temperature" not in result
    assert result["model"] == "x"
    assert result["max_output_tokens"] == 128


def test_is_responses_api_unsupported_error_detects_gemini_plain_404():
    class _Resp:
        text = ""

    class _Err(Exception):
        status_code = 404
        body = ""
        response = _Resp()

        def __str__(self):
            return "Error code: 404"

    assert is_responses_api_unsupported_error(_Err()) is True


# -- is_json_output_unsupported_error tests --


def test_json_output_error_detected_via_body_param_response_format():
    """Vercel AI Gateway returns 400 with param='response_format'."""

    class _Err(Exception):
        body = {
            "message": "Invalid input",
            "type": "invalid_request_error",
            "param": "response_format",
            "code": "invalid_request_error",
        }

    assert is_json_output_unsupported_error(_Err()) is True


def test_json_output_error_detected_via_body_param_response_format_type():
    class _Err(Exception):
        body = {
            "message": "Invalid input",
            "param": "response_format.type",
        }

    assert is_json_output_unsupported_error(_Err()) is True


def test_json_output_error_detected_via_legacy_string_matching():
    err = Exception(
        "response_format.type is not supported by this model"
    )
    assert is_json_output_unsupported_error(err) is True


def test_json_output_error_not_triggered_by_unrelated_400():
    class _Err(Exception):
        body = {
            "message": "Invalid input",
            "param": "messages",
        }

    assert is_json_output_unsupported_error(_Err()) is False


def test_json_output_error_not_triggered_without_body():
    err = Exception("some random 400 error")
    assert is_json_output_unsupported_error(err) is False
