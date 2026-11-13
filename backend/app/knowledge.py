"""Knowledge Base documents published to AOM by the daily loader.

Two kinds:

* **Product profile** - one per product due for a plan review soon, rendered
  from that morning's Databricks row as plain prose. The assistant retrieves
  it with a `document_id`-scoped search, so a question about one product can
  only ever be grounded in *that* product's profile, never another plant's
  that happens to be semantically close.
* **Production Planning Playbook** - forecasting, scheduling, inventory,
  material-ordering and approval policy. Contains no product data, so answers
  grounded only in it are safe to share across planners through AOM's LLM
  cache.

FAQ entries are also synced to the phone inside the catalog document, so the
app can answer the common questions offline from Couchbase Lite.
"""
from __future__ import annotations

from . import catalog as C

FAQ = [
    {"q": "How does a budget override work?",
     "a": "Every product has a quarterly material budget. If the materials on a production order would take the product past "
          "what is left of it (the limit minus what is already committed), release it as a budget override with a reason. The "
          "Plant Controller reviews it in the Agent Operations Manager's Approvals page; once approved, the shortfall becomes a "
          "one-time extension and the order is released automatically. Products on a quality hold can't be overridden - the "
          "hold has to be released first.",
     "keywords": ["budget", "override", "limit", "over budget", "approval", "plant controller", "controller", "authorization",
                  "authorisation", "spend"]},
    {"q": "When do I need an overtime shift?",
     "a": "When the build you want to release is more than the line can make this week. Capacity is the line's rate times its "
          "scheduled hours; the app shows it next to the scheduled and recommended quantities. Request an overtime shift from "
          "the app with the hours (1 to 16) and a reason; the Plant Manager approves it in the Agent Operations Manager, the "
          "line's capacity for the week goes up by those hours and the larger build can be released. A release above capacity "
          "is refused until the shift is approved.",
     "keywords": ["overtime", "shift", "capacity", "saturday", "extra hours", "plant manager", "line rate", "over capacity",
                  "extended hours", "weekend"]},
    {"q": "What is the difference between a scheduled run and an expedite?",
     "a": "A scheduled run is the normal weekly release: the model's recommended build quantity, the readiness checklist for "
          "the line and the materials that are due. An expedite is driven by an open alert - a demand spike, a late supplier "
          "PO, cover below safety stock - and goes to the front of the line's sequence. The app builds one plan with both "
          "halves, alert tasks first, so a single release covers them. Rush orders and pilot runs are released the same way "
          "with their own order type.",
     "keywords": ["scheduled run", "expedite", "rush", "pilot", "order type", "difference", "routine", "priority", "sequence"]},
    {"q": "What happens when a material is not in stock?",
     "a": "The production order is still released. Material on hand at the plant's warehouse is issued and the rest goes on "
          "order with the supplier, with an ETA on the order card; the order shows as 'Materials on order' and a follow-up is "
          "planned for the ETA. The app shows warehouse on-hand units and the supplier lead time on every line so you can cut "
          "the quantity or pick an alternate before you release.",
     "keywords": ["out of stock", "not in stock", "in stock", "backorder", "available", "availability", "on hand", "alternate", "short", "shortage",
                  "on order", "warehouse", "lead time"]},
    {"q": "How is the demand forecast built?",
     "a": "Each night the demand-planning model in Databricks reads three years of sales history by product and customer, the "
          "open order book, promotions and price changes, and market signals such as distributor sell-through, end-market "
          "indexes and commodity prices. It produces a weekly forecast for the next eight weeks with a low and high band, and "
          "tracks its own accuracy per product over the last 30 days. The forecast and the drivers behind it land on the phone "
          "with the daily export.",
     "keywords": ["forecast", "demand", "sales history", "historical sales", "predict", "prediction", "accuracy", "trend",
                  "drivers", "seasonal", "seasonality"]},
    {"q": "How is safety stock set?",
     "a": "Safety stock covers the gap between forecast and reality. The model sets it per product from the service class's "
          "fill-rate target, the forecast error over the last 30 days and the replenishment lead time: a product with a 98% "
          "fill-rate target and a volatile forecast carries more than a steady C item. When demand is trending up or accuracy "
          "drops below 85% the model recommends raising it; the recommended level shows next to the current one and takes "
          "effect when Supply Chain accepts it in the planning system.",
     "keywords": ["safety stock", "buffer", "cover", "days of cover", "inventory level", "inventory levels", "fill rate",
                  "reorder point", "stock level"]},
    {"q": "How is the recommended build quantity worked out?",
     "a": "It is this week's forecast plus half of the gap between the days of finished-goods cover on hand and the product's "
          "target, rounded to the line's batch size. A product whose cover has fallen below target gets a larger build than "
          "its forecast alone; one that is over target gets a smaller one. The quantity is checked against the line's capacity "
          "for the week - anything above that needs an overtime shift.",
     "keywords": ["build quantity", "recommended build", "how many", "how much to build", "schedule", "factory schedule",
                  "run quantity", "batch", "this week"]},
    {"q": "How do supplier returns work?",
     "a": "Material that arrives damaged, out of spec or in the wrong revision can be returned within 30 days of being issued. "
          "Create the supplier return from the production order line; the cost is credited back to the product's material "
          "budget and the material has to be staged at the warehouse dock within five business days. Quality opens the "
          "supplier corrective action under the same return number.",
     "keywords": ["return", "returns", "supplier return", "rtv", "refund", "credit", "damaged", "wrong revision", "out of spec",
                  "reject", "send back"]},
    {"q": "How do standing orders work?",
     "a": "Any kanban material - resin, fasteners, packaging, consumables - can be put on a standing order for a product: a "
          "fixed quantity every 1, 2, 4, 8, 12 or 26 weeks. The order is raised automatically on its date and materials on a "
          "standing order are left out of the model's recommendations so they are never doubled up. Long-lead items such as "
          "castings on allocation and controller boards are ordered against the forecast instead.",
     "keywords": ["standing order", "standing", "recurring", "kanban", "cadence", "every week", "every month", "auto",
                  "automatic", "replenish", "interval"]},
    {"q": "How does the production plan get built?",
     "a": "Each night the demand-planning model in Databricks forecasts every product, recommends a build quantity and a "
          "safety stock, and predicts days-to-stockout for each material from its usage and open purchase orders, with a "
          "confidence per material. The daily export lands in Couchbase and syncs to the planner's phone, where it is checked "
          "against warehouse on-hand units, the line's capacity and the product's remaining material budget and combined with "
          "open alerts and the line's readiness checklist. Every order you release re-ranks the plan on the next sync.",
     "keywords": ["production plan", "plan", "recommended", "recommendation", "model", "why", "databricks", "optimize",
                  "optimise", "material ordering"]},
    {"q": "What can I do in the app with no signal?",
     "a": "Everything you need on the floor: open the product, read its forecast, inventory, alerts and material cover, walk "
          "the readiness checklist with the supervisor, set the build quantity, pick materials and release the production "
          "order. It is saved to Couchbase Lite on the phone as 'On device' and syncs through Sync Gateway the moment you have "
          "a connection - at the cell door, in the office or at the next line. Releases, materials on order and approvals come "
          "back to the same order card.",
     "keywords": ["signal", "offline", "no service", "connection", "wifi", "wi-fi", "sync", "press shop", "dead zone", "coverage"]},
    {"q": "What do the service classes mean?",
     "a": "The service class sets the fill rate a product has to hit and how much finished-goods cover it carries: C items 92% "
          "and about 20 days, B items 95% and 14 days, A items 98% and 10 days, and key-account products 99.5% and 7 days with "
          "penalties for a missed ship. The class also sets how often the plan is reviewed and decides which product gets the "
          "line when two compete for it.",
     "keywords": ["service class", "class", "tier", "abc", "a item", "b item", "c item", "key account", "fill rate target",
                  "priority"]},
    {"q": "What is a quality hold?",
     "a": "A product goes on quality hold when it can't be built or shipped until Quality signs off: a customer complaint in "
          "containment, an engineering change waiting for approval or an overdue first-article inspection. No production "
          "order can be released and no override or overtime can be granted until Quality releases the hold. Log the line "
          "check as 'on hold' with what you found so Quality can follow up.",
     "keywords": ["hold", "quality hold", "containment", "ppap", "first article", "engineering change", "blocked", "quarantine"]},
    {"q": "Which warehouse serves a plant?",
     "a": "Plants draw materials from the warehouse for their state: Northeast (Putnam, CT), Hudson Valley (Montgomery, NY), "
          "Mid-Atlantic (Hagerstown, MD), Southeast (Lakeland, FL), South Central (Arlington, TX), Midwest (Terre Haute, IN) "
          "and West (Ontario, CA). On-hand numbers in the app are that warehouse's.",
     "keywords": ["warehouse", "stores", "where do materials", "which warehouse", "distribution center"]},
    {"q": "What market signals does the model use?",
     "a": "Customer order intake against forecast, distributor sell-through, end-market indexes, and supply-side signals that "
          "move material cost and lead time: the steel and aluminum index, resin contract prices, foundry and semiconductor "
          "lead-time indexes and corrugated prices. A rising index makes the model recommend ordering ahead (a forward buy); a "
          "lengthening lead time pulls the order date in.",
     "keywords": ["market", "market trends", "signals", "index", "commodity", "steel", "price increase", "forward buy",
                  "supply chain"]},
    {"q": "What do I check before releasing a run?",
     "a": "Walk the line's readiness checklist with the supervisor: first-article or first-shot approval for the revision, "
          "tooling and fixtures staged, material kitted and certified, the test or inspection program at the current "
          "revision, and a crew trained for the part on the shift. Record what you checked on the production order. If the "
          "line needs a changeover first, the product screen shows how long it takes.",
     "keywords": ["readiness", "checklist", "before releasing", "before a run", "changeover", "first article", "tooling",
                  "release a run"]},
]


