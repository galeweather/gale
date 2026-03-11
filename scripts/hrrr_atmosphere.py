#!/usr/bin/env python3
"""Gale Phase 5: HRRR Pressure-Level Atmosphere → Binary profile grid.

Downloads HRRR pressure-level data (6 levels × 5 variables = 30 GRIB2 files)
from NOMADS, processes through GDAL to a common CONUS grid, and packs into
a single binary profile.bin file for the frontend cross-section and sounding
features.

Binary format (profile.bin):
  Header (64 bytes):
    4B  magic "GALE"
    2B  version (1)
    2B  grid width (281)
    2B  grid height (141)
    2B  num_levels (6)
    4B  lon_min × 1000 (-130000)
    4B  lon_max × 1000 (-60000)
    4B  lat_min × 1000 (20000)
    4B  lat_max × 1000 (55000)
    12B pressure values (6 × uint16): 1000, 925, 850, 700, 500, 250
    8B  HRRR timestamp (YYYYMMDD HH as uint32 date + uint32 hour)
    16B reserved (zeros)

  Data (Int16 per value, 5 vars × 281 × 141 × 6 levels = 1,189,530 values):
    For each level (1000..250mb):
      For each row (lat 20→55):
        For each col (lon -130→-60):
          temp (K × 10)
          u_wind (m/s × 100)
          v_wind (m/s × 100)
          rh (% × 100)
          geopotential_height (m, as int16)

No pip dependencies — uses only stdlib + GDAL CLI tools.
"""

import struct
import subprocess
import os
import sys
import json
import shutil
import numpy as np
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
from urllib.error import URLError

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# NOMADS migrated from filter_hrrr_prs.pl to filter_hrrr_2d.pl circa early 2026.
# The 2d filter serves both surface and pressure-level variables from wrfsfc files.
# Source: NOMADS homepage (https://nomads.ncep.noaa.gov/), verified 2026-03-11.
NOMADS_PRS = "https://nomads.ncep.noaa.gov/cgi-bin/filter_hrrr_2d.pl"

# Grid definition: 0.25° CONUS
GRID_W = 281   # -130 to -60 at 0.25° = 280 intervals + 1
GRID_H = 141   # 20 to 55 at 0.25° = 140 intervals + 1
LON_MIN, LON_MAX = -130.0, -60.0
LAT_MIN, LAT_MAX = 20.0, 55.0

PRESSURE_LEVELS = [1000, 925, 850, 700, 500, 250]
VARIABLES = ["TMP", "UGRD", "VGRD", "RH", "HGT"]

# Map NOMADS variable names to their GRIB2 level strings
LEVEL_NAMES = {
    1000: "1000_mb", 925: "925_mb", 850: "850_mb",
    700: "700_mb", 500: "500_mb", 250: "250_mb",
}

HEADER_SIZE = 64
VERSION = 1
MAGIC = b"GALE"


def find_latest_hrrr_run():
    """Try current hour, then fall back up to 3 hours to find available HRRR run."""
    now = datetime.now(timezone.utc)
    for hours_ago in range(4):
        run_time = now - timedelta(hours=hours_ago)
        date_str = run_time.strftime("%Y%m%d")
        hour_str = run_time.strftime("%H")
        url = (
            f"{NOMADS_PRS}?dir=%2Fhrrr.{date_str}%2Fconus"
            f"&file=hrrr.t{hour_str}z.wrfsfcf00.grib2"
            f"&var_TMP=on&lev_1000_mb=on"
        )
        try:
            req = Request(url, method="HEAD")
            req.add_header("User-Agent", "Gale Weather (galeweather.com)")
            resp = urlopen(req, timeout=15)
            if resp.status == 200:
                print(f"Found HRRR pressure-level run: {date_str} {hour_str}Z")
                return date_str, hour_str
        except (URLError, OSError):
            continue
    return None, None


