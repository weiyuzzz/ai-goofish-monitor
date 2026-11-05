"""反爬伪装层单测：会话预热、cookie 复用、诱饵动作、请求头真实性。

全部用 httpx.MockTransport，不联网。
"""
import json

import httpx
import pytest

from src.kongfz.client import (
    DEFAULT_COOKIE_FILE,
    HOME_URL,
    SEARCH_PAGE_URL,
    KongfzClient,
    _item_page_url,
)


def _api_ok_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "status": 1,
            "data": {
                "totalFound": 1,
                "itemResponse": {
                    "list": [
                        {
                            "itemId": 1,
                            "title": "测试书",
                            "isSoldOut": False,
                            "priceText": "10.00",
                            "link": {"pc": "https://book.kongfz.com/1/1"},
                        }
                    ],
                    "pager": {"pages": 1},
                },
            },
        },
    )


class Recorder:
    def __init__(self):
        self.requests: list[tuple[str, str, dict]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(
            (request.method, str(request.url), dict(request.headers))
        )
        if "/list" in str(request.url):
            return _api_ok_response()
        return httpx.Response(200, text="<html><body>ok</body></html>")

    @property
    def urls(self) -> list[str]:
        return [url for _method, url, _headers in self.requests]


@pytest.fixture()
def recorder():
    return Recorder()


def _client(tmp_path, recorder, **kwargs) -> KongfzClient:
    return KongfzClient(
        transport=httpx.MockTransport(recorder.handler),
        cookie_file=str(tmp_path / "cookies.json"),
        min_interval=0,
        jitter=0,
        **kwargs,
    )


def test_warmup_visits_homepage_then_search_page(tmp_path, recorder):
    recorder.requests.clear()

    async def run():
        async with _client(tmp_path, recorder) as client:
            await client.warmup("红楼梦")

    import asyncio

    asyncio.run(run())

    urls = recorder.urls
    assert urls, "应当有预热请求"
    assert urls[0].startswith(HOME_URL), f"首个请求应为首页，实际 {urls[0]}"
    assert any(SEARCH_PAGE_URL in url for url in urls[1:]), "第二个请求应为搜索页"


def test_json_request_carries_real_browser_headers(tmp_path, recorder):
    async def run():
        async with _client(tmp_path, recorder) as client:
            await client.fetch_items("红楼梦", scan_limit=10, page_size=20)

    import asyncio

    asyncio.run(run())

    api_call = None
    for _method, url, headers in recorder.requests:
        if "/list" in url:
            api_call = (headers, url)
            break
    assert api_call is not None, "应当发出列表接口请求"
    headers, _url = api_call
    # 接口请求必须像页面发起的 XHR
    assert "search.kongfz.com" in headers.get("referer", "")
    assert headers.get("origin") == "https://search.kongfz.com"
    assert headers.get("x-requested-with") == "XMLHttpRequest"
    assert headers.get("sec-fetch-mode") == "cors"
    assert "Chrome" in headers.get("user-agent", "")


def test_cookie_file_is_reused_across_runs(tmp_path):
    cookie_path = tmp_path / "cookies.json"
    cookie_path.write_text(
        json.dumps(
            {
                "cookies": [
                    {"name": "PHPSESSID", "value": "abc", "domain": ".kongfz.com", "path": "/"}
                ]
            }
        ),
        encoding="utf-8",
    )
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("cookie", ""))
        return _api_ok_response()

    async def run():
        async with KongfzClient(
            transport=httpx.MockTransport(handler),
            cookie_file=str(cookie_path),
            min_interval=0,
            jitter=0,
        ) as client:
            await client.fetch_items("红楼梦", scan_limit=5, page_size=20)

    import asyncio

    asyncio.run(run())

    assert any("PHPSESSID=abc" in cookie for cookie in seen), "应复用上一轮的会话 cookie"


def test_cookie_file_is_written_after_run(tmp_path):
    cookie_path = tmp_path / "out.json"

    def handler(_request: httpx.Request) -> httpx.Response:
        return _api_ok_response()

    async def run():
        async with KongfzClient(
            transport=httpx.MockTransport(handler),
            cookie_file=str(cookie_path),
            min_interval=0,
            jitter=0,
        ) as client:
            client._client.cookies.set("kfz_uuid", "xyz", domain=".kongfz.com")
            await client.fetch_items("红楼梦", scan_limit=5, page_size=20)

    import asyncio

    asyncio.run(run())

    payload = json.loads(cookie_path.read_text(encoding="utf-8"))
    names = {cookie["name"] for cookie in payload["cookies"]}
    assert "kfz_uuid" in names


def test_decoy_actions_only_touch_html_pages(tmp_path, recorder):
    recorder.requests.clear()
    items = [{"itemId": 9, "link": {"pc": "https://book.kongfz.com/1/9"}}]

    async def run():
        async with _client(tmp_path, recorder, decoy_max_actions=2) as client:
            await client.decoy_actions(items)

    import asyncio

    asyncio.run(run())

    for _method, url, headers in recorder.requests:
        assert "/list" not in url, "诱饵动作绝不能打 JSON 接口"
        assert "/pc-gw/" not in url
        assert headers.get("sec-fetch-mode") == "navigate"


def test_decoy_disabled_makes_no_requests(tmp_path, recorder):
    recorder.requests.clear()

    async def run():
        async with _client(tmp_path, recorder, decoy=False) as client:
            await client.decoy_actions([{"itemId": 1}])

    import asyncio

    asyncio.run(run())
    assert recorder.requests == []


def test_warmup_disabled_makes_no_requests(tmp_path, recorder):
    recorder.requests.clear()

    async def run():
        async with _client(tmp_path, recorder, warmup=False) as client:
            await client.warmup("红楼梦")

    import asyncio

    asyncio.run(run())
    assert recorder.requests == []


def test_item_page_url_fallbacks():
    assert _item_page_url({"link": {"pc": "https://book.kongfz.com/1/2"}}) == "https://book.kongfz.com/1/2"
    assert _item_page_url({"itemId": 2, "shopId": 1}) == "https://book.kongfz.com/1/2"
    assert _item_page_url({}) == ""


def test_default_cookie_file_is_outside_login_state_dir():
    # 游客 cookie 不能落在 state/ 下，否则会被当成“账号登录态”列出
    assert not DEFAULT_COOKIE_FILE.startswith("state/")
    assert DEFAULT_COOKIE_FILE.endswith(".json")