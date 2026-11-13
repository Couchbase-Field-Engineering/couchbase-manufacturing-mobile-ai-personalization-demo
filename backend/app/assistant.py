"""Manufacturing Planning Assistant - the agent behind the app's Assistant tab.

One turn:

1. **Classify** the question into an intent, and decide whether it is about
   *this product* (needs the product's data) or a *playbook* question anyone could
   ask ("how does a budget override work?", "how is safety stock set?").
2. **Playbook questions -> AOM RAG Application.** One call to
   `/v1/agent/rag/mfg-planning-assistant/query`. The app is scoped to today's
   Production Planning Playbook document; AOM runs retrieval behind its Context
   Cache and generation behind its LLM Cache, and counts the query on its RAG
   Applications page. If the app isn't registered (or AOM predates RAG
   Applications) the question takes the product path below without product data.
3. **Product questions -> Context Caching.** Look the context up in AOM's
   Context Cache (`/v1/context/get`, namespace
   `rag:mfg-planning-assistant:product-context`), then in the Couchbase
   `ctx::<product>::<intent>` document. On a miss, assemble it: AOM Knowledge
   Base retrieval scoped to this product's profile document and to the playbook,
   plus live facts from the governed MCP tools (production plan, open alerts, warehouse
   on-hand, budget). It is stored in AOM (`/v1/context/set`, with the real
   build time) and in Couchbase, keyed by the export date and the product
   revision, so the next daily load or any production order on the product
   invalidates it. The Couchbase copy carries the plant channel, so Sync
   Gateway puts it on the planner's phone too - which is what lets the
   app answer in a press shop with no Wi-Fi.
4. **Memory** - recall what planners have noted about this product before
   (AOM agent memory), live every turn.
5. **LLM Caching** - complete through AOM `/v1/llm/complete` in the
   `rag:mfg-planning-assistant:product` namespace (the `rag:<app>:` prefix is
   how AOM attributes the activity to the RAG application).
6. Suggest one-tap actions; the app turns a tap into an offline-first
   `production_orders` document.
"""
from __future__ import annotations

import logging
import re
import time
import uuid

from . import catalog as C
from . import config, knowledge, metrics, rag_app, tool_gateway
from .aom_client import AOMError, aom, current_trace
from .docs import (STATE, product_id_doc, chat_id, ctx_id, days_since, money, now_iso, planner_channel, planner_id_doc,
                   plant_channel)
from .store import store

log = logging.getLogger("mfg.assistant")

INTENTS = ["plan", "materials", "alerts", "overtime", "line", "returns", "budget", "history", "standing", "review",
           "forecast", "inventory", "general"]
# Context-cache keys: product context per intent, plus a playbook-only
# context per intent for generic questions (no product data in it).
CONTEXT_KEYS = INTENTS + [f"playbook_{i}" for i in INTENTS]

KEYWORDS = {
    "budget": ["budget", "override", "over budget", "limit", "spend", "authoriz", "authoris", "hold", "approval", "committed"],
    "returns": ["return", "supplier return", "rtv", "damaged", "wrong revision", "out of spec", "reject", "send back"],
    "standing": ["standing order", "standing", "recurring", "every week", "every month", "cadence", "interval", "kanban",
                 "auto-replen", "on a schedule"],
    "overtime": ["overtime", "over capacity", "capacity", "extra shift", "saturday", "weekend shift", "extended hours",
                 "plant manager"],
    "materials": ["in stock", "available", "availability", "on hand", "backorder", "on order", "alternate", "part number",
                  "sku", "how much is", "cost", "price", "warehouse", "lead time", "material", "castings", "resin"],
    "line": ["line", "changeover", "shift pattern", "supervisor", "contact", "who do i", "where is", "building", "cell", "press"],
    "alerts": ["alert", "alerts", "exception", "supplier delay", "late po", "demand spike", "why is", "coverage", "short on"],
    "history": ["history", "last production order", "last time", "previous", "ordered before", "usually", "how often",
                "what was released", "last run"],
    "review": ["line check", "review", "floor walk", "next review", "come back", "follow up", "log"],
    "forecast": ["forecast", "demand", "sales", "trend", "market", "signal", "customers", "next month", "next four weeks",
                 "seasonal", "drivers", "selling"],
    "inventory": ["inventory", "safety stock", "days of cover", "finished goods", "stock level", "stockout", "how much stock",
                  "cover do we"],
    "plan": ["production plan", "plan", "recommend", "what should", "what needs", "needs doing", "to do", "checklist",
             "readiness", "how many", "build", "schedule", "expedite", "release", "this week", "today", "about to run out",
             "running out", "run out"],
}
PRODUCT_RE = re.compile(r"\b(this|their|they|them|the product|this product|the line|this line|this item|this sku|here|we|our|my)\b", re.I)
PRODUCT_INTENTS = {"plan", "alerts", "history", "review", "forecast", "inventory"}

# Canonical retrieval query per intent: the cached context is per intent, so
# it is retrieved with a stable query rather than the wording of whichever
# question happened to miss the cache first.
INTENT_QUERY = {
    "plan": "recommended build quantity schedule capacity materials days to stockout readiness checklist alerts",
    "materials": "materials cost warehouse on hand lead time backorder alternate",
    "alerts": "open alerts supplier delay demand spike material cover",
    "overtime": "overtime shift line capacity recommended build Plant Manager approval",
    "line": "line shift pattern changeover supervisor contacts notes quality hold",
    "returns": "supplier return damaged out of spec credit warehouse dock",
    "budget": "material budget committed budget override Plant Controller approval quality hold",
    "history": "order history material spend last production order usage",
    "standing": "standing order kanban material cadence",
    "review": "plan review cadence next review line check notes",
    "forecast": "demand forecast sales history trend drivers market signals forecast accuracy",
    "inventory": "finished goods inventory days of cover safety stock stockout risk",
    "general": "frequently asked questions policy",
}