def download(url, dest, label="GRIB2"):
    """Download a file from URL to local path."""
    print(f"  Downloading {label}...")
    req = Request(url)
    req.add_header("User-Agent", "Gale Weather (galeweather.com)")
    with urlopen(req, timeout=180) as resp, open(dest, "wb") as f:
        total = 0
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            f.write(chunk)
            total += len(chunk)
    print(f"  Downloaded {total / 1024:.0f} KB")


def run_cmd(args, label):
    """Run a subprocess, printing the label."""
    print(f"  {label}...")
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  STDERR: {result.stderr[:500]}")
        raise RuntimeError(f"{label} failed with code {result.returncode}")


def nomads_prs_url(date_str, hour_str, variable, level_mb):
    """Build NOMADS filter URL for a specific HRRR pressure-level variable."""
    level_name = LEVEL_NAMES[level_mb]
    return (
        f"{NOMADS_PRS}?dir=%2Fhrrr.{date_str}%2Fconus"
        f"&file=hrrr.t{hour_str}z.wrfsfcf00.grib2"
        f"&var_{variable}=on&lev_{level_name}=on"
    )


def grib_to_array(grib_path, work_dir, tag):
    """Convert GRIB2 → warped GeoTIFF → numpy array (GRID_H × GRID_W)."""
    tif_path = os.path.join(work_dir, f"{tag}.tif")

    run_cmd([
        "gdalwarp",
        "-t_srs", "EPSG:4326",
        "-te", str(LON_MIN), str(LAT_MIN), str(LON_MAX), str(LAT_MAX),
        "-ts", str(GRID_W), str(GRID_H),
        "-r", "bilinear",
        "-of", "GTiff",
        grib_path, tif_path,
    ], f"{tag}: GRIB2 → GeoTIFF")

    # Read raw values from GeoTIFF using gdal_translate to raw binary
    raw_path = os.path.join(work_dir, f"{tag}.raw")
    run_cmd([
        "gdal_translate",
        "-of", "ENVI",
        "-ot", "Float32",
        tif_path, raw_path,
    ], f"{tag}: GeoTIFF → raw float32")

    # ENVI format: raw float32 BSQ
    data = np.fromfile(raw_path, dtype=np.float32)
    if data.size != GRID_W * GRID_H:
        raise RuntimeError(f"{tag}: expected {GRID_W * GRID_H} values, got {data.size}")

    # GDAL outputs top-to-bottom (north first), we want south-to-north (lat 20→55)
    grid = data.reshape(GRID_H, GRID_W)
    grid = np.flipud(grid)

    # Cleanup
    for f in [grib_path, tif_path, raw_path, raw_path + ".hdr"]:
        if os.path.exists(f):
            os.remove(f)

    return grid


def pack_profile(all_data, date_str, hour_str, output_path):
    """Pack all atmospheric data into binary profile.bin format.

    all_data: dict[(level_mb, var_name)] → numpy array (GRID_H × GRID_W)
    """
    header = bytearray(HEADER_SIZE)

    # Magic + version
    header[0:4] = MAGIC
    struct.pack_into("<H", header, 4, VERSION)
    struct.pack_into("<H", header, 6, GRID_W)
    struct.pack_into("<H", header, 8, GRID_H)
    struct.pack_into("<H", header, 10, len(PRESSURE_LEVELS))

    # Bounding box (× 1000 as int32)
    struct.pack_into("<i", header, 12, int(LON_MIN * 1000))
    struct.pack_into("<i", header, 16, int(LON_MAX * 1000))
    struct.pack_into("<i", header, 20, int(LAT_MIN * 1000))
    struct.pack_into("<i", header, 24, int(LAT_MAX * 1000))

    # Pressure levels (6 × uint16)
    for i, p in enumerate(PRESSURE_LEVELS):
        struct.pack_into("<H", header, 28 + i * 2, p)

    # HRRR timestamp
    struct.pack_into("<I", header, 40, int(date_str))
    struct.pack_into("<I", header, 44, int(hour_str))

    # Reserved zeros (48..63) — already zero from bytearray

    # Pack data: for each level, for each row, for each col, 5 int16 values
    num_values = len(PRESSURE_LEVELS) * GRID_H * GRID_W * 5
    data_buf = np.zeros(num_values, dtype=np.int16)

    idx = 0
    for level in PRESSURE_LEVELS:
        tmp = all_data[(level, "TMP")]
        ugrd = all_data[(level, "UGRD")]
        vgrd = all_data[(level, "VGRD")]
        rh = all_data[(level, "RH")]
        hgt = all_data[(level, "HGT")]

        for row in range(GRID_H):
            for col in range(GRID_W):
                data_buf[idx]     = int(np.clip(tmp[row, col] * 10, -32768, 32767))      # K × 10
                data_buf[idx + 1] = int(np.clip(ugrd[row, col] * 100, -32768, 32767))    # m/s × 100
                data_buf[idx + 2] = int(np.clip(vgrd[row, col] * 100, -32768, 32767))    # m/s × 100
                data_buf[idx + 3] = int(np.clip(rh[row, col] * 100, -32768, 32767))      # % × 100
                data_buf[idx + 4] = int(np.clip(hgt[row, col], -32768, 32767))            # meters
                idx += 5

    with open(output_path, "wb") as f:
        f.write(bytes(header))
        f.write(data_buf.tobytes())

    file_size = os.path.getsize(output_path)
    print(f"  Wrote {output_path} ({file_size / 1024:.0f} KB, {num_values} int16 values)")
    return file_size


