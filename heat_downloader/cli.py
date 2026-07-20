"""CLI, config, and orchestration."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from itertools import islice
from pathlib import Path

from heat_downloader.artifacts import probe_artifact
from heat_downloader.download import download
from heat_downloader.extract import extract
from heat_downloader.models import (
    Release,
    archive_version_candidates,
    extract_raw_version,
    format_date,
    format_datetime,
    format_source_tag,
    merge_releases,
)
from heat_downloader.patreon import fetch_patreon_releases
from heat_downloader.steamdb import fetch_steamdb_releases

CONFIG_PATH = Path.home() / ".heat_downloader.json"

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


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def get_releases(*, include_steamdb: bool = True) -> list[Release]:
    patreon = list(fetch_patreon_releases())
    groups = [patreon]

    if include_steamdb:
        try:
            steamdb = fetch_steamdb_releases()
            groups.append(steamdb)
        except RuntimeError as exc:
            LOG.warn(str(exc))

    return merge_releases(*groups)


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


def format_release_list_line(index: int, release: Release) -> str:
    date_part = f"[{format_date(release)}]" if release.date else "[unknown date]"
    source_part = format_source_tag(release.source)
    kind_part = release.kind if release.kind != "Unknown" else ""
    meta = " ".join(part for part in (date_part, source_part, kind_part) if part)
    return (
        f"  {DIM}{index:>3}.{RESET} {BOLD}{release.version}{RESET}\n"
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
    for release in get_releases(include_steamdb=include_steamdb):
        if release.version == version:
            return release
    return None


def interactive() -> None:
    print()
    print(f"{BOLD}{CYAN}  ╔═══════════════════════════════════╗{RESET}")
    print(f"{BOLD}{CYAN}  ║      🔥 Heat Downloader v3.0      ║{RESET}")
    print(f"{BOLD}{CYAN}  ╚═══════════════════════════════════╝{RESET}")
    print()

    cfg = load_config()
    default_dir = cfg.get("output_dir", os.getcwd())

    print(f"  {BOLD}1{RESET}) Download latest release")
    print(f"  {BOLD}2{RESET}) Download specific version")
    print(f"  {BOLD}3{RESET}) List recent releases")
    print(f"  {BOLD}4{RESET}) Probe latest (no download)")
    print(f"  {BOLD}5{RESET}) Set default output directory")
    print(f"  {BOLD}q{RESET}) Quit")
    print()

    choice = input(f"  {CYAN}>{RESET} ").strip().lower()

    if choice == "1":
        dest = input(f"  output dir [{default_dir}]: ").strip() or default_dir
        if not Path(dest).is_dir():
            LOG.err(f"directory not found: {dest}")
            return
        cfg["output_dir"] = dest
        save_config(cfg)
        release, url = find_latest()
        if not url:
            return
        path = download(url, dest, log=LOG)
        if path and input("\n  extract-> [Y/n]: ").strip().lower() != "n":
            extract(path, log=LOG)

    elif choice == "2":
        version = input("  version (e.g. 1.4.0): ").strip()
        if not version:
            return
        dest = input(f"  output dir [{default_dir}]: ").strip() or default_dir
        if not Path(dest).is_dir():
            LOG.err(f"directory not found: {dest}")
            return
        cfg["output_dir"] = dest
        save_config(cfg)
        print()
        release = find_release(version)
        if release:
            print_checking(release)
        url = probe_artifact(version, release=release, log=LOG)
        if not url:
            LOG.err(f"no artifact found for {version}")
            return
        path = download(url, dest, log=LOG)
        if path and input("\n  extract-> [Y/n]: ").strip().lower() != "n":
            extract(path, log=LOG)

    elif choice == "3":
        print()
        for index, release in enumerate(islice(get_releases(), 15), 1):
            print(format_release_list_line(index, release))
            print()

    elif choice == "4":
        release, url = find_latest()
        if url and release:
            LOG.ok(f"latest: {release.version} {format_source_tag(release.source)}  ->  {url}")

    elif choice == "5":
        new_dir = input(f"  new default dir [{default_dir}]: ").strip()
        if new_dir and Path(new_dir).is_dir():
            cfg["output_dir"] = new_dir
            save_config(cfg)
            LOG.ok(f"saved: {new_dir}")
        elif new_dir:
            LOG.err(f"directory not found: {new_dir}")

    elif choice == "q":
        return
    else:
        LOG.warn("invalid choice")


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

    args = parser.parse_args()
    cfg = load_config()
    include_steamdb = not getattr(args, "patreon_only", False)

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
        if path and args.extract:
            extract(path, log=LOG)

    elif args.cmd in ("list", "l"):
        for index, release in enumerate(islice(get_releases(include_steamdb=include_steamdb), args.n), 1):
            print(format_release_list_line(index, release))
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

    else:
        interactive()