def playbook_markdown(export_date: str) -> str:
    lines = [f"# Production Planning Playbook (published {export_date})", "",
             "Forecasting, scheduling, inventory, material-ordering and approval knowledge for the Manufacturing Planning "
             "Assistant. Contains no product data.", ""]
    lines += ["## Frequently asked questions", ""]
    for f in FAQ:
        lines += [f"**{f['q']}** {f['a']}", ""]
    lines += ["## Service classes", ""]
    for t in C.SERVICE_TIERS:
        lines.append(f"- {t['name']} ({t['code']}): {t['fill_rate_target_pct']:g}% fill-rate target, about {t['cover_target_days']} "
                     f"days of finished-goods cover, plan reviewed every {t['review_cadence_days']} days. {t['blurb']}")
    lines += ["", "Order types: " + ", ".join(C.ORDER_TYPES) + ".", ""]
    lines += ["## Budget, capacity and approval policy", "",
              "Each product has a quarterly material budget. What is available is the limit plus any approved one-time "
              "extension, minus what is already committed. Production orders whose materials fit within it are released at "
              "once. Orders above it need a budget override approved by the Plant Controller; the approval creates a one-time "
              "extension for the shortfall. A build above the line's weekly capacity needs an overtime shift approved by the "
              "Plant Manager, between 1 and 16 hours. Products on a quality hold cannot be released, overridden or given "
              "overtime until Quality releases the hold.", ""]
    lines += ["## Run-readiness checklists", ""]
    for code, lt in C.LINE_TYPES.items():
        lines.append(f"### {lt['name']}")
        for t in lt["readiness"]:
            lines.append(f"- {t}")
        lines.append("")
    lines += ["## Materials", ""]
    for code, cat in C.CATEGORIES.items():
        items = [p for p in C.MATERIALS if p["category"] == code]
        if not items:
            continue
        lines.append(f"### {cat['name']}")
        for p in items:
            lines.append(f"- {p['name']} ({p['sku']}): ${p['unit_cost']:,.2f} per {p['uom']}, {p['spec']}; "
                         f"{p['lead_time_days']}-day supplier lead time"
                         + ("; long-lead, ordered against the forecast" if p["long_lead"] else "")
                         + ("; kanban item, can go on a standing order." if p["kanban"] else "; not a kanban item."))
        lines.append("")
    lines += ["## Materials warehouses", ""]
    for code, wh in C.WAREHOUSES.items():
        lines.append(f"- {wh['name']} ({code}), {wh['city']}: serves {', '.join(wh['states'])}.")
    lines += ["", "## Line types we plan", ""]
    for code, lt in C.LINE_TYPES.items():
        top = sorted(lt["usage"].items(), key=lambda kv: -kv[1])[:4]
        lines.append(f"- {lt['name']}: consumes " + ", ".join(C.MATERIAL_BY_SKU[s]["name"].split(",")[0] for s, _ in top) + ".")
    return "\n".join(lines)


