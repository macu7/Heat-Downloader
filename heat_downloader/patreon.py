"""Patreon release fetcher."""

from __future__ import annotations

from datetime import datetime, timezone

from curl_cffi import requests as cf_requests

from heat_downloader.http import HEAT_CAMPAIGN_ID, PATREON_API
from heat_downloader.models import Release, detect_kind, extract_version

PATREON_PAGE = "https://www.patreon.com/heatgame"

RELEASE_KEYWORDS = {"heat", "milestone", "test", "build", "hotfix", "fix", "experimental"}

CF_FALLBACKS = (
    "firefox135",
    "firefox133",
    "chrome136",
    "chrome131",
    "safari18_0",
    "safari",
    "chrome",
)


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


def _make_session(impersonate: str):
    session = cf_requests.Session(impersonate=impersonate)
    session.headers.update(
        {
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.patreon.com/",
            "Origin": "https://www.patreon.com",
        }
    )
    try:
        session.get("https://www.patreon.com/", timeout=25)
    except Exception:
        pass
    return session


def _patreon_get(session, params: dict) -> dict:
    response = session.get(PATREON_API, params=params, timeout=25)
    if response.status_code == 403:
        raise RuntimeError("403 Forbidden")
    response.raise_for_status()
    return response.json()


def _release_from_post(attrs: dict) -> Release | None:
    title = attrs.get("title", "")
    if not any(kw in title.lower().split() for kw in RELEASE_KEYWORDS):
        return None
    version = extract_version(title)
    if not version:
        return None
    return Release(
        version=version,
        title=title,
        date=_parse_published_at(attrs.get("published_at")),
        source="Patreon",
        kind=detect_kind(title),
        buildid=None,
    )


def fetch_patreon_releases_live() -> list[Release]:
    """Fetch from Patreon API (raises RuntimeError on total failure)."""
    params = {
        "filter[campaign_id]": HEAT_CAMPAIGN_ID,
        "include": "campaign",
        "fields[post]": "title,published_at,url",
        "fields[user]": "full_name",
        "sort": "-published_at",
        "json-api-version": "1.0",
    }
    errors: list[str] = []
    session = None
    for impersonate in CF_FALLBACKS:
        try:
            session = _make_session(impersonate)
            _patreon_get(session, params)
            break
        except Exception as exc:
            errors.append(f"{impersonate}: {exc}")
            session = None

    if session is None:
        raise RuntimeError("Patreon API request failed: " + "; ".join(errors))

    releases: list[Release] = []
    while True:
        data = _patreon_get(session, params)
        for post in data.get("data", []):
            if post.get("type") != "post":
                continue
            release = _release_from_post(post.get("attributes") or {})
            if release:
                releases.append(release)
        cursor = data.get("meta", {}).get("pagination", {}).get("cursors", {}).get("next")
        if not cursor:
            break
        params["page[cursor]"] = cursor

    return releases


def fetch_patreon_releases():
    """Yield Patreon releases."""
    yield from fetch_patreon_releases_live()
