# NEC Lab Justfile

default_user := `echo "${SUDO_USER:-$(id -un)}"`
workdir := justfile_directory()
service_name := "nec-dashboard"
service_file := service_name + ".service"
target_unit := "/etc/systemd/system/" + service_file

# Default recipe: list available commands
default:
    @just --list

# Path to the uv binary; falls back to the location the official installer uses
uv_path := `command -v uv 2>/dev/null || { [ -x "$HOME/.local/bin/uv" ] && echo "$HOME/.local/bin/uv"; } || { [ -x "$HOME/.cargo/bin/uv" ] && echo "$HOME/.cargo/bin/uv"; } || echo "$HOME/.local/bin/uv"`

# Ensure uv is installed (quiet no-op when already present)
[private]
_ensure-uv:
    @test -x "{{uv_path}}" || curl -LsSf https://astral.sh/uv/install.sh | sh

# Install uv using the official installer (no-op if already installed)
install-uv: _ensure-uv
    @"{{uv_path}}" --version

# Create virtual environment with uv if it does not exist (installs uv if missing)
venv: _ensure-uv
    @test -f .venv/bin/python && echo "Virtual environment (.venv) already exists." || "{{uv_path}}" sync

# Re-synchronize the virtual environment with uv
sync: _ensure-uv
    @"{{uv_path}}" sync

# Run the dashboard interactively
run *args="": venv
    "{{workdir}}/.venv/bin/python" dashboard/main.py {{args}}

# Preview the rendered systemd service file
render-service user=default_user:
    #!/usr/bin/env bash
    set -euo pipefail
    GROUP="$(id -gn "{{user}}")"
    sed \
        -e "s|__USER__|{{user}}|g" \
        -e "s|__GROUP__|$GROUP|g" \
        -e "s|__WORKDIR__|{{workdir}}|g" \
        "{{service_file}}"

# Install and enable the systemd service to auto-start on boot
install-service user=default_user: venv
    #!/usr/bin/env bash
    set -euo pipefail

    if ! id "{{user}}" >/dev/null 2>&1; then
        echo "Error: User '{{user}}' does not exist on this system." >&2
        exit 1
    fi

    GROUP="$(id -gn "{{user}}")"
    WORKDIR="{{workdir}}"
    SERVICE_NAME="{{service_name}}"
    TARGET_UNIT="{{target_unit}}"

    echo "Installing $SERVICE_NAME systemd service..."
    echo "  • User:             {{user}}"
    echo "  • Group:            $GROUP"
    echo "  • WorkingDirectory: $WORKDIR"
    echo "  • Target Unit:      $TARGET_UNIT"

    TMP_SERVICE="$(mktemp)"
    sed \
        -e "s|__USER__|{{user}}|g" \
        -e "s|__GROUP__|$GROUP|g" \
        -e "s|__WORKDIR__|$WORKDIR|g" \
        "{{service_file}}" > "$TMP_SERVICE"

    sudo install -m 644 "$TMP_SERVICE" "$TARGET_UNIT"
    rm -f "$TMP_SERVICE"

    echo "Reloading systemd daemon..."
    sudo systemctl daemon-reload

    echo "Enabling and starting $SERVICE_NAME..."
    sudo systemctl enable --now "$SERVICE_NAME"

    echo "Service $SERVICE_NAME is installed and active!"
    echo "Check status with: just status"
    echo "Follow logs with:   just logs"

# Check systemd service status
status:
    systemctl status {{service_name}}.service

# Start the systemd service
start:
    sudo systemctl start {{service_name}}.service

# Stop the systemd service
stop:
    sudo systemctl stop {{service_name}}.service

# Restart the systemd service
restart:
    sudo systemctl restart {{service_name}}.service

# Follow service logs (pass e.g. -n 50 to see last 50 lines)
logs *args="-f":
    journalctl -u {{service_name}}.service {{args}}

# Uninstall and disable the systemd service
uninstall-service:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "Stopping and disabling {{service_name}}.service..."
    sudo systemctl disable --now {{service_name}}.service 2>/dev/null || true
    if [ -f "{{target_unit}}" ]; then
        echo "Removing {{target_unit}}..."
        sudo rm -f "{{target_unit}}"
        sudo systemctl daemon-reload
        echo "Service {{service_name}} uninstalled."
    else
        echo "Service unit {{target_unit}} was not found."
    fi

alias install := install-service
alias uninstall := uninstall-service
