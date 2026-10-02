import socket
import subprocess
import re
from typing import Tuple, List

# Default network ports
DEFAULT_HTTP_PORT = 8050
DEFAULT_MODBUS_PORT = 1502

# Friendly metadata for industrial simulation
COIL_DEFINITIONS = [
    {"addr": 0, "name": "Main Coolant Pump", "desc": "Circulation pump power state"},
    {"addr": 1, "name": "Inlet Solenoid Valve", "desc": "Main feed liquid flow gate"},
    {"addr": 2, "name": "Primary Heating Coil", "desc": "Vessel thermal element stage 1"},
    {"addr": 3, "name": "Secondary Heater", "desc": "Vessel thermal element stage 2"},
    {"addr": 4, "name": "Emergency Exhaust Fan", "desc": "Over-pressure ventilation exhaust"},
    {"addr": 5, "name": "Agitator Motor", "desc": "Mixing drive motor"},
    {"addr": 6, "name": "Recycle Flow Bypass", "desc": "Secondary loop recirculation valve"},
    {"addr": 7, "name": "Safety Interlock Relay", "desc": "Hardware safety trip line"},
    {"addr": 8, "name": "Alarm Strobe Beacon", "desc": "Audible/visual alert siren"},
    {"addr": 9, "name": "Auxiliary Reservoir Fill", "desc": "Backup buffer tank inlet gate"},
]

REGISTER_DEFINITIONS = [
    {"addr": 0, "name": "Process Tick Counter", "unit": "ticks", "desc": "Sequential cycle timer (drifting)"},
    {"addr": 1, "name": "Supply Bus Voltage", "unit": "V", "desc": "AC line RMS voltage ~230V"},
    {"addr": 2, "name": "Reactor Pressure", "unit": "hPa", "desc": "Core vessel pressure ~1013 hPa"},
    {"addr": 3, "name": "Motor Speed (RPM)", "unit": "rpm", "desc": "Agitator tachometer feedback"},
    {"addr": 4, "name": "Coolant Temp", "unit": "°C", "desc": "Return line coolant temperature"},
    {"addr": 5, "name": "Target Flow Setpoint", "unit": "L/h", "desc": "Operator assigned flow target"},
    {"addr": 6, "name": "Flow Rate Reading", "unit": "L/h", "desc": "Electromagnetic flow sensor"},
    {"addr": 7, "name": "System Alarm Code", "unit": "code", "desc": "0 = OK, >0 = Active warning"},
    {"addr": 8, "name": "Batch Counter", "unit": "units", "desc": "Total completed units count"},
    {"addr": 9, "name": "Firmware Build ID", "unit": "v", "desc": "PLC firmware release revision"},
]


def get_local_ips() -> Tuple[str, List[str]]:
    """
    Detect the machine's primary LAN IP and all active network IPs reliably.
    """
    ips = set()
    primary_ip = None

    # 1. Probe outbound UDP socket without sending wire data
    probe_targets = [("8.8.8.8", 80), ("1.1.1.1", 80), ("10.255.255.255", 80), ("192.168.255.255", 80)]
    for target_ip, target_port in probe_targets:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((target_ip, target_port))
            ip = s.getsockname()[0]
            s.close()
            if ip and not ip.startswith("127."):
                ips.add(ip)
                if not primary_ip:
                    primary_ip = ip
                break
        except Exception:
            pass

    # 2. Try 'hostname -I' (standard on Linux)
    try:
        out = subprocess.check_output(["hostname", "-I"], text=True, stderr=subprocess.DEVNULL)
        for token in out.split():
            token = token.strip()
            if token and not token.startswith("127.") and ":" not in token:
                ips.add(token)
                if not primary_ip:
                    primary_ip = token
    except Exception:
        pass

    # 3. Try 'ip -4 addr show' (Linux iproute2)
    try:
        out = subprocess.check_output(["ip", "-4", "addr", "show"], text=True, stderr=subprocess.DEVNULL)
        for match in re.findall(r'inet\s+(\d+\.\d+\.\d+\.\d+)', out):
            if not match.startswith("127."):
                ips.add(match)
                if not primary_ip:
                    primary_ip = match
    except Exception:
        pass

    # 4. Standard socket hostname resolution fallback
    try:
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            if not ip.startswith("127."):
                ips.add(ip)
                if not primary_ip:
                    primary_ip = ip
    except Exception:
        pass

    all_ips = sorted(ips)
    if not primary_ip:
        primary_ip = all_ips[0] if all_ips else "127.0.0.1"
    if not all_ips:
        all_ips = [primary_ip]

    return primary_ip, all_ips
