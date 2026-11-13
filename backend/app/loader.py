"""Daily Databricks export -> Couchbase + AOM Knowledge Base.

Run by the backend on startup and every DUMP_POLL_SECONDS (it only does work
when a newer `dt=YYYY-MM-DD/_SUCCESS` partition appears), or by hand:

    python -m app.loader [--dump-dir PATH] [--force] [--no-kb]

1. Stream every JSON Lines part file of the newest complete partition.
2. Upsert one `products` document per product-at-a-plant (all ~100MB in
   Couchbase mode). Live state that changed since the last export (production orders
   released, schedule changes, alerts cleared, budget committed, standing orders, line checks) is carried forward - in
   production that state would already be in the next Databricks export.
3. Build one `planners` document per production planner (plant, today's floor walk), index
   employee ids for login, publish the catalog, and build the production plan
   for every product due for a plan review soon.
4. Publish to the AOM Knowledge Base: the Production Planning Playbook, and one
   profile document per product due for a plan review within KB_WINDOW_DAYS (demo
   plants first), replacing yesterday's version of each. The assistant's
   AOM RAG Application is then re-scoped to the new playbook document.

Context caches need no sweep: every cached context records the export date it
was built from, so the new export makes them all stale at once.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import glob
import json
import logging
import os
import time

from . import catalog as C
from . import config, knowledge, personalization, rag_app
from .aom_client import AOMError, aom
from .docs import (CATALOG_CHANNEL, CATALOG_ID, STATE, product_id_doc, now_iso, planner_channel, planner_id_doc,
                   plant_channel)
from .store import store

log = logging.getLogger("mfg.loader")
PERSONAS = ["PLN1001", "PLN1002", "PLN1003"]            # demo planners (employee ids 204817, 318552, 427190)
PERSONA_PLANTS = ["PLT-MA-PEA", "PLT-NY-KGN", "PLT-TX-FTW"]
LIVE_FIELDS = ("order_history", "usage", "standing_orders", "supplier_returns", "overtime_shifts", "spend", "alerts", "coverage")
SUGGEST_WINDOW_DAYS = 14
STATUS = {"state": "idle"}
_TASKS: list = []  # keep a reference so background KB publishing is not garbage-collected


def latest_partition(dump_dir: str) -> str | None:
    parts = sorted(p for p in glob.glob(os.path.join(dump_dir, "dt=*")) if os.path.exists(os.path.join(p, "_SUCCESS")))
    return parts[-1] if parts else None


def read_rows(partition: str):
    for path in sorted(glob.glob(os.path.join(partition, "part-*.json"))):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    yield json.loads(line)


async def mark_live(product_id: str):
    """Called after an action - remembers which products have state newer than the export."""
    doc = await store.get("lookups", "live_products") or {"products": []}
    if product_id not in doc["products"]:
        doc["products"].append(product_id)
        await store.upsert("lookups", "live_products", doc)


def _stop(a: dict, plan: dict | None) -> dict:
    last = a["order_history"][0] if a.get("order_history") else None
    sched = personalization.schedule_view(a)
    return {"product_id": a["product_id"], "product_name": a["product_name"], "line_type": a["line_type_name"],
            "line": a["line"].get("name"), "building": a["line"].get("building"), "tier_name": a["service"].get("tier_name"),
            "scheduled_units": sched["scheduled_units"], "recommended_units": sched["recommended_units"],
            "overtime_needed": sched["overtime_needed"], "planned_time": a["review"].get("planned_time") or "09:00",
            "signal_note": a["review"].get("signal_note"), "contact": (a.get("contacts") or [{}])[0].get("name"),
            "product_code": a.get("product_code"), "plan_total_usd": (plan or {}).get("plan_total_usd"), "tier": a["service"]["tier"],
            "order_type": (plan or {}).get("order_type"), "alert_count": len(a.get("alerts", [])),
            "worst_alert": min((al.get("severity") for al in a.get("alerts", [])), key=lambda s: {"critical": 0, "major": 1, "minor": 2}.get(s, 3), default=None),
            "stockout_risk": a["ml_scores"].get("stockout_risk"),
            "last_order_date": last["date"] if last else None, "hold_flag": bool(a["auth"].get("on_hold")),
            "status": "checked" if a["review"].get("last_review") == a["review"].get("next_review_date") else "planned"}


async def build_planner(planner: dict, products: list[dict], today: dt.date) -> dict:
    prev = await store.get("planners", planner_id_doc(planner["planner_id"])) or {}
    due = [a for a in products if a["review"].get("next_review_date") == today.isoformat()]
    due.sort(key=lambda a: a["review"].get("planned_time") or "09:00")
    stops = []
    for a in due:
        plan = await store.get("production_plans", f"plan::{a['product_id']}")
        stop = _stop(a, plan)
        old = next((s for s in prev.get("route", []) if s["product_id"] == a["product_id"]), None)
        if old and prev.get("route_date") == today.isoformat() and old.get("status") == "checked":
            stop.update(status="checked", outcome=old.get("outcome"), checked_at=old.get("checked_at"))
        stops.append(stop)
    terr = C.PLANTS.get(planner["plant_id"], {})
    return {
        "type": "planner", **planner, "plant_name": terr.get("name", planner.get("plant_name")),
        "product_count": len(products), "route_date": today.isoformat(), "route": stops,
        "due_this_week": sum(1 for a in products if 0 <= (dt.date.fromisoformat(a["review"]["next_review_date"]) - today).days <= 6),
        "committed_usd": round(sum(a["auth"]["committed_usd"] for a in products), 2),
        "on_hold_products": sum(1 for a in products if a["auth"].get("on_hold")),
        "open_alerts": sum(len(a.get("alerts", [])) for a in products),
        "rev": int(prev.get("rev", 0)) + 1, "export_date": STATE.get("export_date"), "updated_at": now_iso(),
        "channels": [planner_channel(planner["planner_id"])],
    }


async def load(dump_dir: str | None = None, force=False, kb=True) -> dict:
    dump_dir = os.path.abspath(dump_dir or config.DUMP_DIR)
    part = latest_partition(dump_dir)
    if not part:
        log.warning("No complete Databricks partition (dt=*/_SUCCESS) under %s", dump_dir)
        STATUS.update(state="no_dump", dump_dir=dump_dir)
        return STATUS
    export_date = os.path.basename(part).split("=", 1)[1]
    state = await store.get("lookups", "load_state") or {}
    if state.get("export_date") == export_date and not force and state.get("driver") == store.driver and store.driver == "couchbase":
        STATE["export_date"] = export_date
        STATUS.update(state="loaded", **state)
        log.info("Export %s already loaded", export_date)
        pb = await store.get("lookups", "kbdoc::playbook") or {}
        if kb and config.KB_ENABLED and aom.admin_enabled and pb.get("export_date") != export_date:
            _TASKS.append(asyncio.create_task(publish_kb(export_date, await _kb_window())))
        elif kb and aom.admin_enabled and pb.get("document_id"):
            _TASKS.append(asyncio.create_task(rag_app.sync(pb["document_id"])))
        return STATUS

    t0 = time.time()
    STATE["export_date"] = export_date
    STATUS.update(state="loading", export_date=export_date, partition=part, started_at=now_iso())
    live = set((await store.get("lookups", "live_products") or {}).get("products", []))
    today = dt.date.fromisoformat(config.DEMO_TODAY or export_date)
    planners: dict[str, dict] = {}
    by_plant: dict[str, list[dict]] = {}
    batch: dict[str, dict] = {}
    rows = skipped = carried = 0
    suggest: list[tuple[int, str]] = []
    nbytes = sum(os.path.getsize(p) for p in glob.glob(os.path.join(part, "part-*.json")))

    for row in read_rows(part):
        aid = row["product_id"]
        tid = row["plant_id"]
        days_to_review = (dt.date.fromisoformat(row["review"]["next_review_date"]) - today).days
        if store.driver == "memory" and days_to_review > config.MEMORY_WINDOW_DAYS and tid not in PERSONA_PLANTS:
            skipped += 1
            continue
        doc = {**row, "type": "product", "rev": 0, "channels": [plant_channel(tid)], "loaded_at": now_iso()}
        if aid in live:
            prev = await store.get("products", product_id_doc(aid))
            if prev:
                for f in LIVE_FIELDS:
                    if f in prev:
                        doc[f] = prev[f]
                doc["auth"] = {**doc["auth"], **{k: prev["auth"][k] for k in ("committed_usd", "temporary_extension_usd",
                                                                                  "last_override") if k in prev["auth"]}}
                # This week's released schedule and approved overtime survive a reload; a new week starts from the export.
                if prev.get("schedule", {}).get("week_of") == doc.get("schedule", {}).get("week_of"):
                    doc["schedule"] = prev["schedule"]
                    if "inventory" in prev:
                        doc["inventory"] = prev["inventory"]
                for f in ("last_review", "next_review_date", "last_outcome", "notes"):
                    if f in prev.get("review", {}):
                        doc["review"][f] = prev["review"][f]
                doc["rev"] = int(prev.get("rev", 0)) + 1
                carried += 1
        batch[product_id_doc(aid)] = doc
        planner = row["assigned_planner"]
        planners.setdefault(planner["planner_id"], {**planner, "plant_id": tid, "plant_name": row["plant_name"]})
        by_plant.setdefault(tid, []).append(doc)
        if days_to_review <= SUGGEST_WINDOW_DAYS or tid in PERSONA_PLANTS:
            suggest.append((days_to_review, aid))
        rows += 1
        if len(batch) >= 1000:
            await store.upsert_many("products", batch)
            batch = {}
            STATUS["rows"] = rows
    await store.upsert_many("products", batch)

    cat = C.public_catalog()
    cat.update({"type": "catalog", "faq": [{"q": f["q"], "a": f["a"], "keywords": f["keywords"]} for f in knowledge.FAQ],
                "export_date": export_date, "channels": [CATALOG_CHANNEL], "updated_at": now_iso()})
    await store.upsert("catalog", CATALOG_ID, cat)

    for _, aid in sorted(suggest):
        await personalization.refresh_production_plan(aid)

    logins = {}
    for rid, planner in planners.items():
        rdoc = await build_planner(planner, by_plant.get(planner["plant_id"], []), today)
        await store.upsert("planners", planner_id_doc(rid), rdoc)
        logins[f"login::{planner['employee_id']}"] = {"type": "login_index", "planner_id": rid, "last_name": planner["last_name"].lower(),
                                                  "plant_id": planner["plant_id"]}
    await store.upsert_many("lookups", logins)

    summary = {"export_date": export_date, "partition": part, "rows": rows, "planners": len(planners), "plants": len(by_plant),
               "skipped_outside_window": skipped, "carried_live_state": carried, "bytes": nbytes,
               "production_plans_built": len(suggest), "load_seconds": round(time.time() - t0, 1),
               "driver": store.driver, "loaded_at": now_iso()}
    await store.upsert("lookups", "load_state", summary)
    STATUS.update(state="loaded", **summary)
    log.info("Loaded export %s: %s products (%s planners) in %.1fs", export_date, rows, len(planners), time.time() - t0)

    if kb and config.KB_ENABLED and aom.admin_enabled:
        _TASKS.append(asyncio.create_task(publish_kb(export_date, await _kb_window())))
    elif kb and config.KB_ENABLED:
        STATUS["kb"] = {"state": "skipped", "reason": "AOM_URL / AOM_ADMIN_PASSWORD not set"}
    return STATUS


async def refresh_planner(planner_id: str):
    """Rebuild a planner's floor-walk document (after a production order or line check changes a stop)."""
    planner = await store.get("planners", planner_id_doc(planner_id))
    if not planner:
        return None
    products = await store.sites_in_plant(planner["plant_id"])
    base = {k: planner[k] for k in ("planner_id", "employee_id", "first_name", "last_name", "email", "certifications", "plant_id", "plant_name") if k in planner}
    doc = await build_planner(base, products, dt.date.fromisoformat(config.DEMO_TODAY or STATE["export_date"]))
    await store.upsert("planners", planner_id_doc(planner_id), doc)
    return doc


