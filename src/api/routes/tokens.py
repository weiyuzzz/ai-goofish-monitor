"""
Token 用量监测路由
"""
from fastapi import APIRouter, HTTPException, Query

from src.services.token_usage_service import (
    DEFAULT_TREND_DAYS,
    MAX_TREND_DAYS,
    build_token_usage_snapshot,
)


router = APIRouter(prefix="/api/tokens", tags=["tokens"])


@router.get("/summary")
async def get_token_usage_summary(
    days: int = Query(DEFAULT_TREND_DAYS, ge=1, le=MAX_TREND_DAYS),
):
    try:
        return build_token_usage_snapshot(days=days)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"加载 token 用量数据失败: {exc}")