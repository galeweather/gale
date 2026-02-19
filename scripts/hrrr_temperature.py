#!/usr/bin/env python3
"""Gale Phase 2/3: HRRR 2m Temperature → Color-ramped Slippy Map tiles.

Downloads the latest HRRR analysis (f00) and +1h forecast (f01) for 2m
temperature from NOMADS, processes through GDAL to produce RGBA PNG tiles
at zoom 2-6, and writes a metadata.json with generation info.

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
RAMP_FILE = os.path.join(SCRIPT_DIR, "temperature_ramp.txt")
NOMADS_BASE = "https://nomads.ncep.noaa.gov/cgi-bin/filter_hrrr_2d.pl"


def find_latest_hrrr_run():
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
                return date_str, hour_str
        except (URLError, OSError):
            continue
    return None, None


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


def process_forecast_hour(date_str, hour_str, fhour, output_dir):
    """Download and process a single HRRR forecast hour into tiles.

    Args:
        date_str: HRRR run date (e.g. "20260218")
        hour_str: HRRR run hour (e.g. "12")
        fhour: Forecast hour number (0 or 1)
        output_dir: Directory to write tiles into
    """
    fhh = f"{fhour:02d}"
    tag = f"f{fhh}"

    # Unique intermediate filenames per forecast hour
    grib_file = os.path.join(SCRIPT_DIR, f"hrrr_tmp2m_{tag}.grib2")
    tiff_file = os.path.join(SCRIPT_DIR, f"hrrr_tmp2m_{tag}.tif")
    rgba_file = os.path.join(SCRIPT_DIR, f"hrrr_tmp2m_{tag}_rgba.tif")

    url = (
        f"{NOMADS_BASE}?dir=%2Fhrrr.{date_str}%2Fconus"
        f"&file=hrrr.t{hour_str}z.wrfsfcf{fhh}.grib2"
        f"&var_TMP=on&lev_2_m_above_ground=on"
    )

    download(url, grib_file)

    # Reproject GRIB2 → EPSG:4326 GeoTIFF, clipped to CONUS
    # -dstnodata ensures pixels outside HRRR grid become NODATA → transparent
    run_cmd([
        "gdalwarp",
        "-t_srs", "EPSG:4326",
        "-te", "-130", "20", "-60", "55",
        "-ts", "3600", "1400",
        "-r", "bilinear",
        "-dstnodata", "-9999",
        "-of", "GTiff",
        grib_file, tiff_file
    ], f"{tag}: GRIB2 → GeoTIFF (EPSG:4326)")

    # Color relief → RGBA GeoTIFF
    run_cmd([
        "gdaldem", "color-relief",
        tiff_file, RAMP_FILE, rgba_file,
        "-alpha", "-of", "GTiff"
    ], f"{tag}: Color relief → RGBA")

    # RGBA GeoTIFF → Slippy Map tiles (zoom 2-6)
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    run_cmd([
        "gdal2tiles.py",
        "--zoom=2-6",
        "--processes=4",
        "--resampling=bilinear",
        "--xyz",
        "--exclude",
        rgba_file, output_dir
    ], f"{tag}: Rendering tiles (zoom 2-6)")

    tile_count = count_tiles(output_dir)
    print(f"{tag}: Generated {tile_count} tiles in {output_dir}")

    # Cleanup intermediate files
    for f in [grib_file, tiff_file, rgba_file]:
        if os.path.exists(f):
            os.remove(f)

    return tile_count


def main():
    # Find latest available HRRR run
    date_str, hour_str = find_latest_hrrr_run()
    if date_str is None:
        print("NOMADS unavailable — no HRRR runs found in last 4 hours. Exiting gracefully.")
        sys.exit(0)

    # Process f00 (analysis — current conditions)
    f00_dir = os.path.join(SCRIPT_DIR, "output", "temperature")
    f00_tiles = process_forecast_hour(date_str, hour_str, 0, f00_dir)

    # Process f01 (+1 hour forecast) — failure is non-fatal
    f01_dir = os.path.join(SCRIPT_DIR, "output", "temperature-f01")
    f01_tiles = 0
    try:
        f01_tiles = process_forecast_hour(date_str, hour_str, 1, f01_dir)
    except Exception as e:
        print(f"WARNING: f01 processing failed, skipping: {e}")

    # Compute valid times
    run_hour = int(hour_str)
    run_dt = datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=timezone.utc)
    f00_valid = (run_dt + timedelta(hours=run_hour)).isoformat()
    f01_valid = (run_dt + timedelta(hours=run_hour + 1)).isoformat()

    # Write metadata (into f00 dir — the primary metadata location)
    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "hrrr_run": f"{date_str} {hour_str}Z",
        "hrrr_date": date_str,
        "hrrr_hour": hour_str,
        "zoom_range": "2-6",
        "tile_count": f00_tiles,
        "variable": "TMP:2m above ground",
        "forecast_hours": ["f00", "f01"] if f01_tiles > 0 else ["f00"],
        "valid_times": {
            "f00": f00_valid,
            "f01": f01_valid
        }
    }
    meta_path = os.path.join(f00_dir, "metadata.json")
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)
    print(f"Wrote {meta_path}")

    print("Done!")


if __name__ == "__main__":
    main()
