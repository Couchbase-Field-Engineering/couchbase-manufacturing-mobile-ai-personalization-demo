import { Ionicons } from '@expo/vector-icons';
import { router, useLocalSearchParams } from 'expo-router';
import { useEffect } from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';
import { useData } from '@/src/data/DataContext';
import { theme } from '@/src/theme';
import type { MarketSignal } from '@/src/types';
import { Button, Card, Empty, fmtDate, GradientArt, HealthBar, money, money2, Pill, SectionTitle, SEVERITY_TONE, STATUS_UI, type IconName } from '@/src/ui/components';

const n0 = (v: number | null | undefined) => (v == null ? '-' : Math.round(v).toLocaleString('en-US'));
const signed = (v: number) => `${v > 0 ? '+' : ''}${v.toLocaleString('en-US')}`;
const MONTHS = ['J', 'F', 'M', 'A', 'M', 'J', 'J', 'A', 'S', 'O', 'N', 'D'];

/** The product: forecast, inventory, this week's schedule and the plan - everything the planner needs at the line, from Couchbase Lite. */
export default function ProductScreen() {
  const { product_id = '' } = useLocalSearchParams<{ product_id: string }>();
  const { snap, setCurrentProductId } = useData();
  const a = snap.products[product_id];
  const plan = snap.plans[product_id];
  useEffect(() => {
    if (product_id) setCurrentProductId(product_id);
  }, [product_id, setCurrentProductId]);
  if (!a) return <Empty icon="cube-outline" title="Product not on this phone" body="It isn't in your plant, or hasn't synced yet." />;

  const c = a.auth;
  const available = c.budget_limit_usd + (c.temporary_extension_usd ?? 0) - c.committed_usd;
  const warehouse = snap.catalog?.warehouses[a.line.warehouse];
  const selected = plan?.lines.filter((l) => l.selected) ?? [];
  const tasks = plan?.tasks ?? [];
  const alertTasks = tasks.filter((t) => t.kind === 'alert').length;
  const orders = snap.orders.filter((o) => o.product_id === product_id).slice(0, 3);
  const noSignal = !/good signal/i.test(a.review.signal_note ?? '');
  const alerts = a.alerts ?? [];
  const risk = Math.round((a.ml_scores.stockout_risk ?? 0) * 100);
  const iv = a.inventory;
  const d = a.demand;
  const sv = plan?.schedule;
  const scheduled = sv?.scheduled_units ?? a.schedule.scheduled_units;
  const recommended = sv?.recommended_units ?? a.ml_scores.recommended_build_units;
  const capacity = sv?.capacity_units ?? a.schedule.capacity_units;
  const delta = recommended - scheduled;
  const scale = Math.max(scheduled, recommended, capacity, 1);
  const next4 = a.ml_scores.forecast_next_4wk_units;
  const recSafety = a.ml_scores.recommended_safety_stock_units;
  const coverPct = Math.min(100, Math.round((iv.days_of_cover / Math.max(1, iv.target_cover_days)) * 100));

  return (
    <ScrollView contentContainerStyle={styles.body}>
      <Card>
        <GradientArt palette="product" icon="cube" height={128}>
          <View style={{ flexDirection: 'row', gap: 6, flexWrap: 'wrap' }}>
            <Pill label={a.line_type_name} tone="dark" icon="construct-outline" />
            <Pill label={`${a.service.tier_name} · ${a.service.fill_rate_target_pct}% fill rate`} tone="dark" icon="ribbon-outline" />
            {a.program ? <Pill label={`${a.program.program_name} · ${a.program.products} products`} tone="dark" /> : null}
          </View>
          <View>
            <Text style={styles.code}>{a.product_code} · {a.plant_name.toUpperCase()}</Text>
            <Text style={styles.title} numberOfLines={2}>{a.product_name}</Text>
          </View>
        </GradientArt>
        <View style={{ padding: 14, gap: 8 }}>
          <Row icon="git-network-outline" label="Line" value={`${a.line.name} · ${a.line.building} · ${a.line.shift_pattern}`} />
          <Row icon="swap-horizontal-outline" label="Changeover" value={a.line.changeover_required ? `Needed before this run · about ${a.line.changeover_min} min` : 'Not needed'} tone={a.line.changeover_required ? 'warn' : undefined} />
          <Row icon="person-outline" label="Contacts" value={a.contacts.map((k) => `${k.name} (${k.role}) ${k.phone}`).join('\n')} />
          {a.line.notes ? <Row icon="document-text-outline" label="Notes" value={a.line.notes} /> : null}
          <Row icon="cellular-outline" label="Signal" value={a.review.signal_note} tone={noSignal ? 'warn' : undefined} />
        </View>
      </Card>

      <View style={styles.kpis}>
        <Kpi label="OPEN ALERTS" value={String(alerts.length)} tone={alerts.length ? 'danger' : 'ok'} />
        <Kpi label="STOCKOUT RISK 30D" value={`${risk}%`} tone={risk >= 50 ? 'danger' : risk >= 25 ? 'warn' : 'ok'} />
        <Kpi label={`DAYS OF COVER / ${iv.target_cover_days}`} value={String(iv.days_of_cover)} tone={coverPct < 60 ? 'danger' : coverPct < 90 ? 'warn' : 'ok'} />
      </View>

      {alerts.length ? (
        <>
          <SectionTitle title="Open alerts" action={<Text style={styles.prov}>clear these first</Text>} />
          <Card style={{ padding: 4 }}>
            {alerts.map((al, i) => (
              <View key={al.alert_id} style={[styles.alert, i > 0 && { borderTopWidth: 1, borderTopColor: theme.color.border }]}>
                <Ionicons name="warning" size={18} color={al.severity === 'minor' ? theme.color.warn : theme.color.danger} />
                <View style={{ flex: 1 }}>
                  <Text style={styles.lineName}>{al.text}</Text>
                  <Text style={styles.meta}>{al.code} · raised {fmtDate(al.raised_at)}{al.action ? ` · ${al.action}` : ''}</Text>
                </View>
                <Pill label={al.severity} tone={SEVERITY_TONE[al.severity] ?? 'warn'} />
              </View>
            ))}
          </Card>
        </>
      ) : null}

      {plan?.briefing?.length ? (
        <>
          <SectionTitle title="Before you walk to the line" />
          <Card style={{ padding: 14, gap: 8 }}>
            {plan.briefing.map((t, i) => (
              <View key={i} style={{ flexDirection: 'row', gap: 8 }}>
                <Ionicons name="sparkles" size={14} color={theme.color.red} style={{ marginTop: 3 }} />
                <Text style={{ flex: 1, color: theme.color.text, lineHeight: 20 }}>{t}</Text>
              </View>
            ))}
          </Card>
        </>
      ) : null}

      <SectionTitle title="This week's schedule" action={<Text style={styles.prov}>week of {fmtDate(a.schedule.week_of, { month: 'short', day: 'numeric' })}</Text>} />
      <Card style={{ padding: 14, gap: 10 }}>
        <Meter label="Scheduled" value={scheduled} scale={scale} color={theme.color.slate} />
        <Meter label="Model recommends" value={recommended} scale={scale} color={theme.color.red} note={delta ? signed(delta) : 'on schedule'} />
        <Meter label="Line capacity" value={capacity} scale={scale} color={theme.color.faint} note={`${a.schedule.units_per_hour}/h`} />
        {a.ml_scores.build_reason ? <Text style={styles.meta}>Why: {a.ml_scores.build_reason}.</Text> : null}
        {sv?.overtime_needed ? (
          <View style={[styles.flag, { backgroundColor: theme.color.warnSoft }]}>
            <Ionicons name="time" size={16} color={theme.color.warn} />
            <Text style={styles.flagText}>{n0(recommended - capacity)} units over this week's capacity - about {sv.overtime_hours} hours of overtime, which the Plant Manager approves.</Text>
          </View>
        ) : null}
        {sv?.overtime_hours_approved ? <Text style={styles.meta}>Includes {sv.overtime_hours_approved} hours of approved overtime.</Text> : null}
        {a.schedule.released_order_id ? <Text style={styles.meta}>Released on {a.schedule.released_order_id}.</Text> : null}
      </Card>

      <SectionTitle title="Demand & forecast" action={<Text style={styles.prov}>{a.ml_scores.model_version}</Text>} />
      <Card style={{ padding: 14, gap: 12 }}>
        <View style={{ flexDirection: 'row', gap: 14, alignItems: 'flex-end' }}>
          <View style={{ flex: 3, gap: 4 }}>
            <Bars values={d.history_12mo.map((h) => h.units)} labels={d.history_12mo.map((h) => MONTHS[Number(h.month.slice(5)) - 1])} color={theme.color.slate} />
            <Text style={styles.chartLbl}>SALES · LAST 12 MONTHS</Text>
          </View>
          <View style={{ flex: 2, gap: 4 }}>
            <Bars values={d.forecast_8wk.map((f) => f.units)} labels={d.forecast_8wk.map((_, i) => `${i + 1}`)} color={theme.color.red} />
            <Text style={styles.chartLbl}>FORECAST · NEXT 8 WEEKS</Text>
          </View>
        </View>
        <View style={{ flexDirection: 'row', gap: 6, flexWrap: 'wrap' }}>
          <Pill label={`Last 4 wks ${n0(d.last_4wk_units)}`} tone="neutral" icon="bar-chart-outline" />
          <Pill label={`Next 4 wks ${n0(next4)} (${d.trend_pct > 0 ? '+' : ''}${d.trend_pct}%)`} tone={d.trend_pct >= 10 ? 'red' : d.trend_pct <= -10 ? 'info' : 'neutral'} icon={d.trend_pct >= 0 ? 'trending-up' : 'trending-down'} />
          <Pill label={`Forecast accuracy ${a.kpis.forecast_accuracy_pct}%`} tone={a.kpis.forecast_accuracy_pct < 85 ? 'warn' : 'ok'} icon="analytics-outline" />
          <Pill label={`Open orders ${n0(d.open_orders_units)}`} tone="neutral" icon="receipt-outline" />
        </View>
        {d.drivers?.length ? <Row icon="pulse-outline" label="Drivers" value={d.drivers.join('\n')} /> : null}
        {(a.market ?? []).map((m) => <Signal key={m.signal} m={m} />)}
        <Text style={styles.meta}>Top customers: {d.top_customers.join(', ')}</Text>
      </Card>

      <SectionTitle title="Inventory" />
      <Card style={{ padding: 14, gap: 8 }}>
        <Row icon="cube-outline" label="On hand" value={`${n0(iv.on_hand_units)} units finished goods${iv.wip_units ? ` · ${n0(iv.wip_units)} in process` : ''}`} />
        <Row icon="hourglass-outline" label="Cover" value={`${iv.days_of_cover} days against a ${iv.target_cover_days}-day target${iv.projected_cover_days ? ` · ${iv.projected_cover_days} days after this week's build` : ''}`} tone={coverPct < 60 ? 'danger' : coverPct < 90 ? 'warn' : undefined} />
        <View style={styles.bar}><View style={[styles.barFill, { width: `${coverPct}%`, backgroundColor: coverPct < 60 ? theme.color.danger : coverPct < 90 ? theme.color.warn : theme.color.ok }]} /></View>
        <Row icon="shield-outline" label="Safety stock" value={recSafety && recSafety !== iv.safety_stock_units ? `${n0(iv.safety_stock_units)} units · model recommends ${n0(recSafety)} (${signed(recSafety - iv.safety_stock_units)})` : `${n0(iv.safety_stock_units)} units · model would keep it`} tone={recSafety > iv.safety_stock_units ? 'warn' : undefined} />
        <Text style={styles.meta}>Fill rate {a.kpis.fill_rate_30d_pct}% in 30 days · schedule adherence {a.kpis.schedule_adherence_pct}% · OEE {a.kpis.oee_pct}%</Text>
      </Card>

      <SectionTitle title="Materials to order" action={plan ? <Text style={styles.prov}>{plan.order_type}</Text> : null} />
      <Card style={{ padding: 14, gap: 10 }}>
        {plan ? (
          <View style={{ flexDirection: 'row', gap: 6, flexWrap: 'wrap' }}>
            <Pill label={`${alertTasks} alert · ${tasks.length - alertTasks} readiness checks`} tone="neutral" icon="list-outline" />
            {plan.long_lead ? <Pill label="Long-lead items" tone="warn" icon="hourglass-outline" /> : null}
          </View>
        ) : null}
        {plan && selected.length ? (
          <>
            {selected.slice(0, 4).map((l) => (
              <View key={l.sku} style={styles.line}>
                <Text style={styles.qty}>{l.qty}×</Text>
                <View style={{ flex: 1 }}>
                  <Text style={styles.lineName} numberOfLines={1}>{l.name}</Text>
                  <Text style={styles.meta}>{l.reason}</Text>
                </View>
                <Text style={styles.lineTotal}>{money2(l.line_total)}</Text>
              </View>
            ))}
            {selected.length > 4 ? <Text style={styles.meta}>+ {selected.length - 4} more materials</Text> : null}
            <View style={[styles.line, { borderTopWidth: 1, borderTopColor: theme.color.border, paddingTop: 10 }]}>
              <Text style={{ flex: 1, fontWeight: '800', color: theme.color.ink }}>{selected.length} recommended materials</Text>
              <Text style={styles.total}>{money2(plan.plan_total_usd)}</Text>
            </View>
            {!plan.auth.ok ? (
              <View style={[styles.flag, plan.auth.on_hold ? { backgroundColor: theme.color.dangerSoft } : { backgroundColor: theme.color.warnSoft }]}>
                <Ionicons name="alert-circle" size={16} color={plan.auth.on_hold ? theme.color.danger : theme.color.warn} />
                <Text style={[styles.flagText, plan.auth.on_hold && { color: theme.color.danger }]}>
                  {plan.auth.on_hold
                    ? `Quality hold for ${c.hold_days} days - ${c.hold_reason}. Nothing can be released until Quality lifts it.`
                    : `${money2(plan.auth.shortfall_usd)} over the remaining material budget - release as a budget override for the Plant Controller.`}
                </Text>
              </View>
            ) : null}
          </>
        ) : (
          <Text style={styles.meta}>No material is close to running out according to this morning's scores. Confirm the build quantity and log the line check.</Text>
        )}
        <Button label="Open production order" icon="construct" onPress={() => router.push({ pathname: '/order', params: { product_id } })} />
        <View style={{ flexDirection: 'row', gap: 8 }}>
          <View style={{ flex: 1 }}><Button small kind="ghost" label="Ask assistant" icon="sparkles-outline" onPress={() => router.push('/(tabs)/assistant')} /></View>
          <View style={{ flex: 1 }}><Button small kind="ghost" label="Log line check" icon="clipboard-outline" onPress={() => router.push({ pathname: '/check', params: { product_id } })} /></View>
        </View>
      </Card>

      {a.coverage?.length ? (
        <>
          <SectionTitle title="Material cover" action={<Text style={styles.prov}>{a.ml_scores.segment}</Text>} />
          <Card style={{ padding: 14, gap: 10 }}>
            {a.coverage.map((x) => (
              <View key={x.coverage_id} style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
                <Text style={[styles.lineName, { width: 150 }]} numberOfLines={1}>{x.name}</Text>
                <HealthBar value={x.health} />
                <Text style={[styles.meta, { width: 44, textAlign: 'right', fontWeight: '800', color: x.status === 'ok' ? theme.color.text : x.status === 'short' ? theme.color.danger : theme.color.warn }]}>{x.days_cover}d</Text>
              </View>
            ))}
            <Text style={styles.meta}>Days of cover per material group, from usage and open purchase orders.</Text>
          </Card>
        </>
      ) : null}

      <SectionTitle title="Service class & budget" />
      <Card style={{ padding: 14, gap: 8 }}>
        <Row icon="ribbon-outline" label="Service" value={`${a.service.tier_name} · ${a.service.fill_rate_target_pct}% fill-rate target · supply agreement ${a.service.agreement_id} renews ${fmtDate(a.service.renewal_date)}`} />
        <Row icon="wallet-outline" label="Budget" value={`${money2(available)} left of ${money(c.budget_limit_usd)} this quarter · ${money2(c.committed_usd)} committed`} tone={c.on_hold ? 'danger' : available < c.budget_limit_usd * 0.25 ? 'warn' : undefined} />
        <View style={styles.bar}><View style={[styles.barFill, { width: `${Math.min(100, Math.round((c.committed_usd / c.budget_limit_usd) * 100))}%`, backgroundColor: c.on_hold ? theme.color.danger : theme.color.red }]} /></View>
        {c.on_hold ? <Row icon="lock-closed-outline" label="Hold" value={`${c.hold_days} days · ${c.hold_reason}`} tone="danger" /> : null}
        <Row icon="business-outline" label="Materials" value={`${warehouse?.name ?? a.line.warehouse}${warehouse ? ` (${warehouse.city})` : ''}`} />
        {a.standing_orders.length ? <Row icon="repeat-outline" label="Standing" value={a.standing_orders.map((s) => `${s.qty} × ${s.name.split(',')[0]} every ${s.cadence_weeks}w`).join('\n')} /> : null}
        {a.overtime_shifts?.length ? <Row icon="time-outline" label="Overtime" value={a.overtime_shifts.slice(0, 2).map((w) => `${w.shift_id} · ${w.hours} h · +${n0(w.added_units)} units · ${w.date}`).join('\n')} /> : null}
      </Card>

      <SectionTitle title="Order history" action={<Text style={styles.prov}>{money(a.spend.last_12mo_usd)} / 12 mo</Text>} />
      {a.order_history.slice(0, 6).map((o) => (
        <Card key={o.production_order_id} style={styles.hist}>
          <Ionicons name={o.source === 'planner' ? 'person' : o.source === 'mrp_run' ? 'calendar-outline' : o.source === 'auto_release' ? 'repeat-outline' : 'flash-outline'} size={16} color={theme.color.red} />
          <View style={{ flex: 1 }}>
            <Text style={styles.histTitle}>{o.production_order_id} · {fmtDate(o.date)}</Text>
            <Text style={styles.meta}>{o.order_type ?? 'Scheduled run'}{o.run_qty ? ` · ${n0(o.run_qty)} units` : ''} · {o.line_count} materials · {(o.source ?? '').replace(/_/g, ' ')}{o.backordered?.length ? ' · materials on order' : ''}{o.budget_override ? ' · budget override' : ''}</Text>
          </View>
          <Text style={styles.histTotal}>{money2(o.total_usd)}</Text>
        </Card>
      ))}

      {orders.length ? (
        <>
          <SectionTitle title="From this phone" />
          {orders.map((q) => {
            const ui = STATUS_UI[q.status] ?? STATUS_UI.pending;
            return (
              <Card key={q._id} style={styles.hist}>
                <Ionicons name={ui.icon} size={16} color={theme.color.red} />
                <View style={{ flex: 1 }}>
                  <Text style={styles.histTitle}>{q.title}</Text>
                  {q.summary ? <Text style={styles.meta} numberOfLines={2}>{q.summary}</Text> : null}
                </View>
                <Pill label={q._local ? 'On device' : ui.label} tone={q._local ? 'warn' : ui.tone} />
              </Card>
            );
          })}
        </>
      ) : null}
      <Text style={styles.tiny}>Product revision {a.rev} · export {a.export_date}. Every release bumps the revision, which invalidates the assistant's context cache for this product.</Text>
    </ScrollView>
  );
}

/** A row of bars sized to the largest value - enough to see the shape of demand without a chart library. */
function Bars({ values, labels, color }: { values: number[]; labels: string[]; color: string }) {
  const max = Math.max(...values, 1);
  return (
    <View>
      <View style={{ flexDirection: 'row', alignItems: 'flex-end', gap: 3, height: 64 }}>
        {values.map((v, i) => (
          <View key={i} style={{ flex: 1, height: Math.max(3, Math.round((v / max) * 64)), backgroundColor: color, borderTopLeftRadius: 2, borderTopRightRadius: 2 }} />
        ))}
      </View>
      <View style={{ flexDirection: 'row', gap: 3, marginTop: 2 }}>
        {labels.map((l, i) => <Text key={i} style={styles.tick}>{l}</Text>)}
      </View>
    </View>
  );
}

function Meter({ label, value, scale, color, note }: { label: string; value: number; scale: number; color: string; note?: string }) {
  return (
    <View style={{ gap: 4 }}>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <Text style={styles.meta}>{label}</Text>
        <Text style={{ fontWeight: '900', color: theme.color.ink }}>{n0(value)} <Text style={styles.meta}>{note ?? 'units'}</Text></Text>
      </View>
      <View style={styles.bar}><View style={[styles.barFill, { width: `${Math.max(2, Math.round((value / scale) * 100))}%`, backgroundColor: color }]} /></View>
    </View>
  );
}

function Signal({ m }: { m: MarketSignal }) {
  const up = m.direction === 'up';
  const flat = m.direction === 'flat';
  const color = flat ? theme.color.muted : up ? theme.color.warn : theme.color.info;
  return (
    <View style={{ flexDirection: 'row', gap: 10, alignItems: 'center' }}>
      <Ionicons name={flat ? 'remove' : up ? 'arrow-up' : 'arrow-down'} size={16} color={color} />
      <Text style={{ flex: 1, color: theme.color.text, fontWeight: '600', fontSize: theme.type.sm }}>{m.signal} <Text style={styles.meta}>· {m.note}</Text></Text>
      <Text style={{ color, fontWeight: '800', fontSize: theme.type.sm }}>{m.change_pct > 0 ? '+' : ''}{m.change_pct}%</Text>
    </View>
  );
}

function Kpi({ label, value, tone }: { label: string; value: string; tone?: 'ok' | 'warn' | 'danger' }) {
  const color = tone === 'danger' ? theme.color.danger : tone === 'warn' ? theme.color.warn : tone === 'ok' ? theme.color.ok : theme.color.ink;
  return (
    <Card style={styles.kpi}>
      <Text style={[styles.kpiNum, { color }]}>{value}</Text>
      <Text style={styles.kpiLbl}>{label}</Text>
    </Card>
  );
}

function Row({ icon, label, value, tone }: { icon: IconName; label: string; value: string; tone?: 'warn' | 'danger' }) {
  const color = tone === 'danger' ? theme.color.danger : tone === 'warn' ? theme.color.warn : theme.color.text;
  return (
    <View style={{ flexDirection: 'row', gap: 10, alignItems: 'flex-start' }}>
      <Ionicons name={icon} size={16} color={theme.color.red} style={{ marginTop: 1 }} />
      <Text style={[styles.meta, { width: 84 }]}>{label}</Text>
      <Text style={{ flex: 1, color, fontWeight: '600', fontSize: theme.type.sm, lineHeight: 19 }} selectable>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  body: { padding: 16, maxWidth: 640, width: '100%', alignSelf: 'center', paddingBottom: 40 },
  title: { color: theme.color.onDark, fontSize: theme.type.xl, fontWeight: '900' },
  code: { color: theme.color.onDarkMuted, fontSize: theme.type.xs, fontWeight: '800', letterSpacing: 1.4 },
  kpis: { flexDirection: 'row', gap: 8, marginTop: 12 },
  kpi: { flex: 1, padding: 12, alignItems: 'center', gap: 2 },
  kpiNum: { fontSize: theme.type.xl, fontWeight: '900' },
  kpiLbl: { color: theme.color.muted, fontSize: 9, fontWeight: '800', letterSpacing: 1 },
  alert: { flexDirection: 'row', gap: 10, alignItems: 'center', padding: 12 },
  meta: { color: theme.color.muted, fontSize: theme.type.sm, fontWeight: '400' },
  tiny: { color: theme.color.faint, fontSize: theme.type.xs, textAlign: 'center', marginTop: 18, lineHeight: 16 },
  prov: { color: theme.color.red, fontSize: theme.type.xs, fontWeight: '700' },
  chartLbl: { color: theme.color.muted, fontSize: 9, fontWeight: '800', letterSpacing: 1 },
  tick: { flex: 1, textAlign: 'center', color: theme.color.faint, fontSize: 8 },
  line: { flexDirection: 'row', gap: 10, alignItems: 'center' },
  qty: { width: 34, fontWeight: '900', color: theme.color.ink },
  lineName: { fontWeight: '700', color: theme.color.text, fontSize: theme.type.sm },
  lineTotal: { fontWeight: '800', color: theme.color.text },
  total: { fontWeight: '900', color: theme.color.red, fontSize: theme.type.lg },
  flag: { flexDirection: 'row', gap: 8, alignItems: 'center', borderRadius: theme.radiusSm, padding: 10 },
  flagText: { flex: 1, color: theme.color.warn, fontWeight: '700', fontSize: theme.type.sm },
  bar: { height: 6, borderRadius: 3, backgroundColor: theme.color.bg, overflow: 'hidden' },
  barFill: { height: 6, borderRadius: 3 },
  hist: { flexDirection: 'row', gap: 10, alignItems: 'center', padding: 12, marginBottom: 8 },
  histTitle: { fontWeight: '700', color: theme.color.text },
  histTotal: { fontWeight: '800', color: theme.color.text },
});
