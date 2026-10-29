#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# First-time (and repeat-safe) setup of the Studio Hub service on the Pi.
# Copies nothing: run it on the Pi against the post-receive checkout.
# Usage: SUDO_PASSWORD=... bash tools/pi-bootstrap.sh   (omit the var if sudo is NOPASSWD)
set -euo pipefail

REPO_DIR=/home/pi/studio-hub
VENV_DIR=/home/pi/studio-hub-venv
SERVICE=studio-hub

run_sudo() {
  if sudo -n true 2>/dev/null; then
    sudo -n "$@"
  else
    if [ -z "${SUDO_PASSWORD:-}" ]; then
      echo "sudo needs a password; export SUDO_PASSWORD first" >&2
      exit 1
    fi
    echo "$SUDO_PASSWORD" | sudo -S "$@"
  fi
}

if [ ! -x "$VENV_DIR/bin/python" ]; then
  python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install -q -r "$REPO_DIR/backend/requirements.txt"

run_sudo cp "$REPO_DIR/tools/studio-hub.service" /etc/systemd/system/
run_sudo systemctl daemon-reload
run_sudo systemctl enable --now "$SERVICE"

sleep 3
curl -fsS http://127.0.0.1:8000/api/health && echo && echo BOOTSTRAP_OK
