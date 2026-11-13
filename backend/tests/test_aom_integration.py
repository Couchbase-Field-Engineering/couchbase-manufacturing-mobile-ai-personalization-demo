"""The AOM-governed path, against tests/fake_aom.py (same routes and shapes as
the real AOM): KB publishing, document-scoped retrieval, LLM cache across
planners, tool calls over real MCP, and the human-approval tier.
"""
import os
import subprocess
import sys
import time
import uuid

import httpx
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKEND = os.path.join(ROOT, "backend")
AOM, API = "http://127.0.0.1:18090", "http://127.0.0.1:18201"


def wait(url, tries=80):
    for _ in range(tries):
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise RuntimeError(f"{url} never came up")


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    dump = tmp_path_factory.mktemp("dump")
    subprocess.run([sys.executable, os.path.join(ROOT, "databricks", "generate_mock_dump.py"), "--export-date",
                    "2026-11-13", "--target-mb", "3", "--out", str(dump)], check=True, capture_output=True)
    env = {**os.environ, "STORE_DRIVER": "memory", "DUMP_DIR": str(dump), "AOM_URL": AOM,
           "AOM_AGENT_API_KEY": "fake-agent-key", "AOM_ADMIN_PASSWORD": "x", "KB_PROFILE_LIMIT": "10",
           "SG_ADMIN_URL": "", "REQUEST_POLL_SECONDS": "0.3", "FAKE_MCP_URL": f"{API}/mfg/mcp", "AOM_MCP_PUBLIC_URL": f"{API}/mfg/mcp"}
    uv = [sys.executable, "-m", "uvicorn"]
    procs = [subprocess.Popen(uv + ["tests.fake_aom:app", "--port", "18090"], cwd=BACKEND, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)]
    wait(f"{AOM}/api/health")
    procs.append(subprocess.Popen(uv + ["app.main:app", "--port", "18201"], cwd=BACKEND, env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    wait(f"{API}/api/health")
    for _ in range(60):
        st = httpx.get(f"{API}/api/status").json()
        if (st["load"].get("kb") or {}).get("state") == "published":
            break
        time.sleep(0.25)
    yield
    for p in procs:
        p.terminate()


def login(employee_id, last):
    r = httpx.post(f"{API}/api/auth/login", json={"employee_id": employee_id, "last_name": last})
    return {"Authorization": f"Bearer {r.json()['token']}"}, r.json()["planner_id"]


def ask(h, q, product_id=None):
    return httpx.post(f"{API}/api/assistant", headers=h, json={"message": q, "product_id": product_id}, timeout=30).json()


PUMP = "FG00101001"


def test_kb_published_and_scoped(stack):
    titles = list(httpx.get(f"{AOM}/test/state").json()["docs"].values())
    assert any(t.startswith("Production Planning Playbook") for t in titles)
    assert any(PUMP in t for t in titles)
    h, _ = login("204817", "Chen")
    a = ask(h, "What needs doing for this product today?", PUMP)
    assert a["context"]["kb_mode"] == "aom" and a["governed"]
    searched = [c[1] for c in httpx.get(f"{AOM}/test/state").json()["calls"] if c[0] == "kb_search"]
    assert all(d is not None for d in searched), "every KB search must be scoped to one document"


def test_llm_cache_shared_for_playbook_only(stack):
    h1, _ = login("204817", "Chen")
    h2, _ = login("318552", "Walker")
    a = ask(h1, "How does a budget override work?")
    b = ask(h2, "How does a budget override work?")
    assert a["personal"] is False and b["llm"]["status"] == "hit_exact"
    p1 = ask(h1, "How much budget is left for this product?", PUMP)
    p2 = ask(h2, "How much budget is left for this product?", "FG00102001")
    assert p1["llm"]["namespace"] == "rag:mfg-planning-assistant:product" and p2["llm"]["status"] == "miss"


def test_rag_application_registered_and_scoped_to_playbook(stack):
    st = httpx.get(f"{AOM}/test/state").json()
    app = st["rag_apps"]["mfg-planning-assistant"]
    playbook = [d for d, t in st["docs"].items() if t.startswith("Production Planning Playbook")]
    assert app["enabled"] and app["document_ids"] == playbook
    assert set(app["allowed_roles"]) == {"production_planner", "admin"}

    h, _ = login("427190", "Alvarez")
    q = "How is safety stock set?"
    a, b = ask(h, q), ask(h, q)
    assert a["context"]["source"] == "aom-rag" and a["llm"]["rag_app"] == "mfg-planning-assistant"
    assert b["context"]["status"] == "hit" and b["llm"]["status"] == "hit_exact"
    assert "safety stock" in a["reply"].lower() and a["sources"]
    rag = [c for c in httpx.get(f"{AOM}/test/state").json()["calls"] if c[0] == "rag_query"]
    # Playbook answers never retrieve a product's profile.
    assert rag and all(set(c[3]) <= set(playbook) for c in rag)


def test_product_context_cached_in_aom(stack):
    h, _ = login("204817", "Chen")
    q = "What was released last time for this product and how often is it run?"
    first, second = ask(h, q, "FG00101003"), ask(h, q, "FG00101003")
    assert second["context"]["status"] == "hit" and second["context"]["source"] == "aom"
    calls = httpx.get(f"{AOM}/test/state").json()["calls"]
    sets = [c for c in calls if c[0] == "ctx_set" and c[2].startswith("FG00101003:history:")]
    assert sets and sets[0][1] == "rag:mfg-planning-assistant:product-context" and sets[0][3] is not None
    assert first["context"]["status"] in ("miss", "hit")


def test_budget_override_waits_for_human(stack):
    h, rid = login("204817", "Chen")
    oid = f"order::{rid}::{uuid.uuid4().hex[:8]}"
    httpx.post(f"{API}/api/sync/push", headers=h, json={"docs": [{"collection": "production_orders", "id": oid, "doc": {
        "type": "request", "planner_id": rid, "product_id": PUMP, "tool": "request_budget_override",
        "args": {"product_id": PUMP, "lines": [{"sku": "MF-CST-1204", "qty": 4}, {"sku": "MF-MOT-5101", "qty": 1},
                                              {"sku": "MF-MTL-4101", "qty": 1}, {"sku": "MF-MOT-5110", "qty": 1}],
                 "reason": "Castings PO is late and demand is 38% over forecast"}, "created_at": "2026-11-13T15:00:00Z"}}]})

    def req():
        docs = httpx.get(f"{API}/api/sync/pull", headers=h).json()["docs"]
        return next(d["doc"] for d in docs if d["id"] == oid)

    for _ in range(30):
        if req()["status"] == "awaiting_approval":
            break
        time.sleep(0.2)
    doc = req()
    assert doc["status"] == "awaiting_approval" and doc["governed"]
    httpx.post(f"{AOM}/test/approve/{doc['approval_id']}")
    for _ in range(30):
        if req()["status"] in ("completed", "materials_pending"):
            break
        time.sleep(0.2)
    assert req()["status"] in ("completed", "materials_pending")
    assert req()["item"]["budget_override"] is True
