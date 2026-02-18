#!/usr/bin/env python3
"""Gale Phase 2: HRRR 10m Wind → Color-ramped Slippy Map tiles.

Downloads the latest HRRR analysis (f00) for 10m UGRD and VGRD from NOMADS,
produces four tile sets:
  - wind/        Wind speed (color-ramped)
  - wind-direction/  Wind direction (grayscale-encoded 0-360°)
  - wind-u/      U component (grayscale-encoded -40 to +40 m/s)
  - wind-v/      V component (grayscale-encoded -40 to +40 m/s)

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
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output", "wind")
WDIR_OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output", "wind-direction")
U_OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output", "wind-u")
V_OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output", "wind-v")
RAMP_FILE = os.path.join(SCRIPT_DIR, "wind_ramp.txt")
WDIR_RAMP_FILE = os.path.join(SCRIPT_DIR, "wind_direction_ramp.txt")
U_RAMP_FILE = os.path.join(SCRIPT_DIR, "wind_u_ramp.txt")
V_RAMP_FILE = os.path.join(SCRIPT_DIR, "wind_v_ramp.txt")
UGRD_GRIB = os.path.join(SCRIPT_DIR, "hrrr_ugrd.grib2")
VGRD_GRIB = os.path.join(SCRIPT_DIR, "hrrr_vgrd.grib2")
UGRD_TIF = os.path.join(SCRIPT_DIR, "hrrr_ugrd.tif")
VGRD_TIF = os.path.join(SCRIPT_DIR, "hrrr_vgrd.tif")
WSPD_TIF = os.path.join(SCRIPT_DIR, "hrrr_wspd.tif")
RGBA_FILE = os.path.join(SCRIPT_DIR, "hrrr_wspd_rgba.tif")
WDIR_TIF = os.path.join(SCRIPT_DIR, "hrrr_wdir.tif")
WDIR_RGBA = os.path.join(SCRIPT_DIR, "hrrr_wdir_rgba.tif")
UGRD_RGBA = os.path.join(SCRIPT_DIR, "hrrr_ugrd_rgba.tif")
VGRD_RGBA = os.path.join(SCRIPT_DIR, "hrrr_vgrd_rgba.tif")

NOMADS_BASE = "https://nomads.ncep.noaa.gov/cgi-bin/filter_hrrr_2d.pl"


def latest_hrrr_url():
    """Try current hour, then fall back up to 3 hours to find available HRRR run."""
    now = datetime.now(timezone.utc)
    for hours_ago in range(4):
        run_time = now - timedelta(hours=hours_ago)
        date_str = run_time.strftime("%Y%m%d")
        hour_str = run_time.strftime("%H")
        base = (
            f"{NOMADS_BASE}?dir=%2Fhrrr.{date_str}%2Fconus"
            f"&file=hrrr.t{hour_str}z.wrfsfcf00.grib2"
        )
        ugrd_url = f"{base}&var_UGRD=on&lev_10_m_above_ground=on"
        vgrd_url = f"{base}&var_VGRD=on&lev_10_m_above_ground=on"
        try:
            req = Request(ugrd_url, method="HEAD")
            req.add_header("User-Agent", "Gale Weather (galeweather.com)")
            resp = urlopen(req, timeout=15)
            if resp.status == 200:
                print(f"Found HRRR run: {date_str} {hour_str}Z")
                return ugrd_url, vgrd_url, date_str, hour_str
        except (URLError, OSError):
            continue
    return None, None, None, None


def download(url, dest, label):
    """Download a file from URL to local path."""
    print(f"Downloading {label}...")
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
    ugrd_url, vgrd_url, date_str, hour_str = latest_hrrr_url()
    if ugrd_url is None:
        print("NOMADS unavailable — no HRRR runs found in last 4 hours. Exiting gracefully.")
        sys.exit(0)

    # Download both components
    download(ugrd_url, UGRD_GRIB, "UGRD (u-component)")
    download(vgrd_url, VGRD_GRIB, "VGRD (v-component)")

    # Step 1: Reproject each component to EPSG:4326, clipped to CONUS
    run_cmd([
        "gdalwarp",
        "-t_srs", "EPSG:4326",
        "-te", "-130", "20", "-60", "55",
        "-ts", "3600", "1400",
        "-r", "bilinear",
        "-of", "GTiff",
        UGRD_GRIB, UGRD_TIF
    ], "UGRD GRIB2 → GeoTIFF (EPSG:4326)")

    run_cmd([
        "gdalwarp",
        "-t_srs", "EPSG:4326",
        "-te", "-130", "20", "-60", "55",
        "-ts", "3600", "1400",
        "-r", "bilinear",
        "-of", "GTiff",
        VGRD_GRIB, VGRD_TIF
    ], "VGRD GRIB2 → GeoTIFF (EPSG:4326)")

    # Step 2: Compute wind speed = sqrt(u² + v²)
    run_cmd([
        "gdal_calc.py",
        "-A", UGRD_TIF,
        "-B", VGRD_TIF,
        "--calc=sqrt(A**2+B**2)",
        "--outfile", WSPD_TIF,
        "--type=Float32",
        "--overwrite"
    ], "Computing wind speed from UGRD + VGRD")

    # Step 3: Color relief → RGBA GeoTIFF
    run_cmd([
        "gdaldem", "color-relief",
        WSPD_TIF, RAMP_FILE, RGBA_FILE,
        "-alpha", "-of", "GTiff"
    ], "Color relief → RGBA")

    # Step 4: RGBA GeoTIFF → Slippy Map tiles (zoom 2-6)
    if os.path.exists(OUTPUT_DIR):
        shutil.rmtree(OUTPUT_DIR)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    run_cmd([
        "gdal2tiles.py",
        "--zoom=2-6",
        "--processes=4",
        "--resampling=bilinear",
        "--xyz",
        "--exclude",
        RGBA_FILE, OUTPUT_DIR
    ], "Rendering tiles (zoom 2-6)")

    tile_count = count_tiles(OUTPUT_DIR)
    print(f"Generated {tile_count} wind speed tiles in {OUTPUT_DIR}")

    # Write speed metadata
    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "hrrr_run": f"{date_str} {hour_str}Z",
        "hrrr_date": date_str,
        "hrrr_hour": hour_str,
        "zoom_range": "2-6",
        "tile_count": tile_count,
        "variable": "WIND:10m above ground (speed from UGRD+VGRD)"
    }
    meta_path = os.path.join(OUTPUT_DIR, "metadata.json")
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)
    print(f"Wrote {meta_path}")

    # ── Wind Direction Tiles ──────────────────────────
    # Compute meteorological wind direction (where wind is coming FROM):
    # atan2(-u, -v) converted to degrees, mod 360
    # Using numpy via gdal_calc: 90 - atan2(v, u) gives "math" bearing,
    # but standard met convention is atan2(-u, -v) * 180/pi % 360
    run_cmd([
        "gdal_calc.py",
        "-A", UGRD_TIF,
        "-B", VGRD_TIF,
        "--calc=(arctan2(-A, -B) * 57.29577951 + 360) % 360",
        "--outfile", WDIR_TIF,
        "--type=Float32",
        "--overwrite"
    ], "Computing wind direction from UGRD + VGRD")

    # Color relief → grayscale RGBA (direction encoded as pixel value)
    run_cmd([
        "gdaldem", "color-relief",
        WDIR_TIF, WDIR_RAMP_FILE, WDIR_RGBA,
        "-alpha", "-of", "GTiff"
    ], "Wind direction color relief → RGBA")

    # Generate direction tiles (NEAREST resampling — no interpolation across 0/360 boundary)
    if os.path.exists(WDIR_OUTPUT_DIR):
        shutil.rmtree(WDIR_OUTPUT_DIR)
    os.makedirs(WDIR_OUTPUT_DIR, exist_ok=True)

    run_cmd([
        "gdal2tiles.py",
        "--zoom=2-6",
        "--processes=4",
        "--resampling=near",
        "--xyz",
        "--exclude",
        WDIR_RGBA, WDIR_OUTPUT_DIR
    ], "Rendering wind direction tiles (zoom 2-6)")

    wdir_tile_count = count_tiles(WDIR_OUTPUT_DIR)
    print(f"Generated {wdir_tile_count} wind direction tiles in {WDIR_OUTPUT_DIR}")

    # Write direction metadata
    wdir_metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "hrrr_run": f"{date_str} {hour_str}Z",
        "hrrr_date": date_str,
        "hrrr_hour": hour_str,
        "zoom_range": "2-6",
        "tile_count": wdir_tile_count,
        "variable": "WDIR:10m above ground (direction from UGRD+VGRD)"
    }
    wdir_meta_path = os.path.join(WDIR_OUTPUT_DIR, "metadata.json")
    with open(wdir_meta_path, "w") as f:
        json.dump(wdir_metadata, f, indent=2)
    print(f"Wrote {wdir_meta_path}")

    # ── U/V Component Tiles (for wind particle animation) ──────
    # U component: grayscale-encoded -40 to +40 m/s (128 = 0 m/s)
    run_cmd([
        "gdaldem", "color-relief",
        UGRD_TIF, U_RAMP_FILE, UGRD_RGBA,
        "-alpha", "-of", "GTiff"
    ], "U-component color relief → RGBA")

    if os.path.exists(U_OUTPUT_DIR):
        shutil.rmtree(U_OUTPUT_DIR)
    os.makedirs(U_OUTPUT_DIR, exist_ok=True)

    run_cmd([
        "gdal2tiles.py",
        "--zoom=2-6",
        "--processes=4",
        "--resampling=near",
        "--xyz",
        "--exclude",
        UGRD_RGBA, U_OUTPUT_DIR
    ], "Rendering U-component tiles (zoom 2-6)")

    u_tile_count = count_tiles(U_OUTPUT_DIR)
    print(f"Generated {u_tile_count} U-component tiles in {U_OUTPUT_DIR}")

    u_metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "hrrr_run": f"{date_str} {hour_str}Z",
        "hrrr_date": date_str,
        "hrrr_hour": hour_str,
        "zoom_range": "2-6",
        "tile_count": u_tile_count,
        "variable": "UGRD:10m above ground (u-component)",
        "encoding": "grayscale: pixel / 255 * 80 - 40 = m/s"
    }
    u_meta_path = os.path.join(U_OUTPUT_DIR, "metadata.json")
    with open(u_meta_path, "w") as f:
        json.dump(u_metadata, f, indent=2)
    print(f"Wrote {u_meta_path}")

    # V component: grayscale-encoded -40 to +40 m/s (128 = 0 m/s)
    run_cmd([
        "gdaldem", "color-relief",
        VGRD_TIF, V_RAMP_FILE, VGRD_RGBA,
        "-alpha", "-of", "GTiff"
    ], "V-component color relief → RGBA")

    if os.path.exists(V_OUTPUT_DIR):
        shutil.rmtree(V_OUTPUT_DIR)
    os.makedirs(V_OUTPUT_DIR, exist_ok=True)

    run_cmd([
        "gdal2tiles.py",
        "--zoom=2-6",
        "--processes=4",
        "--resampling=near",
        "--xyz",
        "--exclude",
        VGRD_RGBA, V_OUTPUT_DIR
    ], "Rendering V-component tiles (zoom 2-6)")

    v_tile_count = count_tiles(V_OUTPUT_DIR)
    print(f"Generated {v_tile_count} V-component tiles in {V_OUTPUT_DIR}")

    v_metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "hrrr_run": f"{date_str} {hour_str}Z",
        "hrrr_date": date_str,
        "hrrr_hour": hour_str,
        "zoom_range": "2-6",
        "tile_count": v_tile_count,
        "variable": "VGRD:10m above ground (v-component)",
        "encoding": "grayscale: pixel / 255 * 80 - 40 = m/s"
    }
    v_meta_path = os.path.join(V_OUTPUT_DIR, "metadata.json")
    with open(v_meta_path, "w") as f:
        json.dump(v_metadata, f, indent=2)
    print(f"Wrote {v_meta_path}")

    # Cleanup intermediate files
    for f in [UGRD_GRIB, VGRD_GRIB, UGRD_TIF, VGRD_TIF, WSPD_TIF, RGBA_FILE,
              WDIR_TIF, WDIR_RGBA, UGRD_RGBA, VGRD_RGBA]:
        if os.path.exists(f):
            os.remove(f)

    print("Done!")


if __name__ == "__main__":
    main()
