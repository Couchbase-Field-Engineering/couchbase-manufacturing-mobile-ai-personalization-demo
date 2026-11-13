/** Document shapes replicated from Couchbase (scope `planning`) into Couchbase Lite. */

export type Contact = { name: string; role: string; phone: string; email?: string };

export type MaterialLine = {
  sku: string;
  name: string;
  qty: number;
  unit_cost: number;
  line_total: number;
  category?: string;
  uom?: string;
  lead_time_days?: number;
  long_lead?: boolean;
  issued_qty?: number;
  backordered_qty?: number;
};

export type HistoryProductionOrder = {
  production_order_id: string;
  date: string;
  status: string;
  source: string;
  order_type?: string;
  run_qty?: number;
  follow_up_date?: string | null;
  line_count: number;
  lines: MaterialLine[];
  materials_usd?: number;
  total_usd: number;
  tasks_done?: string[];
  backordered?: { sku: string; name: string; qty: number; eta?: string }[];
  budget_override?: boolean;
  reference?: string;
};

/** A planning alert from the exception report: a demand spike, a late PO, cover below safety stock. */
export type PlanAlert = {
  alert_id: string;
  severity: 'critical' | 'major' | 'minor';
  code: string;
  text: string;
  action?: string;
  /** A material category, or `schedule` for demand and finished-goods alerts. */
  category: string;
  raised_at: string;
};

/** Cover for one group of materials the product consumes. */
export type Coverage = {
  coverage_id: string;
  category: string;
  name: string;
  supplier?: string;
  days_cover: number;
  health: number;
  status: 'ok' | 'watch' | 'short';
};

export type Demand = {
  uom: string;
  history_12mo: { month: string; units: number }[];
  last_4wk_units: number;
  forecast_8wk: { week_of: string; units: number; low: number; high: number }[];
  trend_pct: number;
  open_orders_units: number;
  top_customers: string[];
  drivers: string[];
};

export type MarketSignal = { signal: string; category: string; direction: 'up' | 'down' | 'flat'; change_pct: number; note: string };

export type Product = {
  type: 'product';
  product_id: string;
  export_date: string;
  rev: number;
  product_name: string;
  product_code: string;
  line_type: string;
  line_type_name: string;
  program: { program_id: string; program_name: string; products: number } | null;
  plant_id: string;
  plant_name: string;
  plant?: { city: string; state: string };
  contacts: Contact[];
  service: { tier: string; tier_name: string; fill_rate_target_pct: number; cover_target_days: number; review_cadence_days?: number; agreement_id: string; renewal_date: string; in_production_since?: string };
  auth: { budget_limit_usd: number; committed_usd: number; period?: string; hold_days: number; on_hold: boolean; hold_reason?: string | null; temporary_extension_usd?: number };
  line: { line_id: string; name: string; building: string; shift_pattern: string; changeover_required: boolean; changeover_min: number; notes: string; warehouse: string };
  review: { last_review: string; cadence_days: number; next_review_date: string; planned_time: string; signal_note: string; notes: string; last_outcome?: string };
  demand: Demand;
  market: MarketSignal[];
  inventory: { on_hand_units: number; wip_units?: number; safety_stock_units: number; days_of_cover: number; target_cover_days: number; unit_price_usd?: number; projected_cover_days?: number };
  schedule: { week_of: string; scheduled_units: number; capacity_units: number; units_per_hour: number; shifts: number; overtime_hours_approved?: number; released_at?: string; released_order_id?: string };
  coverage: Coverage[];
  alerts: PlanAlert[];
  kpis: { fill_rate_30d_pct: number; schedule_adherence_pct: number; forecast_accuracy_pct: number; oee_pct: number };
  order_history: HistoryProductionOrder[];
  usage: Record<string, { per_week: number; last_ordered: string; last_qty?: number | null }>;
  spend: { last_12mo_usd: number; ytd_usd: number; avg_production_order_usd: number; production_orders_last_12mo: number; category_mix: Record<string, number> };
  standing_orders: { sku: string; name: string; qty: number; unit_cost: number; cadence_weeks: number; next_date: string }[];
  supplier_returns?: { return_id: string; production_order_id: string; sku: string; name: string; qty: number; refund_usd: number; date: string }[];
  overtime_shifts?: { shift_id: string; hours: number; added_units: number; reason: string; date: string }[];
  ml_scores: Record<string, unknown> & {
    model_version: string;
    segment: string;
    next_best_action: string;
    stockout_risk: number;
    forecast_next_4wk_units: number;
    recommended_build_units: number;
    build_reason?: string;
    recommended_safety_stock_units: number;
    predicted_materials_cost_usd: number;
    recommended_materials: { sku: string; qty: number; confidence: number; reason: string }[];
    forward_buy_skus: string[];
    days_to_stockout: Record<string, number>;
  };
  flags: { make_to_order: boolean; shared_line: boolean };
};

