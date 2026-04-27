#!/usr/bin/env bash
# discus setup — installs dependencies, adds sudoers rule, and wires up cron.
# Run once as yourself (sudo will be prompted as needed).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== discus setup ==="
echo ""

# --- smartmontools ---
if command -v smartctl &>/dev/null; then
    echo "[ok] smartmontools already installed"
else
    echo "[..] Installing smartmontools..."
    sudo apt-get update -qq
    sudo apt-get install -y smartmontools
    echo "[ok] smartmontools installed"
fi

# --- python3-yaml ---
if python3 -c "import yaml" 2>/dev/null; then
    echo "[ok] python3-yaml already available"
else
    echo "[..] Installing python3-yaml..."
    sudo apt-get install -y python3-yaml
    echo "[ok] python3-yaml installed"
fi

# --- sudoers rule (lets smartctl run without a password, needed for cron) ---
SUDOERS_FILE="/etc/sudoers.d/discus"
SUDOERS_LINE="${USER} ALL=(ALL) NOPASSWD: /usr/bin/smartctl"
if sudo test -f "$SUDOERS_FILE" && sudo grep -q "NOPASSWD.*smartctl" "$SUDOERS_FILE"; then
    echo "[ok] sudoers rule already present"
else
    echo "$SUDOERS_LINE" | sudo tee "$SUDOERS_FILE" > /dev/null
    sudo chmod 440 "$SUDOERS_FILE"
    echo "[ok] sudoers rule added: ${SUDOERS_FILE}"
fi

# --- cron jobs (daily at 4 am + on every boot) ---
CRON_DAILY="0 4 * * *  cd ${SCRIPT_DIR} && /usr/bin/python3 discus.py >> logs/cron.log 2>&1"
# 60-second delay on reboot gives USB drives time to be recognised
CRON_BOOT="@reboot  sleep 60 && cd ${SCRIPT_DIR} && /usr/bin/python3 discus.py >> logs/cron.log 2>&1"
CURRENT_CRON="$(crontab -l 2>/dev/null | grep -v 'discus\.py' || true)"
if crontab -l 2>/dev/null | grep -q "discus\.py"; then
    echo "[ok] cron jobs already present"
else
    printf '%s\n%s\n%s\n' "$CURRENT_CRON" "$CRON_DAILY" "$CRON_BOOT" | crontab -
    echo "[ok] cron jobs added (daily at 04:00 and on every boot)"
fi

# --- show last result in every new terminal ---
BASHRC_MARKER="# discus — disk health"
if grep -q "discus" ~/.bashrc 2>/dev/null; then
    echo "[ok] ~/.bashrc entry already present"
else
    cat >> ~/.bashrc <<BASHRC

${BASHRC_MARKER}
if [[ -f "${SCRIPT_DIR}/logs/summary.log" ]]; then
    tail -1 "${SCRIPT_DIR}/logs/summary.log"
fi
BASHRC
    echo "[ok] added to ~/.bashrc (last result shown on every new terminal)"
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo ""
echo "  1.  python3 ${SCRIPT_DIR}/discus.py --init"
echo "      Discovers all connected drives and writes their serials"
echo "      to config.yaml.  Run this again whenever you add new drives."
echo ""
echo "  2.  Edit config.yaml"
echo "      Change each 'name: sdX' placeholder to a friendly name"
echo "      like 'lifeboat', 'curate', 'encyclo', etc."
echo ""
echo "  3.  python3 ${SCRIPT_DIR}/discus.py"
echo "      Run a check right now to write the first summary.log entry."
echo "      After that, every new terminal window will show the last result"
echo "      automatically — no manual runs needed."
echo ""
echo "  Logs will be written to: ${SCRIPT_DIR}/logs/"
echo "    summary.log    — one line per run, shown on terminal open"
echo "    YYYY-MM-DD.log — full detail report (open when something looks wrong)"
