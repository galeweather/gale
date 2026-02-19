"""Gale Coordinator — Packet lifecycle and API endpoints."""

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from auth import verify_worker
from config import settings
from models import get_db
from r2 import generate_presigned_urls, generate_multi_prefix_urls
from credits import award_credits

router = APIRouter(prefix="/v1/packets", tags=["packets"])

# Layer → output prefixes mapping
LAYER_PREFIXES = {
    "temperature": ["temperature"],
    "temperature-f01": ["temperature-f01"],
    "wind": ["wind", "wind-direction", "wind-u", "wind-v"],
    "precipitation": ["precipitation"],
}

# All layers created per HRRR run
LAYERS_PER_RUN = ["temperature", "temperature-f01", "wind", "precipitation"]


def create_packets_for_run(hrrr_date: str, hrrr_hour: str):
    """Create pending packets for all layers of a new HRRR run."""
    hrrr_run = f"{hrrr_date} {hrrr_hour}Z"
    conn = get_db()
    try:
        for layer in LAYERS_PER_RUN:
            forecast_hour = "f01" if layer in ("temperature-f01", "precipitation") else "f00"
            output_prefix = LAYER_PREFIXES[layer][0]  # primary prefix
            conn.execute(
                "INSERT OR IGNORE INTO packets "
                "(layer, hrrr_run, hrrr_date, hrrr_hour, forecast_hour, "
                " output_prefix, status) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
                (layer, hrrr_run, hrrr_date, hrrr_hour, forecast_hour, output_prefix),
            )
        conn.commit()
    finally:
        conn.close()


def cleanup_stale_assignments():
    """Mark assignments older than timeout as failed, free packets for reassignment."""
    timeout = settings.ASSIGNMENT_TIMEOUT_SECONDS
    conn = get_db()
    try:
        # Find stale assignments
        stale = conn.execute(
            "SELECT a.id, a.packet_id, a.worker_id FROM assignments a "
            "WHERE a.status = 'assigned' "
            "AND a.assigned_at < datetime('now', ? || ' seconds')",
            (f"-{timeout}",),
        ).fetchall()

        for row in stale:
            conn.execute(
                "UPDATE assignments SET status = 'timeout' WHERE id = ?",
                (row["id"],),
            )
            conn.execute(
                "UPDATE workers SET packets_failed = packets_failed + 1 WHERE id = ?",
                (row["worker_id"],),
            )
            # Check if packet should go back to pending
            active = conn.execute(
                "SELECT COUNT(*) as c FROM assignments "
                "WHERE packet_id = ? AND status = 'assigned'",
                (row["packet_id"],),
            ).fetchone()["c"]
            if active == 0:
                conn.execute(
                    "UPDATE packets SET status = 'pending' WHERE id = ? AND status = 'assigned'",
                    (row["packet_id"],),
                )

        conn.commit()
    finally:
        conn.close()


class PacketResponse(BaseModel):
    packet_id: int
    layer: str
    hrrr_run: str
    hrrr_date: str
    hrrr_hour: str
    forecast_hour: str
    output_prefixes: list[str]
    presigned_urls: dict[str, str]


@router.get("/next")
def get_next_packet(authorization: str = Header()) -> PacketResponse | dict:
    """Assign the next available packet to this worker."""
    worker_id = verify_worker(authorization)

    # Clean up stale assignments first
    cleanup_stale_assignments()

    conn = get_db()
    try:
        # Find a packet that needs workers (< 2 active assignments)
        # Prefer packets with fewer assignments, then oldest first
        packet = conn.execute(
            """
            SELECT p.id, p.layer, p.hrrr_run, p.hrrr_date, p.hrrr_hour,
                   p.forecast_hour, p.output_prefix, p.status
            FROM packets p
            WHERE p.status IN ('pending', 'assigned')
            AND p.id NOT IN (
                SELECT packet_id FROM assignments
                WHERE worker_id = ? AND status IN ('assigned', 'completed')
            )
            AND (
                SELECT COUNT(*) FROM assignments
                WHERE packet_id = p.id AND status IN ('assigned', 'completed')
            ) < 2
            ORDER BY p.created_at ASC
            LIMIT 1
            """,
            (worker_id,),
        ).fetchone()

        if not packet:
            return {"packet_id": None, "message": "No work available"}

        # Create assignment
        conn.execute(
            "INSERT INTO assignments (packet_id, worker_id) VALUES (?, ?)",
            (packet["id"], worker_id),
        )
        # Update packet status to assigned
        conn.execute(
            "UPDATE packets SET status = 'assigned' WHERE id = ?",
            (packet["id"],),
        )
        conn.commit()

        # Generate presigned URLs for all output prefixes
        prefixes = LAYER_PREFIXES.get(packet["layer"], [packet["output_prefix"]])
        if len(prefixes) > 1:
            urls = generate_multi_prefix_urls(prefixes)
        else:
            urls = generate_presigned_urls(prefixes[0])

        return PacketResponse(
            packet_id=packet["id"],
            layer=packet["layer"],
            hrrr_run=packet["hrrr_run"],
            hrrr_date=packet["hrrr_date"],
            hrrr_hour=packet["hrrr_hour"],
            forecast_hour=packet["forecast_hour"],
            output_prefixes=prefixes,
            presigned_urls=urls,
        )
    finally:
        conn.close()


class CompleteRequest(BaseModel):
    result_hash: str


