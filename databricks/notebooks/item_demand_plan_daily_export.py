# Databricks notebook source
# MAGIC %md
# MAGIC # Item demand plan - daily export for the manufacturing production planning app
# MAGIC
# MAGIC Runs every morning (see `../jobs/item_demand_plan_daily_export.job.json`, 04:30 America/New_York).
# MAGIC
# MAGIC 1. Join every active product-at-a-plant with Planning 360 (line, service class, material budget, contacts,
# MAGIC    quality holds), 12 months of sales, the open order book, market signals, finished-goods inventory, the
# MAGIC    week's schedule and line capacity, material cover, open planning alerts, the last 12 production orders
# MAGIC    and weekly material usage.
# MAGIC 2. Score every product with the registered demand-planning model (`mfg_demand_planning`, alias `champion`):
# MAGIC    the 8-week demand forecast, the recommended build quantity and safety stock, days-to-stockout per material,
# MAGIC    the materials to order with a confidence each, forward-buy candidates and 30-day stockout risk.
# MAGIC 3. Write the Delta table `mfg_prod.planning360.item_demand_plan_daily`.
# MAGIC 4. Export the same rows as JSON Lines to a Unity Catalog volume partitioned by `dt=`,
# MAGIC    with `_SUCCESS` and `_manifest.json` - the drop the demo backend's loader picks up and
# MAGIC    publishes to Couchbase (operational + Couchbase Lite sync) and the AOM Knowledge Base.
# MAGIC
# MAGIC **This is the only Databricks compute the app ever uses.** Every forecast read, production plan, material
# MAGIC lookup and assistant answer during the day is served from Couchbase (or from Couchbase Lite on the
# MAGIC planner's phone), not from a SQL warehouse or a Model Serving endpoint. One scheduled job, once a day.
# MAGIC
# MAGIC In this repo the same output is produced locally by `databricks/generate_mock_dump.py`
# MAGIC (mock data, identical schema), so the demo runs without a Databricks workspace.

# COMMAND ----------

dbutils.widgets.text("catalog", "mfg_prod")
dbutils.widgets.text("schema", "planning360")
dbutils.widgets.text("export_volume", "/Volumes/mfg_prod/planning360/exports/item_demand_plan_daily")
dbutils.widgets.text("history_production_orders", "12")
dbutils.widgets.text("num_files", "4")

CATALOG = dbutils.widgets.get("catalog")
SCHEMA = dbutils.widgets.get("schema")
EXPORT = dbutils.widgets.get("export_volume")
HISTORY = int(dbutils.widgets.get("history_production_orders"))
NUM_FILES = int(dbutils.widgets.get("num_files"))
SCHEMA_VERSION = "item_demand_plan_v1"

import datetime as dt
import json

from pyspark.sql import functions as F
from pyspark.sql import Window

export_date = dt.date.today().isoformat()
spark.sql(f"USE CATALOG {CATALOG}")
spark.sql(f"USE SCHEMA {SCHEMA}")

# COMMAND ----------
# MAGIC %md ## 1. Active products + Planning 360

# COMMAND ----------

products = spark.table("item_plant_current").where(F.col("status") == "Active")   # one row per finished product per plant
plants = spark.table("plants").select("plant_id", "plant_name", "city", "state", "planner_id")
planners = spark.table("production_planners").select("planner_id", "employee_id", "first_name", "last_name", "email", "certifications")
contacts = (
    spark.table("line_contacts").groupBy("product_id")
    .agg(F.collect_list(F.struct("name", "role", "phone", "email")).alias("contacts"))
)
service = spark.table("item_service_class")                 # tier, tier_name, fill_rate_target_pct, cover_target_days, review_cadence_days, agreement_id, renewal_date, in_production_since
budget = spark.table("item_material_budget")                # budget_limit_usd, committed_usd, period
holds = spark.table("quality_holds_open")                   # hold_days, on_hold, hold_reason
line = spark.table("item_routing_primary")                  # line_id, line_name, building, shift_pattern, changeover_required, changeover_min, line_notes, warehouse
review = spark.table("plan_review_schedule")                # last_review, cadence_days, next_review_date, planned_time, signal_note, review_notes