PERSONA = ("You are the Manufacturing Planning Assistant in a production planner's mobile app, helping them on the shop "
           "floor decide this week's plan for a product: how much to build, what inventory to carry and which materials to "
           "order. Be direct, concise (under 120 words) and specific: quantities, dates, part numbers, days of cover. Use "
           "only the facts in CONTEXT. Never invent stock, forecasts, alerts or production orders. Say when a build needs an "
           "overtime shift or a material order needs a budget override. Never say a production order is released - the "
           "planner confirms actions with the buttons shown under your reply.")


def classify(message: str) -> tuple[str, bool]:
    m = message.lower()
    scores = {i: sum(1 for k in kws if k in m) for i, kws in KEYWORDS.items()}
    intent = max(scores, key=scores.get)
    if scores[intent] == 0:
        intent = "general"
    about_product = bool(PRODUCT_RE.search(message)) or intent in PRODUCT_INTENTS
    if intent == "general" and not about_product:
        return "general", False
    # A policy or method question phrased generically ("how does a budget
    # override work?", "how is safety stock set?") stays on the shared
    # playbook path even though its intent is product-capable.
    if not PRODUCT_RE.search(message) and re.match(r"\s*(how|what is|what are|what's the|is|are|can|do|does|who|which|when do)\b", m):
        return intent, False
    return intent, True


