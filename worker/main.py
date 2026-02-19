#!/usr/bin/env python3
"""Gale Worker — CLI entry point.

Usage:
    docker run gale-worker --coordinator=URL --secret=SECRET --name=NAME
    python -m worker.main --coordinator=URL --secret=SECRET --name=NAME
"""

import argparse
import json
import time
import sys
from urllib.request import Request, urlopen
from urllib.error import URLError

from process import process_packet
from upload import upload_tiles, upload_metadata

POLL_INTERVAL = 30  # seconds between polls when no work available


def api_request(base_url: str, method: str, path: str,
                token: str | None = None, body: dict | None = None) -> dict:
    """Make an HTTP request to the coordinator API."""
    url = f"{base_url}{path}"
    data = json.dumps(body).encode() if body else None
    req = Request(url, data=data, method=method)
    req.add_header("User-Agent", "Gale Worker")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def register(base_url: str, name: str, secret: str) -> tuple[int, str]:
    """Register with coordinator, return (worker_id, token)."""
    result = api_request(base_url, "POST", "/v1/workers/register", body={
        "name": name,
        "secret": secret,
    })
    return result["worker_id"], result["token"]


def heartbeat(base_url: str, token: str) -> int:
    """Send heartbeat, return current credits."""
    result = api_request(base_url, "POST", "/v1/workers/heartbeat", token=token)
    return result["credits"]


def poll_for_work(base_url: str, token: str) -> dict | None:
    """Poll for next packet. Returns packet dict or None."""
    result = api_request(base_url, "GET", "/v1/packets/next", token=token)
    if result.get("packet_id") is None:
        return None
    return result


def report_complete(base_url: str, token: str, packet_id: int, result_hash: str) -> dict:
    """Report packet completion with hash."""
    return api_request(
        base_url, "POST", f"/v1/packets/{packet_id}/complete",
        token=token, body={"result_hash": result_hash},
    )


def report_failure(base_url: str, token: str, packet_id: int, reason: str) -> dict:
    """Report packet failure."""
    return api_request(
        base_url, "POST", f"/v1/packets/{packet_id}/fail",
        token=token, body={"reason": reason},
    )


def main():
    parser = argparse.ArgumentParser(description="Gale Distributed Worker")
    parser.add_argument("--coordinator", required=True, help="Coordinator URL")
    parser.add_argument("--secret", required=True, help="Registration secret")
    parser.add_argument("--name", required=True, help="Worker name")
    args = parser.parse_args()

    base_url = args.coordinator.rstrip("/")

    # Register
    print(f"Registering with {base_url}...")
    try:
        worker_id, token = register(base_url, args.name, args.secret)
        print(f"Registered as worker #{worker_id}")
    except Exception as e:
        print(f"Registration failed: {e}")
        sys.exit(1)

    # Poll loop
    consecutive_errors = 0
    while True:
        try:
            # Heartbeat
            credits = heartbeat(base_url, token)

            # Check for work
            packet = poll_for_work(base_url, token)
            if not packet:
                print(f"No work available (credits: {credits}). Sleeping {POLL_INTERVAL}s...")
                time.sleep(POLL_INTERVAL)
                consecutive_errors = 0
                continue

            packet_id = packet["packet_id"]
            layer = packet["layer"]
            hrrr_date = packet["hrrr_date"]
            hrrr_hour = packet["hrrr_hour"]
            forecast_hour = packet["forecast_hour"]
            presigned_urls = packet["presigned_urls"]
            output_prefixes = packet["output_prefixes"]

            print(f"\n{'='*60}")
            print(f"Processing packet #{packet_id}: {layer} ({hrrr_date} {hrrr_hour}Z {forecast_hour})")
            print(f"{'='*60}")

            try:
                # Process GRIB2 data into tiles
                output_dirs, result_hash = process_packet(
                    layer, hrrr_date, hrrr_hour, forecast_hour,
                )
                print(f"  Hash: {result_hash[:16]}...")

                # Upload tiles via presigned URLs
                tile_count = upload_tiles(
                    output_dirs, presigned_urls, layer,
                    hrrr_date, hrrr_hour, forecast_hour,
                )

                # Upload metadata.json for each prefix
                upload_metadata(
                    presigned_urls, layer, hrrr_date, hrrr_hour,
                    forecast_hour, tile_count, output_prefixes,
                )

                # Report completion
                result = report_complete(base_url, token, packet_id, result_hash)
                print(f"  Result: {result.get('status', 'unknown')}")

            except Exception as e:
                print(f"  Processing failed: {e}")
                try:
                    report_failure(base_url, token, packet_id, str(e)[:200])
                except Exception:
                    pass

            consecutive_errors = 0

        except (URLError, OSError) as e:
            consecutive_errors += 1
            backoff = min(300, POLL_INTERVAL * consecutive_errors)
            print(f"Connection error: {e}. Retrying in {backoff}s...")
            time.sleep(backoff)
        except KeyboardInterrupt:
            print("\nShutting down.")
            break
        except Exception as e:
            consecutive_errors += 1
            backoff = min(300, POLL_INTERVAL * consecutive_errors)
            print(f"Unexpected error: {e}. Retrying in {backoff}s...")
            time.sleep(backoff)


if __name__ == "__main__":
    main()