/** This week's schedule for a product next to the model's recommended build and the line's capacity. */
export type ScheduleView = {
  week_of: string;
  scheduled_units: number;
  recommended_units: number;
  capacity_units: number;
  delta_units: number;
  units_per_hour: number;
  shifts?: number;
  overtime_needed: boolean;
  overtime_hours: number;
  overtime_hours_approved: number;
  released: boolean;
  reason?: string | null;
};

export type PlanLine = {
  sku: string;
  name: string;
  category: string;
  category_name: string;
  uom: string;
  spec: string;
  qty: number;
  unit_cost: number;
  line_total: number;
  confidence: number | null;
  reason: string;
  on_hand_at_warehouse: number;
  availability: 'in_stock' | 'low' | 'backorder';
  days_to_stockout: number | null;
  lead_time_days: number;
  long_lead: boolean;
  score: number;
  selected: boolean;
  rank: number;
};

export type PlanTask = {
  id: string;
  kind: 'alert' | 'readiness';
  title: string;
  est_min: number;
  severity?: string;
  alert?: string;
};

export type AuthCheck = {
  budget_limit_usd: number;
  committed_usd: number;
  temporary_extension_usd: number;
  available_usd: number;
  on_hold: boolean;
  hold_days: number;
  hold_reason?: string | null;
  ok: boolean;
  shortfall_usd: number;
};

export type ProductionPlanDoc = {
  type: 'production_plan';
  product_id: string;
  product_name: string;
  lines: PlanLine[];
  forward_buys: { sku: string; name: string; category: string; uom: string; spec: string; qty: number; unit_cost: number; line_total: number; lead_time_days?: number; long_lead?: boolean; reason: string; on_hand_at_warehouse: number }[];
  tasks: PlanTask[];
  order_type: string;
  alerts: PlanAlert[];
  schedule: ScheduleView;
  demand: { last_4wk_units: number | null; next_4wk_units: number | null; trend_pct: number | null; forecast_accuracy_pct: number | null };
  inventory: { on_hand_units: number | null; days_of_cover: number | null; target_cover_days: number | null; safety_stock_units: number | null; recommended_safety_stock_units: number | null };
  plan_total_usd: number;
  est_minutes: number;
  long_lead: boolean;
  auth: AuthCheck;
  briefing: string[];
  model_version: string;
  segment: string;
  stockout_risk?: number;
  export_date: string;
  product_rev: number;
  generated_at: string;
};

/** One stop on today's floor walk: a product whose plan is due for review. */
export type RouteStop = {
  product_id: string;
  product_name: string;
  product_code?: string;
  line_type: string;
  line?: string | null;
  building?: string | null;
  planned_time: string;
  signal_note: string;
  contact: string | null;
  plan_total_usd: number | null;
  tier: string;
  tier_name?: string | null;
  order_type?: string | null;
  scheduled_units?: number | null;
  recommended_units?: number | null;
  overtime_needed?: boolean;
  alert_count?: number;
  worst_alert?: string | null;
  stockout_risk?: number | null;
  last_order_date: string | null;
  hold_flag: boolean;
  status: 'planned' | 'checked';
  outcome?: string;
  checked_at?: string;
};

