"""Builds the production plan for one product.

Inputs are the Databricks demand-planning scores from the daily dump (the
demand forecast, the recommended build quantity, days-to-stockout per
material) plus live state the dump can't know about: production orders
released since (materials ordered, alerts cleared, budget committed, the
schedule changed), on-hand units at the serving warehouse, and what is left of
the product's material budget. The result is written to `plan::<product_id>`,
which Sync Gateway replicates to every planner in the plant - so the
Production order screen renders from Couchbase Lite, even in a press shop
with no Wi-Fi, and re-ranks itself on the next sync after every release.

A plan has three parts:

* **schedule** - what the line is scheduled to build this week next to what
  the model recommends, against the line's capacity (and whether that needs
  an overtime shift)
* **materials** - what to order, ranked by how soon each runs out, checked
  against warehouse stock and the material budget
* **tasks** - the open planning alerts to clear, then the run-readiness
  checklist for the kind of line the product runs on
"""
from __future__ import annotations

import math

from . import catalog as C
from .docs import product_id_doc, days_since, now_iso, plan_id, plant_channel
from .store import store

SEVERITY_ORDER = {"critical": 0, "major": 1, "minor": 2}
FORWARD_BUY_REASON = {
    "castings": "Foundry lead times are lengthening - buy ahead of the next price step",
    "metals": "Steel and aluminum index rising - forward-buy candidate",
    "resin": "Resin price index trending up",
    "electronics": "Component allocation tightening - secure supply now",
    "tooling": "Tool life trend on this line",
}


def _reason(sku: str, a: dict, rec: dict) -> str:
    w = a.get("usage", {}).get(sku, {})
    p = C.MATERIAL_BY_SKU[sku]
    out = []
    dso = days_since(w.get("last_ordered"))
    dts = (a["ml_scores"].get("days_to_stockout") or {}).get(sku)
    if dts is not None and dts <= 0:
        out.append("Predicted to run out now")
    elif dts is not None and dts <= 14:
        out.append(f"Runs out in about {dts} day{'s' if dts != 1 else ''}")
    elif dso is not None:
        out.append(f"Last ordered {dso} days ago")
    if dts is not None and p["lead_time_days"] > max(dts, 0):
        out.append(f"{p['lead_time_days']}-day lead time")
    if rec.get("reason"):
        out.append(rec["reason"])
    return " · ".join(out[:3])


def schedule_view(a: dict) -> dict:
    """This week's schedule for the product next to the model's recommended
    build, against the line's capacity. Overtime already approved is part of
    the capacity."""
    sc = a.get("schedule") or {}
    s = a["ml_scores"]
    rate = float(sc.get("units_per_hour") or 0)
    cap = int(sc.get("capacity_units") or 0)
    sched = int(sc.get("scheduled_units") or 0)
    rec = int(s.get("recommended_build_units") or sched)
    over = max(0, rec - cap)
    return {
        "week_of": sc.get("week_of"), "scheduled_units": sched, "recommended_units": rec, "capacity_units": cap,
        "delta_units": rec - sched, "units_per_hour": rate, "shifts": sc.get("shifts"),
        "overtime_needed": over > 0, "overtime_hours": min(16, math.ceil(over / rate)) if over and rate else 0,
        "overtime_hours_approved": sc.get("overtime_hours_approved", 0),
        "released": bool(sc.get("released_at")), "reason": s.get("build_reason"),
    }


def plan_tasks(a: dict) -> list[dict]:
    """Alert tasks from the product's open planning alerts, then the
    run-readiness checklist for its line type."""
    tasks = []
    for al in sorted(a.get("alerts", []), key=lambda x: SEVERITY_ORDER.get(x.get("severity"), 3)):
        tasks.append({"id": f"al-{al.get('alert_id')}", "kind": "alert", "severity": al.get("severity"),
                      "title": al.get("action") or f"Review and clear: {al.get('text')}",
                      "alert": al.get("text"), "est_min": 10 if al.get("severity") == "minor" else 20})
    for i, title in enumerate(C.LINE_TYPES.get(a["line_type"], {}).get("readiness", [])):
        tasks.append({"id": f"rd-{i + 1}", "kind": "readiness", "title": title, "est_min": 5})
    return tasks


