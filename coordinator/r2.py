"""Gale Coordinator — Cloudflare R2 presigned URL generation."""

import boto3
from botocore.config import Config
from config import settings

# Tile keys for CONUS at zoom 2-6 (XYZ scheme)
# Precomputed bounding box: lon -130 to -60, lat 20 to 55
ZOOM_TILE_RANGES = {
    2: {"x": (0, 1), "y": (1, 2)},
    3: {"x": (0, 3), "y": (2, 4)},
    4: {"x": (1, 6), "y": (4, 8)},
    5: {"x": (3, 12), "y": (9, 16)},
    6: {"x": (7, 24), "y": (18, 31)},
}


def get_s3_client():
    """Create boto3 S3 client for Cloudflare R2."""
    return boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint,
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def tile_keys(prefix: str, zoom_min: int = 2, zoom_max: int = 6) -> list[str]:
    """Generate all tile keys for CONUS at given zoom range."""
    keys = []
    for z in range(zoom_min, zoom_max + 1):
        r = ZOOM_TILE_RANGES.get(z)
        if not r:
            continue
        for x in range(r["x"][0], r["x"][1] + 1):
            for y in range(r["y"][0], r["y"][1] + 1):
                keys.append(f"{prefix}/{z}/{x}/{y}.png")
    return keys


def generate_presigned_urls(
    output_prefix: str,
    extra_keys: list[str] | None = None,
) -> dict[str, str]:
    """Generate presigned PUT URLs for all tiles + metadata.json.

    Args:
        output_prefix: e.g. "temperature" or "wind"
        extra_keys: additional keys beyond tiles (e.g. metadata.json)

    Returns:
        Dict mapping object key to presigned PUT URL.
    """
    client = get_s3_client()
    keys = tile_keys(output_prefix)
    keys.append(f"{output_prefix}/metadata.json")
    if extra_keys:
        keys.extend(extra_keys)

    urls = {}
    for key in keys:
        urls[key] = client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": settings.R2_BUCKET,
                "Key": key,
                "ContentType": "image/png" if key.endswith(".png") else "application/json",
            },
            ExpiresIn=settings.PRESIGNED_URL_EXPIRY_SECONDS,
        )
    return urls


def generate_multi_prefix_urls(prefixes: list[str]) -> dict[str, str]:
    """Generate presigned URLs for multiple output prefixes (e.g. wind mega-packet)."""
    all_urls = {}
    for prefix in prefixes:
        all_urls.update(generate_presigned_urls(prefix))
    return all_urls
