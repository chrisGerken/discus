#!/usr/bin/env python3
"""
discus — system health monitor

Usage:
    python3 discus.py          # run all health checks
    python3 discus.py --init   # discover drives and seed config.yaml
"""
import sys
from pathlib import Path
from datetime import datetime

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from checks import disk_health

CONFIG_PATH = Path(__file__).parent / "config.yaml"
LOG_DIR = Path(__file__).parent / "logs"


def load_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}


def main():
    init_mode = "--init" in sys.argv
    config = load_config()
    LOG_DIR.mkdir(exist_ok=True)

    results = [
        disk_health.run(config, init_mode=init_mode),
        # Add future checks here, e.g.:
        # cpu_health.run(config),
        # filesystem.run(config),
    ]

    statuses = [r.status for r in results]
    if "error" in statuses:
        overall = "ALERT"
    elif "warning" in statuses:
        overall = "WARN "
    else:
        overall = " OK  "

    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d %H:%M")
    summaries = " | ".join(r.summary for r in results)
    one_liner = f"[{date_str}] {overall}  {summaries}"

    print(one_liner)

    with open(LOG_DIR / "summary.log", "a") as f:
        f.write(one_liner + "\n")

    detail_path = LOG_DIR / f"{now.strftime('%Y-%m-%d')}.log"
    with open(detail_path, "w") as f:
        f.write(f"discus health report — {date_str}\n")
        f.write("=" * 70 + "\n\n")
        for r in results:
            f.write(r.details + "\n\n")

    if init_mode:
        print(f"\nConfig written to: {CONFIG_PATH}")
        print("Edit 'name' fields in config.yaml to assign friendly names to each drive.")


if __name__ == "__main__":
    main()
