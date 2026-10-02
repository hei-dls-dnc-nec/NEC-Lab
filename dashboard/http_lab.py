import json
import random
import time
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from dashboard.state import state

router = APIRouter(prefix="/api/lab", tags=["HTTP Traffic Lab"])


def get_padding_size(size_label: str) -> int:
    sizes = {
        "small": 0,           # ~150 B (single standard TCP segment)
        "medium": 1024,       # ~1.2 KB
        "large": 10 * 1024,   # ~10 KB (multi-packet TCP stream, exceeds standard 1460 MSS)
        "jumbo": 64 * 1024,   # ~64 KB (TCP windowing & IP fragmentation demo)
    }
    return sizes.get(size_label.lower(), 0)


@router.get("/traffic")
async def get_traffic(
    request: Request,
    size: str = "small",
    keep_alive: bool = True,
):
    """
    HTTP GET endpoint for Wireshark traffic inspection.
    Supports adjustable response payload sizes to demonstrate TCP segmentation and IP fragmentation.
    """
    client_host = request.client.host if request.client else "unknown"
    padding_bytes = get_padding_size(size)

    payload = {
        "type": "server_telemetry",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cpu_load_pct": round(random.uniform(5.0, 35.0), 1),
        "memory_load_pct": round(random.uniform(40.0, 70.0), 1),
        "requested_size": size,
    }
    if padding_bytes > 0:
        payload["padding"] = "X" * padding_bytes

    body_bytes = json.dumps(payload).encode("utf-8")
    content_len = len(body_bytes)

    # Update state telemetry
    state.stats["http_rx_responses"] += 1
    state.stats["http_rx_bytes"] += content_len

    # Log incoming GET
    state.add_log_entry(
        proto="HTTP",
        direction="RX",
        summary=f"GET /api/lab/traffic?size={size}",
        peer=client_host,
        details={
            "method": "GET",
            "size": size,
            "status": 200,
            "bytes_out": content_len,
            "keep_alive": keep_alive,
        },
    )

    headers = {
        "Content-Type": "application/json",
        "Content-Length": str(content_len),
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Connection": "keep-alive" if keep_alive else "close",
    }

    return Response(content=body_bytes, status_code=200, headers=headers)


@router.post("/traffic")
async def post_traffic(
    request: Request,
    size: str = "small",
    keep_alive: bool = True,
):
    """
    HTTP POST endpoint for Wireshark traffic inspection.
    Accepts client payload and responds with designated payload size.
    """
    client_host = request.client.host if request.client else "unknown"
    req_body = await request.body()
    in_bytes = len(req_body)

    state.stats["http_tx_requests"] += 1
    state.stats["http_tx_bytes"] += in_bytes

    padding_bytes = get_padding_size(size)
    payload = {
        "status": "acknowledged",
        "received_bytes": in_bytes,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "requested_response_size": size,
    }
    if padding_bytes > 0:
        payload["padding"] = "Y" * padding_bytes

    body_bytes = json.dumps(payload).encode("utf-8")
    out_bytes = len(body_bytes)

    state.stats["http_rx_responses"] += 1
    state.stats["http_rx_bytes"] += out_bytes

    state.add_log_entry(
        proto="HTTP",
        direction="RX",
        summary=f"POST /api/lab/traffic (In: {in_bytes} B, Out: {out_bytes} B)",
        peer=client_host,
        details={
            "method": "POST",
            "bytes_in": in_bytes,
            "bytes_out": out_bytes,
            "status": 200,
            "keep_alive": keep_alive,
        },
    )

    headers = {
        "Content-Type": "application/json",
        "Content-Length": str(out_bytes),
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Connection": "keep-alive" if keep_alive else "close",
    }

    return Response(content=body_bytes, status_code=200, headers=headers)
