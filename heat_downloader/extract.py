"""Archive extraction helpers."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def find_7z() -> str | None:
    found = shutil.which("7z") or shutil.which("7za")
    if found:
        return found

    for candidate in (
        Path(os.environ.get("ProgramFiles", "")) / "7-Zip" / "7z.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "7-Zip" / "7z.exe",
    ):
        if candidate.exists():
            return str(candidate)
    return None


def extract(archive_path: str | Path, *, log=None) -> Path | None:
    """Extract an archive using 7-Zip if available."""
    archive_path = Path(archive_path)
    extract_dir = archive_path.with_suffix("")
    seven_zip = find_7z()

    if not seven_zip:
        if log:
            log.warn("7-Zip not found — skipping extraction")
            log.warn("install 7-Zip or add it to PATH to enable auto-extract")
        return None

    if log:
        log.info(f"extracting to {extract_dir}")

    result = subprocess.run(
        [seven_zip, "x", str(archive_path), f"-o{extract_dir}", "-aoa", "-y"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        if log:
            log.ok(f"extracted to {extract_dir}")
        return extract_dir

    if log:
        log.err(f"extraction failed (exit {result.returncode})")
        if result.stderr.strip():
            log.err(result.stderr.strip())
    return None
