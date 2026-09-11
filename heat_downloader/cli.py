"""CLI, config, and orchestration."""

from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from dataclasses import dataclass
from itertools import islice
from pathlib import Path

from heat_downloader import __version__
from heat_downloader.artifacts import (
    compute_probe_coverage,
    ensure_probes_file,
    load_artifact_cache,
    load_probe_store,
    probe_all_artifacts,
    probe_artifact,
    probes_json_path,
    save_artifacts_json,
)
from heat_downloader.download import download, fmt_size
from heat_downloader.extract import extract
from heat_downloader.models import (
    Release,
    archive_version_candidates,
    extract_raw_version,
    extract_version,
    format_date,
    format_datetime,
    format_source_tag,
    merge_releases,
    normalize_version,
    version_sort_key,
)
from heat_downloader.patreon import PATREON_PAGE, fetch_patreon_releases
from heat_downloader.steamdb import fetch_steamdb_releases

CONFIG_PATH = Path.home() / ".heat_downloader.json"
DEFAULT_CATALOG_JSON = Path.cwd() / "heat_artifacts.json"

RED, GREEN, BLUE, YELLOW, CYAN, DIM, BOLD, RESET = (
    "\033[91m",
    "\033[92m",
    "\033[94m",
    "\033[93m",
    "\033[96m",
    "\033[2m",
    "\033[1m",
    "\033[0m",
)


@dataclass
class Log:
    def info(self, msg: str) -> None:
        print(f"{BLUE}[INFO]{RESET} {msg}")

    def warn(self, msg: str) -> None:
        print(f"{YELLOW}[WARN]{RESET} {msg}")

    def err(self, msg: str) -> None:
        print(f"{RED}[ERROR]{RESET} {msg}")

    def ok(self, msg: str) -> None:
        print(f"{GREEN}[OK]{RESET} {msg}")


LOG = Log()

# Session cache so list/probe don't refetch catalogs every menu action.
_RELEASES_CACHE: list[Release] | None = None


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default not in (None, "") else ""
    value = input(f"  {prompt}{suffix}: ").strip()
    if not value and default is not None:
        return default
    return value


def ask_yes(prompt: str, *, default_yes: bool = True) -> bool:
    hint = "Y/n" if default_yes else "y/N"
    answer = input(f"  {prompt} [{hint}]: ").strip().lower()
    if not answer:
        return default_yes
    return answer in {"y", "yes"}


def pause() -> None:
    input(f"\n  {DIM}Press Enter to return to menu...{RESET}")


def get_releases(
    *,
    include_steamdb: bool = True,
    refresh: bool = False,
) -> list[Release]:
    global _RELEASES_CACHE
    if _RELEASES_CACHE is not None and not refresh and include_steamdb:
        return _RELEASES_CACHE

    groups: list[list[Release]] = []

    try:
        groups.append(list(fetch_patreon_releases()))
    except RuntimeError as exc:
        LOG.warn(str(exc))
        LOG.info("Open Patreon in your browser, wait for the page to load, then come back:")
        print(f"       {CYAN}{PATREON_PAGE}{RESET}")
        if sys.stdin.isatty():
            try:
                webbrowser.open(PATREON_PAGE)
            except Exception:
                pass
            if ask_yes("Retry Patreon now?", default_yes=True):
                try:
                    groups.append(list(fetch_patreon_releases()))
                    LOG.ok("Patreon OK")
                except RuntimeError as retry_exc:
                    LOG.warn(str(retry_exc))
                    LOG.warn("Continuing without Patreon.")
            else:
                LOG.warn("Continuing without Patreon.")
        else:
            LOG.warn("Continuing without Patreon.")

    if include_steamdb:
        try:
            groups.append(fetch_steamdb_releases())
        except RuntimeError as exc:
            LOG.warn(str(exc))

    merged = merge_releases(*groups)
    if include_steamdb:
        _RELEASES_CACHE = merged
    return merged


def print_checking(release: Release) -> None:
    print()
    LOG.info(f"checking {release.version}")
    print(f"  source : {release.source}")
    print(f"  kind   : {release.kind}")
    print(f"  date   : {format_datetime(release)}")
    print(f"  title  : {release.title}")
    source_version = extract_raw_version(release.title)
    if source_version:
        print(f"  source version   : {source_version}")
    print(f"  canonical version: {release.version}")
    print(
        "  archive forms    : "
        + ", ".join(archive_version_candidates(source_version or release.version))
    )


