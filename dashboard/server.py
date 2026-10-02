import argparse
import asyncio
import json
import os
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, Any, Optional

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from dashboard.config import (
    DEFAULT_HTTP_PORT,
    DEFAULT_MODBUS_PORT,
)
from dashboard.state import state, PRIMARY_LAN_IP, ALL_LAN_IPS
from dashboard.modbus_engine import (
    ModbusEngine,
    trigger_manual_write,
    trigger_manual_query,
)
from dashboard.http_lab import router as http_lab_router

# Static assets directory
STATIC_DIR = Path(__file__).resolve().parent / "static"

# Global modbus engine reference
modbus_engine_instance: Optional[ModbusEngine] = None


async def websocket_broadcast_task():
    """Worker task that dispatches state events and new logs to all connected WebSockets."""
    while True:
        try:
            event = await state._event_queue.get()
            if not state.active_websockets:
                state._event_queue.task_done()
                continue

            msg_text = json.dumps(event)
            dead_sockets = set()
            for ws in list(state.active_websockets):
                try:
                    await ws.send_text(msg_text)
                except Exception:
                    dead_sockets.add(ws)

            for ws in dead_sockets:
                state.active_websockets.discard(ws)

            state._event_queue.task_done()
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(0.05)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: configure event loop in state and launch broadcaster
    loop = asyncio.get_running_loop()
    state.set_event_loop(loop)
    broadcast_task = asyncio.create_task(websocket_broadcast_task())

    # Start Modbus Engine if configured
    global modbus_engine_instance
    modbus_port = getattr(app.state, "modbus_port", DEFAULT_MODBUS_PORT)
    slave_enabled = getattr(app.state, "slave_enabled", True)
    poller_enabled = getattr(app.state, "poller_enabled", True)

    state.master_config["polling_active"] = poller_enabled
    target_host = getattr(app.state, "target_host", "127.0.0.1")
    state.master_config["target_host"] = target_host
    state.master_config["target_port"] = modbus_port

    if slave_enabled:
        modbus_engine_instance = ModbusEngine(port=modbus_port)
        modbus_engine_instance.start()

    yield

    # Shutdown
    broadcast_task.cancel()
    if modbus_engine_instance:
        modbus_engine_instance.stop()


app = FastAPI(
    title="NEC Protocol Workbench",
    description="Unified HTTP & Modbus TCP SCADA Protocol Lab",
    version="1.0.0",
    lifespan=lifespan,
)

# Include HTTP traffic router
app.include_router(http_lab_router)


# --- REST API Endpoints ---

@app.get("/api/status")
async def get_status():
    """Returns complete state snapshot: coils, registers, statistics, poller configuration."""
    return state.get_full_status()


class MasterConfigUpdate(BaseModel):
    polling_active: Optional[bool] = None
    interval: Optional[float] = None
    function_code: Optional[int] = None
    connection_mode: Optional[str] = None
    target_host: Optional[str] = None
    target_port: Optional[int] = None


@app.post("/api/modbus/config")
async def update_master_config(cfg: MasterConfigUpdate):
    """Updates master poller configuration dynamically."""
    with state._lock:
        if cfg.polling_active is not None:
            state.master_config["polling_active"] = bool(cfg.polling_active)
        if cfg.interval is not None:
            state.master_config["interval"] = max(0.1, min(10.0, float(cfg.interval)))
        if cfg.function_code is not None:
            state.master_config["function_code"] = int(cfg.function_code)
        if cfg.connection_mode is not None:
            state.master_config["connection_mode"] = str(cfg.connection_mode)
        if cfg.target_host is not None and cfg.target_host.strip():
            state.master_config["target_host"] = cfg.target_host.strip()
        if cfg.target_port is not None and cfg.target_port > 0:
            state.master_config["target_port"] = int(cfg.target_port)

    state.broadcast_state_update()
    return {"status": "updated", "config": state.master_config}


class WriteCoilRequest(BaseModel):
    index: int
    value: Optional[bool] = None  # None = toggle current


@app.post("/api/modbus/write_coil")
async def write_coil(req: WriteCoilRequest):
    """Writes a single coil value (FC05) locally or to target slave."""
    if not (0 <= req.index < len(state.coils)):
        raise HTTPException(status_code=400, detail="Invalid coil index")

    target_val = not state.coils[req.index] if req.value is None else bool(req.value)

    # Trigger wire write via thread
    target_host = state.master_config.get("target_host", "127.0.0.1")
    target_port = state.master_config.get("target_port", DEFAULT_MODBUS_PORT)
    val_int = 0xFF00 if target_val else 0x0000

    threading.Thread(
        target=trigger_manual_write,
        args=(5, req.index, val_int, target_host, target_port),
        daemon=True,
    ).start()

    return {"status": "dispatched", "index": req.index, "value": target_val}


