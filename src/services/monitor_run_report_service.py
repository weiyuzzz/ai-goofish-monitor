"""监控任务的「运行报表」读写服务。

数据库里的 `爬取时间` 是商品**首次被发现**的时间，只在出现新商品时才会变化；
因此必须单独记录每一轮的真实运行时间与结果，否则界面无法区分
「任务没跑」和「跑了但本轮没有新商品」。

孔夫子任务由独立子进程执行，每轮结束（含失败）都会写一条报表。
"""
from __future__ import annotations

import asyncio
from datetime import datetime

from src.infrastructure.persistence.sqlite_bootstrap import bootstrap_sqlite_storage
from src.infrastructure.persistence.sqlite_connection import sqlite_connection
from src.infrastructure.persistence.storage_names import normalize_platform

STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"


def build_task_key(platform: str, keyword: str) -> str:
    return f"{normalize_platform(platform)}::{str(keyword or '').strip()}"


def _now_iso() -> str:
    return datetime.now().isoformat()


def record_monitor_run_sync(
    *,
    platform: str,
    keyword: str,
    task_name: str = "",
    status: str = STATUS_OK,
    message: str = "",
    window_size: int = 0,
    new_listing_count: int = 0,
    backfill_count: int = 0,
    removed_count: int = 0,
    run_at: str | None = None,
) -> None:
    """写入 / 覆盖一条运行报表（同步，供子进程直接调用）。"""
    resolved_platform = normalize_platform(platform)
    keyword = str(keyword or "").strip()
    if not keyword:
        return

    now = _now_iso()
    task_key = build_task_key(resolved_platform, keyword)
    try:
        bootstrap_sqlite_storage()
        with sqlite_connection() as conn:
            conn.execute(
                """
                INSERT INTO monitor_run_reports (
                    task_key, platform, keyword, task_name, last_run_at, last_status,
                    last_message, window_size, new_listing_count, backfill_count,
                    removed_count, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_key) DO UPDATE SET
                    task_name = excluded.task_name,
                    last_run_at = excluded.last_run_at,
                    last_status = excluded.last_status,
                    last_message = excluded.last_message,
                    window_size = excluded.window_size,
                    new_listing_count = excluded.new_listing_count,
                    backfill_count = excluded.backfill_count,
                    removed_count = excluded.removed_count,
                    updated_at = excluded.updated_at
                """,
                (
                    task_key,
                    resolved_platform,
                    keyword,
                    task_name or "",
                    run_at or now,
                    status,
                    (message or "")[:500],
                    int(window_size or 0),
                    int(new_listing_count or 0),
                    int(backfill_count or 0),
                    int(removed_count or 0),
                    now,
                ),
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        print(f"[运行报表] 写入失败（不影响任务本身）: {exc}")


async def record_monitor_run(**kwargs) -> None:
    await asyncio.to_thread(record_monitor_run_sync, **kwargs)


def load_monitor_run_reports(platform: str) -> dict[str, dict]:
    """按 keyword 返回某平台的运行报表。"""
    resolved_platform = normalize_platform(platform)
    bootstrap_sqlite_storage()
    with sqlite_connection() as conn:
        rows = conn.execute(
            """
            SELECT keyword, task_name, last_run_at, last_status, last_message,
                   window_size, new_listing_count, backfill_count, removed_count
            FROM monitor_run_reports
            WHERE platform = ?
            """,
            (resolved_platform,),
        ).fetchall()
    return {
        str(row["keyword"]): {
            "task_name": str(row["task_name"] or ""),
            "last_run_at": str(row["last_run_at"] or ""),
            "last_status": str(row["last_status"] or STATUS_OK),
            "last_message": str(row["last_message"] or ""),
            "window_size": int(row["window_size"] or 0),
            "new_listing_count": int(row["new_listing_count"] or 0),
            "backfill_count": int(row["backfill_count"] or 0),
            "removed_count": int(row["removed_count"] or 0),
        }
        for row in rows
    }