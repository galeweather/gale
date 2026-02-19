#!/usr/bin/env python3
"""Gale: HRRR 10m Wind → Color-ramped Slippy Map tiles (f00-f18).

Downloads the latest HRRR for 10m UGRD and VGRD from NOMADS.
- f00: Full product suite (speed, direction, U, V tiles)
- f01-f18: Wind speed tiles only (forecast)

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
RAMP_FILE = os.path.join(SCRIPT_DIR, "wind_ramp.txt")
WDIR_RAMP_FILE = os.path.join(SCRIPT_DIR, "wind_direction_ramp.txt")
U_RAMP_FILE = os.path.join(SCRIPT_DIR, "wind_u_ramp.txt")
V_RAMP_FILE = os.path.join(SCRIPT_DIR, "wind_v_ramp.txt")
NOMADS_BASE = "https://nomads.ncep.noaa.gov/cgi-bin/filter_hrrr_2d.pl"
MAX_FORECAST_HOUR = 18


def find_latest_hrrr_run():
    """Find latest HRRR run where f12 is available for wind."""
    now = datetime.now(timezone.utc)
    for hours_ago in range(6):
        run_time = now - timedelta(hours=hours_ago)
        date_str = run_time.strftime("%Y%m%d")
        hour_str = run_time.strftime("%H")
        url = (
            f"{NOMADS_BASE}?dir=%2Fhrrr.{date_str}%2Fconus"
            f"&file=hrrr.t{hour_str}z.wrfsfcf{MAX_FORECAST_HOUR:02d}.grib2"
            f"&var_UGRD=on&lev_10_m_above_ground=on"
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


def warp_grib(grib_file, tif_file, tag):
    """Reproject GRIB2 → EPSG:4326 GeoTIFF, clipped to CONUS."""
    run_cmd([
        "gdalwarp",
        "-t_srs", "EPSG:4326",
        "-te", "-130", "20", "-60", "55",
        "-ts", "3600", "1400",
        "-r", "bilinear",
        "-dstnodata", "-9999",
        "-of", "GTiff",
        grib_file, tif_file
    ], f"{tag}: GRIB2 → GeoTIFF (EPSG:4326)")


def make_speed_tiles(ugrd_tif, vgrd_tif, output_dir, tag):
    """Compute wind speed from UGRD+VGRD and generate tiles."""
    wspd_tif = os.path.join(SCRIPT_DIR, f"hrrr_wspd_{tag}.tif")
    rgba_file = os.path.join(SCRIPT_DIR, f"hrrr_wspd_{tag}_rgba.tif")

    run_cmd([
        "gdal_calc.py",
        "-A", ugrd_tif, "-B", vgrd_tif,
        "--calc=sqrt(A**2+B**2)",
        "--outfile", wspd_tif,
        "--type=Float32", "--overwrite"
    ], f"{tag}: Computing wind speed")

    run_cmd([
        "gdaldem", "color-relief",
        wspd_tif, RAMP_FILE, rgba_file,
        "-alpha", "-of", "GTiff"
    ], f"{tag}: Color relief → RGBA")

    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    run_cmd([
        "gdal2tiles.py",
        "--zoom=2-6", "--processes=4",
        "--resampling=bilinear", "--xyz", "--exclude",
        rgba_file, output_dir
    ], f"{tag}: Rendering wind speed tiles")

    tile_count = count_tiles(output_dir)

    for f in [wspd_tif, rgba_file]:
        if os.path.exists(f):
            os.remove(f)

    return tile_count


def process_wind_speed_hour(date_str, hour_str, fhour, output_dir):
    """Download UGRD+VGRD for a forecast hour, compute speed, generate tiles."""
    tag = f"f{fhour:02d}"
    ugrd_grib = os.path.join(SCRIPT_DIR, f"hrrr_ugrd_{tag}.grib2")
    vgrd_grib = os.path.join(SCRIPT_DIR, f"hrrr_vgrd_{tag}.grib2")
    ugrd_tif = os.path.join(SCRIPT_DIR, f"hrrr_ugrd_{tag}.tif")
    vgrd_tif = os.path.join(SCRIPT_DIR, f"hrrr_vgrd_{tag}.tif")

    base = (
        f"{NOMADS_BASE}?dir=%2Fhrrr.{date_str}%2Fconus"
        f"&file=hrrr.t{hour_str}z.wrfsfcf{fhour:02d}.grib2"
    )

    download(f"{base}&var_UGRD=on&lev_10_m_above_ground=on", ugrd_grib, f"{tag} UGRD")
    download(f"{base}&var_VGRD=on&lev_10_m_above_ground=on", vgrd_grib, f"{tag} VGRD")

    warp_grib(ugrd_grib, ugrd_tif, f"{tag} UGRD")
    warp_grib(vgrd_grib, vgrd_tif, f"{tag} VGRD")

    tile_count = make_speed_tiles(ugrd_tif, vgrd_tif, output_dir, tag)

    for f in [ugrd_grib, vgrd_grib, ugrd_tif, vgrd_tif]:
        if os.path.exists(f):
            os.remove(f)

    return tile_count


def main():
    date_str, hour_str = find_latest_hrrr_run()
    if date_str is None:
        print("NOMADS unavailable — no HRRR runs found in last 6 hours. Exiting gracefully.")
        sys.exit(0)

    OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output", "wind")
    WDIR_OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output", "wind-direction")
    U_OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output", "wind-u")
    V_OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output", "wind-v")

    # ══════════════════════════════════════════════
    # f00: Full product suite (speed + direction + U + V)
    # ══════════════════════════════════════════════
    print("=== f00: Full wind processing (speed + direction + U + V) ===")

    UGRD_GRIB = os.path.join(SCRIPT_DIR, "hrrr_ugrd.grib2")
    VGRD_GRIB = os.path.join(SCRIPT_DIR, "hrrr_vgrd.grib2")
    UGRD_TIF = os.path.join(SCRIPT_DIR, "hrrr_ugrd.tif")
    VGRD_TIF = os.path.join(SCRIPT_DIR, "hrrr_vgrd.tif")

    base_f00 = (
        f"{NOMADS_BASE}?dir=%2Fhrrr.{date_str}%2Fconus"
        f"&file=hrrr.t{hour_str}z.wrfsfcf00.grib2"
    )
    download(f"{base_f00}&var_UGRD=on&lev_10_m_above_ground=on", UGRD_GRIB, "f00 UGRD")
    download(f"{base_f00}&var_VGRD=on&lev_10_m_above_ground=on", VGRD_GRIB, "f00 VGRD")

    warp_grib(UGRD_GRIB, UGRD_TIF, "f00 UGRD")
    warp_grib(VGRD_GRIB, VGRD_TIF, "f00 VGRD")

    # Wind speed tiles
    tile_count = make_speed_tiles(UGRD_TIF, VGRD_TIF, OUTPUT_DIR, "f00")
    print(f"f00: {tile_count} wind speed tiles")

    # Write speed metadata
    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "hrrr_run": f"{date_str} {hour_str}Z",
        "hrrr_date": date_str,
        "hrrr_hour": hour_str,
        "zoom_range": "2-6",
        "tile_count": tile_count,
        "variable": "WIND:10m above ground (speed from UGRD+VGRD)",
        "forecast_hours": ["f00"]  # updated at end
    }
    meta_path = os.path.join(OUTPUT_DIR, "metadata.json")
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)

    # ── Wind Direction Tiles ──
    WDIR_TIF = os.path.join(SCRIPT_DIR, "hrrr_wdir.tif")
    WDIR_RGBA = os.path.join(SCRIPT_DIR, "hrrr_wdir_rgba.tif")

    run_cmd([
        "gdal_calc.py",
        "-A", UGRD_TIF, "-B", VGRD_TIF,
        "--calc=(arctan2(-A, -B) * 57.29577951 + 360) % 360",
        "--outfile", WDIR_TIF,
        "--type=Float32", "--overwrite"
    ], "f00: Computing wind direction")

    run_cmd([
        "gdaldem", "color-relief",
        WDIR_TIF, WDIR_RAMP_FILE, WDIR_RGBA,
        "-alpha", "-of", "GTiff"
    ], "f00: Wind direction color relief")

    if os.path.exists(WDIR_OUTPUT_DIR):
        shutil.rmtree(WDIR_OUTPUT_DIR)
    os.makedirs(WDIR_OUTPUT_DIR, exist_ok=True)
    run_cmd([
        "gdal2tiles.py", "--zoom=2-6", "--processes=4",
        "--resampling=near", "--xyz", "--exclude",
        WDIR_RGBA, WDIR_OUTPUT_DIR
    ], "f00: Rendering wind direction tiles")
    print(f"f00: {count_tiles(WDIR_OUTPUT_DIR)} wind direction tiles")

    wdir_metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "hrrr_run": f"{date_str} {hour_str}Z",
        "hrrr_date": date_str,
        "hrrr_hour": hour_str,
        "zoom_range": "2-6",
        "tile_count": count_tiles(WDIR_OUTPUT_DIR),
        "variable": "WDIR:10m above ground (direction from UGRD+VGRD)"
    }
    with open(os.path.join(WDIR_OUTPUT_DIR, "metadata.json"), "w") as f:
        json.dump(wdir_metadata, f, indent=2)

    # ── U/V Component Tiles (for wind particles) ──
    UGRD_RGBA = os.path.join(SCRIPT_DIR, "hrrr_ugrd_rgba.tif")
    VGRD_RGBA = os.path.join(SCRIPT_DIR, "hrrr_vgrd_rgba.tif")

    run_cmd([
        "gdaldem", "color-relief",
        UGRD_TIF, U_RAMP_FILE, UGRD_RGBA,
        "-alpha", "-of", "GTiff"
    ], "f00: U-component color relief")
    if os.path.exists(U_OUTPUT_DIR):
        shutil.rmtree(U_OUTPUT_DIR)
    os.makedirs(U_OUTPUT_DIR, exist_ok=True)
    run_cmd([
        "gdal2tiles.py", "--zoom=2-6", "--processes=4",
        "--resampling=near", "--xyz", "--exclude",
        UGRD_RGBA, U_OUTPUT_DIR
    ], "f00: Rendering U-component tiles")
    print(f"f00: {count_tiles(U_OUTPUT_DIR)} U-component tiles")

    u_metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "hrrr_run": f"{date_str} {hour_str}Z",
        "hrrr_date": date_str,
        "hrrr_hour": hour_str,
        "zoom_range": "2-6",
        "tile_count": count_tiles(U_OUTPUT_DIR),
        "variable": "UGRD:10m above ground (u-component)",
        "encoding": "grayscale: pixel / 255 * 80 - 40 = m/s"
    }
    with open(os.path.join(U_OUTPUT_DIR, "metadata.json"), "w") as f:
        json.dump(u_metadata, f, indent=2)

    run_cmd([
        "gdaldem", "color-relief",
        VGRD_TIF, V_RAMP_FILE, VGRD_RGBA,
        "-alpha", "-of", "GTiff"
    ], "f00: V-component color relief")
    if os.path.exists(V_OUTPUT_DIR):
        shutil.rmtree(V_OUTPUT_DIR)
    os.makedirs(V_OUTPUT_DIR, exist_ok=True)
    run_cmd([
        "gdal2tiles.py", "--zoom=2-6", "--processes=4",
        "--resampling=near", "--xyz", "--exclude",
        VGRD_RGBA, V_OUTPUT_DIR
    ], "f00: Rendering V-component tiles")
    print(f"f00: {count_tiles(V_OUTPUT_DIR)} V-component tiles")

    v_metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "hrrr_run": f"{date_str} {hour_str}Z",
        "hrrr_date": date_str,
        "hrrr_hour": hour_str,
        "zoom_range": "2-6",
        "tile_count": count_tiles(V_OUTPUT_DIR),
        "variable": "VGRD:10m above ground (v-component)",
        "encoding": "grayscale: pixel / 255 * 80 - 40 = m/s"
    }
    with open(os.path.join(V_OUTPUT_DIR, "metadata.json"), "w") as f:
        json.dump(v_metadata, f, indent=2)

    # Cleanup f00 intermediates
    for f in [UGRD_GRIB, VGRD_GRIB, UGRD_TIF, VGRD_TIF,
              WDIR_TIF, WDIR_RGBA, UGRD_RGBA, VGRD_RGBA]:
        if os.path.exists(f):
            os.remove(f)

    # ══════════════════════════════════════════════
    # f01-f12: Wind speed tiles only (forecast)
    # ══════════════════════════════════════════════
    all_hours = ["f00"]
    for fhour in range(1, MAX_FORECAST_HOUR + 1):
        tag = f"f{fhour:02d}"
        out_dir = os.path.join(SCRIPT_DIR, "output", f"wind-f{fhour:02d}")
        print(f"=== {tag}: Wind speed only ===")
        try:
            tc = process_wind_speed_hour(date_str, hour_str, fhour, out_dir)
            all_hours.append(tag)
            print(f"{tag}: {tc} tiles")
        except Exception as e:
            print(f"WARNING: {tag} wind processing failed: {e}")

    # Update metadata with all forecast hours
    metadata["forecast_hours"] = all_hours
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)
    print(f"Wrote {meta_path}")

    print(f"Done! Processed {len(all_hours)} wind forecast hours.")


if __name__ == "__main__":
    main()
