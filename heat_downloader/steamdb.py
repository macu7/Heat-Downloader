"""SteamDB patchnotes fetcher."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import bs4
from curl_cffi import requests as cf_requests

from heat_downloader.http import STEAM_APP_ID, STEAMDB_PATCHNOTES
from heat_downloader.models import Release, detect_kind, extract_version

STEAMDB_RSS = f"https://steamdb.info/api/PatchnotesRSS/?appid={STEAM_APP_ID}"
CF_IMPERSONATE = "safari"

_BUILD_ID_RE = re.compile(r"^\d+$")
_BUILD_GUID_RE = re.compile(r"build#(\d+)")
_DESC_SUFFIX_RE = re.compile(r"\s*\(SteamDB Build \d+\)\s*$")
_DATE_RE = re.compile(
    r"(\d{1,2})\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{4})",
    re.IGNORECASE,
)
_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def _fetch_text(url: str) -> str:
    response = cf_requests.get(url, impersonate=CF_IMPERSONATE, timeout=20)
    response.raise_for_status()
    return response.text


def _is_cloudflare_challenge(html: str) -> bool:
    markers = (
        "Checking your browser",
        "Enable JavaScript and cookies",
        "cf-error",
        "challenge-platform",
    )
    return any(marker in html for marker in markers)


def _cell_text(cell: bs4.Tag | None) -> str:
    if cell is None:
        return ""
    return cell.get_text(" ", strip=True)


def _parse_build_date(date_text: str, time_text: str) -> datetime | None:
    match = _DATE_RE.search(date_text)
    if not match:
        return None

    day = int(match.group(1))
    month = _MONTHS[match.group(2).lower()]
    year = int(match.group(3))

    hour = 0
    minute = 0
    if time_text and ":" in time_text:
        parts = time_text.split(":", 1)
        try:
            hour = int(parts[0])
            minute = int(parts[1][:2])
        except ValueError:
            pass

    try:
        return datetime(year, month, day, hour, minute)
    except ValueError:
        return None


def _row_timestamp(row: bs4.Tag, date_text: str, time_text: str) -> datetime | None:
    data_date = row.get("data-date")
    if data_date:
        try:
            return datetime.fromtimestamp(int(data_date), tz=timezone.utc).replace(tzinfo=None)
        except ValueError:
            pass
    return _parse_build_date(date_text, time_text)


def _parse_build_row(row: bs4.Tag) -> Release | None:
    cells = row.find_all("td")
    if len(cells) < 4:
        return None

    date_text = _cell_text(cells[0])
    time_text = _cell_text(cells[2]) if len(cells) > 2 else ""
    title = _cell_text(cells[3])

    buildid = None
    last = _cell_text(cells[-1])
    if _BUILD_ID_RE.match(last):
        buildid = int(last)

    if not title or title.lower() == "no title":
        title = f"Steam build {buildid}" if buildid else "Untitled patch"

    version = extract_version(title)
    if not version:
        return None

    return Release(
        version=version,
        title=title,
        date=_row_timestamp(row, date_text, time_text),
        source="SteamDB",
        kind=detect_kind(title),
        buildid=buildid,
    )


def parse_steamdb_html(html: str) -> list[Release]:
    """Parse SteamDB patchnotes HTML (`tbody#js-builds` rows)."""
    soup = bs4.BeautifulSoup(html, "html.parser")
    tbody = soup.select_one("#js-builds")
    if tbody is None:
        return []

    releases: list[Release] = []
    for row in tbody.select("tr"):
        release = _parse_build_row(row)
        if release:
            releases.append(release)
    return releases


def parse_steamdb_rss(xml_text: str) -> list[Release]:
    """Parse SteamDB PatchnotesRSS feed."""
    root = ET.fromstring(xml_text)
    releases: list[Release] = []

    for item in root.findall("./channel/item"):
        guid = item.findtext("guid", "") or ""
        build_match = _BUILD_GUID_RE.search(guid)
        buildid = int(build_match.group(1)) if build_match else None

        description = (item.findtext("description") or "").strip()
        title = _DESC_SUFFIX_RE.sub("", description).strip()
        if not title or title.startswith("SteamDB Build"):
            title = f"Steam build {buildid}" if buildid else "Untitled patch"

        version = extract_version(title)
        if not version:
            continue

        pub_date = item.findtext("pubDate")
        date = None
        if pub_date:
            try:
                date = parsedate_to_datetime(pub_date).replace(tzinfo=None)
            except (TypeError, ValueError, OverflowError):
                pass

        releases.append(
            Release(
                version=version,
                title=title,
                date=date,
                source="SteamDB",
                kind=detect_kind(title),
                buildid=buildid,
            )
        )

    return releases


def fetch_steamdb_releases() -> list[Release]:
    """Fetch recent SteamDB builds via the public PatchnotesRSS feed."""
    try:
        releases = parse_steamdb_rss(_fetch_text(STEAMDB_RSS))
        if releases:
            return releases
    except Exception as rss_exc:
        html_exc: Exception | None = None
        try:
            html = _fetch_text(STEAMDB_PATCHNOTES)
            if _is_cloudflare_challenge(html):
                raise RuntimeError(
                    "SteamDB returned a Cloudflare challenge page"
                ) from rss_exc
            releases = parse_steamdb_html(html)
            if releases:
                return releases
            raise RuntimeError("SteamDB page did not contain #js-builds patch rows")
        except Exception as exc:
            html_exc = exc
        raise RuntimeError(
            f"SteamDB request failed: {rss_exc}"
        ) from (html_exc or rss_exc)

    raise RuntimeError("SteamDB RSS feed contained no versioned builds")