# Historical sales: 12 monthly buckets per product, plus the last four weeks and the open order book.
sales = (
    spark.table("sales_monthly").where(F.col("month") >= F.add_months(F.trunc(F.current_date(), "month"), -12))
    .groupBy("product_id")
    .agg(F.sort_array(F.collect_list(F.struct("month", "units"))).alias("history_12mo"))
)
recent = spark.table("sales_last_4wk")                      # last_4wk_units, open_orders_units, top_customers
# Market trends: commodity and lead-time indexes by material category, customer order intake by product.
market = (
    spark.table("market_signals_current").groupBy("product_id")
    .agg(F.collect_list(F.struct("signal", "category", "direction", "change_pct", "note")).alias("market"))
)
# Supply chain: finished-goods position, the week's schedule, cover per material group, open exceptions.
inventory = spark.table("fg_inventory_position")            # on_hand_units, wip_units, safety_stock_units, days_of_cover, target_cover_days, unit_price_usd
schedule = spark.table("line_schedule_this_week")           # week_of, scheduled_units, capacity_units, units_per_hour, shifts, overtime_hours_approved
coverage = (
    spark.table("material_cover_by_group").groupBy("product_id")
    .agg(F.collect_list(F.struct("coverage_id", "category", "name", "supplier", "days_cover", "health", "status")).alias("coverage"))
)
alerts = (
    spark.table("planning_exceptions_open").groupBy("product_id")   # MRP exception report + supplier and demand alerts, open only
    .agg(F.collect_list(F.struct("alert_id", "severity", "code", "text", "action", "category", "raised_at")).alias("alerts"))
)
kpis = spark.table("item_kpis_30d")                         # fill_rate_30d_pct, schedule_adherence_pct, forecast_accuracy_pct, oee_pct
production_orders = (
    spark.table("production_orders").join(spark.table("production_order_materials").groupBy("production_order_id").agg(
        F.collect_list(F.struct("sku", "name", "qty", "unit_cost", "line_total")).alias("lines"),
        F.count("*").alias("line_count")), "production_order_id")
    .withColumn("rn", F.row_number().over(Window.partitionBy("product_id").orderBy(F.desc("released_date"))))
    .where(f"rn <= {HISTORY}")
    .groupBy("product_id")
    .agg(F.collect_list(F.struct(F.col("production_order_id"), F.col("released_date").alias("date"), "status", "source", "order_type",
                                 "run_qty", "line_count", "lines", "materials_usd", "total_usd", "backordered")).alias("order_history"))
)
usage = spark.table("material_usage_by_product")            # map<sku, struct<per_week, last_ordered, last_qty>>
spend = spark.table("item_material_spend_12m")              # last_12mo_usd, ytd_usd, avg_production_order_usd, production_orders_last_12mo, category_mix
standing = spark.table("standing_orders_active")            # array<struct<sku,name,qty,unit_cost,cadence_weeks,next_date>>

base = (
    products.join(plants, "plant_id").join(planners, "planner_id")
    .join(contacts, "product_id", "left").join(service, "product_id").join(budget, "product_id").join(holds, "product_id", "left")
    .join(line, "product_id").join(review, "product_id")
    .join(sales, "product_id", "left").join(recent, "product_id", "left").join(market, "product_id", "left")
    .join(inventory, "product_id").join(schedule, "product_id").join(coverage, "product_id", "left")
    .join(alerts, "product_id", "left").join(kpis, "product_id", "left").join(production_orders, "product_id", "left")
    .join(usage, "product_id", "left").join(spend, "product_id", "left").join(standing, "product_id", "left")
)

# COMMAND ----------
# MAGIC %md ## 2. Score with the demand-planning model

# COMMAND ----------

import mlflow

mlflow.set_registry_uri("databricks-uc")
model_uri = f"models:/{CATALOG}.ml.mfg_demand_planning@champion"
score_udf = mlflow.pyfunc.spark_udf(spark, model_uri, result_type="string")  # returns JSON: the forecast + ml_scores
model_version = mlflow.MlflowClient().get_model_version_by_alias(f"{CATALOG}.ml.mfg_demand_planning", "champion").version

feature_cols = [c for c in base.columns if c not in ("contacts", "first_name", "last_name", "email")]
scored = base.withColumn("model_json", score_udf(F.struct(*feature_cols)))

# COMMAND ----------
# MAGIC %md ## 3. Shape to the export schema and write Delta

# COMMAND ----------

model_schema = (
    "struct<forecast_8wk:array<struct<week_of:string,units:int,low:int,high:int>>,trend_pct:double,drivers:array<string>,"
    "ml_scores:struct<model_version:string,scored_at:string,castings_shortage_risk:double,resin_shortage_risk:double,"
    "electronics_shortage_risk:double,metals_shortage_risk:double,motion_shortage_risk:double,"
    "fasteners_shortage_risk:double,packaging_shortage_risk:double,tooling_wear_score:double,"
    "stockout_risk:double,forecast_next_4wk_units:int,recommended_build_units:int,build_reason:string,"
    "recommended_safety_stock_units:int,predicted_materials_cost_usd:double,days_to_stockout:map<string,int>,"
    "recommended_materials:array<struct<sku:string,qty:int,confidence:double,reason:string>>,"
    "forward_buy_skus:array<string>,segment:string,next_best_action:string>>")
scored = scored.withColumn("m", F.from_json("model_json", model_schema))

