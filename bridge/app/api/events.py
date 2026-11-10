"""SSE 推送事件接口。"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from api.deps import get_service
from service import GatewayService

router = APIRouter()


@router.get("/api/events")
async def api_events(
    request: Request,
    service: GatewayService = Depends(get_service),
):
    state = service.state
    queue = state.subscribe()
    ready_seq = state.push_seq
    ready_connected = state.connected

    async def event_stream():
        try:
            yield "retry: 5000\n\n"
            ready = {
                "seq": ready_seq,
                "kind": "ready",
                "connected": ready_connected,
            }
            yield f"data: {json.dumps(ready, ensure_ascii=False)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                payload = json.dumps(event, ensure_ascii=False, default=str)
                yield f"data: {payload}\n\n"
        finally:
            state.unsubscribe(queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
