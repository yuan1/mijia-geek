"""日志接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from api.deps import require_gateway
from api.utils import gateway_http_error
from protocol import MijiaGateway

router = APIRouter()


@router.get("/api/logs/all")
async def api_logs_all(
    pages: int = Query(3, ge=1, le=20),
    gateway: MijiaGateway = Depends(require_gateway),
):
    chunks: list[str] = []
    try:
        for page in range(pages):
            chunk = await gateway.get_log(page)
            if not chunk:
                break
            chunks.append(str(chunk))
        return {"pages": len(chunks), "requested": pages,
                "log": "\n".join(chunks)}
    except Exception as err:
        raise gateway_http_error(err) from err


@router.get("/api/logs")
async def api_logs(
    page: int = Query(0, ge=0, le=1000),
    gateway: MijiaGateway = Depends(require_gateway),
):
    try:
        log = await gateway.get_log(page)
        return {"page": page, "log": log or ""}
    except Exception as err:
        raise gateway_http_error(err) from err
