"""
AI Token 用量记录与统计服务。

每次模型调用后写入一条用量记录（``ai_token_usage`` 表），并按“当日 / 分时 /
近 N 天 / 模型 / 来源”聚合，供 Web UI 的 Token 监测页面展示。

设计约束：记录失败绝不影响 AI 调用本身，所有写入都以 try/except 兜底。
"""
from __future__ import annotations

import threading
from datetime import date, datetime, timedelta
from typing import Any, Optional

from src.infrastructure.persistence.sqlite_connection import (
    TOKEN_USAGE_SCHEMA_STATEMENTS,
    sqlite_connection,
)

# 调用来源标签，前端按这些 key 展示本地化名称。
SOURCE_ANALYSIS = "analysis"
SOURCE_PROMPT = "prompt"
SOURCE_TEST = "test"

DEFAULT_TREND_DAYS = 14
MAX_TREND_DAYS = 90
RECENT_LIMIT = 20
HOURS_PER_DAY = 24
MODEL_BREAKDOWN_LIMIT = 8

_schema_lock = threading.Lock()
_schema_ready = False


def _ensure_schema(conn) -> None:
    """确保 token 用量表存在（每个进程只执行一次建表语句）。"""
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        for statement in TOKEN_USAGE_SCHEMA_STATEMENTS:
            conn.execute(statement)
        _schema_ready = True


# --------------------------------------------------------------------------- #
# 记录
# --------------------------------------------------------------------------- #


def extract_token_usage(response: Any) -> Optional[dict]:
    """从 OpenAI 兼容响应中提取 token 用量；无用量信息时返回 ``None``。

    Chat Completions 返回 ``prompt_tokens`` / ``completion_tokens``，
    Responses API 返回 ``input_tokens`` / ``output_tokens``，两种都支持。
    """
    usage = _resolve_usage(response)
    if usage is None:
        return None

    prompt = _coerce_optional_int(
        _usage_field(usage, "prompt_tokens", "input_tokens")
    )
    completion = _coerce_optional_int(
        _usage_field(usage, "completion_tokens", "output_tokens")
    )
    total = _coerce_optional_int(_usage_field(usage, "total_tokens"))

    if prompt is None and completion is None and total is None:
        return None

    prompt = prompt or 0
    completion = completion or 0
    if total is None:
        total = prompt + completion
    # 部分网关的 total_tokens 不含缓存命中部分，这里以本地求和为准。
    total = max(total, prompt + completion)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
    }


def record_response_usage(
    response: Any,
    *,
    model: Optional[str] = None,
    source: str = SOURCE_ANALYSIS,
    task_name: Optional[str] = None,
) -> bool:
    """从响应对象里提取并记录 token 用量。

    响应没有携带用量信息时返回 ``False``，不做任何写入。
    """
    usage = extract_token_usage(response)
    if usage is None:
        return False

    return record_token_usage(
        prompt_tokens=usage["prompt_tokens"],
        completion_tokens=usage["completion_tokens"],
        total_tokens=usage["total_tokens"],
        model=_resolve_model(response) or model,
        source=source,
        task_name=task_name,
    )