async def _replace_kb_doc(key: str, title: str, content: str, metadata: dict):
    prev = await store.get("lookups", f"kbdoc::{key}")
    res = await aom.kb_ingest(title, content, metadata)
    new_id = res["document"]["document_id"]
    await store.upsert("lookups", f"kbdoc::{key}", {"type": "kb_index", "document_id": new_id, "title": title,
                                                    "export_date": metadata.get("export_date"), "at": now_iso()})
    if prev and prev.get("document_id") and prev["document_id"] != new_id:
        await aom.kb_delete(prev["document_id"])
    return new_id


async def publish_kb(export_date: str, product_ids: list[str]):
    t0 = time.time()
    STATUS["kb"] = {"state": "publishing", "total": len(product_ids) + 1, "done": 0, "errors": 0}
    try:
        playbook_id = await _replace_kb_doc("playbook", f"Production Planning Playbook {export_date}",
                                            knowledge.playbook_markdown(export_date),
                                            {"type": "playbook", "export_date": export_date, "pii": False})
        STATUS["kb"]["done"] += 1
        await rag_app.sync(playbook_id)
    except AOMError as exc:
        log.error("KB playbook publish failed: %s", exc)
        STATUS["kb"].update(state="error", error=str(exc)[:300])
        return
    sem = asyncio.Semaphore(4)

    async def one(aid):
        async with sem:
            a = await store.get("products", product_id_doc(aid))
            if not a:
                return
            try:
                await _replace_kb_doc(aid, f"Product profile {aid} {export_date}", knowledge.product_profile_markdown(a),
                                      {"type": "product_profile", "product_id": aid, "export_date": export_date,
                                       "plant_id": a["plant_id"], "pii": True})
                STATUS["kb"]["done"] += 1
            except AOMError as exc:
                STATUS["kb"]["errors"] += 1
                log.warning("KB publish for %s failed: %s", aid, exc)

    await asyncio.gather(*(one(a) for a in product_ids))
    STATUS["kb"].update(state="published", seconds=round(time.time() - t0, 1), export_date=export_date)
    log.info("Published %d KB documents to AOM in %.1fs", STATUS["kb"]["done"], time.time() - t0)


