"""Sync Gateway admin calls: one SG user per production planner, and session minting.

The planner signs in to this backend (employee id + last name); the backend makes
sure an SG user named after the planner_id exists with exactly three channels -
`planner:<planner_id>`, `plant:<plant_id>` and `catalog` - and mints a
session. The phone hands that session to Couchbase Lite's SessionAuthenticator,
the same way mobile_field_service does, so a planner's replicator can only ever
pull their own route, their plant's products and the catalog.
"""
from __future__ import annotations

import logging
import secrets

import httpx

from . import config
from .docs import CATALOG_CHANNEL, planner_channel, plant_channel

log = logging.getLogger("mfg.sgw")


def enabled() -> bool:
    return bool(config.SG_ADMIN_URL)


async def mint_session(planner_id: str, plant_id: str) -> dict | None:
    if not enabled():
        return None
    auth = (config.SG_ADMIN_USERNAME, config.SG_ADMIN_PASSWORD)
    base = f"{config.SG_ADMIN_URL}/{config.SG_DB}"
    async with httpx.AsyncClient(timeout=10, auth=auth) as c:
        user = {"name": planner_id, "password": secrets.token_urlsafe(24)}
        # Collection-scoped channel grants (SG 3.x named collections).
        user["collection_access"] = {
            config.COUCHBASE_SCOPE: {col: {"admin_channels": [planner_channel(planner_id), plant_channel(plant_id), CATALOG_CHANNEL]}
                                     for col in ("products", "planners", "production_plans", "production_orders", "assistant",
                                                 "context_cache", "catalog")}}
        r = await c.put(f"{base}/_user/{planner_id}", json=user)
        if r.status_code >= 400:
            log.warning("SG user upsert failed: %s %s", r.status_code, r.text[:200])
            return None
        r = await c.post(f"{base}/_session", json={"name": planner_id, "ttl": config.SG_SESSION_TTL_SECONDS})
        if r.status_code >= 400:
            log.warning("SG session failed: %s %s", r.status_code, r.text[:200])
            return None
        s = r.json()
        return {"session_id": s.get("session_id"), "cookie_name": s.get("cookie_name", "SyncGatewaySession"),
                "expires": s.get("expires"), "db": config.SG_DB, "url": config.SG_PUBLIC_URL or None}
