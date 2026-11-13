"""End-to-end checks of the demo backend in memory mode (no Couchbase, no AOM).

    cd backend && pytest -q
"""
import os
import subprocess
import sys
import time
import uuid

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXPORT = "2026-11-13"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    dump = tmp_path_factory.mktemp("dump")
    subprocess.run([sys.executable, os.path.join(ROOT, "databricks", "generate_mock_dump.py"),
                    "--export-date", EXPORT, "--target-mb", "3", "--out", str(dump)], check=True,
                   capture_output=True)
    os.environ.update(STORE_DRIVER="memory", DUMP_DIR=str(dump), AOM_URL="", SG_ADMIN_URL="")
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        for _ in range(100):
            if c.get("/api/health").json()["load"] == "loaded":
                break
            time.sleep(0.1)
        yield c


def login(client, employee_id, last):
    r = client.post("/api/auth/login", json={"employee_id": employee_id, "last_name": last})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}, r.json()["planner_id"]


def pull(client, h):
    docs = client.get("/api/sync/pull", headers=h).json()["docs"]
    return {(d["collection"], d["id"]): d["doc"] for d in docs}


def push(client, h, pid, tool, args):
    oid = f"order::{pid}::{uuid.uuid4().hex[:12]}"
    r = client.post("/api/sync/push", headers=h, json={"docs": [{"collection": "production_orders", "id": oid, "doc": {
        "type": "request", "planner_id": pid, "tool": tool, "args": args, "product_id": args.get("product_id"),
        "created_at": f"{EXPORT}T15:00:00Z"}}]})
    assert r.status_code == 200, r.text
    for _ in range(50):
        d = pull(client, h).get(("production_orders", oid))
        if d and d["status"] not in ("pending", "processing"):
            return d
        time.sleep(0.1)
    raise AssertionError("production order never processed")


PUMP = "FG00101001"        # HP-2200 gear pump: demand spike, castings late, plan over budget
CONTROLLER = "FG00101002"  # MC-480 motor controller: needs boards, which are on allocation everywhere
ENCLOSURE = "FG00102001"   # EH-300 enclosure: recommended build is over the press's weekly capacity
HEAT_SHIELD = "FG00103020"  # HS-612 heat shield: on a quality hold
CASTING, PCBA, RESIN, SCREWS = "MF-CST-1204", "MF-ELC-3101", "MF-RSN-2101", "MF-FST-7110"


def test_personas_and_login(client):
    ps = client.get("/api/demo/personas").json()["personas"]
    assert [p["planner_id"] for p in ps] == ["PLN1001", "PLN1002", "PLN1003"]
    assert ps[0]["stops_today"] == 5 and ps[1]["stops_today"] == 4 and ps[2]["stops_today"] == 5
    assert client.post("/api/auth/login", json={"employee_id": "204817", "last_name": "Nope"}).status_code == 401


