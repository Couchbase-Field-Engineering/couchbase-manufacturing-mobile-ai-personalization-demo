/**
 * Offline assistant: answers from what Couchbase Lite already holds.
 *
 *  1. this planner's earlier answers (`assistant` doc `answers`, keyed by product + normalized question)
 *  2. the product's production plan (`production_plans` doc) and product document
 *     (forecast, inventory, schedule, alerts, budget, line, history)
 *  3. the product's context cache (`context_cache` docs the server assembled
 *     from the Couchbase Labs Agent Operations Manager Knowledge Base + tool facts)
 *  4. the playbook FAQ synced in the `catalog` doc
 *
 * Mirrors backend/app/assistant.py's intent keywords so the same question
 * lands on the same cached context offline as online.
 */
import type { Snapshot } from '../../db/localDb';
import { ids } from '../../db/localDb';
import type { ChatMessage, SuggestedAction } from '../../types';

const KEYWORDS: Record<string, string[]> = {
  budget: ['budget', 'override', 'over budget', 'limit', 'spend', 'authoriz', 'authoris', 'hold', 'approval', 'committed'],
  returns: ['return', 'supplier return', 'rtv', 'damaged', 'wrong revision', 'out of spec', 'reject', 'send back'],
  standing: ['standing order', 'standing', 'recurring', 'every week', 'every month', 'cadence', 'interval', 'kanban', 'auto-replen', 'on a schedule'],
  overtime: ['overtime', 'over capacity', 'capacity', 'extra shift', 'saturday', 'weekend shift', 'extended hours', 'plant manager'],
  materials: ['in stock', 'available', 'availability', 'on hand', 'backorder', 'on order', 'alternate', 'part number', 'sku', 'how much is', 'cost', 'price',
    'warehouse', 'lead time', 'material', 'castings', 'resin'],
  line: ['line', 'changeover', 'shift pattern', 'supervisor', 'contact', 'who do i', 'where is', 'building', 'cell', 'press'],
  alerts: ['alert', 'alerts', 'exception', 'supplier delay', 'late po', 'demand spike', 'why is', 'coverage', 'short on'],
  history: ['history', 'last production order', 'last time', 'previous', 'ordered before', 'usually', 'how often', 'what was released', 'last run'],
  review: ['line check', 'review', 'floor walk', 'next review', 'come back', 'follow up', 'log'],
  forecast: ['forecast', 'demand', 'sales', 'trend', 'market', 'signal', 'customers', 'next month', 'next four weeks', 'seasonal', 'drivers', 'selling'],
  inventory: ['inventory', 'safety stock', 'days of cover', 'finished goods', 'stock level', 'stockout', 'how much stock', 'cover do we'],
  plan: ['production plan', 'plan', 'recommend', 'what should', 'what needs', 'needs doing', 'to do', 'checklist', 'readiness', 'how many', 'build', 'schedule',
    'expedite', 'release', 'this week', 'today', 'about to run out', 'running out', 'run out'],
};

export function classify(q: string): string {
  const m = q.toLowerCase();
  let best = 'general';
  let score = 0;
  for (const [intent, kws] of Object.entries(KEYWORDS)) {
    const s = kws.filter((k) => m.includes(k)).length;
    if (s > score) {
      best = intent;
      score = s;
    }
  }
  return best;
}

