"""Backend for the Couchbase manufacturing demand and production planning demo.

  /api/*          planner sign-in (mints a Sync Gateway session), assistant, status,
                  and a REST "preview sync" used only by the browser preview build
  /mfg/mcp/       the mfg-production-planning MCP server AOM governs

Background tasks: the Databricks export loader/poller and the offline-first
production-order worker.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import logging
import time

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import aom_bootstrap, assistant, config, loader, metrics, rag_app, sgw, worker
from .aom_client import aom
from .docs import CATALOG_ID, STATE, product_id_doc, chat_id, ctx_id, now_iso, plan_id, planner_channel, planner_id_doc
from .mcp_server import mcp
from .store import store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("mfg.api")

PERSONA_BLURBS = {
    "PLN1001": "Peabody Fluid Power Plant: a gear pump with demand 38% over forecast, castings on a late PO, a foundry bay with no signal and a production order that needs a budget override; a motor controller short of boards on allocation; a valve block ahead of a steel price step; a weld cell and a kitting line.",
    "PLN1002": "Kingston Molding Plant: a key-account enclosure whose recommended build is over the press's weekly capacity, in a steel press shop with no Wi-Fi; a resin price increase two weeks out; two steady runners.",
    "PLN1003": "Fort Worth Assembly Plant: a 12-product OEM tractor program on one key-account contract, plus a heat shield on a quality hold.",
}
_bg: list = []
_LAST_PULL: dict[str, str] = {}


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    await store.connect()

    auto_register = config.AOM_AUTO_REGISTER and bool(config.AOM_ADMIN_PASSWORD)

    async def initial_load():
        try:
            # With self-registration the KB is published once AOM sign-in works.
            await loader.load(kb=not auto_register)
            metrics.export_loaded(loader.STATUS.get("rows", 0))
        except Exception:  # noqa: BLE001
            log.exception("initial export load failed")

    first = asyncio.create_task(initial_load())
    _bg.append(first)
    if auto_register:
        _bg.append(asyncio.create_task(aom_bootstrap.run(first)))
    _bg.append(asyncio.create_task(worker.loop()))
    _bg.append(asyncio.create_task(loader.poll_forever()))
    async with mcp.session_manager.run():
        yield
    for t in _bg:
        t.cancel()


app = FastAPI(title="Couchbase Manufacturing Production Planning Demo", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])
app.mount("/mfg", mcp.streamable_http_app())


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------
def _sign(payload: str) -> str:
    return hmac.new(config.APP_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]


def make_token(planner_id: str) -> str:
    payload = f"{planner_id}|{int(time.time()) + config.APP_TOKEN_TTL_SECONDS}"
    return base64.urlsafe_b64encode(f"{payload}|{_sign(payload)}".encode()).decode()


def current_planner(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Sign in first")
    try:
        rid, exp, sig = base64.urlsafe_b64decode(authorization.split(" ", 1)[1].encode()).decode().split("|")
    except Exception:  # noqa: BLE001
        raise HTTPException(401, "Invalid session") from None
    if not hmac.compare_digest(sig, _sign(f"{rid}|{exp}")) or int(exp) < time.time():
        raise HTTPException(401, "Session expired - sign in again")
    return rid


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index():
    """Landing page: where everything in the demo lives."""
    rows = [
        ("http://localhost:8085/story", "The story: a production plan that doesn't wait for Wi-Fi, and a Databricks bill that stops growing per tap (start here)"),
        ("http://localhost:8085", "The app in a browser (docker compose service <code>web</code>)"),
        ("http://localhost:8204/api/status", "Backend status: export load, Knowledge Base publishing, AOM, Sync Gateway, Databricks offload"),
        ("http://localhost:8204/docs", "Backend API (OpenAPI)"),
        ("http://localhost:8204/mfg/mcp", "MCP server <code>mfg-production-planning</code> (called by AOM)"),
        ("http://localhost:18095", "Couchbase Web Console (Administrator / password123)"),
        ("ws://localhost:4992/manufacturing", "Sync Gateway - Couchbase Lite replicates here"),
        ("https://localhost:8090", "Couchbase Agent Operations Manager (its own stack)"),
    ]
    items = "".join(f'<li><a href="{u}">{u}</a> &nbsp;{d}</li>' for u, d in rows)
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Couchbase manufacturing production planning demo backend</title>
<style>body{{font-family:-apple-system,Arial,sans-serif;max-width:760px;margin:48px auto;padding:0 16px;color:#1f1f1f}}
h1{{color:#ea2328}} li{{margin:10px 0}} a{{color:#ea2328;font-weight:600}}</style></head>
<body><h1>Couchbase manufacturing production planning demo</h1>
<p>This is the backend. Open the app at <a href="http://localhost:8085">http://localhost:8085</a>.
Loaded export: <b>{STATE.get("export_date") or "loading..."}</b> ({loader.STATUS.get("state")}).</p>
<ul>{items}</ul></body></html>"""