def main():
    date_str, hour_str = find_latest_hrrr_run()
    if date_str is None:
        print("NOMADS unavailable — no HRRR pressure-level runs found. Exiting gracefully.")
        sys.exit(0)

    work_dir = os.path.join(SCRIPT_DIR, "atmos_work")
    output_dir = os.path.join(SCRIPT_DIR, "output", "atmosphere")
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    all_data = {}
    downloaded = 0

    for level in PRESSURE_LEVELS:
        for var in VARIABLES:
            tag = f"{var}_{level}mb"
            grib_path = os.path.join(work_dir, f"{tag}.grib2")

            url = nomads_prs_url(date_str, hour_str, var, level)
            try:
                download(url, grib_path, tag)
                grid = grib_to_array(grib_path, work_dir, tag)
                all_data[(level, var)] = grid
                downloaded += 1
            except Exception as e:
                print(f"  WARNING: Failed to process {tag}: {e}")
                # Fill with NaN-equivalent (0 for int16)
                all_data[(level, var)] = np.zeros((GRID_H, GRID_W), dtype=np.float32)

    if downloaded < 20:
        print(f"Only got {downloaded}/30 variables — too many failures. Exiting.")
        sys.exit(1)

    print(f"Downloaded and processed {downloaded}/30 variables")

    # Pack into binary format
    profile_path = os.path.join(output_dir, "profile.bin")
    pack_profile(all_data, date_str, hour_str, profile_path)

    # Write metadata.json
    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "hrrr_run": f"{date_str} {hour_str}Z",
        "hrrr_date": date_str,
        "hrrr_hour": hour_str,
        "grid": {
            "width": GRID_W,
            "height": GRID_H,
            "lon_min": LON_MIN,
            "lon_max": LON_MAX,
            "lat_min": LAT_MIN,
            "lat_max": LAT_MAX,
            "resolution_deg": 0.25,
        },
        "levels_mb": PRESSURE_LEVELS,
        "variables": ["TMP", "UGRD", "VGRD", "RH", "HGT"],
        "encoding": {
            "TMP": "K × 10 (int16)",
            "UGRD": "m/s × 100 (int16)",
            "VGRD": "m/s × 100 (int16)",
            "RH": "% × 100 (int16)",
            "HGT": "meters (int16)",
        },
        "variables_downloaded": downloaded,
    }
    meta_path = os.path.join(output_dir, "metadata.json")
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)
    print(f"  Wrote {meta_path}")

    # Cleanup work directory
    if os.path.exists(work_dir):
        shutil.rmtree(work_dir)

    print("Atmosphere pipeline complete!")


if __name__ == "__main__":
    main()
