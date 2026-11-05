"""
AI 响应解析工具
"""
import json
from typing import Any


class EmptyAIResponseError(ValueError):
    """AI 返回了空内容。"""


def extract_ai_response_content(response: Any) -> str:
    """从不同形态的 AI 响应中提取文本内容。"""
    if response is None:
        raise EmptyAIResponseError("AI响应对象为空。")

    if isinstance(response, (bytes, bytearray)):
        text = response.decode("utf-8", errors="replace")
        return _normalize_text_content(text)

    if isinstance(response, str):
        return _normalize_text_content(response)

    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str):
        return _normalize_text_content(output_text)

    choices = getattr(response, "choices", None)
    if choices:
        message = getattr(choices[0], "message", None)
        if message is None:
            raise EmptyAIResponseError("AI响应缺少 message。")
        content = getattr(message, "content", None)
        
        # 智谱等 OpenAI 兼容网关在某些模式下会把输出放在 reasoning_content 而非 content
        try:
            return _normalize_text_content(_coerce_content_parts(content))
        except EmptyAIResponseError:
            reasoning_content = getattr(message, "reasoning_content", None)
            if reasoning_content:
                return _normalize_text_content(_coerce_content_parts(reasoning_content))
            raise

    raise ValueError(f"无法识别的AI响应类型: {type(response).__name__}")


def parse_ai_response_json(content: str, required_keys=None) -> dict:
    """解析 AI 文本响应中的 JSON。

    ``required_keys`` 用于应对“推理型模型把思考草稿写进正文”的情况：一段响应里可能
    混着若干个 JSON 片段（草稿、示例、最终结果），此时优先返回同时包含全部必需字段的
    那个对象，而不是简单取第一个 ``{``。
    """
    cleaned = _strip_code_fences(content)
    required = tuple(required_keys or ())

    parsed = None
    parse_error = None
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        parse_error = exc

    if parsed is not None and _matches_required_keys(parsed, required):
        return parsed

    candidate = _extract_best_json_value(cleaned, required)
    if candidate is not None:
        return candidate

    if parsed is not None:
        return parsed
    if parse_error is not None:
        raise parse_error
    raise json.JSONDecodeError("响应中未找到 JSON 内容", cleaned, 0)


def _coerce_content_parts(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, (bytes, bytearray)):
        return content.decode("utf-8", errors="replace")
    if not isinstance(content, list):
        raise ValueError(f"AI响应内容类型不受支持: {type(content).__name__}")

    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
            continue
        if isinstance(item, dict):
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
            continue
        text = getattr(item, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _normalize_text_content(content: str) -> str:
    text = str(content).strip()
    if not text:
        raise EmptyAIResponseError("AI响应内容为空。")
    return text


def _strip_code_fences(content: str) -> str:
    cleaned = content.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()


def _matches_required_keys(value: Any, required: tuple[str, ...]) -> bool:
    if not required:
        return True
    return isinstance(value, dict) and all(key in value for key in required)


def _required_key_score(value: Any, required: tuple[str, ...]) -> int:
    if not required:
        return 1 if isinstance(value, dict) else 0
    if not isinstance(value, dict):
        return 0
    return sum(1 for key in required if key in value)


def _extract_best_json_value(content: str, required: tuple[str, ...] = ()):
    """扫描文本中所有可解析的 JSON 片段，优先返回匹配必需字段的那个。"""
    decoder = json.JSONDecoder()
    best = None
    best_score = -1

    for start_index, char in enumerate(content):
        if char not in "{[":
            continue
        try:
            value, _ = decoder.raw_decode(content[start_index:])
        except json.JSONDecodeError:
            continue
        if _matches_required_keys(value, required):
            return value
        score = _required_key_score(value, required)
        if score > best_score:
            best = value
            best_score = score

    return best


def _extract_first_json_value(
    content: str,
    fallback_error: json.JSONDecodeError,
):
    value = _extract_best_json_value(content)
    if value is not None:
        return value
    raise fallback_error
