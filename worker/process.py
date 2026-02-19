"""Gale Worker — GRIB2 processing extracted from pipeline scripts.

Handles all layer types:
- temperature / temperature-f01: single GRIB → gdalwarp → gdaldem → gdal2tiles
- wind: download UGRD + VGRD → warp → gdal_calc (speed, dir) → color-relief × 4 → tiles × 4
- precipitation: single GRIB (f01) → gdalwarp → gdaldem → gdal2tiles
"""

import subprocess
import os
import shutil
import hashlib
from urllib.request import urlopen, Request

RAMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ramps")
WORK_DIR = "/tmp/gale-worker"
NOMADS_BASE = "https://nomads.ncep.noaa.gov/cgi-bin/filter_hrrr_2d.pl"


def download(url: str, dest: str, label: str = "GRIB2"):
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


def run_cmd(args: list[str], label: str):
    """Run a subprocess, raising on failure."""
    print(f"  {label}...")
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  STDERR: {result.stderr[:500]}")
        raise RuntimeError(f"{label} failed with code {result.returncode}")


def count_tiles(directory: str) -> int:
    """Count PNG files in tile directory."""
    count = 0
    for _, _, files in os.walk(directory):
        count += sum(1 for f in files if f.endswith(".png"))
    return count


def compute_output_hash(output_dirs: list[str]) -> str:
    """Compute deterministic SHA-256 hash of all tile outputs.

    Hash = SHA-256 of sorted "relpath:filehash" pairs.
    """
    entries = []
    for base_dir in sorted(output_dirs):
        for root, dirs, files in sorted(os.walk(base_dir)):
            dirs.sort()
            for fname in sorted(files):
                if not fname.endswith(".png"):
                    continue
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, base_dir)
                with open(fpath, "rb") as f:
                    fhash = hashlib.sha256(f.read()).hexdigest()
                # Include the dir name as prefix for multi-prefix layers
                dir_name = os.path.basename(base_dir)
                entries.append(f"{dir_name}/{rel}:{fhash}")

    entries.sort()
    combined = "\n".join(entries)
    return hashlib.sha256(combined.encode()).hexdigest()


def nomads_url(date_str: str, hour_str: str, forecast_hour: str,
               variable: str, level: str) -> str:
    """Build NOMADS filter URL for a specific HRRR variable."""
    fhh = forecast_hour  # e.g. "f00" or "f01"
    return (
        f"{NOMADS_BASE}?dir=%2Fhrrr.{date_str}%2Fconus"
        f"&file=hrrr.t{hour_str}z.wrfsfc{fhh}.grib2"
        f"&var_{variable}=on&lev_{level}=on"
    )


def _warp(grib: str, tif: str, label: str):
    """Reproject GRIB2 → EPSG:4326 GeoTIFF, clipped to CONUS."""
    run_cmd([
        "gdalwarp",
        "-t_srs", "EPSG:4326",
        "-te", "-130", "20", "-60", "55",
        "-ts", "3600", "1400",
        "-r", "bilinear",
        "-of", "GTiff",
        grib, tif,
    ], f"{label}: GRIB2 → GeoTIFF")


def _color_relief(tif: str, ramp: str, rgba: str, label: str):
    """Apply color relief to produce RGBA GeoTIFF."""
    run_cmd([
        "gdaldem", "color-relief",
        tif, ramp, rgba,
        "-alpha", "-of", "GTiff",
    ], f"{label}: color relief")


def _make_tiles(rgba: str, output_dir: str, label: str, resampling: str = "bilinear"):
    """Generate XYZ slippy map tiles from RGBA GeoTIFF."""
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    os.makedirs(output_dir, exist_ok=True)
    run_cmd([
        "gdal2tiles.py",
        "--zoom=2-6",
        "--processes=4",
        "--resampling=" + resampling,
        "--xyz",
        "--exclude",
        rgba, output_dir,
    ], f"{label}: tiles (zoom 2-6)")
    tc = count_tiles(output_dir)
    print(f"  {label}: {tc} tiles")
    return tc


def process_temperature(date_str: str, hour_str: str, forecast_hour: str = "f00") -> tuple[list[str], str]:
    """Process temperature layer. Returns (output_dirs, hash)."""
    tag = "temperature" if forecast_hour == "f00" else "temperature-f01"
    work = os.path.join(WORK_DIR, tag)
    os.makedirs(work, exist_ok=True)

    grib = os.path.join(work, "tmp.grib2")
    tif = os.path.join(work, "tmp.tif")
    rgba = os.path.join(work, "tmp_rgba.tif")
    output_dir = os.path.join(work, "tiles")

    url = nomads_url(date_str, hour_str, forecast_hour, "TMP", "2_m_above_ground")
    download(url, grib, f"TMP {forecast_hour}")
    _warp(grib, tif, tag)
    _color_relief(tif, os.path.join(RAMP_DIR, "temperature_ramp.txt"), rgba, tag)
    _make_tiles(rgba, output_dir, tag)

    result_hash = compute_output_hash([output_dir])

    # Cleanup intermediate
    for f in [grib, tif, rgba]:
        if os.path.exists(f):
            os.remove(f)

    return [output_dir], result_hash