@router.post("/{packet_id}/complete")
def complete_packet(
    packet_id: int, req: CompleteRequest, authorization: str = Header()
):
    """Submit processing result hash for verification."""
    worker_id = verify_worker(authorization)

    conn = get_db()
    try:
        # Verify assignment exists
        assignment = conn.execute(
            "SELECT id FROM assignments "
            "WHERE packet_id = ? AND worker_id = ? AND status = 'assigned'",
            (packet_id, worker_id),
        ).fetchone()
        if not assignment:
            raise HTTPException(status_code=404, detail="No active assignment")

        # Mark assignment completed
        conn.execute(
            "UPDATE assignments SET status = 'completed', result_hash = ?, "
            "completed_at = datetime('now') WHERE id = ?",
            (req.result_hash, assignment["id"]),
        )
        conn.execute(
            "UPDATE workers SET packets_completed = packets_completed + 1 WHERE id = ?",
            (worker_id,),
        )
        conn.commit()

        # Check if we have 2 completed assignments to verify
        completed = conn.execute(
            "SELECT worker_id, result_hash FROM assignments "
            "WHERE packet_id = ? AND status = 'completed'",
            (packet_id,),
        ).fetchall()

        if len(completed) >= 2:
            hashes = [r["result_hash"] for r in completed]
            if hashes[0] == hashes[1]:
                # Match — packet verified
                conn.execute(
                    "UPDATE packets SET status = 'completed', verified_hash = ?, "
                    "completed_at = datetime('now') WHERE id = ?",
                    (hashes[0], packet_id),
                )
                # Award credits to both workers
                for row in completed:
                    award_credits(
                        conn, row["worker_id"], settings.CREDITS_PER_PACKET,
                        "packet_verified", packet_id,
                    )
                conn.commit()
                return {"status": "verified", "hash": hashes[0]}
            else:
                # Mismatch — need a 3rd worker
                total_assignments = conn.execute(
                    "SELECT COUNT(*) as c FROM assignments WHERE packet_id = ?",
                    (packet_id,),
                ).fetchone()["c"]

                if total_assignments >= 3:
                    # Re-fetch all completed assignments (stale `completed` only had 2)
                    from collections import Counter
                    all_completed = conn.execute(
                        "SELECT worker_id, result_hash FROM assignments "
                        "WHERE packet_id = ? AND status = 'completed'",
                        (packet_id,),
                    ).fetchall()
                    all_hashes = [r["result_hash"] for r in all_completed]
                    hash_counts = Counter(all_hashes)
                    majority_hash, count = hash_counts.most_common(1)[0]
                    if count >= 2:
                        conn.execute(
                            "UPDATE packets SET status = 'completed', "
                            "verified_hash = ?, completed_at = datetime('now') "
                            "WHERE id = ?",
                            (majority_hash, packet_id),
                        )
                        # Credit winners, flag losers
                        for row in all_completed:
                            if row["result_hash"] == majority_hash:
                                award_credits(
                                    conn, row["worker_id"],
                                    settings.CREDITS_PER_PACKET,
                                    "packet_verified", packet_id,
                                )
                            else:
                                conn.execute(
                                    "UPDATE workers SET is_flagged = 1 WHERE id = ?",
                                    (row["worker_id"],),
                                )
                        conn.commit()
                        return {"status": "verified_majority", "hash": majority_hash}
                    else:
                        # No majority — packet failed
                        conn.execute(
                            "UPDATE packets SET status = 'failed' WHERE id = ?",
                            (packet_id,),
                        )
                        conn.commit()
                        return {"status": "failed", "reason": "no hash consensus"}
                else:
                    # Need 3rd assignment — set packet back to assigned for pickup
                    conn.execute(
                        "UPDATE packets SET status = 'assigned' WHERE id = ?",
                        (packet_id,),
                    )
                    conn.commit()
                    return {"status": "verifying", "message": "hash mismatch, awaiting 3rd worker"}

        conn.commit()
        return {"status": "accepted", "message": "awaiting second worker"}
    finally:
        conn.close()


class FailRequest(BaseModel):
    reason: str = ""


@router.post("/{packet_id}/fail")
def fail_packet(
    packet_id: int, req: FailRequest, authorization: str = Header()
):
    """Report packet processing failure."""
    worker_id = verify_worker(authorization)

    conn = get_db()
    try:
        assignment = conn.execute(
            "SELECT id FROM assignments "
            "WHERE packet_id = ? AND worker_id = ? AND status = 'assigned'",
            (packet_id, worker_id),
        ).fetchone()
        if not assignment:
            raise HTTPException(status_code=404, detail="No active assignment")

        conn.execute(
            "UPDATE assignments SET status = 'failed', "
            "completed_at = datetime('now') WHERE id = ?",
            (assignment["id"],),
        )
        conn.execute(
            "UPDATE workers SET packets_failed = packets_failed + 1 WHERE id = ?",
            (worker_id,),
        )

        # Check if packet has any remaining active assignments
        active = conn.execute(
            "SELECT COUNT(*) as c FROM assignments "
            "WHERE packet_id = ? AND status = 'assigned'",
            (packet_id,),
        ).fetchone()["c"]
        if active == 0:
            conn.execute(
                "UPDATE packets SET status = 'pending' WHERE id = ? AND status != 'completed'",
                (packet_id,),
            )
        conn.commit()
        return {"status": "recorded"}
    finally:
        conn.close()
