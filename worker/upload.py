"""Gale Worker — Parallel tile upload via presigned PUT URLs."""

import os
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import Request, urlopen
from datetime import datetime, timezone

# Layer → output dir name mapping (order matches process.py output_dirs)
LAYER_DIR_MAP = {
    "temperature": ["temperature"],
    "temperature-f01": ["temperature-f01"],
    "precipitation": ["precipitation"],
    "wind": ["wind", "wind-direction", "wind-u", "wind-v"],
    "atmosphere": ["atmosphere"],
}

# Wind output dirs come from process.py in this fixed order
WIND_PREFIX_ORDER = ["wind", "wind-direction", "wind-u", "wind-v"]


def _put_file(url: str, filepath: str, content_type: str) -> str:
    """Upload a single file via presigned PUT URL."""
    with open(filepath, "rb") as f:
        data = f.read()
    req = Request(url, data=data, method="PUT")
    req.add_header("Content-Type", content_type)
    with urlopen(req, timeout=30) as resp:
        if resp.status not in (200, 201):
            raise RuntimeError(f"Upload failed: {resp.status}")
    return filepath


def upload_tiles(
    output_dirs: list[str],
    presigned_urls: dict[str, str],
    layer: str,
    hrrr_date: str,
    hrrr_hour: str,
    forecast_hour: str,
    max_workers: int = 16,
) -> int:
    """Upload all tiles from output_dirs using presigned URLs.

    Returns count of files uploaded.
    """
    # Build mapping: local file path → presigned URL key
    uploads = []  # (local_path, url, content_type)

    prefixes = LAYER_DIR_MAP.get(layer, [layer])

    if layer == "atmosphere":
        # Atmosphere: upload profile.bin directly (not tile grid)
        output_dir = output_dirs[0]
        profile_path = os.path.join(output_dir, "profile.bin")
        key = "atmosphere/profile.bin"
        if key in presigned_urls and os.path.exists(profile_path):
            uploads.append((profile_path, presigned_urls[key], "application/octet-stream"))
    elif layer == "wind":
        # Wind has 4 output dirs in fixed order: speed, direction, u, v
        for i, output_dir in enumerate(output_dirs):
            prefix = WIND_PREFIX_ORDER[i]
            _collect_tile_uploads(output_dir, prefix, presigned_urls, uploads)
    else:
        # Single-prefix layers
        prefix = prefixes[0]
        _collect_tile_uploads(output_dirs[0], prefix, presigned_urls, uploads)

    if not uploads:
        print("  WARNING: No files matched presigned URLs")
        return 0

    print(f"  Uploading {len(uploads)} files...")
    uploaded = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_put_file, url, path, ct): path
            for path, url, ct in uploads
        }
        for future in as_completed(futures):
            try:
                future.result()
                uploaded += 1
            except Exception as e:
                failed += 1
                print(f"  Upload error: {e}")

    print(f"  Uploaded {uploaded} files ({failed} failed)")
    return uploaded


def _collect_tile_uploads(
    output_dir: str,
    prefix: str,
    presigned_urls: dict[str, str],
    uploads: list,
):
    """Collect (local_path, url, content_type) tuples for tiles in output_dir."""
    for root, _, files in os.walk(output_dir):
        for fname in files:
            if not fname.endswith(".png"):
                continue
            local_path = os.path.join(root, fname)
            rel = os.path.relpath(local_path, output_dir)
            key = f"{prefix}/{rel}"
            if key in presigned_urls:
                uploads.append((local_path, presigned_urls[key], "image/png"))


def upload_metadata(
    presigned_urls: dict[str, str],
    layer: str,
    hrrr_date: str,
    hrrr_hour: str,
    forecast_hour: str,
    tile_count: int,
    output_prefixes: list[str],
):
    """Upload metadata.json for each output prefix."""
    for prefix in output_prefixes:
        key = f"{prefix}/metadata.json"
        url = presigned_urls.get(key)
        if not url:
            print(f"  WARNING: No presigned URL for {key}")
            continue

        metadata = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "hrrr_run": f"{hrrr_date} {hrrr_hour}Z",
            "hrrr_date": hrrr_date,
            "hrrr_hour": hrrr_hour,
            "zoom_range": "2-6",
            "tile_count": tile_count,
            "source": "distributed_worker",
        }

        # Layer-specific metadata fields
        if prefix == "temperature":
            metadata["variable"] = "TMP:2m above ground"
            metadata["forecast_hours"] = ["f00", "f01"]
            run_hour = int(hrrr_hour)
            f00_valid = f"{hrrr_date[:4]}-{hrrr_date[4:6]}-{hrrr_date[6:]}T{run_hour:02d}:00:00+00:00"
            f01_valid = f"{hrrr_date[:4]}-{hrrr_date[4:6]}-{hrrr_date[6:]}T{run_hour + 1:02d}:00:00+00:00"
            metadata["valid_times"] = {"f00": f00_valid, "f01": f01_valid}
        elif prefix == "temperature-f01":
            metadata["variable"] = "TMP:2m above ground (f01)"
        elif prefix == "precipitation":
            metadata["variable"] = "APCP:surface (0-1h accumulation from f01)"
        elif prefix == "wind":
            metadata["variable"] = "WIND:10m above ground (speed from UGRD+VGRD)"
        elif prefix == "wind-direction":
            metadata["variable"] = "WDIR:10m above ground (direction from UGRD+VGRD)"
        elif prefix == "wind-u":
            metadata["variable"] = "UGRD:10m above ground (u-component)"
            metadata["encoding"] = "grayscale: pixel / 255 * 80 - 40 = m/s"
        elif prefix == "wind-v":
            metadata["variable"] = "VGRD:10m above ground (v-component)"
            metadata["encoding"] = "grayscale: pixel / 255 * 80 - 40 = m/s"
        elif prefix == "atmosphere":
            metadata["variable"] = "HRRR pressure-level profile (6 levels × 5 vars)"
            metadata["levels_mb"] = [1000, 925, 850, 700, 500, 250]
            metadata["grid"] = {
                "width": 281, "height": 141,
                "lon_min": -130, "lon_max": -60,
                "lat_min": 20, "lat_max": 55,
                "resolution_deg": 0.25,
            }
            metadata["encoding"] = "binary profile.bin: int16 per value (TMP×10, UGRD×100, VGRD×100, RH×100, HGT)"

        data = json.dumps(metadata, indent=2).encode()
        req = Request(url, data=data, method="PUT")
        req.add_header("Content-Type", "application/json")
        with urlopen(req, timeout=15) as resp:
            if resp.status not in (200, 201):
                print(f"  WARNING: metadata upload failed for {key}: {resp.status}")
            else:
                print(f"  Uploaded {key}")