def successful_probe_versions() -> set[str]:
    return {item.version for item in load_probe_store().probes}


def release_has_successful_probe(release: Release, found_versions: set[str]) -> bool:
    candidates = {release.version, normalize_version(release.version)}
    raw = extract_raw_version(release.title)
    if raw:
        candidates.add(raw)
        candidates.add(normalize_version(raw))
    candidates.update(archive_version_candidates(raw or release.version))
    return any(version in found_versions for version in candidates)


def format_release_list_line(
    index: int,
    release: Release,
    *,
    probed: bool = False,
) -> str:
    date_part = f"[{format_date(release)}]" if release.date else "[unknown date]"
    source_part = format_source_tag(release.source)
    kind_part = release.kind if release.kind != "Unknown" else ""
    probe_part = f"{GREEN}probed{RESET}" if probed else f"{DIM}no probe{RESET}"
    meta = " ".join(part for part in (date_part, source_part, kind_part) if part)
    return (
        f"  {DIM}{index:>3}.{RESET} {BOLD}{release.version}{RESET}  {probe_part}\n"
        f"       {DIM}{meta}{RESET}\n"
        f"       {release.title}"
    )


def find_latest(*, quiet: bool = False, include_steamdb: bool = True) -> tuple[Release | None, str | None]:
    releases = get_releases(include_steamdb=include_steamdb)
    nearby_versions = [extract_raw_version(item.title) or item.version for item in releases]
    for release in releases:
        if not quiet:
            print_checking(release)
        url = probe_artifact(
            release.version,
            release=release,
            quiet=quiet,
            log=LOG,
            nearby_versions=nearby_versions,
        )
        if url:
            return release, url
        if not quiet:
            LOG.warn(f"no artifact for {release.version}")
    LOG.err("no downloadable release found")
    return None, None


def find_release(version: str, *, include_steamdb: bool = True) -> Release | None:
    needle = version.strip().lower().lstrip("v")
    for release in get_releases(include_steamdb=include_steamdb):
        if release.version.lower() == needle:
            return release
        raw = extract_raw_version(release.title)
        if raw and raw.lower() == needle:
            return release
    return None


def resolve_output_dir(cfg: dict, *, ask_confirm: bool = True) -> Path | None:
    default_dir = cfg.get("output_dir") or os.getcwd()
    if ask_confirm:
        dest = ask("output dir", default_dir)
    else:
        dest = default_dir
    path = Path(dest)
    if not path.is_dir():
        LOG.err(f"directory not found: {dest}")
        return None
    cfg["output_dir"] = str(path)
    save_config(cfg)
    return path


def remember_download(cfg: dict, url: str, path: Path, version: str | None = None) -> None:
    detected = version or extract_version(path.name) or extract_version(url)
    if detected:
        cfg["last_downloaded_version"] = detected
    cfg["last_downloaded_file"] = str(path)
    save_config(cfg)


def downloaded_builds_in_dir(output_dir: str | Path) -> list[tuple[str, Path]]:
    folder = Path(output_dir)
    if not folder.is_dir():
        return []
    try:
        entries = list(folder.iterdir())
    except OSError:
        return []

    found: list[tuple[str, Path]] = []
    archive_suffixes = {".7z", ".zip", ".rar"}
    for entry in entries:
        if entry.is_file() and entry.suffix.lower() not in archive_suffixes:
            continue
        version = extract_version(entry.name)
        if version:
            found.append((version, entry))
    return found


def last_downloaded_version(cfg: dict) -> str | None:
    recorded = str(cfg.get("last_downloaded_version") or "").strip() or None
    output_dir = cfg.get("output_dir") or os.getcwd()
    found = downloaded_builds_in_dir(output_dir)
    if found:
        found.sort(
            key=lambda item: (
                version_sort_key(item[0]),
                item[1].stat().st_mtime if item[1].exists() else 0,
            ),
            reverse=True,
        )
        scanned = found[0][0]
        if recorded:
            return recorded if version_sort_key(recorded) >= version_sort_key(scanned) else scanned
        return scanned
    return recorded


def maybe_download_and_extract(
    url: str,
    cfg: dict,
    *,
    force: bool = False,
    version: str | None = None,
) -> None:
    if not ask_yes("Download this archive?", default_yes=True):
        return
    dest = resolve_output_dir(cfg, ask_confirm=True)
    if dest is None:
        return
    path = download(url, dest, force=force, log=LOG)
    if not path:
        return
    remember_download(cfg, url, path, version)
    if ask_yes("Extract with 7-Zip?", default_yes=True):
        extract(path, log=LOG)


