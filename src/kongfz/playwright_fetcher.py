"""孔夫子旧书网 Playwright 备用数据获取器。

当 httpx 方式触发反爬虫时，切换到 Playwright 浏览器方式获取数据。
原理：用真实浏览器打开搜索页，拦截 XHR/fetch 响应中的 JSON 数据，
从而绕过反爬检测，同时返回与 KongfzClient.fetch_items() 相同的结构。

环境变量：
- KONGFZ_USE_PLAYWRIGHT: 是否启用 Playwright 备用方式（true/false，默认 false）
- KONGFZ_PLAYWRIGHT_HEADLESS: 是否无头模式（true/false，默认 true）
- KONGFZ_PLAYWRIGHT_TIMEOUT: 浏览器操作超时秒数（默认 30）
"""
from __future__ import annotations

import asyncio
import json
import os
import random
from typing import Any, Optional
from urllib.parse import quote

from src.kongfz.client import (
    API_MAX_PAGE_SIZE,
    DEFAULT_UA,
    FetchResult,
    HOME_URL,
    ITEM_HOST,
    SCENE_KEYWORD,
    SCENE_LATEST,
    SEARCH_HOST,
    SEARCH_PAGE_URL,
    SORT_LATEST,
    VALID_SCENES,
    _env_bool,
    _env_float,
    _env_int,
    load_cookie_header,
)


def _is_playwright_enabled() -> bool:
    return _env_bool("KONGFZ_USE_PLAYWRIGHT", False)


def _pick_browser_channel() -> str:
    try:
        from src.scraper import _resolve_browser_channel

        return _resolve_browser_channel()
    except Exception:  # noqa: BLE001
        return "chrome"


async def fetch_items_with_playwright(
    *,
    keyword: str,
    scan_limit: int,
    page_size: int = API_MAX_PAGE_SIZE,
    cookie_header: str = "",
    state_file: Optional[str] = None,
    proxy: Optional[str] = None,
    scene: Optional[str] = None,
) -> FetchResult:
    """用 Playwright 浏览器打开孔夫子搜索页，拦截接口 JSON 数据。

    返回与 KongfzClient.fetch_items() 相同的 FetchResult 结构，
    以便 runner 无缝替换。
    """
    from playwright.async_api import async_playwright

    resolved_scene = _resolve_scene(scene, cookie_header)
    size = max(1, min(API_MAX_PAGE_SIZE, int(page_size or API_MAX_PAGE_SIZE)))
    target = max(1, int(scan_limit or size))

    headless = _env_bool("KONGFZ_PLAYWRIGHT_HEADLESS", True)
    nav_timeout = int(_env_float("KONGFZ_PLAYWRIGHT_TIMEOUT", 30.0) * 1000)

    # 拦截到的全部 item 列表
    intercepted_items: list[dict] = []
    total_found = 0
    reached_end = False

    # 孔夫子搜索接口 URL 前缀（scene 可能是 keyword 或 latest）
    api_url_prefix = f"{SEARCH_HOST}/pc-gw/search-web/client/pc/product/"

    async with async_playwright() as p:
        channel = _pick_browser_channel()
        try:
            browser = await p.chromium.launch(channel=channel, headless=headless)
        except Exception:  # noqa: BLE001
            browser = await p.chromium.launch(headless=headless)

        context_kwargs: dict[str, Any] = {
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
            "viewport": {"width": 1280, "height": 900},
            "user_agent": DEFAULT_UA,
        }

        # 加载登录态 storage_state（如果有）
        storage_state_path = state_file if state_file and os.path.exists(state_file) else None
        if storage_state_path:
            context_kwargs["storage_state"] = storage_state_path

        if proxy:
            context_kwargs["proxy"] = {"server": proxy}

        context = await browser.new_context(**context_kwargs)
        page = await context.new_page()

        # 设置接口拦截：监听孔夫子搜索列表 API 的响应
        async def _handle_response(response):
            nonlocal total_found
            url = response.url
            # 只关心孔夫子搜索列表接口
            if api_url_prefix not in url or "/list" not in url:
                return
            try:
                payload = await response.json()
            except Exception:  # noqa: BLE001
                return
            if not isinstance(payload, dict):
                return
            if payload.get("status") != 1:
                return
            data = payload.get("data") or {}
            item_response = data.get("itemResponse") or {}
            items = item_response.get("list") or []
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        intercepted_items.append(item)
            if isinstance(data.get("totalFound"), int):
                total_found = int(data["totalFound"])

        page.on("response", _handle_response)

        # 构建搜索页 URL
        search_url = _build_search_page_url(keyword, resolved_scene)
        print(f"   [孔夫子-Playwright] 正在打开搜索页: {search_url[:80]}...")

        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=nav_timeout)
        except Exception as exc:  # noqa: BLE001
            print(f"   [孔夫子-Playwright] 打开搜索页失败: {exc}")
            await context.close()
            await browser.close()
            return FetchResult(
                items=[],
                pages=0,
                scanned=0,
                total_found=0,
                reached_end=False,
                scene=resolved_scene,
            )

        # 等待页面加载和首批 XHR 完成
        await asyncio.sleep(random.uniform(2.0, 4.0))

        # 如果需要更多页，通过页面内滚动或翻页获取
        pages_fetched = 1
        while len(intercepted_items) < target and not reached_end:
            # 尝试滚动到底部触发懒加载或翻页
            try:
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(random.uniform(2.0, 4.0))

                # 检查是否有"下一页"按钮
                next_btn = await page.query_selector(
                    "a[class*='next']:not([class*='disabled']), "
                    "li[class*='next']:not([class*='disabled']) a, "
                    "button[class*='next']"
                )
                if next_btn:
                    await next_btn.click()
                    pages_fetched += 1
                    await asyncio.sleep(random.uniform(2.0, 4.0))
                else:
                    # 没有下一页，结束
                    reached_end = True
                    break

                # 安全上限：防止无限翻页
                if pages_fetched >= 10:
                    print(f"   [孔夫子-Playwright] 已翻 {pages_fetched} 页，达到安全上限")
                    break
            except Exception as exc:  # noqa: BLE001
                print(f"   [孔夫子-Playwright] 翻页失败: {exc}")
                break

        # 去重
        seen_ids: set[str] = set()
        unique_items: list[dict] = []
        for item in intercepted_items:
            item_id = str(item.get("itemId") or "").strip()
            if item_id and item_id in seen_ids:
                continue
            if item_id:
                seen_ids.add(item_id)
            unique_items.append(item)

        result = FetchResult(
            items=unique_items[:target],
            pages=pages_fetched,
            scanned=len(unique_items[:target]),
            total_found=total_found,
            reached_end=reached_end or len(unique_items) < target,
            scene=resolved_scene,
        )

        print(
            f"   [孔夫子-Playwright] {resolved_scene} 场景抓取 {result.pages} 页 / "
            f"{result.scanned} 条（站点命中 {result.total_found} 条）"
        )

        # 少量浏览动作伪装
        await _decoy_browse(page, unique_items)

        await context.close()
        await browser.close()

    return result


