"""Usage statistics API endpoints."""

from fastapi import APIRouter, Request, Query, HTTPException

router = APIRouter(prefix="/admin/usage-stats", tags=["usage-stats"])

# 用量统计关闭/尚未初始化时的空摘要（与 UsageStatsStore.get_summary 的零值形状一致），
# 让面板拿到干净的空响应而不是 500（修复 #14/#30）。
_EMPTY_SUMMARY = {
    "request_count": 0, "error_count": 0,
    "active_accounts": 0, "total_accounts": 0,
    "model_requests": {}, "avg_latency_ms": 0.0,
    "max_latency_ms": 0.0, "rotation_success": 0,
    "rotation_failure": 0, "uptime_seconds": 0,
}


@router.get("/summary")
async def get_summary(request: Request):
    # store 在 lifespan 中始终实例化；这里仍做防御性 getattr，避免任何未初始化场景 500。
    store = getattr(request.app.state, "usage_stats_store", None)
    if store is None:
        return dict(_EMPTY_SUMMARY)
    return store.get_summary()


@router.get("/history")
async def get_history(
    request: Request,
    granularity: str = Query("hourly", pattern="^(raw|five_min|hourly|daily)$"),
    # hours 限定为 "all" 或正整数，避免非数字值（?hours=abc / 空 / 1.5）触发未捕获 ValueError → 500
    # （修复 #15/#26）。Query 的 regex 在到达处理函数前即返回 422。
    hours: str = Query("24", pattern=r"^(all|[1-9]\d*)$"),
):
    store = getattr(request.app.state, "usage_stats_store", None)
    if store is None:
        return []
    try:
        h = None if hours == "all" else int(hours)
    except ValueError:
        # regex 已挡住非法值，这里仅作纵深防御。
        raise HTTPException(status_code=400, detail="invalid hours")
    return store.get_history(granularity=granularity, hours=h)