export const normalize = (q: string) => q.toLowerCase().replace(/[^a-z0-9 ]/g, '').trim().slice(0, 120);
const money = (n: number) => `$${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const n0 = (n: number | null | undefined) => Math.round(n ?? 0).toLocaleString('en-US');
const signed = (n: number) => `${n > 0 ? '+' : ''}${n.toLocaleString('en-US')}`;
const short = (name: string) => name.split(',')[0];
/** A method or policy question asked generically ("how is…") is a playbook question even with a product open. */
const GENERIC = /^\s*(how|what is|what are|what's the|is|are|can|do|does|who|which|when do)\b/i;
const ABOUT_PRODUCT = /\b(this|their|they|them|the product|this product|the line|this line|this item|this sku|here|we|our|my)\b/i;

export function answerOffline(q: string, snap: Snapshot, productId: string | null, plannerId: string): Omit<ChatMessage, 'id' | 'at' | 'role'> {
  const intent = classify(q);
  const prior = snap.chat?.answers?.[`${productId ?? '-'}|${normalize(q)}`];
  if (prior) {
    return { text: prior.reply, intent, actions: prior.actions, offline: true, context: { status: 'device', latency_ms: 0 } };
  }
  const m = q.toLowerCase();
  const faq = (snap.catalog?.faq ?? [])
    .map((f) => ({ f, s: f.keywords.filter((k) => m.includes(k)).length }))
    .filter((x) => x.s > 0)
    .sort((a, b) => b.s - a.s)[0];

  const playbookOnly = GENERIC.test(q) && !ABOUT_PRODUCT.test(q) && !!faq;
  const a = productId && !playbookOnly ? snap.products[productId] : null;
  const plan = productId && !playbookOnly ? snap.plans[productId] : null;
  const ctx = productId ? snap.contexts[ids.ctx(productId, intent)] : snap.contexts[ids.ctx(plannerId, `playbook_${intent}`)];
  const actions: SuggestedAction[] = [];
  let text: string | null = null;
  const warehouse = a ? (snap.catalog?.warehouses[a.line.warehouse]?.name ?? a.line.warehouse) : '';
  const sv = plan?.schedule;
  const openOrder = (label = 'Open Production order') => a && actions.push({ label, screen: 'order', args: { product_id: a.product_id } });
  const overtimeAction = () => {
    if (!a || !sv?.overtime_needed) return;
    actions.push({
      label: `Request ${sv.overtime_hours}-hour overtime shift`, tool: 'request_overtime_shift',
      args: { product_id: a.product_id, hours: sv.overtime_hours, reason: `Recommended build of ${n0(sv.recommended_units)} units is ${n0(sv.recommended_units - sv.capacity_units)} over this week's capacity on ${a.line.name}` },
    });
  };

  if (a && plan && sv && (intent === 'plan' || intent === 'standing')) {
    const sel = plan.lines.filter((l) => l.selected);
    const al = plan.tasks.filter((t) => t.kind === 'alert');
    const rd = plan.tasks.filter((t) => t.kind === 'readiness');
    const urgent = plan.lines.filter((l) => l.days_to_stockout != null && l.days_to_stockout <= 7);
    const low = plan.lines.filter((l) => l.on_hand_at_warehouse < l.qty);
    text = `From the plan on this phone - ${plan.order_type.toLowerCase()} for ${a.product_name}: build ${n0(sv.recommended_units)} units this week`;
    text += sv.delta_units ? ` (${signed(sv.delta_units)} against the ${n0(sv.scheduled_units)} scheduled).` : ', as scheduled.';
    if (sv.overtime_needed) text += ` That is over ${a.line.name}'s capacity of ${n0(sv.capacity_units)} - about ${sv.overtime_hours} hours of overtime for the Plant Manager to approve.`;
    text += al.length ? ` ${al.length} alert${al.length > 1 ? 's' : ''} to clear first, then the ${rd.length}-step readiness checklist.` : ` Walk the ${rd.length}-step readiness checklist.`;
    if (sel.length) text += ` ${sel.length} material line${sel.length > 1 ? 's' : ''} to order, ${money(plan.plan_total_usd)}.`;
    if (urgent.length) text += ` Start with ${urgent.slice(0, 2).map((l) => `${short(l.name)} x${l.qty} (runs out in ~${Math.max(0, l.days_to_stockout ?? 0)}d, ${l.lead_time_days}-day lead time)`).join(', ')}.`;
    if (low.length) text += ` The warehouse is short on ${low.slice(0, 2).map((l) => short(l.name)).join(', ')}.`;
    if (!plan.auth.ok && !plan.auth.on_hold) text += ` That is ${money(plan.auth.shortfall_usd)} over the remaining material budget - release it as a budget override.`;
    if (plan.auth.on_hold) text += ' This product is on a quality hold.';
    overtimeAction();
    openOrder();
  } else if (a && intent === 'alerts') {
    const al = a.alerts ?? [];
    text = al.length
      ? `${a.product_name} has ${al.length} open alert${al.length > 1 ? 's' : ''}: ${al.slice(0, 3).map((x) => `${x.severity} - ${x.text}${x.action ? ` (${x.action})` : ''}`).join('; ')}.`
      : `${a.product_name} has no open alerts as of this morning's export.`;
    openOrder();
  } else if (a && sv && intent === 'overtime') {
    text = sv.overtime_needed
      ? `Yes - the model recommends ${n0(sv.recommended_units)} units and ${a.line.name} can make ${n0(sv.capacity_units)} this week, so you are ${n0(sv.recommended_units - sv.capacity_units)} short: about ${sv.overtime_hours} hours of overtime at ${sv.units_per_hour} units an hour. Request the shift; it will send when you have a connection and the Plant Manager approves it.`
      : `No - the recommended build of ${n0(sv.recommended_units)} units fits within this week's capacity of ${n0(sv.capacity_units)} on ${a.line.name}.`;
    overtimeAction();
    openOrder();
  } else if (a && intent === 'forecast') {
    const d = a.demand;
    const mk = (a.market ?? []).filter((x) => x.category !== 'demand').slice(0, 2);
    text = `${a.product_name}: ${n0(d.last_4wk_units)} units sold in the last four weeks; the model forecasts ${n0(a.ml_scores.forecast_next_4wk_units)} for the next four (${d.trend_pct > 0 ? '+' : ''}${d.trend_pct}%).` +
      (d.drivers?.length ? ` Drivers: ${d.drivers.slice(0, 2).join('; ')}.` : '') +
      (mk.length ? ` Market signals: ${mk.map((x) => `${x.signal.toLowerCase()} ${x.direction} ${Math.abs(x.change_pct)}%`).join('; ')}.` : '') +
      ` Forecast accuracy over the last 30 days is ${a.kpis.forecast_accuracy_pct}%.` +
      (sv ? ` It recommends building ${n0(sv.recommended_units)} units this week against ${n0(sv.scheduled_units)} scheduled.` : '');
    openOrder();
  } else if (a && intent === 'inventory') {
    const iv = a.inventory;
    const rss = a.ml_scores.recommended_safety_stock_units;
    text = `${a.product_name} has ${n0(iv.on_hand_units)} units of finished goods on hand - ${iv.days_of_cover} days of cover against a ${iv.target_cover_days}-day target.` +
      (rss && rss !== iv.safety_stock_units
        ? ` Safety stock is ${n0(iv.safety_stock_units)} units; the model recommends ${rss > iv.safety_stock_units ? 'raising' : 'lowering'} it to ${n0(rss)}.`
        : ` Safety stock is ${n0(iv.safety_stock_units)} units, which the model would keep.`) +
      (sv ? ` To get back to target it recommends building ${n0(sv.recommended_units)} units this week (${n0(sv.scheduled_units)} are scheduled).` : '');
    openOrder();
  } else if (a && intent === 'budget') {
    const c = a.auth;
    const avail = c.budget_limit_usd + (c.temporary_extension_usd ?? 0) - c.committed_usd;
    text = c.on_hold
      ? `${a.product_name} is on a quality hold for ${c.hold_days} days: ${c.hold_reason}. No production order can be released until Quality lifts it.`
      : `${a.product_name} has ${money(avail)} left this quarter: a ${money(c.budget_limit_usd)} material budget with ${money(c.committed_usd)} committed. Material orders above that need a budget override approved by the Plant Controller.`;
  } else if (a && intent === 'history') {
    const lo = a.order_history[0];
    text = `${a.product_name}: ${money(a.spend.last_12mo_usd)} of materials over ${a.spend.production_orders_last_12mo} production orders in 12 months, average ${money(a.spend.avg_production_order_usd)}.` +
      (lo ? ` Last production order ${lo.production_order_id} on ${lo.date}: ${(lo.order_type ?? 'scheduled run').toLowerCase()}, ${n0(lo.run_qty)} units, ${money(lo.total_usd)} of materials.` : '');
    actions.push({ label: 'Open product', screen: 'product', args: { product_id: a.product_id } });
  } else if (a && intent === 'line') {
    const ln = a.line;
    const sup = a.contacts.find((k) => k.role === 'Line Supervisor');
    text = `${a.product_name} runs on ${ln.name} in ${ln.building}: ${ln.shift_pattern}, ${ln.changeover_required ? `changeover needed before this run (about ${ln.changeover_min} minutes)` : 'no changeover needed'}.` +
      (sup ? ` Supervisor: ${sup.name}, ${sup.phone}.` : '') + (ln.notes ? ` ${ln.notes}.` : '') + ` Floor note: ${a.review.signal_note}.` +
      (a.auth.on_hold ? ` The product is on a quality hold: ${a.auth.hold_reason}.` : '');
  } else if (a && intent === 'review') {
    text = `Plan last reviewed ${a.review.last_review}; every ${a.review.cadence_days} days, next ${a.review.next_review_date}.`;
    actions.push({ label: 'Log line check', screen: 'check', args: { product_id: a.product_id } });
  } else if (a && intent === 'materials') {
    const stop = ['stock', 'warehouse', 'there', 'have', 'many', 'much', 'cost', 'material', 'materials', 'lead', 'time', 'this', 'product', 'what', 'does'];
    const words = m.split(/\s+/).map((w) => w.replace(/[^a-z0-9+]/g, '')).filter((w) => w.length > 3 && !stop.includes(w));
    const hit = (snap.catalog?.materials ?? []).find((p) => words.some((w) => `${p.name} ${p.tags.join(' ')}`.toLowerCase().includes(w.replace(/ies$/, 'y').replace(/s$/, ''))));
    const line = hit ? plan?.lines.find((l) => l.sku === hit.sku) : null;
    if (hit) {
      text = `${hit.name} (${hit.sku}) is ${money(hit.unit_cost)} per ${hit.uom}, ${hit.lead_time_days}-day supplier lead time.` +
        (line ? ` ${line.on_hand_at_warehouse} on hand at ${warehouse} as of the last sync.` : ` Warehouse stock for it isn't on this phone - it shows on the production order once you're connected.`) +
        (a.usage[hit.sku] ? ` Last ordered for this product ${a.usage[hit.sku].last_ordered}.` : '') +
        (hit.long_lead ? ' It is long-lead - order against the forecast, not the reorder point.' : '');
    } else if (plan?.lines.length) {
      text = `Materials recommended for ${a.product_name}, with stock at ${warehouse}: ${plan.lines.slice(0, 4).map((l) => `${l.qty} x ${short(l.name)} (${l.on_hand_at_warehouse} on hand)`).join('; ')}.`;
    }
    openOrder();
  }
  if (!text && faq) text = faq.f.a;
  if (!text && ctx?.kb_playbook?.[0]) text = ctx.kb_playbook[0].answer ?? ctx.kb_playbook[0].content;
  if (!text && !productId && intent !== 'general') {
    text = "Open a product on today's floor walk and ask again - offline, I answer from that product's data on this phone.";
    actions.push({ label: "Open today's floor walk", screen: 'route' });
  }
  if (!text) {
    text = "You're offline right now, so I can only answer from what's saved on this phone: the product's forecast, inventory, schedule, alerts, plan, budget and history, and the playbook FAQ.";
  }
  return {
    text,
    intent,
    actions,
    offline: true,
    context: { status: ctx || plan ? 'device' : 'none', latency_ms: 0 },
    sources: faq && (playbookOnly || !a) ? [{ title: 'Production Planning Playbook (on device)', snippet: faq.f.q, kind: 'couchbase-lite' }] : [],
  };
}
