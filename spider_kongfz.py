"""孔夫子旧书网监控任务入口（对标 spider_v2.py）。

用法示例：
    python spider_kongfz.py
    python spider_kongfz.py --task-name "孔网-红楼梦"
    python spider_kongfz.py --debug-limit 3
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import re
import signal
import sys

from src.ai_handler import cleanup_task_images, send_ntfy_notification
from src.failure_guard import FailureGuard
from src.infrastructure.persistence.sqlite_task_repository import SqliteTaskRepository
from src.kongfz.client import KongfzBlockedError, KongfzLoginRequiredError
from src.kongfz.runner import run_kongfz_task
from src.services.monitor_run_report_service import (
    STATUS_FAILED,
    STATUS_SKIPPED,
    record_monitor_run_sync,
)

PLATFORM = "kongfz"
FAILURE_GUARD = FailureGuard()


def normalize_keywords(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_values = re.split(r"[\n,]+", value)
    elif isinstance(value, (list, tuple, set)):
        raw_values = list(value)
    else:
        raw_values = [value]

    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_values:
        text = str(item).strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(text)
    return normalized


def load_prompt_text(task: dict) -> str:
    """组合 AI prompt（与闲鱼一致：base + criteria）。"""
    decision_mode = str(task.get("decision_mode", "ai")).strip().lower()
    if decision_mode == "keyword" or task.get("notify_all_new", True):
        return ""

    base_file = task.get("ai_prompt_base_file")
    criteria_file = task.get("ai_prompt_criteria_file")
    if not base_file or not criteria_file:
        return ""
    try:
        with open(base_file, "r", encoding="utf-8") as handle:
            base_prompt = handle.read()
        with open(criteria_file, "r", encoding="utf-8") as handle:
            criteria_text = handle.read()
    except FileNotFoundError as exc:
        print(f"警告: 任务 '{task.get('task_name')}' 的 prompt 文件缺失: {exc}")
        return ""
    except Exception as exc:  # noqa: BLE001
        print(f"错误: 任务 '{task.get('task_name')}' 读取 prompt 失败: {exc}")
        return ""

    return base_prompt.replace("{{CRITERIA_SECTION}}", criteria_text)


async def load_task_configs(config_path: str | None, task_name: str | None) -> list[dict]:
    if config_path:
        if not os.path.exists(config_path):
            sys.exit(f"错误: 配置文件 '{config_path}' 不存在。")
        try:
            with open(config_path, "r", encoding="utf-8-sig") as handle:
                raw_tasks = json.load(handle)
        except (json.JSONDecodeError, IOError) as exc:
            sys.exit(f"错误: 读取或解析配置文件 '{config_path}' 失败: {exc}")
        tasks_config = []
        for task in raw_tasks:
            if not isinstance(task, dict):
                continue
            # 通过本入口传入的配置文件默认视为孔夫子任务。
            task.setdefault("platform", PLATFORM)
            tasks_config.append(task)
    else:
        repository = SqliteTaskRepository()
        tasks = await repository.find_all()
        tasks_config = [task.dict() for task in tasks]

    selected: list[dict] = []
    for task in tasks_config:
        if str(task.get("platform") or "xianyu").strip().lower() != PLATFORM:
            continue
        if task_name and task.get("task_name") != task_name:
            continue
        selected.append(task)

    for task in selected:
        task["keyword_rules"] = normalize_keywords(task.get("keyword_rules"))
        task["ai_prompt_text"] = load_prompt_text(task)

    return selected


async def _notify_failure(task_config: dict, reason: str) -> None:
    task_name = task_config.get("task_name", "未命名任务")
    try:
        await send_ntfy_notification(
            {
                "商品标题": f"[孔夫子任务失败] {task_name}",
                "当前售价": "N/A",
                "商品链接": "#",
                "来源": "孔夫子旧书网",
            },
            f"任务执行失败: {reason}",
            platform=PLATFORM,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"发送任务失败通知时出错: {exc}")


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="孔夫子旧书网商品监控脚本，按最新排序同步监控窗口。",
        epilog="""