def test_floor_walk_and_production_plans_sync(client):
    h, pid = login(client, "204817", "Chen")
    docs = pull(client, h)
    planner = docs[("planners", f"planner::{pid}")]
    stops = [s["product_id"] for s in planner["route"]]
    assert stops == [PUMP, CONTROLLER, "FG00101003", "FG00101004", "FG00101005"]
    assert planner["route"][0]["signal_note"].startswith("Foundry bay")
    assert planner["route"][0]["alert_count"] == 2 and planner["route"][0]["worst_alert"] == "major"
    assert planner["route"][0]["recommended_units"] == 1200 and planner["route"][0]["scheduled_units"] == 900
    plan = docs[("production_plans", f"plan::{PUMP}")]
    # schedule: the model wants 1,200 this week against 900 scheduled, inside the cell's 1,300 capacity
    assert plan["schedule"]["recommended_units"] == 1200 and plan["schedule"]["delta_units"] == 300
    assert plan["schedule"]["capacity_units"] == 1300 and not plan["schedule"]["overtime_needed"]
    assert plan["inventory"]["days_of_cover"] == 4.2 and plan["inventory"]["recommended_safety_stock_units"] == 620
    assert plan["demand"]["next_4wk_units"] == 4800
    # materials: castings first, one pallet short at the Northeast warehouse
    assert plan["lines"] and plan["lines"][0]["sku"] == CASTING
    assert plan["lines"][0]["days_to_stockout"] == 1 and plan["lines"][0]["availability"] == "low"  # 3 on hand, 4 wanted
    assert plan["lines"][0]["long_lead"] and "28-day lead time" in plan["lines"][0]["reason"]
    assert plan["plan_total_usd"] == 18970.0
    assert plan["order_type"] == "Expedite"  # a major alert is open
    kinds = [t["kind"] for t in plan["tasks"]]
    assert kinds[0] == "alert" and "readiness" in kinds  # alerts first, then the readiness checklist
    assert not plan["auth"]["ok"] and plan["auth"]["shortfall_usd"] == 3774.0  # the override story
    assert ("catalog", "catalog::v1") in docs
    # the product document carries the forecast the plan was built from
    pump = docs[("products", f"product::{PUMP}")]
    assert len(pump["demand"]["history_12mo"]) == 12 and len(pump["demand"]["forecast_8wk"]) == 8
    assert sum(f["units"] for f in pump["demand"]["forecast_8wk"][:4]) in range(4790, 4801)
    # every product in the plant syncs, nothing from another plant
    assert all(d["plant_id"] == "PLT-MA-PEA" for (c, _), d in docs.items() if c == "products")


def test_assistant_quotes_the_same_plan_the_phone_shows(client):
    h, pid = login(client, "204817", "Chen")
    plan = pull(client, h)[("production_plans", f"plan::{PUMP}")]
    a = client.post("/api/assistant", headers=h, json={"message": "What needs doing for this product today?", "product_id": PUMP}).json()
    assert "$18,970.00" in a["reply"] and "$3,774.00" in a["reply"]
    act = next(x for x in a["actions"] if x.get("tool") == "request_budget_override")
    assert sorted(l["sku"] for l in act["args"]["lines"]) == sorted(l["sku"] for l in plan["lines"] if l["selected"])


def test_release_over_budget_fails_then_override_releases_it(client):
    h, pid = login(client, "204817", "Chen")
    plan = pull(client, h)[("production_plans", f"plan::{PUMP}")]
    lines = [{"sku": l["sku"], "qty": l["qty"]} for l in plan["lines"] if l["selected"]]
    tasks = [t["title"] for t in plan["tasks"]]
    r = push(client, h, pid, "release_production_order", {"product_id": PUMP, "lines": lines, "run_qty": 1200, "reference": "SO-1042"})
    assert r["status"] == "failed" and r["reason_code"] == "budget_limit", r
    r = push(client, h, pid, "request_budget_override", {"product_id": PUMP, "lines": lines, "run_qty": 1200, "tasks_done": tasks,
                                                         "reason": "Castings PO is late and demand is 38% over forecast"})
    assert r["status"] == "materials_pending", r  # one pallet of castings is short at the Northeast warehouse
    assert r["item"]["budget_override"] is True and r["cost_usd"] == 18970.0 and r["item"]["run_qty"] == 1200
    assert r["item"]["backordered"][0]["sku"] == CASTING and r["item"]["follow_up_date"]
    assert r["item"]["tasks_done"] == tasks
    docs = pull(client, h)
    product = docs[("products", f"product::{PUMP}")]
    assert product["order_history"][0]["production_order_id"] == r["item"]["production_order_id"]
    assert product["auth"]["committed_usd"] == 84804.0 + 18970.0
    assert product["schedule"]["scheduled_units"] == 1200  # the week's schedule now matches the model
    assert product["alerts"] == []  # castings ordered and the build raised: both alerts cleared
    # the plan re-ranked: the materials just ordered are gone and the next release is a scheduled run again
    plan2 = docs[("production_plans", f"plan::{PUMP}")]
    assert not any(l["sku"] == CASTING for l in plan2["lines"])
    assert plan2["order_type"] == "Scheduled run" and plan2["schedule"]["delta_units"] == 0