def catalog_probe(
    *,
    output: Path | None = None,
    include_steamdb: bool = True,
    only_new: bool = True,
    verify_old: bool = False,
    quiet: bool = False,
) -> Path:
    """Probe catalog versions and update heat_probes.json."""
    ensure_probes_file()
    releases = get_releases(include_steamdb=include_steamdb, refresh=True)
    if not quiet:
        mode = []
        if only_new:
            mode.append("only-new")
        else:
            mode.append("all-unverified")
        if verify_old:
            mode.append("verify-old")
        LOG.info(f"catalog size: {len(releases)}  mode: {', '.join(mode)}")
        LOG.info(f"probes file: {probes_json_path()}")

    artifacts = probe_all_artifacts(
        releases,
        quiet=quiet,
        log=LOG,
        only_new=only_new,
        verify_old=verify_old,
        progress=not quiet,
    )
    out = output or probes_json_path()
    save_artifacts_json(artifacts, out)
    if not quiet:
        coverage = compute_probe_coverage(releases)
        totals = coverage.totals
        LOG.ok(
            f"coverage: found={totals['found']}  not_found={totals['not_found']}  "
            f"not_probed={totals['not_probed']}  catalog={totals['catalog']}"
        )
        for item in artifacts[:8]:
            stamp = (
                item.last_modified.astimezone().strftime("%Y-%m-%d")
                if item.last_modified
                else "?"
            )
            print(f"  {stamp}  {item.version:<10}  {item.filename}")
        if len(artifacts) > 8:
            print(f"  ... and {len(artifacts) - 8} more")
    return out


def print_probe_status(
    *,
    include_steamdb: bool = True,
    show_not_found: bool = False,
    show_not_probed: bool = False,
    limit: int = 50,
) -> None:
    ensure_probes_file()
    releases = get_releases(include_steamdb=include_steamdb)
    coverage = compute_probe_coverage(releases)
    totals = coverage.totals
    store = load_probe_store()

    print()
    LOG.info("probe status")
    print(f"  catalog versions : {totals['catalog']}")
    print(f"  found (probed OK): {totals['found']}")
    print(f"  not found        : {totals['not_found']}")
    print(f"  not probed yet   : {totals['not_probed']}")
    print(f"  probes file      : {probes_json_path()}")
    print(f"  saved probe rows : {len(store.probes)}")
    print(f"  saved not_found  : {len(store.not_found)}")

    if show_not_found:
        print()
        LOG.info(f"not found ({len(coverage.not_found)})")
        if not coverage.not_found:
            print("  (none)")
        for index, item in enumerate(coverage.not_found[:limit], 1):
            extra = f"  {item.title}" if item.title and item.title != item.version else ""
            print(f"  {DIM}{index:>3}.{RESET} {BOLD}{item.version}{RESET}{extra}")
        if len(coverage.not_found) > limit:
            print(f"  ... and {len(coverage.not_found) - limit} more")

    if show_not_probed:
        print()
        LOG.info(f"not probed ({len(coverage.not_probed)})")
        if not coverage.not_probed:
            print("  (none)")
        for index, item in enumerate(coverage.not_probed[:limit], 1):
            print(f"  {DIM}{index:>3}.{RESET} {BOLD}{item.version}{RESET}  {item.title}")
        if len(coverage.not_probed) > limit:
            print(f"  ... and {len(coverage.not_probed) - limit} more")


def print_banner() -> None:
    print()
    print(f"{BOLD}{CYAN}  +--------------------------------------+{RESET}")
    print(f"{BOLD}{CYAN}  |     Heat Downloader  v{__version__:<6}       |{RESET}")
    print(f"{BOLD}{CYAN}  +--------------------------------------+{RESET}")


def print_status(cfg: dict) -> None:
    out = cfg.get("output_dir") or os.getcwd()
    probes = load_artifact_cache(probes_json_path())
    cached = len(_RELEASES_CACHE) if _RELEASES_CACHE is not None else 0
    last = last_downloaded_version(cfg) or "none yet"
    print(f"  {DIM}output : {out}{RESET}")
    print(f"  {DIM}last   : {last}{RESET}")
    print(f"  {DIM}probes : {len(probes)} saved  |  catalog cache: {cached or 'empty'}{RESET}")