export type Planner = {
  type: 'planner';
  planner_id: string;
  employee_id: string;
  first_name: string;
  last_name: string;
  email: string;
  certifications?: string[];
  plant_id: string;
  plant_name: string;
  product_count: number;
  route_date: string;
  route: RouteStop[];
  due_this_week: number;
  committed_usd: number;
  on_hold_products: number;
  open_alerts?: number;
  rev: number;
  export_date: string;
};

export type RequestStatus = 'pending' | 'processing' | 'awaiting_approval' | 'completed' | 'materials_pending' | 'failed';

/** An offline-first production order or planning action: created on the phone, processed by the backend through Couchbase Labs Agent Operations Manager. */
export type ProductionOrderRequest = {
  _id: string;
  type: 'request';
  planner_id: string;
  product_id: string;
  product_name?: string;
  tool: string;
  args: Record<string, unknown>;
  title: string;
  status: RequestStatus;
  created_at: string;
  summary?: string;
  cost_usd?: number;
  reason_code?: string | null;
  auth?: AuthCheck | null;
  item?: Record<string, unknown> & { production_order_id?: string; run_qty?: number; backordered?: HistoryProductionOrder['backordered']; follow_up_date?: string | null };
  governed?: boolean;
  trace_id?: string | null;
  approval_id?: string | null;
  completed_at?: string | null;
  _local?: boolean; // created on this device, not yet acknowledged by the server
};

export type ChatMessage = {
  id: string;
  role: 'planner' | 'assistant';
  text: string;
  at: string;
  product_id?: string | null;
  intent?: string;
  actions?: SuggestedAction[];
  context?: { status: string; latency_ms: number; kb_mode?: string; build_ms?: number };
  llm?: { status: string; namespace?: string; similarity?: number | null; latency_ms?: number | null; stub?: boolean; composed_locally?: boolean };
  sources?: { title: string; snippet: string; kind: string }[];
  offline?: boolean;
  pending?: boolean;
};

export type SuggestedAction = { label: string; tool?: string; screen?: string; args?: Record<string, unknown> };

export type ChatDoc = {
  type: 'assistant';
  messages: ChatMessage[];
  answers: Record<string, { reply: string; intent: string; at: string; actions?: SuggestedAction[] }>;
};

export type ContextDoc = {
  type: 'context_cache';
  scope: string;
  product_id: string | null;
  intent: string;
  personal: boolean;
  export_date: string;
  built_at: string;
  build_ms: number;
  kb_mode: string;
  kb_profile: { title: string; content: string }[];
  kb_playbook: { title: string; content: string; answer?: string }[];
  facts: { tool?: string; data?: any; catalog?: any[] };
};

export type Material = {
  sku: string;
  name: string;
  category: string;
  uom: string;
  unit_cost: number;
  spec: string;
  tags: string[];
  kanban: boolean;
  lead_time_days: number;
  long_lead: boolean;
};

export type Catalog = {
  type: 'catalog';
  export_date: string;
  categories: Record<string, string>;
  materials: Material[];
  service_tiers: { code: string; name: string; fill_rate_target_pct: number; cover_target_days: number; review_cadence_days: number; blurb: string }[];
  order_types: string[];
  warehouses: Record<string, { name: string; city: string }>;
  line_types: Record<string, string>;
  readiness_checklists: Record<string, string[]>;
  faq: { q: string; a: string; keywords: string[] }[];
};

export type SyncState = {
  engine: 'couchbase-lite' | 'preview';
  status: 'connecting' | 'connected' | 'idle' | 'busy' | 'offline' | 'paused' | 'error' | 'stopped';
  lastSyncAt: number | null;
  pendingPush: number;
  detail?: string;
  endpoint?: string;
};
