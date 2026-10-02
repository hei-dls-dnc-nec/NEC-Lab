import random
import socket
import socketserver
import struct
import sys
import threading
import time
from typing import Optional, List, Tuple

from dashboard.state import state, PRIMARY_LAN_IP
from dashboard.config import DEFAULT_MODBUS_PORT


def to_hex_spaced(data_bytes: bytes) -> str:
    """Format raw binary bytes into space-separated hexadecimal."""
    return " ".join(f"{b:02X}" for b in data_bytes)


def recv_all(sock: socket.socket, num_bytes: int) -> Optional[bytes]:
    """Safely receive exactly num_bytes from a TCP stream."""
    buf = bytearray()
    while len(buf) < num_bytes:
        try:
            chunk = sock.recv(num_bytes - len(buf))
        except (OSError, socket.timeout):
            return None
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def pack_coils(bool_list: List[bool]) -> bytes:
    """Pack a list of booleans into packed byte representation for Modbus FC1/FC2."""
    byte_count = (len(bool_list) + 7) // 8
    packed = bytearray(byte_count)
    for i, val in enumerate(bool_list):
        if val:
            packed[i // 8] |= (1 << (i % 8))
    return bytes(packed)


class ModbusTCPSlaveHandler(socketserver.BaseRequestHandler):
    """
    Standard-compliant Modbus TCP Slave Server (PLC Simulator).
    Handles FC01, FC02, FC03, FC04, FC05, FC06, FC15, FC16.
    """

    def handle(self):
        client_address = f"{self.client_address[0]}:{self.client_address[1]}"
        state.register_modbus_client_connected(client_address)

        try:
            while True:
                # Modbus TCP Header (MBAP) is exactly 7 bytes:
                # TransID (2), ProtoID (2), Length (2), UnitID (1)
                mbap = recv_all(self.request, 7)
                if not mbap or len(mbap) < 7:
                    break  # Client closed connection

                state.stats["modbus_bytes_rx"] += len(mbap)
                trans_id, proto_id, length, unit_id = struct.unpack('>HHHB', mbap)

                # Length field includes UnitID (1 byte) + PDU size
                pdu_len = length - 1
                if pdu_len < 0:
                    break

                pdu = recv_all(self.request, pdu_len)
                if not pdu or len(pdu) < pdu_len:
                    break

                state.stats["modbus_bytes_rx"] += len(pdu)
                func_code = pdu[0]
                resp_pdu = b""
                pdu_desc = ""

                # --- FC01: Read Coils & FC02: Read Discrete Inputs ---
                if func_code in (1, 2):
                    start_addr, quantity = struct.unpack('>HH', pdu[1:5])
                    with state._lock:
                        slice_coils = state.coils[start_addr : start_addr + quantity]
                    packed = pack_coils(slice_coils)
                    resp_pdu = struct.pack('>BB', func_code, len(packed)) + packed
                    pdu_desc = f"Read Coils 0000{start_addr+1}..0000{start_addr+quantity} (Qty: {quantity})"

                # --- FC03: Read Holding Registers & FC04: Read Input Registers ---
                elif func_code in (3, 4):
                    start_addr, quantity = struct.unpack('>HH', pdu[1:5])
                    with state._lock:
                        slice_regs = state.holding_registers[start_addr : start_addr + quantity]
                    resp_pdu = struct.pack('>BB', func_code, 2 * quantity) + b"".join(
                        struct.pack('>H', r) for r in slice_regs
                    )
                    pdu_desc = f"Read Holding Regs 40001+{start_addr}..40001+{start_addr+quantity-1} (Qty: {quantity})"

                # --- FC05: Write Single Coil ---
                elif func_code == 5:
                    addr, val = struct.unpack('>HH', pdu[1:5])
                    coil_val = (val == 0xFF00)
                    with state._lock:
                        if 0 <= addr < len(state.coils):
                            state.coils[addr] = coil_val
                    resp_pdu = struct.pack('>BHH', 5, addr, val)  # Echo back
                    pdu_desc = f"Write Single Coil 0000{addr+1} = {'ON' if coil_val else 'OFF'}"
                    state.broadcast_state_update()

                # --- FC06: Write Single Register ---
                elif func_code == 6:
                    addr, val = struct.unpack('>HH', pdu[1:5])
                    with state._lock:
                        if 0 <= addr < len(state.holding_registers):
                            state.holding_registers[addr] = val
                    resp_pdu = struct.pack('>BHH', 6, addr, val)  # Echo back
                    pdu_desc = f"Write Single Register 40001+{addr} = {val}"
                    state.broadcast_state_update()

                # --- FC15: Write Multiple Coils ---
                elif func_code == 15:
                    start_addr, quantity, byte_count = struct.unpack('>HHB', pdu[1:6])
                    data_bytes = pdu[6 : 6 + byte_count]
                    with state._lock:
                        for i in range(quantity):
                            byte_idx = i // 8
                            bit_idx = i % 8
                            if byte_idx < len(data_bytes):
                                bit_val = bool(data_bytes[byte_idx] & (1 << bit_idx))
                                if 0 <= (start_addr + i) < len(state.coils):
                                    state.coils[start_addr + i] = bit_val
                    resp_pdu = struct.pack('>BHH', 15, start_addr, quantity)
                    pdu_desc = f"Write Multiple Coils 0000{start_addr+1} (Qty: {quantity})"
                    state.broadcast_state_update()

                # --- FC16: Write Multiple Registers ---
                elif func_code == 16:
                    start_addr, quantity, _byte_count = struct.unpack('>HHB', pdu[1:6])
                    values = []
                    with state._lock:
                        for i in range(quantity):
                            val = struct.unpack('>H', pdu[6 + 2*i : 8 + 2*i])[0]
                            values.append(val)
                            if 0 <= (start_addr + i) < len(state.holding_registers):
                                state.holding_registers[start_addr + i] = val
                    resp_pdu = struct.pack('>BHH', 16, start_addr, quantity)
                    pdu_desc = f"Write Multiple Regs 40001+{start_addr} (Qty: {quantity}) = {values}"
                    state.broadcast_state_update()

                # --- Exception: Illegal Function Code (01) ---
                else:
                    resp_pdu = struct.pack('>BB', func_code + 0x80, 1)
                    pdu_desc = f"Illegal Modbus Function Code Exception: {func_code}"

                # Construct Response MBAP Header
                resp_len = len(resp_pdu) + 1  # 1 for unit ID
                resp_mbap = struct.pack('>HHHB', trans_id, proto_id, resp_len, unit_id)
                response = resp_mbap + resp_pdu

                # Log incoming request from client
                state.add_log_entry(
                    proto="MODBUS",
                    direction="RX",
                    summary=f"Slave RX: {pdu_desc}",
                    peer=client_address,
                    details={
                        "trans_id": trans_id,
                        "proto_id": proto_id,
                        "unit_id": unit_id,
                        "fc": func_code,
                        "role": "slave_rx",
                    },
                    raw_hex=to_hex_spaced(mbap + pdu),
                )

                # Send response back to Client
                self.request.sendall(response)
                state.stats["modbus_bytes_tx"] += len(response)
                state.stats["modbus_transactions"] += 1

                # Log outgoing response to client
                state.add_log_entry(
                    proto="MODBUS",
                    direction="TX",
                    summary=f"Slave TX: Reply {pdu_desc}",
                    peer=client_address,
                    details={
                        "trans_id": trans_id,
                        "unit_id": unit_id,
                        "len": resp_len,
                        "role": "slave_tx",
                    },
                    raw_hex=to_hex_spaced(response),
                )

        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            state.register_modbus_client_disconnected(client_address)


class ModbusTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class ModbusEngine:
    def __init__(self, port: int = DEFAULT_MODBUS_PORT):
        self.port = port
        self.server: Optional[ModbusTCPServer] = None
        self.server_thread: Optional[threading.Thread] = None
        self.poller_thread: Optional[threading.Thread] = None
        self.simulation_thread: Optional[threading.Thread] = None
        self.running = False

    def start(self):
        self.running = True

        # 1. Start Modbus Slave Server
        try:
            self.server = ModbusTCPServer(("0.0.0.0", self.port), ModbusTCPSlaveHandler)
            self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.server_thread.start()
            print(f"[+] Modbus TCP Server listening on 0.0.0.0:{self.port}")
        except Exception as e:
            print(f"[!] Failed to bind Modbus TCP server on port {self.port}: {e}", file=sys.stderr)
            raise

        # 2. Start Master Poller background thread
        self.poller_thread = threading.Thread(target=self._master_poller_loop, daemon=True)
        self.poller_thread.start()

        # 3. Start Telemetry simulation drift
        self.simulation_thread = threading.Thread(target=self._telemetry_simulation_loop, daemon=True)
        self.simulation_thread.start()

    def stop(self):
        self.running = False
        if self.server:
            self.server.shutdown()

    def _telemetry_simulation_loop(self):
        """Simulates subtle real-world sensor fluctuations in registers 0-2 if pointing locally."""
        while self.running:
            time.sleep(1.5)
            target = state.master_config.get("target_host", "127.0.0.1")
            if target in ("127.0.0.1", "localhost", PRIMARY_LAN_IP):
                with state._lock:
                    state.holding_registers[0] = (state.holding_registers[0] + 1) % 65535  # Tick counter
                    state.holding_registers[1] = max(220, min(240, int(230 + random.uniform(-3, 3))))  # AC Voltage
                    state.holding_registers[2] = max(980, min(1050, int(1013 + random.uniform(-6, 6))))  # Pressure
                state.broadcast_state_update()

    def _master_poller_loop(self):
        """Background Modbus Master client polling engine."""
        trans_id = 0
        client_socket: Optional[socket.socket] = None
        current_target: Optional[Tuple[str, int]] = None

        while self.running:
            config = state.master_config
            if not config.get("polling_active", True):
                if client_socket:
                    client_socket.close()
                    client_socket = None
                    current_target = None
                time.sleep(0.5)
                continue

            target_host = config.get("target_host", "127.0.0.1")
            target_port = config.get("target_port", self.port)
            target_key = (target_host, target_port)
            peer_desc = f"{target_host}:{target_port}"
            interval = max(0.1, float(config.get("interval", 1.0)))
            conn_mode = config.get("connection_mode", "keep-alive")

            try:
                # If target changed or transient mode requested, reset socket
                if client_socket and (current_target != target_key or conn_mode == "transient"):
                    client_socket.close()
                    client_socket = None

                if not client_socket:
                    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    client_socket.settimeout(4.0)
                    client_socket.connect((target_host, target_port))
                    current_target = target_key
                    state.stats["modbus_connections_opened"] += 1

                trans_id = (trans_id + 1) % 65535
                fc = config.get("function_code", 3)
                unit_id = 1

                mbap_len = 6  # 1 byte Unit ID + 5 bytes PDU
                mbap = struct.pack('>HHHB', trans_id, 0, mbap_len, unit_id)

                if fc == 1:
                    pdu = struct.pack('>BHH', 1, 0, 10)
                    pdu_desc = "Master Poll: Read Coils 00001..00010"
                else:
                    pdu = struct.pack('>BHH', 3, 0, 10)
                    pdu_desc = "Master Poll: Read Holding Regs 40001..40010"

                request_frame = mbap + pdu

                # Log Master TX frame
                state.add_log_entry(
                    proto="MODBUS",
                    direction="TX",
                    summary=f"Master Query: {pdu_desc}",
                    peer=peer_desc,
                    details={"trans_id": trans_id, "fc": fc, "role": "master_tx"},
                    raw_hex=to_hex_spaced(request_frame),
                )

                client_socket.sendall(request_frame)
                state.stats["modbus_bytes_tx"] += len(request_frame)

                # Receive response MBAP (7 bytes)
                resp_mbap = recv_all(client_socket, 7)
                if resp_mbap and len(resp_mbap) >= 7:
                    state.stats["modbus_bytes_rx"] += len(resp_mbap)
                    _r_trans, _r_proto, r_len, _r_unit = struct.unpack('>HHHB', resp_mbap)
                    pdu_len = r_len - 1
                    if pdu_len > 0:
                        resp_pdu = recv_all(client_socket, pdu_len)
                        if resp_pdu:
                            state.stats["modbus_bytes_rx"] += len(resp_pdu)
                            state.stats["modbus_transactions"] += 1

                            # Update datastore with incoming wire response
                            if resp_pdu[0] == 3 and len(resp_pdu) >= 2:
                                byte_cnt = resp_pdu[1]
                                num_regs = min(len(state.holding_registers), byte_cnt // 2)
                                with state._lock:
                                    for i in range(num_regs):
                                        val = struct.unpack('>H', resp_pdu[2 + 2*i : 4 + 2*i])[0]
                                        state.holding_registers[i] = val
                                state.broadcast_state_update()
                            elif resp_pdu[0] == 1 and len(resp_pdu) >= 2:
                                byte_cnt = resp_pdu[1]
                                data_bytes = resp_pdu[2 : 2 + byte_cnt]
                                with state._lock:
                                    for i in range(len(state.coils)):
                                        b_idx = i // 8
                                        bit_idx = i % 8
                                        if b_idx < len(data_bytes):
                                            state.coils[i] = bool(data_bytes[b_idx] & (1 << bit_idx))
                                state.broadcast_state_update()

                            state.add_log_entry(
                                proto="MODBUS",
                                direction="RX",
                                summary=f"Master Reply: {pdu_desc}",
                                peer=peer_desc,
                                details={"trans_id": _r_trans, "pdu_len": len(resp_pdu), "role": "master_rx"},
                                raw_hex=to_hex_spaced(resp_mbap + resp_pdu),
                            )

                if conn_mode == "transient" and client_socket:
                    client_socket.close()
                    client_socket = None

                time.sleep(interval)

            except Exception:
                if client_socket:
                    try:
                        client_socket.close()
                    except Exception:
                        pass
                    client_socket = None
                    current_target = None
                time.sleep(max(1.0, interval))


def trigger_manual_write(func_code: int, address: int, value: int, target_host: str, target_port: int):
    """Executes a single Modbus write (FC5 Write Single Coil or FC6 Write Single Register)."""
    peer_desc = f"{target_host}:{target_port}"
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(4.0)
        sock.connect((target_host, target_port))
        state.stats["modbus_connections_opened"] += 1

        trans_id = random.randint(1, 65535)
        mbap = struct.pack('>HHHB', trans_id, 0, 6, 1)
        pdu = struct.pack('>BHH', func_code, address, value)
        request = mbap + pdu

        pdu_desc = f"Manual Write: {'Coil' if func_code == 5 else 'Register'} {address} = {value}"
        state.add_log_entry(
            proto="MODBUS",
            direction="TX",
            summary=f"Master TX: {pdu_desc}",
            peer=peer_desc,
            details={"trans_id": trans_id, "fc": func_code, "role": "manual_tx"},
            raw_hex=to_hex_spaced(request),
        )

        sock.sendall(request)
        state.stats["modbus_bytes_tx"] += len(request)

        resp_mbap = recv_all(sock, 7)
        if resp_mbap and len(resp_mbap) >= 7:
            state.stats["modbus_bytes_rx"] += len(resp_mbap)
            _t, _p, r_len, _u = struct.unpack('>HHHB', resp_mbap)
            pdu_len = r_len - 1
            if pdu_len > 0:
                resp_pdu = recv_all(sock, pdu_len)
                if resp_pdu:
                    state.stats["modbus_bytes_rx"] += len(resp_pdu)
                    state.stats["modbus_transactions"] += 1
                    state.add_log_entry(
                        proto="MODBUS",
                        direction="RX",
                        summary=f"Master RX: Ack {pdu_desc}",
                        peer=peer_desc,
                        details={"trans_id": _t, "role": "manual_rx"},
                        raw_hex=to_hex_spaced(resp_mbap + resp_pdu),
                    )

        sock.close()
    except Exception as e:
        print(f"[!] Manual write to {peer_desc} failed: {e}", file=sys.stderr)


def trigger_manual_query(func_code: int, start_addr: int, quantity: int, target_host: str, target_port: int):
    """Executes a single Modbus read query (FC1 Read Coils or FC3 Read Holding Registers)."""
    peer_desc = f"{target_host}:{target_port}"
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(4.0)
        sock.connect((target_host, target_port))
        state.stats["modbus_connections_opened"] += 1

        trans_id = random.randint(1, 65535)
        mbap = struct.pack('>HHHB', trans_id, 0, 6, 1)
        pdu = struct.pack('>BHH', func_code, start_addr, quantity)
        request = mbap + pdu

        pdu_desc = f"Manual Query: {'FC01 Read Coils' if func_code == 1 else 'FC03 Read Regs'} {start_addr}..{start_addr+quantity-1}"
        state.add_log_entry(
            proto="MODBUS",
            direction="TX",
            summary=f"Master TX: {pdu_desc}",
            peer=peer_desc,
            details={"trans_id": trans_id, "fc": func_code, "role": "manual_tx"},
            raw_hex=to_hex_spaced(request),
        )

        sock.sendall(request)
        state.stats["modbus_bytes_tx"] += len(request)

        resp_mbap = recv_all(sock, 7)
        if resp_mbap and len(resp_mbap) >= 7:
            state.stats["modbus_bytes_rx"] += len(resp_mbap)
            _t, _p, r_len, _u = struct.unpack('>HHHB', resp_mbap)
            pdu_len = r_len - 1
            if pdu_len > 0:
                resp_pdu = recv_all(sock, pdu_len)
                if resp_pdu:
                    state.stats["modbus_bytes_rx"] += len(resp_pdu)
                    state.stats["modbus_transactions"] += 1
                    state.add_log_entry(
                        proto="MODBUS",
                        direction="RX",
                        summary=f"Master RX: Reply {pdu_desc}",
                        peer=peer_desc,
                        details={"trans_id": _t, "role": "manual_rx"},
                        raw_hex=to_hex_spaced(resp_mbap + resp_pdu),
                    )

        sock.close()
    except Exception as e:
        print(f"[!] Manual query to {peer_desc} failed: {e}", file=sys.stderr)
