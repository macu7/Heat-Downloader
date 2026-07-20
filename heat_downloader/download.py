"""Download helpers."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

from tqdm import tqdm

from heat_downloader.http import SESSION


def fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def download(url: str, dest_dir: str | Path, *, force: bool = False, log=None) -> Path | None:
    """Download a file with progress bar and resume support."""
    dest_dir = Path(dest_dir)
    filename = unquote(url.rsplit("/", 1)[-1])
    dest = dest_dir / filename
    partial = dest.with_suffix(dest.suffix + ".part")

    if dest.exists() and not force:
        if log:
            log.ok(f"already exists: {dest} ({fmt_size(dest.stat().st_size)})")
        return dest

    headers: dict[str, str] = {}
    initial_size = 0
    if partial.exists():
        initial_size = partial.stat().st_size
        headers["Range"] = f"bytes={initial_size}-"
        if log:
            log.info(f"resuming from {fmt_size(initial_size)}")

    try:
        head = SESSION.head(url, timeout=10)
        total = int(head.headers.get("Content-Length", 0))
        accepts_range = head.headers.get("Accept-Ranges", "").lower() == "bytes"
    except Exception as exc:
        if log:
            log.err(f"failed to get file info: {exc}")
        return None

    if initial_size and not accepts_range:
        if log:
            log.warn("server doesn't support resume — restarting download")
        initial_size = 0
        headers.pop("Range", None)

    try:
        response = SESSION.get(url, stream=True, headers=headers, timeout=30)
        response.raise_for_status()
    except Exception as exc:
        if log:
            log.err(f"download failed: {exc}")
        return None

    mode = "ab" if initial_size else "wb"
    with open(partial, mode) as handle, tqdm(
        total=total,
        initial=initial_size,
        unit="B",
        unit_scale=True,
        desc=filename,
        ncols=80,
    ) as bar:
        for chunk in response.iter_content(65536):
            handle.write(chunk)
            bar.update(len(chunk))

    partial.rename(dest)
    if log:
        log.ok(f"saved: {dest} ({fmt_size(dest.stat().st_size)})")
    return dest
