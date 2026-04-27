#!/usr/bin/env python3
"""Disk health check using smartmontools (smartctl)."""

import glob
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"


@dataclass
class CheckResult:
    status: str   # "ok", "warning", "error"
    summary: str  # one-liner for the status line
    details: str  # full section for the daily log


@dataclass
class DriveResult:
    device: str
    serial: str
    name: str
    model: str
    capacity_gb: object   # int or None
    temp_c: object        # int or None
    issues: list = field(default_factory=list)


def _get_transport(device):
    """Return transport type (usb, sata, nvme, …) via lsblk."""
    try:
        r = subprocess.run(
            ["lsblk", "-d", "-n", "-o", "TRAN", device],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip().lower()
    except Exception:
        return ""


def _run_smartctl(device, transport):
    """
    Run smartctl --all --json on device.
    USB drives need -d sat to pass SMART commands through the USB bridge.
    Returns parsed JSON dict, or None on hard failure.
    """
    args = ["sudo", "smartctl", "-j", "--all"]
    if transport == "usb":
        args += ["-d", "sat"]
    args.append(device)

    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=60)
        # smartctl exit code is a bitmask; bits 0-1 mean parse/open failure
        if r.returncode & 0x03:
            return None
        return json.loads(r.stdout)
    except Exception:
        return None


def _evaluate(data, thresholds):
    """
    Inspect SMART data for problems.
    Returns (list_of_issue_strings, temperature_int_or_None).
    """
    issues = []
    temp = data.get("temperature", {}).get("current")

    # Overall SMART health verdict
    if not data.get("smart_status", {}).get("passed", True):
        issues.append("SMART overall health check FAILED")

    # ATA/SATA attributes — map id → raw value
    attrs = {
        a["id"]: a["raw"]["value"]
        for a in data.get("ata_smart_attributes", {}).get("table", [])
        if "raw" in a
    }

    # The attributes that matter most for data integrity
    for attr_id, label in [
        (5,   "Reallocated sectors"),
        (187, "Reported uncorrectable errors"),
        (196, "Reallocation event count"),
        (197, "Current pending sectors"),
        (198, "Offline uncorrectable sectors"),
        (199, "UltraDMA CRC errors"),
    ]:
        val = attrs.get(attr_id, 0)
        if val > 0:
            issues.append(f"{label}: {val}")

    # SSD wear / life remaining (vendor-specific attribute IDs)
    warn_life_pct = thresholds.get("ssd_life_warning_pct", 10)
    for attr_id, label in [
        (177, "Wear leveling count"),
        (231, "SSD life left (%)"),
        (233, "Media wearout indicator"),
    ]:
        if attr_id in attrs:
            val = attrs[attr_id]
            if val < warn_life_pct:
                issues.append(f"{label} low: {val}%")

    # NVMe health log (entirely separate from ATA attributes)
    nvme = data.get("nvme_smart_health_information_log", {})
    if nvme:
        warn_code = nvme.get("critical_warning", 0)
        if warn_code:
            issues.append(f"NVMe critical warning (flags: {warn_code:#04x})")
        media_errs = nvme.get("media_errors", 0)
        if media_errs:
            issues.append(f"NVMe media errors: {media_errs}")
        err_entries = nvme.get("num_err_log_entries", 0)
        if err_entries:
            issues.append(f"NVMe error log entries: {err_entries}")
        pct_used = nvme.get("percentage_used", 0)
        if pct_used >= (100 - warn_life_pct):
            issues.append(f"NVMe life used: {pct_used}%")

    # Temperature
    warn_temp = thresholds.get("temperature_warning_c", 55)
    if temp is not None and temp > warn_temp:
        issues.append(f"High temperature: {temp}°C (threshold {warn_temp}°C)")

    return issues, temp


def run(config, init_mode=False):
    thresholds = config.get("thresholds", {})
    known = {
        d["serial"]: d["name"]
        for d in config.get("known_drives", [])
        if "serial" in d
    }

    # Discover all whole-disk block devices (no partitions)
    devices = sorted(glob.glob("/dev/sd?") + glob.glob("/dev/nvme?n?"))

    drive_results = []
    found_serials = set()

    for dev in devices:
        transport = _get_transport(dev)
        data = _run_smartctl(dev, transport)
        if not data:
            continue

        serial = data.get("serial_number", "unknown")
        model = data.get("model_name", "unknown model")
        cap = data.get("user_capacity", {}).get("gigabytes")
        issues, temp = _evaluate(data, thresholds)
        found_serials.add(serial)

        drive_results.append(DriveResult(
            device=dev,
            serial=serial,
            name=known.get(serial, Path(dev).name),
            model=model,
            capacity_gb=cap,
            temp_c=temp,
            issues=issues,
        ))

    # Sort to match config.yaml order; unknown drives go to the end
    config_order = {d["serial"]: i for i, d in enumerate(config.get("known_drives", []))}
    drive_results.sort(key=lambda d: config_order.get(d.serial, len(config_order)))

    # Drives listed in config but absent from the system
    missing = [
        d for d in config.get("known_drives", [])
        if d.get("serial") and d["serial"] not in found_serials
    ]

    if init_mode:
        _update_config(drive_results)

    # --- Build status + summary ---

    problems = [(d.name, d.issues) for d in drive_results if d.issues]

    if missing:
        miss_names = ", ".join(d["name"] for d in missing)
        status = "error"
        summary = f"MISSING drive(s): {miss_names}"
        if problems:
            summary += f";  {len(problems)} other drive(s) with errors"
    elif problems:
        status = "warning"
        parts = [f"{name}: {'; '.join(issues)}" for name, issues in problems]
        summary = "  |  ".join(parts)
    else:
        names = ", ".join(d.name for d in drive_results)
        status = "ok"
        summary = f"All {len(drive_results)} drives healthy ({names})"

    # --- Build detailed report ---

    lines = ["DISK HEALTH", "-" * 70]
    for d in drive_results:
        flag = " OK " if not d.issues else "WARN"
        temp_str = f"{d.temp_c}°C" if d.temp_c is not None else "?°C"
        cap_str = f"{d.capacity_gb}GB" if d.capacity_gb is not None else "?"
        lines.append(
            f"  [{flag}]  {d.name:<16}  {d.model:<34}  {cap_str:>8}  {temp_str:>5}  {d.device}"
        )
        for issue in d.issues:
            lines.append(f"             ! {issue}")

    if missing:
        lines += ["", "MISSING FROM SYSTEM:"]
        for d in missing:
            lines.append(f"  - {d['name']}  (serial: {d.get('serial', '?')})")

    if not drive_results:
        lines.append("  (no drives found — is smartmontools installed?)")

    return CheckResult(status=status, summary=summary, details="\n".join(lines))


def _update_config(drive_results):
    """Append newly discovered drives (by serial) to config.yaml."""
    config = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            config = yaml.safe_load(f) or {}

    known_serials = {d["serial"] for d in config.get("known_drives", [])}
    added = 0

    for d in drive_results:
        if d.serial in known_serials or d.serial == "unknown":
            continue
        config.setdefault("known_drives", []).append({
            "name": Path(d.device).name,   # placeholder — user should rename
            "serial": d.serial,
            "model": d.model,
        })
        known_serials.add(d.serial)
        added += 1

    if added:
        with open(CONFIG_PATH, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)
        print(f"  Added {added} new drive(s) to config.yaml")
    else:
        print("  No new drives found (config.yaml unchanged)")
