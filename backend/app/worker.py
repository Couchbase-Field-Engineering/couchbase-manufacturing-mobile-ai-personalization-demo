"""Processes offline-first production orders and other planning actions.

The phone never calls a production-order API directly. A tap writes a `production_orders`
document into Couchbase Lite (`status: "pending"`); the replicator pushes it
through Sync Gateway whenever the planner has connectivity, and this worker picks
it up from Couchbase, runs it through AOM (tool_gateway), and writes the
outcome back onto the same document. Sync Gateway replicates the update to
the phone, where a live query flips the card from "On device" to "Completed".
Budget overrides and overtime shifts sit in `awaiting_approval` until a
person decides in AOM's Approvals page; this worker polls and re-invokes with
the approval id.
"""
from __future__ import annotations

import asyncio
import logging

from . import config, metrics, tool_gateway
from .actions import READ_ONLY_TOOLS, TOOLS
from .docs import now_iso, planner_channel
from .store import store

log = logging.getLogger("mfg.worker")

# Tools a planner's phone may request. Read-only tools are answered by the
# assistant; nothing here lets a phone act as another planner.
REQUESTABLE = set(TOOLS) - set(READ_ONLY_TOOLS)


async def process(key: str, doc: dict) -> dict:
    first = doc.get("status") != "awaiting_approval"
    planner_id = doc.get("planner_id")
    tool = doc.get("tool")
    if doc.get("status") == "awaiting_approval":
        status = await tool_gateway.approval_status(doc.get("approval_id", ""))
        if status in (None, "pending"):
            return doc
        if status != "approved":
            doc.update(status="failed", summary=f"This request was not approved ({status}).",
                       completed_at=now_iso())
            await store.upsert("production_orders", key, doc)
            return doc
        result = await tool_gateway.invoke(tool, {**doc.get("args", {}), "planner_id": planner_id},
                                           approval_id=doc["approval_id"])
    else:
        if tool not in REQUESTABLE:
            doc.update(status="failed", summary=f"'{tool}' can't be requested from the app", completed_at=now_iso())
            await store.upsert("production_orders", key, doc)
            return doc
        doc.update(status="processing", processing_at=now_iso())
        await store.upsert("production_orders", key, doc)
        # planner_id comes from the document, which Sync Gateway only accepted
        # from that signed-in planner (requireUser in the sync function).
        args = {k: v for k, v in (doc.get("args") or {}).items() if k != "planner_id"}
        result = await tool_gateway.invoke(tool, {**args, "planner_id": planner_id})

    status = result.get("status", "failed")
    if result.get("ok") and status in ("ok", "completed", "materials_pending"):
        status = "completed" if status == "ok" else status
    if not result.get("ok"):
        status = "failed"
    doc.update({
        "status": status, "summary": result.get("summary"), "cost_usd": result.get("cost_usd"),
        "item": result.get("item"), "reason_code": result.get("reason_code"), "auth": result.get("auth"),
        "governed": result.get("governed", False), "trace_id": result.get("trace_id"),
        "approval_id": result.get("approval_id", doc.get("approval_id")),
        "completed_at": None if status == "awaiting_approval" else now_iso(),
        "channels": [planner_channel(planner_id)],
    })
    await store.upsert("production_orders", key, doc)
    metrics.order(doc, first)
    log.info("production order %s %s -> %s", key, tool, status)
    return doc


async def run_once() -> int:
    rows = await store.pending_production_orders()
    for key, doc in rows:
        try:
            await process(key, doc)
        except Exception as exc:  # noqa: BLE001
            log.exception("production order %s failed", key)
            doc.update(status="failed", summary=f"Something went wrong: {exc}", completed_at=now_iso())
            await store.upsert("production_orders", key, doc)
    return len(rows)


async def loop():
    while True:
        try:
            await run_once()
        except Exception as exc:  # noqa: BLE001
            log.warning("worker tick failed: %s", exc)
        await asyncio.sleep(config.REQUEST_POLL_SECONDS)
