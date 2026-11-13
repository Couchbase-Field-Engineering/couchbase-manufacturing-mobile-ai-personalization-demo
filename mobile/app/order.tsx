import { Ionicons } from '@expo/vector-icons';
import { useLocalSearchParams } from 'expo-router';
import { useMemo, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { useData } from '@/src/data/DataContext';
import { theme } from '@/src/theme';
import type { Material, PlanLine, PlanTask } from '@/src/types';
import { ChoiceRow } from '@/src/ui/Choice';
import { Button, Card, Empty, money2, Pill, SectionTitle, SEVERITY_TONE } from '@/src/ui/components';
import { openAction } from '@/src/ui/nav';
import { ProductionOrderTracker } from '@/src/ui/ProductionOrderTracker';

type Line = {
  sku: string; name: string; uom: string; qty: number; unit_cost: number; on_hand: number | null;
  lead_time_days: number; long_lead: boolean; reason?: string; days_to_stockout?: number | null;
};
const n0 = (v: number) => Math.round(v).toLocaleString('en-US');

/**
 * The production order: how much to build this week, the alerts to clear,
 * the line's readiness checklist and the materials to order - all read from
 * Couchbase Lite and costed on the phone, so it works in a press shop with
 * no Wi-Fi.
 */
export default function ProductionOrderScreen() {
  const { product_id = '' } = useLocalSearchParams<{ product_id: string }>();
  const { snap, request, offline } = useData();
  const a = snap.products[product_id];
  const plan = snap.plans[product_id];
  const cat = snap.catalog;
  const [lines, setLines] = useState<Line[]>(() =>
    (plan?.lines ?? []).filter((l: PlanLine) => l.selected).map((l) => ({
      sku: l.sku, name: l.name, uom: l.uom, qty: l.qty, unit_cost: l.unit_cost, on_hand: l.on_hand_at_warehouse,
      lead_time_days: l.lead_time_days, long_lead: l.long_lead, reason: l.reason, days_to_stockout: l.days_to_stockout,
    })),
  );
  const scheduled = plan?.schedule.scheduled_units ?? a?.schedule.scheduled_units ?? 0;
  const recommended = plan?.schedule.recommended_units ?? a?.ml_scores.recommended_build_units ?? scheduled;
  const capacity = plan?.schedule.capacity_units ?? a?.schedule.capacity_units ?? 0;
  const rate = a?.schedule.units_per_hour ?? 0;
  const [runQty, setRunQty] = useState<number>(() => (capacity ? Math.min(recommended, capacity) : recommended));
  const tasks: PlanTask[] = plan?.tasks ?? [];
  const [done, setDone] = useState<Record<string, boolean>>({});
  const [reference, setReference] = useState('');
  const [notes, setNotes] = useState('');
  const [orderType, setOrderType] = useState<string>(plan?.order_type ?? 'Scheduled run');
  const [q, setQ] = useState('');
  const [reqId, setReqId] = useState<string | null>(null);
  const [overrideReason, setOverrideReason] = useState('Stockout predicted inside the supplier lead time; materials exceed the remaining material budget');

  const results = useMemo(() => {
    const m = q.trim().toLowerCase();
    if (!m || !cat) return [] as Material[];
    return cat.materials.filter((p) => `${p.name} ${p.sku} ${p.tags.join(' ')} ${cat.categories[p.category]}`.toLowerCase().includes(m)).slice(0, 6);
  }, [q, cat]);

  if (!a) return <Empty icon="construct-outline" title="Product not on this phone" body="Open a product from today's floor walk first." />;

  const costed = lines.map((l) => ({ ...l, total: Math.round(l.unit_cost * l.qty * 100) / 100 }));
  const total = Math.round(costed.reduce((s, l) => s + l.total, 0) * 100) / 100;
  const c = a.auth;
  const available = Math.round((c.budget_limit_usd + (c.temporary_extension_usd ?? 0) - c.committed_usd) * 100) / 100;
  const shortfall = Math.max(0, Math.round((total - available) * 100) / 100);
  const overBudget = total > available && !c.on_hold;
  const short = costed.filter((l) => l.on_hand != null && l.on_hand < l.qty);
  const longLead = costed.filter((l) => l.long_lead);
  const overCapacity = capacity > 0 && runQty > capacity ? runQty - capacity : 0;
  const overtimeHours = overCapacity && rate ? Math.min(16, Math.ceil(overCapacity / rate)) : 0;
  const doneCount = tasks.filter((t) => done[t.id]).length;
  const alertTasks = tasks.filter((t) => t.kind === 'alert');
  const readiness = tasks.filter((t) => t.kind === 'readiness');
  const step = runQty >= 1000 ? 50 : 10;
  const daily = (a.ml_scores.forecast_next_4wk_units ?? 0) / 28;
  const coverAfter = daily ? Math.round(((a.inventory.on_hand_units + runQty) / daily) * 10) / 10 : null;

  const setQty = (sku: string, qty: number) => setLines((ls) => ls.map((l) => (l.sku === sku ? { ...l, qty: Math.max(1, Math.min(100, qty)) } : l)));
  const remove = (sku: string) => setLines((ls) => ls.filter((l) => l.sku !== sku));
  const add = (p: { sku: string; name: string; uom: string; unit_cost: number; lead_time_days?: number; long_lead?: boolean }, onHand: number | null = null) => {
    setLines((ls) =>
      ls.some((l) => l.sku === p.sku)
        ? ls.map((l) => (l.sku === p.sku ? { ...l, qty: l.qty + 1 } : l))
        : [...ls, { sku: p.sku, name: p.name, uom: p.uom, qty: 1, unit_cost: p.unit_cost, on_hand: onHand, lead_time_days: p.lead_time_days ?? 0, long_lead: !!p.long_lead }],
    );
    setQ('');
  };

  const submit = async (asOverride: boolean) => {
    const payload = {
      product_id,
      lines: costed.map((l) => ({ sku: l.sku, qty: l.qty })),
      run_qty: runQty,
      reference: reference.trim(),
      order_type: orderType,
      notes: notes.trim(),
      tasks_done: tasks.filter((t) => done[t.id]).map((t) => t.title),
    };
    const title = `${asOverride ? 'Budget override' : 'Production order'} · ${a.product_name} · ${n0(runQty)} units · ${money2(total)}`;
    const req = asOverride
      ? await request('request_budget_override', { ...payload, reason: overrideReason.trim() || 'Materials exceed the remaining material budget' }, title)
      : await request('release_production_order', payload, title);
    setReqId(req._id);
  };

  const notOnOrder = (plan?.lines ?? []).filter((l) => !lines.some((x) => x.sku === l.sku));
  const forwardBuys = (plan?.forward_buys ?? []).filter((l) => !lines.some((x) => x.sku === l.sku));
  const warehouseName = cat?.warehouses[a.line.warehouse]?.name ?? a.line.warehouse;
  const canRelease = (lines.length > 0 || runQty > 0) && !c.on_hold && !overCapacity;

  const TaskRow = ({ t }: { t: PlanTask }) => (
    <Pressable onPress={() => setDone((d) => ({ ...d, [t.id]: !d[t.id] }))} style={styles.task}>
      <Ionicons name={done[t.id] ? 'checkbox' : 'square-outline'} size={22} color={done[t.id] ? theme.color.ok : theme.color.faint} />
      <View style={{ flex: 1 }}>
        <Text style={[styles.lineName, done[t.id] && { color: theme.color.muted, textDecorationLine: 'line-through' }]}>{t.title}</Text>
        {t.alert ? <Text style={styles.meta}>Alert: {t.alert}</Text> : null}
      </View>
      {t.severity ? <Pill label={t.severity} tone={SEVERITY_TONE[t.severity] ?? 'warn'} /> : null}
    </Pressable>
  );

  return (
    <ScrollView contentContainerStyle={styles.body} keyboardShouldPersistTaps="handled">
      <Card style={{ padding: 14, gap: 4 }}>
        <Text style={styles.meta}>PRODUCTION ORDER FOR</Text>
        <Text style={styles.h2}>{a.product_name}</Text>
        <Text style={styles.meta}>{a.line.name} · {a.line.building} · {a.service.tier_name}, {a.service.fill_rate_target_pct}% fill rate · materials from {warehouseName}</Text>
      </Card>

      {reqId ? (
        <ProductionOrderTracker id={reqId} onRetry={() => setReqId(null)} />
      ) : (
        <>
          <SectionTitle title="Order type" />
          <ChoiceRow options={(cat?.order_types ?? ['Scheduled run', 'Expedite']).map((w) => ({ value: w, label: w }))} value={orderType} onChange={setOrderType} />

          <SectionTitle title="Build this week" action={<Text style={styles.prov}>from the demand forecast</Text>} />
          <Card style={{ padding: 14, gap: 10 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
              <Pressable onPress={() => setRunQty((v) => Math.max(0, v - step))} style={styles.bigStep}><Ionicons name="remove" size={20} color={theme.color.ink} /></Pressable>
              <View style={{ alignItems: 'center' }}>
                <Text style={styles.run}>{n0(runQty)}</Text>
                <Text style={styles.meta}>units on {a.line.name}</Text>
              </View>
              <Pressable onPress={() => setRunQty((v) => v + step)} style={styles.bigStep}><Ionicons name="add" size={20} color={theme.color.ink} /></Pressable>
            </View>
            <View style={{ flexDirection: 'row', gap: 6, flexWrap: 'wrap', justifyContent: 'center' }}>
              <Pressable onPress={() => setRunQty(scheduled)}><Pill label={`Scheduled ${n0(scheduled)}`} tone={runQty === scheduled ? 'info' : 'neutral'} /></Pressable>
              <Pressable onPress={() => setRunQty(recommended)}><Pill label={`Model recommends ${n0(recommended)}`} tone={runQty === recommended ? 'red' : 'neutral'} icon="sparkles-outline" /></Pressable>
              <Pressable onPress={() => setRunQty(capacity)}><Pill label={`Capacity ${n0(capacity)}`} tone={overCapacity ? 'warn' : 'neutral'} icon="speedometer-outline" /></Pressable>
            </View>
            {a.ml_scores.build_reason ? <Text style={styles.meta}>Why {n0(recommended)}: {a.ml_scores.build_reason}.</Text> : null}
            {coverAfter != null ? <Text style={styles.meta}>Cover after this build: about {coverAfter} days against a {a.inventory.target_cover_days}-day target ({a.inventory.days_of_cover} today).</Text> : null}
          </Card>

          {overCapacity ? (
            <Card style={[styles.sugg, { marginTop: 10, backgroundColor: theme.color.warnSoft }]}>
              <Ionicons name="time" size={20} color={theme.color.warn} />
              <View style={{ flex: 1 }}>
                <Text style={[styles.lineName, { color: theme.color.warn }]}>{n0(overCapacity)} units over this week's capacity · about {overtimeHours} h of overtime</Text>
                <Text style={styles.meta}>The Plant Manager has to approve an overtime shift before this quantity can be released.</Text>
              </View>
              <Button small kind="light" label="Request" onPress={() =>
                openAction({ label: 'Overtime shift', tool: 'request_overtime_shift', args: { product_id, hours: overtimeHours, reason: `Build of ${n0(runQty)} units is ${n0(overCapacity)} over this week's capacity on ${a.line.name}` } },
                  `Overtime shift · ${a.product_name} · ${overtimeHours} h`)} />
            </Card>
          ) : null}

          {alertTasks.length ? (
            <>
              <SectionTitle title={`Alerts · ${alertTasks.length} open`} action={<Text style={styles.prov}>clear these first</Text>} />
              <Card style={{ padding: 4 }}>{alertTasks.map((t) => <TaskRow key={t.id} t={t} />)}</Card>
            </>
          ) : null}

          <SectionTitle title={`Run readiness · ${readiness.filter((t) => done[t.id]).length}/${readiness.length}`} />
          <Card style={{ padding: 4 }}>
            {readiness.length ? readiness.map((t) => <TaskRow key={t.id} t={t} />) : <Text style={[styles.meta, { padding: 12 }]}>No checklist for this line type.</Text>}
          </Card>

          <SectionTitle title={`Materials to order · ${lines.length}`} action={plan ? <Text style={styles.prov}>pre-filled by the model</Text> : null} />
          <Card style={{ padding: 4 }}>
            {costed.length === 0 ? <Text style={[styles.meta, { padding: 12 }]}>No materials yet - add from the catalog below, or release the build quantity on its own.</Text> : null}
            {costed.map((l, i) => (
              <View key={l.sku} style={[styles.line, i > 0 && styles.lineBorder]}>
                <View style={{ flex: 1, gap: 2 }}>
                  <Text style={styles.lineName} numberOfLines={2}>{l.name}</Text>
                  <Text style={styles.meta}>
                    {money2(l.unit_cost)}/{l.uom}
                    {l.on_hand != null ? ` · ${l.on_hand} at warehouse` : ''}
                    {l.days_to_stockout != null && l.days_to_stockout <= 7 ? ` · runs out in ~${Math.max(0, l.days_to_stockout)}d` : ''}
                  </Text>
                  {l.long_lead ? <Text style={[styles.meta, { color: theme.color.danger, fontWeight: '700' }]}>Long-lead · {l.lead_time_days} days from the supplier</Text> : null}
                  {l.on_hand != null && l.on_hand < l.qty ? <Text style={[styles.meta, { color: theme.color.warn, fontWeight: '700' }]}>Only {l.on_hand} on hand - {l.qty - l.on_hand} will go on order</Text> : null}
                </View>
                <View style={styles.stepper}>
                  <Pressable onPress={() => (l.qty <= 1 ? remove(l.sku) : setQty(l.sku, l.qty - 1))} style={styles.step}><Ionicons name={l.qty <= 1 ? 'trash-outline' : 'remove'} size={16} color={theme.color.ink} /></Pressable>
                  <Text style={styles.qty}>{l.qty}</Text>
                  <Pressable onPress={() => setQty(l.sku, l.qty + 1)} style={styles.step}><Ionicons name="add" size={16} color={theme.color.ink} /></Pressable>
                </View>
                <Text style={styles.lineTotal}>{money2(l.total)}</Text>
              </View>
            ))}
          </Card>

          {notOnOrder.length || forwardBuys.length ? (
            <>
              <SectionTitle title="Also recommended" />
              <View style={{ gap: 8 }}>
                {notOnOrder.map((l) => (
                  <Pressable key={l.sku} onPress={() => add(l, l.on_hand_at_warehouse)}>
                    <Card style={styles.sugg}>
                      <Ionicons name="add-circle" size={20} color={theme.color.red} />
                      <View style={{ flex: 1 }}>
                        <Text style={styles.lineName} numberOfLines={1}>{l.qty} × {l.name}</Text>
                        <Text style={styles.meta}>{l.reason} · {money2(l.unit_cost)}/{l.uom}</Text>
                      </View>
                      <Pill label={`${Math.round((l.confidence ?? 0) * 100)}%`} tone="neutral" />
                    </Card>
                  </Pressable>
                ))}
                {forwardBuys.map((l) => (
                  <Pressable key={l.sku} onPress={() => add(l, l.on_hand_at_warehouse)}>
                    <Card style={styles.sugg}>
                      <Ionicons name="trending-up" size={20} color={theme.color.info} />
                      <View style={{ flex: 1 }}>
                        <Text style={styles.lineName} numberOfLines={1}>{l.name}</Text>
                        <Text style={styles.meta}>{l.reason} · {money2(l.unit_cost)}/{l.uom}</Text>
                      </View>
                      <Pill label="Forward buy" tone="info" />
                    </Card>
                  </Pressable>
                ))}
              </View>
            </>
          ) : null}

          <SectionTitle title="Add from the materials catalog" />
          <View style={styles.search}>
            <Ionicons name="search" size={16} color={theme.color.faint} />
            <TextInput value={q} onChangeText={setQ} placeholder="Casting, resin, bearing, harness…" placeholderTextColor={theme.color.faint} style={styles.input} />
          </View>
          {results.map((p) => (
            <Pressable key={p.sku} onPress={() => add(p)}>
              <Card style={styles.sugg}>
                <Ionicons name="add-circle-outline" size={20} color={theme.color.red} />
                <View style={{ flex: 1 }}>
                  <Text style={styles.lineName} numberOfLines={1}>{p.name}</Text>
                  <Text style={styles.meta}>{p.sku} · {p.spec} · {money2(p.unit_cost)}/{p.uom} · {p.lead_time_days}d lead time{a.usage[p.sku] ? ` · last ordered for this product ${a.usage[p.sku].last_ordered}` : ''}</Text>
                </View>
              </Card>
            </Pressable>
          ))}

          <SectionTitle title="Reference" />
          <Card style={{ padding: 14, gap: 10 }}>
            <TextInput value={reference} onChangeText={setReference} placeholder="Sales order or customer PO (optional)" placeholderTextColor={theme.color.faint} style={styles.field} />
            <TextInput value={notes} onChangeText={setNotes} placeholder="Notes for the supervisor and the buyer (optional)" placeholderTextColor={theme.color.faint} style={styles.field} multiline />
          </Card>

          <SectionTitle title="Release" />
          <Card style={{ padding: 14, gap: 6 }}>
            <Tot label="Build this week" text={`${n0(runQty)} units${runQty !== scheduled ? ` (${runQty > scheduled ? '+' : ''}${n0(runQty - scheduled)} vs schedule)` : ''}`} />
            <Tot label="Alerts and readiness checks done" text={`${doneCount} of ${tasks.length}`} />
            <View style={[styles.tot, { borderTopWidth: 1, borderTopColor: theme.color.border, paddingTop: 8, marginTop: 2 }]}>
              <Text style={{ fontWeight: '900', color: theme.color.ink, fontSize: theme.type.lg }}>Materials total</Text>
              <Text style={{ fontWeight: '900', color: theme.color.red, fontSize: theme.type.xl }}>{money2(total)}</Text>
            </View>
            <View style={{ flexDirection: 'row', gap: 6, flexWrap: 'wrap' }}>
              <Pill label={`Material budget left ${money2(available)}`} tone={c.on_hold ? 'danger' : overBudget ? 'warn' : 'ok'} icon="wallet-outline" />
              {short.length ? <Pill label={`${short.length} material${short.length > 1 ? 's' : ''} short at warehouse`} tone="warn" icon="cube-outline" /> : null}
              {longLead.length ? <Pill label={`${longLead.length} long-lead`} tone="warn" icon="hourglass-outline" /> : null}
              {offline ? <Pill label="Will send when connected" tone="warn" icon="cloud-upload-outline" /> : null}
            </View>
            {c.on_hold ? (
              <Text style={styles.warn}>This product is on a quality hold ({c.hold_days} days: {c.hold_reason}). Releases will be refused until Quality lifts it.</Text>
            ) : overCapacity ? (
              <Text style={styles.warn}>{n0(runQty)} units is over this week's capacity of {n0(capacity)}. Request the overtime shift above, or bring the quantity down to capacity.</Text>
            ) : overBudget ? (
              <>
                <Text style={styles.warn}>{money2(shortfall)} over the product's remaining material budget. Release as a budget override and the Plant Controller will approve it in the Couchbase Labs Agent Operations Manager.</Text>
                <TextInput value={overrideReason} onChangeText={setOverrideReason} placeholder="Reason for the override" placeholderTextColor={theme.color.faint} style={styles.field} />
                <Button label={`Release as budget override · ${money2(total)}`} icon="shield-checkmark" onPress={() => void submit(true)} disabled={!lines.length} />
                <Button label="Try as a normal release anyway" kind="ghost" onPress={() => void submit(false)} disabled={!lines.length} />
              </>
            ) : (
              <Button label={`Release production order · ${n0(runQty)} units`} icon="checkmark-done" onPress={() => void submit(false)} disabled={!canRelease} />
            )}
            <Text style={styles.tiny}>Costed on this phone from the materials catalog in Couchbase Lite. The order is saved locally and sent through Sync Gateway when you have a connection; Couchbase Labs Agent Operations Manager runs it as a governed tool call.</Text>
          </Card>
        </>
      )}
    </ScrollView>
  );
}

function Tot({ label, text }: { label: string; text: string }) {
  return (
    <View style={styles.tot}>
      <Text style={styles.meta}>{label}</Text>
      <Text style={{ fontWeight: '700', color: theme.color.text }}>{text}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  body: { padding: 16, maxWidth: 640, width: '100%', alignSelf: 'center', paddingBottom: 40 },
  h2: { fontSize: theme.type.xl, fontWeight: '900', color: theme.color.ink },
  meta: { color: theme.color.muted, fontSize: theme.type.sm },
  tiny: { color: theme.color.faint, fontSize: theme.type.xs, textAlign: 'center', lineHeight: 16 },
  warn: { color: theme.color.warn, fontWeight: '700', fontSize: theme.type.sm, lineHeight: 19 },
  prov: { color: theme.color.red, fontSize: theme.type.xs, fontWeight: '700' },
  run: { fontSize: 40, fontWeight: '900', color: theme.color.ink, lineHeight: 44 },
  bigStep: { width: 44, height: 44, borderRadius: 22, backgroundColor: theme.color.bg, alignItems: 'center', justifyContent: 'center' },
  task: { flexDirection: 'row', gap: 10, alignItems: 'center', padding: 12 },
  line: { flexDirection: 'row', gap: 10, alignItems: 'center', padding: 12 },
  lineBorder: { borderTopWidth: 1, borderTopColor: theme.color.border },
  lineName: { fontWeight: '700', color: theme.color.text, fontSize: theme.type.sm },
  lineTotal: { fontWeight: '800', color: theme.color.text, width: 86, textAlign: 'right' },
  stepper: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  step: { width: 30, height: 30, borderRadius: 15, backgroundColor: theme.color.bg, alignItems: 'center', justifyContent: 'center' },
  qty: { width: 26, textAlign: 'center', fontWeight: '900', color: theme.color.ink },
  sugg: { flexDirection: 'row', gap: 10, alignItems: 'center', padding: 12, marginBottom: 2 },
  search: { flexDirection: 'row', gap: 8, alignItems: 'center', backgroundColor: theme.color.surface, borderRadius: 999, paddingHorizontal: 14, borderWidth: 1, borderColor: theme.color.border, marginBottom: 8 },
  input: { flex: 1, paddingVertical: 10, fontSize: theme.type.md, color: theme.color.text },
  field: { borderWidth: 1, borderColor: theme.color.border, borderRadius: theme.radiusSm, padding: 11, fontSize: theme.type.md, color: theme.color.text },
  tot: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
});
