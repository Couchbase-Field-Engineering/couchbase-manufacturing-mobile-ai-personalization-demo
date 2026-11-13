"""`mfg-production-planning` - the MCP server registered with AOM.

Streamable HTTP at /mfg/mcp/. AOM ingests these tool definitions into its
vetted catalog (embeddings for discovery, RBAC per role, drift detection)
and is the only caller the demo expects: the app and assistant reach these
tools through `POST /v1/tools/invoke`, never directly.

Business errors (over budget, over capacity, quality hold, unknown material,
bad order type) come back as `{"ok": false, ...}` results rather than MCP
errors, so AOM records a completed call and the planner gets a useful message
instead of a gateway failure.
"""
from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from . import actions

log = logging.getLogger("mfg.mcp")

mcp = FastMCP(
    "mfg-production-planning",
    instructions=("Demand and production planning for a planner's plant: the demand forecast and recommended build quantity "
                  "from the demand-planning model, this week's schedule against line capacity, finished-goods cover and "
                  "safety stock, open planning alerts, materials and warehouse stock, production-order release, budget "
                  "overrides, overtime shifts, standing orders, supplier returns and line checks."),
    stateless_http=True,
    # Called by AOM over the Docker host gateway, so the Host header is not
    # localhost; same reasoning as AOM's own bundled sample servers.
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


async def _run(fn, **kw) -> dict:
    try:
        res = await fn(**kw)
    except actions.ToolError as exc:
        return {"ok": False, "status": "failed", "summary": str(exc)}
    return res if isinstance(res, dict) and "ok" in res else {"ok": True, "status": "ok", "data": res}


@mcp.tool()
async def get_product_context(planner_id: str, product_id: str) -> dict:
    """Read a product: line, contacts, service class, demand history and 8-week forecast, market signals, finished-goods inventory and safety stock, this week's schedule and capacity, open alerts, material cover, material budget and quality hold, last production order, review cadence and stockout risk."""
    return await _run(actions.get_product_context, planner_id=planner_id, product_id=product_id)


@mcp.tool()
async def get_production_plan(planner_id: str, product_id: str) -> dict:
    """The demand-planning model's plan for a product: recommended build quantity against this week's schedule and line capacity, alert tasks, the run-readiness checklist, and materials to order with days-to-stockout, supplier lead time, on-hand units at the serving warehouse and a budget check."""
    return await _run(actions.get_production_plan, planner_id=planner_id, product_id=product_id)


@mcp.tool()
async def search_materials(planner_id: str, product_id: str, query: str = "", category: str = "") -> dict:
    """Search the materials catalog for a product: unit cost, warehouse on-hand units, supplier lead time, whether it is long-lead and when it was last ordered for this product. category is one of castings, resin, electronics, metals, motion, fasteners, packaging, tooling."""
    return await _run(actions.search_materials, planner_id=planner_id, product_id=product_id, query=query, category=category)


@mcp.tool()
async def quote_production_order(planner_id: str, product_id: str, lines: list, run_qty: int = 0) -> dict:
    """Cost a production order for a product before releasing it: material lines [{sku, qty}] and a build quantity. Returns the materials total, longest lead time, budget check, any short warehouse stock and how far the build is over the line's weekly capacity."""
    return await _run(actions.quote_production_order, planner_id=planner_id, product_id=product_id, lines=lines, run_qty=run_qty)


@mcp.tool()
async def release_production_order(planner_id: str, product_id: str, lines: list | None = None, run_qty: int = 0,
                                   reference: str = "", order_type: str = "", notes: str = "",
                                   tasks_done: list | None = None) -> dict:
    """Release a production order for a product: the build quantity for this week (defaults to the model's recommendation, capped at line capacity), material lines [{sku, qty}] and the readiness checks completed. Sets the week's schedule, issues warehouse stock (puts the rest on order with the supplier), commits the cost to the material budget and clears the alerts it resolves. Fails with reason_code budget_limit when the materials exceed the remaining budget, capacity_limit when the build exceeds line capacity, or quality_hold when the product is on a quality hold."""
    return await _run(actions.release_production_order, planner_id=planner_id, product_id=product_id, lines=lines, run_qty=run_qty,
                      reference=reference, order_type=order_type, notes=notes, tasks_done=tasks_done)


@mcp.tool()
async def request_budget_override(planner_id: str, product_id: str, lines: list, reason: str, run_qty: int = 0,
                                  reference: str = "", order_type: str = "", notes: str = "",
                                  tasks_done: list | None = None) -> dict:
    """Release a production order whose materials cost more than the product's remaining material budget. Requires a reason; held for the Plant Controller's approval. Once approved, the shortfall becomes a one-time extension and the order is released."""
    return await _run(actions.request_budget_override, planner_id=planner_id, product_id=product_id, lines=lines, reason=reason,
                      run_qty=run_qty, reference=reference, order_type=order_type, notes=notes, tasks_done=tasks_done)


@mcp.tool()
async def set_standing_order(planner_id: str, product_id: str, sku: str, qty: int, cadence_weeks: int = 4) -> dict:
    """Create or replace a standing order for a kanban material on a product: a fixed quantity every 1, 2, 4, 8, 12 or 26 weeks."""
    return await _run(actions.set_standing_order, planner_id=planner_id, product_id=product_id, sku=sku, qty=qty, cadence_weeks=cadence_weeks)


@mcp.tool()
async def create_supplier_return(planner_id: str, product_id: str, production_order_id: str, sku: str, qty: int, reason: str) -> dict:
    """Create a supplier return for material on a recent production order (within 30 days): credits the cost back to the material budget and sets the date it has to be staged at the warehouse dock."""
    return await _run(actions.create_supplier_return, planner_id=planner_id, product_id=product_id,
                      production_order_id=production_order_id, sku=sku, qty=qty, reason=reason)


@mcp.tool()
async def log_line_check(planner_id: str, product_id: str, outcome: str, notes: str = "", next_review_days: int = 0) -> dict:
    """Log a line check for a product (outcome: on_plan, plan_adjusted, follow_up, on_hold), mark the stop on today's floor walk as checked and schedule the next plan review."""
    return await _run(actions.log_line_check, planner_id=planner_id, product_id=product_id, outcome=outcome, notes=notes,
                      next_review_days=next_review_days)


@mcp.tool()
async def request_overtime_shift(planner_id: str, product_id: str, hours: int, reason: str) -> dict:
    """Request an overtime shift (1 to 16 hours) on a product's line this week so the schedule can meet the recommended build. Requires a reason; held for the Plant Manager's approval. Once approved, the line's weekly capacity goes up by the hours."""
    return await _run(actions.request_overtime_shift, planner_id=planner_id, product_id=product_id, hours=hours, reason=reason)
