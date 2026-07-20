"""Shared HTTP session for Heat Downloader."""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

PATREON_API = "https://www.patreon.com/api/posts"
STEAMDB_PATCHNOTES = "https://steamdb.info/app/2236060/patchnotes/"
ARTIFACT_SERVER = "https://anthroheat.net/"

HEAT_CAMPAIGN_ID = 4451021
STEAM_APP_ID = 2236060

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def make_session(*, api: bool = False) -> requests.Session:
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retries))
    session.mount("http://", HTTPAdapter(max_retries=retries))
    if api:
        session.headers["User-Agent"] = "HeatDownloader/3.0"
    else:
        session.headers.update(
            {
                "User-Agent": BROWSER_UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
    return session


SESSION = make_session(api=True)
BROWSER = make_session(api=False)