class WriteRegisterRequest(BaseModel):
    index: int
    value: int


@app.post("/api/modbus/write_register")
async def write_register(req: WriteRegisterRequest):
    """Writes a single 16-bit register value (FC06)."""
    if not (0 <= req.index < len(state.holding_registers)):
        raise HTTPException(status_code=400, detail="Invalid register index")
    if not (0 <= req.value <= 65535):
        raise HTTPException(status_code=400, detail="Value must be 16-bit unsigned (0..65535)")

    target_host = state.master_config.get("target_host", "127.0.0.1")
    target_port = state.master_config.get("target_port", DEFAULT_MODBUS_PORT)

    threading.Thread(
        target=trigger_manual_write,
        args=(6, req.index, req.value, target_host, target_port),
        daemon=True,
    ).start()

    return {"status": "dispatched", "index": req.index, "value": req.value}


class ManualQueryRequest(BaseModel):
    fc: int = 3
    start_addr: int = 0
    quantity: int = 10


@app.post("/api/modbus/manual_query")
async def manual_query(req: ManualQueryRequest):
    """Executes a one-off Modbus query (FC01 or FC03)."""
    target_host = state.master_config.get("target_host", "127.0.0.1")
    target_port = state.master_config.get("target_port", DEFAULT_MODBUS_PORT)

    threading.Thread(
        target=trigger_manual_query,
        args=(req.fc, req.start_addr, req.quantity, target_host, target_port),
        daemon=True,
    ).start()

    return {"status": "dispatched", "target": f"{target_host}:{target_port}"}


@app.post("/api/logs/clear")
async def clear_logs():
    """Clears the in-memory circular event log."""
    with state._lock:
        state.recent_logs.clear()
    state.broadcast_state_update()
    return {"status": "cleared"}


@app.get("/api/logs")
async def get_logs():
    """Returns the circular buffer of recent protocol events."""
    with state._lock:
        return list(state.recent_logs)


# --- WebSocket Real-Time Stream ---

@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
    """Real-time bidirectional event feed for packet frames, state updates, and metrics."""
    await websocket.accept()
    state.active_websockets.add(websocket)

    try:
        # Send initial full state and recent logs
        with state._lock:
            init_payload = {
                "type": "init",
                "state": state.get_full_status(),
                "logs": list(state.recent_logs),
            }
        await websocket.send_text(json.dumps(init_payload))

        while True:
            # Client can send ping or commands
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text('{"type":"pong"}')
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        state.active_websockets.discard(websocket)


# Mount static assets
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    async def serve_index():
        return FileResponse(STATIC_DIR / "index.html")


def main():
    parser = argparse.ArgumentParser(
        description="NEC Protocol Workbench - Unified HTTP & Modbus TCP SCADA Lab"
    )
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Binding host IP (default: 0.0.0.0)")
    parser.add_argument("--http-port", type=int, default=DEFAULT_HTTP_PORT, help="HTTP Dashboard port (default: 8050)")
    parser.add_argument("--modbus-port", type=int, default=DEFAULT_MODBUS_PORT, help="Modbus TCP port (default: 1502)")
    parser.add_argument("--target", type=str, default="127.0.0.1", help="Target Modbus Slave IP for the Master (default: 127.0.0.1)")
    parser.add_argument("--slave-only", action="store_true", help="Run Modbus Slave & Web Dashboard only (disable poller)")
    parser.add_argument("--master-only", action="store_true", help="Run Modbus Master poller only (disable slave)")
    parser.add_argument("--no-poller", action="store_true", help="Start with master polling paused")
    args = parser.parse_args()

    # Pass args to app state
    app.state.modbus_port = args.modbus_port
    app.state.slave_enabled = not args.master_only
    app.state.poller_enabled = not (args.slave_only or args.no_poller)
    app.state.target_host = args.target

    # Clean technical startup banner
    print("\n" + "=" * 70)
    print("  NEC PROTOCOL WORKBENCH (HTTP & MODBUS TCP SCADA LAB)")
    print("=" * 70)
    print("  Endpoints:")
    print(f"    • Web Dashboard:    http://localhost:{args.http_port} (or http://{PRIMARY_LAN_IP}:{args.http_port})")
    if not args.master_only:
        print(f"    • Modbus TCP Slave: 0.0.0.0:{args.modbus_port} (Local: 127.0.0.1:{args.modbus_port})")
    print(f"    • Master Poller:    Target -> {args.target}:{args.modbus_port}")
    if len(ALL_LAN_IPS) > 1:
        print(f"    • Detected LAN IPs: {', '.join(ALL_LAN_IPS)}")
    print("=" * 70 + "\n")

    uvicorn.run(app, host=args.host, port=args.http_port, log_level="warning")


if __name__ == "__main__":
    main()
