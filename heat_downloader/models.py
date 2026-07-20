"""Release model and merge helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Iterable

VERSION_RE = re.compile(r"\b(?:0\.)?\d+\.\d+\.\d+(?:\.\d+)?\b")

KNOWN_KINDS = ("Milestone", "Fix", "Hotfix", "Test", "Experimental")


def normalize_version(version: str) -> str:
    """Canonicalize the two known Patreon prefix conventions."""
    parts = version.split(".")
    if len(parts) == 4 and parts[0] in {"0", "1"} and parts[1] == "1":
        return ".".join(parts[1:])
    return version


def _version_key(version: str) -> tuple[int, ...] | None:
    """Return numeric version components, or None for an invalid value."""
    parts = version.split(".")
    if not 3 <= len(parts) <= 4 or not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def _version_distance(left: str, right: str) -> int:
    """A simple numeric distance used only to choose fallback prefixes."""
    left_key = _version_key(left)
    right_key = _version_key(right)
    if left_key is None or right_key is None:
        return 10**12
    left_key = (0,) * (4 - len(left_key)) + left_key
    right_key = (0,) * (4 - len(right_key)) + right_key
    return sum(abs(a - b) * (1000 ** (3 - index)) for index, (a, b) in enumerate(zip(left_key, right_key)))


def archive_version_candidates(
    version: str,
    *,
    nearby_versions: Iterable[str] = (),
) -> tuple[str, ...]:
    """Generate exact, shortened, and nearby-prefix archive version forms.

    A four-part source version is checked verbatim and then without its first
    component. For a three-part source version, its exact spelling is checked.
    Prefixes for fallback four-part forms are learned from nearby source posts,
    rather than being hard-coded to 0 or 1.
    """
    raw = version.strip()
    parts = raw.split(".")
    if not 3 <= len(parts) <= 4 or not all(part.isdigit() for part in parts):
        return (raw,)

    base = ".".join(parts[-3:])
    candidates = [raw]
    if base != raw:
        candidates.append(base)

    ranked_nearby = sorted(
        (item.strip() for item in nearby_versions),
        key=lambda item: _version_distance(base, ".".join(item.split(".")[-3:])),
    )
    for nearby in ranked_nearby[:8]:
        nearby_parts = nearby.split(".")
        if len(nearby_parts) != 4 or not all(part.isdigit() for part in nearby_parts):
            continue
        candidates.append(f"{nearby_parts[0]}.{base}")

    return tuple(dict.fromkeys(candidates))

def extract_raw_version(text: str) -> str | None:
    match = VERSION_RE.search(text)
    return match.group(0) if match else None


def extract_version(text: str) -> str | None:
    version = extract_raw_version(text)
    if not version:
         return None
    return normalize_version(version)


def detect_kind(title: str) -> str:
    """Infer release type from a patch/post title."""
    lower = title.lower()
    if "hotfix" in lower or re.search(r"\bfix\b", lower):
        return "Fix"
    if "milestone" in lower:
        return "Milestone"
    if "experimental" in lower:
        return "Experimental"
    if "test" in lower:
        return "Test"
    return "Unknown"


@dataclass(frozen=True, slots=True)
class Release:
    version: str
    title: str
    date: datetime | None
    source: str
    kind: str
    buildid: int | None = None

    def with_normalized_version(self) -> Release:
        return replace(self, version=normalize_version(self.version))


def _pick_title(a: Release, b: Release) -> str:
    if a.source == "Patreon" and a.title:
        return a.title
    if b.source == "Patreon" and b.title:
        return b.title
    return a.title or b.title


def _is_build_source(source: str) -> bool:
    return source in ("Steam", "SteamDB")


def _pick_buildid(a: Release, b: Release) -> int | None:
    if _is_build_source(a.source) and a.buildid is not None:
        return a.buildid
    if _is_build_source(b.source) and b.buildid is not None:
        return b.buildid
    return a.buildid if a.buildid is not None else b.buildid


def _pick_kind(a: Release, b: Release) -> str:
    patreon = a if a.source == "Patreon" else b if b.source == "Patreon" else None
    steam = (
        a
        if _is_build_source(a.source)
        else b
        if _is_build_source(b.source)
        else None
    )

    if patreon and steam:
        if patreon.kind in ("", "Unknown") and steam.kind not in ("", "Unknown"):
            return steam.kind
        if patreon.kind not in ("", "Unknown"):
            return patreon.kind
        if steam.kind not in ("", "Unknown"):
            return steam.kind
        return "Unknown"

    for release in (a, b):
        if release.kind not in ("", "Unknown"):
            return release.kind
    return a.kind or b.kind or "Unknown"


def _pick_date(a: Release, b: Release) -> datetime | None:
    if a.date and b.date:
        return max(a.date, b.date)
    return a.date or b.date


def _format_source(sources: set[str]) -> str:
    order = ("Patreon", "Steam", "SteamDB", "AnthroHeat")
    ordered = [name for name in order if name in sources]
    extras = sorted(sources - set(order))
    return " + ".join(ordered + extras)


def _merge_pair(existing: Release, incoming: Release, sources: set[str]) -> Release:
    return Release(
        version=existing.version,
        title=_pick_title(existing, incoming),
        date=_pick_date(existing, incoming),
        source=_format_source(sources),
        kind=_pick_kind(existing, incoming),
        buildid=_pick_buildid(existing, incoming),
    )


def merge_releases(*groups: Iterable[Release]) -> list[Release]:
    """Merge release lists keyed by version with source-specific preferences."""
    by_version: dict[str, Release] = {}
    sources_by_version: dict[str, set[str]] = {}

    for release in (r.with_normalized_version() for group in groups for r in group):
        version = release.version
        if version not in by_version:
            by_version[version] = release
            sources_by_version[version] = {release.source}
            continue

        sources_by_version[version].add(release.source)
        by_version[version] = _merge_pair(
            by_version[version],
            release,
            sources_by_version[version],
        )

    return sorted(
        by_version.values(),
        key=lambda r: r.date or datetime.min.replace(tzinfo=None),
        reverse=True,
    )


def format_date(release: Release) -> str:
    if not release.date:
        return "unknown"
    return release.date.strftime("%Y-%m-%d")


def format_datetime(release: Release) -> str:
    if not release.date:
        return "unknown"
    return release.date.strftime("%Y-%m-%d %H:%M")


def format_source_tag(source: str) -> str:
    return f"[{source}]"
