"""Document ids, channel names and small shared helpers."""
from __future__ import annotations

import datetime as dt

from . import config

STATE = {"export_date": None}  # set by the loader


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> dt.date:
    """The demo's notion of 'today' - see config.DEMO_TODAY."""
    if config.DEMO_TODAY:
        return dt.date.fromisoformat(config.DEMO_TODAY)
    if STATE.get("export_date"):
        return dt.date.fromisoformat(STATE["export_date"])
    return dt.date.today()


# Channels. A planner's Sync Gateway user gets exactly three: their own channel
# (route, production orders, assistant chat), their plant's channel (every product
# and production plan in the plant) and the shared catalog.
def planner_channel(planner_id: str) -> str:
    return f"planner:{planner_id}"


def plant_channel(plant_id: str) -> str:
    return f"plant:{plant_id}"


CATALOG_CHANNEL = "catalog"


def planner_id_doc(r):
    return f"planner::{r}"


def product_id_doc(a):
    return f"product::{a}"


def plan_id(a):
    return f"plan::{a}"


def chat_id(r):
    return f"chat::{r}"


def ctx_id(scope, intent):
    """scope = product id for product context, planner id for playbook context."""
    return f"ctx::{scope}::{intent}"


def inventory_id(warehouse):
    return f"inv::{warehouse}"


CATALOG_ID = "catalog::v1"


def days_since(date_str: str | None) -> int | None:
    if not date_str:
        return None
    return (today() - dt.date.fromisoformat(date_str[:10])).days


def days_until(date_str: str | None) -> int | None:
    if not date_str:
        return None
    return (dt.date.fromisoformat(date_str[:10]) - today()).days


def money(x: float) -> str:
    return f"${x:,.2f}"
