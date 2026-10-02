import asyncio
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Set
from fastapi import WebSocket

from dashboard.config import (
    DEFAULT_MODBUS_PORT,
    DEFAULT_HTTP_PORT,
    DEFAULT_HTTP_POLLER_ACTIVE,
    DEFAULT_MODBUS_POLLER_ACTIVE,
    COIL_DEFINITIONS,
    REGISTER_DEFINITIONS,
    get_local_ips,
)

PRIMARY_LAN_IP, ALL_LAN_IPS = get_local_ips()


class AppState:
    def __init__(self):
        self._lock = threading.RLock()
        self.log_counter = 0

        # Modbus PLC Datastore
        self.coils: List[bool] = [True, False, True, True, False, False, True, False, True, False]
        self.holding_registers: List[int] = [120, 230, 1013, 1450, 45, 500, 498, 0, 128, 4]

        # Connected LAN clients on Modbus port
        self.active_modbus_clients: Set[str] = set()

        # Telemetry Stats
        self.stats: Dict[str, Any] = {
            "http_tx_requests": 0,
            "http_tx_bytes": 0,
            "http_rx_responses": 0,
            "http_rx_bytes": 0,
            "modbus_transactions": 0,
            "modbus_bytes_tx": 0,
            "modbus_bytes_rx": 0,
            "modbus_connections_opened": 0,
            "modbus_active_connections": 0,
            "modbus_connected_clients": [],
        }

        self.default_http_poller_active = DEFAULT_HTTP_POLLER_ACTIVE
        self.default_modbus_poller_active = DEFAULT_MODBUS_POLLER_ACTIVE

        # Modbus Master Poller Configuration
        self.master_config: Dict[str, Any] = {
            "polling_active": DEFAULT_MODBUS_POLLER_ACTIVE,
            "interval": 1.0,
            "function_code": 3,  # 3 = FC03 Read Holding Registers, 1 = FC01 Read Coils
            "connection_mode": "keep-alive",  # "keep-alive" or "transient"
            "target_host": "127.0.0.1",
            "target_port": DEFAULT_MODBUS_PORT,
        }

        # Circular buffer for recent logs (keep last 200 events)
        self.recent_logs: deque = deque(maxlen=200)

        # Async WebSocket distribution
        self.active_websockets: Set[WebSocket] = set()
        self.async_loop: Optional[asyncio.AbstractEventLoop] = None
        self._event_queue: asyncio.Queue = None

    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        self.async_loop = loop
        self._event_queue = asyncio.Queue()

    def add_log_entry(
        self,
        proto: str,  # "HTTP" or "MODBUS"
        direction: str,  # "TX" or "RX"
        summary: str,
        peer: str = "local",
        details: Optional[Dict[str, Any]] = None,
        raw_hex: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Thread-safe append of a protocol transaction to log and queue for WebSocket broadcast."""
        with self._lock:
            self.log_counter += 1
            entry = {
                "id": self.log_counter,
                "time": datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3],
                "proto": proto.upper(),
                "dir": direction.upper(),
                "peer": peer,
                "summary": summary,
                "details": details or {},
                "raw_hex": raw_hex or "",
            }
            self.recent_logs.append(entry)

        # Notify async WebSocket broadcaster
        if self.async_loop and self.async_loop.is_running() and self._event_queue:
            self.async_loop.call_soon_threadsafe(
                self._event_queue.put_nowait,
                {"type": "log", "entry": entry}
            )

        return entry

    def broadcast_state_update(self):
        """Notify connected web clients about updated registers, coils, and stats."""
        if not (self.async_loop and self.async_loop.is_running() and self._event_queue):
            return

        state_snapshot = self.get_full_status()

        self.async_loop.call_soon_threadsafe(
            self._event_queue.put_nowait,
            {"type": "state", "payload": state_snapshot}
        )

    def register_modbus_client_connected(self, peer: str):
        with self._lock:
            self.active_modbus_clients.add(peer)
            self.stats["active_connections"] = len(self.active_modbus_clients)
            self.stats["modbus_connected_clients"] = sorted(self.active_modbus_clients)
            self.stats["modbus_connections_opened"] += 1
        self.broadcast_state_update()

    def register_modbus_client_disconnected(self, peer: str):
        with self._lock:
            self.active_modbus_clients.discard(peer)
            self.stats["active_connections"] = len(self.active_modbus_clients)
            self.stats["modbus_connected_clients"] = sorted(self.active_modbus_clients)
        self.broadcast_state_update()

    def get_full_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "coils": [
                    {
                        "addr": idx,
                        "name": COIL_DEFINITIONS[idx]["name"] if idx < len(COIL_DEFINITIONS) else f"Coil {idx+1}",
                        "desc": COIL_DEFINITIONS[idx]["desc"] if idx < len(COIL_DEFINITIONS) else "",
                        "value": bool(self.coils[idx]),
                    }
                    for idx in range(len(self.coils))
                ],
                "registers": [
                    {
                        "addr": idx,
                        "display_addr": 40001 + idx,
                        "hex_addr": f"0x{idx:04X}",
                        "name": REGISTER_DEFINITIONS[idx]["name"] if idx < len(REGISTER_DEFINITIONS) else f"Reg {idx+1}",
                        "unit": REGISTER_DEFINITIONS[idx]["unit"] if idx < len(REGISTER_DEFINITIONS) else "",
                        "desc": REGISTER_DEFINITIONS[idx]["desc"] if idx < len(REGISTER_DEFINITIONS) else "",
                        "value": int(self.holding_registers[idx]),
                    }
                    for idx in range(len(self.holding_registers))
                ],
                "stats": dict(self.stats),
                "master_config": dict(self.master_config),
                "server_info": {
                    "primary_ip": PRIMARY_LAN_IP,
                    "all_ips": ALL_LAN_IPS,
                    "http_port": DEFAULT_HTTP_PORT,
                    "modbus_port": DEFAULT_MODBUS_PORT,
                    "default_http_poller_active": self.default_http_poller_active,
                    "default_modbus_poller_active": self.default_modbus_poller_active,
                },
            }


state = AppState()