def _norm(q: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", q.lower()).strip()


async def kb_doc_id(key: str) -> str | None:
    doc = await store.get("lookups", f"kbdoc::{key}")
    return doc.get("document_id") if doc else None


# --------------------------------------------------------------------------
# Context cache
# --------------------------------------------------------------------------
async def invalidate_context(product_id: str):
    for key in INTENTS:
        await store.remove("context_cache", ctx_id(product_id, key))


async def _kb(query: str, document_id: str | None, top_k=3) -> tuple[list[dict], str]:
    if not (aom.enabled and document_id):
        return [], "unavailable"
    try:
        res = await aom.knowledge_search(query, top_k=top_k, document_id=document_id)
        return [{"title": r.get("document_title"), "content": r.get("content"), "score": round(r.get("score") or 0, 3),
                 "document_id": r.get("document_id"), "source": "aom-kb"} for r in res.get("results", [])], "aom"
    except AOMError as exc:
        log.warning("KB search failed: %s", exc)
        return [], "error"


def _local_profile_chunks(product: dict, intent: str) -> list[dict]:
    md = knowledge.product_profile_markdown(product)
    sections = md.split("\n## ")
    want = {"plan": ["Model recommendation", "Inventory and schedule", "Alerts"], "materials": ["Order history", "Model recommendation"],
            "alerts": ["Alerts", "Model recommendation"], "overtime": ["Inventory and schedule", "Service class"],
            "line": ["Product", "Plan review"], "returns": ["Order history"], "budget": ["Service class", "Order history"],
            "history": ["Order history", "Plan review"], "standing": ["Order history"], "review": ["Plan review", "Product"],
            "forecast": ["Demand and forecast"], "inventory": ["Inventory and schedule", "Demand and forecast"],
            "general": ["Product"]}[intent]
    out = []
    for s in sections:
        if any(s.startswith(w) for w in want):
            out.append({"title": f"Product profile {product['product_id']}", "content": s[:1200], "score": None,
                        "document_id": None, "source": "local-dump"})
    return out


def _local_playbook_chunks(query: str) -> list[dict]:
    q = query.lower()
    scored = []
    for f in knowledge.FAQ:
        s = sum(1 for k in f["keywords"] if k in q)
        if s:
            scored.append((s, f))
    scored.sort(key=lambda x: -x[0])
    return [{"title": "Production Planning Playbook", "content": f"{f['q']} {f['a']}", "answer": f["a"], "score": None,
             "document_id": None, "source": "local-playbook"} for _, f in scored[:3]]


async def _facts(planner_id: str, product_id: str, intent: str, message: str) -> dict:
    if intent in ("plan", "materials", "alerts", "overtime", "standing", "inventory"):
        tool, args = "get_production_plan", {}
    else:
        tool, args = "get_product_context", {}
    res = await tool_gateway.invoke(tool, {"planner_id": planner_id, "product_id": product_id, **args})
    data = res.get("data", res)
    facts = {"tool": tool, "governed": res.get("governed", False), "trace_id": res.get("trace_id"), "data": data}
    if intent == "materials":
        # Also look the mentioned materials up in the catalog with this product's warehouse stock.
        stop = ("much", "does", "have", "they", "this", "what", "price", "stock", "cost", "product", "warehouse", "there", "here", "many", "material", "materials", "lead", "time")
        words = [w for w in re.findall(r"[a-z]{3,}", message.lower()) if w not in stop]
        if words:
            srch = await tool_gateway.invoke("search_materials", {"planner_id": planner_id, "product_id": product_id, "query": " ".join(words[:4])})
            facts["catalog"] = (srch.get("data", srch) or {}).get("items", [])[:5]
    return facts


def _aom_context_key(scope: str, cache_key: str, product: dict | None, personal: bool) -> str:
    """Key in AOM's Context Cache. The export date and (for product context)
    the product revision are part of the key, so a new daily export or any
    order makes the old entry unreachable at once. Playbook context holds no
    product data, so every planner shares one entry per intent."""
    if personal and product:
        return f"{scope}:{cache_key}:{product.get('export_date')}:r{product.get('rev', 0)}"
    return f"{cache_key}:{STATE.get('export_date')}"


def _local_valid(doc: dict | None, product: dict | None, personal: bool) -> bool:
    if not doc or doc.get("export_date") != STATE.get("export_date"):
        return False
    return not personal or (product is not None and doc.get("product_rev") == product.get("rev", 0))


async def get_context(planner_id: str, intent: str, product: dict | None, personal: bool, message: str = "") -> dict:
    """Context Caching, with AOM's Context Cache in front of the assembly.

    1. AOM `/v1/context/get` in the `rag:<app>:product-context` namespace, so
       hit rate and latency avoided show on AOM's Context Cache and RAG
       Applications pages.
    2. On a miss (or with AOM unreachable), the Couchbase `context_cache`
       document, if it was built from today's export and the current product.
    3. Otherwise assemble: KB retrieval scoped to this product's profile
       document and the playbook, plus live facts from the governed MCP
       tools. The result goes to AOM (`/v1/context/set` with the real build
       time as `source_latency_ms`) and to Couchbase, where the plant
       channel puts it on the planner's phone for offline answers.
    """
    personal = personal and product is not None
    cache_key = intent if personal else f"playbook_{intent}"
    scope = product["product_id"] if personal else planner_id
    key = ctx_id(scope, cache_key)
    channels = [plant_channel(product["plant_id"])] if personal else [planner_channel(planner_id)]
    aom_key = _aom_context_key(scope, cache_key, product, personal)
    t0 = time.perf_counter()
    aom_status = "off"
    if aom.enabled:
        try:
            res = await aom.context_get(aom_key, config.CONTEXT_NAMESPACE)
            aom_status = (res.get("cache") or {}).get("status") or ("hit" if res.get("hit") else "miss")
            value = res.get("value")
            if res.get("hit") and isinstance(value, dict):
                # A shared playbook entry may have been stored by another planner's
                # turn: re-home it before it syncs to this planner's phone.
                doc = dict(value, scope=scope, channels=channels)
                local = await store.get("context_cache", key)
                if not _local_valid(local, product, personal):
                    await store.upsert("context_cache", key, doc, ttl=config.CONTEXT_CACHE_TTL_SECONDS)
                doc["_cache"] = {"status": "hit", "source": "aom", "namespace": config.CONTEXT_NAMESPACE,
                                 "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}
                return doc
        except AOMError as exc:
            aom_status = "error"
            log.info("AOM context get failed: %s", exc)

    cached = await store.get("context_cache", key)
    if _local_valid(cached, product, personal):
        cached["_cache"] = {"status": "hit", "source": "couchbase", "aom": aom_status,
                            "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}
        if aom_status == "miss":
            await _aom_context_set(aom_key, cached, cached.get("build_ms"))
        return cached

    query = INTENT_QUERY[intent]
    product_doc = await kb_doc_id(product["product_id"]) if personal else None
    playbook_doc = await kb_doc_id("playbook")
    kb_profile, kb_mode = ([], "skipped")
    if personal and intent != "general":
        kb_profile, kb_mode = await _kb(query, product_doc)
        if not kb_profile:
            kb_profile = _local_profile_chunks(product, intent)
    kb_playbook, pb_mode = await _kb(query, playbook_doc)
    if not kb_playbook:
        kb_playbook = _local_playbook_chunks(query + " " + intent)
    if kb_mode == "skipped":
        kb_mode = pb_mode
    facts = await _facts(planner_id, product["product_id"], intent, message) if personal and intent != "general" else {}
    build_ms = round((time.perf_counter() - t0) * 1000, 1)
    doc = {
        "type": "context_cache", "scope": scope, "product_id": product["product_id"] if personal else None,
        "intent": intent, "personal": personal, "query": query,
        "export_date": STATE.get("export_date"), "product_rev": product.get("rev", 0) if personal else None,
        "built_at": now_iso(), "build_ms": build_ms, "kb_mode": kb_mode,
        "kb_profile": kb_profile, "kb_playbook": kb_playbook, "facts": facts,
        "product_document_id": product_doc, "playbook_document_id": playbook_doc,
        "aom_key": aom_key, "channels": channels,
    }
    await store.upsert("context_cache", key, doc, ttl=config.CONTEXT_CACHE_TTL_SECONDS)
    await _aom_context_set(aom_key, doc, build_ms)
    doc = dict(doc)
    doc["_cache"] = {"status": "miss", "source": "built", "aom": aom_status, "latency_ms": build_ms}
    return doc


async def _aom_context_set(aom_key: str, doc: dict, build_ms) -> None:
    if not aom.enabled:
        return
    value = {k: v for k, v in doc.items() if not k.startswith("_")}
    if not value.get("personal"):
        if value.get("kb_profile") or value.get("facts"):
            return  # never share product data across planners' playbook entries
        for k in ("scope", "channels", "product_document_id", "product_id"):
            value.pop(k, None)
    try:
        await aom.context_set(aom_key, value, config.CONTEXT_NAMESPACE, ttl_seconds=config.CONTEXT_CACHE_TTL_SECONDS,
                              source_latency_ms=int(build_ms or 0))
    except AOMError as exc:
        # 409 = context caching switched off in AOM's policy, 413 = over its size limit.
        log.info("AOM context set skipped: %s", exc)


# --------------------------------------------------------------------------
# Answer composition
# --------------------------------------------------------------------------
def _product_summary(a: dict) -> str:
    c = a["auth"]
    last = a["order_history"][0] if a.get("order_history") else None
    alerts = a.get("alerts", [])
    line, iv, sc, s = a["line"], a.get("inventory") or {}, a.get("schedule") or {}, a["ml_scores"]
    return (f"{a['product_name']} ({a.get('product_code')}), built on {line.get('name')} ({a['line_type_name']}) at the {a['plant_name']}, "
            f"{a['service']['tier_name']} service class, {a['service'].get('fill_rate_target_pct'):g}% fill-rate target. "
            f"Finished goods {iv.get('on_hand_units', 0):,} units, {iv.get('days_of_cover')} days of cover against a {iv.get('target_cover_days')}-day target. "
            f"This week {sc.get('scheduled_units', 0):,} units scheduled, model recommends {s.get('recommended_build_units', 0):,}, "
            f"line capacity {sc.get('capacity_units', 0):,}. "
            + (f"{len(alerts)} open alert(s): " + "; ".join(f"{al['severity']} {al['text']}" for al in alerts[:3]) + ". " if alerts else "No open alerts. ")
            + f"Quarterly material budget ${c['budget_limit_usd']:,.0f}, committed ${c['committed_usd']:,.2f}"
            + (f", ON QUALITY HOLD ({c.get('hold_reason')})" if c.get("on_hold") else "") + ". "
            + (f"Last production order {last['production_order_id']} on {last['date']}, {last.get('run_qty', 0):,} units, ${last['total_usd']:,.2f} of materials. " if last else "No recent production orders. ")
            + f"Materials from {C.WAREHOUSES[line['warehouse']]['name']}; {line.get('shift_pattern')}"
            + (f"; changeover needed, about {line.get('changeover_min')} min" if line.get("changeover_required") else "") + ".")


def _schedule_text(sv: dict) -> str:
    return (f"Schedule week of {sv.get('week_of')}: {sv.get('scheduled_units', 0):,} units scheduled, model recommends "
            f"{sv.get('recommended_units', 0):,} ({sv.get('reason') or 'forecast'}), line capacity {sv.get('capacity_units', 0):,} at "
            f"{sv.get('units_per_hour')} units/hour"
            + (f", needs about {sv.get('overtime_hours')} hours of overtime" if sv.get("overtime_needed") else ", fits within capacity")
            + (f", {sv.get('overtime_hours_approved')} overtime hours already approved" if sv.get("overtime_hours_approved") else ""))


def _facts_text(facts: dict) -> str:
    d = facts.get("data") or {}
    out = []
    if facts.get("tool") == "get_production_plan":
        if d.get("schedule"):
            out.append(_schedule_text(d["schedule"]))
        iv = d.get("inventory") or {}
        if iv:
            out.append(f"Inventory: {iv.get('on_hand_units')} units on hand, {iv.get('days_of_cover')} days of cover vs target "
                       f"{iv.get('target_cover_days')}; safety stock {iv.get('safety_stock_units')}, model recommends "
                       f"{iv.get('recommended_safety_stock_units')}; forecast next 4 weeks {d.get('forecast_next_4wk_units')} units")
        rows = [f"{l['qty']} x {l['name']} ({l['sku']}) at ${l['unit_cost']} = ${l['line_total']}, warehouse on hand {l['on_hand_at_warehouse']}"
                + (f", runs out in {l['days_to_stockout']}d" if l.get("days_to_stockout") is not None else "")
                + f", {l.get('lead_time_days')}-day lead time" + (", long-lead" if l.get("long_lead") else "")
                for l in d.get("lines", [])]
        if rows:
            out.append("Recommended materials: " + "; ".join(rows))
        tasks = d.get("tasks") or []
        al = [t["title"] for t in tasks if t.get("kind") == "alert"]
        rd = [t["title"] for t in tasks if t.get("kind") == "readiness"]
        if al:
            out.append("Alert tasks: " + "; ".join(al))
        if rd:
            out.append("Readiness checklist: " + "; ".join(rd))
        t = d.get("totals") or {}
        if t:
            out.append(f"Materials total ${t.get('total_usd')}, longest lead time {t.get('max_lead_days')} days")
        cr = d.get("auth") or {}
        if cr:
            out.append(f"Budget: ${cr.get('available_usd')} available of ${cr.get('budget_limit_usd')}"
                       + (f", shortfall ${cr.get('shortfall_usd')}" if cr.get("shortfall_usd") else "")
                       + (", ON QUALITY HOLD" if cr.get("on_hold") else ""))
    elif d:
        cr = d.get("auth") or {}
        out.append(f"Budget available ${cr.get('available_usd')} of ${cr.get('budget_limit_usd')}"
                   + (f", ON QUALITY HOLD {cr.get('hold_days')} days: {cr.get('hold_reason')}" if cr.get("on_hold") else ""))
        dm = d.get("demand") or {}
        if dm:
            out.append(f"Demand: {dm.get('last_4wk_units')} units in the last 4 weeks, forecast {d.get('forecast_next_4wk_units')} for the next 4 "
                       f"({dm.get('trend_pct')}%); open orders {dm.get('open_orders_units')}; drivers: {'; '.join(dm.get('drivers') or [])}")
        if d.get("market"):
            out.append("Market signals: " + "; ".join(f"{m['signal']} {m['direction']} {m.get('change_pct')}%" for m in d["market"]))
        if d.get("schedule"):
            out.append(_schedule_text(d["schedule"]))
        if d.get("last_production_order"):
            lo = d["last_production_order"]
            out.append(f"Last production order {lo['production_order_id']} {lo['date']} {lo.get('run_qty')} units, ${lo['total_usd']} of materials "
                       f"({lo.get('order_type')}, {len(lo.get('lines', []))} lines" + (", materials on order" if lo.get("backordered") else "") + ")")
        if d.get("standing_orders"):
            out.append("Standing orders: " + "; ".join(f"{s['qty']} x {s['name'].split(',')[0]} every {s['cadence_weeks']}w" for s in d["standing_orders"]))
        if d.get("review"):
            v = d["review"]
            out.append(f"Plan reviews: last {v.get('last_review')}, next {v.get('next_review_date')}, every {v.get('cadence_days')} days")
        if d.get("line"):
            ln = d["line"]
            out.append(f"Line: {ln.get('name')}, {ln.get('building')}, {ln.get('shift_pattern')}; {ln.get('notes') or 'no notes'}"
                       + (f"; changeover needed, about {ln.get('changeover_min')} min" if ln.get("changeover_required") else ""))
    for it in facts.get("catalog") or []:
        out.append(f"Catalog: {it['name']} ({it['sku']}) ${it['unit_cost']}, warehouse on hand {it['on_hand_at_warehouse']}, "
                   f"{it.get('lead_time_days')}-day lead time")
    return "\n".join(out)


def build_prompt(message: str, personal: bool, ctx: dict, product: dict | None, memories: list[str]) -> str:
    parts = [PERSONA, "", "CONTEXT:"]
    if personal and product:
        parts.append("Product: " + _product_summary(product))
        for c in ctx.get("kb_profile", [])[:3]:
            parts.append("Profile note: " + (c.get("content") or "")[:900])
        ft = _facts_text(ctx.get("facts") or {})
        if ft:
            parts.append(ft)
        for m in memories[:3]:
            parts.append("Earlier a planner noted: " + m[:300])
    for c in ctx.get("kb_playbook", [])[:3]:
        parts.append("Playbook: " + (c.get("content") or "")[:900])
    parts += ["", f"PLANNER QUESTION: {message.strip()}", "", "ANSWER:"]
    return "\n".join(parts)


def _s(n: int) -> str:
    return "" if n == 1 else "s"


def compose_fallback(intent: str, personal: bool, message: str, ctx: dict, product: dict | None) -> str:
    """Deterministic answer from the same context - used when AOM has no LLM
    provider key (it returns a labelled stub) or AOM is not configured."""
    d = (ctx.get("facts") or {}).get("data") or {}
    if not personal or not product or intent == "general":
        pb = _local_playbook_chunks(message) or ctx.get("kb_playbook")
        if pb:
            return pb[0].get("answer") or pb[0]["content"]
        return ("I can help with this week's plan for the product you have open: the demand forecast, the build quantity, "
                "inventory and safety stock, materials and warehouse stock, alerts, overtime, budget and order history.")
    name = product["product_name"]
    line_name = product["line"].get("name", "the line")
    warehouse = C.WAREHOUSES[product["line"]["warehouse"]]["name"]
    sv = d.get("schedule") or {}
    if intent in ("plan", "standing"):
        lines = d.get("lines", [])
        tasks = d.get("tasks", [])
        al = [t for t in tasks if t.get("kind") == "alert"]
        rd = [t for t in tasks if t.get("kind") == "readiness"]
        rec, sched = sv.get("recommended_units", 0), sv.get("scheduled_units", 0)
        changed = bool(sched) and abs(rec - sched) >= 0.05 * sched
        if not lines and not al and not changed:
            return (f"Nothing needs changing for {name}: the {sched:,} units scheduled match the forecast, no material is close "
                    f"to running out and there are no open alerts - walk the {len(rd)}-step readiness checklist and log the line check.")
        urgent = [l for l in lines if l.get("days_to_stockout") is not None and l["days_to_stockout"] <= 7]
        short = [l for l in lines if l["on_hand_at_warehouse"] < l["qty"]]
        t = d.get("totals") or {}
        txt = f"{d.get('order_type', 'Scheduled run')} for {name}: build {rec:,} units this week"
        txt += f" ({rec - sched:+,} against the {sched:,} scheduled)." if changed else ", as scheduled."
        if sv.get("overtime_needed"):
            txt += (f" That is over {line_name}'s capacity of {sv.get('capacity_units', 0):,} - about {sv.get('overtime_hours')} hours of "
                    "overtime, which the Plant Manager has to approve first.")
        if al:
            txt += f" {len(al)} alert{_s(len(al))} to clear first ({al[0]['title'][0].lower() + al[0]['title'][1:]}), then"
            txt += f" the {len(rd)}-step readiness checklist."
        else:
            txt += f" Walk the {len(rd)}-step readiness checklist."
        if lines:
            txt += f" The model recommends ordering {len(lines)} material line{_s(len(lines))}, {money(t.get('total_usd', 0))}."
        if urgent:
            txt += " Start with " + ", ".join(f"{l['name'].split(',')[0]} x{l['qty']} (runs out in ~{max(0, l['days_to_stockout'])}d, "
                                              f"{l.get('lead_time_days')}-day lead time)" for l in urgent[:2]) + "."
        if short:
            txt += " Heads up: " + ", ".join(f"only {l['on_hand_at_warehouse']} {l['name'].split(',')[0]} at the warehouse" for l in short[:2]) + "."
        cr = d.get("auth") or {}
        if cr.get("on_hold"):
            txt += f" The product is on a quality hold ({cr.get('hold_reason')}) - nothing can be released until Quality lifts it."
        elif cr and not cr.get("ok"):
            txt += (f" That is {money(cr.get('shortfall_usd', 0))} over the product's remaining material budget - release it as a "
                    "budget override and the Plant Controller will approve it.")
        if intent == "standing" and lines:
            top = next((l for l in lines if C.MATERIAL_BY_SKU[l["sku"]]["kanban"]), lines[0])
            txt += (f" {top['name'].split(',')[0]} is a kanban item here - a standing order every 4 weeks "
                    "would keep it off the recommendation list.")
        return txt
    if intent == "alerts":
        alerts = d.get("alerts", [])
        if not alerts:
            return f"{name} has no open alerts as of this morning's export."
        return (f"{name} has {len(alerts)} open alert{_s(len(alerts))}: "
                + "; ".join(f"{al['severity']} - {al['text']}" + (f" ({al['action']})" if al.get("action") else "") for al in alerts[:3]) + ".")
    if intent == "materials":
        items = (ctx.get("facts") or {}).get("catalog") or []
        if items:
            it = items[0]
            return (f"{it['name']} ({it['sku']}) is {money(it['unit_cost'])} per {it['uom']}; {it['on_hand_at_warehouse']} on hand at {warehouse}, "
                    f"{it.get('lead_time_days')}-day supplier lead time."
                    + (f" Last ordered for this product {it['last_ordered']}." if it.get("last_ordered") else "")
                    + (" It is long-lead - order against the forecast, not the reorder point." if it.get("long_lead") else ""))
        lines = d.get("lines", [])
        if lines:
            return (f"Materials recommended for {name}, with stock at {warehouse}: "
                    + "; ".join(f"{l['qty']} x {l['name'].split(',')[0]} ({l['on_hand_at_warehouse']} on hand)" for l in lines[:4]) + ".")
        return f"{name} draws materials from {warehouse}. Open the production order to search the catalog with live warehouse stock."
    if intent == "overtime":
        rec, cap = sv.get("recommended_units", 0), sv.get("capacity_units", 0)
        if sv.get("overtime_needed"):
            return (f"Yes - the model recommends building {rec:,} units and {line_name} can make {cap:,} this week, so you are "
                    f"{rec - cap:,} short: about {sv.get('overtime_hours')} hours of overtime at {sv.get('units_per_hour'):g} units an hour. "
                    f"Request the shift below; the Plant Manager approves it and the week's capacity goes up. {name} is a "
                    f"{product['service']['tier_name'].lower()} product with a {product['service'].get('fill_rate_target_pct'):g}% fill-rate target.")
        return (f"No - the recommended build of {rec:,} units fits within this week's capacity of {cap:,} on {line_name}"
                + (f", which already includes {sv.get('overtime_hours_approved')} hours of approved overtime" if sv.get("overtime_hours_approved") else "")
                + ". If you want to build more than that, request a shift first.")
    if intent == "budget":
        cr = d.get("auth") or {}
        if cr.get("on_hold"):
            return (f"{name} is on a quality hold for {cr.get('hold_days')} days: {cr.get('hold_reason')}. No production order can be "
                    "released and an override can't lift a hold - log the line check as on hold and Quality will follow up.")
        return (f"{name} has {money(cr.get('available_usd', 0))} left this quarter: a {money(cr.get('budget_limit_usd', 0))} "
                f"material budget with {money(cr.get('committed_usd', 0))} committed. Material orders above that need a budget "
                "override approved by the Plant Controller.")
    if intent == "line":
        ln = product["line"]
        cr = d.get("auth") or {}
        sup = next((k for k in product.get("contacts", []) if k.get("role") == "Line Supervisor"), None)
        return (f"{name} runs on {ln.get('name')} in {ln.get('building')}: {ln.get('shift_pattern')}"
                + (f", changeover needed before this run (about {ln.get('changeover_min')} minutes)" if ln.get("changeover_required") else ", no changeover needed")
                + "." + (f" Supervisor: {sup['name']}, {sup['phone']}." if sup else "")
                + (f" Note: {ln['notes']}." if ln.get("notes") else "")
                + f" Floor note: {product['review'].get('signal_note')}."
                + (f" The product is on a quality hold: {cr.get('hold_reason')}." if cr.get("on_hold") else ""))
    if intent == "returns":
        lo = d.get("last_production_order")
        if lo:
            return (f"The last production order for this product, {lo['production_order_id']} ({lo['date']}), is within the 30-day window - "
                    "pick the material and quantity and I'll create the supplier return; the cost goes back on the material budget "
                    "and the material is due at the warehouse dock in five business days.")
        return "Material can be returned within 30 days of being issued; the cost is credited back to the material budget."
    if intent == "history":
        sp = d.get("spend") or {}
        lo = d.get("last_production_order")
        return (f"{name}: {money(sp.get('last_12mo_usd', 0))} of materials over {sp.get('production_orders_last_12mo', 0)} production orders in "
                f"the last 12 months, average {money(sp.get('avg_production_order_usd', 0))}."
                + (f" Last production order {lo['production_order_id']} on {lo['date']}: {str(lo.get('order_type', 'scheduled run')).lower()}, "
                   f"{lo.get('run_qty', 0):,} units, {money(lo['total_usd'])} of materials." if lo else ""))
    if intent == "review":
        v = d.get("review") or {}
        return (f"Plan last reviewed {v.get('last_review')}; this product is on a {v.get('cadence_days')}-day cadence, next due {v.get('next_review_date')}. "
                "Log the line check below and I'll schedule the next one.")
    if intent == "forecast":
        dm = d.get("demand") or {}
        mk = [m for m in d.get("market") or [] if m.get("category") != "demand"][:2]
        acc = (d.get("kpis") or {}).get("forecast_accuracy_pct")
        txt = (f"{name}: {dm.get('last_4wk_units', 0):,} units sold in the last four weeks; the model forecasts "
               f"{d.get('forecast_next_4wk_units', 0):,} for the next four ({dm.get('trend_pct', 0):+g}%).")
        if dm.get("drivers"):
            txt += " Drivers: " + "; ".join(dm["drivers"][:2]) + "."
        if mk:
            txt += " Market signals: " + "; ".join(f"{m['signal'].lower()} {m['direction']} {abs(m.get('change_pct', 0)):g}%" for m in mk) + "."
        if acc:
            txt += f" Forecast accuracy over the last 30 days is {acc:g}%."
        if sv:
            txt += f" It recommends building {sv.get('recommended_units', 0):,} units this week against {sv.get('scheduled_units', 0):,} scheduled."
        return txt
    if intent == "inventory":
        iv = d.get("inventory") or {}
        txt = (f"{name} has {iv.get('on_hand_units', 0):,} units of finished goods on hand - {iv.get('days_of_cover')} days of cover "
               f"against a {iv.get('target_cover_days')}-day target.")
        ss, rss = iv.get("safety_stock_units") or 0, iv.get("recommended_safety_stock_units") or 0
        if rss and rss != ss:
            txt += f" Safety stock is {ss:,} units; the model recommends {'raising' if rss > ss else 'lowering'} it to {rss:,}."
        elif ss:
            txt += f" Safety stock is {ss:,} units, which the model would keep."
        if sv:
            txt += (f" To get back to target it recommends building {sv.get('recommended_units', 0):,} units this week "
                    f"({sv.get('scheduled_units', 0):,} are scheduled).")
        return txt
    return "Here's what I found for this product."


def suggest_actions(intent: str, ctx: dict, product: dict | None, message: str) -> list[dict]:
    if not product:
        return []
    d = (ctx.get("facts") or {}).get("data") or {}
    acts: list[dict] = []
    aid = product["product_id"]
    sv = d.get("schedule") or {}
    cr = d.get("auth") or {}
    hold = {"label": "Log line check · on hold", "tool": "log_line_check",
            "args": {"product_id": aid, "outcome": "on_hold", "notes": "Quality hold - nothing released"}}

    def overtime():
        over = sv.get("recommended_units", 0) - sv.get("capacity_units", 0)
        return {"label": f"Request {sv.get('overtime_hours')}-hour overtime shift", "tool": "request_overtime_shift",
                "args": {"product_id": aid, "hours": sv.get("overtime_hours"),
                         "reason": f"Recommended build of {sv.get('recommended_units', 0):,} units is {over:,} over this week's capacity on "
                                   f"{product['line'].get('name', 'the line')}"}}

    if intent in ("plan", "materials", "alerts", "inventory"):
        lines = d.get("lines", [])
        run = min(sv.get("recommended_units", 0), sv.get("capacity_units", 0) or sv.get("recommended_units", 0))
        if (lines or run) and not cr.get("on_hold"):
            payload = [{"sku": l["sku"], "qty": l["qty"]} for l in lines]
            t = d.get("totals") or {}
            base = {"product_id": aid, "lines": payload, "run_qty": run, "order_type": d.get("order_type") or ""}
            if lines and cr and not cr.get("ok"):
                acts.append({"label": f"Release as budget override · {money(t.get('total_usd', 0))}", "tool": "request_budget_override",
                             "args": {**base, "reason": f"Recommended materials exceed the remaining material budget by {money(cr.get('shortfall_usd', 0))}; "
                                                        "stockout predicted inside the supplier lead time"}})
            else:
                acts.append({"label": f"Release recommended plan · {run:,} units · {money(t.get('total_usd', 0))}", "tool": "release_production_order",
                             "args": base})
        if cr.get("on_hold"):
            acts.append(hold)
        else:
            if sv.get("overtime_needed"):
                acts.append(overtime())
            acts.append({"label": "Review in Production order", "screen": "order", "args": {"product_id": aid}})
    elif intent == "overtime":
        if sv.get("overtime_needed") and not cr.get("on_hold"):
            acts.append(overtime())
        acts.append({"label": "Open Production order", "screen": "order", "args": {"product_id": aid}})
    elif intent == "budget":
        if cr.get("on_hold"):
            acts.append(hold)
        else:
            acts.append({"label": "Open Production order", "screen": "order", "args": {"product_id": aid}})
    elif intent == "standing":
        lines = [l for l in d.get("lines", []) if C.MATERIAL_BY_SKU.get(l["sku"], {}).get("kanban")]
        if lines:
            top = lines[0]
            acts.append({"label": f"Standing order · {top['qty']} x {top['name'].split(',')[0]} / 4 wks", "tool": "set_standing_order",
                         "args": {"product_id": aid, "sku": top["sku"], "qty": top["qty"], "cadence_weeks": 4}})
    elif intent == "returns":
        lo = d.get("last_production_order")
        if lo and lo.get("lines"):
            l0 = lo["lines"][0]
            acts.append({"label": f"Return 1 x {l0['name'].split(',')[0]} from {lo['production_order_id']}", "tool": "create_supplier_return",
                         "args": {"product_id": aid, "production_order_id": lo["production_order_id"], "sku": l0["sku"], "qty": 1,
                                  "reason": f"Planner-reported via assistant: {message.strip()[:120]}"}})
    elif intent == "review":
        acts.append({"label": "Log line check · on plan", "tool": "log_line_check", "args": {"product_id": aid, "outcome": "on_plan"}})
        acts.append({"label": "Log line check · follow up", "tool": "log_line_check", "args": {"product_id": aid, "outcome": "follow_up"}})
    elif intent in ("history", "line", "forecast"):
        acts.append({"label": "Open Production order", "screen": "order", "args": {"product_id": aid}})
    return acts


# Only durable things the planner notes about the product become memories
# ("the supplier is shipping partial pallets", "the supervisor wants changeovers on second shift").
# Plain questions don't, which also keeps repeat questions' prompts stable
# for the LLM cache.
STATEMENT_RE = re.compile(r"\b(they|they're|the supplier|the customer|customer|the supervisor|the buyer|quality|the line|the press|the mold|the die|the product)\s+"
                          r"(want|wants|prefer|prefers|said|asked|need|needs|is|are|was|has|have|changed|moved|ships|runs)\b"
                          r"|\bnote that\b|\bremember\b", re.I)


async def _remember(product_id: str, planner_id: str, message: str, intent: str, session_id: str):
    if not aom.enabled or not STATEMENT_RE.search(message):
        return
    try:
        await aom.memory_add(product_id, f"Planner {planner_id} noted ({intent}): {message.strip()[:500]}", session_id=session_id,
                             metadata={"intent": intent, "app": "mfg-planning-mobile", "planner_id": planner_id})
    except AOMError as exc:
        log.info("memory add skipped: %s", exc)


async def _recall(product_id: str, message: str) -> list[str]:
    if not aom.enabled:
        return []
    try:
        res = await aom.memory_search(product_id, message, top_k=3)
        return [e.get("content", "") for e in res.get("entries", []) if e.get("content")]
    except AOMError:
        return []


async def _ask_rag_app(message: str, intent: str) -> dict | None:
    """Playbook questions go to the assistant's AOM RAG Application: one call
    that runs AOM's Context Cache (retrieval), the KB search scoped to today's
    playbook, and the LLM Cache, and is counted on the RAG Applications page."""
    if not rag_app.usable():
        return None
    l0 = time.perf_counter()
    try:
        res = await aom.rag_query(config.RAG_APP_ID, message)
    except AOMError as exc:
        rag_app.query_failed(exc)
        log.info("RAG app query failed, answering on the assistant's own path: %s", exc)
        return None
    ms = round((time.perf_counter() - l0) * 1000, 1)
    retrieval = res.get("retrieval") or {}
    llm_res = res.get("llm") or {}
    cache = llm_res.get("cache") or {}
    srcs = res.get("sources") or []
    chunks = [{"title": c.get("document_title"), "content": c.get("preview"), "source": "aom-rag"} for c in srcs]
    reply = (res.get("answer") or "").strip()
    llm = {"status": cache.get("status", "skipped" if not res.get("llm") else "miss"), "similarity": cache.get("similarity"),
           "namespace": f"rag:{config.RAG_APP_ID}", "provider": llm_res.get("provider"), "model": llm_res.get("model"),
           "latency_ms": ms, "stub": bool(llm_res.get("stub")), "tokens_saved": llm_res.get("tokens_saved") or 0,
           "cost_saved_usd": llm_res.get("cost_saved_usd") or 0, "rag_app": config.RAG_APP_ID}
    if not res.get("llm") or llm["stub"] or not reply:
        reply = compose_fallback(intent, False, message, {"kb_playbook": chunks}, None)
        llm["composed_locally"] = True
    return {
        "reply": reply, "llm": llm,
        "sources": [{"title": c["title"], "snippet": (c.get("content") or "")[:160], "kind": "aom-rag"} for c in chunks[:3]],
        "context": {"status": retrieval.get("cache", "miss"), "latency_ms": retrieval.get("latency_ms", 0),
                    "source": "aom-rag", "rag_app": config.RAG_APP_ID, "chunks": retrieval.get("chunks"),
                    "export_date": STATE.get("export_date"), "kb_mode": "aom"},
        "trace_id": (res.get("trace") or {}).get("trace_id"),
    }


async def ask(planner_id: str, message: str, product_id: str | None = None, session_id: str | None = None) -> dict:
    session_id = session_id or f"app-{planner_id}"
    trace_id = uuid.uuid4().hex
    token = current_trace.set({"trace_id": trace_id, "session_id": session_id})
    t0 = time.perf_counter()
    try:
        planner = await store.get("planners", planner_id_doc(planner_id))
        if not planner:
            raise ValueError("unknown planner")
        product = None
        if product_id:
            product = await store.get("products", product_id_doc(product_id))
            if not product or product.get("plant_id") != planner.get("plant_id"):
                raise ValueError("product not in this planner's plant")
        intent, personal = classify(message)
        if personal and not product:
            result = {
                "reply": "Open a product on today's floor walk first and I'll answer for it - forecast, build quantity, "
                         "inventory, materials, alerts, budget and history.", "intent": intent, "personal": True, "actions": [{"label": "Open today's floor walk", "screen": "route"}],
                "sources": [], "context": {"status": "skipped", "latency_ms": 0}, "memory_recalled": 0,
                "llm": {"status": "skipped", "composed_locally": True}, "trace_id": None, "governed": False,
                "total_ms": round((time.perf_counter() - t0) * 1000, 1), "export_date": STATE.get("export_date"), "product_id": None,
            }
            await _append_chat(planner_id, message, result)
            return result
        if not personal:
            via_rag = await _ask_rag_app(message, intent)
            if via_rag:
                result = {
                    "reply": via_rag["reply"], "intent": intent, "personal": False, "actions": [],
                    "sources": via_rag["sources"], "context": via_rag["context"], "memory_recalled": 0,
                    "llm": via_rag["llm"], "trace_id": via_rag["trace_id"] or trace_id, "governed": True,
                    "total_ms": round((time.perf_counter() - t0) * 1000, 1), "export_date": STATE.get("export_date"),
                    "product_id": product_id,
                }
                metrics.assistant(result)
                await _append_chat(planner_id, message, result)
                return result
        ctx = await get_context(planner_id, intent, product, personal, message)
        memories = await _recall(product["product_id"], message) if personal and product else []
        prompt = build_prompt(message, personal, ctx, product, memories)
        namespace = config.LLM_NAMESPACE_PRODUCT if personal else config.LLM_NAMESPACE_PLAYBOOK
        llm = {"status": "local", "namespace": namespace, "provider": None, "model": None, "similarity": None,
               "latency_ms": None, "stub": False, "tokens_saved": 0}
        reply = None
        if aom.enabled:
            try:
                l0 = time.perf_counter()
                res = await aom.complete(prompt, namespace=namespace)
                cache = res.get("cache") or {}
                llm.update({"status": cache.get("status", "miss"), "similarity": cache.get("similarity"),
                            "provider": res.get("provider"), "model": res.get("model"),
                            "latency_ms": round((time.perf_counter() - l0) * 1000, 1), "stub": bool(res.get("stub")),
                            "tokens_saved": res.get("tokens_saved", 0), "cost_saved_usd": res.get("cost_saved_usd", 0)})
                if not res.get("stub") and (res.get("response") or "").strip():
                    reply = res["response"].strip()
            except AOMError as exc:
                llm["status"] = "error"
                llm["error"] = str(exc)[:200]
        if not reply:
            reply = compose_fallback(intent, personal, message, ctx, product)
            llm["composed_locally"] = True
        actions = suggest_actions(intent, ctx, product, message) if personal else []
        sources = ([{"title": c["title"], "snippet": (c.get("content") or "")[:160], "kind": c.get("source")}
                    for c in (ctx.get("kb_profile", []) if personal else [])[:2]]
                   + [{"title": c["title"], "snippet": (c.get("content") or "")[:160], "kind": c.get("source")}
                      for c in ctx.get("kb_playbook", [])[:2]])
        result = {
            "reply": reply, "intent": intent, "personal": personal, "actions": actions, "sources": sources,
            "context": {"status": ctx["_cache"]["status"], "latency_ms": ctx["_cache"]["latency_ms"],
                        "source": ctx["_cache"].get("source"),
                        "built_at": ctx.get("built_at"), "build_ms": ctx.get("build_ms"), "kb_mode": ctx.get("kb_mode"),
                        "export_date": ctx.get("export_date")},
            "memory_recalled": len(memories), "llm": llm, "trace_id": trace_id if aom.enabled else None,
            "governed": aom.enabled, "total_ms": round((time.perf_counter() - t0) * 1000, 1),
            "export_date": STATE.get("export_date"), "product_id": product_id if personal else None,
        }
        metrics.assistant(result)
        await _append_chat(planner_id, message, result)
        if personal and product:
            await _remember(product["product_id"], planner_id, message, intent, session_id)
        return result
    finally:
        current_trace.reset(token)


async def _append_chat(planner_id: str, message: str, result: dict):
    doc = await store.get("assistant", chat_id(planner_id)) or {
        "type": "assistant", "planner_id": planner_id, "messages": [], "answers": {}, "channels": [planner_channel(planner_id)]}
    now = now_iso()
    doc["messages"] = (doc["messages"] + [
        {"id": uuid.uuid4().hex[:12], "role": "planner", "text": message, "at": now, "product_id": result.get("product_id")},
        {"id": uuid.uuid4().hex[:12], "role": "assistant", "text": result["reply"], "at": now,
         "intent": result["intent"], "actions": result["actions"], "context": result["context"], "llm": result["llm"],
         "sources": result["sources"], "product_id": result.get("product_id")},
    ])[-40:]
    # Answer cache the phone can reuse offline for the same (normalized) question on the same product.
    akey = f"{result.get('product_id') or '-'}|{_norm(message)[:120]}"
    doc["answers"][akey] = {"reply": result["reply"], "intent": result["intent"], "at": now, "actions": result["actions"]}
    if len(doc["answers"]) > 60:
        doc["answers"] = dict(list(doc["answers"].items())[-60:])
    doc["updated_at"] = now
    await store.upsert("assistant", chat_id(planner_id), doc)
