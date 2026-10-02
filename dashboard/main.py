import sys
from pathlib import Path

# Ensure both repository root and dashboard directory are in sys.path
_current_dir = Path(__file__).resolve().parent
_root_dir = _current_dir.parent

if str(_root_dir) not in sys.path:
    sys.path.insert(0, str(_root_dir))
if str(_current_dir) not in sys.path:
    sys.path.insert(0, str(_current_dir))

from dashboard.server import main

if __name__ == "__main__":
    main()