def default_order_type(a: dict) -> str:
    """Expedite when the product has a major or critical alert open, a scheduled run otherwise."""
    if any(al.get("severity") in ("critical", "major") for al in a.get("alerts", [])):
        return C.ORDER_TYPES[1]
    return C.ORDER_TYPES[0]


def rank_materials(a: dict, on_hand_by_sku: dict) -> list[dict]:
    """The model's recommended materials for a product, scored and ranked.
    `selected` marks the ones the plan pre-fills on the production order."""
    s = a["ml_scores"]
    lines = []
    recent = {l["sku"] for o in a.get("order_history", [])[:1] for l in o.get("lines", [])
              if days_since(o.get("date")) is not None and days_since(o.get("date")) <= 3}
    standing = {so["sku"] for so in a.get("standing_orders", [])}
    for rec in s.get("recommended_materials", []):
        sku = rec["sku"]
        p = C.MATERIAL_BY_SKU.get(sku)
        if not p or sku in recent or sku in standing:
            continue  # just ordered on the last release, or already on a standing order
        qty = int(rec["qty"])
        unit = C.material_cost(sku)
        on_hand = on_hand_by_sku.get(sku, 0)
        cat_risk = s.get(C.CATEGORIES[p["category"]]["risk"], 0.5)
        score = float(rec.get("confidence", 0.5)) * (0.6 + 0.4 * cat_risk)
        dts = (s.get("days_to_stockout") or {}).get(sku)
        if dts is not None and dts <= 7:
            score *= 1.2
        lines.append({
            "sku": sku, "name": p["name"], "category": p["category"], "category_name": C.CATEGORIES[p["category"]]["name"],
            "uom": p["uom"], "spec": p["spec"], "qty": qty, "unit_cost": unit,
            "line_total": round(unit * qty, 2), "confidence": rec.get("confidence"), "reason": _reason(sku, a, rec),
            "on_hand_at_warehouse": on_hand, "availability": "in_stock" if on_hand >= qty else ("low" if on_hand > 0 else "backorder"),
            "days_to_stockout": dts, "lead_time_days": p["lead_time_days"], "long_lead": p["long_lead"],
            "score": round(score, 3), "selected": score >= 0.45,
        })
    lines.sort(key=lambda l: -l["score"])
    for i, l in enumerate(lines):
        l["rank"] = i + 1
    return lines


async def compute_production_plan(a: dict) -> dict:
    from .actions import auth_check, get_inventory  # avoid import cycle

    s = a["ml_scores"]
    inv, _ = await get_inventory(a["line"]["warehouse"])
    lines = rank_materials(a, inv["on_hand"])

    forward_buys = []
    for sku in s.get("forward_buy_skus", []):
        p = C.MATERIAL_BY_SKU.get(sku)
        if not p or any(l["sku"] == sku for l in lines):
            continue
        unit = C.material_cost(sku)
        forward_buys.append({"sku": sku, "name": p["name"], "category": p["category"], "uom": p["uom"], "spec": p["spec"],
                             "qty": 1, "unit_cost": unit, "line_total": unit, "lead_time_days": p["lead_time_days"],
                             "long_lead": p["long_lead"],
                             "reason": FORWARD_BUY_REASON.get(p["category"], "Second source qualified - products like this one carry it"),
                             "on_hand_at_warehouse": inv["on_hand"].get(sku, 0)})
    selected = [l for l in lines if l["selected"]]
    subtotal = round(sum(l["line_total"] for l in selected), 2)
    tasks = plan_tasks(a)
    d, iv = a.get("demand") or {}, a.get("inventory") or {}
    return {
        "lines": lines, "forward_buys": forward_buys[:3], "tasks": tasks,
        "order_type": default_order_type(a),
        "alerts": a.get("alerts", []),
        "schedule": schedule_view(a),
        "demand": {"last_4wk_units": d.get("last_4wk_units"), "next_4wk_units": s.get("forecast_next_4wk_units"),
                   "trend_pct": d.get("trend_pct"), "forecast_accuracy_pct": (a.get("kpis") or {}).get("forecast_accuracy_pct")},
        "inventory": {"on_hand_units": iv.get("on_hand_units"), "days_of_cover": iv.get("days_of_cover"),
                      "target_cover_days": iv.get("target_cover_days"), "safety_stock_units": iv.get("safety_stock_units"),
                      "recommended_safety_stock_units": s.get("recommended_safety_stock_units")},
        "plan_total_usd": subtotal,
        "est_minutes": sum(t["est_min"] for t in tasks),
        "long_lead": any(l["long_lead"] for l in selected),
        "auth": auth_check(a, subtotal),
        "briefing": briefing(a, lines),
    }


