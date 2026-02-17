#!/usr/bin/env python3
"""Gale Phase 2: HRRR 2m Temperature → Color-ramped Slippy Map tiles.

Downloads the latest HRRR analysis (f00) for 2m temperature from NOMADS,
processes through GDAL to produce RGBA PNG tiles at zoom 2-8, and writes
a metadata.json with generation info.

No pip dependencies — uses only stdlib + GDAL CLI tools.
"""

import subprocess
import os
import sys
import json
import shutil
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
from urllib.error import URLError

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output", "temperature")
RAMP_FILE = os.path.join(SCRIPT_DIR, "temperature_ramp.txt")
GRIB_FILE = os.path.join(SCRIPT_DIR, "hrrr_tmp2m.grib2")
TIFF_FILE = os.path.join(SCRIPT_DIR, "hrrr_tmp2m.tif")
RGBA_FILE = os.path.join(SCRIPT_DIR, "hrrr_tmp2m_rgba.tif")

NOMADS_BASE = "https://nomads.ncep.noaa.gov/cgi-bin/filter_hrrr_2d.pl"


def latest_hrrr_url():
    """Try current hour, then fall back up to 3 hours to find available HRRR run."""
    now = datetime.now(timezone.utc)
    for hours_ago in range(4):
        run_time = now - timedelta(hours=hours_ago)
        date_str = run_time.strftime("%Y%m%d")
        hour_str = run_time.strftime("%H")
        url = (
            f"{NOMADS_BASE}?dir=%2Fhrrr.{date_str}%2Fconus"
            f"&file=hrrr.t{hour_str}z.wrfsfcf00.grib2"
            f"&var_TMP=on&lev_2_m_above_ground=on"
        )
        try:
            req = Request(url, method="HEAD")
            req.add_header("User-Agent", "Gale Weather (galeweather.com)")
            resp = urlopen(req, timeout=15)
            if resp.status == 200:
                print(f"Found HRRR run: {date_str} {hour_str}Z")
                return url, date_str, hour_str
        except (URLError, OSError):
            continue
    return None, None, None


def download(url, dest):
    """Download a file from URL to local path."""
    print(f"Downloading HRRR GRIB2...")
    req = Request(url)
    req.add_header("User-Agent", "Gale Weather (galeweather.com)")
    with urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
        total = 0
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            f.write(chunk)
            total += len(chunk)
    print(f"Downloaded {total / 1024:.0f} KB")


def run_cmd(args, label):
    """Run a subprocess, printing the label."""
    print(f"{label}...")
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"STDERR: {result.stderr}")
        raise RuntimeError(f"{label} failed with code {result.returncode}")


def count_tiles(directory):
    """Count PNG files in tile directory."""
    count = 0
    for _, _, files in os.walk(directory):
        count += sum(1 for f in files if f.endswith(".png"))
    return count


def main():
    # Find latest available HRRR run
    url, date_str, hour_str = latest_hrrr_url()
    if url is None:
        print("NOMADS unavailable — no HRRR runs found in last 4 hours. Exiting gracefully.")
        sys.exit(0)

    # Download
    download(url, GRIB_FILE)

    # Step 1: GRIB2 → GeoTIFF
    run_cmd([
        "gdal_translate", "-of", "GTiff", "-a_srs", "EPSG:4326",
        GRIB_FILE, TIFF_FILE
    ], "GRIB2 → GeoTIFF")

    # Step 2: Color relief → RGBA GeoTIFF
    run_cmd([
        "gdaldem", "color-relief",
        TIFF_FILE, RAMP_FILE, RGBA_FILE,
        "-alpha", "-of", "GTiff"
    ], "Color relief → RGBA")

    # Step 3: RGBA GeoTIFF → Slippy Map tiles (zoom 2-8)
    if os.path.exists(OUTPUT_DIR):
        shutil.rmtree(OUTPUT_DIR)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    run_cmd([
        "gdal2tiles.py",
        "--zoom=2-8",
        "--processes=4",
        "--resampling=bilinear",
        "--xyz",
        "--exclude",
        RGBA_FILE, OUTPUT_DIR
    ], "Rendering tiles (zoom 2-8)")

    tile_count = count_tiles(OUTPUT_DIR)
    print(f"Generated {tile_count} tiles in {OUTPUT_DIR}")

    # Write metadata
    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "hrrr_run": f"{date_str} {hour_str}Z",
        "hrrr_date": date_str,
        "hrrr_hour": hour_str,
        "zoom_range": "2-8",
        "tile_count": tile_count,
        "variable": "TMP:2m above ground"
    }
    meta_path = os.path.join(OUTPUT_DIR, "metadata.json")
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)
    print(f"Wrote {meta_path}")

    # Cleanup intermediate files
    for f in [GRIB_FILE, TIFF_FILE, RGBA_FILE]:
        if os.path.exists(f):
            os.remove(f)

    print("Done!")


if __name__ == "__main__":
    main()
