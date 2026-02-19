"""Gale Coordinator — HRRR availability scheduler."""

import threading
import time
import json
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
from urllib.error import URLError
from config import settings
from models import get_db
from packets import create_packets_for_run

NOMADS_BASE = settings.NOMADS_BASE


def check_hrrr_availability():
    """Check NOMADS for the latest available HRRR run and create packets."""
    now = datetime.now(timezone.utc)
    for hours_ago in range(4):
        run_time = now - timedelta(hours=hours_ago)
        date_str = run_time.strftime("%Y%m%d")
        hour_str = run_time.strftime("%H")
        hrrr_run = f"{date_str} {hour_str}Z"

        # Skip if we already have this run
        conn = get_db()
        try:
            existing = conn.execute(
                "SELECT hrrr_run FROM hrrr_runs WHERE hrrr_run = ?",
                (hrrr_run,),
            ).fetchone()
            if existing:
                continue
        finally:
            conn.close()

        # Check if R2 already has tiles from GH Actions
        try:
            meta_url = f"{settings.R2_PUBLIC_URL}/temperature/metadata.json"
            req = Request(meta_url)
            req.add_header("User-Agent", "Gale Coordinator")
            with urlopen(req, timeout=10) as resp:
                meta = json.loads(resp.read())
                if meta.get("hrrr_run") == hrrr_run:
                    # GH Actions already handled this run
                    conn = get_db()
                    try:
                        conn.execute(
                            "INSERT OR IGNORE INTO hrrr_runs (hrrr_run, source) "
                            "VALUES (?, 'github_actions')",
                            (hrrr_run,),
                        )
                        conn.commit()
                    finally:
                        conn.close()
                    return
        except (URLError, OSError, json.JSONDecodeError, KeyError):
            pass

        # Check NOMADS availability
        url = (
            f"{NOMADS_BASE}?dir=%2Fhrrr.{date_str}%2Fconus"
            f"&file=hrrr.t{hour_str}z.wrfsfcf00.grib2"
            f"&var_TMP=on&lev_2_m_above_ground=on"
        )
        try:
            req = Request(url, method="HEAD")
            req.add_header("User-Agent", "Gale Coordinator")
            resp = urlopen(req, timeout=15)
            if resp.status == 200:
                # New HRRR run available
                conn = get_db()
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO hrrr_runs (hrrr_run, source) "
                        "VALUES (?, 'coordinator')",
                        (hrrr_run,),
                    )
                    conn.commit()
                finally:
                    conn.close()

                create_packets_for_run(date_str, hour_str)
                print(f"Created packets for HRRR run {hrrr_run}")
                return
        except (URLError, OSError):
            continue


def _scheduler_loop():
    """Background thread that checks HRRR availability on interval."""
    while True:
        try:
            check_hrrr_availability()
        except Exception as e:
            print(f"Scheduler error: {e}")
        time.sleep(settings.HRRR_CHECK_INTERVAL_SECONDS)


def start_scheduler():
    """Start the scheduler background thread."""
    thread = threading.Thread(target=_scheduler_loop, daemon=True)
    thread.start()
    print(f"Scheduler started (interval: {settings.HRRR_CHECK_INTERVAL_SECONDS}s)")
