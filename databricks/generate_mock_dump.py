#!/usr/bin/env python3
"""Generate a mock Databricks daily export of demand and production-planning data.

Writes the exact layout a Databricks job produces with
`df.repartition(n).write.mode("overwrite").json(path)`:

    data/databricks-dump/dt=2026-11-13/
        part-00000-<uuid>-c000.json      # JSON Lines, one product-at-a-plant per line
        part-00001-<uuid>-c000.json
        ...
        _SUCCESS
        _manifest.json                   # row/byte counts + schema version (written by the notebook)

Each line is one finished product a plant builds - a gear pump on a CNC cell,
an enclosure on a molding line, a controller on an SMT line - joined with what
a production planner needs to decide this week's plan for it: 12 months of
sales history, the 8-week demand forecast, market signals, finished-goods
inventory and safety stock, the week's schedule and line capacity, material
cover, open planning alerts, the last production orders with their material
lines, weekly material usage, the material budget, and the scores the
demand-planning model (mfg-demand-planning-v3.1) produced in Databricks:
the recommended build quantity, recommended safety stock, days-to-stockout per
material, the materials to order with a confidence each, forward-buy
candidates and 30-day stockout risk.

Fully deterministic for a given --seed and --export-date, and standard
library only (runs anywhere Python 3.9+ does). Default target is 100 MiB.

    python databricks/generate_mock_dump.py                 # today, 100 MiB
    python databricks/generate_mock_dump.py --export-date 2026-11-13 --target-mb 100
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import random
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
from app import catalog as C  # noqa: E402

SCHEMA_VERSION = "item_demand_plan_v1"
MODEL_VERSION = "mfg-demand-planning-v3.1"

FIRST = ["Olivia", "Liam", "Emma", "Noah", "Ava", "Elijah", "Sophia", "James", "Isabella", "Lucas", "Mia", "Mason",
         "Amelia", "Ethan", "Harper", "Logan", "Evelyn", "Aiden", "Abigail", "Jackson", "Emily", "Sebastian", "Ella",
         "Mateo", "Scarlett", "Daniel", "Grace", "Henry", "Chloe", "Owen", "Camila", "Wyatt", "Aria", "Carter", "Riley",
         "Jayden", "Nora", "Gabriel", "Lily", "Julian", "Zoey", "Leo", "Hannah", "David", "Layla", "Joseph", "Priya",
         "Arjun", "Mei", "Hiroshi", "Fatima", "Omar", "Sofia", "Diego", "Valentina", "Carlos", "Lucia", "Andre", "Aaliyah",
         "Marcus", "Keisha", "Tyrone", "Ngozi", "Kwame", "Siobhan", "Declan", "Ingrid", "Lars", "Anika", "Ravi"]
LAST = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez",
        "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee",
        "Perez", "Thompson", "White", "Harris", "Sanchez", "Clark", "Ramirez", "Lewis", "Robinson", "Walker", "Young",
        "Allen", "King", "Wright", "Scott", "Torres", "Nguyen", "Hill", "Flores", "Green", "Adams", "Nelson", "Baker",
        "Hall", "Rivera", "Campbell", "Mitchell", "Carter", "Roberts", "Patel", "Shah", "Kim", "Chen", "Okafor",
        "O'Brien", "Kowalski", "Nakamura", "Fischer", "Rossi", "Bennett", "Natarajan", "Haddad", "Silva"]
ROLES = ["Line Supervisor", "Buyer", "Quality Engineer", "Customer Service Rep", "Maintenance Lead", "Materials Handler"]
CERTS = ["APICS CPIM", "Lean Six Sigma Green Belt", "SAP PP", "S&OP facilitation", "Kanban design"]
SHIFT_PATTERNS = {1: "1 shift, Mon-Fri 6:00-14:30", 2: "2 shifts, Mon-Fri 6:00-22:30", 3: "3 shifts, 24x5"}
LINE_NOTES = ["Shared with two other part families - sequence the changeover with the supervisor",
              "Line-side racks hold one shift of material; the rest stages in the warehouse lane",
              "First hour after a changeover runs at reduced rate", "Tooling is stored in the crib on the mezzanine",
              "Quality buy-off is needed on the first five pieces of every run",
              "Forklift access only from the east aisle", "", "", ""]
SEGMENTS = ["Stable Runners", "Demand Surge", "Seasonal Ramp", "Long-Lead Constrained", "Chronic Shortage",
            "New Product Ramp", "Slow Movers", "Promotion Driven"]
SUPPLIERS = ["Supplier A", "Supplier B", "Supplier C", "Supplier D"]
CUSTOMERS = ["Distributor North", "Distributor West", "OEM Tractor Co", "OEM Lift Systems", "Aftermarket Direct",
             "Industrial Supply Group", "Export - EMEA", "Export - LATAM"]
REVIEW_TIMES = ["08:30", "09:15", "10:00", "10:45", "11:30", "13:00", "13:45", "14:30", "15:15", "16:00"]
SOURCES = ["planner", "mrp_run", "auto_release", "customer_expedite"]
HOLD_REASONS = ["Customer complaint containment", "Engineering change pending approval", "First-article inspection overdue"]
DRIVERS_UP = ["Distributor orders up month over month", "OEM build schedule increased for next quarter",
              "Seasonal peak building", "Promotion running at two key accounts", "Competitor stock-out in the channel",
              "New dealer onboarding pulling stocking orders"]
DRIVERS_DOWN = ["Dealer inventory correction under way", "End-market index trending down",
                "Key account pushed its next release out", "Season winding down"]
DRIVERS_FLAT = ["Order book in line with forecast", "Steady replenishment pattern from distribution"]
# Market signals the model reads, by the material category they move.
MARKET = {
    "castings": ("Foundry lead-time index", "weeks quoted by iron foundries"),
    "resin": ("Resin price index (PA66 / ABS)", "contract price, 4-week change"),
    "electronics": ("Semiconductor lead-time index", "average quoted lead time"),
    "metals": ("Steel HRC and aluminum index", "spot price, 4-week change"),
    "motion": ("Bearing import lead time", "port-to-dock days"),
    "packaging": ("Corrugated price index", "4-week change"),
}

# Planning alert text per category - what the planner would see on the exception report.
ALERTS = {
    "castings": [("SUP-201", "Supplier delay: casting PO running {n} days late", "Order castings from warehouse stock and expedite the open PO"),
                 ("SUP-214", "Foundry cut next month's allocation", "Pull the next casting order forward")],
    "resin": [("SUP-310", "Resin lot on quality hold at the supplier", "Order replacement resin from warehouse stock"),
              ("MKT-322", "Resin price increase announced, effective in 2 weeks", "Order resin ahead of the price step")],
    "electronics": [("SUP-410", "Component allocation: board supply cut 30%", "Secure boards from the warehouse and confirm the allocation"),
                    ("SUP-434", "Harness supplier line down", "Order harnesses to cover the next two weeks")],
    "metals": [("MKT-501", "Steel index up {n}% this month", "Order bar and coil ahead of the next price step"),
               ("SUP-515", "Coil delivery slipped a week", "Order coil from warehouse stock to cover the run")],
    "motion": [("INV-601", "Bearing stock below safety stock", "Reorder bearings and seals"),
               ("SUP-620", "Seal supplier lead time up to 6 weeks", "Order seals now to cover the longer lead time")],
    "fasteners": [("INV-701", "Fastener kanban bin empty at line side", "Replenish fasteners from the warehouse")],
    "packaging": [("INV-801", "Packaging below two days of cover", "Order cartons and labels")],
    "tooling": [("TLG-901", "Tool life: inserts at 90% of expected life", "Order inserts and coolant before the run")],
    "schedule": [("DMD-110", "Demand spike: orders +{n}% vs forecast", "Raise this week's build to the recommended quantity"),
                 ("INV-210", "Finished-goods cover below safety stock", "Build the recommended quantity to restore cover"),
                 ("DMD-131", "Key account pulled an order in by a week", "Raise this week's build to cover the pull-in")],
}


def clamp(x: float, lo=0.01, hi=0.99) -> float:
    return round(max(lo, min(hi, x)), 3)


def nice(x: float) -> int:
    """Round a quantity the way a schedule would: to 10s under 1,000, 50s above."""
    step = 10 if x < 1000 else 50
    return int(max(step, round(x / step) * step))


def deep_update(base: dict, upd: dict):
    for k, v in upd.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_update(base[k], v)
        else:
            base[k] = v


class Gen:
    def __init__(self, export_date: dt.date, seed: int):
        self.d0 = export_date
        self.monday = export_date - dt.timedelta(days=export_date.weekday())
        self.r = random.Random(seed)
        self.product_seq = 200000
        self.planner_seq = 2000
        self.emp_seq = 500000
        self.order_seq = 0
        self.alert_seq = 40000

    # -- planners and plants ----------------------------------------------------
    def plant(self, base_code: str, variant: int, planner: dict | None = None) -> dict:
        base = C.PLANTS[base_code]
        pid = base_code if variant == 0 else f"{base_code}-{variant:02d}"
        name = base["name"] if variant == 0 else f"{base['name']} - Unit {variant + 1}"
        if planner is None:
            self.planner_seq += 1
            self.emp_seq += self.r.randint(3, 97)
            first, last = self.r.choice(FIRST), self.r.choice(LAST)
            planner = {"planner_id": f"PLN{self.planner_seq}", "employee_id": f"{self.emp_seq}", "first_name": first, "last_name": last,
                       "email": f"{first.lower()}.{last.lower().replace(chr(39), '')}@mfg.example.com",
                       "certifications": self.r.sample(CERTS, k=3)}
        return {"plant_id": pid, "plant_name": name, "city": base["city"], "state": base["state"], "mix": base["mix"],
                "planner": planner, "prefix": base_code.split("-")[-1], "line_seq": 0}

    # -- products -----------------------------------------------------------------
    def next_product_id(self):
        self.product_seq += 1
        return f"FG{self.product_seq:08d}"

    def contact(self, role=None):
        first, last = self.r.choice(FIRST), self.r.choice(LAST)
        return {"name": f"{first} {last}", "role": role or self.r.choice(ROLES),
                "phone": f"({self.r.randint(201, 989)}) 555-{self.r.randint(100, 199):04d}",
                "email": f"{first.lower()}.{last.lower().replace(chr(39), '')}@mfg.example.com"}

    def production_order(self, when: dt.date, usage: dict, cadence: int, weekly_units: float) -> dict:
        r = self.r
        self.order_seq += 1
        lines = []
        for sku, per_week in usage.items():
            expect = per_week * cadence / 7.0
            if r.random() < min(0.75, expect * 0.5 + 0.08):
                qty = max(1, int(round(expect * r.uniform(0.7, 1.6))))
                unit = C.material_cost(sku)
                lines.append({"sku": sku, "name": C.MATERIAL_BY_SKU[sku]["name"], "qty": qty, "unit_cost": unit,
                              "line_total": round(unit * qty, 2)})
        if not lines:
            sku = next(iter(usage))
            unit = C.material_cost(sku)
            lines = [{"sku": sku, "name": C.MATERIAL_BY_SKU[sku]["name"], "qty": 1, "unit_cost": unit, "line_total": unit}]
        total = round(sum(l["line_total"] for l in lines), 2)
        return {"production_order_id": f"PRO-{when:%y%m%d}-{10000 + self.order_seq % 90000}", "date": when.isoformat(), "status": "completed",
                "source": r.choices(SOURCES, weights=[40, 40, 15, 5])[0],
                "order_type": r.choices(C.ORDER_TYPES, weights=[62, 28, 6, 4])[0],
                "run_qty": nice(weekly_units * cadence / 7.0 * r.uniform(0.8, 1.2)),
                "line_count": len(lines), "lines": lines, "materials_usd": total, "total_usd": total,
                "backordered": [] if r.random() > 0.08 else [{"sku": lines[0]["sku"], "name": lines[0]["name"], "qty": 1}]}

    def alert(self, category: str, severity: str, days_ago: int = 0, pick: int | None = None, n: int | None = None) -> dict:
        self.alert_seq += 1
        code, text, action = ALERTS[category][pick if pick is not None else self.r.randrange(len(ALERTS[category]))]
        raised = dt.datetime.combine(self.d0 - dt.timedelta(days=days_ago), dt.time(self.r.randint(0, 5), self.r.randint(0, 59)))
        return {"alert_id": f"AL{self.alert_seq}", "severity": severity, "code": code,
                "text": text.format(n=n if n is not None else self.r.randint(4, 9)), "action": action,
                "category": category, "raised_at": raised.strftime("%Y-%m-%dT%H:%M:%SZ")}

    def demand(self, weekly: float, tier_def: dict) -> tuple[dict, list, float]:
        """12 months of sales, the 8-week forecast and the drivers behind it."""
        r = self.r
        amp, phase = r.uniform(0.0, 0.25), r.uniform(0, 2 * math.pi)
        yearly_trend = r.uniform(-0.15, 0.35)
        hist = []
        first = dt.date(self.d0.year, self.d0.month, 1)
        for i in range(12, 0, -1):
            y, m = first.year, first.month - i
            while m <= 0:
                y, m = y - 1, m + 12
            season = 1 + amp * math.sin(2 * math.pi * m / 12 + phase)
            level = 1 + yearly_trend * (12 - i) / 12.0
            hist.append({"month": f"{y}-{m:02d}", "units": int(weekly * 4.33 * season * level * r.uniform(0.9, 1.1))})
        now_level = weekly * (1 + yearly_trend)
        last4 = int(now_level * 4 * r.uniform(0.92, 1.08))
        step = r.choice([-0.02, 0.0, 0.0, 0.01, 0.02, 0.03, 0.05])
        bump = r.choice([1.0, 1.0, 1.0, 1.05, 1.12, 1.2, 0.92])
        band = r.uniform(0.08, 0.2)
        fc = []
        for w in range(8):
            season = 1 + amp * math.sin(2 * math.pi * (self.d0.month + w / 4.33) / 12 + phase) - amp * math.sin(2 * math.pi * self.d0.month / 12 + phase)
            u = now_level * bump * (1 + step * w) * season
            fc.append({"week_of": (self.monday + dt.timedelta(weeks=w)).isoformat(), "units": int(u),
                       "low": int(u * (1 - band)), "high": int(u * (1 + band))})
        next4 = sum(f["units"] for f in fc[:4])
        trend = round(100.0 * (next4 - last4) / max(1, last4), 1)
        pool = DRIVERS_UP if trend >= 6 else DRIVERS_DOWN if trend <= -6 else DRIVERS_FLAT
        return ({"uom": "units", "history_12mo": hist, "last_4wk_units": last4, "forecast_8wk": fc, "trend_pct": trend,
                 "open_orders_units": int(now_level * r.uniform(0.8, 2.6)),
                 "top_customers": r.sample(CUSTOMERS, k=3), "drivers": r.sample(pool, k=min(2, len(pool)))}, fc, trend)

    def product(self, plant: dict, ltype: str, overrides: dict | None = None, name: str | None = None,
                program: dict | None = None, due_today: bool = False) -> dict:
        r = self.r
        pid = self.next_product_id()
        lt = C.LINE_TYPES[ltype]
        usage_rate = {sku: round(m * r.uniform(0.6, 1.5), 2) for sku, m in lt["usage"].items() if r.random() < 0.9}
        if not usage_rate:
            usage_rate = dict(lt["usage"])
        tier = program["tier"] if program else r.choices(["C", "B", "A"], weights=[44, 36, 20])[0]
        tier_def = C.TIER_BY_CODE[tier]
        cadence = r.choice([14, 30, 30]) if tier == "C" else r.choice([7, 14, 14]) if tier == "B" else r.choice([7, 7, 14])
        unit_price = round(r.uniform(*lt["unit_price"]), 2)
        weekly = r.uniform(30000, 250000) / unit_price
        demand, fc, trend = self.demand(weekly, tier_def)
        next4 = sum(f["units"] for f in fc[:4])
        daily = max(1.0, next4 / 28.0)
        # last production orders, newest first
        history = []
        when = self.d0 - dt.timedelta(days=r.randint(2, cadence))
        for _ in range(r.randint(5, 12)):
            history.append(self.production_order(when, usage_rate, cadence, weekly))
            when -= dt.timedelta(days=int(cadence * r.uniform(0.7, 1.4)) or 1)
        last_order = history[0]
        last_by_sku = {}
        for o in history:
            for l in o["lines"]:
                last_by_sku.setdefault(l["sku"], o["date"])
        oldest = history[-1]["date"]
        # A material that isn't on a recent order is somewhere in the middle of its replenishment cycle.
        usage = {sku: {"per_week": m, "last_ordered": last_by_sku.get(sku, (self.d0 - dt.timedelta(
                           days=r.randint(1, max(2, int(7.0 / max(m, 0.05)))))).isoformat()),
                       "last_qty": next((l["qty"] for o in history for l in o["lines"] if l["sku"] == sku), 1)}
                 for sku, m in usage_rate.items()}
        spend_12 = round(sum(o["total_usd"] for o in history) * 365.0 / max(30, (self.d0 - dt.date.fromisoformat(oldest)).days), 2)
        limit = r.choice([50000, 75000, 100000, 150000, 250000]) if tier != "C" else r.choice([25000, 50000, 75000])
        committed = round(min(limit * r.uniform(0.05, 0.85), last_order["total_usd"] * r.uniform(0.6, 2.4) + r.uniform(0, 6000)), 2)
        hold_days = r.choices([0] * 70 + [21, 68], k=1)[0]
        on_hold = hold_days > 0
        # model: recommended materials are the ones closest to running out
        since = {sku: (self.d0 - dt.date.fromisoformat(w["last_ordered"])).days for sku, w in usage.items()}
        recommended, dts_map = [], {}
        for sku, w in usage.items():
            cover_days = 7.0 / max(w["per_week"], 0.05) * (w["last_qty"] or 1) * r.uniform(0.85, 1.2)
            dts = int(round(cover_days - since[sku]))
            dts_map[sku] = dts
            if dts <= 6:
                qty = max(1, int(round(w["per_week"] * cadence / 7.0 * 1.1)))
                conf = clamp(0.95 - max(0, dts) * 0.03 + r.gauss(0, 0.04))
                recommended.append({"sku": sku, "qty": qty, "confidence": conf,
                                    "reason": "Below reorder point" if dts <= 0 else "Projected to cross the reorder point"})
        recommended.sort(key=lambda s: -s["confidence"])
        recommended = recommended[:8]
        buy_pool = [p["sku"] for p in C.MATERIALS if p["category"] in ("castings", "metals", "resin", "electronics")
                    and p["sku"] not in usage_rate and p["unit_cost"] < 5000]
        forward = r.sample(buy_pool, k=min(2, len(buy_pool)))
        # inventory and this week's schedule
        cover_target = tier_def["cover_target_days"]
        cover = round(max(0.5, cover_target * r.uniform(0.35, 1.5)), 1)
        on_hand = int(daily * cover)
        safety = int(daily * cover_target * 0.4)
        accuracy = round(r.uniform(78, 96), 1)
        rec_safety = int(safety * (1 + (0.15 if trend >= 10 else 0) + (0.1 if accuracy < 85 else 0)))
        shifts = r.choice([1, 2, 2, 3])
        scheduled = nice(demand["last_4wk_units"] / 4.0 * r.uniform(0.9, 1.1))
        rec_build = nice(fc[0]["units"] + max(0.0, cover_target - cover) * daily * 0.5)
        uph = round(max(scheduled, rec_build * r.uniform(0.8, 1.0)) * r.uniform(1.05, 1.5) / (shifts * 40.0), 2)
        capacity = int(uph * shifts * 40)
        # alerts: materials about to run out usually already show on the exception report
        alerts = []
        seen_cat = set()
        for rec in recommended:
            cat = C.MATERIAL_BY_SKU[rec["sku"]]["category"]
            d = dts_map[rec["sku"]]
            if cat in ALERTS and cat not in seen_cat and d <= 0 and r.random() < 0.1:
                seen_cat.add(cat)
                alerts.append(self.alert(cat, "major" if d <= -2 and r.random() < 0.6 else "minor", days_ago=r.randint(0, 4)))
        if trend >= 15 and rec_build > scheduled * 1.1 and r.random() < 0.5:
            alerts.append(self.alert("schedule", "major" if cover < cover_target * 0.6 else "minor", days_ago=r.randint(0, 2), pick=0, n=int(trend)))
        elif on_hand < safety and r.random() < 0.3:
            alerts.append(self.alert("schedule", "minor", days_ago=r.randint(0, 3), pick=1))
        risks = {C.CATEGORIES[cat]["risk"]: clamp(0.1 + r.random() * 0.5) for cat in C.CATEGORIES}
        for rec in recommended:
            risks[C.CATEGORIES[C.MATERIAL_BY_SKU[rec["sku"]]["category"]]["risk"]] = clamp(0.55 + r.random() * 0.4)
        stockout = clamp(0.04 + 0.45 * max(0.0, 1 - cover / cover_target) + 0.1 * len(alerts) + 0.015 * len(recommended) + r.gauss(0, 0.04))
        coverage = []
        for i, (cat, cname) in enumerate(lt["coverage"]):
            risk = risks[C.CATEGORIES[cat]["risk"]]
            health = int(max(22, min(99, 100 - risk * 70 + r.gauss(0, 6))))
            coverage.append({"coverage_id": f"{pid}-C{i + 1:02d}", "category": cat, "name": cname, "supplier": r.choice(SUPPLIERS),
                             "days_cover": max(1, int(health / 100.0 * 24)), "health": health,
                             "status": "watch" if health < 60 else "ok"})
        cats = [c for c in {cv["category"] for cv in coverage} if c in MARKET]
        market = []
        for cat in r.sample(cats, k=min(2, len(cats))):
            chg = round(r.gauss(1.5, 4.0), 1)
            market.append({"signal": MARKET[cat][0], "category": cat, "direction": "up" if chg > 1 else "down" if chg < -1 else "flat",
                           "change_pct": chg, "note": MARKET[cat][1]})
        market.append({"signal": "Customer order intake", "category": "demand", "direction": "up" if trend > 3 else "down" if trend < -3 else "flat",
                       "change_pct": trend, "note": "next 4 weeks vs last 4 weeks"})
        next_review = self.d0 + dt.timedelta(days=r.randint(0, cadence))
        predicted = round(sum(C.material_cost(s["sku"]) * s["qty"] for s in recommended) * r.uniform(0.9, 1.3), 2)
        word = r.choice(lt["name_words"])
        code = f"{''.join(w[0] for w in word.split()[:2]).upper()}-{r.randint(100, 9899)}"
        plant["line_seq"] = plant.get("line_seq", 0) % 14 + 1
        delta = rec_build - scheduled
        build_reason = (f"forecast is {trend:+.0f}% against the last four weeks and cover is {cover:g} days against a {cover_target}-day target"
                        if abs(delta) >= 0.05 * scheduled else "the schedule already matches the forecast")
        row = {
            "export_date": self.d0.isoformat(),
            "schema_version": SCHEMA_VERSION,
            "product_id": pid,
            "product_name": name or f"{code} {word}",
            "product_code": code,
            "line_type": ltype, "line_type_name": lt["name"],
            "program": {"program_id": program["program_id"], "program_name": program["program_name"], "products": program["products"]} if program else None,
            "plant_id": plant["plant_id"], "plant_name": plant["plant_name"],
            "plant": {"city": plant["city"], "state": plant["state"]},
            "assigned_planner": dict(plant["planner"]),
            "contacts": [self.contact("Line Supervisor"), self.contact("Buyer")]
                        + ([self.contact("Quality Engineer")] if r.random() < 0.3 else []),
            "service": {"tier": tier, "tier_name": tier_def["name"], "fill_rate_target_pct": tier_def["fill_rate_target_pct"],
                        "cover_target_days": cover_target, "review_cadence_days": tier_def["review_cadence_days"],
                        "agreement_id": program["agreement_id"] if program else f"SA-{r.randint(100000, 999999)}",
                        "renewal_date": (self.d0 + dt.timedelta(days=r.randint(15, 360))).isoformat(),
                        "in_production_since": str(self.d0.year - r.randint(1, 18))},
            "auth": {"budget_limit_usd": float(limit), "committed_usd": committed, "period": f"{self.d0.year}-Q{(self.d0.month - 1) // 3 + 1}",
                     "hold_days": hold_days, "on_hold": on_hold,
                     "hold_reason": r.choice(HOLD_REASONS) if on_hold else None,
                     "temporary_extension_usd": 0},
            "line": {"line_id": f"{plant['prefix']}-L{plant['line_seq']:02d}", "name": f"Line {plant['line_seq']} - {lt['name']}",
                     "building": f"Building {r.randint(1, 4)}", "shift_pattern": SHIFT_PATTERNS[shifts],
                     "changeover_required": r.random() < 0.35, "changeover_min": r.choice([20, 30, 45, 60, 90]),
                     "notes": r.choice(LINE_NOTES), "warehouse": C.warehouse_for_state(plant["state"])},
            "review": {"last_review": (self.d0 - dt.timedelta(days=r.randint(1, cadence))).isoformat(), "cadence_days": cadence,
                       "next_review_date": next_review.isoformat(), "planned_time": r.choice(REVIEW_TIMES),
                       "signal_note": r.choice(C.SIGNAL_NOTES), "notes": ""},
            "demand": demand,
            "market": market,
            "inventory": {"on_hand_units": on_hand, "wip_units": int(scheduled * r.uniform(0.05, 0.3)), "safety_stock_units": safety,
                          "days_of_cover": cover, "target_cover_days": cover_target, "unit_price_usd": unit_price},
            "schedule": {"week_of": self.monday.isoformat(), "scheduled_units": scheduled, "capacity_units": capacity,
                         "units_per_hour": uph, "shifts": shifts, "overtime_hours_approved": 0},
            "coverage": coverage,
            "alerts": alerts,
            "kpis": {"fill_rate_30d_pct": round(min(99.9, tier_def["fill_rate_target_pct"] + r.gauss(-1.0, 2.5)), 1),
                     "schedule_adherence_pct": round(r.uniform(82, 99), 1), "forecast_accuracy_pct": accuracy,
                     "oee_pct": round(r.uniform(55, 88), 1)},
            "order_history": history,
            "usage": usage,
            "spend": {"last_12mo_usd": spend_12, "ytd_usd": round(spend_12 * 0.83, 2),
                      "avg_production_order_usd": round(sum(o["total_usd"] for o in history) / len(history), 2),
                      "production_orders_last_12mo": len(history), "category_mix": self._mix(history)},
            "standing_orders": [],
            "ml_scores": {
                "model_version": MODEL_VERSION, "scored_at": f"{self.d0.isoformat()}T04:48:11Z",
                **risks,
                "stockout_risk": stockout,
                "forecast_next_4wk_units": next4,
                "recommended_build_units": rec_build,
                "build_reason": build_reason,
                "recommended_safety_stock_units": rec_safety,
                "predicted_materials_cost_usd": predicted,
                "days_to_stockout": dts_map,
                "recommended_materials": recommended,
                "forward_buy_skus": forward,
                "segment": r.choice(SEGMENTS),
                "next_best_action": "clear_quality_hold" if on_hold else ("expedite" if alerts else ("replenish" if recommended else "hold_plan")),
            },
            "flags": {"make_to_order": r.random() < 0.2, "shared_line": r.random() < 0.5},
            "_ingest": {"source_table": "mfg_prod.planning360.item_demand_plan_daily",
                        "pipeline": "item_demand_plan_daily_export", "dbr_version": "16.4 LTS"},
        }
        if overrides:
            deep_update(row, overrides)
            # A hand-written recommendation should not sit next to a material ordered last week.
            for rec in row["ml_scores"]["recommended_materials"]:
                w = row["usage"].setdefault(rec["sku"], {"per_week": lt["usage"].get(rec["sku"], 0.5), "last_qty": rec["qty"]})
                age = max(9, int(7.0 / max(w["per_week"], 0.2) * (w.get("last_qty") or 1) * r.uniform(0.9, 1.15)))
                w["last_ordered"] = (self.d0 - dt.timedelta(days=age)).isoformat()
            # Keep the forecast consistent with a hand-written next-4-week number.
            want = row["ml_scores"]["forecast_next_4wk_units"]
            have = sum(f["units"] for f in row["demand"]["forecast_8wk"][:4]) or 1
            if want != have:
                k = want / have
                for f in row["demand"]["forecast_8wk"]:
                    f.update(units=int(f["units"] * k), low=int(f["low"] * k), high=int(f["high"] * k))
            row["schedule"]["capacity_units"] = int(row["schedule"]["units_per_hour"] * row["schedule"]["shifts"] * 40)
            row["line"]["shift_pattern"] = SHIFT_PATTERNS[row["schedule"]["shifts"]]
            # Past runs and monthly sales in proportion to the hand-written schedule and recent demand.
            for o in row["order_history"]:
                o["run_qty"] = nice(row["schedule"]["scheduled_units"] * r.uniform(0.85, 1.1))
            hist = row["demand"]["history_12mo"]
            k = row["demand"]["last_4wk_units"] * 1.04 / max(1, hist[-1]["units"])
            for h in hist:
                h["units"] = int(h["units"] * k)
            # ...and the plan engine leaves out materials ordered in the last three days.
            if (self.d0 - dt.date.fromisoformat(row["order_history"][0]["date"])).days <= 3:
                row["order_history"][0]["date"] = (self.d0 - dt.timedelta(days=4)).isoformat()
        # Each supply alert lands on the cover position in its category.
        for al in row["alerts"]:
            hit = next((cv for cv in row["coverage"] if cv["category"] == al["category"]), None)
            if hit:
                hit["status"], hit["health"] = "short", min(hit["health"], 38 if al["severity"] != "minor" else 52)
                hit["days_cover"] = min(hit["days_cover"], 3 if al["severity"] != "minor" else 6)
        if due_today:
            row["review"].update(next_review_date=self.d0.isoformat(), cadence_days=7,
                                 last_review=(self.d0 - dt.timedelta(days=7)).isoformat())
        return row

    def _mix(self, history):
        tot = {}
        for o in history:
            for l in o["lines"]:
                cat = C.MATERIAL_BY_SKU[l["sku"]]["category"]
                tot[cat] = tot.get(cat, 0) + l["line_total"]
        s = sum(tot.values()) or 1
        return {k: round(v / s, 3) for k, v in sorted(tot.items(), key=lambda kv: -kv[1])}

    def plant_rows(self, plant: dict, n: int):
        types = list(C.LINE_TYPES)
        for _ in range(n):
            yield self.product(plant, self.r.choices(types, weights=plant["mix"])[0])

    def _fill(self, rows: list, plant: dict, n: int):
        d0 = self.d0.isoformat()
        for a in self.plant_rows(plant, n):
            if a["review"]["next_review_date"] == d0:
                a["review"]["next_review_date"] = (self.d0 + dt.timedelta(days=1)).isoformat()
            rows.append(a)

    # -- personas -------------------------------------------------------------------
    def personas(self):
        """Hand-built demo planners and the stops on their floor walk today.
        Their `next_review_date` is the export date, so the story is the same
        in every daily export."""
        rows = []
        ok = {"hold_days": 0, "on_hold": False, "hold_reason": None}

        def sched(scheduled, uph, shifts):
            return {"scheduled_units": scheduled, "units_per_hour": uph, "shifts": shifts}

        # 1. Maya Chen - Peabody Fluid Power Plant.
        maya = {"planner_id": "PLN1001", "employee_id": "204817", "first_name": "Maya", "last_name": "Chen",
                "email": "maya.chen@mfg.example.com", "certifications": ["APICS CPIM", "S&OP facilitation", "SAP PP"]}
        p1 = self.plant("PLT-MA-PEA", 0, maya)
        rows.append(self.product(p1, "cnc_machining", name="HP-2200 Hydraulic Gear Pump", due_today=True, overrides={
            "product_id": "FG00101001", "product_code": "HP-2200",
            "contacts": [{"name": "Frank Molloy", "role": "Line Supervisor", "phone": "(978) 555-0142", "email": "frank.molloy@mfg.example.com"},
                         {"name": "Dee Alvarez", "role": "Buyer", "phone": "(978) 555-0143", "email": "dee.alvarez@mfg.example.com"}],
            "service": {"tier": "A", "tier_name": "A item", "fill_rate_target_pct": 98.0, "cover_target_days": 10, "review_cadence_days": 7,
                        "agreement_id": "SA-448120", "renewal_date": (self.d0 + dt.timedelta(days=95)).isoformat(), "in_production_since": "2011"},
            "auth": {"budget_limit_usd": 100000.0, "committed_usd": 84804.00, **ok},
            "line": {"line_id": "PEA-L03", "name": "Line 3 - CNC Cell B", "building": "Building 2", "shift_pattern": SHIFT_PATTERNS[2],
                     "changeover_required": False, "changeover_min": 45,
                     "notes": "Castings stage in the foundry bay next to the cell; the supervisor's desk is inside the cell guarding",
                     "warehouse": "WH-NE"},
            "review": {"planned_time": "08:30", "signal_note": "Foundry bay - handhelds drop off the network"},
            "demand": {"last_4wk_units": 3480, "trend_pct": 37.9, "open_orders_units": 2960,
                       "top_customers": ["Distributor North", "OEM Lift Systems", "Aftermarket Direct"],
                       "drivers": ["Distributor orders running 38% over forecast for three weeks", "OEM Lift Systems raised its Q1 build schedule"]},
            "market": [{"signal": "Foundry lead-time index", "category": "castings", "direction": "up", "change_pct": 12.0, "note": "weeks quoted by iron foundries"},
                       {"signal": "Steel HRC and aluminum index", "category": "metals", "direction": "up", "change_pct": 6.1, "note": "spot price, 4-week change"},
                       {"signal": "Customer order intake", "category": "demand", "direction": "up", "change_pct": 37.9, "note": "next 4 weeks vs last 4 weeks"}],
            "inventory": {"on_hand_units": 720, "wip_units": 140, "safety_stock_units": 480, "days_of_cover": 4.2, "target_cover_days": 10,
                          "unit_price_usd": 412.00},
            "schedule": sched(900, 16.25, 2),
            "alerts": [self.alert("schedule", "major", days_ago=1, pick=0, n=38), self.alert("castings", "major", days_ago=3, pick=0, n=6)],
            "kpis": {"fill_rate_30d_pct": 94.6, "schedule_adherence_pct": 96.2, "forecast_accuracy_pct": 81.4, "oee_pct": 78.5},
            "ml_scores": {"segment": "Demand Surge", "next_best_action": "expedite", "stockout_risk": 0.62,
                          "forecast_next_4wk_units": 4800, "recommended_build_units": 1200, "recommended_safety_stock_units": 620,
                          "build_reason": "distributor orders are 38% over forecast and cover is 4.2 days against a 10-day target",
                          "castings_shortage_risk": 0.91, "motion_shortage_risk": 0.7, "metals_shortage_risk": 0.62,
                          "fasteners_shortage_risk": 0.45, "packaging_shortage_risk": 0.5, "tooling_wear_score": 0.55,
                          "recommended_materials": [
                              {"sku": "MF-CST-1204", "qty": 4, "confidence": 0.96, "reason": "Open PO is 6 days late; covers 160 pumps"},
                              {"sku": "MF-MOT-5101", "qty": 1, "confidence": 0.93, "reason": "Usage up with the higher build"},
                              {"sku": "MF-MTL-4101", "qty": 1, "confidence": 0.88, "reason": "Steel index rising - order before the price step"},
                              {"sku": "MF-MOT-5110", "qty": 1, "confidence": 0.84, "reason": "At reorder point"},
                              {"sku": "MF-FST-7110", "qty": 1, "confidence": 0.74, "reason": "Due with this run"},
                              {"sku": "MF-PKG-8162", "qty": 1, "confidence": 0.7, "reason": "Due with this run"},
                              {"sku": "MF-TLG-9101", "qty": 1, "confidence": 0.48, "reason": "Insert life trending down"},
                              {"sku": "MF-PKG-8110", "qty": 1, "confidence": 0.4, "reason": "Slow mover; cover is adequate"}],
                          "days_to_stockout": {"MF-CST-1204": 1, "MF-MOT-5101": 4, "MF-MTL-4101": 6, "MF-MOT-5110": 9,
                                               "MF-FST-7110": 12, "MF-PKG-8162": 14, "MF-TLG-9101": 19, "MF-PKG-8110": 21},
                          "forward_buy_skus": ["MF-CST-1260", "MF-MOT-5140"], "predicted_materials_cost_usd": 18900.0},
        }))
        rows.append(self.product(p1, "smt_electronics", name="MC-480 Motor Controller", due_today=True, overrides={
            "product_id": "FG00101002", "product_code": "MC-480",
            "service": {"tier": "KEY", "tier_name": "Key account", "fill_rate_target_pct": 99.5, "cover_target_days": 7,
                        "review_cadence_days": 7, "agreement_id": "SA-391877"},
            "auth": {"budget_limit_usd": 250000.0, "committed_usd": 64101.50, **ok},
            "line": {"line_id": "PEA-L07", "name": "Line 7 - SMT", "building": "Building 3", "warehouse": "WH-NE", "changeover_required": False,
                     "notes": "ESD area - sign in at the smock station; boards are kitted on the mezzanine"},
            "review": {"planned_time": "10:00", "signal_note": "Mezzanine stores - dead zone"},
            "inventory": {"on_hand_units": 410, "safety_stock_units": 300, "days_of_cover": 3.4, "target_cover_days": 7},
            "schedule": sched(820, 11.5, 2),
            "alerts": [self.alert("electronics", "major", days_ago=0, pick=0)],
            "kpis": {"fill_rate_30d_pct": 97.8, "schedule_adherence_pct": 91.0, "forecast_accuracy_pct": 88.9, "oee_pct": 71.2},
            "ml_scores": {"segment": "Long-Lead Constrained", "next_best_action": "expedite", "stockout_risk": 0.34,
                          "forecast_next_4wk_units": 3360, "recommended_build_units": 850, "recommended_safety_stock_units": 360,
                          "build_reason": "the OEM schedule is steady but board supply is on allocation",
                          "electronics_shortage_risk": 0.88, "packaging_shortage_risk": 0.66,
                          "recommended_materials": [
                              {"sku": "MF-ELC-3101", "qty": 2, "confidence": 0.94, "reason": "Supplier allocation cut 30%; 84-day lead time"},
                              {"sku": "MF-ELC-3160", "qty": 2, "confidence": 0.9, "reason": "Feeders at minimum"},
                              {"sku": "MF-PKG-8110", "qty": 1, "confidence": 0.78, "reason": "Due with this run"},
                              {"sku": "MF-PKG-8150", "qty": 1, "confidence": 0.7, "reason": "Due with this run"}],
                          "days_to_stockout": {"MF-ELC-3101": 0, "MF-ELC-3160": 5, "MF-PKG-8110": 10, "MF-PKG-8150": 12},
                          "forward_buy_skus": ["MF-ELC-3120"], "predicted_materials_cost_usd": 8600.0},
        }))
        rows.append(self.product(p1, "cnc_machining", name="VB-75 Valve Block", due_today=True, overrides={
            "product_id": "FG00101003", "product_code": "VB-75",
            "service": {"tier": "A", "tier_name": "A item", "fill_rate_target_pct": 98.0, "cover_target_days": 10, "review_cadence_days": 7,
                        "agreement_id": "SA-520034"},
            "auth": {"budget_limit_usd": 75000.0, "committed_usd": 8600.0, **ok},
            "line": {"line_id": "PEA-L04", "name": "Line 4 - CNC Cell C", "building": "Building 2", "warehouse": "WH-NE",
                     "changeover_required": True, "changeover_min": 60, "notes": "Shares fixtures with the manifold family"},
            "review": {"planned_time": "11:30", "signal_note": "Assembly hall, weak Wi-Fi"},
            "schedule": sched(1400, 22.0, 2),
            "alerts": [self.alert("metals", "major", days_ago=2, pick=0, n=6)],
            "ml_scores": {"segment": "Seasonal Ramp", "stockout_risk": 0.41, "next_best_action": "expedite",
                          "forecast_next_4wk_units": 6000, "recommended_build_units": 1550, "recommended_safety_stock_units": 900,
                          "build_reason": "the spring ramp starts three weeks earlier than last year",
                          "metals_shortage_risk": 0.9, "castings_shortage_risk": 0.6,
                          "recommended_materials": [
                              {"sku": "MF-MTL-4101", "qty": 2, "confidence": 0.95, "reason": "Steel index up 6% - order before the price step"},
                              {"sku": "MF-CST-1210", "qty": 1, "confidence": 0.9, "reason": "Covers the ramp build"},
                              {"sku": "MF-TLG-9101", "qty": 1, "confidence": 0.78, "reason": "Insert life trending down"},
                              {"sku": "MF-PKG-8162", "qty": 1, "confidence": 0.62, "reason": "Due with this run"}],
                          "days_to_stockout": {"MF-MTL-4101": 2, "MF-CST-1210": 3, "MF-TLG-9101": 11, "MF-PKG-8162": 13},
                          "forward_buy_skus": ["MF-MTL-4110"], "predicted_materials_cost_usd": 6100.0},
        }))
        rows.append(self.product(p1, "welding_fab", name="RS-12 Reservoir Tank", due_today=True, overrides={
            "product_id": "FG00101004", "product_code": "RS-12",
            "service": {"tier": "B", "tier_name": "B item", "fill_rate_target_pct": 95.0, "cover_target_days": 14, "review_cadence_days": 14,
                        "agreement_id": "SA-611209", "renewal_date": (self.d0 + dt.timedelta(days=28)).isoformat()},
            "auth": {"budget_limit_usd": 50000.0, "committed_usd": 3100.0, **ok},
            "line": {"line_id": "PEA-L09", "name": "Line 9 - Weld Cell", "building": "Building 1", "warehouse": "WH-NE",
                     "changeover_required": False, "notes": "Robot cell; leak test is the bottleneck"},
            "review": {"planned_time": "13:30", "signal_note": "Good signal"},
            "alerts": [],
            "ml_scores": {"segment": "Stable Runners", "stockout_risk": 0.14, "next_best_action": "replenish",
                          "metals_shortage_risk": 0.72, "tooling_wear_score": 0.58,
                          "recommended_materials": [
                              {"sku": "MF-MTL-4130", "qty": 1, "confidence": 0.9, "reason": "Projected to cross the reorder point"},
                              {"sku": "MF-TLG-9120", "qty": 1, "confidence": 0.8, "reason": "Due with this run"},
                              {"sku": "MF-FST-7150", "qty": 1, "confidence": 0.77, "reason": "Kanban bin at one card"}],
                          "days_to_stockout": {"MF-MTL-4130": 5, "MF-TLG-9120": 8, "MF-FST-7150": 10},
                          "forward_buy_skus": ["MF-MTL-4124"], "predicted_materials_cost_usd": 2700.0},
        }))
        rows.append(self.product(p1, "packaging_kitting", name="FK-9 Filter Service Kit", due_today=True, overrides={
            "product_id": "FG00101005", "product_code": "FK-9",
            "service": {"tier": "C", "tier_name": "C item", "fill_rate_target_pct": 92.0, "cover_target_days": 20, "review_cadence_days": 30,
                        "agreement_id": "SA-702881", "renewal_date": (self.d0 + dt.timedelta(days=30)).isoformat()},
            "auth": {"budget_limit_usd": 25000.0, "committed_usd": 0.0, **ok},
            "line": {"line_id": "PEA-L12", "name": "Line 12 - Kitting", "building": "Building 4", "warehouse": "WH-NE",
                     "changeover_required": False, "notes": "Runs on the afternoon shift only"},
            "review": {"planned_time": "15:00", "signal_note": "Good signal"},
            "alerts": [],
            "ml_scores": {"segment": "Slow Movers", "stockout_risk": 0.08, "next_best_action": "replenish",
                          "recommended_materials": [
                              {"sku": "MF-PKG-8162", "qty": 1, "confidence": 0.9, "reason": "Due with this run"},
                              {"sku": "MF-PKG-8130", "qty": 1, "confidence": 0.86, "reason": "Label stock at reorder point"},
                              {"sku": "MF-MOT-5120", "qty": 1, "confidence": 0.6, "reason": "Due with this run"}],
                          "days_to_stockout": {"MF-PKG-8162": 4, "MF-PKG-8130": 7, "MF-MOT-5120": 14},
                          "forward_buy_skus": [], "predicted_materials_cost_usd": 510.0},
        }))
        self._fill(rows, p1, 52)

        # 2. Devon Walker - Kingston Molding Plant: a press shop with no Wi-Fi and a line short of capacity.
        devon = {"planner_id": "PLN1002", "employee_id": "318552", "first_name": "Devon", "last_name": "Walker",
                 "email": "devon.walker@mfg.example.com", "certifications": ["Lean Six Sigma Green Belt", "Kanban design", "SAP PP"]}
        p2 = self.plant("PLT-NY-KGN", 0, devon)
        rows.append(self.product(p2, "injection_molding", name="EH-300 Enclosure Housing", due_today=True, overrides={
            "product_id": "FG00102001", "product_code": "EH-300",
            "service": {"tier": "KEY", "tier_name": "Key account", "fill_rate_target_pct": 99.5, "cover_target_days": 7,
                        "review_cadence_days": 7, "agreement_id": "SA-377410"},
            "auth": {"budget_limit_usd": 150000.0, "committed_usd": 31200.0, **ok},
            "line": {"line_id": "KGN-L02", "name": "Line 2 - 550-ton press", "building": "Building 1", "shift_pattern": SHIFT_PATTERNS[2],
                     "changeover_required": True, "changeover_min": 90, "warehouse": "WH-NY",
                     "notes": "Mold change takes a crane and two setters; the dryer needs four hours' notice"},
            "review": {"planned_time": "08:30", "signal_note": "Press shop - steel building, no Wi-Fi"},
            "demand": {"last_4wk_units": 16400, "trend_pct": 24.4, "open_orders_units": 14800,
                       "drivers": ["Key account pulled its launch build in by two weeks", "Promotion running at two key accounts"]},
            "inventory": {"on_hand_units": 2200, "safety_stock_units": 2000, "days_of_cover": 3.0, "target_cover_days": 7},
            "schedule": sched(4200, 57.5, 2),
            "alerts": [self.alert("resin", "major", days_ago=1, pick=0), self.alert("schedule", "minor", days_ago=1, pick=2)],
            "kpis": {"fill_rate_30d_pct": 97.1, "schedule_adherence_pct": 89.4, "forecast_accuracy_pct": 84.0, "oee_pct": 74.0},
            "ml_scores": {"segment": "Demand Surge", "stockout_risk": 0.57, "next_best_action": "expedite",
                          "forecast_next_4wk_units": 20400, "recommended_build_units": 5200, "recommended_safety_stock_units": 2300,
                          "build_reason": "the key account pulled its launch in and cover is 3 days against a 7-day target",
                          "resin_shortage_risk": 0.9, "fasteners_shortage_risk": 0.75, "packaging_shortage_risk": 0.6,
                          "recommended_materials": [
                              {"sku": "MF-RSN-2101", "qty": 2, "confidence": 0.95, "reason": "Supplier lot on quality hold; dryer is on its last gaylord"},
                              {"sku": "MF-RSN-2140", "qty": 1, "confidence": 0.9, "reason": "Usage up with the higher build"},
                              {"sku": "MF-FST-7130", "qty": 1, "confidence": 0.84, "reason": "Insert bowl feeder at minimum"},
                              {"sku": "MF-PKG-8162", "qty": 1, "confidence": 0.72, "reason": "Due with this run"}],
                          "days_to_stockout": {"MF-RSN-2101": 1, "MF-RSN-2140": 5, "MF-FST-7130": 6, "MF-PKG-8162": 12},
                          "forward_buy_skus": ["MF-RSN-2110"], "predicted_materials_cost_usd": 5300.0},
        }))
        rows.append(self.product(p2, "injection_molding", name="FS-140 Fan Shroud", due_today=True, overrides={
            "product_id": "FG00102002", "product_code": "FS-140",
            "service": {"tier": "A", "tier_name": "A item", "fill_rate_target_pct": 98.0, "cover_target_days": 10, "review_cadence_days": 7,
                        "agreement_id": "SA-299150"},
            "auth": {"budget_limit_usd": 150000.0, "committed_usd": 28900.0, **ok},
            "line": {"line_id": "KGN-L05", "name": "Line 5 - 300-ton press", "building": "Building 1", "warehouse": "WH-NY", "changeover_required": False},
            "review": {"planned_time": "11:00", "signal_note": "Mezzanine stores - dead zone"},
            "alerts": [self.alert("resin", "minor", days_ago=2, pick=1)],
            "ml_scores": {"segment": "Chronic Shortage", "stockout_risk": 0.22, "next_best_action": "replenish",
                          "resin_shortage_risk": 0.86,
                          "recommended_materials": [
                              {"sku": "MF-RSN-2110", "qty": 2, "confidence": 0.94, "reason": "Price increase in 2 weeks - order ahead"},
                              {"sku": "MF-RSN-2140", "qty": 1, "confidence": 0.82, "reason": "Projected to cross the reorder point"},
                              {"sku": "MF-PKG-8121", "qty": 1, "confidence": 0.75, "reason": "Due with this run"}],
                          "days_to_stockout": {"MF-RSN-2110": 3, "MF-RSN-2140": 8, "MF-PKG-8121": 12},
                          "forward_buy_skus": ["MF-RSN-2122"], "predicted_materials_cost_usd": 3700.0},
        }))
        rows.append(self.product(p2, "injection_molding", name="CB-22 Connector Body", due_today=True, overrides={
            "product_id": "FG00102003", "product_code": "CB-22",
            "service": {"tier": "C", "tier_name": "C item", "fill_rate_target_pct": 92.0, "cover_target_days": 20, "review_cadence_days": 30,
                        "agreement_id": "SA-805567"},
            "auth": {"budget_limit_usd": 50000.0, "committed_usd": 1900.0, **ok},
            "line": {"line_id": "KGN-L08", "name": "Line 8 - 110-ton press", "building": "Building 2", "warehouse": "WH-NY", "changeover_required": True},
            "review": {"planned_time": "13:30", "signal_note": "Good signal"},
            "alerts": [],
            "ml_scores": {"segment": "Stable Runners", "stockout_risk": 0.1, "next_best_action": "replenish",
                          "recommended_materials": [
                              {"sku": "MF-RSN-2101", "qty": 2, "confidence": 0.9, "reason": "Due with this run"},
                              {"sku": "MF-TLG-9140", "qty": 1, "confidence": 0.82, "reason": "Last case opened"}],
                          "days_to_stockout": {"MF-RSN-2101": 5, "MF-TLG-9140": 6},
                          "forward_buy_skus": ["MF-RSN-2122"], "predicted_materials_cost_usd": 4800.0},
        }))
        rows.append(self.product(p2, "injection_molding", name="HG-8 Handle Grip", due_today=True, overrides={
            "product_id": "FG00102004", "product_code": "HG-8",
            "service": {"tier": "B", "tier_name": "B item", "fill_rate_target_pct": 95.0, "cover_target_days": 14, "review_cadence_days": 14,
                        "agreement_id": "SA-460239"},
            "auth": {"budget_limit_usd": 75000.0, "committed_usd": 22000.0, **ok},
            "line": {"line_id": "KGN-L11", "name": "Line 11 - two-shot press", "building": "Building 2", "warehouse": "WH-NY", "changeover_required": False},
            "review": {"planned_time": "15:30", "signal_note": "Good signal"},
            "alerts": [],
            "ml_scores": {"segment": "Promotion Driven", "stockout_risk": 0.12, "next_best_action": "replenish",
                          "recommended_materials": [
                              {"sku": "MF-RSN-2150", "qty": 1, "confidence": 0.94, "reason": "At reorder point"},
                              {"sku": "MF-RSN-2140", "qty": 1, "confidence": 0.87, "reason": "Projected to cross the reorder point"},
                              {"sku": "MF-PKG-8162", "qty": 1, "confidence": 0.8, "reason": "Due with this run"}],
                          "days_to_stockout": {"MF-RSN-2150": 4, "MF-RSN-2140": 9, "MF-PKG-8162": 11},
                          "forward_buy_skus": ["MF-RSN-2110"], "predicted_materials_cost_usd": 720.0},
        }))
        self._fill(rows, p2, 44)

        # 3. Sofia Alvarez - Fort Worth Assembly Plant, with a 12-product OEM program on one key-account contract.
        sofia = {"planner_id": "PLN1003", "employee_id": "427190", "first_name": "Sofia", "last_name": "Alvarez",
                 "email": "sofia.alvarez@mfg.example.com", "certifications": ["APICS CPIM", "Lean Six Sigma Green Belt", "S&OP facilitation"]}
        p3 = self.plant("PLT-TX-FTW", 0, sofia)
        program = {"program_id": "PRG-4471", "program_name": "Lone Star Tractor OEM Program", "products": 12, "tier": "KEY",
                   "agreement_id": "SA-PRG-4471"}
        words = ["Hydraulic Power Unit", "Actuator Assembly", "Pump Assembly", "Control Valve Assembly", "Gearbox Assembly", "Steering Unit"]
        for n in range(12):
            on_route = n < 3
            ov = {"product_id": f"FG001030{n + 1:02d}", "product_code": f"LST-{n + 401}",
                  "service": {"tier": "KEY", "tier_name": "Key account", "fill_rate_target_pct": 99.5, "cover_target_days": 7,
                              "review_cadence_days": 7, "agreement_id": "SA-PRG-4471"},
                  "auth": {"budget_limit_usd": 50000.0, **ok},
                  "line": {"warehouse": "WH-SC"}}
            if on_route:
                deep_update(ov, {"review": {"planned_time": ["08:30", "10:15", "12:00"][n],
                                 "signal_note": ["Cold dock - no coverage past the doors", "Good signal", "Assembly hall, weak Wi-Fi"][n]}})
            else:
                ov["review"] = {"next_review_date": (self.d0 + dt.timedelta(days=1 + n % 9)).isoformat()}
            if n == 0:
                ov["auth"]["committed_usd"] = 9200.0
                ov["schedule"] = sched(640, 10.0, 2)
                ov["alerts"] = [self.alert("electronics", "minor", days_ago=1, pick=1)]
                ov["ml_scores"] = {"stockout_risk": 0.31, "next_best_action": "expedite", "electronics_shortage_risk": 0.85,
                                   "forecast_next_4wk_units": 2720, "recommended_build_units": 700,
                                   "build_reason": "the OEM raised next month's line rate",
                                   "recommended_materials": [
                                       {"sku": "MF-ELC-3133", "qty": 1, "confidence": 0.95, "reason": "Harness supplier line is down"},
                                       {"sku": "MF-FST-7110", "qty": 1, "confidence": 0.86, "reason": "Due with this run"},
                                       {"sku": "MF-PKG-8162", "qty": 1, "confidence": 0.8, "reason": "Due with this run"}],
                                   "days_to_stockout": {"MF-ELC-3133": 2, "MF-FST-7110": 6, "MF-PKG-8162": 9},
                                   "forward_buy_skus": ["MF-ELC-3150"], "predicted_materials_cost_usd": 1300.0}
            rows.append(self.product(p3, "final_assembly", name=f"LST-{n + 401} {words[n % len(words)]}", program=program,
                                     overrides=ov, due_today=on_route))
        rows.append(self.product(p3, "stamping", name="HS-612 Heat Shield", due_today=True, overrides={
            "product_id": "FG00103020", "product_code": "HS-612",
            "service": {"tier": "C", "tier_name": "C item", "fill_rate_target_pct": 92.0, "cover_target_days": 20, "review_cadence_days": 30,
                        "agreement_id": "SA-900147"},
            "auth": {"budget_limit_usd": 50000.0, "committed_usd": 19805.50, "hold_days": 68, "on_hold": True,
                     "hold_reason": "Customer complaint containment - PPAP re-approval pending"},
            "line": {"line_id": "FTW-L04", "name": "Line 4 - 400-ton press", "building": "Building 2", "warehouse": "WH-SC",
                     "changeover_required": False, "notes": "Containment cage at the end of the press; nothing ships until Quality releases it"},
            "review": {"planned_time": "13:30", "signal_note": "Good signal"},
            "alerts": [self.alert("metals", "minor", days_ago=9, pick=1)],
            "ml_scores": {"segment": "Chronic Shortage", "stockout_risk": 0.71, "next_best_action": "clear_quality_hold",
                          "recommended_materials": [{"sku": "MF-MTL-4110", "qty": 1, "confidence": 0.9, "reason": "Below reorder point"},
                                                    {"sku": "MF-FST-7150", "qty": 2, "confidence": 0.86, "reason": "Projected to cross the reorder point"}],
                          "days_to_stockout": {"MF-MTL-4110": 0, "MF-FST-7150": 5}, "forward_buy_skus": [], "predicted_materials_cost_usd": 5200.0},
        }))
        rows.append(self.product(p3, "final_assembly", name="CV-31 Control Valve Assembly", due_today=True, overrides={
            "product_id": "FG00103021", "product_code": "CV-31",
            "service": {"tier": "A", "tier_name": "A item", "fill_rate_target_pct": 98.0, "cover_target_days": 10, "review_cadence_days": 7,
                        "agreement_id": "SA-512240"},
            "auth": {"budget_limit_usd": 150000.0, "committed_usd": 14200.0, **ok},
            "line": {"line_id": "FTW-L06", "name": "Line 6 - Valve assembly", "building": "Building 3", "warehouse": "WH-SC", "changeover_required": False},
            "review": {"planned_time": "15:00", "signal_note": "Good signal"},
            "alerts": [],
            "ml_scores": {"segment": "Stable Runners", "stockout_risk": 0.05, "next_best_action": "replenish",
                          "recommended_materials": [{"sku": "MF-MOT-5131", "qty": 2, "confidence": 0.96, "reason": "Due with this run"},
                                                    {"sku": "MF-FST-7101", "qty": 1, "confidence": 0.88, "reason": "Due with this run"},
                                                    {"sku": "MF-PKG-8150", "qty": 1, "confidence": 0.62, "reason": "Projected to cross the reorder point"}],
                          "days_to_stockout": {"MF-MOT-5131": 2, "MF-FST-7101": 9, "MF-PKG-8150": 14},
                          "forward_buy_skus": ["MF-ELC-3150"], "predicted_materials_cost_usd": 1160.0},
        }))
        self._fill(rows, p3, 38)
        return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--export-date", default=dt.date.today().isoformat())
    ap.add_argument("--target-mb", type=float, default=100.0, help="approximate total size in MiB")
    ap.add_argument("--parts", type=int, default=4, help="number of part files (keep each under GitHub's 50MB warning)")
    ap.add_argument("--seed", type=int, default=20261113)
    ap.add_argument("--out", default=os.path.join(HERE, "..", "data", "databricks-dump"))
    args = ap.parse_args(argv)

    export_date = dt.date.fromisoformat(args.export_date)
    g = Gen(export_date, args.seed)
    target = int(args.target_mb * 1024 * 1024)
    per_part = target // args.parts + 1
    out_dir = os.path.abspath(os.path.join(args.out, f"dt={export_date.isoformat()}"))
    os.makedirs(out_dir, exist_ok=True)
    for f in os.listdir(out_dir):
        if f.startswith("part-") or f in ("_SUCCESS", "_manifest.json"):
            os.remove(os.path.join(out_dir, f))

    job_uuid = uuid.UUID(int=g.r.getrandbits(128))
    written, rows, plants = 0, 0, 3
    files = []
    pending = g.personas()
    demo = ("PLT-MA-PEA", "PLT-NY-KGN", "PLT-TX-FTW")
    base_codes = [c for c in C.PLANTS if c not in demo] + list(C.PLANTS)
    variant = {c: 0 for c in C.PLANTS}
    for part in range(args.parts):
        name = f"part-{part:05d}-{job_uuid}-c000.json"
        path = os.path.join(out_dir, name)
        part_bytes, part_rows = 0, 0
        with open(path, "w", encoding="utf-8") as fh:
            while part_bytes < per_part and written < target:
                if not pending:
                    code = base_codes[plants % len(base_codes)]
                    variant[code] += 1
                    plant = g.plant(code, variant[code])
                    pending = list(g.plant_rows(plant, g.r.randint(45, 75)))
                    plants += 1
                line = json.dumps(pending.pop(0), separators=(",", ":"), ensure_ascii=False) + "\n"
                b = len(line.encode("utf-8"))
                fh.write(line)
                part_bytes += b
                written += b
                part_rows += 1
        rows += part_rows
        files.append({"file": name, "rows": part_rows, "bytes": part_bytes})
        print(f"  {name}  {part_rows:>7,} rows  {part_bytes / 1048576:6.1f} MiB", flush=True)

    manifest = {
        "table": "mfg_prod.planning360.item_demand_plan_daily",
        "export_date": export_date.isoformat(), "schema_version": SCHEMA_VERSION, "model_version": MODEL_VERSION,
        "format": "jsonl", "rows": rows, "plants": plants, "bytes": written, "files": files,
        "generator": "databricks/generate_mock_dump.py", "seed": args.seed,
        "demo_planners": ["PLN1001", "PLN1002", "PLN1003"],
    }
    with open(os.path.join(out_dir, "_manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2)
    open(os.path.join(out_dir, "_SUCCESS"), "w").close()
    print(f"Wrote {rows:,} products ({plants:,} plants) = {written / 1048576:.1f} MiB to {out_dir}")


if __name__ == "__main__":
    main()