def test_short_warehouse_stock_puts_materials_on_order(client):
    h, pid = login(client, "204817", "Chen")
    # Every warehouse holds 1-3 trays of controller boards; ask for more than it has.
    r = push(client, h, pid, "release_production_order", {"product_id": CONTROLLER, "lines": [{"sku": PCBA, "qty": 5}]})
    assert r["status"] == "materials_pending", r
    assert r["item"]["backordered"][0]["sku"] == PCBA


def test_build_over_capacity_needs_an_overtime_shift(client):
    h, pid = login(client, "318552", "Walker")
    plan = pull(client, h)[("production_plans", f"plan::{ENCLOSURE}")]
    assert plan["schedule"]["overtime_needed"] and plan["schedule"]["overtime_hours"] == 11  # 5,200 wanted, 4,600 capacity
    o = client.post("/api/assistant", headers=h, json={"message": "Do I need an overtime shift for this product?", "product_id": ENCLOSURE}).json()
    assert o["intent"] == "overtime" and "11 hours" in o["reply"]
    assert any(x.get("tool") == "request_overtime_shift" and x["args"]["hours"] == 11 for x in o["actions"])
    r = push(client, h, pid, "release_production_order", {"product_id": ENCLOSURE, "lines": [{"sku": RESIN, "qty": 2}], "run_qty": 5200})
    assert r["status"] == "failed" and r["reason_code"] == "capacity_limit", r
    r = push(client, h, pid, "request_overtime_shift", {"product_id": ENCLOSURE, "hours": 11, "reason": "Key account pulled its launch in"})
    assert r["status"] == "completed" and r["item"]["hours"] == 11 and r["item"]["capacity_units"] >= 5200
    product = pull(client, h)[("products", f"product::{ENCLOSURE}")]
    assert product["overtime_shifts"][0]["shift_id"] == r["item"]["shift_id"]
    r = push(client, h, pid, "release_production_order", {"product_id": ENCLOSURE, "lines": [{"sku": RESIN, "qty": 2}], "run_qty": 5200})
    assert r["status"] == "completed" and r["item"]["run_qty"] == 5200, r
    plan2 = pull(client, h)[("production_plans", f"plan::{ENCLOSURE}")]
    assert not plan2["schedule"]["overtime_needed"] and plan2["schedule"]["scheduled_units"] == 5200
    assert plan2["alerts"] == []  # resin ordered, build covers the pull-in


def test_assistant_context_cache_and_playbook(client):
    h, pid = login(client, "318552", "Walker")
    product = "FG00102002"
    a = client.post("/api/assistant", headers=h, json={"message": "What needs doing for this product today?", "product_id": product}).json()
    assert a["intent"] == "plan" and a["personal"] and a["context"]["status"] == "miss"
    assert any(x.get("tool") == "release_production_order" for x in a["actions"])
    b = client.post("/api/assistant", headers=h, json={"message": "Anything about to run out here?", "product_id": product}).json()
    assert b["context"]["status"] == "hit"
    g = client.post("/api/assistant", headers=h, json={"message": "How does a budget override work?"}).json()
    assert g["personal"] is False and "plant controller" in g["reply"].lower()
    s = client.post("/api/assistant", headers=h, json={"message": "How is safety stock set?"}).json()
    assert s["personal"] is False and "fill-rate target" in s["reply"].lower()
    n = client.post("/api/assistant", headers=h, json={"message": "What needs doing here?"}).json()
    assert n["personal"] and "floor walk" in n["reply"].lower()  # no product open
    f = client.post("/api/assistant", headers=h, json={"message": "What is the demand forecast for this product?", "product_id": product}).json()
    assert f["intent"] == "forecast" and "next four" in f["reply"]
    i = client.post("/api/assistant", headers=h, json={"message": "How much inventory do we have and is the safety stock right?", "product_id": product}).json()
    assert i["intent"] == "inventory" and "days of cover" in i["reply"]
    assert ("context_cache", f"ctx::{product}::plan") in pull(client, h)