def print_main_menu() -> None:
    print()
    print(f"  {BOLD}1{RESET}) List recent releases")
    print(f"  {BOLD}2{RESET}) Probe latest")
    print(f"  {BOLD}3{RESET}) Probe specific version")
    print(f"  {BOLD}4{RESET}) Advanced")
    print(f"  {BOLD}q{RESET}) Quit")
    print()


def action_list_releases(cfg: dict) -> None:
    count_raw = ask("how many to show", "15")
    try:
        count = max(1, int(count_raw))
    except ValueError:
        count = 15

    refresh = ask_yes("refresh Patreon/SteamDB catalog?", default_yes=False)
    print()
    releases = list(islice(get_releases(refresh=refresh), count))
    if not releases:
        LOG.warn("no releases found")
        return

    found_versions = successful_probe_versions()
    for index, release in enumerate(releases, 1):
        print(
            format_release_list_line(
                index,
                release,
                probed=release_has_successful_probe(release, found_versions),
            )
        )
        print()

    pick = ask("probe # from list (Enter to skip)", "")
    if not pick:
        return
    try:
        index = int(pick)
    except ValueError:
        LOG.warn("invalid number")
        return
    if not 1 <= index <= len(releases):
        LOG.warn("number out of range")
        return

    release = releases[index - 1]
    nearby = [extract_raw_version(item.title) or item.version for item in releases]
    print_checking(release)
    url = probe_artifact(
        release.version,
        release=release,
        log=LOG,
        nearby_versions=nearby,
    )
    if not url:
        LOG.err(f"no artifact found for {release.version}")
        return
    LOG.ok(f"{release.version}  ->  {url}")
    maybe_download_and_extract(url, cfg, version=release.version)


def action_probe_latest(cfg: dict) -> None:
    release, url = find_latest()
    if not url or not release:
        return
    LOG.ok(f"latest: {release.version} {format_source_tag(release.source)}  ->  {url}")
    maybe_download_and_extract(url, cfg, version=release.version)


def action_probe_specific(cfg: dict) -> None:
    version = ask("version (e.g. 1.6.2.1 or 1.7.0)")
    if not version:
        return

    releases = get_releases()
    nearby = [extract_raw_version(item.title) or item.version for item in releases]
    release = find_release(version)
    if release:
        print_checking(release)
    else:
        LOG.warn("version not in catalog — probing filename templates anyway")

    url = probe_artifact(
        version,
        release=release,
        log=LOG,
        nearby_versions=nearby,
    )
    if not url:
        LOG.err(f"no artifact found for {version}")
        return
    LOG.ok(f"{version}  ->  {url}")
    maybe_download_and_extract(url, cfg, version=release.version if release else version)


def action_show_saved_probes() -> None:
    probes = load_artifact_cache(probes_json_path())
    if not probes:
        LOG.warn(f"no saved probes yet ({probes_json_path()})")
        return
    LOG.info(f"{len(probes)} saved probes in {probes_json_path()}")
    for index, item in enumerate(probes[:20], 1):
        stamp = (
            item.last_modified.astimezone().strftime("%Y-%m-%d")
            if item.last_modified
            else "unknown"
        )
        size = fmt_size(item.size_bytes) if item.size_bytes is not None else "?"
        print(f"  {DIM}{index:>3}.{RESET} {BOLD}{item.version}{RESET}  [{stamp}]  {size}")
        print(f"       {item.filename}")
    if len(probes) > 20:
        print(f"  {DIM}... and {len(probes) - 20} more{RESET}")


