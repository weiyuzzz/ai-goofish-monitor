"""孔夫子监控任务执行器。

一次运行的流程：
1. 读取登录态（账号栏配置的 `state/kongfz_*.json`）；
2. 按「最新上架」倒序抓取，扫描量按窗口大小放大（预留已售商品的空位）；
3. 计算窗口差异：进窗口的新商品 → 分析 + 通知；被挤出/已售的商品 → 直接移除；
4. 把本地记录同步成「站点最新 N 条在售商品」。
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import random
from typing import Any, Optional

from src.ai_handler import (
    cleanup_task_images,
    download_all_images,
    get_ai_analysis,
    send_ntfy_notification,
)
from src.keyword_rule_engine import build_search_text, evaluate_keyword_rules
from src.kongfz.accounts import resolve_task_state_file
from src.kongfz.client import (
    KongfzBlockedError,
    KongfzClient,
    KongfzLoginRequiredError,
)
from src.kongfz.playwright_fetcher import (
    _is_playwright_enabled,
    fetch_items_with_playwright,
)
from src.kongfz.parser import (
    build_item_unique_key,
    build_notification_payload,
    build_record,
)
from src.kongfz.window import (
    ENTRY_NEW_LISTING,
    build_window_plan,
    describe_removal_reason,
    format_removal_reason,
    resolve_page_size,
    resolve_scan_limit,
    summarize_plan,
)
from src.services.monitor_run_report_service import (
    STATUS_EMPTY,
    STATUS_OK,
    record_monitor_run_sync,
)
from src.services.result_storage_service import (
    load_monitor_window,
    save_result_record,
    sync_monitor_window,
)

PLATFORM = "kongfz"
DEFAULT_LATEST_COUNT = 50
DEFAULT_ANALYSIS_CONCURRENCY = 2


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _analysis_concurrency(task_config: dict) -> int:
    default = _as_int(os.getenv("AI_ANALYSIS_CONCURRENCY"), DEFAULT_ANALYSIS_CONCURRENCY)
    return max(1, _as_int(task_config.get("analysis_concurrency"), default))


async def _build_analysis(
    *,
    record: dict,
    item: dict,
    task_config: dict,
    notify_all_new: bool,
    decision_mode: str,
    keyword_rules: list[str],
    prompt_text: str,
    analyze_images: bool,
) -> dict:
    """按任务配置产出 ai_analysis 结构（与闲鱼保持同构）。"""
    if notify_all_new:
        return {
            "is_recommended": True,
            "analysis_source": "new_item",
            "reason": "新上架商品",
            "keyword_hit_count": 0,
        }

    if decision_mode == "keyword":
        return evaluate_keyword_rules(list(keyword_rules or []), build_search_text(record))

    item_info = record.get("商品信息", {}) or {}
    image_paths: list[str] = []
    try:
        image_urls = item_info.get("商品图片列表") or []
        if analyze_images and image_urls:
            image_paths = await download_all_images(
                item_info.get("商品ID") or "",
                image_urls,
                task_config.get("task_name", "default"),
            )
        if not prompt_text:
            return {
                "is_recommended": False,
                "analysis_source": "ai",
                "reason": "任务未配置AI prompt，跳过分析。",
                "keyword_hit_count": 0,
            }
        ai_result = await get_ai_analysis(record, image_paths, prompt_text)
        if not ai_result:
            return {
                "is_recommended": False,
                "analysis_source": "ai",
                "reason": "AI 分析未返回结果。",
                "keyword_hit_count": 0,
            }
        ai_result.setdefault("analysis_source", "ai")
        ai_result.setdefault("keyword_hit_count", 0)
        return ai_result
    except Exception as exc:  # noqa: BLE001
        return {
            "is_recommended": False,
            "analysis_source": "ai",
            "reason": f"AI分析异常: {exc}",
            "keyword_hit_count": 0,
        }
    finally:
        for path in image_paths:
            try:
                if path and os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass


async def _save_silent_items(
    items: list[dict],
    *,
    keyword: str,
    task_name: str,
    entry_type: str | None,
    reason: str,
) -> int:
    """递补 / 基线商品：只入库，不分析、不通知。"""
    if not items:
        return 0

    saved = 0
    for item in items:
        record = build_record(item, keyword=keyword, task_name=task_name)
        record["入库方式"] = entry_type
        record["ai_analysis"] = {
            "is_recommended": False,
            "analysis_source": entry_type or "baseline",
            "reason": reason,
            "keyword_hit_count": 0,
        }
        try:
            if await save_result_record(record, keyword, PLATFORM):
                saved += 1
        except Exception as exc:  # noqa: BLE001
            print(f"   [孔夫子] 保存递补/基线商品失败: {exc}")
    return saved


async def _process_new_listing_items(
    *,
    new_listing_items: list[dict],
    task_config: dict,
    keyword: str,
    notify_all_new: bool,
    decision_mode: str,
    keyword_rules: list[str],
    prompt_text: str,
    analyze_images: bool,
) -> int:
    """真·新上架商品：按任务配置分析并通知。"""
    if not new_listing_items:
        return 0

    task_name = task_config.get("task_name", "未命名任务")
    semaphore = asyncio.Semaphore(_analysis_concurrency(task_config))
    processed = 0

    async def _handle(item: dict) -> None:
        nonlocal processed
        async with semaphore:
            record = build_record(item, keyword=keyword, task_name=task_name)
            record["入库方式"] = ENTRY_NEW_LISTING
            analysis = await _build_analysis(
                record=record,
                item=item,
                task_config=task_config,
                notify_all_new=notify_all_new,
                decision_mode=decision_mode,
                keyword_rules=keyword_rules,
                prompt_text=prompt_text,
                analyze_images=analyze_images,
            )
            record["ai_analysis"] = analysis

            if await save_result_record(record, keyword, PLATFORM):
                processed += 1

            if analysis.get("is_recommended"):
                try:
                    await send_ntfy_notification(
                        build_notification_payload(item),
                        analysis.get("reason", "无"),
                        platform=PLATFORM,
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"   [孔夫子] 发送通知失败: {exc}")

    await asyncio.gather(*[_handle(item) for item in new_listing_items])
    return processed


async def _start_jitter() -> None:
    """随机启动抖动：避免每一轮都在固定的第 0 秒整点开跑（很容易被识别）。"""
    try:
        budget = int(os.getenv("KONGFZ_START_JITTER_SECONDS", "20") or 0)
    except (TypeError, ValueError):
        budget = 20
    if budget <= 0:
        return
    delay = random.uniform(0, float(budget))
    print(f"[孔夫子] 启动抖动 {delay:.1f}s（上限 {budget}s，用于打散固定节奏）")
    await asyncio.sleep(delay)


async def run_kongfz_task(task_config: dict, debug_limit: int = 0) -> int:
    """执行单个孔夫子监控任务，返回本次新增商品数。"""
    keyword = str(task_config.get("keyword") or "").strip()
    task_name = task_config.get("task_name") or "未命名任务"
    if not keyword:
        raise ValueError(f"任务 '{task_name}' 未配置关键词")

    latest_count = max(1, _as_int(task_config.get("latest_count"), DEFAULT_LATEST_COUNT))
    notify_all_new = _as_bool(task_config.get("notify_all_new"), True)
    notify_removal = _as_bool(task_config.get("notify_removal"), False)
    decision_mode = str(task_config.get("decision_mode", "ai")).strip().lower()
    if decision_mode not in {"ai", "keyword"}:
        decision_mode = "ai"
    keyword_rules = list(task_config.get("keyword_rules") or [])
    prompt_text = str(task_config.get("ai_prompt_text") or "")
    analyze_images = _as_bool(task_config.get("analyze_images"), True)

    state_file = resolve_task_state_file(task_config)
    cookie_header = ""
    if state_file:
        from src.kongfz.client import load_cookie_header

        cookie_header = load_cookie_header(state_file)
        if cookie_header:
            print(f"[孔夫子] 使用登录态: {state_file}")
        else:
            print(f"[孔夫子] 登录态文件 {state_file} 中未找到 kongfz cookie，将按游客模式尝试")
    else:
        print("[孔夫子] 未配置登录态，将按游客模式尝试（容易触发登录墙，建议先在账号栏扫码登录）")

    # 游客会话 cookie 按「关键词 + 账号」隔离成独立文件，避免多任务同时写入
    # 同一个 data/kongfz_guest_cookies.json 造成竞态覆盖。
    guest_seed = f"{keyword}|{state_file or 'guest'}"
    guest_hash = hashlib.md5(guest_seed.encode("utf-8")).hexdigest()[:12]
    guest_cookie_file = os.path.join("data", f"kongfz_guest_{guest_hash}.json")

    scan_limit = resolve_scan_limit(latest_count)
    page_size = resolve_page_size(scan_limit)

    window_rows = await load_monitor_window(keyword, PLATFORM)
    existing_keys = {row["link_unique_key"] for row in window_rows}
    rows_by_key = {row["link_unique_key"]: row for row in window_rows}
    print(
        f"[孔夫子] 任务 '{task_name}' 关键词 '{keyword}'，"
        f"窗口 {latest_count} 条，本地已保存 {len(existing_keys)} 条"
    )

    use_playwright = _is_playwright_enabled()
    if use_playwright:
        print("[孔夫子] Playwright 备用模式已开启，使用浏览器方式获取数据")

    if use_playwright:
        # Playwright 备用模式：用真实浏览器获取数据
        await _start_jitter()
        try:
            fetch = await fetch_items_with_playwright(
                keyword=keyword,
                scan_limit=scan_limit,
                page_size=page_size,
                cookie_header=cookie_header,
                state_file=state_file,
                proxy=task_config.get("proxy") or None,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[孔夫子] Playwright 获取失败，回退到 httpx 方式: {exc}")
            use_playwright = False

    if not use_playwright:
        async with KongfzClient(
            cookie_header=cookie_header,
            cookie_source=state_file,
            proxy=task_config.get("proxy") or None,
            cookie_file=guest_cookie_file,
        ) as client:
            # 伪装层：随机启动抖动（打散固定节奏）→ 会话预热 → 抓取 → 少量浏览动作
            await _start_jitter()
            await client.warmup(keyword)

            fetch = await client.fetch_items(
                keyword,
                scan_limit=scan_limit,
                page_size=page_size,
            )

            # 伪装层：抓完后随机做几次"正常浏览"，让整轮请求看起来像人在逛
            await client.decoy_actions(fetch.items)

    scan_capped = bool(fetch.scanned >= scan_limit)
    plan = build_window_plan(
        fetch.items,
        existing_keys,
        latest_count,
        complete=bool(fetch.complete and fetch.items),
        scan_capped=scan_capped,
    )
    print(f"[孔夫子] {summarize_plan(plan, latest_count)}")
    if plan.is_baseline_run:
        print("[孔夫子] 首轮运行：按基线入库，不发送通知")

    notify_items = plan.new_listing_items
    if debug_limit and debug_limit > 0:
        notify_items = notify_items[:debug_limit]
        print(f"[孔夫子] 调试模式：最多处理 {debug_limit} 个新上架商品")

    # 递补（因在售商品下架而补位）与首轮基线：只入库，不通知。
    await _save_silent_items(
        plan.backfill_items,
        keyword=keyword,
        task_name=task_name,
        entry_type="backfill",
        reason="因在售商品下架而递补进入监控窗口（不通知）",
    )
    await _save_silent_items(
        plan.baseline_items,
        keyword=keyword,
        task_name=task_name,
        entry_type=None,
        reason="初始监控样本基线（不通知）",
    )

    processed = await _process_new_listing_items(
        new_listing_items=notify_items,
        task_config=task_config,
        keyword=keyword,
        notify_all_new=notify_all_new,
        decision_mode=decision_mode,
        keyword_rules=keyword_rules,
        prompt_text=prompt_text,
        analyze_images=analyze_images,
    )

    if not plan.complete:
        print("[孔夫子] 本次抓取不完整，跳过窗口同步（不会删除任何本地记录）")
    else:
        removed = plan.removed_keys
        sync_result = await sync_monitor_window(keyword, PLATFORM, plan.window_keys)
        removed = sync_result.get("removed") or []
        if removed:
            print(f"[孔夫子] 已移除 {len(removed)} 条记录（下架/售出/被新商品挤出窗口）")
        if notify_removal:
            await _notify_removed(removed, rows_by_key, plan.sold_keys, keyword, task_name)

    # 运行报表：记录「这一轮真的跑了」以及本轮结果，供界面展示
    effective_playwright = bool(use_playwright)
    if fetch.items:
        run_status = STATUS_OK
        empty_note = ""
    else:
        # 跑通了但站点返回 0 条：极可能是反爬 / 登录墙，不应显示成绿色「成功」
        run_status = STATUS_EMPTY
        empty_note = "本轮未抓到任何商品（可能触发反爬/登录墙，建议检查账号与开关）"
    run_message = (
        f"窗口 {len(plan.window_keys)} 条 · 新上架 {plan.new_listing_count} · "
        f"递补 {plan.backfill_count} · 移除 {len(plan.removed_keys)}"
        + (f" · {empty_note}" if empty_note else "")
    )
    record_monitor_run_sync(
        platform=PLATFORM,
        keyword=keyword,
        task_name=task_name,
        status=run_status,
        message=run_message,
        window_size=len(plan.window_keys),
        new_listing_count=plan.new_listing_count,
        backfill_count=plan.backfill_count,
        removed_count=len(plan.removed_keys),
        fetch_mode="playwright" if effective_playwright else "httpx",
    )

    cleanup_task_images(task_config.get("task_name", "default"))
    return processed


async def _notify_removed(
    removed_keys: list[str],
    rows_by_key: dict[str, dict],
    sold_keys: list[str],
    keyword: str,
    task_name: str,
) -> None:
    for link_key in removed_keys or []:
        row = rows_by_key.get(link_key) or {}
        reason = describe_removal_reason(link_key, sold_keys)
        payload = {
            "商品标题": row.get("title") or "（已移除商品）",
            "当前售价": row.get("price_display") or "-",
            "商品链接": link_key if str(link_key).startswith("http") else "#",
            "来源": "孔夫子旧书网",
            "监控关键词": keyword,
            "任务": task_name,
        }
        try:
            await send_ntfy_notification(
                payload,
                f"商品已移除监控：{format_removal_reason(reason)}",
                platform=PLATFORM,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"   [孔夫子] 发送移除通知失败: {exc}")


__all__ = [
    "KongfzBlockedError",
    "KongfzLoginRequiredError",
    "PLATFORM",
    "build_item_unique_key",
    "run_kongfz_task",
]