def test_business_rules_and_quality_hold(client):
    h, pid = login(client, "427190", "Alvarez")
    r = push(client, h, pid, "release_production_order", {"product_id": HEAT_SHIELD, "lines": [{"sku": CASTING, "qty": 1}]})
    assert r["status"] == "failed" and r["reason_code"] == "quality_hold"
    r = push(client, h, pid, "request_overtime_shift", {"product_id": HEAT_SHIELD, "hours": 8, "reason": "Catch up after containment"})
    assert r["status"] == "failed" and "hold" in r["summary"]
    r = push(client, h, pid, "set_standing_order", {"product_id": "FG00103001", "sku": SCREWS, "qty": 1, "cadence_weeks": 4})
    assert r["status"] == "completed" and r["item"]["cadence_weeks"] == 4
    plan = pull(client, h)[("production_plans", "plan::FG00103001")]
    assert not any(l["sku"] == SCREWS for l in plan["lines"])  # on a standing order: off the recommendations
    r = push(client, h, pid, "set_standing_order", {"product_id": "FG00103001", "sku": PCBA, "qty": 1})
    assert r["status"] == "failed" and "kanban" in r["summary"]
    r = push(client, h, pid, "release_production_order", {"product_id": PUMP, "lines": [{"sku": RESIN, "qty": 1}]})
    assert r["status"] == "failed" and "plant" in r["summary"]
    r = push(client, h, pid, "get_production_plan", {"product_id": "FG00103001"})
    assert r["status"] == "failed"


def test_line_check_marks_floor_walk_stop(client):
    h, pid = login(client, "427190", "Alvarez")
    r = push(client, h, pid, "log_line_check", {"product_id": "FG00103021", "outcome": "on_plan", "notes": "Test stand verified"})
    assert r["status"] == "completed"
    planner = pull(client, h)[("planners", f"planner::{pid}")]
    stop = next(s for s in planner["route"] if s["product_id"] == "FG00103021")
    assert stop["status"] == "checked" and stop["outcome"] == "on_plan"


def test_supplier_return_credits_the_material_budget(client):
    h, pid = login(client, "318552", "Walker")
    ok = push(client, h, pid, "release_production_order", {"product_id": "FG00102003", "lines": [{"sku": RESIN, "qty": 2}]})
    assert ok["status"] == "completed"
    before = pull(client, h)[("products", "product::FG00102003")]["auth"]["committed_usd"]
    r = push(client, h, pid, "create_supplier_return", {"product_id": "FG00102003", "production_order_id": ok["item"]["production_order_id"],
                                                        "sku": RESIN, "qty": 1, "reason": "Moisture above spec"})
    assert r["status"] == "completed" and r["cost_usd"] < 0
    after = pull(client, h)[("products", "product::FG00102003")]["auth"]["committed_usd"]
    assert after < before


def test_push_rejects_other_planners(client):
    h, pid = login(client, "427190", "Alvarez")
    r = client.post("/api/sync/push", headers=h, json={"docs": [{"collection": "production_orders", "id": "order::PLN1001::x",
                                                                 "doc": {"planner_id": "PLN1001", "tool": "release_production_order"}}]})
    assert r.status_code == 403


def test_databricks_offload_metrics(client):
    m = client.get("/api/status").json()["metrics"]
    assert m["warehouse_queries_avoided"] > 0 and m["estimated_dbx_spend_avoided_usd"] > 0
    assert m["production_orders_completed"] >= 3 and m["production_orders_queued_offline"] >= 1