def process_precipitation(date_str: str, hour_str: str) -> tuple[list[str], str]:
    """Process precipitation layer (always f01). Returns (output_dirs, hash)."""
    tag = "precipitation"
    work = os.path.join(WORK_DIR, tag)
    os.makedirs(work, exist_ok=True)

    grib = os.path.join(work, "apcp.grib2")
    tif = os.path.join(work, "apcp.tif")
    rgba = os.path.join(work, "apcp_rgba.tif")
    output_dir = os.path.join(work, "tiles")

    url = nomads_url(date_str, hour_str, "f01", "APCP", "surface")
    download(url, grib, "APCP f01")
    _warp(grib, tif, tag)
    _color_relief(tif, os.path.join(RAMP_DIR, "precip_ramp.txt"), rgba, tag)
    _make_tiles(rgba, output_dir, tag)

    result_hash = compute_output_hash([output_dir])

    for f in [grib, tif, rgba]:
        if os.path.exists(f):
            os.remove(f)

    return [output_dir], result_hash


def process_wind(date_str: str, hour_str: str) -> tuple[list[str], str]:
    """Process wind mega-packet: speed + direction + u + v. Returns (output_dirs, hash)."""
    work = os.path.join(WORK_DIR, "wind")
    os.makedirs(work, exist_ok=True)

    ugrd_grib = os.path.join(work, "ugrd.grib2")
    vgrd_grib = os.path.join(work, "vgrd.grib2")
    ugrd_tif = os.path.join(work, "ugrd.tif")
    vgrd_tif = os.path.join(work, "vgrd.tif")
    wspd_tif = os.path.join(work, "wspd.tif")
    wdir_tif = os.path.join(work, "wdir.tif")

    # Download both components
    ugrd_url = nomads_url(date_str, hour_str, "f00", "UGRD", "10_m_above_ground")
    vgrd_url = nomads_url(date_str, hour_str, "f00", "VGRD", "10_m_above_ground")
    download(ugrd_url, ugrd_grib, "UGRD")
    download(vgrd_url, vgrd_grib, "VGRD")

    # Warp both
    _warp(ugrd_grib, ugrd_tif, "UGRD")
    _warp(vgrd_grib, vgrd_tif, "VGRD")

    # Compute wind speed
    run_cmd([
        "gdal_calc.py",
        "-A", ugrd_tif, "-B", vgrd_tif,
        "--calc=sqrt(A**2+B**2)",
        "--outfile", wspd_tif,
        "--type=Float32", "--overwrite",
    ], "wind speed = sqrt(u²+v²)")

    # Compute wind direction
    run_cmd([
        "gdal_calc.py",
        "-A", ugrd_tif, "-B", vgrd_tif,
        "--calc=(arctan2(-A, -B) * 57.29577951 + 360) % 360",
        "--outfile", wdir_tif,
        "--type=Float32", "--overwrite",
    ], "wind direction = atan2(-u, -v)")

    output_dirs = []

    # 1. Wind speed tiles
    wspd_rgba = os.path.join(work, "wspd_rgba.tif")
    wspd_tiles = os.path.join(work, "tiles_speed")
    _color_relief(wspd_tif, os.path.join(RAMP_DIR, "wind_ramp.txt"), wspd_rgba, "wind-speed")
    _make_tiles(wspd_rgba, wspd_tiles, "wind-speed")
    output_dirs.append(wspd_tiles)

    # 2. Wind direction tiles (nearest resampling — no 0/360 interpolation)
    wdir_rgba = os.path.join(work, "wdir_rgba.tif")
    wdir_tiles = os.path.join(work, "tiles_direction")
    _color_relief(wdir_tif, os.path.join(RAMP_DIR, "wind_direction_ramp.txt"), wdir_rgba, "wind-direction")
    _make_tiles(wdir_rgba, wdir_tiles, "wind-direction", resampling="near")
    output_dirs.append(wdir_tiles)

    # 3. U component tiles (nearest)
    ugrd_rgba = os.path.join(work, "ugrd_rgba.tif")
    u_tiles = os.path.join(work, "tiles_u")
    _color_relief(ugrd_tif, os.path.join(RAMP_DIR, "wind_u_ramp.txt"), ugrd_rgba, "wind-u")
    _make_tiles(ugrd_rgba, u_tiles, "wind-u", resampling="near")
    output_dirs.append(u_tiles)

    # 4. V component tiles (nearest)
    vgrd_rgba = os.path.join(work, "vgrd_rgba.tif")
    v_tiles = os.path.join(work, "tiles_v")
    _color_relief(vgrd_tif, os.path.join(RAMP_DIR, "wind_v_ramp.txt"), vgrd_rgba, "wind-v")
    _make_tiles(vgrd_rgba, v_tiles, "wind-v", resampling="near")
    output_dirs.append(v_tiles)

    result_hash = compute_output_hash(output_dirs)

    # Cleanup intermediate
    for f in [ugrd_grib, vgrd_grib, ugrd_tif, vgrd_tif, wspd_tif, wdir_tif,
              wspd_rgba, wdir_rgba, ugrd_rgba, vgrd_rgba]:
        if os.path.exists(f):
            os.remove(f)

    return output_dirs, result_hash


def process_packet(layer: str, hrrr_date: str, hrrr_hour: str,
                   forecast_hour: str) -> tuple[list[str], str]:
    """Route to the correct processor based on layer type.

    Returns (output_dirs, result_hash).
    """
    # Clean work dir
    if os.path.exists(WORK_DIR):
        shutil.rmtree(WORK_DIR)
    os.makedirs(WORK_DIR, exist_ok=True)

    if layer == "temperature":
        return process_temperature(hrrr_date, hrrr_hour, "f00")
    elif layer == "temperature-f01":
        return process_temperature(hrrr_date, hrrr_hour, "f01")
    elif layer == "wind":
        return process_wind(hrrr_date, hrrr_hour)
    elif layer == "precipitation":
        return process_precipitation(hrrr_date, hrrr_hour)
    else:
        raise ValueError(f"Unknown layer: {layer}")
