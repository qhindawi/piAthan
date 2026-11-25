"""Read published Hijri dates from a mosque's Mawaqit annual PDF."""

from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

DEFAULT_MOSQUE_URL = "https://mawaqit.net/en/haus-des-friedens-dr-lslm-wuppertal-42103-germany"
DEFAULT_PDF_URL = "https://mawaqit.net/en/calendar/d5c340e2-4963-4c3f-b9fb-9ab767f90005.pdf"


def parse_pdf_text(text: str, year: int) -> dict[str, dict]:
    """Read unambiguous date/Islamic-day/month/year rows from extracted text."""
    # Some PDF extractors place a complete table row on one line; pypdf may
    # instead emit one word per line. Normalize both formats to one parser.
    text = re.sub(
        r"(?m)^(\d{4}-\d{2}-\d{2})[ \t]+(\d{1,2})[ \t]+([^\d\n]+?)[ \t]+(\d{4})(?=[ \t]|$)",
        lambda match: "\n".join(match.groups()) + "\n", text,
    )
    lines = [line.strip() for line in text.splitlines()]
    days = {}
    for index, line in enumerate(lines):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", line):
            continue
        gregorian = date.fromisoformat(line)
        if gregorian.year != year:
            continue
        if index + 3 >= len(lines) or not re.fullmatch(r"\d{1,2}", lines[index + 1]):
            # Some month pages append the following Gregorian day with prayer
            # times only. Its Hijri row is on the next month's page.
            continue
        if line in days:
            raise ValueError(f"Duplicate Hijri date row in mosque calendar PDF: {line}")
        hijri_day = int(lines[index + 1])
        month_parts = []
        cursor = index + 2
        while cursor < len(lines) and len(month_parts) < 4 and not re.fullmatch(r"\d{4}", lines[cursor]):
            if not re.fullmatch(r"[^\W\d_][^\W\d_'’ -]*(?:[ '’\-][^\W\d_]+)*", lines[cursor], re.UNICODE):
                raise ValueError(f"Invalid Hijri month for {line}")
            month_parts.append(lines[cursor])
            cursor += 1
        if not month_parts or cursor >= len(lines) or not re.fullmatch(r"\d{4}", lines[cursor]):
            raise ValueError(f"Missing Hijri year for {line}")
        hijri_year = int(lines[cursor])
        if not 1 <= hijri_day <= 30 or not 1000 <= hijri_year <= 2000:
            raise ValueError(f"Invalid Hijri date for {line}")
        days[line] = {"day": hijri_day, "month": " ".join(month_parts), "year": hijri_year,
                      "provider": "mawaqit_pdf"}
    if not days:
        raise ValueError("No dated Hijri rows found in Mawaqit PDF")
    return days


def extract_pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        binary = shutil.which("pdftotext")
        if not binary:
            raise RuntimeError("Install python3-pypdf or poppler-utils to read the Mawaqit PDF")
        result = subprocess.run([binary, "-raw", "-", "-"], input=data, capture_output=True,
                                timeout=30, check=False)
        if result.returncode:
            raise RuntimeError("pdftotext could not read the Mawaqit PDF")
        return result.stdout.decode("utf-8", errors="replace")
    document = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in document.pages)


class MawaqitPdfHijriProvider:
    def __init__(self, url: str):
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != "mawaqit.net" or not parsed.path.endswith(".pdf"):
            raise ValueError("Hijri calendar must be a Mawaqit HTTPS PDF")
        self.url = url

    @classmethod
    def from_config(cls, path: Path, mosque_url: str):
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            if mosque_url != DEFAULT_MOSQUE_URL:
                raise ValueError("Configure a Mawaqit PDF for the selected mosque")
            return cls(DEFAULT_PDF_URL)
        if config.get("provider") != "mawaqit_pdf" or config.get("mosque_url") != mosque_url:
            raise ValueError("Hijri PDF configuration does not match the selected mosque")
        return cls(config["pdf_url"])

    def fetch_year(self, year: int) -> dict[str, dict]:
        request = Request(self.url, headers={"User-Agent": "piAthan/1.0",
                                             "Cache-Control": "no-cache", "Pragma": "no-cache"})
        with urlopen(request, timeout=30) as response:
            data = response.read(5_000_001)
        if len(data) > 5_000_000 or not data.startswith(b"%PDF-"):
            raise ValueError("Mawaqit calendar response is not a valid-sized PDF")
        return parse_pdf_text(extract_pdf_text(data), year)
