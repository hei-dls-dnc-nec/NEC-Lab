import sys
from pathlib import Path

# Ensure both repository root and dashboard directory are in sys.path
_current_dir = Path(__file__).resolve().parent
_root_dir = _current_dir.parent

if str(_root_dir) not in sys.path:
    sys.path.insert(0, str(_root_dir))
if str(_current_dir) not in sys.path:
    sys.path.insert(0, str(_current_dir))

# --------------------------------------------------------------------------
# Default Poller Configuration
# Set these constants to True if you want pollers to run automatically on launch,
# or False to require explicit activation from the user interface / CLI.
# --------------------------------------------------------------------------
DEFAULT_HTTP_POLLER_ACTIVE = False
DEFAULT_MODBUS_POLLER_ACTIVE = False

from dashboard.server import main as _server_main

def main():
    _server_main(
        default_http_poller=DEFAULT_HTTP_POLLER_ACTIVE,
        default_modbus_poller=DEFAULT_MODBUS_POLLER_ACTIVE,
    )

if __name__ == "__main__":
    main()
