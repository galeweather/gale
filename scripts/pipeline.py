#!/usr/bin/env python3
"""Gale proof-of-concept: GRIB2 → Slippy Map PNG tiles"""

import subprocess
import os

GRIB_FILE = "scripts/test.grib2"
TIFF_FILE = "scripts/test.tif"
BYTE_VRT = "scripts/test_byte.vrt"
TILES_DIR = "scripts/tiles"

# Step 1: GRIB2 → GeoTIFF
print("Converting GRIB2 → GeoTIFF...")
subprocess.run([
    "gdal_translate", "-of", "GTiff", "-a_srs", "EPSG:4326",
    GRIB_FILE, TIFF_FILE
], check=True)

# Step 2: Scale to 8-bit (temperature range ~200-330K → 0-255)
print("Scaling to 8-bit...")
subprocess.run([
    "gdal_translate", "-of", "VRT", "-ot", "Byte",
    "-scale", "200", "330", "0", "255",
    TIFF_FILE, BYTE_VRT
], check=True)

# Step 3: Render Slippy Map tiles (zoom 2-5)
print("Rendering tiles (zoom 2-5)...")
os.makedirs(TILES_DIR, exist_ok=True)
subprocess.run([
    "gdal2tiles.py",
    "--zoom=2-5",
    "--processes=4",
    "--resampling=bilinear",
    "--xyz",
    BYTE_VRT, TILES_DIR
], check=True)

total = sum(len(f) for _, _, f in os.walk(TILES_DIR) if f)
print(f"\nDone! Generated {total} tiles in {TILES_DIR}/")
print("Preview: open scripts/tiles/leaflet.html in a browser")