@app.get("/api/health")
async def health():
    return {"status": "ok", "export_date": STATE.get("export_date"), "load": loader.STATUS.get("state")}


@app.get("/api/status")
async def status():
    aom_state = {"configured": aom.enabled, "url": config.AOM_URL or None, "kb_publisher": aom.admin_enabled,
                 "last_error": aom.last_error, "registration": aom_bootstrap.STATUS, "rag_app": dict(rag_app.STATUS, app_id=config.RAG_APP_ID),
                 "context_namespace": config.CONTEXT_NAMESPACE,
                 "dashboard_url": config.AOM_DASHBOARD_URL or None}
    if aom.enabled:
        try:
            aom_state["health"] = await aom.health()
        except Exception as exc:  # noqa: BLE001
            aom_state["health"] = {"error": str(exc)[:200]}
    return {"store": store.driver, "export_date": STATE.get("export_date"),
            "load": {k: v for k, v in loader.STATUS.items()}, "aom": aom_state, "metrics": metrics.snapshot(),
            "sync_gateway": {"configured": sgw.enabled(), "public_url": config.SG_PUBLIC_URL or None, "db": config.SG_DB},
            "mcp_server": {"server_id": config.AOM_MCP_SERVER_ID, "registered_url": config.AOM_MCP_PUBLIC_URL}}


@app.get("/api/demo/personas")
async def personas():
    out = []
    for rid in loader.PERSONAS:
        r = await store.get("planners", planner_id_doc(rid))
        if not r:
            continue
        out.append({"planner_id": rid, "name": f"{r['first_name']} {r['last_name']}", "last_name": r["last_name"],
                    "employee_id": r["employee_id"], "plant": r["plant_name"], "stops_today": len(r.get("route", [])),
                    "products": r.get("product_count"), "blurb": PERSONA_BLURBS.get(rid, "")})
    return {"personas": out, "export_date": STATE.get("export_date")}


class LoginRequest(BaseModel):
    employee_id: str
    last_name: str


@app.post("/api/auth/login")
async def login(req: LoginRequest):
    idx = await store.get("lookups", f"login::{req.employee_id.strip()}")
    if not idx or req.last_name.strip().lower() != idx.get("last_name"):
        raise HTTPException(401, "We couldn't find that employee ID and last name.")
    planner_id = idx["planner_id"]
    planner = await store.get("planners", planner_id_doc(planner_id))
    sg = await sgw.mint_session(planner_id, planner["plant_id"])
    return {"token": make_token(planner_id), "planner_id": planner_id,
            "planner": {"first_name": planner["first_name"], "last_name": planner["last_name"], "email": planner.get("email"),
                    "plant_id": planner["plant_id"], "plant_name": planner["plant_name"]},
            "channels": [planner_channel(planner_id), f"plant:{planner['plant_id']}", "catalog"], "sync_gateway": sg,
            "export_date": STATE.get("export_date")}


@app.post("/api/auth/refresh")
async def refresh(planner_id: str = Depends(current_planner)):
    """Re-mint the Sync Gateway session (24h) for a signed-in planner - called by
    the app on launch when it has connectivity."""
    planner = await store.get("planners", planner_id_doc(planner_id))
    if not planner:
        raise HTTPException(401, "Sign in again")
    return {"token": make_token(planner_id), "planner_id": planner_id, "sync_gateway": await sgw.mint_session(planner_id, planner["plant_id"]),
            "export_date": STATE.get("export_date")}


