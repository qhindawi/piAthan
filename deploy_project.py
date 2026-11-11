#!/usr/bin/env python3
"""Compile, install, restart, and report the complete Athan project."""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
SERVICE_DIR = PROJECT_DIR / "athan.services"
REPORT_DIR = PROJECT_DIR / "logs"
SETTING_AUDIO_SCRIPT = PROJECT_DIR / "src" / "setting_audio_device.py"
TIMEZONE_NAME = "Europe/Berlin"
EXCLUDED_DIRS = {".git", "__pycache__", "cache", "logs", ".venv", "venv"}
COMMAND_TIMEOUT_SECONDS = 60
SYSTEMD_TIMEOUT_SECONDS = 15
SYSTEMD_ATTEMPTS = 5
SYSTEMD_RETRY_DELAY_SECONDS = 2


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
    timeout_seconds: float = COMMAND_TIMEOUT_SECONDS,
    attempts: int = 1,
) -> int:
    full_command = ["sudo", *command] if privileged else command
    report.append(f"\n### {label}\n$ {command_text(full_command)}")
    attempts = max(1, attempts)
    for attempt in range(1, attempts + 1):
        try:
            result = subprocess.run(
                full_command,
                cwd=PROJECT_DIR,
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout_seconds,
            )
            output = (result.stdout or "") + (result.stderr or "")
            output_text = output.rstrip() or "(no output)"
            report.append(f"attempt={attempt}/{attempts}")
            report.append(output_text)
            report.append(f"exit_code={result.returncode}")
            print(f"\n[{label}] attempt {attempt}/{attempts} exit_code={result.returncode}")
            print(output_text)
            if result.returncode == 0 or attempt == attempts:
                return result.returncode
            report.append(
                f"retrying after non-zero exit code; wait={SYSTEMD_RETRY_DELAY_SECONDS}s"
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            output_text = (str(stdout) + str(stderr)).rstrip() or "(no output)"
            report.append(f"attempt={attempt}/{attempts} timed out after {timeout_seconds}s")
            report.append(output_text)
            print(
                f"\n[{label}] attempt {attempt}/{attempts} timed out "
                f"after {timeout_seconds}s"
            )
            if attempt == attempts:
                report.append("exit_code=124")
                return 124
            report.append(
                f"retrying after timeout; wait={SYSTEMD_RETRY_DELAY_SECONDS}s"
            )
        except OSError as exc:
            report.append(f"Could not run command: {exc}")
            report.append("exit_code=127")
            print(f"\n[{label}] could not run: {exc}")
            return 127
        time.sleep(SYSTEMD_RETRY_DELAY_SECONDS)
    return 124


def run_systemd_command(
    report: list[str], label: str, command: list[str]
) -> int:
    """Run a systemd command with bounded five-attempt retry behavior."""
    return run_command(
        report,
        label,
        command,
        privileged=True,
        timeout_seconds=SYSTEMD_TIMEOUT_SECONDS,
        attempts=SYSTEMD_ATTEMPTS,
    )


def run_interactive(
    report: list[str], label: str, command: list[str]
) -> int:
    """Run a user-facing helper without capturing its prompts or output."""
    report.append(f"\n### {label}\n$ {command_text(command)}")
    print(f"\n[{label}]")
    try:
        result = subprocess.run(command, cwd=PROJECT_DIR, check=False)
    except OSError as exc:
        report.append(f"Could not run command: {exc}")
        report.append("exit_code=127")
        print(f"Could not run command: {exc}")
        return 127
    report.append(f"interactive command exit_code={result.returncode}")
    return result.returncode


def ask_yes_no(question: str, default: bool) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        answer = input(f"{question} [{suffix}] ").strip().lower()
        if not answer:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Please answer yes or no.")


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
    if run_systemd_command(
        report, "systemd daemon-reload", ["systemctl", "daemon-reload"]
    ) != 0:
        report.append("systemd daemon-reload failed; stopping deployment before service changes")
        return False
    for unit in ordered:
        service_name = unit.name
        enable_code = run_systemd_command(
            report,
            f"enable {service_name}",
            ["systemctl", "enable", service_name],
        )
        if enable_code != 0:
            report.append(
                f"enable {service_name} failed; stopping before restarting this or later services"
            )
            return False
        code = run_systemd_command(
            report,
            f"restart {service_name}",
            ["systemctl", "restart", service_name],
        )
        if code != 0:
            report.append(
                f"restart {service_name} failed; stopping before processing later services"
            )
            return False
    return True


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

    if args.compile_only:
        if not compile_project(report):
            report.append("\nDEPLOYMENT_RESULT=FAILED during Python compilation")
            report_path.write_text("\n".join(report) + "\n", encoding="utf-8")
            print(f"\nReport saved to {report_path}")
            return 1
        report.append("\nDEPLOYMENT_RESULT=COMPILE_ONLY_SUCCESS")
        report_path.write_text("\n".join(report) + "\n", encoding="utf-8")
        print(f"\nReport saved to {report_path}")
        return 0

    change_output = ask_yes_no("Change the Athan audio output device?", False)
    if change_output:
        if not SETTING_AUDIO_SCRIPT.is_file():
            report.append(f"Missing audio-output setup script: {SETTING_AUDIO_SCRIPT}")
            report.append("DEPLOYMENT_RESULT=FAILED missing audio-output setup script")
            report_path.write_text("\n".join(report) + "\n", encoding="utf-8")
            print(f"\nReport saved to {report_path}")
            return 1
        code = run_interactive(
            report,
            "select audio output",
            [sys.executable, str(SETTING_AUDIO_SCRIPT)],
        )
        if code != 0:
            report.append("\nDEPLOYMENT_RESULT=FAILED during audio-output selection")
            report_path.write_text("\n".join(report) + "\n", encoding="utf-8")
            print(f"\nReport saved to {report_path}")
            return 1

    restart_requested = ask_yes_no(
        "Recompile, install, and restart the Athan services now?", True
    )
    report.append(f"\nchange_output={change_output}")
    report.append(f"restart_requested={restart_requested}")
    if not restart_requested:
        report.append(
            "Audio-output JSON is hot-reloaded by athan-audio.service; no service restart was requested."
        )
        run_command(report, "wpctl status", ["wpctl", "status"])
        report.append("DEPLOYMENT_RESULT=CONFIGURED_NO_RESTART")
        report_path.write_text("\n".join(report) + "\n", encoding="utf-8")
        print(f"\nDeployment configuration saved. Report saved to {report_path}")
        return 0

    if not compile_project(report):
        report.append("\nDEPLOYMENT_RESULT=FAILED during Python compilation")
        report_path.write_text("\n".join(report) + "\n", encoding="utf-8")
        print(f"\nReport saved to {report_path}")
        return 1

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
            run_systemd_command(
            report,
            f"status {unit.name}",
            ["systemctl", "is-active", unit.name],
            ) != 0
        ) or diagnostics_failed

    audio_units = [unit.name for unit in units if "audio" in unit.name]
    for service_name in audio_units:
        diagnostics_failed = (
            run_systemd_command(
            report,
            f"journalctl -u {service_name} -n 40 --no-pager",
            ["journalctl", "-u", service_name, "-n", "40", "--no-pager"],
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
