"""
结果数据的 SQLite 读写服务。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime

from src.infrastructure.persistence.sqlite_bootstrap import bootstrap_sqlite_storage
from src.infrastructure.persistence.sqlite_connection import sqlite_connection
from src.infrastructure.persistence.storage_names import (
    PLATFORM_XIANYU,
    build_result_filename,
    normalize_platform,
)
from src.services.price_history_service import parse_price_value
from src.services.result_blacklist_service import (
    match_blacklist_keywords,
    normalize_blacklist_keywords,
)


SORT_COLUMN_MAP = {
    "crawl_time": "crawl_time",
    "publish_time": "COALESCE(publish_time, '')",
    "price": "COALESCE(price, 0)",
    "keyword_hit_count": "keyword_hit_count",
    "window_rank": "COALESCE(window_rank, 999999)",
}

# 同一次抓取写入的记录，`爬取时间` 之间有秒级差异，用容差判定"本轮新增"。
NEW_ITEM_TOLERANCE_SECONDS = 120


def _parse_iso_datetime(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def latest_crawl_time(rows: list[dict]) -> str:
    """取窗口内最新的首次发现时间（即最近一次抓取的时间）。"""
    return max((str(row.get("crawl_time") or "") for row in rows), default="")


def _within_tolerance(latest: datetime, seen: datetime | None, tolerance_seconds: int) -> bool:
    return seen is not None and (latest - seen).total_seconds() <= max(0, int(tolerance_seconds))


def count_new_window_rows(
    rows: list[dict],
    *,
    tolerance_seconds: int = NEW_ITEM_TOLERANCE_SECONDS,
) -> int:
    """统计最近一次抓取中「真·新上架」的商品数（不含递补/基线）。"""
    latest = _parse_iso_datetime(latest_crawl_time(rows))
    if latest is None:
        return 0
    return sum(
        1
        for row in rows
        if str(row.get("entry_type") or "") == "new_listing"
        and _within_tolerance(latest, _parse_iso_datetime(row.get("crawl_time")), tolerance_seconds)
    )


def count_backfill_window_rows(
    rows: list[dict],
    *,
    tolerance_seconds: int = NEW_ITEM_TOLERANCE_SECONDS,
) -> int:
    """统计最近一次抓取中「因下架递补」进入窗口的商品数。"""
    latest = _parse_iso_datetime(latest_crawl_time(rows))
    if latest is None:
        return 0
    return sum(
        1
        for row in rows
        if str(row.get("entry_type") or "") == "backfill"
        and _within_tolerance(latest, _parse_iso_datetime(row.get("crawl_time")), tolerance_seconds)
    )


def annotate_new_flags(
    records: list[dict],
    latest_run_at: str | None,
    *,
    tolerance_seconds: int = NEW_ITEM_TOLERANCE_SECONDS,
) -> dict:
    """为窗口记录标注「本次新增」与「新增 / 递补」属性。

    记录的 `爬取时间` 是首次写入时间（重复商品走 INSERT OR IGNORE，不会被刷新），
    因此与最近一次抓取时间接近的记录就是本轮进入窗口的商品；
    再结合 `入库方式`（new_listing / backfill）区分真·新上架与递补。
    """
    latest = _parse_iso_datetime(latest_run_at) if latest_run_at else None
    new_count = 0

    for record in records:
        first_seen = str(record.get("爬取时间") or "")
        record["first_seen_at"] = first_seen
        is_new = False
        if latest is not None:
            is_new = _within_tolerance(
                latest, _parse_iso_datetime(first_seen), tolerance_seconds
            )
        entry_type = str(record.get("入库方式") or "")
        record["is_new"] = is_new
        record["is_new_listing"] = bool(is_new and entry_type == "new_listing")
        record["is_backfill"] = bool(is_new and entry_type == "backfill")
        if is_new:
            new_count += 1

    return {
        "new_count": new_count,
        "latest_run_at": latest_run_at or None,
    }


def _get_link_unique_key(link: str) -> str:
    return link.split("&", 1)[0]


def _fallback_unique_key(record: dict, item: dict) -> str:
    item_id = str(item.get("商品ID") or "").strip()
    if item_id:
        return f"item:{item_id}"
    digest = hashlib.sha1(
        json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"hash:{digest}"


def _parse_raw_record(raw_json: str, *, status: str | None = None) -> dict:
    record = json.loads(raw_json)
    if status is not None:
        record["_status"] = status
    return record


def _build_query_conditions(
    *,
    filename: str,
    ai_recommended_only: bool,
    keyword_recommended_only: bool,
) -> tuple[str, list]:
    conditions = ["result_filename = ?"]
    params: list = [filename]
    if ai_recommended_only:
        conditions.append("is_recommended = 1")
        conditions.append("analysis_source = ?")
        params.append("ai")
    if keyword_recommended_only:
        conditions.append("is_recommended = 1")
        conditions.append("analysis_source = ?")
        params.append("keyword")
    return " AND ".join(conditions), params


def _sort_expression(sort_by: str, sort_order: str) -> str:
    column = SORT_COLUMN_MAP.get(sort_by, SORT_COLUMN_MAP["crawl_time"])
    direction = "ASC" if sort_order == "asc" else "DESC"
    return f"(CASE WHEN status = 'active' THEN 0 ELSE 1 END), {column} {direction}, id {direction}"


def _load_blacklist_keywords_from_conn(conn, filename: str) -> list[str]:
    row = conn.execute(
        """
        SELECT blacklist_keywords_json
        FROM result_blacklist_rules
        WHERE result_filename = ?
        """,
        (filename,),
    ).fetchone()
    if row is None:
        return []
    try:
        payload = json.loads(row["blacklist_keywords_json"] or "[]")
    except json.JSONDecodeError:
        return []
    return normalize_blacklist_keywords(payload)


def _decorate_record_visibility(record: dict, status: str | None, blacklist_keywords: list[str]) -> dict:
    matched_keywords = match_blacklist_keywords(record, blacklist_keywords)
    hidden_reason = None
    if status == "expired":
        hidden_reason = "expired"
    elif status and status != "active":
        hidden_reason = "manual"
    elif matched_keywords:
        hidden_reason = "rule"

    record["_status"] = status or "active"
    record["_matched_blacklist_keywords"] = matched_keywords
    record["_hidden_reason"] = hidden_reason
    record["_effective_hidden"] = hidden_reason is not None
    return record


def _is_record_visible(record: dict) -> bool:
    return record.get("_effective_hidden") is not True


def _load_filtered_records_from_conn(
    conn,
    *,
    filename: str,
    ai_recommended_only: bool,
    keyword_recommended_only: bool,
    sort_by: str,
    sort_order: str,
    include_hidden: bool,
) -> list[dict]:
    where_clause, params = _build_query_conditions(
        filename=filename,
        ai_recommended_only=ai_recommended_only,
        keyword_recommended_only=keyword_recommended_only,
    )
    order_clause = _sort_expression(sort_by, sort_order)
    rows = conn.execute(
        f"""
        SELECT raw_json, status
        FROM result_items
        WHERE {where_clause}
        ORDER BY {order_clause}
        """,
        tuple(params),
    ).fetchall()
    blacklist_keywords = _load_blacklist_keywords_from_conn(conn, filename)

    records: list[dict] = []
    for row in rows:
        record = _parse_raw_record(str(row["raw_json"]), status=row["status"])
        decorated = _decorate_record_visibility(record, row["status"], blacklist_keywords)
        if include_hidden or _is_record_visible(decorated):
            records.append(decorated)
    return records


def _resolve_link_unique_key(record: dict, item: dict, link: str) -> str:
    """唯一键解析优先级：商品信息里的显式唯一键 > 链接 > 兜底哈希。"""
    explicit = str(item.get("唯一键") or "").strip()
    if explicit:
        return explicit
    if link:
        return _get_link_unique_key(link)
    return _fallback_unique_key(record, item)


async def save_result_record(record: dict, keyword: str, platform: str | None = None) -> bool:
    return await asyncio.to_thread(_save_result_record_sync, record, keyword, platform)


def _save_result_record_sync(record: dict, keyword: str, platform: str | None = None) -> bool:
    bootstrap_sqlite_storage()
    item = record.get("商品信息", {}) or {}
    analysis = record.get("ai_analysis", {}) or {}
    link = str(item.get("商品链接") or "")
    link_unique_key = _resolve_link_unique_key(record, item, link)
    resolved_platform = normalize_platform(platform or record.get("平台"))
    entry_type = str(record.get("入库方式") or "").strip() or None
    keyword_hit_count = analysis.get("keyword_hit_count", 0)
    try:
        keyword_hit_count = int(keyword_hit_count)
    except (TypeError, ValueError):
        keyword_hit_count = 0

    with sqlite_connection() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO result_items (
                result_filename, keyword, task_name, crawl_time, publish_time, price,
                price_display, item_id, title, link, link_unique_key, seller_nickname,
                is_recommended, analysis_source, keyword_hit_count, platform, entry_type, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                build_result_filename(keyword, resolved_platform),
                record.get("搜索关键字", keyword),
                record.get("任务名称", ""),
                record.get("爬取时间", ""),
                item.get("发布时间"),
                parse_price_value(item.get("当前售价")),
                item.get("当前售价"),
                item.get("商品ID"),
                item.get("商品标题"),
                link,
                link_unique_key,
                (record.get("卖家信息", {}) or {}).get("卖家昵称") or item.get("卖家昵称"),
                1 if analysis.get("is_recommended") else 0,
                analysis.get("analysis_source"),
                keyword_hit_count,
                resolved_platform,
                entry_type,
                json.dumps(record, ensure_ascii=False),
            ),
        )
        conn.commit()
    return True


def load_processed_link_keys(keyword: str, platform: str = PLATFORM_XIANYU) -> set[str]:
    bootstrap_sqlite_storage()
    filename = build_result_filename(keyword, platform)
    with sqlite_connection() as conn:
        rows = conn.execute(
            """
            SELECT link_unique_key FROM result_items
            WHERE result_filename = ? AND platform = ?
            """,
            (filename, normalize_platform(platform)),
        ).fetchall()
    return {str(row["link_unique_key"]) for row in rows if row["link_unique_key"]}


async def load_monitor_window(keyword: str, platform: str = PLATFORM_XIANYU) -> list[dict]:
    """返回监控窗口内的商品（按 window_rank 升序）。"""
    return await asyncio.to_thread(_load_monitor_window_sync, keyword, platform)


def _load_monitor_window_sync(keyword: str, platform: str) -> list[dict]:
    bootstrap_sqlite_storage()
    filename = build_result_filename(keyword, platform)
    with sqlite_connection() as conn:
        rows = conn.execute(
            """
            SELECT link_unique_key, item_id, title, price_display, crawl_time,
                   window_rank, status, entry_type
            FROM result_items
            WHERE result_filename = ? AND platform = ?
            ORDER BY (window_rank IS NULL), window_rank ASC, id ASC
            """,
            (filename, normalize_platform(platform)),
        ).fetchall()
    return [
        {
            "link_unique_key": str(row["link_unique_key"]),
            "item_id": str(row["item_id"] or ""),
            "title": str(row["title"] or ""),
            "price_display": str(row["price_display"] or ""),
            "crawl_time": str(row["crawl_time"] or ""),
            "window_rank": row["window_rank"],
            "status": str(row["status"] or "active"),
            "entry_type": row["entry_type"],
        }
        for row in rows
    ]


async def sync_monitor_window(
    keyword: str,
    platform: str,
    ranked_keys: list[str],
) -> dict:
    """把监控窗口替换为 `ranked_keys` 指定的集合。

    - `ranked_keys` 按站点最新排序给出，索引即 window_rank；
    - 不在新窗口中的历史记录会被**直接删除**（商品已下架/已售/被更新商品挤出窗口）；
    - 已在窗口中的记录仅回写 window_rank，不更新其它字段。
    """
    return await asyncio.to_thread(
        _sync_monitor_window_sync, keyword, platform, ranked_keys
    )


def _sync_monitor_window_sync(
    keyword: str,
    platform: str,
    ranked_keys: list[str],
) -> dict:
    bootstrap_sqlite_storage()
    resolved_platform = normalize_platform(platform)
    filename = build_result_filename(keyword, resolved_platform)
    ordered_keys = [key for key in ranked_keys if key]
    incoming = set(ordered_keys)

    with sqlite_connection() as conn:
        rows = conn.execute(
            """
            SELECT link_unique_key FROM result_items
            WHERE result_filename = ? AND platform = ?
            """,
            (filename, resolved_platform),
        ).fetchall()
        existing = {str(row["link_unique_key"]) for row in rows if row["link_unique_key"]}

        removed = sorted(existing - incoming)
        if removed:
            conn.executemany(
                """
                DELETE FROM result_items
                WHERE result_filename = ? AND platform = ? AND link_unique_key = ?
                """,
                [(filename, resolved_platform, key) for key in removed],
            )

        for rank, key in enumerate(ordered_keys):
            conn.execute(
                """
                UPDATE result_items SET window_rank = ?
                WHERE result_filename = ? AND platform = ? AND link_unique_key = ?
                """,
                (rank, filename, resolved_platform, key),
            )
        conn.commit()

    return {
        "removed": removed,
        "removed_count": len(removed),
        "window_size": len(ordered_keys),
        "added_count": len(incoming - existing),
        "kept_count": len(incoming & existing),
    }


async def list_result_filenames() -> list[str]:
    return await asyncio.to_thread(_list_result_filenames_sync)


def _list_result_filenames_sync() -> list[str]:
    bootstrap_sqlite_storage()
    with sqlite_connection() as conn:
        rows = conn.execute(
            """
            SELECT result_filename, MAX(crawl_time) AS latest_crawl_time
            FROM result_items
            GROUP BY result_filename
            ORDER BY latest_crawl_time DESC, result_filename DESC
            """
        ).fetchall()
    return [str(row["result_filename"]) for row in rows]


async def result_file_exists(filename: str) -> bool:
    return await asyncio.to_thread(_result_file_exists_sync, filename)


def _result_file_exists_sync(filename: str) -> bool:
    bootstrap_sqlite_storage()
    with sqlite_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM result_items WHERE result_filename = ? LIMIT 1",
            (filename,),
        ).fetchone()
    return row is not None


async def delete_result_file_records(filename: str) -> int:
    return await asyncio.to_thread(_delete_result_file_records_sync, filename)


def _delete_result_file_records_sync(filename: str) -> int:
    bootstrap_sqlite_storage()
    with sqlite_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM result_items WHERE result_filename = ?",
            (filename,),
        )
        conn.commit()
    return int(cursor.rowcount or 0)


async def query_result_records(
    filename: str,
    *,
    ai_recommended_only: bool,
    keyword_recommended_only: bool,
    sort_by: str,
    sort_order: str,
    page: int,
    limit: int,
    include_hidden: bool = False,
) -> tuple[int, list[dict]]:
    return await asyncio.to_thread(
        _query_result_records_sync,
        filename,
        ai_recommended_only,
        keyword_recommended_only,
        sort_by,
        sort_order,
        page,
        limit,
        include_hidden,
    )


def _query_result_records_sync(
    filename: str,
    ai_recommended_only: bool,
    keyword_recommended_only: bool,
    sort_by: str,
    sort_order: str,
    page: int,
    limit: int,
    include_hidden: bool,
) -> tuple[int, list[dict]]:
    bootstrap_sqlite_storage()
    offset = max(page - 1, 0) * limit
    with sqlite_connection() as conn:
        records = _load_filtered_records_from_conn(
            conn,
            filename=filename,
            ai_recommended_only=ai_recommended_only,
            keyword_recommended_only=keyword_recommended_only,
            sort_by=sort_by,
            sort_order=sort_order,
            include_hidden=include_hidden,
        )
    total = len(records)
    return total, records[offset: offset + limit]


async def load_all_result_records(
    filename: str,
    *,
    ai_recommended_only: bool,
    keyword_recommended_only: bool,
    sort_by: str,
    sort_order: str,
    include_hidden: bool = False,
) -> list[dict]:
    return await asyncio.to_thread(
        _load_all_result_records_sync,
        filename,
        ai_recommended_only,
        keyword_recommended_only,
        sort_by,
        sort_order,
        include_hidden,
    )


def _load_all_result_records_sync(
    filename: str,
    ai_recommended_only: bool,
    keyword_recommended_only: bool,
    sort_by: str,
    sort_order: str,
    include_hidden: bool,
) -> list[dict]:
    bootstrap_sqlite_storage()
    with sqlite_connection() as conn:
        return _load_filtered_records_from_conn(
            conn,
            filename=filename,
            ai_recommended_only=ai_recommended_only,
            keyword_recommended_only=keyword_recommended_only,
            sort_by=sort_by,
            sort_order=sort_order,
            include_hidden=include_hidden,
        )


async def build_result_ndjson(filename: str) -> str:
    return await asyncio.to_thread(_build_result_ndjson_sync, filename)


def _build_result_ndjson_sync(filename: str) -> str:
    bootstrap_sqlite_storage()
    with sqlite_connection() as conn:
        rows = conn.execute(
            "SELECT raw_json FROM result_items WHERE result_filename = ? ORDER BY id ASC",
            (filename,),
        ).fetchall()
    return "\n".join(str(row["raw_json"]) for row in rows)


async def load_result_summary(filename: str) -> dict | None:
    return await asyncio.to_thread(_load_result_summary_sync, filename)


def _load_result_summary_sync(filename: str) -> dict | None:
    bootstrap_sqlite_storage()
    with sqlite_connection() as conn:
        visible_records = _load_filtered_records_from_conn(
            conn,
            filename=filename,
            ai_recommended_only=False,
            keyword_recommended_only=False,
            sort_by="crawl_time",
            sort_order="desc",
            include_hidden=False,
        )
    if not visible_records:
        return None

    recommended_records = [
        record
        for record in visible_records
        if (record.get("ai_analysis", {}) or {}).get("is_recommended") is True
    ]
    ai_recommended_items = 0
    keyword_recommended_items = 0
    for record in recommended_records:
        source = (record.get("ai_analysis", {}) or {}).get("analysis_source")
        if source == "ai":
            ai_recommended_items += 1
        elif source == "keyword":
            keyword_recommended_items += 1

    return {
        "total_items": len(visible_records),
        "recommended_items": len(recommended_records),
        "ai_recommended_items": ai_recommended_items,
        "keyword_recommended_items": keyword_recommended_items,
        "latest_crawl_time": visible_records[0].get("爬取时间"),
        "latest_record": visible_records[0],
        "latest_recommendation": recommended_records[0] if recommended_records else None,
    }


async def update_item_status(filename: str, item_id: str, status: str) -> bool:
    valid = {"active", "hidden", "expired"}
    if status not in valid:
        raise ValueError(f"status must be one of {valid}")
    return await asyncio.to_thread(_update_item_status_sync, filename, item_id, status)


def _update_item_status_sync(filename: str, item_id: str, status: str) -> bool:
    bootstrap_sqlite_storage()
    with sqlite_connection() as conn:
        cursor = conn.execute(
            "UPDATE result_items SET status = ? WHERE result_filename = ? AND item_id = ?",
            (status, filename, item_id),
        )
        conn.commit()
        return cursor.rowcount > 0


async def load_result_blacklist_keywords(filename: str) -> list[str]:
    return await asyncio.to_thread(_load_result_blacklist_keywords_sync, filename)


def _load_result_blacklist_keywords_sync(filename: str) -> list[str]:
    bootstrap_sqlite_storage()
    with sqlite_connection() as conn:
        return _load_blacklist_keywords_from_conn(conn, filename)


async def save_result_blacklist_keywords(filename: str, keywords: list[str]) -> list[str]:
    return await asyncio.to_thread(_save_result_blacklist_keywords_sync, filename, keywords)


def _save_result_blacklist_keywords_sync(filename: str, keywords: list[str]) -> list[str]:
    bootstrap_sqlite_storage()
    normalized_keywords = normalize_blacklist_keywords(keywords)
    now = datetime.now().isoformat()
    with sqlite_connection() as conn:
        conn.execute(
            """
            INSERT INTO result_blacklist_rules (
                result_filename, blacklist_keywords_json, updated_at
            ) VALUES (?, ?, ?)
            ON CONFLICT(result_filename) DO UPDATE SET
                blacklist_keywords_json = excluded.blacklist_keywords_json,
                updated_at = excluded.updated_at
            """,
            (filename, json.dumps(normalized_keywords, ensure_ascii=False), now),
        )
        conn.commit()
    return normalized_keywords


def load_visible_result_item_ids(filename: str) -> set[str]:
    bootstrap_sqlite_storage()
    with sqlite_connection() as conn:
        visible_records = _load_filtered_records_from_conn(
            conn,
            filename=filename,
            ai_recommended_only=False,
            keyword_recommended_only=False,
            sort_by="crawl_time",
            sort_order="desc",
            include_hidden=False,
        )
    item_ids: set[str] = set()
    for record in visible_records:
        product = record.get("商品信息", {}) or {}
        item_id = str(product.get("商品ID") or "").strip()
        if item_id:
            item_ids.add(item_id)
    return item_ids
