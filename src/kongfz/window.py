"""监控窗口计算（纯函数，便于单测）。

孔夫子监控的语义：**展示集合永远等于站点最新排序下最新 N 条仍在售的商品**。

- 有新商品上架 → 进入窗口，末尾商品被挤出并移除；
- 有商品下架/售出 → 被跳过，后面的商品补位；
- 结果：本地保存的记录与站点「最新上架」排序持续同步。

新增 vs 递补的判定（站点按「最新上架」倒序返回，顺序稳定）：
- 首个「上一轮已在窗口内」的商品作为锚点（anchor）；
- 锚点之前出现的陌生商品 → 上架时间晚于全部已知商品 → **真·新上架**（通知）；
- 锚点之后出现的陌生商品 → 上一轮就存在于窗口之外（更旧）→ **递补**（只入库不通知）；
- 上一轮窗口为空 → 全部视为**初始基线**（只入库不通知）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from src.kongfz.parser import build_item_unique_key

ENTRY_NEW_LISTING = "new_listing"
ENTRY_BACKFILL = "backfill"


@dataclass(frozen=True)
class WindowPlan:
    """一次抓取后的窗口差异计划。"""

    window_items: list[dict] = field(default_factory=list)
    window_keys: list[str] = field(default_factory=list)
    new_items: list[dict] = field(default_factory=list)
    new_keys: list[str] = field(default_factory=list)
    new_listing_items: list[dict] = field(default_factory=list)
    new_listing_keys: list[str] = field(default_factory=list)
    backfill_items: list[dict] = field(default_factory=list)
    backfill_keys: list[str] = field(default_factory=list)
    baseline_items: list[dict] = field(default_factory=list)
    removed_keys: list[str] = field(default_factory=list)
    sold_keys: list[str] = field(default_factory=list)
    scanned: int = 0
    complete: bool = True

    @property
    def new_count(self) -> int:
        return len(self.new_items)

    @property
    def new_listing_count(self) -> int:
        return len(self.new_listing_items)

    @property
    def backfill_count(self) -> int:
        return len(self.backfill_items)

    @property
    def removed_count(self) -> int:
        return len(self.removed_keys)

    @property
    def is_baseline_run(self) -> bool:
        """首轮：本地还没有任何样本，全部按基线入库（不通知）。"""
        return bool(self.baseline_items) and not self.new_listing_items


def build_window_plan(
    items: Sequence[dict],
    existing_keys: Iterable[str],
    limit: int,
    *,
    complete: bool = True,
    scan_capped: bool = False,
) -> WindowPlan:
    """根据最新排序的商品列表计算窗口。

    Args:
        items: 站点返回的商品，必须按「最新上架」倒序排列。
        existing_keys: 本地已保存的唯一键集合（即上一轮窗口）。
        limit: 窗口大小（用户配置的「监控最新 N 条」）。
        complete: 本次抓取是否可信（分页完整且未出错）。
            为 False 时上层不应执行删除。
        scan_capped: 是否因扫描上限而截断（未扫完）。
            截断时若整个扫描范围内都没出现旧窗口商品，
            说明新上架数量超过扫描上限，此时陌生商品按「新上架」处理。
    """
    size = max(1, int(limit or 1))
    existing = {str(key) for key in (existing_keys or []) if key}

    ordered_items: list[dict] = []
    ordered_keys: list[str] = []
    sold_keys: list[str] = []
    seen: set[str] = set()

    for item in items or []:
        if not isinstance(item, dict):
            continue
        key = build_item_unique_key(item)
        if not key or key in seen:
            continue
        seen.add(key)

        if item.get("isSoldOut"):
            # 已售/已下架：不进入窗口，若之前保存过则记录为「已售」移除原因。
            if key in existing:
                sold_keys.append(key)
            continue

        ordered_items.append(item)
        ordered_keys.append(key)

    # 锚点：扫描序列里第一个「上一轮已在窗口」的商品。
    anchor_index: int | None = None
    for index, key in enumerate(ordered_keys):
        if key in existing:
            anchor_index = index
            break

    window_items = ordered_items[:size]
    window_keys = ordered_keys[:size]

    def _classify(index: int) -> str:
        if not existing:
            return "baseline"
        if anchor_index is None:
            # 旧窗口商品一个都没扫到：
            # 截断 → 新上架爆量；扫完 → 旧窗口整体消失（陌生商品全是更旧的递补）。
            return ENTRY_NEW_LISTING if scan_capped else ENTRY_BACKFILL
        return ENTRY_NEW_LISTING if index < anchor_index else ENTRY_BACKFILL

    new_listing_items: list[dict] = []
    backfill_items: list[dict] = []
    baseline_items: list[dict] = []
    new_items: list[dict] = []

    for index, (item, key) in enumerate(zip(window_items, window_keys)):
        if key in existing:
            continue
        new_items.append(item)
        kind = _classify(index)
        if kind == ENTRY_NEW_LISTING:
            new_listing_items.append(item)
        elif kind == ENTRY_BACKFILL:
            backfill_items.append(item)
        else:
            baseline_items.append(item)

    window_key_set = set(window_keys)
    removed_keys = sorted(existing - window_key_set)

    return WindowPlan(
        window_items=window_items,
        window_keys=window_keys,
        new_items=new_items,
        new_keys=[build_item_unique_key(item) for item in new_items],
        new_listing_items=new_listing_items,
        new_listing_keys=[build_item_unique_key(item) for item in new_listing_items],
        backfill_items=backfill_items,
        backfill_keys=[build_item_unique_key(item) for item in backfill_items],
        baseline_items=baseline_items,
        removed_keys=removed_keys,
        sold_keys=sold_keys,
        scanned=len(seen),
        complete=bool(complete),
    )


def describe_removal_reason(link_key: str, sold_keys: Iterable[str]) -> str:
    if link_key in set(sold_keys or []):
        return "sold"
    return "left_window"


def format_removal_reason(reason: str) -> str:
    if reason == "sold":
        return "已售出/已下架"
    return "已被更新的商品挤出监控窗口"


def resolve_scan_limit(limit: int, *, factor: int = 3, hard_cap: int = 500) -> int:
    """计算需要扫描的原始条目数（含已售条目，因此需要放大）。"""
    size = max(1, int(limit or 1))
    scan = max(size + 10, size * max(1, factor))
    return min(scan, hard_cap)


def resolve_page_size(scan_limit: int, *, api_max: int = 100) -> int:
    """按扫描目标选择单页条数，让一轮抓取尽量合并成 1 个请求。

    实测 size≤100 可匿名访问（200 会触发登录墙），因此上限钉在 100；
    小目标直接用目标值（例如窗口 30 → 扫描 90 → 一页 90，单请求完成）。
    """
    target = max(1, int(scan_limit or 1))
    if target >= api_max:
        return api_max
    return max(20, target)


def summarize_plan(plan: WindowPlan, limit: int) -> str:
    return (
        f"窗口 {len(plan.window_keys)}/{limit} 条，"
        f"新上架 {plan.new_listing_count}，递补 {plan.backfill_count}，"
        f"移除 {plan.removed_count}，扫描 {plan.scanned} 条"
    )