def product_profile_markdown(a: dict) -> str:
    s = a["ml_scores"]
    c = a["auth"]
    d, iv, sc, k = a.get("demand") or {}, a.get("inventory") or {}, a.get("schedule") or {}, a.get("kpis") or {}
    hist = a.get("order_history", [])[:6]
    hist_txt = "; ".join(f"{o['production_order_id']} on {o['date']} {o.get('order_type', 'scheduled run').lower()} "
                         f"{o.get('run_qty', 0):,} units, ${o['total_usd']:,.2f} of materials "
                         f"({o.get('line_count', len(o.get('lines', [])))} lines, {o.get('source', 'planner')})" for o in hist) or "no production orders yet"
    usage = sorted(a.get("usage", {}).items(), key=lambda kv: -float(kv[1].get("per_week") or 0))[:8]
    usage_txt = "; ".join(f"{C.MATERIAL_BY_SKU[sku]['name'].split(',')[0]} ~{v.get('per_week', 0):g}/week, last ordered "
                          f"{v.get('last_ordered')}" for sku, v in usage if sku in C.MATERIAL_BY_SKU)
    rec = "; ".join(f"{int(l['qty'])} x {C.MATERIAL_BY_SKU[l['sku']]['name'].split(',')[0]} ({l.get('confidence', 0):.0%}"
                    + (f", ~{s.get('days_to_stockout', {}).get(l['sku'])}d to stockout" if s.get("days_to_stockout", {}).get(l["sku"]) is not None else "") + ")"
                    for l in s.get("recommended_materials", []) if l["sku"] in C.MATERIAL_BY_SKU) or "none"
    contacts = "; ".join(f"{x['name']} ({x['role']}, {x['phone']})" for x in a.get("contacts", []))
    program = (f" Part of the {a['program']['program_name']} ({a['program']['program_id']}, "
               f"{a['program'].get('products')} products).") if a.get("program") else ""
    sos = "; ".join(f"{so['qty']} x {so['name'].split(',')[0]} every {so['cadence_weeks']}w" for so in a.get("standing_orders", [])) or "none"
    alerts = "; ".join(f"{al['severity']} {al.get('code', '')} {al['text']} (raised {al.get('raised_at', '')[:10]})"
                       for al in a.get("alerts", [])) or "none"
    cover = "; ".join(f"{x['name']} {x.get('days_cover')} days ({x.get('status')})" for x in a.get("coverage", [])) or "not listed"
    hist12 = ", ".join(f"{h['month']} {h['units']:,}" for h in d.get("history_12mo", []))
    fc = ", ".join(f"{f['week_of'][5:]} {f['units']:,}" for f in d.get("forecast_8wk", []))
    market = "; ".join(f"{m['signal']} {m['direction']} {m.get('change_pct', 0):+g}% ({m.get('note')})" for m in a.get("market", [])) or "none"
    line = a["line"]
    changeover = (f"changeover needed before this product runs, about {line.get('changeover_min')} minutes"
                  if line.get("changeover_required") else "no changeover needed")
    return "\n".join([
        f"# Product profile {a['product_id']}: {a['product_name']}",
        f"Source: Databricks daily export {a['export_date']}, model {s.get('model_version')}.",
        "",
        "## Product",
        f"{a['product_name']} ({a.get('product_code')}) is built on {line.get('name')} ({a['line_type_name'].lower()}, {line.get('building')}) at the "
        f"{a['plant_name']}, drawing materials from {C.WAREHOUSES[line['warehouse']]['name']}.{program} Contacts: {contacts}. "
        f"Line: {line.get('shift_pattern')}, {changeover}. "
        f"Line notes: {line.get('notes') or 'none'}. Floor note: {a['review'].get('signal_note')}.",
        "",
        "## Service class and budget",
        f"{a['service']['tier_name']} service class, {a['service'].get('fill_rate_target_pct'):g}% fill-rate target, "
        f"{a['service'].get('cover_target_days')} days of cover, supply agreement {a['service'].get('agreement_id')} renews "
        f"{a['service'].get('renewal_date')}. Quarterly material budget ${c['budget_limit_usd']:,.0f}, committed ${c['committed_usd']:,.2f}"
        + (f", ON QUALITY HOLD for {c.get('hold_days', 0)} days ({c.get('hold_reason')})" if c.get("on_hold") else "") + ".",
        "",
        "## Demand and forecast",
        f"Sales last 12 months (units): {hist12}. Last four weeks {d.get('last_4wk_units', 0):,} units; forecast next four weeks "
        f"{s.get('forecast_next_4wk_units', 0):,} units ({d.get('trend_pct', 0):+g}%). Weekly forecast: {fc}. "
        f"Open orders {d.get('open_orders_units', 0):,} units. Drivers: {'; '.join(d.get('drivers', [])) or 'none'}. "
        f"Forecast accuracy last 30 days {k.get('forecast_accuracy_pct')}%.",
        f"Market signals: {market}.",
        "",
        "## Inventory and schedule",
        f"Finished goods on hand {iv.get('on_hand_units', 0):,} units, {iv.get('days_of_cover')} days of cover against a "
        f"{iv.get('target_cover_days')}-day target. Safety stock {iv.get('safety_stock_units', 0):,} units; the model recommends "
        f"{s.get('recommended_safety_stock_units', 0):,}.",
        f"Week of {sc.get('week_of')}: {sc.get('scheduled_units', 0):,} units scheduled, the model recommends {s.get('recommended_build_units', 0):,} "
        f"({s.get('build_reason')}). Line capacity {sc.get('capacity_units', 0):,} units this week at {sc.get('units_per_hour')} units an hour"
        + (f", including {sc.get('overtime_hours_approved')} hours of approved overtime" if sc.get("overtime_hours_approved") else "") + ".",
        f"Fill rate last 30 days {k.get('fill_rate_30d_pct')}%, schedule adherence {k.get('schedule_adherence_pct')}%, OEE {k.get('oee_pct')}%.",
        "",
        "## Alerts and material cover",
        f"Open alerts: {alerts}.",
        f"Material cover: {cover}.",
        "",
        "## Order history and usage",
        f"Material spend last 12 months ${a.get('spend', {}).get('last_12mo_usd', 0):,.0f} over "
        f"{a.get('spend', {}).get('production_orders_last_12mo', 0)} production orders, average ${a.get('spend', {}).get('avg_production_order_usd', 0):,.0f}. "
        f"Recent production orders: {hist_txt}.",
        f"Usage: {usage_txt}.",
        f"Standing orders: {sos}.",
        "",
        "## Plan review",
        f"Last reviewed {a['review'].get('last_review')}, every {a['review'].get('cadence_days')} days, next {a['review'].get('next_review_date')} "
        f"(planned {a['review'].get('planned_time')}). Notes: {a['review'].get('notes') or 'none'}.",
        "",
        "## Model recommendation",
        f"Segment: {s.get('segment')}. Next best action: {s.get('next_best_action')}. Stockout risk next 30 days {s.get('stockout_risk')}. "
        f"Predicted material cost ${s.get('predicted_materials_cost_usd', 0):,.0f}. Recommended materials: {rec}. "
        f"Forward-buy candidates: {', '.join(C.MATERIAL_BY_SKU[u]['name'].split(',')[0] for u in s.get('forward_buy_skus', []) if u in C.MATERIAL_BY_SKU) or 'none'}.",
    ])
