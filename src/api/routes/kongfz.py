"""孔夫子旧书网监控路由：账号配置栏、监控窗口、最新预览。"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import time
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.infrastructure.config.env_manager import env_manager

from src.api.dependencies import get_scheduler_service, get_task_service
from src.infrastructure.persistence.storage_names import (
    PLATFORM_KONGFZ,
    build_result_filename,
)
from src.kongfz.accounts import (
    build_state_path,
    default_state_path,
    describe_state_file,
    list_state_files,
    next_state_path,
    state_dir,
)
from src.kongfz.client import KongfzClient, load_cookie_header
from src.kongfz.parser import parse_kongfz_item
from src.services.monitor_run_report_service import load_monitor_run_reports
from src.services.result_storage_service import (
    annotate_new_flags,
    count_backfill_window_rows,
    count_new_window_rows,
    latest_crawl_time,
    load_monitor_window,
    query_result_records,
)
from src.services.scheduler_service import SchedulerService
from src.services.task_service import TaskService

router = APIRouter(prefix="/api/kongfz", tags=["kongfz"])

# 预览接口并发 / 频率限制：避免多次点击瞬间拉起多个浏览器实例导致资源打满。
_PREVIEW_SEMAPHORE = asyncio.Semaphore(
    max(1, int(os.getenv("KONGFZ_PREVIEW_MAX_CONCURRENT", "1") or 1))
)
_PREVIEW_LAST_START = 0.0
_PREVIEW_MIN_INTERVAL = max(1.0, float(os.getenv("KONGFZ_PREVIEW_MIN_INTERVAL", "3") or 3))

LOGIN_ENTRY = "kongfz_login.py"
LOG_DIR = "logs"
LOGIN_JOB_TTL_SECONDS = 3600

_login_jobs: dict[str, dict] = {}


class LoginRequest(BaseModel):
    name: Optional[str] = None
    state_file: Optional[str] = None
    timeout: int = Field(default=900, ge=60, le=3600)


class VerifyRequest(BaseModel):
    path: Optional[str] = None


class ImportRequest(BaseModel):
    content: str
    name: Optional[str] = None


class PlaywrightToggleRequest(BaseModel):
    enabled: bool


def _env_bool_value(key: str, default: bool = False) -> bool:
    raw = env_manager.get_value(key)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _normalize_bool_value(value: bool) -> str:
    return "true" if value else "false"


def _kongfz_tasks(tasks) -> list:
    return [
        task
        for task in tasks
        if str(getattr(task, "platform", "xianyu") or "xianyu").strip().lower()
        == PLATFORM_KONGFZ
    ]


def _login_log_path(job_id: str) -> str:
    return os.path.join(LOG_DIR, f"kongfz_login_{job_id}.log")


def _read_log_tail(path: str, limit: int = 4000) -> str:
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()[-limit:]
    except OSError:
        return ""


def _purge_old_jobs() -> None:
    now = datetime.now().timestamp()
    stale = [
        job_id
        for job_id, job in _login_jobs.items()
        if now - float(job.get("created_at") or now) > LOGIN_JOB_TTL_SECONDS
    ]
    for job_id in stale:
        _login_jobs.pop(job_id, None)


@router.get("/accounts")
async def get_accounts():
    """返回孔夫子登录态（账号栏）列表。"""
    accounts = list_state_files()
    return {
        "accounts": accounts,
        "state_dir": state_dir(),
        "default_path": next_state_path(),
    }


def _running_login_paths() -> set[str]:
    return {
        str(job.get("state_file") or "")
        for job in _login_jobs.values()
        if job.get("status") == "running"
    }


def _next_free_state_path(busy_paths: set[str]) -> str:
    """选择一个既不存在、也没有被其它登录进程占用的文件路径。"""
    index = 1
    while True:
        candidate = default_state_path(index)
        if candidate not in busy_paths and not os.path.exists(candidate):
            return candidate
        index += 1


@router.post("/accounts/login")
async def start_login(payload: LoginRequest):
    """启动扫码登录子进程（会在服务器本机弹出浏览器窗口）。"""
    _purge_old_jobs()
    os.makedirs(LOG_DIR, exist_ok=True)

    busy = _running_login_paths()
    if payload.state_file:
        state_path = payload.state_file
    elif payload.name:
        state_path = build_state_path(payload.name)
    else:
        state_path = _next_free_state_path(busy)

    # 同一文件不允许两个扫码进程同时写（否则互相覆盖，谁先成功谁被后写入的覆盖）
    if state_path in busy:
        raise HTTPException(
            status_code=409,
            detail=(
                f"该登录态文件正在被另一个扫码登录进程写入: {state_path}。"
                "请先完成或关闭那个登录窗口（或为本次登录指定另一个文件名）。"
            ),
        )

    os.makedirs(os.path.dirname(state_path) or ".", exist_ok=True)

    job_id = uuid.uuid4().hex[:12]
    log_path = _login_log_path(job_id)
    log_handle = open(log_path, "a", encoding="utf-8")

    command = [
        sys.executable,
        "-u",
        LOGIN_ENTRY,
        "--state-file",
        state_path,
        "--timeout",
        str(payload.timeout),
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=log_handle,
            stderr=log_handle,
            stdin=asyncio.subprocess.DEVNULL,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        )
    except Exception as exc:  # noqa: BLE001
        log_handle.close()
        raise HTTPException(status_code=500, detail=f"启动扫码登录失败: {exc}") from exc

    job = {
        "job_id": job_id,
        "state_file": state_path,
        "log_path": log_path,
        "pid": process.pid,
        "status": "running",
        "created_at": datetime.now().timestamp(),
        "finished_at": None,
        "returncode": None,
    }
    _login_jobs[job_id] = job

    async def _watch() -> None:
        returncode = await process.wait()
        job["returncode"] = returncode
        job["status"] = "done" if returncode == 0 else "failed"
        job["finished_at"] = datetime.now().timestamp()
        with contextlib.suppress(Exception):
            log_handle.close()

    asyncio.create_task(_watch())

    return {
        "message": "已在服务器本机打开孔夫子登录窗口，请扫码完成登录。",
        "job_id": job_id,
        "state_file": state_path,
        "pid": process.pid,
    }


@router.get("/accounts/login/{job_id}")
async def get_login_status(job_id: str):
    job = _login_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="登录任务不存在或已过期")
    return {
        "job_id": job_id,
        "status": job["status"],
        "returncode": job.get("returncode"),
        "state_file": job["state_file"],
        "output": _read_log_tail(job["log_path"]),
    }


@router.post("/accounts/import")
async def import_account(payload: ImportRequest):
    """粘贴导入登录态 JSON（Docker 等无法弹出浏览器的场景）。"""
    text = (payload.content or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="内容不能为空")

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"不是合法的 JSON: {exc.msg}") from exc

    if not isinstance(parsed, dict) or not isinstance(parsed.get("cookies"), list):
        raise HTTPException(
            status_code=400,
            detail="需要 Playwright storage_state 结构（至少包含 cookies 数组）",
        )

    cookies = [cookie for cookie in parsed["cookies"] if isinstance(cookie, dict)]
    kongfz_cookies = [
        cookie for cookie in cookies if "kongfz" in str(cookie.get("domain") or "")
    ]
    if not kongfz_cookies:
        raise HTTPException(status_code=400, detail="内容中没有 kongfz 域的 Cookie")

    state_path = build_state_path(payload.name) if payload.name else next_state_path()
    os.makedirs(os.path.dirname(state_path) or ".", exist_ok=True)
    try:
        with open(state_path, "w", encoding="utf-8") as handle:
            json.dump(parsed, handle, ensure_ascii=False, indent=2)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"写入登录态失败: {exc}") from exc

    return {
        "message": "孔夫子登录态已导入",
        "path": state_path,
        "cookie_count": len(kongfz_cookies),
    }


@router.post("/accounts/verify")
async def verify_account(payload: VerifyRequest):
    """校验登录态是否仍然有效（请求一个需要登录的列表接口）。"""
    path = payload.path or default_state_path()
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"登录态文件不存在: {path}")

    cookie_header = load_cookie_header(path)
    if not cookie_header:
        return {"ok": False, "message": "该文件不包含 kongfz 域 cookie", "path": path}

    async with KongfzClient(cookie_header=cookie_header, cookie_source=path) as client:
        ok, message = await client.verify_login_state()
    return {"ok": bool(ok), "message": message, "path": path}


@router.delete("/accounts/{name}")
async def delete_account(name: str):
    if not name or "/" in name or ".." in name:
        raise HTTPException(status_code=400, detail="非法的账号文件名")
    path = os.path.join(state_dir(), name)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="账号文件不存在")
    try:
        os.remove(path)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"删除失败: {exc}") from exc
    return {"message": f"账号文件 {name} 已删除"}


@router.get("/monitors")
async def get_monitors(
    service: TaskService = Depends(get_task_service),
    scheduler_service: SchedulerService = Depends(get_scheduler_service),
):
    """孔夫子监控任务概览（含监控窗口规模与最近一次运行结果）。"""
    tasks = _kongfz_tasks(await service.get_all_tasks())
    run_reports = load_monitor_run_reports(PLATFORM_KONGFZ)
    monitors = []
    for task in tasks:
        keyword = (task.keyword or "").strip()
        rows = await load_monitor_window(keyword, PLATFORM_KONGFZ) if keyword else []
        latest = latest_crawl_time(rows)
        report = run_reports.get(keyword) or {}
        next_run = (
            scheduler_service.get_next_run_time(task.id)
            if task.id is not None and scheduler_service is not None
            else None
        )
        accounts = describe_state_file(task.account_state_file) if task.account_state_file else None
        monitors.append(
            {
                "id": task.id,
                "task_name": task.task_name,
                "keyword": keyword,
                "enabled": task.enabled,
                "cron": task.cron,
                "latest_count": task.latest_count,
                "notify_all_new": task.notify_all_new,
                "notify_removal": task.notify_removal,
                "window_size": len(rows),
                "new_count": count_new_window_rows(rows),
                "backfill_count": count_backfill_window_rows(rows),
                # 最近一次真实运行（每轮都会更新，与是否发现新商品无关）
                "last_run_at": report.get("last_run_at"),
                "last_run_status": report.get("last_status"),
                "last_run_message": report.get("last_message"),
                "last_run_new_count": report.get("new_listing_count", 0),
                "last_run_backfill_count": report.get("backfill_count", 0),
                "last_run_removed_count": report.get("removed_count", 0),
                "next_run_at": next_run.isoformat() if next_run else None,
                # 兼容旧字段：这里是「窗口内最新商品的入库时间」
                "latest_crawl_time": latest or None,
                "latest_run_at": report.get("last_run_at") or (latest or None),
                "account_state_file": task.account_state_file,
                "account": accounts,
            }
        )
    return {"monitors": monitors, "total": len(monitors)}


@router.get("/items")
async def get_items(
    task_id: Optional[int] = None,
    keyword: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    service: TaskService = Depends(get_task_service),
):
    """读取某个孔夫子监控任务的窗口商品（按站点最新排序）。"""
    resolved_keyword = (keyword or "").strip()
    if not resolved_keyword and task_id is not None:
        task = await service.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="任务未找到")
        resolved_keyword = (task.keyword or "").strip()
    if not resolved_keyword:
        raise HTTPException(status_code=400, detail="需要提供 keyword 或 task_id")

    filename = build_result_filename(resolved_keyword, PLATFORM_KONGFZ)
    window_rows = await load_monitor_window(resolved_keyword, PLATFORM_KONGFZ)
    latest_run_at = latest_crawl_time(window_rows)

    total, items = await query_result_records(
        filename,
        ai_recommended_only=False,
        keyword_recommended_only=False,
        sort_by="window_rank",
        sort_order="asc",
        page=page,
        limit=limit,
    )
    new_flags = annotate_new_flags(items, latest_run_at)
    backfill_count = sum(1 for item in items if item.get("is_backfill"))

    return {
        "total_items": total,
        "page": page,
        "limit": limit,
        "keyword": resolved_keyword,
        "filename": filename,
        "window_size": len(window_rows),
        "latest_run_at": new_flags["latest_run_at"],
        "new_count": sum(1 for item in items if item.get("is_new_listing")),
        "backfill_count": backfill_count,
        "items": items,
    }


@router.get("/preview")
async def preview_latest(
    keyword: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=50),
    state_file: Optional[str] = None,
):
    """试抓预览：读取站点最新排序的前 N 条，不写入任何数据。"""
    # 并发 / 频率限制：同一时刻只允许有限个预览浏览器在跑，且两次预览之间留最小间隔
    if _PREVIEW_SEMAPHORE.locked():
        raise HTTPException(
            status_code=429,
            detail="已有预览正在执行，请稍候再试（避免同时拉起多个浏览器）",
        )
    async with _PREVIEW_SEMAPHORE:
        global _PREVIEW_LAST_START
        now = time.monotonic()
        if now - _PREVIEW_LAST_START < _PREVIEW_MIN_INTERVAL:
            raise HTTPException(
                status_code=429,
                detail=f"预览请求过于频繁，请至少间隔 {_PREVIEW_MIN_INTERVAL:.0f} 秒",
            )
        _PREVIEW_LAST_START = now

        resolved_state = state_file or None
        if not resolved_state:
            for entry in list_state_files():
                if entry.get("valid"):
                    resolved_state = str(entry["path"])
                    break

        cookie_header = load_cookie_header(resolved_state) if resolved_state else ""

        use_playwright = _env_bool_value("KONGFZ_USE_PLAYWRIGHT", False)

        if use_playwright:
            from src.kongfz.playwright_fetcher import fetch_items_with_playwright

            try:
                result = await fetch_items_with_playwright(
                    keyword=keyword.strip(),
                    scan_limit=max(limit, 10),
                    page_size=max(20, min(100, limit)),
                    cookie_header=cookie_header,
                    state_file=resolved_state,
                )
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=502, detail=str(exc)) from exc
        else:
            async with KongfzClient(
                cookie_header=cookie_header,
                cookie_source=resolved_state,
            ) as client:
                try:
                    result = await client.fetch_items(
                        keyword.strip(),
                        scan_limit=max(limit, 10),
                        page_size=max(20, min(100, limit)),
                    )
                except Exception as exc:  # noqa: BLE001
                    raise HTTPException(status_code=502, detail=str(exc)) from exc

        items = [
            parse_kongfz_item(item)
            for item in result.items
            if isinstance(item, dict) and not item.get("isSoldOut")
        ][:limit]

        return {
            "keyword": keyword.strip(),
            "scene": result.scene,
            "from_state_file": resolved_state,
            "total_found": result.total_found,
            "items": items,
            "playwright_used": use_playwright,
        }


@router.get("/config")
async def get_kongfz_config():
    """账号配置栏使用的前端配置项。"""
    return {
        "state_dir": state_dir(),
        "default_state_path": next_state_path(),
        "request_interval": os.getenv("KONGFZ_REQUEST_INTERVAL", "3"),
        "list_scene": os.getenv("KONGFZ_LIST_SCENE", ""),
        "login_entry": LOGIN_ENTRY,
        "accounts": list_state_files(),
        "playwright_enabled": _env_bool_value("KONGFZ_USE_PLAYWRIGHT", False),
        "playwright_headless": _env_bool_value("KONGFZ_PLAYWRIGHT_HEADLESS", True),
    }


@router.put("/config/playwright")
async def update_playwright_toggle(payload: PlaywrightToggleRequest):
    """切换 Playwright 备用获取模式开关。"""
    success = env_manager.update_values({
        "KONGFZ_USE_PLAYWRIGHT": _normalize_bool_value(payload.enabled),
    })
    if not success:
        raise HTTPException(status_code=500, detail="更新 Playwright 开关失败")
    return {
        "message": f"Playwright 备用模式已{'开启' if payload.enabled else '关闭'}",
        "playwright_enabled": payload.enabled,
    }