async def poll_forever():
    while True:
        await asyncio.sleep(config.DUMP_POLL_SECONDS)
        try:
            part = latest_partition(os.path.abspath(config.DUMP_DIR))
            if part and os.path.basename(part).split("=", 1)[1] != STATE.get("export_date"):
                log.info("New Databricks partition detected: %s", part)
                await load()
        except Exception as exc:  # noqa: BLE001
            log.warning("dump poll failed: %s", exc)


async def _kb_window() -> list[str]:
    """KB targets: today's stops in the demo plants first, then every
    product due for a plan review within KB_WINDOW_DAYS."""
    today = dt.date.fromisoformat(config.DEMO_TODAY or STATE["export_date"])
    end = (today + dt.timedelta(days=config.KB_WINDOW_DAYS)).isoformat()
    out: list[str] = []
    for rid in PERSONAS:
        planner = await store.get("planners", planner_id_doc(rid))
        out += [s["product_id"] for s in (planner or {}).get("route", []) if s["product_id"] not in out]
    if store.driver == "memory":
        rows = sorted((a["review"]["next_review_date"], a["product_id"]) for a in store.data["products"].values()
                      if today.isoformat() <= a["review"]["next_review_date"] <= end)
        out += [aid for _, aid in rows if aid not in out]
    else:
        rows = await store._query(
            f"SELECT RAW a.product_id FROM {store._ks('products')} a WHERE a.review.next_review_date >= $t "
            f"AND a.review.next_review_date <= $d ORDER BY a.review.next_review_date LIMIT $n",
            t=today.isoformat(), d=end, n=config.KB_PROFILE_LIMIT)
        out += [a for a in rows if a not in out]
    return out[: max(config.KB_PROFILE_LIMIT, len(out[:12]))]


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump-dir")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-kb", action="store_true")
    args = ap.parse_args()

    async def run():
        await store.connect()
        await load(args.dump_dir, force=args.force, kb=False)
        if not args.no_kb and config.KB_ENABLED and aom.admin_enabled:
            state = await store.get("lookups", "load_state")
            await publish_kb(state["export_date"], await _kb_window())
        print(json.dumps({k: v for k, v in STATUS.items()}, indent=2, default=str))

    asyncio.run(run())


if __name__ == "__main__":
    main()
