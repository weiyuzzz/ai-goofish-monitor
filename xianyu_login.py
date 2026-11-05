"""闲鱼登录态获取工具（扫码登录 → 导出 state/*.json）。

用途：不想安装 Chrome 扩展时，用这个脚本打开一个真实浏览器窗口，
用闲鱼/手机淘宝 App 扫码登录后，脚本会把 Playwright storage_state
写入 state/<账号名>.json，格式与「账号管理」里粘贴的 JSON 一致。

用法:
    .venv\\Scripts\\python.exe xianyu_login.py                    # 默认保存到 state/acc1.json
    .venv\\Scripts\\python.exe xianyu_login.py --name acc2        # 保存到 state/acc2.json
    .venv\\Scripts\\python.exe xianyu_login.py --timeout 900      # 等待扫码的秒数
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

from playwright.async_api import async_playwright

HOME_URL = "https://www.goofish.com/"
# 注意：cookie2 / _tb_token_ / _m_h5_tk / tfstk / cna 在“未登录”时也会被下发，
# 只有 unb（淘宝用户编号）才是真正登录成功的判据。
LOGIN_COOKIE_NAMES = ("unb", "cookie2", "_tb_token_")
LOGGED_IN_PAGE_MARKERS = ("发布闲置", "我的闲鱼", "退出登录", "我的发布", "我的关注")


def _pick_channel() -> str:
    """优先 Chrome，其次 Edge（与 scraper 的 channel 逻辑保持一致）。"""
    from src.scraper import _resolve_browser_channel

    return _resolve_browser_channel()


def _login_cookies(cookies: list[dict]) -> dict:
    picked = {}
    for cookie in cookies:
        if cookie.get("name") in LOGIN_COOKIE_NAMES and cookie.get("value"):
            picked[cookie["name"]] = str(cookie["value"])
    return picked


def _cookies_look_logged_in(picked: dict) -> bool:
    unb = (picked.get("unb") or "").strip()
    return bool(unb) and unb.lower() not in {"0", "null", "false"}


async def _page_looks_logged_in(page) -> bool:
    try:
        text = await page.inner_text("body")
    except Exception:  # noqa: BLE001
        return False
    return any(marker in text for marker in LOGGED_IN_PAGE_MARKERS)


async def main() -> int:
    parser = argparse.ArgumentParser(description="闲鱼登录态导出工具")
    parser.add_argument("--name", default="acc1", help="账号文件名（不含 .json）")
    parser.add_argument("--timeout", type=int, default=900, help="等待扫码登录的秒数")
    parser.add_argument("--headless", action="store_true", help="无头模式（无法扫码，仅用于自检）")
    args = parser.parse_args()

    state_dir = os.getenv("ACCOUNT_STATE_DIR", "state")
    os.makedirs(state_dir, exist_ok=True)
    state_path = os.path.join(state_dir, f"{args.name}.json")

    channel = _pick_channel()
    print(f"[登录] 保存目标: {state_path}")
    print(f"[登录] 浏览器通道: {channel}")

    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(channel=channel, headless=args.headless)
        except Exception as exc:  # noqa: BLE001
            print(f"[登录] channel={channel} 启动失败（{exc}），回退到内置 Chromium")
            browser = await p.chromium.launch(headless=args.headless)

        context = await browser.new_context(
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()
        await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)

        print("[登录] 已打开 www.goofish.com。请点击右上角「登录」，然后用手机闲鱼/淘宝 App 扫码。")
        print("[登录] 扫码完成后脚本会自动检测并保存登录态，无需其它操作。")

        deadline = time.time() + args.timeout
        saved = False
        last_hint = ""
        while time.time() < deadline:
            await asyncio.sleep(2)
            try:
                cookies = await context.cookies()
            except Exception as exc:  # noqa: BLE001
                print(f"[登录] 浏览器似乎已关闭: {exc}")
                return 2
            picked = _login_cookies(cookies)
            cookie_ok = _cookies_look_logged_in(picked)
            page_ok = True if cookie_ok else await _page_looks_logged_in(page)
            if cookie_ok or page_ok:
                # 刷新一次，确保拿到登录后新下发的全部 cookie，并二次确认。
                try:
                    await page.reload(wait_until="domcontentloaded", timeout=60000)
                    await asyncio.sleep(3)
                except Exception:  # noqa: BLE001
                    pass
                cookies = await context.cookies()
                picked = _login_cookies(cookies)
                cookie_ok = _cookies_look_logged_in(picked)
                page_ok = await _page_looks_logged_in(page)
                if not (cookie_ok or page_ok):
                    print("[登录] 疑似检测到登录但二次确认未通过，继续等待…")
                    continue
                await context.storage_state(path=state_path)
                saved = True
                print(
                    f"[登录] 已确认登录（unb={'有' if cookie_ok else '无'}，"
                    f"页面标记={'有' if page_ok else '无'}）；"
                    f"cookie: {sorted(picked)}"
                )
                break

            hint = f"[登录] 等待扫码中… 已等待 {int(time.time() - (deadline - args.timeout))}s"
            if hint != last_hint:
                print(hint, flush=True)
                last_hint = hint

        if not saved:
            print(f"[登录] 超时（{args.timeout}s）未检测到登录，已放弃。")
            await context.close()
            await browser.close()
            return 1

        try:
            with open(state_path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            print(
                f"[登录] 已保存: {state_path} "
                f"(cookies={len(payload.get('cookies', []))}, origins={len(payload.get('origins', []))})"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[登录] 保存校验失败: {exc}")
            return 3

        print("[登录] 现在可以在 Web UI 的「账号管理」看到该账号，或用任务绑定它。")
        print("[登录] 浏览器 5 秒后自动关闭。")
        await asyncio.sleep(5)
        await context.close()
        await browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))