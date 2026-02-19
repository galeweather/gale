"""Gale Coordinator — Worker authentication."""

import hashlib
import hmac
import secrets
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from config import settings
from models import get_db

router = APIRouter(prefix="/v1/workers", tags=["workers"])


def hash_token(token: str) -> str:
    """SHA-256 hash a token for storage."""
    return hashlib.sha256(token.encode()).hexdigest()


def verify_worker(authorization: str = Header()) -> int:
    """Verify Bearer token, return worker_id. Raises 401 on failure."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    token = authorization[7:]
    token_h = hash_token(token)
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, is_flagged FROM workers WHERE token_hash = ?",
            (token_h,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        if row["is_flagged"]:
            raise HTTPException(status_code=403, detail="Worker is flagged")
        return row["id"]
    finally:
        conn.close()


class RegisterRequest(BaseModel):
    name: str
    secret: str


class RegisterResponse(BaseModel):
    worker_id: int
    token: str


@router.post("/register", response_model=RegisterResponse)
def register(req: RegisterRequest):
    """Register a new worker. Requires coordinator secret."""
    if not settings.COORDINATOR_SECRET:
        raise HTTPException(status_code=503, detail="Registration not configured")
    # Timing-safe comparison
    if not hmac.compare_digest(req.secret, settings.COORDINATOR_SECRET):
        raise HTTPException(status_code=403, detail="Invalid secret")

    token = secrets.token_urlsafe(32)
    token_h = hash_token(token)

    conn = get_db()
    try:
        cursor = conn.execute(
            "INSERT INTO workers (name, token_hash) VALUES (?, ?)",
            (req.name[:64], token_h),
        )
        conn.commit()
        return RegisterResponse(worker_id=cursor.lastrowid, token=token)
    finally:
        conn.close()


class HeartbeatResponse(BaseModel):
    credits: int


@router.post("/heartbeat", response_model=HeartbeatResponse)
def heartbeat(authorization: str = Header()):
    """Update worker heartbeat, return current credits."""
    worker_id = verify_worker(authorization)
    conn = get_db()
    try:
        conn.execute(
            "UPDATE workers SET last_heartbeat = datetime('now') WHERE id = ?",
            (worker_id,),
        )
        conn.commit()
        row = conn.execute(
            "SELECT credits FROM workers WHERE id = ?", (worker_id,)
        ).fetchone()
        return HeartbeatResponse(credits=row["credits"])
    finally:
        conn.close()
