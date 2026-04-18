"""Web UI路由模块 - 链路可视化面板数据API

实现链路可视化面板的Web UI数据接口，提供时间线聚合、状态分布、链路拓扑等可视化数据。
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel, Field

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.ids import new_request_id
from host_brain.app.main import (
    build_webui_timeline_aggregation,
    build_webui_status_distribution,
    build_webui_request_detail,
    build_webui_link_topology,
    search_webui_requests
)

logger = logging.getLogger("Natsume.web_ui")
_SEARCH_REQUESTS_CACHE_TTL_MS = max(
    int(os.getenv("OBS_SEARCH_REQUESTS_CACHE_TTL_MS", "3000") or 3000),
    0,
)

# Web UI API路由
router = APIRouter(prefix="/ui", tags=["web-ui"])

# 数据模型定义
class TimelineAggregationResponse(BaseModel):
    """时间线聚合查询响应"""
    request_id: str = Field(..., description="请求ID")
    start_time: int = Field(..., description="开始时间戳(毫秒)")
    end_time: int = Field(..., description="结束时间戳(毫秒)")
    interval: str = Field(..., description="聚合间隔")
    intervals: List[Dict[str, Any]] = Field(..., description="时间间隔数据")
    total_requests: int = Field(..., description="总请求数")
    success_rate: float = Field(..., description="成功率")
    avg_duration: float = Field(..., description="平均耗时")
    timestamp: int = Field(..., description="响应时间戳(毫秒)")

class StatusDistributionResponse(BaseModel):
    """状态分布统计响应"""
    request_id: str = Field(..., description="请求ID")
    start_time: int = Field(..., description="开始时间戳(毫秒)")
    end_time: int = Field(..., description="结束时间戳(毫秒)")
    status_counts: Dict[str, int] = Field(..., description="状态计数")
    total_requests: int = Field(..., description="总请求数")
    distribution_percentage: Dict[str, float] = Field(..., description="分布百分比")
    timestamp: int = Field(..., description="响应时间戳(毫秒)")

class RequestDetailResponse(BaseModel):
    """请求详情响应"""
    request_id: str = Field(..., description="请求ID")
    timestamp: int = Field(..., description="请求时间戳(毫秒)")
    status: str = Field(..., description="状态")
    duration: float = Field(..., description="耗时(毫秒)")
    request_type: str = Field(..., description="请求类型")
    user_id: Optional[str] = Field(None, description="用户ID")
    error_message: Optional[str] = Field(None, description="错误信息")
    trace_data: Dict[str, Any] = Field(..., description="追踪数据")
    query_timestamp: int = Field(..., description="查询时间戳(毫秒)")

class LinkTopologyResponse(BaseModel):
    """链路拓扑响应"""
    request_id: str = Field(..., description="请求ID")
    start_time: int = Field(..., description="开始时间戳(毫秒)")
    end_time: int = Field(..., description="结束时间戳(毫秒)")
    nodes: List[Dict[str, Any]] = Field(..., description="节点列表")
    edges: List[Dict[str, Any]] = Field(..., description="边列表")
    clusters: List[Dict[str, Any]] = Field(..., description="集群列表")
    timestamp: int = Field(..., description="响应时间戳(毫秒)")

def get_ledger() -> SQLiteLedger:
    """获取SQLite账本实例"""
    from host_brain.app.dependencies import get_ledger as get_app_ledger
    return get_app_ledger()

@router.get("/timeline", response_model=TimelineAggregationResponse)
def get_timeline_aggregation(
    start_time: datetime = Query(..., description="开始时间"),
    end_time: datetime = Query(..., description="结束时间"),
    interval: str = Query("1h", description="聚合间隔: 1m,5m,15m,1h,1d"),
    status: Optional[str] = Query(None, description="状态过滤"),
    request_type: Optional[str] = Query(None, description="请求类型过滤"),
    limit: int = Query(100, ge=1, le=10000, description="最大返回记录数"),
    offset: int = Query(0, ge=0, description="分页偏移量"),
):
    """获取时间线聚合数据"""
    try:
        ledger = get_ledger()

        # 构建过滤条件
        filters = {}
        if status:
            filters["status"] = status.split(",")
        if request_type:
            filters["request_type"] = request_type.split(",")

        # 转换为时间戳
        start_ts = int(start_time.timestamp() * 1000)
        end_ts = int(end_time.timestamp() * 1000)

        # 查询时间线聚合数据
        timeline_data = build_webui_timeline_aggregation(
            ledger=ledger,
            start_time=start_ts,
            end_time=end_ts,
            interval=interval,
            filters=filters
        )

        return TimelineAggregationResponse(**timeline_data)

    except Exception as e:
        logger.error(f"Failed to get timeline aggregation: {e}")
        raise HTTPException(status_code=500, detail="Failed to get timeline data")

@router.get("/status-distribution", response_model=StatusDistributionResponse)
def get_status_distribution(
    start_time: datetime = Query(..., description="开始时间"),
    end_time: datetime = Query(..., description="结束时间"),
    request_type: Optional[str] = Query(None, description="请求类型过滤"),
    limit: int = Query(100, ge=1, le=10000, description="最大返回记录数"),
    offset: int = Query(0, ge=0, description="分页偏移量"),
):
    """获取状态分布统计数据"""
    try:
        ledger = get_ledger()

        filters = {}
        if request_type:
            filters["request_type"] = request_type.split(",")

        # 转换为时间戳
        start_ts = int(start_time.timestamp() * 1000)
        end_ts = int(end_time.timestamp() * 1000)

        distribution_data = build_webui_status_distribution(
            ledger=ledger,
            start_time=start_ts,
            end_time=end_ts,
            filters=filters
        )

        return StatusDistributionResponse(**distribution_data)

    except Exception as e:
        logger.error(f"Failed to get status distribution: {e}")
        raise HTTPException(status_code=500, detail="Failed to get status distribution data")

@router.get("/request/{request_id}", response_model=RequestDetailResponse)
def get_request_detail(request_id: str) -> dict:
    """获取请求详情"""
    try:
        ledger = get_ledger()

        request_detail = build_webui_request_detail(
            ledger=ledger,
            request_id=request_id
        )

        if not request_detail:
            raise HTTPException(status_code=404, detail="Request not found")

        return RequestDetailResponse(**request_detail)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get request detail: {e}")
        raise HTTPException(status_code=500, detail="Failed to get request detail")

@router.get("/link-topology", response_model=LinkTopologyResponse)
def get_link_topology(
    start_time: datetime = Query(..., description="开始时间"),
    end_time: datetime = Query(..., description="结束时间"),
    min_connections: int = Query(1, description="最小连接数阈值"),
    limit: int = Query(100, ge=1, le=10000, description="最大返回记录数"),
    offset: int = Query(0, ge=0, description="分页偏移量"),
):
    """获取链路拓扑数据"""
    try:
        ledger = get_ledger()

        # 转换为时间戳
        start_ts = int(start_time.timestamp() * 1000)
        end_ts = int(end_time.timestamp() * 1000)

        topology_data = build_webui_link_topology(
            ledger=ledger,
            start_time=start_ts,
            end_time=end_ts,
            min_connections=min_connections
        )

        return LinkTopologyResponse(**topology_data)

    except Exception as e:
        logger.error(f"Failed to get link topology: {e}")
        raise HTTPException(status_code=500, detail="Failed to get link topology data")

@router.get("/search-requests")
def search_requests(
    query: str = Query(..., description="搜索关键词"),
    start_time: datetime = Query(..., description="开始时间"),
    end_time: datetime = Query(..., description="结束时间"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页大小"),
    limit: int = Query(100, ge=1, le=10000, description="最大返回记录数"),
    offset: int = Query(0, ge=0, description="分页偏移量"),
):
    """搜索请求记录"""
    try:
        from host_brain.app.obs_routes_core import _get_obs_route_cached_response

        def _build_response() -> Dict[str, Any]:
            ledger = get_ledger()

            # 转换为时间戳
            start_ts = int(start_time.timestamp() * 1000)
            end_ts = int(end_time.timestamp() * 1000)

            search_results = search_webui_requests(
                ledger=ledger,
                query=query,
                start_time=start_ts,
                end_time=end_ts,
                page=page,
                page_size=page_size
            )

            return {
                "results": search_results["results"],
                "total": search_results["total"],
                "page": page,
                "page_size": page_size
            }

        return _get_obs_route_cached_response(
            cache_name="webui_search_requests",
            cache_key={
                "query": query,
                "start_time": int(start_time.timestamp() * 1000),
                "end_time": int(end_time.timestamp() * 1000),
                "page": int(page),
                "page_size": int(page_size),
            },
            ttl_ms=_SEARCH_REQUESTS_CACHE_TTL_MS,
            builder=_build_response,
        )

    except Exception as e:
        logger.error(f"Failed to search requests: {e}")
        raise HTTPException(status_code=500, detail="Failed to search requests")
