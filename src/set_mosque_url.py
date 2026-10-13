#!/usr/bin/env python3
"""Set the Mawaqit mosque page used by the Athan scheduler."""

from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import urlparse


PROJECT_DIR = Path(__file__).resolve().parent.parent
URL_FILE = PROJECT_DIR / "config" / "mosque_url.txt"


def validate_url(value: str) -> str:
    url = value.strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "mawaqit.net" or not parsed.path.startswith("/en/"):
        raise argparse.ArgumentTypeError(
            "URL must look like https://mawaqit.net/en/mosque-name-city-country"
        )
    return url


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Persist the Mawaqit mosque page used by the scheduler."
    )
    parser.add_argument("url", type=validate_url)
    args = parser.parse_args()
    URL_FILE.parent.mkdir(parents=True, exist_ok=True)
    URL_FILE.write_text(args.url + "\n", encoding="utf-8")
    print(f"Saved mosque URL to {URL_FILE}: {args.url}")
    print("Restart athan.service to use the new URL.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