async def probe_item_with_playwright(
    item_id: str,
    shop_id: str | None = None,
    *,
    state_file: Optional[str] = None,
    proxy: Optional[str] = None,
) -> "ItemStatus":
    """用 Playwright 浏览器探测商品在售状态。"""
    from src.kongfz.client import ItemStatus, ON_SALE_MARKERS, OFF_SALE_MARKERS
    from playwright.async_api import async_playwright

    item_id = str(item_id or "").strip()
    if not item_id or not shop_id:
        return ItemStatus(
            item_id=item_id,
            url="",
            http_status=None,
            exists=False,
            on_sale=None,
            reason="missing_ids",
        )

    url = f"{ITEM_HOST}/{shop_id}/{item_id}"
    headless = _env_bool("KONGFZ_PLAYWRIGHT_HEADLESS", True)
    nav_timeout = int(_env_float("KONGFZ_PLAYWRIGHT_TIMEOUT", 30.0) * 1000)

    async with async_playwright() as p:
        channel = _pick_browser_channel()
        try:
            browser = await p.chromium.launch(channel=channel, headless=headless)
        except Exception:  # noqa: BLE001
            browser = await p.chromium.launch(headless=headless)

        context_kwargs: dict[str, Any] = {
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
            "viewport": {"width": 1280, "height": 900},
            "user_agent": DEFAULT_UA,
        }
        storage_state_path = state_file if state_file and os.path.exists(state_file) else None
        if storage_state_path:
            context_kwargs["storage_state"] = storage_state_path
        if proxy:
            context_kwargs["proxy"] = {"server": proxy}

        context = await browser.new_context(**context_kwargs)
        page = await context.new_page()

        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=nav_timeout)
            http_status = response.status if response else None
            text = await page.inner_text("body")
        except Exception as exc:  # noqa: BLE001
            await context.close()
            await browser.close()
            return ItemStatus(
                item_id=item_id,
                url=url,
                http_status=None,
                exists=False,
                on_sale=None,
                reason=f"navigation_error: {exc}",
            )

        if http_status in (404, 410):
            await context.close()
            await browser.close()
            return ItemStatus(
                item_id=item_id,
                url=url,
                http_status=http_status,
                exists=False,
                on_sale=False,
                reason="not_found",
            )

        if any(marker in text for marker in ON_SALE_MARKERS):
            result = ItemStatus(
                item_id=item_id, url=url, http_status=http_status,
                exists=True, on_sale=True, reason="on_sale",
            )
        elif any(marker in text for marker in OFF_SALE_MARKERS):
            result = ItemStatus(
                item_id=item_id, url=url, http_status=http_status,
                exists=True, on_sale=False, reason="off_sale",
            )
        else:
            result = ItemStatus(
                item_id=item_id, url=url, http_status=http_status,
                exists=True, on_sale=None, reason="unknown_marker",
            )

        await context.close()
        await browser.close()

    return result