@app.get("/api/me")
async def me(planner_id: str = Depends(current_planner)):
    return await store.get("planners", planner_id_doc(planner_id))


async def _planner_docs(planner_id: str) -> list[dict]:
    out = []
    planner = await store.get("planners", planner_id_doc(planner_id))
    if not planner:
        return out
    out.append({"collection": "planners", "id": planner_id_doc(planner_id), "doc": planner})
    for a in await store.sites_in_plant(planner["plant_id"]):
        out.append({"collection": "products", "id": product_id_doc(a["product_id"]), "doc": a})
        plan = await store.get("production_plans", plan_id(a["product_id"]))
        if plan:
            out.append({"collection": "production_plans", "id": plan_id(a["product_id"]), "doc": plan})
        for key in assistant.INTENTS:
            d = await store.get("context_cache", ctx_id(a["product_id"], key))
            if d:
                out.append({"collection": "context_cache", "id": ctx_id(a["product_id"], key), "doc": d})
    for key in assistant.INTENTS:
        d = await store.get("context_cache", ctx_id(planner_id, f"playbook_{key}"))
        if d:
            out.append({"collection": "context_cache", "id": ctx_id(planner_id, f"playbook_{key}"), "doc": d})
    for col, key in (("assistant", chat_id(planner_id)), ("catalog", CATALOG_ID)):
        d = await store.get(col, key)
        if d:
            out.append({"collection": col, "id": key, "doc": d})
    for d in await store.production_orders_for_planner(planner_id):
        key = d.pop("_id")
        out.append({"collection": "production_orders", "id": key, "doc": d})
    return out


@app.get("/api/sync/pull")
async def sync_pull(planner_id: str = Depends(current_planner)):
    """Browser-preview stand-in for Sync Gateway: the same documents and ids
    the planner's channels would replicate into Couchbase Lite."""
    docs = await _planner_docs(planner_id)
    # Count only what changed since this planner's last pull, the way a Couchbase
    # Lite replicator only moves changed revisions - a poll that finds nothing
    # new is not a warehouse query avoided.
    fp = hashlib.sha1("|".join(f"{d['id']}:{d['doc'].get('updated_at') or d['doc'].get('generated_at') or d['doc'].get('rev', '')}:{d['doc'].get('status', '')}"
                               for d in docs).encode()).hexdigest()
    if _LAST_PULL.get(planner_id) != fp:
        metrics.sync_pull(len(docs) if planner_id not in _LAST_PULL else max(1, len(docs) // 10))
        _LAST_PULL[planner_id] = fp
    return {"docs": docs, "at": now_iso(), "export_date": STATE.get("export_date")}


class PushDoc(BaseModel):
    collection: str
    id: str
    doc: dict


class PushRequest(BaseModel):
    docs: list[PushDoc] = Field(default_factory=list)


@app.post("/api/sync/push")
async def sync_push(req: PushRequest, planner_id: str = Depends(current_planner)):
    """Browser-preview stand-in for a push replication. Same rule the Sync
    Gateway sync function enforces: a planner may only create `production_orders`
    documents, only as themselves, only as `pending`."""
    accepted = []
    for d in req.docs:
        if d.collection != "production_orders" or d.doc.get("planner_id") != planner_id or not d.id.startswith(f"order::{planner_id}::"):
            raise HTTPException(403, "Only your own production orders can be pushed")
        if await store.get("production_orders", d.id):
            continue
        doc = {**d.doc, "type": "request", "status": "pending", "channels": [planner_channel(planner_id)],
               "received_at": now_iso()}
        await store.upsert("production_orders", d.id, doc)
        accepted.append(d.id)
    if accepted:
        _bg.append(asyncio.create_task(worker.run_once()))
    return {"accepted": accepted}


class AskRequest(BaseModel):
    message: str
    product_id: str | None = None
    session_id: str | None = None


@app.post("/api/assistant")
async def ask(req: AskRequest, planner_id: str = Depends(current_planner)):
    if not req.message.strip():
        raise HTTPException(400, "Ask me something")
    try:
        return await assistant.ask(planner_id, req.message[:1000], req.product_id, req.session_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@app.post("/api/admin/reload")
async def reload_dump(force: bool = True):
    return {k: v for k, v in (await loader.load(force=force)).items()}
