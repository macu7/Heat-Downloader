"""Patreon release fetcher."""

from __future__ import annotations

from datetime import datetime, timezone

from heat_downloader.http import HEAT_CAMPAIGN_ID, PATREON_API, SESSION
from heat_downloader.models import Release, detect_kind, extract_version

RELEASE_KEYWORDS = {"heat", "milestone", "test", "build", "hotfix", "fix", "experimental"}


def _parse_published_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def fetch_patreon_releases():
    """Yield Release objects from Patreon posts, newest first."""
    params = {
        "filter[campaign_id]": HEAT_CAMPAIGN_ID,
        "include": "campaign",
        "fields[post]": "title,published_at,url",
        "fields[user]": "full_name",
        "sort": "-published_at",
    }

    while True:
        try:
            response = SESSION.get(PATREON_API, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
        except Exception:
            return

        for post in data.get("data", []):
            if post.get("type") != "post":
                continue

            attrs = post.get("attributes", {})
            title = attrs.get("title", "")
            if not any(kw in title.lower().split() for kw in RELEASE_KEYWORDS):
                continue

            version = extract_version(title)
            if not version:
                continue

            yield Release(
                version=version,
                title=title,
                date=_parse_published_at(attrs.get("published_at")),
                source="Patreon",
                kind=detect_kind(title),
                buildid=None,
            )

        cursor = data.get("meta", {}).get("pagination", {}).get("cursors", {}).get("next")
        if not cursor:
            break
        params["page[cursor]"] = cursor