def action_advanced(cfg: dict) -> None:
    while True:
        print()
        print(f"  {BOLD}{CYAN}Advanced{RESET}")
        print(f"  {BOLD}1{RESET}) Set default output directory")
        print(f"  {BOLD}2{RESET}) Probe-all catalog (new / verify-old / all)")
        print(f"  {BOLD}3{RESET}) Probe status (found / not found / not probed)")
        print(f"  {BOLD}4{RESET}) Show saved probes")
        print(f"  {BOLD}5{RESET}) Refresh release catalog cache")
        print(f"  {BOLD}6{RESET}) Show paths / config")
        print(f"  {BOLD}7{RESET}) Force re-download a version")
        print(f"  {BOLD}b{RESET}) Back to main menu")
        print()
        choice = ask("choice").lower()

        if choice in {"b", "q", ""}:
            return

        if choice == "1":
            current = cfg.get("output_dir") or os.getcwd()
            new_dir = ask("new default dir", current)
            if new_dir and Path(new_dir).is_dir():
                cfg["output_dir"] = new_dir
                save_config(cfg)
                LOG.ok(f"saved: {new_dir}")
            elif new_dir:
                LOG.err(f"directory not found: {new_dir}")

        elif choice == "2":
            print()
            print(f"  {BOLD}a{RESET}) only new (not in probes file yet)  [default]")
            print(f"  {BOLD}b{RESET}) verify old found URLs still exist")
            print(f"  {BOLD}c{RESET}) both (verify old + probe new)")
            print(f"  {BOLD}d{RESET}) re-probe all unverified / not-found")
            mode = ask("mode", "a").lower()
            only_new = mode in {"a", "c", ""}
            verify_old = mode in {"b", "c"}
            if mode == "d":
                only_new = False
                verify_old = False
            print()
            catalog_probe(only_new=only_new, verify_old=verify_old)

        elif choice == "3":
            print_probe_status()
            if ask_yes("list not found?", default_yes=False):
                print_probe_status(show_not_found=True)
            if ask_yes("list not probed?", default_yes=False):
                print_probe_status(show_not_probed=True)

        elif choice == "4":
            print()
            action_show_saved_probes()

        elif choice == "5":
            print()
            releases = get_releases(refresh=True)
            LOG.ok(f"catalog refreshed: {len(releases)} releases")

        elif choice == "6":
            print()
            print(f"  config      : {CONFIG_PATH}")
            print(f"  probes      : {probes_json_path()}")
            print(f"  output dir  : {cfg.get('output_dir') or os.getcwd()}")
            print(f"  last down.  : {last_downloaded_version(cfg) or 'none yet'}")
            print(f"  version     : {__version__}")

        elif choice == "7":
            version = ask("version to force re-download")
            if not version:
                continue
            release = find_release(version)
            if release:
                print_checking(release)
            url = probe_artifact(version, release=release, log=LOG)
            if not url:
                LOG.err(f"no artifact found for {version}")
                continue
            LOG.ok(f"{version}  ->  {url}")
            if ask_yes("Download (force overwrite)?", default_yes=True):
                dest = resolve_output_dir(cfg, ask_confirm=True)
                if dest is None:
                    continue
                path = download(url, dest, force=True, log=LOG)
                if path:
                    remember_download(cfg, url, path, version)
                    if ask_yes("Extract with 7-Zip?", default_yes=True):
                        extract(path, log=LOG)
        else:
            LOG.warn("invalid choice")


