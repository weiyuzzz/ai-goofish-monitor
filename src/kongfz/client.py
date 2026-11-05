"""孔夫子旧书网 HTTP 客户端。

实测结论（2026-11）：
- 列表接口是 JSON 网关 `/pc-gw/search-web/client/pc/product/{scene}/list`；
- `scene=latest`（最新上架）游客可短期访问，但请求量上来后会返回
  `errType=102 / GO_LOGIN`，因此**配置登录态是可靠运行的前提**；
- `scene=keyword` + `sortType=3`（最新上架，官方前端同款）需要登录态；
- 商品详情页 `https://book.kongfz.com/{shopId}/{itemId}` 是服务端渲染，
  在售时含 `class="on-sell"` / `go-buy` / `up-book-date-onsale` 标记，
  商品不存在时返回 404。
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import quote

import httpx

SEARCH_HOST = "https://search.kongfz.com"
ITEM_HOST = "https://book.kongfz.com"
PRODUCT_API = "/pc-gw/search-web/client/pc/product"

SCENE_KEYWORD = "keyword"
SCENE_LATEST = "latest"
VALID_SCENES = (SCENE_KEYWORD, SCENE_LATEST)

SORT_LATEST = 3  # 官方前端 sortType 枚举：最新上架

API_MAX_PAGE_SIZE = 100  # 实测 size=100 稳定，200 会触发登录墙

LOGIN_ERR_TYPE = "102"
LOGIN_HINT = "登录态缺失或已失效，请在「孔夫子监控 → 账号配置」中扫码登录后再运行"
VERIFY_HINT_MARKERS = ("验证", "自动请求", "计算机软件", "人机")
VERIFY_HINT = (
    "站点要求人机验证（提示「类似计算机软件的自动请求」）："
    "请降低监控频率，并在「账号配置」重新扫码登录、在弹窗中手动完成验证后再运行"
)

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# ---------- 反爬伪装相关 ----------
HOME_URL = "https://www.kongfz.com/"
SEARCH_PAGE_URL = f"{SEARCH_HOST}/product_result/"
# 游客会话 cookie 持久化（跨轮复用会话，避免每轮都是“全新机器人”）
DEFAULT_COOKIE_FILE = "data/kongfz_guest_cookies.json"
# 诱饵动作里的其它搜索词（制造“顺手逛了逛”的多样性）
DECOY_KEYWORDS = ("三国演义", "红楼梦", "线装书", "人民文学出版社", "旧书")

ON_SALE_MARKERS = ('class="on-sell"', "go-buy", "up-book-date-onsale")
OFF_SALE_MARKERS = ("已售完", "已售出", "该商品已下架", "商品已下架", "该商品不存在", "已下架")


class KongfzError(RuntimeError):
    """孔夫子接口调用失败。"""


class KongfzLoginRequiredError(KongfzError):
    """接口要求登录（errType=102）。"""


class KongfzBlockedError(KongfzError):
    """触发风控 / 频控，需要退避或更换登录态。"""


@dataclass
class FetchResult:
    items: list[dict] = field(default_factory=list)
    pages: int = 0
    scanned: int = 0
    total_found: int = 0
    reached_end: bool = False
    scene: str = ""

    @property
    def complete(self) -> bool:
        """抓取是否可信（可以据此删除本地记录）。"""
        return self.pages > 0


@dataclass
class ItemStatus:
    item_id: str
    url: str
    http_status: Optional[int] = None
    exists: bool = False
    on_sale: Optional[bool] = None
    reason: str = ""


def load_cookie_header(state_file: str) -> str:
    """从 Playwright storage_state 文件提取 kongfz 域下的 cookie 头。"""
    if not state_file or not os.path.exists(state_file):
        return ""
    try:
        with open(state_file, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return ""

    cookies = payload.get("cookies") if isinstance(payload, dict) else None
    if not isinstance(cookies, list):
        return ""

    parts: list[str] = []
    seen: set[str] = set()
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        domain = str(cookie.get("domain") or "")
        if domain and "kongfz" not in domain:
            continue
        name = cookie.get("name")
        value = cookie.get("value")
        if not name or value is None or str(value) == "" or name in seen:
            continue
        seen.add(str(name))
        parts.append(f"{name}={value}")
    return "; ".join(parts)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _item_page_url(item: dict) -> str:
    """取商品详情页 URL（诱饵动作用）。"""
    if not isinstance(item, dict):
        return ""
    link = item.get("link")
    if isinstance(link, dict):
        for key in ("pc", "h5", "mp"):
            value = link.get(key)
            if value:
                return str(value)
    item_id = str(item.get("itemId") or "").strip()
    shop_id = str(item.get("shopId") or "").strip()
    if item_id and shop_id:
        return f"{ITEM_HOST}/{shop_id}/{item_id}"
    return ""


class KongfzClient:
    """带限速、重试与错误分类的孔夫子接口客户端。"""

    def __init__(
        self,
        *,
        cookie_header: str | None = None,
        cookie_source: str | None = None,
        scene: str | None = None,
        min_interval: float | None = None,
        jitter: float | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        proxy: str | None = None,
        user_area: str | None = None,
        warmup: bool | None = None,
        decoy: bool | None = None,
        decoy_max_actions: int | None = None,
        cookie_file: str | None = None,
        transport=None,
    ) -> None:
        self.cookie_header = (cookie_header or "").strip()
        self.cookie_source = cookie_source
        self.scene = (scene or "").strip().lower() or None
        self.min_interval = (
            min_interval if min_interval is not None
            else _env_float("KONGFZ_REQUEST_INTERVAL", 3.0)
        )
        self.jitter = jitter if jitter is not None else _env_float("KONGFZ_REQUEST_JITTER", 1.0)
        self.timeout = timeout if timeout is not None else _env_float("KONGFZ_REQUEST_TIMEOUT", 20.0)
        self.max_retries = (
            max_retries if max_retries is not None
            else _env_int("KONGFZ_REQUEST_RETRIES", 3)
        )
        self.proxy = proxy or os.getenv("KONGFZ_PROXY", "").strip() or None
        self.user_area = (user_area or os.getenv("KONGFZ_USER_AREA", "").strip()) or None
        # 伪装开关：默认全开（纯页面级动作，不打接口）
        self.warmup_enabled = (
            warmup if warmup is not None else _env_bool("KONGFZ_WARMUP_ENABLED", True)
        )
        self.decoy_enabled = (
            decoy if decoy is not None else _env_bool("KONGFZ_DECOY_ENABLED", True)
        )
        self.decoy_max_actions = (
            decoy_max_actions
            if decoy_max_actions is not None
            else _env_int("KONGFZ_DECOY_MAX_ACTIONS", 2)
        )
        self.cookie_file = (
            cookie_file
            if cookie_file is not None
            else (os.getenv("KONGFZ_COOKIE_FILE", "").strip() or DEFAULT_COOKIE_FILE)
        )
        self._transport = transport
        self._client: Optional[httpx.AsyncClient] = None
        self._last_request_at = 0.0
        self._referer = f"{SEARCH_PAGE_URL}?"
        self.warmup_done = False

    # ------------------------------------------------------------------ 基建

    @property
    def has_login(self) -> bool:
        return bool(self.cookie_header)

    def resolve_scene(self) -> str:
        if self.scene in VALID_SCENES:
            return str(self.scene)
        override = os.getenv("KONGFZ_LIST_SCENE", "").strip().lower()
        if override in VALID_SCENES:
            return override
        return SCENE_KEYWORD if self.has_login else SCENE_LATEST

    def _headers(self, *, json_request: bool = True) -> dict[str, str]:
        """尽量贴近真实浏览器：固定 UA 会话、带 Referer 链与 Sec-Fetch 头。"""
        headers = {
            "User-Agent": DEFAULT_UA,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
        if json_request:
            headers["Accept"] = "application/json, text/plain, */*"
            # Referer 指向真实搜索页（warmup 后会带上关键词），而不是空泛的根路径
            headers["Referer"] = self._referer
            headers["Origin"] = SEARCH_HOST
            headers["X-Requested-With"] = "XMLHttpRequest"
            headers["Sec-Fetch-Dest"] = "empty"
            headers["Sec-Fetch-Mode"] = "cors"
            headers["Sec-Fetch-Site"] = "same-site"
        else:
            headers["Accept"] = (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            )
            headers["Sec-Fetch-Dest"] = "document"
            headers["Sec-Fetch-Mode"] = "navigate"
            headers["Sec-Fetch-Site"] = "none"
            headers["Sec-Fetch-User"] = "?1"
        if self.cookie_header:
            headers["Cookie"] = self.cookie_header
        return headers

    async def __aenter__(self) -> "KongfzClient":
        self._client = httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            proxy=self.proxy or None,
            transport=self._transport,
        )
        self._load_cookies()
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.close()

    async def close(self) -> None:
        if self._client is not None:
            self._save_cookies()
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------ cookie 复用

    def _load_cookies(self) -> None:
        """载入上一轮保存的游客会话 cookie，让多轮看起来是同一个人。"""
        if self._client is None or not self.cookie_file:
            return
        if self.cookie_header:
            # 已有登录态（storage_state）时以它为准，不混入游客 cookie
            return
        try:
            if not os.path.exists(self.cookie_file):
                return
            with open(self.cookie_file, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:  # noqa: BLE001
            return

        cookies = payload.get("cookies") if isinstance(payload, dict) else None
        if not isinstance(cookies, list):
            return
        for cookie in cookies:
            if not isinstance(cookie, dict):
                continue
            name = cookie.get("name")
            value = cookie.get("value")
            if not name or value is None:
                continue
            try:
                self._client.cookies.set(
                    str(name),
                    str(value),
                    domain=str(cookie.get("domain") or "") or None,
                    path=str(cookie.get("path") or "/"),
                )
            except Exception:  # noqa: BLE001
                continue

    def _save_cookies(self) -> None:
        if self._client is None or not self.cookie_file or self.cookie_header:
            return
        try:
            payload = {
                "cookies": [
                    {
                        "name": cookie.name,
                        "value": cookie.value,
                        "domain": cookie.domain or "",
                        "path": cookie.path or "/",
                    }
                    for cookie in self._client.cookies.jar
                ]
            }
            if not payload["cookies"]:
                return
            directory = os.path.dirname(self.cookie_file)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(self.cookie_file, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
        except Exception as exc:  # noqa: BLE001
            print(f"   [孔夫子] 保存会话 cookie 失败（忽略）: {exc}")

    # -------------------------------------------------------------- 反爬伪装

    async def _human_pause(self, low: float = 1.5, high: float = 4.0) -> None:
        await asyncio.sleep(random.uniform(low, high))

    async def warmup(self, keyword: str = "") -> None:
        """先像人一样打开首页与搜索结果页，再打接口。

        直接裸调 JSON 接口最容易被判成“自动请求”；先有文档级访问，
        接口请求就带着真实的 Referer 链和会话 cookie。
        """
        if not self.warmup_enabled or self._client is None:
            return
        try:
            await self._http_get(HOME_URL, json_request=False, retry=False)
            await self._human_pause()
            params = {"key": keyword} if keyword else None
            await self._http_get(
                SEARCH_PAGE_URL, params=params, json_request=False, retry=False
            )
            if keyword:
                # HTTP 头必须是 ASCII，中文关键词必须 URL 编码
                self._referer = f"{SEARCH_PAGE_URL}?key={quote(keyword)}"
            self.warmup_done = True
            print("   [孔夫子] 已完成会话预热（首页 → 搜索页）")
            await self._human_pause(1.0, 2.5)
        except Exception as exc:  # noqa: BLE001
            print(f"   [孔夫子] 会话预热失败（继续尝试抓取）: {exc}")

    async def decoy_actions(self, items: list[dict]) -> None:
        """抓完之后随机做 0~N 个“像人”的浏览动作。

        只访问 HTML 页面（商品页/首页/其它搜索页），绝不打接口，
        避免把“每分钟请求数”推高到阈值。
        """
        if not self.decoy_enabled or self._client is None:
            return
        budget = max(0, int(self.decoy_max_actions or 0))
        if budget <= 0:
            return
        count = random.randint(0, budget)
        if count <= 0:
            return

        for _ in range(count):
            try:
                roll = random.random()
                if roll < 0.55 and items:
                    url = _item_page_url(random.choice(items))
                    if url:
                        await self._http_get(url, json_request=False, retry=False)
                elif roll < 0.8:
                    await self._http_get(HOME_URL, json_request=False, retry=False)
                else:
                    await self._http_get(
                        SEARCH_PAGE_URL,
                        params={"key": random.choice(DECOY_KEYWORDS)},
                        json_request=False,
                        retry=False,
                    )
            except Exception:  # noqa: BLE001
                # 诱饵动作失败绝不能影响主流程
                pass
            await self._human_pause(2.0, 6.0)
        print(f"   [孔夫子] 已完成 {count} 个浏览动作（伪装）")

    async def _pace(self) -> None:
        """全局限速：保证两次请求之间的间隔。"""
        if self.min_interval <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        wait = self.min_interval + random.uniform(0, max(0.0, self.jitter)) - elapsed
        if wait > 0:
            await asyncio.sleep(wait)

    async def _http_get(
        self,
        url: str,
        *,
        params: dict | None = None,
        json_request: bool = True,
        retry: bool = True,
    ):
        if self._client is None:
            raise KongfzError("KongfzClient 未初始化，请使用 async with 上下文")

        attempts = max(1, self.max_retries) if retry else 1
        last_error: Optional[Exception] = None
        for attempt in range(1, attempts + 1):
            await self._pace()
            try:
                response = await self._client.get(
                    url,
                    params=params,
                    headers=self._headers(json_request=json_request),
                )
                self._last_request_at = time.monotonic()
                if not retry:
                    # 预热/诱饵请求：失败也不重试、不抛错，避免拖慢主流程
                    return response
                if response.status_code in (429, 403) or response.status_code >= 500:
                    last_error = KongfzBlockedError(
                        f"HTTP {response.status_code} from {url}"
                    )
                    await self._backoff(attempt)
                    continue
                return response
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                self._last_request_at = time.monotonic()
                if not retry:
                    raise KongfzError(f"请求 {url} 失败: {exc}") from exc
                await self._backoff(attempt)

        raise KongfzError(f"请求 {url} 失败: {last_error}")

    async def _backoff(self, attempt: int) -> None:
        delay = min(30.0, 1.5 * (2 ** (attempt - 1)))
        print(f"   [孔夫子] 请求失败，{delay:.1f}s 后重试（第 {attempt} 次）")
        await asyncio.sleep(delay)

    # -------------------------------------------------------------- 列表接口

    async def _request_list_page(
        self,
        *,
        keyword: str,
        page: int,
        size: int,
        scene: str,
    ) -> dict:
        params: dict[str, Any] = {"page": page, "size": size}
        if keyword:
            params["keyword"] = keyword
        if scene == SCENE_KEYWORD:
            params["sortType"] = SORT_LATEST
        if self.user_area:
            params["userArea"] = self.user_area

        url = f"{SEARCH_HOST}{PRODUCT_API}/{scene}/list"
        response = await self._http_get(url, params=params)
        try:
            payload = response.json()
        except Exception as exc:
            raise KongfzError(f"列表接口返回非 JSON: {exc}") from exc

        status = payload.get("status")
        if status == 1:
            return payload.get("data") or {}

        message = str(payload.get("message") or "").strip()
        err_type = str(payload.get("errType") or "").strip()
        if any(marker in message for marker in VERIFY_HINT_MARKERS):
            raise KongfzBlockedError(
                f"{VERIFY_HINT}（errType={err_type or 'N/A'}，message={message}）"
            )
        if err_type == LOGIN_ERR_TYPE or "登录" in message:
            raise KongfzLoginRequiredError(
                f"{LOGIN_HINT}（errType={err_type or 'N/A'}，message={message}）"
            )
        raise KongfzError(f"列表接口返回错误: errType={err_type} message={message}")

    async def fetch_items(
        self,
        keyword: str,
        *,
        scan_limit: int,
        page_size: int = API_MAX_PAGE_SIZE,
    ) -> FetchResult:
        """按「最新上架」倒序抓取，最多扫描 `scan_limit` 条。"""
        scene = self.resolve_scene()
        size = max(1, min(API_MAX_PAGE_SIZE, int(page_size or API_MAX_PAGE_SIZE)))
        target = max(1, int(scan_limit or size))
        if keyword:
            # 让接口请求带着「搜索页」Referer（未预热时同样是真实链路）
            self._referer = f"{SEARCH_PAGE_URL}?key={quote(keyword)}"

        result = FetchResult(scene=scene)
        page = 1
        while result.scanned < target:
            data = await self._request_list_page(
                keyword=keyword.strip(),
                page=page,
                size=size,
                scene=scene,
            )
            result.pages += 1

            item_response = data.get("itemResponse") or {}
            items = item_response.get("list") or []
            if isinstance(data.get("totalFound"), int):
                result.total_found = int(data["totalFound"])

            if not items:
                result.reached_end = True
                break

            result.items.extend(item for item in items if isinstance(item, dict))
            result.scanned = len(result.items)

            pager = item_response.get("pager") or {}
            total_pages = pager.get("pages") or pager.get("pageCount")
            if isinstance(total_pages, int) and total_pages > 0 and page >= total_pages:
                result.reached_end = True
                break
            page += 1

        print(
            f"   [孔夫子] {scene} 场景抓取 {result.pages} 页 / {result.scanned} 条"
            f"（站点命中 {result.total_found} 条）"
        )
        return result

    # -------------------------------------------------------------- 详情探测

    async def probe_item(self, item_id: str, shop_id: str | None = None) -> ItemStatus:
        """探测商品是否仍在售（404=已下架/删除，200=按页面标记判断）。"""
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
        response = await self._http_get(url, json_request=False)
        text = response.text or ""

        if response.status_code in (404, 410):
            return ItemStatus(
                item_id=item_id,
                url=url,
                http_status=response.status_code,
                exists=False,
                on_sale=False,
                reason="not_found",
            )
        if response.status_code >= 400:
            return ItemStatus(
                item_id=item_id,
                url=url,
                http_status=response.status_code,
                exists=True,
                on_sale=None,
                reason=f"http_{response.status_code}",
            )

        if any(marker in text for marker in ON_SALE_MARKERS):
            return ItemStatus(
                item_id=item_id,
                url=url,
                http_status=response.status_code,
                exists=True,
                on_sale=True,
                reason="on_sale",
            )
        if any(marker in text for marker in OFF_SALE_MARKERS):
            return ItemStatus(
                item_id=item_id,
                url=url,
                http_status=response.status_code,
                exists=True,
                on_sale=False,
                reason="off_sale",
            )
        return ItemStatus(
            item_id=item_id,
            url=url,
            http_status=response.status_code,
            exists=True,
            on_sale=None,
            reason="unknown_marker",
        )

    # -------------------------------------------------------------- 登录态

    async def verify_login_state(self) -> tuple[bool, str]:
        """用一条最小请求校验当前 cookie 是否有效。"""
        url = f"{SEARCH_HOST}{PRODUCT_API}/{SCENE_KEYWORD}/list"
        params: dict[str, Any] = {"keyword": "书", "page": 1, "size": 1, "sortType": SORT_LATEST}
        if self.user_area:
            params["userArea"] = self.user_area
        try:
            response = await self._http_get(url, params=params)
            payload = response.json()
        except KongfzError as exc:
            return False, str(exc)
        except Exception as exc:  # noqa: BLE001
            return False, f"校验请求失败: {exc}"

        if payload.get("status") == 1:
            return True, "登录态有效"
        message = str(payload.get("message") or "").strip()
        if str(payload.get("errType")) == LOGIN_ERR_TYPE or "登录" in message:
            return False, "登录态已失效，请重新扫码登录"
        return False, f"接口返回异常: {message or payload}"