out = scored.select(
    F.lit(export_date).alias("export_date"),
    F.lit(SCHEMA_VERSION).alias("schema_version"),
    "product_id", "product_name", "product_code", "line_type", "line_type_name",
    F.when(F.col("program_id").isNotNull(),
           F.struct("program_id", "program_name", F.col("program_products").alias("products"))).alias("program"),
    "plant_id", "plant_name",
    F.struct("city", "state").alias("plant"),
    F.struct("planner_id", "employee_id", "first_name", "last_name", "email", "certifications").alias("assigned_planner"),
    "contacts",
    F.struct("tier", "tier_name", "fill_rate_target_pct", "cover_target_days", "review_cadence_days", "agreement_id",
             "renewal_date", "in_production_since").alias("service"),
    F.struct("budget_limit_usd", "committed_usd", "period", F.coalesce("hold_days", F.lit(0)).alias("hold_days"),
             F.coalesce("on_hold", F.lit(False)).alias("on_hold"), "hold_reason",
             F.lit(0.0).alias("temporary_extension_usd")).alias("auth"),
    F.struct("line_id", F.col("line_name").alias("name"), "building", "shift_pattern", "changeover_required", "changeover_min",
             F.col("line_notes").alias("notes"), "warehouse").alias("line"),
    F.struct("last_review", "cadence_days", "next_review_date", "planned_time", "signal_note",
             F.col("review_notes").alias("notes")).alias("review"),
    F.struct(F.lit("units").alias("uom"), "history_12mo", "last_4wk_units", F.col("m.forecast_8wk").alias("forecast_8wk"),
             F.col("m.trend_pct").alias("trend_pct"), "open_orders_units", "top_customers",
             F.col("m.drivers").alias("drivers")).alias("demand"),
    F.coalesce("market", F.array()).alias("market"),
    F.struct("on_hand_units", "wip_units", "safety_stock_units", "days_of_cover", "target_cover_days", "unit_price_usd").alias("inventory"),
    F.struct("week_of", "scheduled_units", "capacity_units", "units_per_hour", "shifts",
             F.coalesce("overtime_hours_approved", F.lit(0)).alias("overtime_hours_approved")).alias("schedule"),
    F.coalesce("coverage", F.array()).alias("coverage"),
    F.coalesce("alerts", F.array()).alias("alerts"),
    F.struct("fill_rate_30d_pct", "schedule_adherence_pct", "forecast_accuracy_pct", "oee_pct").alias("kpis"),
    "order_history", "usage",
    F.struct("last_12mo_usd", "ytd_usd", "avg_production_order_usd", "production_orders_last_12mo", "category_mix").alias("spend"),
    "standing_orders",
    F.col("m.ml_scores").alias("ml_scores"),
    F.struct("make_to_order", "shared_line").alias("flags"),
    F.struct(F.lit(f"{CATALOG}.{SCHEMA}.item_demand_plan_daily").alias("source_table"),
             F.lit("item_demand_plan_daily_export").alias("pipeline"),
             F.lit(spark.conf.get("spark.databricks.clusterUsageTags.sparkVersion", "")).alias("dbr_version"))
    .alias("_ingest"),
)

(out.write.format("delta").mode("overwrite").option("replaceWhere", f"export_date = '{export_date}'")
    .partitionBy("export_date").saveAsTable("item_demand_plan_daily"))

# COMMAND ----------
# MAGIC %md ## 4. Export JSON Lines for the app (dt= partition + _SUCCESS + _manifest.json)

# COMMAND ----------

path = f"{EXPORT}/dt={export_date}"
daily = spark.table("item_demand_plan_daily").where(F.col("export_date") == export_date)
daily.repartition(NUM_FILES).write.mode("overwrite").json(path)   # Spark writes part-*.json + _SUCCESS

files = [f for f in dbutils.fs.ls(path) if f.name.startswith("part-")]
manifest = {
    "table": f"{CATALOG}.{SCHEMA}.item_demand_plan_daily",
    "export_date": export_date, "schema_version": SCHEMA_VERSION,
    "model_version": f"mfg-demand-planning-v{model_version}", "format": "jsonl",
    "rows": daily.count(), "plants": daily.select("plant_id").distinct().count(),
    "bytes": sum(f.size for f in files), "files": [{"file": f.name, "bytes": f.size} for f in files],
}
dbutils.fs.put(f"{path}/_manifest.json", json.dumps(manifest, indent=2), overwrite=True)
print(json.dumps(manifest, indent=2))

# COMMAND ----------
# MAGIC %md
# MAGIC ## Delivery to the demo backend
# MAGIC The backend watches `DUMP_DIR` for a newer `dt=*/_SUCCESS`. Point it at wherever this volume lands:
# MAGIC * copy it down: `databricks fs cp -r dbfs:/Volumes/.../dt=<date> ./data/databricks-dump/dt=<date>`
# MAGIC * or mount the cloud storage location behind the volume into the backend container at `/data/databricks-dump`.
# MAGIC
# MAGIC After this job finishes, Databricks is done for the day: the warehouse can auto-stop.