def interactive() -> None:
    cfg = load_config()
    print_banner()

    while True:
        print_status(cfg)
        print_main_menu()
        choice = ask("choice").lower()

        try:
            if choice == "1":
                action_list_releases(cfg)
                pause()
            elif choice == "2":
                action_probe_latest(cfg)
                pause()
            elif choice == "3":
                action_probe_specific(cfg)
                pause()
            elif choice == "4":
                action_advanced(cfg)
            elif choice in {"q", "quit", "exit"}:
                print(f"\n  {DIM}bye{RESET}\n")
                return
            else:
                LOG.warn("invalid choice")
        except KeyboardInterrupt:
            print(f"\n\n  {DIM}interrupted — back to menu (q to quit){RESET}")
        except EOFError:
            print()
            return


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Heat Downloader — grab the latest Anthro Heat builds",
        epilog="Run without arguments for interactive mode.",
    )
    sub = parser.add_subparsers(dest="cmd")

    dl = sub.add_parser("download", aliases=["d"], help="Download latest (or specific) release")
    dl.add_argument("-o", "--output", default=None, help="Output directory (default: saved or cwd)")
    dl.add_argument("-v", "--version", default=None, help="Specific version (e.g. 1.4.0)")
    dl.add_argument("-f", "--force", action="store_true", help="Re-download even if file exists")
    dl.add_argument("-x", "--extract", action="store_true", help="Extract after download")
    dl.add_argument(
        "--patreon-only",
        action="store_true",
        help="Skip SteamDB and use Patreon releases only",
    )

    ls = sub.add_parser("list", aliases=["l"], help="List available releases")
    ls.add_argument("n", nargs="*", type=int, default=10, help="How many to show")
    ls.add_argument(
        "--patreon-only",
        action="store_true",
        help="Skip SteamDB and use Patreon releases only",
    )

    pb = sub.add_parser("probe", aliases=["p"], help="Probe release URL without downloading")
    pb.add_argument("-v", "--version", default=None, help="Specific version to probe")
    pb.add_argument(
        "--patreon-only",
        action="store_true",
        help="Skip SteamDB and use Patreon releases only",
    )

    cat = sub.add_parser(
        "catalog",
        aliases=["c", "probe-all"],
        help="Probe catalog versions into heat_probes.json",
    )
    cat.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output JSON path (default: heat_probes.json)",
    )
    cat.add_argument(
        "--only-new",
        action="store_true",
        default=True,
        help="Only probe versions not already in the probes file (default)",
    )
    cat.add_argument(
        "--all",
        dest="probe_all_unverified",
        action="store_true",
        help="Probe all catalog versions not currently verified in this run",
    )
    cat.add_argument(
        "--verify-old",
        action="store_true",
        help="Re-check previously found probe URLs are still available",
    )
    cat.add_argument(
        "--patreon-only",
        action="store_true",
        help="Skip SteamDB and use Patreon releases only",
    )
    cat.add_argument("-q", "--quiet", action="store_true", help="Less console output")

    ps = sub.add_parser(
        "probe-status",
        aliases=["ps", "probe-list"],
        help="Show found / not-found / not-probed coverage",
    )
    ps.add_argument(
        "--not-found",
        action="store_true",
        help="List versions probed but not found on the server",
    )
    ps.add_argument(
        "--not-probed",
        action="store_true",
        help="List catalog versions not probed yet",
    )
    ps.add_argument(
        "-n",
        "--limit",
        type=int,
        default=50,
        help="Max rows when listing (default: 50)",
    )
    ps.add_argument(
        "--patreon-only",
        action="store_true",
        help="Skip SteamDB and use Patreon releases only",
    )

    args = parser.parse_args()
    cfg = load_config()
    include_steamdb = not getattr(args, "patreon_only", False)
    ensure_probes_file()

    if args.cmd in ("download", "d"):
        dest = args.output or cfg.get("output_dir") or os.getcwd()
        if not Path(dest).is_dir():
            LOG.err(f"directory not found: {dest}")
            return
        if args.version:
            release = find_release(args.version, include_steamdb=include_steamdb)
            if release:
                print_checking(release)
            url = probe_artifact(args.version, release=release, log=LOG)
            if not url:
                LOG.err(f"artifact for {args.version} not found")
                return
        else:
            _, url = find_latest(include_steamdb=include_steamdb)
            if not url:
                return
        path = download(url, dest, force=args.force, log=LOG)
        if path:
            remember_download(
                cfg,
                url,
                path,
                args.version or extract_version(path.name),
            )
            if args.extract:
                extract(path, log=LOG)

    elif args.cmd in ("list", "l"):
        count = args.n[0] if isinstance(args.n, list) and args.n else (
            args.n if isinstance(args.n, int) else 10
        )
        found_versions = successful_probe_versions()
        for index, release in enumerate(
            islice(get_releases(include_steamdb=include_steamdb), count), 1
        ):
            print(
                format_release_list_line(
                    index,
                    release,
                    probed=release_has_successful_probe(release, found_versions),
                )
            )
            print()

    elif args.cmd in ("probe", "p"):
        if args.version:
            release = find_release(args.version, include_steamdb=include_steamdb)
            if release:
                print_checking(release)
            url = probe_artifact(args.version, release=release, log=LOG)
            if url:
                LOG.ok(f"{args.version}  ->  {url}")
            else:
                LOG.err(f"no artifact found for {args.version}")
        else:
            release, url = find_latest(include_steamdb=include_steamdb)
            if url and release:
                LOG.ok(
                    f"latest: {release.version} {format_source_tag(release.source)}  ->  {url}"
                )

    elif args.cmd in ("catalog", "c", "probe-all"):
        only_new = not bool(getattr(args, "probe_all_unverified", False))
        catalog_probe(
            output=Path(args.output) if args.output else None,
            include_steamdb=include_steamdb,
            only_new=only_new,
            verify_old=bool(args.verify_old),
            quiet=args.quiet,
        )

    elif args.cmd in ("probe-status", "ps", "probe-list"):
        print_probe_status(
            include_steamdb=include_steamdb,
            show_not_found=bool(args.not_found),
            show_not_probed=bool(args.not_probed),
            limit=args.limit,
        )

    else:
        interactive()