使用示例:
  python spider_kongfz.py                          # 运行所有启用的孔夫子任务
  python spider_kongfz.py --task-name "孔网-红楼梦" # 只运行指定任务（调度器调用）
  python spider_kongfz.py --debug-limit 3          # 调试模式，每个任务最多处理 3 个新商品
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--debug-limit", type=int, default=0, help="每个任务最多处理 N 个新商品")
    parser.add_argument("--config", type=str, help="指定任务配置文件路径（传参时优先读取 JSON）")
    parser.add_argument("--task-name", type=str, help="只运行指定名称的单个任务")
    args = parser.parse_args()

    tasks_config = await load_task_configs(args.config, args.task_name)
    if args.task_name and not tasks_config:
        print(f"错误：未找到名为 '{args.task_name}' 的孔夫子任务。")
        return 1

    active = [task for task in tasks_config if task.get("enabled", True)]
    if args.task_name and tasks_config and not active:
        print(f"任务 '{args.task_name}' 已被禁用，跳过执行。")
        return 0

    if not active:
        print("没有需要执行的孔夫子任务，程序退出。")
        return 0

    print("\n--- 开始执行孔夫子监控任务 ---")
    for task in active:
        print(f"-> 任务 '{task.get('task_name')}' 已加入执行队列。")
    print("--------------------")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError, ValueError):
            loop.add_signal_handler(sig, stop_event.set)

    failures: dict[str, str] = {}

    async def _run_one(task_config: dict) -> int:
        task_name = task_config.get("task_name", "未命名任务")
        decision = FAILURE_GUARD.should_skip_start(
            task_name,
            cookie_path=task_config.get("account_state_file"),
        )
        if decision.skip:
            print(f"[FailureGuard] 跳过任务 '{task_name}'（连续失败 {decision.consecutive_failures}）")
            failures[task_name] = "任务处于暂停状态，已跳过执行"
            record_monitor_run_sync(
                platform=PLATFORM,
                keyword=str(task_config.get("keyword") or ""),
                task_name=task_name,
                status=STATUS_SKIPPED,
                message=f"任务暂停中，已跳过执行（连续失败 {decision.consecutive_failures} 次）",
            )
            return 0
        try:
            count = await run_kongfz_task(task_config, debug_limit=args.debug_limit)
            FAILURE_GUARD.record_success(task_name)
            return count
        except KongfzBlockedError as exc:
            # 人机验证墙只能由人工处理：立即暂停，避免每分钟反复撞墙把风控越撞越重
            print(f"任务 '{task_name}' 被人机验证拦截: {exc}")
            FAILURE_GUARD.record_failure(
                task_name,
                reason=str(exc),
                min_failures_to_pause=1,
            )
            failures[task_name] = f"人机验证拦截: {exc}"
            record_monitor_run_sync(
                platform=PLATFORM,
                keyword=str(task_config.get("keyword") or ""),
                task_name=task_name,
                status=STATUS_FAILED,
                message=f"被人机验证拦截，已暂停：{exc}",
            )
            await _notify_failure(task_config, str(exc))
            cleanup_task_images(task_name)
            return 0
        except KongfzLoginRequiredError as exc:
            print(f"任务 '{task_name}' 登录态失效: {exc}")
            FAILURE_GUARD.record_failure(task_name, reason=str(exc))
            failures[task_name] = f"登录态失效: {exc}"
            record_monitor_run_sync(
                platform=PLATFORM,
                keyword=str(task_config.get("keyword") or ""),
                task_name=task_name,
                status=STATUS_FAILED,
                message=f"登录态失效：{exc}",
            )
            await _notify_failure(task_config, str(exc))
            cleanup_task_images(task_name)
            return 0
        except Exception as exc:  # noqa: BLE001
            print(f"任务 '{task_name}' 执行失败: {type(exc).__name__}: {exc}")
            FAILURE_GUARD.record_failure(task_name, reason=f"{type(exc).__name__}: {exc}")
            failures[task_name] = f"{type(exc).__name__}: {exc}"
            record_monitor_run_sync(
                platform=PLATFORM,
                keyword=str(task_config.get("keyword") or ""),
                task_name=task_name,
                status=STATUS_FAILED,
                message=f"{type(exc).__name__}: {exc}",
            )
            await _notify_failure(task_config, f"{type(exc).__name__}: {exc}")
            cleanup_task_images(task_name)
            return 0

    tasks = [asyncio.create_task(_run_one(task)) for task in active]

    async def _shutdown_watcher() -> None:
        await stop_event.wait()
        print("\n收到终止信号，正在优雅退出，取消所有孔夫子任务...")
        for task in tasks:
            if not task.done():
                task.cancel()

    watcher = asyncio.create_task(_shutdown_watcher())
    try:
        results = await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher

    print("\n--- 孔夫子任务执行完毕 ---")
    has_failure = False
    for task, result in zip(active, results):
        name = task.get("task_name", "未命名任务")
        if isinstance(result, Exception):
            has_failure = True
            print(f"任务 '{name}' 因异常而终止: {result}")
            continue
        if name in failures:
            has_failure = True
            print(f"任务 '{name}' 执行失败: {failures[name]}")
            continue
        print(f"任务 '{name}' 正常结束，本次新上架 {result} 个商品（递补不通知）。")
    return 1 if has_failure else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))