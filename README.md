# Heat Downloader

A Windows-friendly command-line downloader for Anthro Heat VR game release archives. It reads public Patreon release posts and SteamDB patch notes, finds a matching archive on `anthroheat.net`, and can download and extract it.

## Features

- Finds the newest downloadable release without downloading it first.
- Lists recent Patreon and SteamDB releases.
- Prints the release source, type, post date, archive URL, archive size, content type, and archive server modification time.
- Handles inconsistent version names between release posts and archive filenames.
- Resumes interrupted downloads when the server supports byte ranges.
- Optionally extracts archives with 7-Zip.

## Requirements

- Windows
- Python 3.10 or newer
- Internet access
- Optional: [7-Zip](https://www.7-zip.org/) for automatic extraction

Install Python dependencies from this folder:

```powershell
py -m pip install -r requirements.txt
```

## Quick start

Start the interactive menu:

```powershell
py .\heat_downloader.py
```

The interactive menu stays open until you quit (`q`).

- `1` List recent releases (optionally probe one by number, then download).
- `2` Probe latest archive, then ask whether to download/extract.
- `3` Probe a specific version, then ask whether to download/extract.
- `4` Advanced: output dir, catalog probe → JSON, saved probes, force re-download, paths.

## Command-line usage

List the newest releases:

```powershell
py .\heat_downloader.py list 15
```

Check the newest archive without downloading:

```powershell
py .\heat_downloader.py probe
```

Check only Patreon releases:

```powershell
py .\heat_downloader.py probe --patreon-only
```

Probe a specific version:

```powershell
py .\heat_downloader.py probe --version 1.6.2
```

Download the newest build to a folder:

```powershell
py .\heat_downloader.py download --output "D:\Games\HeatGame"
```

Download and extract a specific build:

```powershell
py .\heat_downloader.py download --version 1.7.0 --output "D:\Games\HeatGame" --extract
```

Use `--force` to download an archive again even when the final archive file is already present.

## Version-name handling

Release posts and archive filenames sometimes use different version spellings. For example, the July 18, 2026 Test post says `1.1.7.0`, while the real archive is named `Anthro Heat 1.7.0 Test.7z`.

The downloader now does the following:

1. Tries the exact source spelling first.
2. For a four-part version such as `a.b.c.d`, also tries `b.c.d`.
3. Looks at nearby release posts and adds fallback forms using their leading version component. For example, nearby `0.1.6.2` and `1.1.6.1` cause `1.6.2` to also be checked as `0.1.6.2` and `1.1.6.2`.
4. Prefers an exact match before any fallback match.

This keeps a genuine hotfix such as `1.6.2.1` intact while still allowing a badly prefixed release-post version to find its archive.

## Understanding dates

The probe prints two separate dates:

- **Release post date**: the date reported by Patreon or SteamDB.
- **Archive last modified**: the `Last-Modified` time reported by the archive server, shown in UTC.

The archive date can be checked before downloading.

For the current `1.7.0 Test` example, the archive server reports `2026-07-18 16:48:13 UTC`, matching the July 18 release post and the files in the extracted build.

## Probe cache

Successful probes are saved to `heat_probes.json` (next to the script/exe) and reused on later runs. Misses go into `not_found`. The file is kept compact (`version`/`url`/`kind`/`last_modified`/`size_bytes`) and is included in release zips.

```powershell
py .\heat_downloader.py probe-all --only-new
py .\heat_downloader.py probe-all --verify-old
py .\heat_downloader.py probe-all --verify-old --all
py .\heat_downloader.py probe-status
py .\heat_downloader.py probe-status --not-found
py .\heat_downloader.py probe-status --not-probed
```

`probe-all` shows a progress bar with ETA.


The [Release workflow](.github/workflows/release.yml) builds the Windows zip, uploads to VirusTotal when the secret is set, and attaches the files to the Release.


## Notes

- **Test** builds may be newer than a Milestone build, but may be less stable.
- `probe` does not download anything.
- Downloads are large; check the printed archive size before starting one.
- If extraction is skipped, install 7-Zip or extract the downloaded archive manually.
- You may need VPN to get access to patreon/anthroheat.net


## Please support Anthro Heat

Heat Downloader is an archive checker and loader for files you are authorized to access. It is not designed as a tool for piracy and should not replace buying the game.

If you enjoy Anthro Heat, please [buy it on Steam](https://store.steampowered.com/app/2236060/Anthro_Heat/) and, if you have a good time with it, consider leaving the developers a positive review. It is a small gesture that helps the team continue making and improving the game.