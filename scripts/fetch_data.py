"""Download the UrbanEV / ST-EVCDP open datasets.

Why this exists
---------------
The charging behaviour in this project was originally simulated. These are the
real, openly-licensed datasets that replace it: Shenzhen public charging pile
telemetry published by the Intelligent Systems Lab.

    ST-EVCDP   18,061 piles, 30 days,  5-minute resolution
    UrbanEV    24,798 piles, 6 months, hourly (+ 5-minute subsets)

Both are licensed for free academic use; see the upstream repositories for the
exact terms.

Why a custom downloader instead of curl/wget
--------------------------------------------
* ``curl.exe`` is blocked in this environment, while Python's own HTTP stack
  works -- so the fetch has to happen in Python.
* raw.githubusercontent.com drops connections intermittently here, so every
  request is retried with exponential backoff. A single transient reset should
  not cost a 16 MB download.
* Files are large and the run should be resumable/idempotent: a file whose size
  already matches Content-Length is skipped.

Usage
-----
    python scripts/fetch_data.py                 # UrbanEV files needed for analysis
    python scripts/fetch_data.py --dataset stevcdp
    python scripts/fetch_data.py --list          # show what would be fetched
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO_RAW = "https://raw.githubusercontent.com/IntelligentSystemsLab/{repo}/main/"
REPO_API = "https://api.github.com/repos/IntelligentSystemsLab/{repo}/contents/"

UA = "Mozilla/5.0 (compatible; ev-charging-insights/0.2)"


@dataclass(frozen=True)
class RemoteFile:
    dataset: str
    path: str            # path inside the repo
    local_name: str      # filename under data/real/<dataset>/
    note: str = ""


# Only what the analyses actually consume. poi.csv (31 MB) and the spatial
# graph files are not needed: this project analyses time series and occupancy,
# not trip generation or zone interaction.
URBANEV_FILES: tuple[RemoteFile, ...] = (
    RemoteFile("urbanev", "data/inf.csv", "inf.csv",
               "station metadata: coordinates, TAZ zone, charger count"),
    RemoteFile("urbanev", "data/volume.csv", "volume.csv",
               "hourly charging volume per station"),
    RemoteFile("urbanev", "data/occupancy.csv", "occupancy.csv",
               "hourly occupied-pile count per station"),
    RemoteFile("urbanev", "data/duration.csv", "duration.csv",
               "hourly mean charging duration per station"),
    RemoteFile("urbanev", "data/e_price.csv", "e_price.csv",
               "electricity price"),
    RemoteFile("urbanev", "data/s_price.csv", "s_price.csv",
               "service price"),
    RemoteFile("urbanev", "data/weather_central.csv", "weather_central.csv",
               "hourly weather at the central station"),
    RemoteFile("urbanev", "data/weather_header.txt", "weather_header.txt",
               "column meanings for the weather files"),
    RemoteFile("urbanev", "readme.md", "readme.md",
               "upstream documentation, kept for provenance"),
)

STEVCDP_FILES: tuple[RemoteFile, ...] = (
    RemoteFile("stevcdp", "datasets/stations.csv", "stations.csv",
               "station metadata"),
    RemoteFile("stevcdp", "datasets/volume.csv", "volume.csv",
               "charging volume, 8640 five-minute timestamps"),
    RemoteFile("stevcdp", "datasets/occupancy.csv", "occupancy.csv",
               "pile occupancy"),
    RemoteFile("stevcdp", "datasets/information.csv", "information.csv",
               "dataset documentation"),
    RemoteFile("stevcdp", "README.md", "README.md", "upstream documentation"),
)

DATASETS = {
    "urbanev": (URBANEV_FILES, "UrbanEV"),
    "stevcdp": (STEVCDP_FILES, "ST-EVCDP"),
}


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": UA})


def fetch_bytes(url: str, attempts: int = 6, timeout: int = 120) -> bytes:
    """GET ``url`` with exponential backoff.

    raw.githubusercontent.com resets connections intermittently from some
    networks. Retrying is not defensive padding here -- without it roughly one
    in three requests failed during development.
    """
    last: Exception | None = None
    for i in range(attempts):
        try:
            with urllib.request.urlopen(_request(url), timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, ConnectionError, TimeoutError,
                OSError) as exc:  # includes RemoteDisconnected
            last = exc
            delay = min(2.0 ** i, 20.0)
            print(f"      retry {i + 1}/{attempts} in {delay:.0f}s "
                  f"({type(exc).__name__})", flush=True)
            time.sleep(delay)
    raise RuntimeError(f"failed after {attempts} attempts: {last}")


def head_size(url: str, attempts: int = 4) -> int | None:
    """Content-Length of a remote file, or None if it cannot be determined."""
    for _ in range(attempts):
        try:
            req = _request(url)
            req.get_method = lambda: "HEAD"  # type: ignore[method-assign]
            with urllib.request.urlopen(req, timeout=60) as resp:
                length = resp.headers.get("Content-Length")
                return int(length) if length else None
        except Exception:
            time.sleep(1.5)
    return None


def download(remote: RemoteFile, root: Path) -> tuple[bool, str]:
    """Fetch one file. Returns ``(changed, message)``."""
    out_dir = root / remote.dataset
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / remote.local_name
    url = REPO_RAW.format(repo=DATASETS[remote.dataset][1]) + remote.path

    expected = head_size(url)
    if out_path.exists():
        have = out_path.stat().st_size
        if expected is not None and have == expected:
            return False, f"skip (already {have:,} bytes)"
        if expected is None and have > 0:
            return False, f"skip (exists, {have:,} bytes; remote size unknown)"

    data = fetch_bytes(url)
    if expected is not None and len(data) != expected:
        return False, f"WARN size mismatch: got {len(data):,} expected {expected:,}"
    out_path.write_bytes(data)
    return True, f"downloaded {len(data):,} bytes"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download open EV charging datasets")
    parser.add_argument("--dataset", choices=sorted(DATASETS) + ["all"], default="urbanev")
    parser.add_argument("--out", type=Path, default=Path("data/real"))
    parser.add_argument("--list", action="store_true", help="only list what would be fetched")
    args = parser.parse_args(argv)

    names = sorted(DATASETS) if args.dataset == "all" else [args.dataset]

    if args.list:
        for name in names:
            files, label = DATASETS[name]
            print(f"{label}  ({len(files)} files)")
            for f in files:
                print(f"  {f.path:<32} -> {f.local_name:<22} {f.note}")
        return 0

    total_changed = 0
    for name in names:
        files, label = DATASETS[name]
        print(f"\n{label}: {len(files)} files -> {args.out / name}")
        for f in files:
            print(f"  {f.local_name:<24}", end=" ", flush=True)
            try:
                changed, msg = download(f, args.out)
            except Exception as exc:  # noqa: BLE001 - report and continue
                print(f"FAILED: {type(exc).__name__}: {str(exc)[:120]}")
                continue
            total_changed += int(changed)
            print(msg)

    # A manifest makes the provenance auditable without re-downloading.
    manifest = args.out / "manifest.json"
    payload = {
        "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "files": [
            {
                "dataset": f.dataset,
                "local": f"{f.dataset}/{f.local_name}",
                "repo_path": f.path,
                "note": f.note,
                "bytes": (args.out / f.dataset / f.local_name).stat().st_size
                if (args.out / f.dataset / f.local_name).exists() else None,
            }
            for name in names for f in DATASETS[name][0]
        ],
    }
    manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"\n{total_changed} file(s) written; manifest -> {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
