#!/usr/bin/env python3
"""Compile, install, restart, and report the complete Athan project."""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
SERVICE_DIR = PROJECT_DIR / "athan.services"
REPORT_DIR = PROJECT_DIR / "logs"
TIMEZONE_NAME = "Europe/Berlin"
EXCLUDED_DIRS = {".git", "__pycache__", "cache", "logs", ".venv", "venv"}


def project_python_files() -> list[Path]:
    files = []
    for path in PROJECT_DIR.rglob("*.py"):
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        files.append(path)
    return sorted(files)


def service_files() -> list[Path]:
    return sorted(SERVICE_DIR.glob("*.service"))


def command_text(command: list[str]) -> str:
    return shlex.join(command)


def run_command(
    report: list[str],
    label: str,
    command: list[str],
    *,
    privileged: bool = False,
) -> int:
    full_command = ["sudo", *command] if privileged else command
    report.append(f"\n### {label}\n$ {command_text(full_command)}")
    try:
        result = subprocess.run(
            full_command,
            cwd=PROJECT_DIR,
            text=True,
            capture_output=True,
            check=False,
        )
        output = (result.stdout or "") + (result.stderr or "")
        report.append(output.rstrip() or "(no output)")
        report.append(f"exit_code={result.returncode}")
        print(f"\n[{label}] exit_code={result.returncode}")
        print(output.rstrip() or "(no output)")
        return result.returncode
    except OSError as exc:
        report.append(f"Could not run command: {exc}")
        report.append("exit_code=127")
        print(f"\n[{label}] could not run: {exc}")
        return 127


def compile_project(report: list[str]) -> bool:
    files = project_python_files()
    if not files:
        report.append("\n### Python compilation\nNo Python files found.")
        print("No Python files found.")
        return False

    report.append("\n### Python files compiled")
    for path in files:
        report.append(str(path.relative_to(PROJECT_DIR)))
    print(f"Compiling {len(files)} Python files...")

    failed = False
    for path in files:
        label = f"compile {path.relative_to(PROJECT_DIR)}"
        code = run_command(
            report,
            label,
            [sys.executable, "-m", "py_compile", str(path)],
        )
        failed = failed or code != 0
    return not failed


def install_services(report: list[str], units: list[Path]) -> bool:
    failed = False
    for unit in units:
        destination = f"/etc/systemd/system/{unit.name}"
        code = run_command(
            report,
            f"install {unit.name}",
            ["install", "-m", "644", str(unit), destination],
            privileged=True,
        )
        failed = failed or code != 0
    return not failed


def restart_services(report: list[str], units: list[Path]) -> bool:
    # Start the persistent audio stream before the scheduler that sends it commands.
    ordered = sorted(units, key=lambda path: ("audio" not in path.name, path.name))
    failed = False
    if run_command(report, "systemd daemon-reload", ["systemctl", "daemon-reload"], privileged=True):
        failed = True
    for unit in ordered:
        service_name = unit.name
        enable_code = run_command(
            report,
            f"enable {service_name}",
            ["systemctl", "enable", service_name],
            privileged=True,
        )
        failed = failed or enable_code != 0
        code = run_command(
            report,
            f"restart {service_name}",
            ["systemctl", "restart", service_name],
            privileged=True,
        )
        failed = failed or code != 0
    return not failed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compile the project, install/restart its systemd units, and collect diagnostics."
    )
    parser.add_argument(
        "--compile-only",
        action="store_true",
        help="Compile all Python files without installing or restarting services.",
    )
    args = parser.parse_args()

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    report_path = REPORT_DIR / f"deploy_report_{timestamp}.log"
    report = [
        "Athan project deployment report",
        f"project={PROJECT_DIR}",
        f"created_at={datetime.now().astimezone().isoformat()}",
        f"timezone={TIMEZONE_NAME}",
    ]

    if not compile_project(report):
        report.append("\nDEPLOYMENT_RESULT=FAILED during Python compilation")
        report_path.write_text("\n".join(report) + "\n", encoding="utf-8")
        print(f"\nReport saved to {report_path}")
        return 1

    if args.compile_only:
        report.append("\nDEPLOYMENT_RESULT=COMPILE_ONLY_SUCCESS")
        report_path.write_text("\n".join(report) + "\n", encoding="utf-8")
        print(f"\nReport saved to {report_path}")
        return 0

    units = service_files()
    if not units:
        report.append(f"\nNo service files found in {SERVICE_DIR}")
        report.append("DEPLOYMENT_RESULT=FAILED no service files")
        report_path.write_text("\n".join(report) + "\n", encoding="utf-8")
        print(f"\nReport saved to {report_path}")
        return 1

    if not install_services(report, units):
        report.append("\nDEPLOYMENT_RESULT=FAILED during service installation")
        report_path.write_text("\n".join(report) + "\n", encoding="utf-8")
        print(f"\nReport saved to {report_path}")
        return 1

    if not restart_services(report, units):
        report.append("\nDEPLOYMENT_RESULT=FAILED during service restart")
        report_path.write_text("\n".join(report) + "\n", encoding="utf-8")
        print(f"\nReport saved to {report_path}")
        return 1

    diagnostics_failed = False
    for unit in units:
        diagnostics_failed = (
            run_command(
            report,
            f"status {unit.name}",
            ["systemctl", "is-active", unit.name],
            privileged=True,
            ) != 0
        ) or diagnostics_failed

    audio_units = [unit.name for unit in units if "audio" in unit.name]
    for service_name in audio_units:
        diagnostics_failed = (
            run_command(
            report,
            f"journalctl -u {service_name} -n 40 --no-pager",
            ["journalctl", "-u", service_name, "-n", "40", "--no-pager"],
            privileged=True,
            ) != 0
        ) or diagnostics_failed

    diagnostics_failed = (
        run_command(report, "wpctl status", ["wpctl", "status"]) != 0
    ) or diagnostics_failed
    result = "COMPLETED_WITH_DIAGNOSTIC_ERRORS" if diagnostics_failed else "SUCCESS"
    report.append(f"\nDEPLOYMENT_RESULT={result}")
    report_path.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"\nDeployment completed. Report saved to {report_path}")
    return 2 if diagnostics_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
