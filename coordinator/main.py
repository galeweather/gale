"""Gale Coordinator — FastAPI application entry point."""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from models import init_db, get_db
from auth import router as auth_router
from packets import router as packets_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize DB on startup, start scheduler."""
    init_db()
    # Scheduler started in commit 4
    from scheduler import start_scheduler
    start_scheduler()
    yield


app = FastAPI(title="Gale Coordinator", version="0.1.0", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(packets_router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/v1/status")
def status():
    conn = get_db()
    try:
        workers = conn.execute(
            "SELECT COUNT(*) as total, "
            "SUM(CASE WHEN last_heartbeat > datetime('now', '-5 minutes') THEN 1 ELSE 0 END) as active "
            "FROM workers WHERE is_flagged = 0"
        ).fetchone()

        packets = conn.execute(
            "SELECT status, COUNT(*) as count FROM packets GROUP BY status"
        ).fetchall()
        packet_stats = {row["status"]: row["count"] for row in packets}

        latest_run = conn.execute(
            "SELECT hrrr_run, source, completed_at FROM hrrr_runs "
            "ORDER BY detected_at DESC LIMIT 1"
        ).fetchone()

        return {
            "workers": {
                "total": workers["total"] if workers else 0,
                "active": workers["active"] if workers else 0,
            },
            "packets": packet_stats,
            "latest_run": dict(latest_run) if latest_run else None,
        }
    finally:
        conn.close()
