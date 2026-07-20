"""Artifact discovery and probing."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from time import sleep
from urllib.parse import quote, unquote, urljoin

import bs4

from heat_downloader.http import ARTIFACT_SERVER, BROWSER
from heat_downloader.models import (
    Release,
    archive_version_candidates,
    detect_kind,
    extract_raw_version,
    extract_version,
)
@dataclass(frozen=True, slots=True)
class ArtifactMetadata:
    last_modified: datetime | None
    size_bytes: int | None
    content_type: str | None


PROBE_DELAY = 0.3

ARTIFACT_EXTENSIONS = [".7z", ".zip", ".rar"]

KIND_TEMPLATES: dict[str, list[str]] = {
    "Fix": [
        "Anthro Heat {v} Fix",
        "Anthro Heat {v} Hotfix",
        "Heat {v} Fix",
        "Heat {v} Hotfix",
    ],
    "Milestone": [
        "Anthro Heat {v} Milestone",
        "Heat {v} Milestone",
    ],
    "Test": [
        "Anthro Heat {v} Test",
        "Heat {v} Test",
    ],
    "Experimental": [
        "Anthro Heat {v} Experimental",
        "Heat {v} Experimental",
    ],
    "Generic": [
        "Anthro Heat {v}",
        "Heat {v}",
    ],
}

PROBE_KIND_ORDER = ["Fix", "Milestone", "Test", "Experimental", "Generic"]

_ARCHIVE_LINK_RE = re.compile(
    r'href="([^"]+\.(?:7z|zip|rar))"',
    re.IGNORECASE,
)

_index_cache: dict[str, list[str]] | None = None


def _resolve_probe_kind(release: Release | None) -> str | None:
    if release is None:
        return None
    if release.kind not in ("", "Unknown"):
        return release.kind
    kind = detect_kind(release.title)
    return kind if kind != "Unknown" else None


def _ordered_probe_kinds(kind: str | None) -> list[str]:
    if not kind or kind not in KIND_TEMPLATES:
        return list(PROBE_KIND_ORDER)
    rest = [name for name in PROBE_KIND_ORDER if name != kind]
    return [kind, *rest]


def _template_names(kind: str) -> list[str]:
    return KIND_TEMPLATES.get(kind, KIND_TEMPLATES["Generic"])


def _iter_candidate_names(version_forms: tuple[str, ...], kind: str | None) -> list[str]:
    seen: set[str] = set()
    names: list[str] = []

    for candidate_version in version_forms:
        for probe_kind in _ordered_probe_kinds(kind):
            for template in _template_names(probe_kind):
                name = template.format(v=candidate_version)
                if name not in seen:
                    seen.add(name)
                    names.append(name)
    return names


def _kind_score(filename: str, kind: str | None) -> int:
    if not kind or kind == "Unknown":
        return 0
    lower = filename.lower()
    if kind == "Fix":
        return 3 if "fix" in lower else 0
    if kind == "Milestone":
        return 3 if "milestone" in lower else 0
    if kind == "Test":
        return 3 if "test" in lower else 0
    if kind == "Experimental":
        return 3 if "experimental" in lower else 0
    return 0


def _extension_score(filename: str) -> int:
    lower = filename.lower()
    if lower.endswith(".7z"):
        return 3
    if lower.endswith(".zip"):
        return 2
    if lower.endswith(".rar"):
        return 1
    return 0


def fetch_anthroheat_archives(*, refresh: bool = False) -> dict[str, list[str]]:
    """Scrape anthroheat.net directory listing and map version -> archive URLs."""
    global _index_cache

    if _index_cache is not None and not refresh:
        return _index_cache

    mapping: dict[str, list[str]] = {}
    try:
        response = BROWSER.get(ARTIFACT_SERVER, timeout=15)
        response.raise_for_status()
    except Exception:
        _index_cache = mapping
        return mapping

    html = response.text.lower()
    if "index of" not in html and not _ARCHIVE_LINK_RE.search(response.text):
        _index_cache = mapping
        return mapping

    soup = bs4.BeautifulSoup(response.text, "html.parser")
    for link in soup.find_all("a", href=True):
        href = unquote(link["href"])
        if not any(href.lower().endswith(ext) for ext in ARTIFACT_EXTENSIONS):
            continue
        filename = href.rsplit("/", 1)[-1]
        version = extract_version(filename)
        if not version:
            continue
        url = urljoin(ARTIFACT_SERVER, href)
        mapping.setdefault(version, []).append(url)

    _index_cache = mapping
    return mapping


def _find_in_index(version_forms: tuple[str, ...], kind: str | None) -> str | None:
    index = fetch_anthroheat_archives()
    urls = [
        url
        for candidate_version in version_forms
        for url in index.get(candidate_version, [])
    ]
    if not urls:
        return None

    ranked = sorted(
        urls,
        key=lambda url: (
            _kind_score(unquote(url.rsplit("/", 1)[-1]), kind),
            _extension_score(unquote(url.rsplit("/", 1)[-1])),
        ),
        reverse=True,
    )
    return ranked[0]


def _artifact_url(base_name: str) -> str:
    return ARTIFACT_SERVER + quote(base_name)


def _head_metadata(url: str) -> ArtifactMetadata | None:
    try:
        response = BROWSER.head(url, timeout=10, allow_redirects=True)
        if response.status_code != 200:
            return None
        try:
            modified = response.headers.get("Last-Modified")
            last_modified = parsedate_to_datetime(modified) if modified else None
        except (TypeError, ValueError):
            last_modified = None
        try:
            size_bytes = int(response.headers["Content-Length"])
        except (KeyError, TypeError, ValueError):
            size_bytes = None
        return ArtifactMetadata(
            last_modified=last_modified,
            size_bytes=size_bytes,
            content_type=response.headers.get("Content-Type"),
        )
    except Exception:
        return None


def _format_size(size_bytes: int | None) -> str:
    if size_bytes is None:
        return "unknown"
    return f"{size_bytes / 1024**3:.2f} GiB ({size_bytes:,} bytes)"


def _log_found(
    url: str,
    metadata: ArtifactMetadata,
    *,
    from_index: bool,
    quiet: bool,
    log,
) -> None:
    if quiet or not log:
        return
    label = "found (index)" if from_index else "found"
    log.ok(f"{label}: {url}")
    log.info(f"archive size: {_format_size(metadata.size_bytes)}")
    if metadata.last_modified:
        log.info(
            "archive last modified (server, UTC): "
            f"{metadata.last_modified.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')}"
        )
    if metadata.content_type:
        log.info(f"archive content type: {metadata.content_type}")


def probe_artifact(
    version: str,
    *,
    release: Release | None = None,
    quiet: bool = False,
    log=None,
    nearby_versions: Iterable[str] = (),
) -> str | None:
    """Try known URL patterns and return the first artifact URL that exists."""
    kind = _resolve_probe_kind(release)
    source_version = extract_raw_version(release.title) if release else None
    version_forms = archive_version_candidates(
        source_version or version,
        nearby_versions=nearby_versions,
    )
    if not quiet and log:
        log.info("version forms: " + ", ".join(version_forms))

    indexed = _find_in_index(version_forms, kind)
    if indexed:
        metadata = _head_metadata(indexed)
        if metadata:
            _log_found(indexed, metadata, from_index=True, quiet=quiet, log=log)
            return indexed

    names = _iter_candidate_names(version_forms, kind)
    for ext in ARTIFACT_EXTENSIONS:
        for base_name in names:
            name = base_name + ext
            url = _artifact_url(name)
            if not quiet and log:
                log.info(f"probing: {name}")
            metadata = _head_metadata(url)
            if metadata:
                _log_found(url, metadata, from_index=False, quiet=quiet, log=log)
                return url
            sleep(PROBE_DELAY)
    return None
