"""Production-planning actions - the business logic behind every MCP tool.

These functions are what actually changes state (production orders, the
week's schedule, warehouse stock, material budget, standing orders, supplier
returns, overtime shifts, line checks). They are only ever reached through
`tool_gateway`, which routes them through the Couchbase Agent Operations
Manager when it is configured (RBAC, approvals, tracing, audit) and calls
them in-process otherwise. Every mutation bumps the product's `rev`, which is
what invalidates that product's context cache and re-ranks its production
plan.

Every tool takes the calling planner's `planner_id`. The worker fills it from
the production-order document, which Sync Gateway only accepted from that
signed-in planner, and the assistant fills it from the app token; a product
can only be planned by the planner whose plant it belongs to.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import uuid

from . import catalog as C
from .docs import (product_id_doc, days_since, inventory_id, money, now_iso, planner_id_doc, plant_channel,
                   today)
from .store import store


class ToolError(Exception):
    pass


def _h(*parts) -> int:
    return int(hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
async def load_product(product_id: str, planner_id: str | None = None) -> dict:
    a = await store.get("products", product_id_doc(product_id))
    if not a:
        raise ToolError(f"No product '{product_id}'")
    if planner_id:
        planner = await store.get("planners", planner_id_doc(planner_id))
        if not planner or planner.get("plant_id") != a.get("plant_id"):
            raise ToolError(f"{product_id} is not in your plant")
    return a


async def save_product(a: dict):
    a["rev"] = int(a.get("rev", 0)) + 1
    a["updated_at"] = now_iso()
    await store.upsert("products", product_id_doc(a["product_id"]), a)
    from .loader import mark_live  # late import: loader -> personalization -> actions
    await mark_live(a["product_id"])


async def after_change(product_id: str):
    # late imports: personalization/assistant import this module
    from . import assistant, personalization
    await personalization.refresh_production_plan(product_id)
    await assistant.invalidate_context(product_id)


async def get_inventory(warehouse: str) -> tuple[dict, int]:
    doc, cas = await store.get_with_cas("inventory", inventory_id(warehouse))
    if doc is None:
        await store.insert("inventory", inventory_id(warehouse),
                           {"type": "inventory", "warehouse": warehouse, "warehouse_name": C.WAREHOUSES[warehouse]["name"],
                            "on_hand": C.initial_inventory(warehouse), "created_at": now_iso()})
        doc, cas = await store.get_with_cas("inventory", inventory_id(warehouse))
    return doc, cas


async def allocate_inventory(warehouse: str, wants: dict[str, int]) -> dict[str, int]:
    """Take what is on hand for each SKU (CAS-protected). Returns what was
    actually issued per SKU; the rest goes on order with the supplier."""
    for _ in range(10):
        doc, cas = await get_inventory(warehouse)
        got = {}
        for sku, qty in wants.items():
            avail = max(0, int(doc["on_hand"].get(sku, 0)))
            got[sku] = min(avail, int(qty))
            doc["on_hand"][sku] = avail - got[sku]
        doc["updated_at"] = now_iso()
        if await store.replace_cas("inventory", inventory_id(warehouse), doc, cas):
            return got
    raise ToolError("Warehouse stock is busy - please try again")


def _line(sku: str, qty: int) -> dict:
    p = C.MATERIAL_BY_SKU.get(sku)
    if not p:
        raise ToolError(f"Unknown material '{sku}'")
    qty = int(qty)
    if qty < 1 or qty > 100:
        raise ToolError(f"Quantity for {p['name']} must be between 1 and 100")
    unit = C.material_cost(sku)
    return {"sku": sku, "name": p["name"], "category": p["category"], "uom": p["uom"], "qty": qty,
            "unit_cost": unit, "line_total": round(unit * qty, 2), "lead_time_days": p["lead_time_days"],
            "long_lead": p["long_lead"]}


def _normalize_lines(lines, allow_empty: bool = False) -> list[dict]:
    if not isinstance(lines, list) or (not lines and not allow_empty):
        raise ToolError("A production order needs at least one material line")
    merged: dict[str, int] = {}
    for ln in lines:
        if not isinstance(ln, dict) or not ln.get("sku"):
            raise ToolError("Each line needs a sku and qty")
        merged[ln["sku"]] = merged.get(ln["sku"], 0) + int(ln.get("qty") or 1)
    return [_line(sku, qty) for sku, qty in merged.items()]


def _totals(lines: list[dict]) -> dict:
    total = round(sum(l["line_total"] for l in lines), 2)
    return {"materials_usd": total, "total_usd": total, "max_lead_days": max((l["lead_time_days"] for l in lines), default=0),
            "long_lead": any(l["long_lead"] for l in lines)}


def auth_check(a: dict, total: float) -> dict:
    """Release authorization for a product: what is left of its material
    budget this quarter, and whether the product is on a quality hold."""
    c = a["auth"]
    available = round(c["budget_limit_usd"] + c.get("temporary_extension_usd", 0) - c["committed_usd"], 2)
    return {"budget_limit_usd": c["budget_limit_usd"], "committed_usd": c["committed_usd"],
            "temporary_extension_usd": c.get("temporary_extension_usd", 0), "available_usd": available,
            "on_hold": bool(c.get("on_hold")), "hold_days": c.get("hold_days", 0), "hold_reason": c.get("hold_reason"),
            "ok": not c.get("on_hold") and total <= available, "shortfall_usd": round(max(0, total - available), 2)}


def _business_days(days: int) -> str:
    d = today()
    while days:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            days -= 1
    return d.isoformat()


def _next_production_order_id(a: dict) -> str:
    return f"PRO-{today():%y%m%d}-{_h(a['product_id'], now_iso(), uuid.uuid4().hex) % 90000 + 10000}"


def _default_run_qty(a: dict) -> int:
    """What a release builds when the planner doesn't say: the model's
    recommended quantity, capped at what the line can make this week."""
    from .personalization import schedule_view
    sv = schedule_view(a)
    return max(0, min(sv["recommended_units"], sv["capacity_units"] or sv["recommended_units"]))


# --------------------------------------------------------------------------
# read-only tools
# --------------------------------------------------------------------------
async def get_product_context(planner_id: str, product_id: str) -> dict:
    from .personalization import schedule_view  # avoid import cycle

    a = await load_product(product_id, planner_id)
    last = a["order_history"][0] if a.get("order_history") else None
    d = a.get("demand") or {}
    return {
        "product_id": product_id, "product_name": a["product_name"], "product_code": a.get("product_code"),
        "line_type": a["line_type_name"], "program": a.get("program"), "contacts": a.get("contacts", []),
        "service": a["service"], "auth": auth_check(a, 0.0), "line": a["line"],
        "review": a["review"], "alerts": a.get("alerts", []), "coverage": a.get("coverage", []), "kpis": a.get("kpis", {}),
        "demand": {k: d.get(k) for k in ("last_4wk_units", "trend_pct", "open_orders_units", "drivers", "forecast_8wk")},
        "market": a.get("market", []), "inventory": a.get("inventory", {}), "schedule": schedule_view(a),
        "last_production_order": last, "days_since_last_production_order": days_since(last["date"]) if last else None,
        "spend": a.get("spend", {}), "standing_orders": a.get("standing_orders", []),
        "overtime_shifts": a.get("overtime_shifts", []),
        "segment": a["ml_scores"].get("segment"), "next_best_action": a["ml_scores"].get("next_best_action"),
        "stockout_risk": a["ml_scores"].get("stockout_risk"),
        "forecast_next_4wk_units": a["ml_scores"].get("forecast_next_4wk_units"),
    }


async def get_production_plan(planner_id: str, product_id: str) -> dict:
    """The demand-planning model's plan for this product: how much to build
    this week against the schedule and the line's capacity, the materials to
    order (checked against the serving warehouse's on-hand units and the
    product's material budget), and the alert and readiness tasks."""
    from .personalization import default_order_type, plan_tasks, rank_materials, schedule_view  # avoid import cycle

    a = await load_product(product_id, planner_id)
    inv, _ = await get_inventory(a["line"]["warehouse"])
    ranked = rank_materials(a, inv["on_hand"])
    lines = []
    # The same lines the plan document pre-fills on the phone, so the assistant and the screen agree.
    for s in (l for l in ranked if l["selected"]):
        ln = _line(s["sku"], s["qty"])
        w = a.get("usage", {}).get(s["sku"], {})
        ln.update({"confidence": s.get("confidence"), "reason": s.get("reason"),
                   "days_since_ordered": days_since(w.get("last_ordered")),
                   "days_to_stockout": a["ml_scores"].get("days_to_stockout", {}).get(s["sku"]),
                   "on_hand_at_warehouse": inv["on_hand"].get(s["sku"], 0)})
        lines.append(ln)
    tot = _totals(lines) if lines else {}
    iv = a.get("inventory") or {}
    return {"product_id": product_id, "product_name": a["product_name"], "tier": a["service"]["tier"],
            "order_type": default_order_type(a), "warehouse": a["line"]["warehouse"], "lines": lines, "totals": tot,
            "schedule": schedule_view(a),
            "inventory": {"on_hand_units": iv.get("on_hand_units"), "days_of_cover": iv.get("days_of_cover"),
                          "target_cover_days": iv.get("target_cover_days"), "safety_stock_units": iv.get("safety_stock_units"),
                          "recommended_safety_stock_units": a["ml_scores"].get("recommended_safety_stock_units")},
            "forecast_next_4wk_units": a["ml_scores"].get("forecast_next_4wk_units"),
            "also_recommended": [{"sku": l["sku"], "name": l["name"], "qty": l["qty"], "confidence": l["confidence"]} for l in ranked if not l["selected"]],
            "tasks": plan_tasks(a), "alerts": a.get("alerts", []),
            "auth": auth_check(a, tot.get("total_usd", 0.0)),
            "forward_buys": [dict(_line(s, 1), reason="Model forward-buy candidate") for s in a["ml_scores"].get("forward_buy_skus", [])
                             if s in C.MATERIAL_BY_SKU][:3],
            "model_version": a["ml_scores"].get("model_version")}


async def search_materials(planner_id: str, product_id: str, query: str = "", category: str = "") -> dict:
    a = await load_product(product_id, planner_id)
    inv, _ = await get_inventory(a["line"]["warehouse"])
    q = (query or "").lower().split()
    out = []
    for p in C.MATERIALS:
        if category and p["category"] != category:
            continue
        hay = f"{p['name']} {p['sku']} {' '.join(p['tags'])} {C.CATEGORIES[p['category']]['name']}".lower()
        score = sum(1 for w in q if w in hay)
        if q and not score:
            continue
        w = a.get("usage", {}).get(p["sku"])
        out.append({**_line(p["sku"], 1), "on_hand_at_warehouse": inv["on_hand"].get(p["sku"], 0),
                    "used_here": bool(w), "per_week": (w or {}).get("per_week"),
                    "last_ordered": (w or {}).get("last_ordered"), "score": score + (0.5 if w else 0)})
    out.sort(key=lambda r: (-r["score"], r["name"]))
    return {"product_id": product_id, "items": out[:15]}


async def quote_production_order(planner_id: str, product_id: str, lines: list, run_qty: int = 0) -> dict:
    from .personalization import schedule_view  # avoid import cycle

    a = await load_product(product_id, planner_id)
    ls = _normalize_lines(lines, allow_empty=True)
    tot = _totals(ls)
    inv, _ = await get_inventory(a["line"]["warehouse"])
    short = [{"sku": l["sku"], "name": l["name"], "requested": l["qty"], "on_hand": inv["on_hand"].get(l["sku"], 0)}
             for l in ls if inv["on_hand"].get(l["sku"], 0) < l["qty"]]
    sv = schedule_view(a)
    run = int(run_qty or _default_run_qty(a))
    return {"product_id": product_id, "lines": ls, "totals": tot, "auth": auth_check(a, tot["total_usd"]),
            "short": short, "warehouse": a["line"]["warehouse"], "run_qty": run, "schedule": sv,
            "over_capacity_units": max(0, run - sv["capacity_units"])}


# --------------------------------------------------------------------------
# mutating tools
# --------------------------------------------------------------------------
async def release_production_order(planner_id: str, product_id: str, lines: list | None = None, run_qty: int = 0,
                                   reference: str = "", order_type: str = "", notes: str = "",
                                   tasks_done: list | None = None, _override: bool = False) -> dict:
    from .personalization import default_order_type, schedule_view  # avoid import cycle

    a = await load_product(product_id, planner_id)
    run = int(run_qty or 0)
    ls = _normalize_lines(lines or [], allow_empty=run > 0)
    otype = order_type or default_order_type(a)
    if otype not in C.ORDER_TYPES:
        raise ToolError(f"Order type must be one of: {', '.join(C.ORDER_TYPES)}")
    if run < 0:
        raise ToolError("Build quantity can't be negative")
    run = run or _default_run_qty(a)
    tot = _totals(ls)
    auth = auth_check(a, tot["total_usd"])
    sv = schedule_view(a)
    if auth["on_hold"]:
        return {"ok": False, "status": "failed", "reason_code": "quality_hold", "auth": auth,
                "summary": f"{a['product_name']} is on a quality hold ({auth['hold_days']} days: "
                           f"{auth.get('hold_reason') or 'see the line notes'}). Quality has to release the hold "
                           "before a production order can be released."}
    if sv["capacity_units"] and run > sv["capacity_units"]:
        return {"ok": False, "status": "failed", "reason_code": "capacity_limit", "auth": auth, "schedule": sv,
                "summary": f"{run:,} units is {run - sv['capacity_units']:,} over what {a['line'].get('name', 'the line')} can build this week "
                           f"({sv['capacity_units']:,} units). Request an overtime shift for the Plant Manager to approve, "
                           "or release a smaller quantity."}
    if not auth["ok"] and not _override:
        return {"ok": False, "status": "failed", "reason_code": "budget_limit", "auth": auth, "totals": tot,
                "summary": f"This {money(tot['total_usd'])} material order exceeds {a['product_name']}'s remaining material budget "
                           f"({money(auth['available_usd'])} of a {money(auth['budget_limit_usd'])} quarterly limit). "
                           f"A budget override of {money(auth['shortfall_usd'])} needs the Plant Controller's approval."}

    warehouse = a["line"]["warehouse"]
    got = await allocate_inventory(warehouse, {l["sku"]: l["qty"] for l in ls}) if ls else {}
    backordered = []
    for l in ls:
        l["issued_qty"] = got[l["sku"]]
        l["backordered_qty"] = l["qty"] - got[l["sku"]]
        if l["backordered_qty"]:
            backordered.append({"sku": l["sku"], "name": l["name"], "qty": l["backordered_qty"],
                                "eta": (today() + dt.timedelta(days=3 + _h(warehouse, l["sku"]) % 7)).isoformat()})
    production_order_id = _next_production_order_id(a)
    status = "materials_pending" if backordered else "completed"
    done = [str(t)[:120] for t in (tasks_done or [])][:20]
    order = {"production_order_id": production_order_id, "date": today().isoformat(), "released_at": now_iso(), "status": status,
             "source": "planner", "planner_id": planner_id, "reference": (reference or "").strip()[:40],
             "order_type": otype, "run_qty": run, "week_of": sv.get("week_of"), "line": a["line"].get("name"),
             "warehouse": warehouse, "notes": (notes or "").strip()[:300],
             "follow_up_date": max(b["eta"] for b in backordered) if backordered else None,
             "lines": ls, "line_count": len(ls), **tot, "tasks_done": done, "backordered": backordered,
             "budget_override": _override}
    # live state on the product: the schedule, committed budget, history, usage clocks, cleared alerts
    sc = a.setdefault("schedule", {})
    previous = int(sc.get("scheduled_units") or 0)
    sc.update(scheduled_units=run, released_at=now_iso(), released_order_id=production_order_id)
    iv = a.get("inventory") or {}
    daily = (a["ml_scores"].get("forecast_next_4wk_units") or 0) / 28.0
    if daily and iv:
        iv["projected_cover_days"] = round((iv.get("on_hand_units", 0) + run) / daily, 1)
    a["auth"]["committed_usd"] = round(a["auth"]["committed_usd"] + tot["total_usd"], 2)
    if _override:
        a["auth"]["temporary_extension_usd"] = 0
        a["auth"]["last_override"] = {"production_order_id": production_order_id, "amount_usd": auth["shortfall_usd"], "at": now_iso()}
    a["order_history"] = ([order] + a.get("order_history", []))[:24]
    ordered_cats = set()
    for l in ls:
        if not l["issued_qty"]:
            continue
        ordered_cats.add(l["category"])
        w = a.setdefault("usage", {}).setdefault(l["sku"], {"per_week": float(l["qty"])})
        w["last_ordered"] = today().isoformat()
        w["last_qty"] = l["qty"]
    # Materials on the way clear the supply alerts in their category; building
    # what the model recommends clears the schedule alerts (demand spike, cover below target).
    meets_plan = run >= 0.95 * sv["recommended_units"]
    def _cleared(al):
        return al.get("category") in ordered_cats or (al.get("category") == "schedule" and meets_plan)
    cleared = [al for al in a.get("alerts", []) if _cleared(al)]
    a["alerts"] = [al for al in a.get("alerts", []) if not _cleared(al)]
    for cov in a.get("coverage", []):
        if cov.get("category") in ordered_cats:
            cov["health"] = max(int(cov.get("health", 80)), 92)
            cov["status"] = "ok"
    sp = a.setdefault("spend", {})
    sp["ytd_usd"] = round(sp.get("ytd_usd", 0) + tot["total_usd"], 2)
    sp["production_orders_last_12mo"] = sp.get("production_orders_last_12mo", 0) + 1
    await save_product(a)
    await after_change(product_id)
    mt = (f" {len(ls)} material line{'s' if len(ls) != 1 else ''}, {money(tot['total_usd'])} against the material budget, issued from "
          f"{C.WAREHOUSES[warehouse]['name']}.") if ls else " No materials ordered."
    bo = (f" {len(backordered)} material{'s' if len(backordered) > 1 else ''} on order with the supplier: "
          + "; ".join(f"{b['name'].split(',')[0]} x{b['qty']} (ETA {b['eta']})" for b in backordered)
          + ". Follow up when they land.") if backordered else ""
    al = f" {len(cleared)} alert{'s' if len(cleared) != 1 else ''} cleared." if cleared else ""
    tk = f" {len(done)} readiness check{'s' if len(done) != 1 else ''} recorded." if done else ""
    chg = f" (was {previous:,})" if previous and previous != run else ""
    return {"ok": True, "status": status, "cost_usd": tot["total_usd"], "item": order,
            "summary": f"Production order {production_order_id} released for {a['product_name']}: {otype.lower()}, "
                       f"{run:,} units on {a['line'].get('name', 'the line')} this week{chg}.{mt}{tk}{al}{bo}"}


async def request_budget_override(planner_id: str, product_id: str, lines: list, reason: str, run_qty: int = 0,
                                  reference: str = "", order_type: str = "", notes: str = "",
                                  tasks_done: list | None = None) -> dict:
    """Release a production order whose materials cost more than what is left
    of the product's material budget. Held for the Plant Controller's decision
    in AOM's approval tier before it can run; once approved the shortfall
    becomes a one-time budget extension and the order is released."""
    if not reason or len(reason.strip()) < 5:
        raise ToolError("A budget override needs a reason for the audit trail")
    a = await load_product(product_id, planner_id)
    ls = _normalize_lines(lines)
    auth = auth_check(a, _totals(ls)["total_usd"])
    if auth["on_hold"]:
        raise ToolError(f"{a['product_name']} is on a quality hold - an override can't release a hold")
    if auth["ok"]:
        # Nothing to override; just release it.
        return await release_production_order(planner_id, product_id, lines, run_qty, reference, order_type, notes, tasks_done)
    a["auth"]["temporary_extension_usd"] = auth["shortfall_usd"]
    a["auth"]["override_reason"] = reason.strip()[:300]
    await save_product(a)
    res = await release_production_order(planner_id, product_id, lines, run_qty, reference, order_type,
                                         notes or f"Budget override approved: {reason.strip()[:120]}", tasks_done, _override=True)
    if res.get("ok"):
        res["summary"] = (f"Budget override of {money(auth['shortfall_usd'])} approved by the Plant Controller. "
                          + res["summary"])
        res["item"]["budget_override_usd"] = auth["shortfall_usd"]
    else:
        # The release was refused for another reason (capacity): don't leave the extension behind.
        a = await load_product(product_id, planner_id)
        a["auth"]["temporary_extension_usd"] = 0
        await save_product(a)
    return res


async def set_standing_order(planner_id: str, product_id: str, sku: str, qty: int, cadence_weeks: int = 4) -> dict:
    a = await load_product(product_id, planner_id)
    ln = _line(sku, qty)
    if not C.MATERIAL_BY_SKU[sku]["kanban"]:
        raise ToolError("Standing orders are for kanban materials (resin, fasteners, packaging, consumables), not long-lead buys")
    cadence = int(cadence_weeks)
    if cadence not in (1, 2, 4, 8, 12, 26):
        raise ToolError("Cadence must be 1, 2, 4, 8, 12 or 26 weeks")
    so = {"sku": sku, "name": ln["name"], "qty": ln["qty"], "unit_cost": ln["unit_cost"], "cadence_weeks": cadence,
          "next_date": (today() + dt.timedelta(weeks=cadence)).isoformat(), "created_at": now_iso(), "created_by": planner_id}
    a["standing_orders"] = [s for s in a.get("standing_orders", []) if s["sku"] != sku] + [so]
    await save_product(a)
    await after_change(product_id)
    return {"ok": True, "status": "completed", "cost_usd": 0, "item": {**so, "kind": "standing_order"},
            "summary": f"Standing order set: {ln['qty']} x {ln['name'].split(',')[0]} every {cadence} week"
                       f"{'s' if cadence > 1 else ''} for {a['product_name']}, next on {so['next_date']}."}


async def create_supplier_return(planner_id: str, product_id: str, production_order_id: str, sku: str, qty: int, reason: str) -> dict:
    a = await load_product(product_id, planner_id)
    order = next((o for o in a.get("order_history", []) if o.get("production_order_id") == production_order_id), None)
    if not order:
        raise ToolError(f"Production order {production_order_id} isn't in {a['product_name']}'s recent history")
    if days_since(order["date"]) > 30:
        raise ToolError("Material can be returned within 30 days of being issued")
    line = next((l for l in order.get("lines", []) if l["sku"] == sku), None)
    if not line:
        raise ToolError(f"{sku} wasn't on production order {production_order_id}")
    qty = int(qty)
    if qty < 1 or qty > line["qty"]:
        raise ToolError(f"Return quantity must be between 1 and {line['qty']}")
    if not reason or len(reason.strip()) < 3:
        raise ToolError("A supplier return needs a reason")
    refund = round(line["unit_cost"] * qty, 2)
    ret = {"return_id": f"RTV-{_h(production_order_id, sku, now_iso()) % 900000 + 100000}", "production_order_id": production_order_id,
           "sku": sku, "name": line["name"], "qty": qty, "refund_usd": refund, "reason": reason.strip()[:200],
           "date": today().isoformat(), "return_by": _business_days(5), "kind": "supplier_return"}
    a["auth"]["committed_usd"] = round(max(0.0, a["auth"]["committed_usd"] - refund), 2)
    a["supplier_returns"] = ([ret] + a.get("supplier_returns", []))[:12]
    await save_product(a)
    await after_change(product_id)
    return {"ok": True, "status": "completed", "cost_usd": -refund, "item": ret,
            "summary": f"{ret['return_id']} created: {qty} x {line['name'].split(',')[0]} from {production_order_id}, "
                       f"{money(refund)} credited back to the material budget. Stage it at the warehouse dock by {ret['return_by']}."}


LINE_CHECK_OUTCOMES = ("on_plan", "plan_adjusted", "follow_up", "on_hold")


async def log_line_check(planner_id: str, product_id: str, outcome: str, notes: str = "", next_review_days: int = 0) -> dict:
    a = await load_product(product_id, planner_id)
    if outcome not in LINE_CHECK_OUTCOMES:
        raise ToolError(f"Outcome must be one of: {', '.join(LINE_CHECK_OUTCOMES)}")
    cadence = int(next_review_days or a["review"].get("cadence_days") or 7)
    a["review"]["last_review"] = today().isoformat()
    a["review"]["last_outcome"] = outcome
    a["review"]["next_review_date"] = (today() + dt.timedelta(days=cadence)).isoformat()
    a["review"]["notes"] = (notes or "").strip()[:300]
    await save_product(a)
    # Mark the stop on the planner's floor walk.
    planner = await store.get("planners", planner_id_doc(planner_id))
    if planner:
        for stop in planner.get("route", []):
            if stop["product_id"] == product_id:
                stop["status"] = "checked"
                stop["outcome"] = outcome
                stop["checked_at"] = now_iso()
        planner["rev"] = int(planner.get("rev", 0)) + 1
        planner["updated_at"] = now_iso()
        await store.upsert("planners", planner_id_doc(planner_id), planner)
    await after_change(product_id)
    item = {"kind": "line_check", "product_id": product_id, "outcome": outcome, "date": today().isoformat(),
            "next_review_date": a["review"]["next_review_date"], "notes": a["review"]["notes"]}
    return {"ok": True, "status": "completed", "cost_usd": 0, "item": item,
            "summary": f"Line check logged for {a['product_name']} ({outcome.replace('_', ' ')}). Next plan review {item['next_review_date']}."}


async def request_overtime_shift(planner_id: str, product_id: str, hours: int, reason: str) -> dict:
    """Extra capacity on the product's line this week (a Saturday shift or
    extended hours) so the schedule can meet the model's recommended build.
    Held for the Plant Manager's decision in AOM's approval tier before it is
    granted; once granted the line's weekly capacity goes up by the hours."""
    a = await load_product(product_id, planner_id)
    hrs = int(hours)
    if hrs < 1 or hrs > 16:
        raise ToolError("Overtime shifts are between 1 and 16 hours")
    if not reason or len(reason.strip()) < 5:
        raise ToolError("An overtime shift needs a reason for the audit trail")
    if a["auth"].get("on_hold"):
        raise ToolError(f"{a['product_name']} is on a quality hold - no overtime can be granted until it is released")
    sc = a.setdefault("schedule", {})
    rate = float(sc.get("units_per_hour") or 0)
    added = int(round(hrs * rate))
    sc["capacity_units"] = int(sc.get("capacity_units") or 0) + added
    sc["overtime_hours_approved"] = int(sc.get("overtime_hours_approved") or 0) + hrs
    item = {"kind": "overtime_shift", "shift_id": f"OT-{_h(product_id, now_iso()) % 900000 + 100000}", "hours": hrs,
            "added_units": added, "capacity_units": sc["capacity_units"], "line": a["line"].get("name"),
            "reason": reason.strip()[:200], "date": today().isoformat(), "granted_at": now_iso(), "requested_by": planner_id,
            "week_of": sc.get("week_of")}
    a["overtime_shifts"] = ([item] + a.get("overtime_shifts", []))[:12]
    await save_product(a)
    await after_change(product_id)
    return {"ok": True, "status": "completed", "cost_usd": 0, "item": item,
            "summary": f"Overtime shift {item['shift_id']} approved by the Plant Manager for {a['product_name']}: {hrs} hours on "
                       f"{a['line'].get('name', 'the line')}, +{added:,} units of capacity this week ({sc['capacity_units']:,} total). "
                       f"Reason recorded: {reason.strip()[:120]}"}


TOOLS = {
    "get_product_context": get_product_context,
    "get_production_plan": get_production_plan,
    "search_materials": search_materials,
    "quote_production_order": quote_production_order,
    "release_production_order": release_production_order,
    "request_budget_override": request_budget_override,
    "set_standing_order": set_standing_order,
    "create_supplier_return": create_supplier_return,
    "log_line_check": log_line_check,
    "request_overtime_shift": request_overtime_shift,
}
READ_ONLY_TOOLS = {"get_product_context", "get_production_plan", "search_materials", "quote_production_order"}
APPROVAL_TOOLS = ["request_budget_override", "request_overtime_shift"]


def plant_channels(a: dict) -> list[str]:
    return [plant_channel(a["plant_id"])]
