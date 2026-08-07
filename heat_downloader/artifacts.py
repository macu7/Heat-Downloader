"""Artifact discovery and probing."""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
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

ARTIFACT_CACHE_PATH = Path.home() / ".heat_downloader_artifacts.json"


def app_dir() -> Path:
    """Directory for portable data files (next to the exe when frozen)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def probes_json_path() -> Path:
    return app_dir() / "heat_probes.json"


def ensure_probes_file() -> Path:
    """Create an empty heat_probes.json when missing; migrate old fat files on load/save."""
    path = probes_json_path()
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        bundled = Path(sys._MEIPASS) / "heat_probes.json"
        if not path.exists() and bundled.exists():
            path.write_text(bundled.read_text(encoding="utf-8"), encoding="utf-8")
            return path
    if not path.exists():
        path.write_text('{"probes":[],"not_found":[]}\n', encoding="utf-8")
    return path


# Back-compat name used by CLI imports.
PROBES_JSON_PATH = probes_json_path()


@dataclass(frozen=True, slots=True)
class ArtifactMetadata:
    last_modified: datetime | None
    size_bytes: int | None
    content_type: str | None


@dataclass(frozen=True, slots=True)
class FoundArtifact:
    version: str
    filename: str
    url: str
    kind: str
    title: str
    source: str
    release_date: datetime | None
    last_modified: datetime | None
    size_bytes: int | None
    content_type: str | None
    probed_at: datetime | None = None

    def sort_key(self) -> datetime:
        return self.last_modified or self.release_date or self.probed_at or datetime.min

    def to_json(self) -> dict:
        """Compact on-disk representation (no duplicates / fluff)."""
        payload: dict = {
            "version": self.version,
            "url": self.url,
        }
        if self.kind and self.kind != "Unknown":
            payload["kind"] = self.kind
        if self.last_modified:
            payload["last_modified"] = (
                self.last_modified.astimezone(timezone.utc).isoformat()
                if self.last_modified.tzinfo
                else self.last_modified.isoformat()
            )
        if self.size_bytes is not None:
            payload["size_bytes"] = self.size_bytes
        return payload


@dataclass(frozen=True, slots=True)
class NotFoundProbe:
    version: str
    title: str = ""
    source: str = ""
    kind: str = "Unknown"
    release_date: datetime | None = None
    probed_at: datetime | None = None


@dataclass
class ProbeStore:
    probes: list[FoundArtifact]
    not_found: list[NotFoundProbe]

    def __init__(
        self,
        probes: list[FoundArtifact] | None = None,
        not_found: list[NotFoundProbe] | None = None,
    ):
        self.probes = list(probes or [])
        self.not_found = list(not_found or [])

    @property
    def found_versions(self) -> set[str]:
        return {item.version for item in self.probes}

    @property
    def not_found_versions(self) -> set[str]:
        return {item.version for item in self.not_found}


@dataclass(frozen=True, slots=True)
class ProbeCoverage:
    catalog: list[Release]
    found: list[FoundArtifact]
    not_found: list[NotFoundProbe]
    not_probed: list[Release]

    @property
    def totals(self) -> dict[str, int]:
        return {
            "catalog": len(self.catalog),
            "found": len(self.found),
            "not_found": len(self.not_found),
            "not_probed": len(self.not_probed),
        }


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
    found = probe_artifact_details(
        version,
        release=release,
        quiet=quiet,
        log=log,
        nearby_versions=nearby_versions,
    )
    return found.url if found else None


def _probe_cache_paths() -> list[Path]:
    return [probes_json_path()]


def _cached_urls_for_version(
    version: str,
    version_forms: tuple[str, ...],
    kind: str | None,
) -> list[str]:
    """Return previously saved probe URLs that might match this version."""
    forms = {version, *version_forms}
    ranked: list[tuple[int, int, str]] = []
    seen: set[str] = set()

    for path in _probe_cache_paths():
        for item in load_artifact_cache(path):
            filename_version = extract_version(item.filename)
            if item.version not in forms and filename_version not in forms:
                continue
            if item.url in seen:
                continue
            seen.add(item.url)
            ranked.append(
                (
                    _kind_score(item.filename, kind),
                    _extension_score(item.filename),
                    item.url,
                )
            )

    ranked.sort(reverse=True)
    return [url for _, _, url in ranked]


def probe_artifact_details(
    version: str,
    *,
    release: Release | None = None,
    quiet: bool = False,
    log=None,
    nearby_versions: Iterable[str] = (),
) -> FoundArtifact | None:
    """Like probe_artifact, but return filename/size/date metadata."""
    kind = _resolve_probe_kind(release)
    source_version = extract_raw_version(release.title) if release else None
    version_forms = archive_version_candidates(
        source_version or version,
        nearby_versions=nearby_versions,
    )
    if not quiet and log:
        log.info("version forms: " + ", ".join(version_forms))

    # 1) Reuse successful probes from heat_probes.json / home cache.
    for url in _cached_urls_for_version(version, version_forms, kind):
        filename = unquote(url.rsplit("/", 1)[-1])
        if not quiet and log:
            log.info(f"checking saved probe: {filename}")
        metadata = _head_metadata(url)
        if metadata:
            if not quiet and log:
                log.ok(f"found (probes cache): {url}")
                _log_found(url, metadata, from_index=False, quiet=True, log=None)
                log.info(f"archive size: {_format_size(metadata.size_bytes)}")
                if metadata.last_modified:
                    log.info(
                        "archive last modified (server, UTC): "
                        f"{metadata.last_modified.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')}"
                    )
            found = _to_found_artifact(url, metadata, version=version, release=release)
            record_successful_probe(found, quiet=True)
            return found

    # 2) Optional live directory index (usually unavailable).
    indexed = _find_in_index(version_forms, kind)
    if indexed:
        metadata = _head_metadata(indexed)
        if metadata:
            _log_found(indexed, metadata, from_index=True, quiet=quiet, log=log)
            found = _to_found_artifact(indexed, metadata, version=version, release=release)
            record_successful_probe(found, quiet=quiet, log=log)
            return found

    # 3) Template probing.
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
                found = _to_found_artifact(url, metadata, version=version, release=release)
                record_successful_probe(found, quiet=quiet, log=log)
                return found
            sleep(PROBE_DELAY)
    return None


def _to_found_artifact(
    url: str,
    metadata: ArtifactMetadata,
    *,
    version: str,
    release: Release | None,
) -> FoundArtifact:
    filename = unquote(url.rsplit("/", 1)[-1])
    return FoundArtifact(
        version=version,
        filename=filename,
        url=url,
        kind=(release.kind if release else detect_kind(filename)),
        title=(release.title if release else filename),
        source=(release.source if release else "AnthroHeat"),
        release_date=(release.date if release else None),
        last_modified=metadata.last_modified,
        size_bytes=metadata.size_bytes,
        content_type=metadata.content_type,
        probed_at=datetime.now(timezone.utc),
    )


def _parse_optional_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_found_item(item: dict) -> FoundArtifact | None:
    try:
        url = item["url"]
        filename = item.get("filename") or unquote(url.rsplit("/", 1)[-1])
        return FoundArtifact(
            version=item["version"],
            filename=filename,
            url=url,
            kind=item.get("kind") or detect_kind(filename),
            title=item.get("title") or filename,
            source=item.get("source") or "cache",
            release_date=_parse_optional_datetime(item.get("release_date")),
            last_modified=_parse_optional_datetime(item.get("last_modified")),
            size_bytes=item.get("size_bytes"),
            content_type=item.get("content_type"),
            probed_at=_parse_optional_datetime(item.get("probed_at")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _parse_not_found_item(item: object) -> NotFoundProbe | None:
    if isinstance(item, str):
        return NotFoundProbe(version=item) if item else None
    if not isinstance(item, dict):
        return None
    version = item.get("version")
    if not version:
        return None
    return NotFoundProbe(
        version=version,
        title=item.get("title") or version,
        source=item.get("source") or "",
        kind=item.get("kind") or "Unknown",
        release_date=_parse_optional_datetime(item.get("release_date")),
        probed_at=_parse_optional_datetime(item.get("probed_at")),
    )


def _probe_rank(item: FoundArtifact) -> tuple:
    """Higher is better when collapsing duplicate URLs/versions."""
    file_version = extract_version(unquote(item.url.rsplit("/", 1)[-1])) or ""
    return (
        1 if file_version == item.version else 0,
        0 if item.version.startswith("0.") else 1,
        item.size_bytes or 0,
        1 if item.kind not in ("", "Unknown") else 0,
    )


def dedupe_probes(probes: Iterable[FoundArtifact]) -> list[FoundArtifact]:
    """One entry per URL and per version — drop alias duplicates."""
    by_url: dict[str, FoundArtifact] = {}
    for item in probes:
        current = by_url.get(item.url)
        if current is None or _probe_rank(item) > _probe_rank(current):
            by_url[item.url] = item

    by_version: dict[str, FoundArtifact] = {}
    for item in by_url.values():
        current = by_version.get(item.version)
        if current is None or _probe_rank(item) > _probe_rank(current):
            by_version[item.version] = item
    return list(by_version.values())


def load_probe_store(path: Path | None = None) -> ProbeStore:
    cache_path = path or probes_json_path()
    if path is None:
        ensure_probes_file()
    if not cache_path.exists():
        return ProbeStore()
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ProbeStore()

    if isinstance(payload, list):
        raw_probes = payload
        raw_not_found: list = []
    else:
        raw_probes = payload.get("probes") or payload.get("artifacts") or []
        raw_not_found = payload.get("not_found") or []

    probes = dedupe_probes(
        item for item in (_parse_found_item(x) for x in raw_probes) if item
    )
    not_found = [
        item for item in (_parse_not_found_item(x) for x in raw_not_found) if item
    ]
    # Drop not_found versions that are already found.
    found_versions = {item.version for item in probes}
    not_found = [item for item in not_found if item.version not in found_versions]
    # Dedupe not_found by version.
    not_found_by = {item.version: item for item in not_found}
    return ProbeStore(probes=probes, not_found=list(not_found_by.values()))


def load_artifact_cache(path: Path | None = None) -> list[FoundArtifact]:
    return load_probe_store(path).probes


def save_probe_store(store: ProbeStore, path: Path) -> Path:
    probes = sorted(dedupe_probes(store.probes), key=lambda item: item.sort_key(), reverse=True)
    found_versions = {item.version for item in probes}
    not_found_versions = sorted(
        {
            item.version
            for item in store.not_found
            if item.version not in found_versions
        }
    )
    payload = {
        "probes": [item.to_json() for item in probes],
        "not_found": not_found_versions,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def save_artifacts_json(
    artifacts: Iterable[FoundArtifact],
    path: Path,
    *,
    note: str | None = None,
    list_key: str = "probes",
) -> Path:
    store = load_probe_store(path) if path.exists() else ProbeStore()
    by_version = {item.version: item for item in store.probes}
    for item in artifacts:
        by_version[item.version] = item
    store.probes = list(by_version.values())
    found_versions = {item.version for item in store.probes}
    store.not_found = [item for item in store.not_found if item.version not in found_versions]
    _ = (note, list_key)
    return save_probe_store(store, path)


def record_successful_probe(
    found: FoundArtifact,
    *,
    path: Path | None = None,
    quiet: bool = False,
    log=None,
) -> Path:
    """Merge a successful probe into heat_probes.json."""
    global PROBES_JSON_PATH
    probes_path = path or probes_json_path()
    PROBES_JSON_PATH = probes_path
    store = load_probe_store(probes_path)
    by_version = {item.version: item for item in store.probes}
    by_version[found.version] = found
    store.probes = dedupe_probes(by_version.values())
    store.not_found = [item for item in store.not_found if item.version != found.version]
    save_probe_store(store, probes_path)

    if not quiet and log:
        log.info(f"saved probe -> {probes_path}")
    return probes_path


def record_not_found_probe(
    release: Release,
    *,
    path: Path | None = None,
) -> Path:
    probes_path = path or probes_json_path()
    store = load_probe_store(probes_path)
    if release.version in store.found_versions:
        return probes_path
    by_version = {item.version: item for item in store.not_found}
    by_version[release.version] = NotFoundProbe(version=release.version)
    store.not_found = list(by_version.values())
    save_probe_store(store, probes_path)
    return probes_path


def compute_probe_coverage(releases: Iterable[Release]) -> ProbeCoverage:
    catalog = list(releases)
    store = load_probe_store()
    found_by_version = {item.version: item for item in store.probes}
    not_found_by_version = {item.version: item for item in store.not_found}

    found: list[FoundArtifact] = []
    not_found: list[NotFoundProbe] = []
    not_probed: list[Release] = []

    for release in catalog:
        if release.version in found_by_version:
            found.append(found_by_version[release.version])
        elif release.version in not_found_by_version:
            not_found.append(not_found_by_version[release.version])
        else:
            not_probed.append(release)

    return ProbeCoverage(
        catalog=catalog,
        found=found,
        not_found=not_found,
        not_probed=not_probed,
    )


def probe_all_artifacts(
    releases: Iterable[Release],
    *,
    quiet: bool = False,
    log=None,
    only_new: bool = True,
    verify_old: bool = False,
    progress: bool = True,
) -> list[FoundArtifact]:
    """Probe catalog releases against the artifact server.

    Modes:
    - only_new: probe versions not yet in heat_probes.json (found or not_found)
    - verify_old: re-check previously found URLs are still online
    """
    from tqdm import tqdm

    release_list = list(releases)
    nearby_versions = [
        extract_raw_version(item.title) or item.version for item in release_list
    ]
    store = load_probe_store()
    by_version = {item.version: item for item in store.probes}
    known = store.found_versions | store.not_found_versions

    work: list[tuple[str, Release | FoundArtifact]] = []

    if verify_old:
        for cached in store.probes:
            work.append(("verify", cached))

    if only_new:
        candidates = [release for release in release_list if release.version not in known]
    else:
        # Full pass: skip versions already covered by verify_old in this run.
        skip = store.found_versions if verify_old else set()
        candidates = [release for release in release_list if release.version not in skip]

    for release in candidates:
        work.append(("probe", release))

    if not work:
        if not quiet and log:
            log.ok("nothing to probe (cache already covers catalog)")
        return sorted(by_version.values(), key=lambda item: item.sort_key(), reverse=True)

    bar = None
    if progress and not quiet:
        bar = tqdm(total=len(work), unit="ver", desc="probing", ncols=88)

    found_count = 0
    missing_count = 0
    stale_count = 0

    try:
        for action, item in work:
            if action == "verify":
                cached = item  # type: ignore[assignment]
                assert isinstance(cached, FoundArtifact)
                if bar:
                    bar.set_postfix_str(f"verify {cached.version}", refresh=False)
                metadata = _head_metadata(cached.url)
                if metadata:
                    refreshed = FoundArtifact(
                        version=cached.version,
                        filename=cached.filename,
                        url=cached.url,
                        kind=cached.kind,
                        title=cached.title,
                        source=cached.source,
                        release_date=cached.release_date,
                        last_modified=metadata.last_modified or cached.last_modified,
                        size_bytes=metadata.size_bytes or cached.size_bytes,
                        content_type=metadata.content_type or cached.content_type,
                        probed_at=datetime.now(timezone.utc),
                    )
                    by_version[refreshed.version] = refreshed
                    record_successful_probe(refreshed, quiet=True)
                    found_count += 1
                else:
                    stale_count += 1
                    # Fall through to full re-probe for this version.
                    release = next(
                        (r for r in release_list if r.version == cached.version),
                        Release(
                            cached.version,
                            cached.title,
                            cached.release_date,
                            cached.source,
                            cached.kind,
                            None,
                        ),
                    )
                    found = probe_artifact_details(
                        release.version,
                        release=release,
                        quiet=True,
                        log=None,
                        nearby_versions=nearby_versions,
                    )
                    if found:
                        by_version[found.version] = found
                        found_count += 1
                    else:
                        by_version.pop(cached.version, None)
                        record_not_found_probe(release)
                        missing_count += 1
            else:
                release = item  # type: ignore[assignment]
                assert isinstance(release, Release)
                if bar:
                    bar.set_postfix_str(f"probe {release.version}", refresh=False)
                found = probe_artifact_details(
                    release.version,
                    release=release,
                    quiet=True,
                    log=None,
                    nearby_versions=nearby_versions,
                )
                if found:
                    by_version[found.version] = found
                    found_count += 1
                else:
                    record_not_found_probe(release)
                    missing_count += 1

            if bar:
                bar.update(1)
    finally:
        if bar:
            bar.close()

    artifacts = sorted(by_version.values(), key=lambda item: item.sort_key(), reverse=True)
    # Persist merged found set + keep not_found from disk after updates.
    store = load_probe_store()
    store.probes = artifacts
    save_probe_store(store, probes_json_path())

    if not quiet and log:
        log.ok(
            f"probe-all done: found/updated={found_count}, "
            f"not_found={missing_count}, stale_rechecked={stale_count}, "
            f"total_saved={len(artifacts)}"
        )
        log.info(f"probes file: {probes_json_path()}")
    return artifacts