def briefing(a: dict, lines: list[dict]) -> list[str]:
    """What the planner should know before walking to the line."""
    s = a["ml_scores"]
    pts = []
    alerts = a.get("alerts", [])
    if alerts:
        worst = sorted(alerts, key=lambda x: SEVERITY_ORDER.get(x.get("severity"), 3))
        pts.append(f"{len(alerts)} open alert{'s' if len(alerts) > 1 else ''}: "
                   + "; ".join(f"{al['severity']} - {al['text']}" for al in worst[:2]))
    sv = schedule_view(a)
    if sv["scheduled_units"] and abs(sv["delta_units"]) >= 0.05 * sv["scheduled_units"]:
        pts.append(f"The model recommends building {sv['recommended_units']:,} units this week against {sv['scheduled_units']:,} scheduled "
                   f"({sv['delta_units']:+,})" + (f" - {sv['reason']}" if sv.get("reason") else ""))
    if sv["overtime_needed"]:
        pts.append(f"That is {sv['recommended_units'] - sv['capacity_units']:,} units over this week's capacity of {sv['capacity_units']:,} - "
                   f"about {sv['overtime_hours']} hours of overtime, which the Plant Manager has to approve")
    urgent = [l for l in lines if l.get("days_to_stockout") is not None and l["days_to_stockout"] <= 7]
    if urgent:
        pts.append("Running out: " + ", ".join(f"{l['name'].split(',')[0]} (~{max(0, l['days_to_stockout'])}d)" for l in urgent[:3]))
    c = a["auth"]
    if c.get("on_hold"):
        pts.append(f"Quality hold for {c.get('hold_days', 0)} days - {c.get('hold_reason') or 'see the line notes'}. "
                   "Quality has to release it before anything is scheduled.")
    elif c["committed_usd"] > 0.75 * c["budget_limit_usd"]:
        pts.append(f"${c['committed_usd']:,.0f} of the ${c['budget_limit_usd']:,.0f} quarterly material budget is committed - "
                   "a large material order may need a budget override")
    if s.get("stockout_risk", 0) >= 0.5:
        pts.append(f"Stockout risk {s['stockout_risk']:.0%} over the next 30 days - {a['service']['tier_name']}, "
                   f"{a['service'].get('fill_rate_target_pct'):g}% fill-rate target")
    if a["line"].get("changeover_required"):
        pts.append(f"{a['line'].get('name', 'The line')} needs a changeover before this run - about {a['line'].get('changeover_min', 45)} minutes")
    last = a["order_history"][0] if a.get("order_history") else None
    if last:
        pts.append(f"Last production order {last['production_order_id']} on {last['date']}: {last.get('order_type', 'scheduled run').lower()}, "
                   f"{last.get('run_qty', 0):,} units, ${last['total_usd']:,.2f} of materials"
                   + (" (some were on order)" if last.get("backordered") else ""))
    if a.get("standing_orders"):
        pts.append(f"{len(a['standing_orders'])} standing order{'s' if len(a['standing_orders']) > 1 else ''} in place")
    return pts[:6]


async def refresh_production_plan(product_id: str) -> dict | None:
    a = await store.get("products", product_id_doc(product_id))
    if not a:
        return None
    plan = await compute_production_plan(a)
    doc = {
        "type": "production_plan", "product_id": product_id, "product_name": a["product_name"], "plant_id": a["plant_id"],
        **plan,
        "model_version": a["ml_scores"].get("model_version"), "segment": a["ml_scores"].get("segment"),
        "stockout_risk": a["ml_scores"].get("stockout_risk"),
        "export_date": a.get("export_date"), "product_rev": a.get("rev", 0),
        "generated_at": now_iso(), "channels": [plant_channel(a["plant_id"])],
    }
    await store.upsert("production_plans", plan_id(product_id), doc)
    return doc
