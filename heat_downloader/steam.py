"""Steam partner events fetcher (official API, no Cloudflare)."""

from __future__ import annotations

from datetime import datetime, timezone

from heat_downloader.http import SESSION, STEAM_APP_ID
from heat_downloader.models import Release, detect_kind, extract_version

STEAM_EVENTS_API = (
    "https://store.steampowered.com/events/ajaxgetpartnereventspageable/"
)
PATCH_EVENT_TYPES = {12, 13, 14}
PAGE_SIZE = 50


def _parse_event_timestamp(value: int | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc).replace(tzinfo=None)


def _is_patch_event(event: dict) -> bool:
    title = event.get("event_name") or ""
    if not extract_version(title):
        return False

    build_id = event.get("build_id") or 0
    event_type = event.get("event_type") or 0
    if build_id > 0:
        return True
    if event_type in PATCH_EVENT_TYPES:
        return True

    lower = title.lower()
    return any(word in lower for word in ("patch", "hotfix", "update", "milestone"))


def _event_to_release(event: dict) -> Release | None:
    title = (event.get("event_name") or "").strip()
    version = extract_version(title)
    if not version:
        return None

    build_id = event.get("build_id") or 0
    return Release(
        version=version,
        title=title,
        date=_parse_event_timestamp(event.get("rtime32_start_time")),
        source="Steam",
        kind=detect_kind(title),
        buildid=build_id if build_id > 0 else None,
    )


def fetch_steam_releases() -> list[Release]:
    """Fetch patch history from Steam's partner events API."""
    releases: list[Release] = []
    offset = 0

    while True:
        try:
            response = SESSION.get(
                STEAM_EVENTS_API,
                params={"appid": STEAM_APP_ID, "offset": offset, "count": PAGE_SIZE},
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise RuntimeError(f"Steam events request failed: {exc}") from exc

        events = payload.get("events") or []
        if not events:
            break

        for event in events:
            if not _is_patch_event(event):
                continue
            release = _event_to_release(event)
            if release:
                releases.append(release)

        if len(events) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    if not releases:
        raise RuntimeError("Steam returned no patch events for this app")
    return releases
