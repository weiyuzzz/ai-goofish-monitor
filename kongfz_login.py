"""孔夫子旧书网登录态获取工具（扫码登录 → 导出 state/kongfz_*.json）。

用途：在「孔夫子监控 → 账号配置」中点「扫码登录」会调用本脚本，
打开一个真实浏览器窗口，用孔网 App / 微信 / QQ 扫码登录后，
脚本把 Playwright storage_state 写入 state/kongfz_*.json。

用法:
    python kongfz_login.py                        # 默认保存到 state/kongfz_1.json（自动找空位）
    python kongfz_login.py --name kongfz_2        # 保存到 state/kongfz_2.json
    python kongfz_login.py --state-file state/kongfz_1.json
    python kongfz_login.py --timeout 900          # 等待扫码的秒数
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

from playwright.async_api import async_playwright

from src.kongfz.accounts import build_state_path, next_state_path, state_dir

HOME_URL = "https://www.kongfz.com/"
SEARCH_API = "https://search.kongfz.com/pc-gw/search-web/client/pc/product/keyword/list"
LOGGED_IN_PAGE_MARKERS = (
    "退出登录",
    "个人中心",
    "我的订单",
    "卖家中心",
    "我的收藏",
)

# 接口探测状态
PROBE_OK = "ok"
PROBE_NOT_LOGGED_IN = "not_logged_in"
PROBE_NEEDS_VERIFY = "needs_verify"
PROBE_ERROR = "error"

# 站点的「人机验证」文案标记（出现这些说明被判定为自动请求，需人工过验证）
VERIFY_HINT_MARKERS = ("验证", "自动请求", "计算机软件", "人机")

# 需要在登录窗口里打开、以便用户过验证码的页面
VERIFY_PAGE_URL = "https://search.kongfz.com/product_result/?key=%E7%BA%A2%E6%A5%BC%E6%A2%A6&sortType=3"

# 页面已登录但接口持续异常（网络/风控页）时，先落盘避免白等
FALLBACK_SAVE_SECONDS = 45


def _pick_channel() -> str:
    """优先 Chrome，其次 Edge（与 scraper 的 channel 逻辑保持一致）。"""
    try:
        from src.scraper import _resolve_browser_channel

        return _resolve_browser_channel()
    except Exception:  # noqa: BLE001
        return "chrome"


def _pick_kongfz_cookies(cookies: list[dict]) -> dict:
    picked: dict[str, str] = {}
    for cookie in cookies:
        domain = str(cookie.get("domain") or "")
        if domain and "kongfz" not in domain:
            continue
        name = cookie.get("name")
        value = cookie.get("value")
        if name and value:
            picked[str(name)] = str(value)
    return picked


async def _page_looks_logged_in(page) -> bool:
    try:
        text = await page.inner_text("body")
    except Exception:  # noqa: BLE001
        return False
    return any(marker in text for marker in LOGGED_IN_PAGE_MARKERS)


def _classify_probe_payload(body: str, http_status: int = 200) -> tuple[str, str]:
    """把接口响应归类为 ok / not_logged_in / error，并给出可读原因。"""
    try:
        payload = json.loads(body)
    except Exception:  # noqa: BLE001
        snippet = " ".join(str(body).strip().split())[:120]
        return PROBE_ERROR, f"接口返回非 JSON（HTTP {http_status}）: {snippet}"

    if not isinstance(payload, dict):
        return PROBE_ERROR, f"接口返回结构异常（HTTP {http_status}）"

    if int(payload.get("status") or 0) == 1:
        return PROBE_OK, "接口已认可登录态"

    err_type = str(payload.get("errType") or "")
    message = str(payload.get("message") or "").strip()
    if any(marker in message for marker in VERIFY_HINT_MARKERS):
        return (
            PROBE_NEEDS_VERIFY,
            f"站点要求人机验证（errType={err_type or 'N/A'} message={message}）",
        )
    if err_type == "102" or "登录" in message:
        return PROBE_NOT_LOGGED_IN, f"接口仍要求登录（errType={err_type or 'N/A'} message={message}）"
    return PROBE_ERROR, f"接口返回异常（errType={err_type or 'N/A'} message={message}）"


async def _probe_search_api(context) -> tuple[str, str]:
    """用「需要登录」的搜索接口判断登录态，并返回可直接打印的原因。

    这是唯一权威判据：页面标记可能误判（未登录时的隐藏菜单也会包含字样），
    而监控任务真正依赖的就是这个接口。
    """
    try:
        response = await context.request.get(
            SEARCH_API,
            params={"keyword": "书", "page": 1, "size": 1, "sortType": 3},
            headers={
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://search.kongfz.com/product_result/",
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=20000,
        )
        body = await response.text()
    except Exception as exc:  # noqa: BLE001
        return PROBE_ERROR, f"探测请求失败: {type(exc).__name__}: {exc}"

    return _classify_probe_payload(body, response.status)


async def main() -> int:
    parser = argparse.ArgumentParser(description="孔夫子旧书网登录态导出工具")
    parser.add_argument("--name", default="", help="账号文件名（不含 .json，默认自动取空位）")
    parser.add_argument("--state-file", default="", help="直接指定保存路径")
    parser.add_argument("--timeout", type=int, default=900, help="等待扫码登录的秒数")
    parser.add_argument("--headless", action="store_true", help="无头模式（无法扫码，仅用于自检）")
    args = parser.parse_args()

    os.makedirs(state_dir(), exist_ok=True)
    if args.state_file:
        state_path = args.state_file
    elif args.name:
        state_path = build_state_path(args.name)
    else:
        state_path = next_state_path()

    channel = _pick_channel()
    print(f"[孔夫子登录] 保存目标: {state_path}")
    print(f"[孔夫子登录] 浏览器通道: {channel}")

    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(channel=channel, headless=args.headless)
        except Exception as exc:  # noqa: BLE001
            print(f"[孔夫子登录] channel={channel} 启动失败（{exc}），回退到内置 Chromium")
            browser = await p.chromium.launch(headless=args.headless)

        context = await browser.new_context(
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()
        await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)

        print("[孔夫子登录] 已打开 www.kongfz.com。")
        print("[孔夫子登录] 请点击「登录/注册」，用微信 / QQ / 孔网账号扫码登录。")
        print("[孔夫子登录] 登录成功后脚本会自动检测并保存登录态，无需其它操作。")

        deadline = time.time() + args.timeout
        started_at = time.time()
        saved = False
        last_hint = ""
        error_since: float | None = None
        probe_count = 0
        verify_page_opened = False
        verify_printed = False

        while time.time() < deadline:
            probe_count += 1
            try:
                cookies = await context.cookies()
            except Exception as exc:  # noqa: BLE001
                print(f"[孔夫子登录] 浏览器似乎已关闭: {exc}")
                return 2

            page_ok = await _page_looks_logged_in(page)
            probe_state, probe_detail = await _probe_search_api(context)
            elapsed = int(time.time() - started_at)

            # 接口认可 = 登录成功，立即落盘（不再要求页面标记同时成立）
            if probe_state == PROBE_OK:
                await context.storage_state(path=state_path)
                saved = True
                picked = _pick_kongfz_cookies(cookies)
                print(
                    f"[孔夫子登录] 登录态已生效并保存: {state_path} "
                    f"(cookies={len(cookies)}，其中 kongfz 域 {len(picked)} 个)"
                )
                break

            if probe_state == PROBE_NEEDS_VERIFY:
                # 站点判定为自动请求：把搜索页推到用户眼前，人机验证只能由人来过。
                if not verify_page_opened and elapsed >= 4:
                    verify_page_opened = True
                    try:
                        await page.goto(
                            VERIFY_PAGE_URL,
                            wait_until="domcontentloaded",
                            timeout=60000,
                        )
                        print(
                            "[孔夫子登录] 已在浏览器窗口打开孔网搜索页。"
                            "如果出现【人机验证 / 滑块 / 点选验证】，请直接在窗口里完成；"
                            "通过后脚本会自动继续并保存。"
                        )
                    except Exception as exc:  # noqa: BLE001
                        print(f"[孔夫子登录] 打开搜索页失败（不影响继续等待）: {exc}")
                if not verify_printed:
                    verify_printed = True
                    print(
                        "[孔夫子登录] 站点要求人机验证（错误信息：「类似计算机软件的自动请求」）。"
                        "这不是登录失败——请在打开的浏览器窗口里手动完成验证。"
                    )
                hint = (
                    f"[孔夫子登录] 等待人工验证 已等待 {elapsed}s · "
                    f"页面标记={page_ok} · 接口={probe_state} · {probe_detail}"
                )
            else:
                hint = (
                    f"[孔夫子登录] 等待中 已等待 {elapsed}s · 页面标记={page_ok} · "
                    f"接口探测={probe_state} · {probe_detail}"
                )

            if probe_state == PROBE_ERROR:
                error_since = error_since or time.time()
            else:
                error_since = None

            if hint != last_hint:
                print(hint, flush=True)
                last_hint = hint

            # 兜底：页面显示已登录、但接口持续异常（网络/风控页）时先保存，
            # 避免用户扫码成功却因为接口临时不可用而白等一整轮。
            if (
                page_ok
                and probe_state == PROBE_ERROR
                and error_since is not None
                and time.time() - error_since >= FALLBACK_SAVE_SECONDS
            ):
                await context.storage_state(path=state_path)
                saved = True
                print(
                    f"[孔夫子登录] 接口探测持续异常（{probe_detail}），但页面已登录，"
                    f"已先保存登录态: {state_path}"
                )
                print("[孔夫子登录] 请稍后用「校验有效性」确认；若仍失效可重新扫码。")
                break

            # 低频探测：前 3 次用 3 秒，之后每 10 秒一次。
            # 高频 XHR 会被站点判成自动请求，反而把自己关在验证墙外面。
            await asyncio.sleep(3 if probe_count <= 3 else 10)

        if not saved:
            print(f"[孔夫子登录] 超时（{args.timeout}s）未确认登录，已放弃。")
            print("[孔夫子登录] 排查提示（请看上面的「接口探测」原因）：")
            print("  · errType=102：站点或账号仍要求登录 → 可能是风控未解除、账号被限制，或扫码未真正完成")
            print("  · 非 JSON / 探测请求失败：网络或代理问题，可检查能否直连 search.kongfz.com")
            await context.close()
            await browser.close()
            return 1

        try:
            with open(state_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            print(
                f"[孔夫子登录] 保存校验通过: cookies={len(payload.get('cookies', []))}, "
                f"origins={len(payload.get('origins', []))}"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[孔夫子登录] 保存校验失败: {exc}")
            return 3

        print("[孔夫子登录] 现在可以在 Web UI 的「孔夫子监控 → 账号配置」看到该账号。")
        print("[孔夫子登录] 浏览器 5 秒后自动关闭。")
        await asyncio.sleep(5)
        await context.close()
        await browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))