async def verify_login_with_playwright(
    *,
    state_file: Optional[str] = None,
    proxy: Optional[str] = None,
) -> tuple[bool, str]:
    """用 Playwright 浏览器校验登录态。"""
    from playwright.async_api import async_playwright

    nav_timeout = int(_env_float("KONGFZ_PLAYWRIGHT_TIMEOUT", 30.0) * 1000)
    api_url_prefix = f"{SEARCH_HOST}/pc-gw/search-web/client/pc/product/"

    result_ok = False
    result_message = ""

    async with async_playwright() as p:
        channel = _pick_browser_channel()
        try:
            browser = await p.chromium.launch(channel=channel, headless=True)
        except Exception:  # noqa: BLE001
            browser = await p.chromium.launch(headless=True)

        context_kwargs: dict[str, Any] = {
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
            "viewport": {"width": 1280, "height": 900},
            "user_agent": DEFAULT_UA,
        }
        storage_state_path = state_file if state_file and os.path.exists(state_file) else None
        if storage_state_path:
            context_kwargs["storage_state"] = storage_state_path
        if proxy:
            context_kwargs["proxy"] = {"server": proxy}

        context = await browser.new_context(**context_kwargs)
        page = await context.new_page()

        async def _handle_response(response):
            nonlocal result_ok, result_message
            url = response.url
            if api_url_prefix not in url or "/list" not in url:
                return
            try:
                payload = await response.json()
            except Exception:  # noqa: BLE001
                return
            if not isinstance(payload, dict):
                return
            if payload.get("status") == 1:
                result_ok = True
                result_message = "登录态有效"
            else:
                message = str(payload.get("message") or "").strip()
                err_type = str(payload.get("errType") or "").strip()
                if err_type == "102" or "登录" in message:
                    result_ok = False
                    result_message = f"登录态已失效（errType={err_type}）"
                else:
                    result_ok = False
                    result_message = f"接口返回异常: {message}"

        page.on("response", _handle_response)

        search_url = _build_search_page_url("书", SCENE_KEYWORD)
        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=nav_timeout)
            await asyncio.sleep(3)
        except Exception as exc:  # noqa: BLE001
            result_ok = False
            result_message = f"页面导航失败: {exc}"

        if not result_message:
            result_ok = False
            result_message = "未拦截到搜索接口响应"

        await context.close()
        await browser.close()

    return result_ok, result_message


def _resolve_scene(scene: Optional[str], cookie_header: str) -> str:
    if scene and scene in VALID_SCENES:
        return scene
    override = os.getenv("KONGFZ_LIST_SCENE", "").strip().lower()
    if override in VALID_SCENES:
        return override
    return SCENE_KEYWORD if cookie_header else SCENE_LATEST


def _build_search_page_url(keyword: str, scene: str) -> str:
    if scene == SCENE_KEYWORD:
        params = f"?key={quote(keyword)}&sortType={SORT_LATEST}"
    else:
        params = f"?key={quote(keyword)}" if keyword else ""
    return f"{SEARCH_PAGE_URL}{params}"


async def _decoy_browse(page, items: list[dict]) -> None:
    """抓完后随机做 0~2 个浏览动作（伪装）。"""
    if not _env_bool("KONGFZ_DECOY_ENABLED", True):
        return
    budget = _env_int("KONGFZ_DECOY_MAX_ACTIONS", 2)
    if budget <= 0:
        return
    count = random.randint(0, min(budget, 2))
    if count <= 0:
        return

    from src.kongfz.client import _item_page_url, DECOY_KEYWORDS

    for _ in range(count):
        try:
            roll = random.random()
            if roll < 0.55 and items:
                url = _item_page_url(random.choice(items))
                if url:
                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            elif roll < 0.8:
                await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)
            else:
                decoy_kw = random.choice(DECOY_KEYWORDS)
                await page.goto(
                    f"{SEARCH_PAGE_URL}?key={quote(decoy_kw)}",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
            await asyncio.sleep(random.uniform(2.0, 5.0))
        except Exception:  # noqa: BLE001
            pass
    print(f"   [孔夫子-Playwright] 已完成 {count} 个浏览动作（伪装）")


__all__ = [
    "fetch_items_with_playwright",
    "probe_item_with_playwright",
    "verify_login_with_playwright",
    "_is_playwright_enabled",
]
