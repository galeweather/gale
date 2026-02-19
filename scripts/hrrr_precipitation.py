#!/usr/bin/env python3
"""Gale: HRRR Precipitation (APCP) → Hourly-rate Color-ramped Slippy Map tiles (f01-f18).

Downloads HRRR APCP (accumulated precipitation) for f01 through f18, computes
hourly rates by subtracting adjacent hours (rate[N] = APCP[N] - APCP[N-1]),
applies a color ramp, and generates PNG tiles at zoom 2-6.

APCP at f00 is always zero (no time elapsed), so we start at f01.
f01 = 0-1h accumulation (used directly as hourly rate).
f02-f18 = subtracted from previous hour to get per-hour rate.

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
RAMP_FILE = os.path.join(SCRIPT_DIR, "precip_ramp.txt")
NOMADS_BASE = "https://nomads.ncep.noaa.gov/cgi-bin/filter_hrrr_2d.pl"
MAX_FORECAST_HOUR = 18


def find_latest_hrrr_run():
    """Find latest HRRR run where f12 APCP is available."""
    now = datetime.now(timezone.utc)
    for hours_ago in range(6):
        run_time = now - timedelta(hours=hours_ago)
        date_str = run_time.strftime("%Y%m%d")
        hour_str = run_time.strftime("%H")
        url = (
            f"{NOMADS_BASE}?dir=%2Fhrrr.{date_str}%2Fconus"
            f"&file=hrrr.t{hour_str}z.wrfsfcf{MAX_FORECAST_HOUR:02d}.grib2"
            f"&var_APCP=on&lev_surface=on"
        )
        try:
            req = Request(url, method="HEAD")
            req.add_header("User-Agent", "Gale Weather (galeweather.com)")
            resp = urlopen(req, timeout=15)
            if resp.status == 200:
                print(f"Found HRRR run: {date_str} {hour_str}Z (f01-f{MAX_FORECAST_HOUR:02d} available)")
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


def generate_tiles(rgba_file, output_dir, tag):
    """Generate slippy map tiles from RGBA GeoTIFF."""
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

    return count_tiles(output_dir)


def main():
    date_str, hour_str = find_latest_hrrr_run()
    if date_str is None:
        print("NOMADS unavailable — no HRRR runs found in last 6 hours. Exiting gracefully.")
        sys.exit(0)

    run_hour = int(hour_str)
    run_dt = datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=timezone.utc)
    all_tiles = {}
    valid_times = {}
    prev_tif = None  # Keep previous hour's warped TIF for subtraction

    for fhour in range(1, MAX_FORECAST_HOUR + 1):
        tag = f"f{fhour:02d}"
        grib_file = os.path.join(SCRIPT_DIR, f"hrrr_apcp_{tag}.grib2")
        tif_file = os.path.join(SCRIPT_DIR, f"hrrr_apcp_{tag}.tif")
        hourly_tif = os.path.join(SCRIPT_DIR, f"hrrr_apcp_{tag}_hourly.tif")
        rgba_file = os.path.join(SCRIPT_DIR, f"hrrr_apcp_{tag}_rgba.tif")

        # Output directory: f01 → precipitation/ (backward compat), f02+ → precipitation-fXX/
        if fhour == 1:
            out_dir = os.path.join(SCRIPT_DIR, "output", "precipitation")
        else:
            out_dir = os.path.join(SCRIPT_DIR, "output", f"precipitation-f{fhour:02d}")

        try:
            # Download APCP for this forecast hour
            url = (
                f"{NOMADS_BASE}?dir=%2Fhrrr.{date_str}%2Fconus"
                f"&file=hrrr.t{hour_str}z.wrfsfcf{fhour:02d}.grib2"
                f"&var_APCP=on&lev_surface=on"
            )
            download(url, grib_file)

            # Reproject to EPSG:4326
            warp_grib(grib_file, tif_file, tag)

            # Compute hourly rate
            if prev_tif is not None and fhour > 1:
                # Hourly rate = current accumulated - previous accumulated (clamp to 0)
                run_cmd([
                    "gdal_calc.py",
                    "-A", tif_file,
                    "-B", prev_tif,
                    "--calc=maximum(A-B, 0)",
                    "--outfile", hourly_tif,
                    "--type=Float32",
                    "--overwrite"
                ], f"{tag}: Hourly rate (f{fhour:02d} - f{fhour-1:02d})")
                rate_tif = hourly_tif
            else:
                # f01: 0-1h accumulation IS the hourly rate
                rate_tif = tif_file

            # Color relief → RGBA GeoTIFF
            run_cmd([
                "gdaldem", "color-relief",
                rate_tif, RAMP_FILE, rgba_file,
                "-alpha", "-of", "GTiff"
            ], f"{tag}: Color relief → RGBA")

            # Generate tiles
            tile_count = generate_tiles(rgba_file, out_dir, tag)
            all_tiles[tag] = tile_count
            valid_times[tag] = (run_dt + timedelta(hours=run_hour + fhour)).isoformat()
            print(f"{tag}: {tile_count} tiles")

            # Keep current warped TIF for next iteration's subtraction
            if prev_tif and os.path.exists(prev_tif):
                os.remove(prev_tif)
            prev_tif = tif_file

            # Cleanup other intermediates (keep tif_file as prev_tif)
            for f in [grib_file, hourly_tif, rgba_file]:
                if os.path.exists(f):
                    os.remove(f)

        except Exception as e:
            print(f"WARNING: {tag} processing failed, skipping: {e}")
            # Cleanup on failure
            for f in [grib_file, tif_file, hourly_tif, rgba_file]:
                if os.path.exists(f):
                    os.remove(f)

    # Cleanup last prev_tif
    if prev_tif and os.path.exists(prev_tif):
        os.remove(prev_tif)

    if not all_tiles:
        print("ERROR: No precipitation forecast hours processed successfully.")
        sys.exit(1)

    # Write metadata (into f01 dir — the primary precipitation directory)
    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "hrrr_run": f"{date_str} {hour_str}Z",
        "hrrr_date": date_str,
        "hrrr_hour": hour_str,
        "zoom_range": "2-6",
        "variable": "APCP:surface (hourly rate, subtracted from accumulated)",
        "forecast_hours": sorted(all_tiles.keys()),
        "valid_times": valid_times
    }
    meta_path = os.path.join(SCRIPT_DIR, "output", "precipitation", "metadata.json")
    os.makedirs(os.path.dirname(meta_path), exist_ok=True)
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)
    print(f"Wrote {meta_path}")

    print(f"Done! Processed {len(all_tiles)} precipitation forecast hours.")


if __name__ == "__main__":
    main()
