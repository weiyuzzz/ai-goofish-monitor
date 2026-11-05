"""孔夫子登录态（账号栏）管理。

登录态沿用与闲鱼一致的 Playwright storage_state 文件格式，
统一放在 `state/` 目录下，文件名以 `kongfz_` 前缀区分。
"""
from __future__ import annotations

import glob
import json
import os
from datetime import datetime
from typing import Optional

KONGFZ_STATE_PREFIX = "kongfz_"
KONGFZ_STATE_SUFFIX = ".json"


def state_dir() -> str:
    raw = os.getenv("ACCOUNT_STATE_DIR", "state").strip().strip('"').strip("'")
    return raw or "state"


def build_state_path(name: str) -> str:
    safe = str(name or "").strip() or "kongfz_1.json"
    if not safe.endswith(KONGFZ_STATE_SUFFIX):
        safe = f"{safe}{KONGFZ_STATE_SUFFIX}"
    if not safe.startswith(KONGFZ_STATE_PREFIX):
        safe = f"{KONGFZ_STATE_PREFIX}{safe}"
    return os.path.join(state_dir(), safe)


def default_state_path(index: int = 1) -> str:
    return build_state_path(f"{KONGFZ_STATE_PREFIX}{max(1, int(index))}")


def next_state_path() -> str:
    index = 1
    while os.path.exists(default_state_path(index)):
        index += 1
    return default_state_path(index)


def list_state_files() -> list[dict]:
    directory = state_dir()
    pattern = os.path.join(directory, f"{KONGFZ_STATE_PREFIX}*{KONGFZ_STATE_SUFFIX}")
    entries: list[dict] = []
    for path in sorted(glob.glob(pattern)):
        entries.append(describe_state_file(path))
    return entries


def describe_state_file(path: str) -> dict:
    name = os.path.basename(path)
    info = {
        "name": name,
        "path": path,
        "exists": os.path.exists(path),
        "size": 0,
        "updated_at": None,
        "cookie_count": 0,
        "valid": False,
        "error": "",
    }
    if not info["exists"]:
        info["error"] = "文件不存在"
        return info

    try:
        stat = os.stat(path)
        info["size"] = int(stat.st_size)
        info["updated_at"] = datetime.fromtimestamp(stat.st_mtime).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except OSError as exc:
        info["error"] = str(exc)

    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        cookies = payload.get("cookies") if isinstance(payload, dict) else None
        if isinstance(cookies, list):
            info["cookie_count"] = len(cookies)
            info["valid"] = any(
                "kongfz" in str(cookie.get("domain") or "")
                for cookie in cookies
                if isinstance(cookie, dict)
            )
        if not info["valid"] and not info["error"]:
            info["error"] = "未包含 kongfz 域 cookie"
    except Exception as exc:  # noqa: BLE001
        if not info["error"]:
            info["error"] = f"读取失败: {exc}"
    return info


def resolve_task_state_file(task_config: dict) -> Optional[str]:
    """解析任务使用的孔夫子登录态文件。"""
    explicit = str((task_config or {}).get("account_state_file") or "").strip()
    if explicit and os.path.exists(explicit):
        return explicit
    if explicit:
        print(f"[孔夫子] 指定的登录态文件不存在: {explicit}")

    for entry in list_state_files():
        if entry.get("valid"):
            return str(entry["path"])
    for entry in list_state_files():
        if entry.get("exists"):
            print(f"[孔夫子] 登录态文件 {entry['name']} 未包含 kongfz cookie，可能未登录")
            return str(entry["path"])
    return None