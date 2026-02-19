#!/usr/bin/env python3
"""Gale: HRRR 2m Temperature → Color-ramped Slippy Map tiles (f00-f12).

Downloads the latest HRRR analysis (f00) through 12-hour forecast (f12) for
2m temperature from NOMADS, processes through GDAL to produce RGBA PNG tiles
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
MAX_FORECAST_HOUR = 12


def find_latest_hrrr_run():
    """Find latest HRRR run where f12 is available (guarantees f00-f12 exist)."""
    now = datetime.now(timezone.utc)
    for hours_ago in range(6):
        run_time = now - timedelta(hours=hours_ago)
        date_str = run_time.strftime("%Y%m%d")
        hour_str = run_time.strftime("%H")
        url = (
            f"{NOMADS_BASE}?dir=%2Fhrrr.{date_str}%2Fconus"
            f"&file=hrrr.t{hour_str}z.wrfsfcf{MAX_FORECAST_HOUR:02d}.grib2"
            f"&var_TMP=on&lev_2_m_above_ground=on"
        )
        try:
            req = Request(url, method="HEAD")
            req.add_header("User-Agent", "Gale Weather (galeweather.com)")
            resp = urlopen(req, timeout=15)
            if resp.status == 200:
                print(f"Found HRRR run: {date_str} {hour_str}Z (f00-f{MAX_FORECAST_HOUR:02d} available)")
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
    # Find latest available HRRR run (requires f12 to be available)
    date_str, hour_str = find_latest_hrrr_run()
    if date_str is None:
        print("NOMADS unavailable — no HRRR runs found in last 6 hours. Exiting gracefully.")
        sys.exit(0)

    run_hour = int(hour_str)
    run_dt = datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=timezone.utc)
    all_tiles = {}
    valid_times = {}

    # Process f00 through f12
    for fhour in range(MAX_FORECAST_HOUR + 1):
        tag = f"f{fhour:02d}"
        if fhour == 0:
            out_dir = os.path.join(SCRIPT_DIR, "output", "temperature")
        else:
            out_dir = os.path.join(SCRIPT_DIR, "output", f"temperature-f{fhour:02d}")

        try:
            tiles = process_forecast_hour(date_str, hour_str, fhour, out_dir)
            all_tiles[tag] = tiles
            valid_times[tag] = (run_dt + timedelta(hours=run_hour + fhour)).isoformat()
            print(f"{tag}: {tiles} tiles")
        except Exception as e:
            print(f"WARNING: {tag} processing failed, skipping: {e}")

    if "f00" not in all_tiles:
        print("ERROR: f00 (analysis) failed. Cannot continue.")
        sys.exit(1)

    # Write metadata (into f00 dir — the primary metadata location)
    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "hrrr_run": f"{date_str} {hour_str}Z",
        "hrrr_date": date_str,
        "hrrr_hour": hour_str,
        "zoom_range": "2-6",
        "variable": "TMP:2m above ground",
        "forecast_hours": sorted(all_tiles.keys()),
        "valid_times": valid_times
    }
    meta_path = os.path.join(SCRIPT_DIR, "output", "temperature", "metadata.json")
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)
    print(f"Wrote {meta_path}")

    print(f"Done! Processed {len(all_tiles)} forecast hours.")


if __name__ == "__main__":
    main()