def record_token_usage(
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: Optional[int] = None,
    model: Optional[str] = None,
    source: str = SOURCE_ANALYSIS,
    task_name: Optional[str] = None,
    recorded_at: Optional[datetime] = None,
) -> bool:
    """写入一条 token 用量记录。失败只打印告警，返回 ``False``。"""
    try:
        moment = recorded_at or datetime.now()
        prompt = _coerce_non_negative_int(prompt_tokens)
        completion = _coerce_non_negative_int(completion_tokens)
        total = (
            _coerce_non_negative_int(total_tokens)
            if total_tokens is not None
            else prompt + completion
        )
        total = max(total, prompt + completion)

        with sqlite_connection() as conn:
            _ensure_schema(conn)
            conn.execute(
                """
                INSERT INTO ai_token_usage (
                    recorded_at, usage_day, model, source, task_name,
                    prompt_tokens, completion_tokens, total_tokens
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    moment.isoformat(),
                    moment.date().isoformat(),
                    _clean_text(model),
                    _clean_text(source) or SOURCE_ANALYSIS,
                    _clean_text(task_name),
                    prompt,
                    completion,
                    total,
                ),
            )
            conn.commit()
        return True
    except Exception as exc:  # pragma: no cover - 仅防御性兜底
        print(f"记录 token 用量失败，已跳过: {exc}")
        return False


# --------------------------------------------------------------------------- #
# 聚合
# --------------------------------------------------------------------------- #


def build_token_usage_snapshot(
    *,
    days: int = DEFAULT_TREND_DAYS,
    now: Optional[datetime] = None,
) -> dict:
    """构建 Token 监测页面所需的全部数据。"""
    moment = now or datetime.now()
    window = clamp_trend_days(days)
    today_key = moment.date().isoformat()
    start_key = (moment.date() - timedelta(days=window - 1)).isoformat()

    with sqlite_connection() as conn:
        _ensure_schema(conn)
        today_total = _fetch_totals(conn, "WHERE usage_day = ?", (today_key,))
        all_time_total = _fetch_totals(conn)
        today_rows = conn.execute(
            """
            SELECT recorded_at, prompt_tokens, completion_tokens, total_tokens
            FROM ai_token_usage
            WHERE usage_day = ?
            """,
            (today_key,),
        ).fetchall()
        daily_rows = conn.execute(
            """
            SELECT usage_day,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COUNT(1) AS requests
            FROM ai_token_usage
            WHERE usage_day >= ?
            GROUP BY usage_day
            """,
            (start_key,),
        ).fetchall()
        model_rows = conn.execute(
            """
            SELECT COALESCE(NULLIF(model, ''), 'unknown') AS name,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COUNT(1) AS requests
            FROM ai_token_usage
            WHERE usage_day = ?
            GROUP BY name
            ORDER BY total_tokens DESC, requests DESC
            LIMIT ?
            """,
            (today_key, MODEL_BREAKDOWN_LIMIT),
        ).fetchall()
        source_rows = conn.execute(
            """
            SELECT COALESCE(NULLIF(source, ''), 'unknown') AS name,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COUNT(1) AS requests
            FROM ai_token_usage
            WHERE usage_day = ?
            GROUP BY name
            ORDER BY total_tokens DESC, requests DESC
            """,
            (today_key,),
        ).fetchall()
        recent_rows = conn.execute(
            """
            SELECT recorded_at, usage_day, model, source, task_name,
                   prompt_tokens, completion_tokens, total_tokens
            FROM ai_token_usage
            ORDER BY recorded_at DESC, id DESC
            LIMIT ?
            """,
            (RECENT_LIMIT,),
        ).fetchall()

    return {
        "generated_at": moment.isoformat(),
        "today": {"day": today_key, **today_total},
        "hourly": _build_hourly_buckets(today_key, today_rows),
        "daily": _build_daily_buckets(moment.date(), window, daily_rows),
        "trend_days": window,
        "models": [_row_to_group(row) for row in model_rows],
        "sources": [_row_to_group(row) for row in source_rows],
        "all_time": _row_to_totals(all_time_total),
        "recent": [_row_to_recent(row) for row in recent_rows],
    }


def clamp_trend_days(days: int) -> int:
    try:
        value = int(days)
    except (TypeError, ValueError):
        return DEFAULT_TREND_DAYS
    return max(1, min(MAX_TREND_DAYS, value))


def _fetch_totals(conn, where_clause: str = "", params: tuple = ()) -> dict:
    row = conn.execute(
        f"""
        SELECT COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
               COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
               COALESCE(SUM(total_tokens), 0) AS total_tokens,
               COUNT(1) AS requests
        FROM ai_token_usage
        {where_clause}
        """,
        params,
    ).fetchone()
    return _row_to_totals(row)


def _build_hourly_buckets(today_key: str, rows) -> list[dict]:
    buckets = [
        {
            "hour": hour,
            "label": f"{hour:02d}:00",
            "day": today_key,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "requests": 0,
        }
        for hour in range(HOURS_PER_DAY)
    ]
    for row in rows or ():
        hour = _extract_hour(row["recorded_at"])
        if hour is None:
            continue
        bucket = buckets[hour]
        bucket["prompt_tokens"] += int(row["prompt_tokens"] or 0)
        bucket["completion_tokens"] += int(row["completion_tokens"] or 0)
        bucket["total_tokens"] += int(row["total_tokens"] or 0)
        bucket["requests"] += 1
    return buckets


def _build_daily_buckets(today: date, window: int, rows) -> list[dict]:
    values = {str(row["usage_day"]): row for row in rows or ()}
    buckets: list[dict] = []
    for offset in range(window - 1, -1, -1):
        day_key = (today - timedelta(days=offset)).isoformat()
        row = values.get(day_key)
        buckets.append(
            {
                "day": day_key,
                # 前端图表用 MM-DD 更紧凑，这里直接给出标签。
                "label": day_key[5:],
                "prompt_tokens": int(row["prompt_tokens"] or 0) if row else 0,
                "completion_tokens": int(row["completion_tokens"] or 0) if row else 0,
                "total_tokens": int(row["total_tokens"] or 0) if row else 0,
                "requests": int(row["requests"] or 0) if row else 0,
            }
        )
    return buckets


def _row_to_totals(row) -> dict:
    if row is None:
        return {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "requests": 0,
        }
    return {
        "prompt_tokens": int(row["prompt_tokens"] or 0),
        "completion_tokens": int(row["completion_tokens"] or 0),
        "total_tokens": int(row["total_tokens"] or 0),
        "requests": int(row["requests"] or 0),
    }


def _row_to_group(row) -> dict:
    return {
        "name": str(row["name"]),
        "prompt_tokens": int(row["prompt_tokens"] or 0),
        "completion_tokens": int(row["completion_tokens"] or 0),
        "total_tokens": int(row["total_tokens"] or 0),
        "requests": int(row["requests"] or 0),
    }


def _row_to_recent(row) -> dict:
    return {
        "recorded_at": str(row["recorded_at"]),
        "day": str(row["usage_day"]),
        "model": row["model"],
        "source": row["source"] or "unknown",
        "task_name": row["task_name"],
        "prompt_tokens": int(row["prompt_tokens"] or 0),
        "completion_tokens": int(row["completion_tokens"] or 0),
        "total_tokens": int(row["total_tokens"] or 0),
    }


# --------------------------------------------------------------------------- #
# 响应字段解析
# --------------------------------------------------------------------------- #


def _resolve_usage(response: Any) -> Any:
    if response is None:
        return None
    if isinstance(response, dict):
        return response.get("usage")
    return getattr(response, "usage", None)


def _resolve_model(response: Any) -> Optional[str]:
    if response is None:
        return None
    if isinstance(response, dict):
        return _clean_text(response.get("model"))
    return _clean_text(getattr(response, "model", None))


def _usage_field(usage: Any, *keys: str) -> Any:
    for key in keys:
        if isinstance(usage, dict):
            value = usage.get(key)
        else:
            value = getattr(usage, key, None)
        if value is not None:
            return value
    return None


def _extract_hour(recorded_at: Any) -> Optional[int]:
    text = str(recorded_at or "")
    if len(text) < 13 or text[10] not in ("T", " "):
        return None
    try:
        hour = int(text[11:13])
    except ValueError:
        return None
    if 0 <= hour < HOURS_PER_DAY:
        return hour
    return None


def _coerce_optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _coerce_non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _clean_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None