import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { router } from 'expo-router';
import { Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useData } from '@/src/data/DataContext';
import { theme } from '@/src/theme';
import type { RouteStop } from '@/src/types';
import { Card, Empty, fmtDate, money, Pill, SectionTitle, STATUS_UI, SyncBadge, Wordmark } from '@/src/ui/components';

export default function Today() {
  const { snap, sync, refresh, setCurrentProductId } = useData();
  const insets = useSafeAreaInsets();
  const planner = snap.planner;
  const stops = planner?.route ?? [];
  const visited = stops.filter((s) => s.status === 'checked').length;
  const recent = snap.orders.slice(0, 3);
  const planned = stops.reduce((t, s) => t + (snap.plans[s.product_id]?.plan_total_usd ?? s.plan_total_usd ?? 0), 0);
  const buildDelta = stops.reduce((t, s) => t + (snap.plans[s.product_id]?.schedule?.delta_units ?? ((s.recommended_units ?? 0) - (s.scheduled_units ?? 0))), 0);
  const alertsToday = stops.reduce((t, s) => t + (snap.products[s.product_id]?.alerts?.length ?? s.alert_count ?? 0), 0);
  const hour = new Date().getHours();
  const greet = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';

  const open = (s: RouteStop) => {
    setCurrentProductId(s.product_id);
    router.push({ pathname: '/product', params: { product_id: s.product_id } });
  };

  return (
    <ScrollView style={{ flex: 1, backgroundColor: theme.color.bg }} refreshControl={<RefreshControl refreshing={false} onRefresh={() => void refresh()} />}>
      <LinearGradient colors={theme.gradient.hero as unknown as [string, string]} style={[styles.hero, { paddingTop: insets.top + 14 }]}>
        <View style={styles.row}>
          <Wordmark size={12} />
          <SyncBadge sync={sync} dark />
        </View>
        {planner ? (
          <>
            <Text style={styles.greet}>{greet}, {planner.first_name}</Text>
            <View style={styles.countRow}>
              <View>
                <Text style={styles.countNum}>{stops.length - visited}</Text>
                <Text style={styles.countLbl}>TO REVIEW</Text>
              </View>
              <View style={{ flex: 1, gap: 4 }}>
                <Text style={styles.terr}>{planner.plant_name}</Text>
                <Text style={styles.itin}>{fmtDate(planner.route_date, { weekday: 'long', month: 'long', day: 'numeric' })}</Text>
                <Text style={styles.itin}>{alertsToday} open alert{alertsToday === 1 ? '' : 's'} on today's floor walk · {buildDelta >= 0 ? '+' : ''}{buildDelta.toLocaleString('en-US')} units vs schedule · {money(planned)} of materials to order · {planner.product_count} products on this phone</Text>
              </View>
            </View>
            <View style={[styles.row, { marginTop: 14, justifyContent: 'flex-start', gap: 8, flexWrap: 'wrap' }]}>
              <Pill label={`${planner.due_this_week} plan reviews due this week`} tone="dark" icon="calendar-outline" />
              {planner.on_hold_products ? <Pill label={`${planner.on_hold_products} on quality hold`} tone="dark" icon="lock-closed-outline" /> : null}
              <Pill label={`Export ${planner.export_date}`} tone="dark" icon="analytics-outline" />
            </View>
          </>
        ) : (
          <Text style={[styles.greet, { marginTop: 24 }]}>Loading today's plan from Couchbase Lite…</Text>
        )}
      </LinearGradient>

      <View style={styles.body}>
        {recent.length ? (
          <>
            <SectionTitle title="Recent production orders & actions" action={<Pressable onPress={() => router.push('/(tabs)/orders')}><Text style={styles.link}>All</Text></Pressable>} />
            <Card style={{ padding: 4 }}>
              {recent.map((q, i) => {
                const ui = STATUS_UI[q.status] ?? STATUS_UI.pending;
                return (
                  <View key={q._id} style={[styles.req, i > 0 && styles.reqBorder]}>
                    <Ionicons name={ui.icon} size={20} color={theme.color.red} />
                    <View style={{ flex: 1 }}>
                      <Text style={styles.reqTitle}>{q.title}</Text>
                      <Text style={styles.reqSub} numberOfLines={2}>
                        {q.summary ?? (q._local || q.status === 'pending' ? 'Saved on this phone - sends automatically when you have a connection.' : '')}
                      </Text>
                    </View>
                    <Pill label={q._local ? 'On device' : ui.label} tone={q._local ? 'warn' : ui.tone} />
                  </View>
                );
              })}
            </Card>
          </>
        ) : null}

        <SectionTitle title="Today's floor walk" action={planner ? <Text style={styles.prov}>{visited}/{stops.length} checked</Text> : null} />
        {stops.length === 0 ? (
          <Empty icon="today-outline" title="Nothing to review today" body="Your plan syncs from Couchbase in a moment." />
        ) : (
          stops.map((s, i) => <StopCard key={s.product_id} s={s} n={i + 1} onPress={() => open(s)} alerts={snap.products[s.product_id]?.alerts?.length ?? s.alert_count ?? 0} workType={snap.plans[s.product_id]?.order_type ?? s.order_type ?? null} />)
        )}
        {planner ? (
          <Text style={styles.footnote}>
            Forecasts, schedules, alerts and production plans come from this morning's Databricks export (model mfg-demand-planning) and live on this phone in Couchbase Lite.
            No warehouse query runs when you open a product.
          </Text>
        ) : null}
      </View>
    </ScrollView>
  );
}

function StopCard({ s, n, onPress, alerts, workType }: { s: RouteStop; n: number; onPress: () => void; alerts: number; workType: string | null }) {
  const expedite = /expedite|rush/i.test(workType ?? '');
  const delta = (s.recommended_units ?? 0) - (s.scheduled_units ?? 0);
  const noSignal = !/good signal/i.test(s.signal_note ?? '');
  return (
    <Pressable onPress={onPress} style={({ pressed }) => [{ marginBottom: 12, opacity: pressed ? 0.92 : 1 }]}>
      <Card style={[styles.stop, s.status === 'checked' && { opacity: 0.6 }]}>
        <View style={[styles.num, s.status === 'checked' && { backgroundColor: theme.color.ok }]}>
          {s.status === 'checked' ? <Ionicons name="checkmark" size={16} color={theme.color.onDark} /> : <Text style={styles.numText}>{n}</Text>}
        </View>
        <View style={{ flex: 1, gap: 4 }}>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
            <Text style={styles.stopTitle} numberOfLines={1}>{s.product_name}</Text>
            <Text style={styles.time}>{s.planned_time}</Text>
          </View>
          <Text style={styles.meta}>{s.line ?? s.line_type}{s.building ? ` · ${s.building}` : ''}</Text>
          {s.recommended_units ? <Text style={styles.meta}>Build {s.recommended_units.toLocaleString('en-US')} this week{delta ? ` (${delta > 0 ? '+' : ''}${delta.toLocaleString('en-US')} vs schedule)` : ' · on schedule'}</Text> : null}
          <View style={{ flexDirection: 'row', gap: 6, flexWrap: 'wrap', marginTop: 2 }}>
            <Pill label={s.tier_name ?? s.tier} tone="neutral" icon="ribbon-outline" />
            {workType ? <Pill label={workType} tone={expedite ? 'danger' : 'ok'} icon="construct-outline" /> : null}
            {alerts ? <Pill label={`${alerts} alert${alerts > 1 ? 's' : ''}`} tone="danger" icon="warning-outline" /> : null}
            {s.hold_flag ? <Pill label="Quality hold" tone="danger" icon="lock-closed" /> : null}
            {s.overtime_needed ? <Pill label="Over capacity" tone="warn" icon="time-outline" /> : null}
            {noSignal ? <Pill label={s.signal_note} tone="warn" icon="cellular-outline" /> : null}
          </View>
        </View>
        <Ionicons name="chevron-forward" size={18} color={theme.color.faint} />
      </Card>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  hero: { paddingHorizontal: 20, paddingBottom: 26, borderBottomLeftRadius: 28, borderBottomRightRadius: 28 },
  row: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  greet: { color: theme.color.onDark, fontSize: theme.type.title, fontWeight: '900', marginTop: 22 },
  countRow: { flexDirection: 'row', gap: 18, marginTop: 14, alignItems: 'center' },
  countNum: { color: theme.color.onDark, fontSize: 52, fontWeight: '900', lineHeight: 56 },
  countLbl: { color: theme.color.onDarkMuted, fontSize: theme.type.xs, fontWeight: '800', letterSpacing: 1.4 },
  terr: { color: theme.color.onDark, fontSize: theme.type.xl, fontWeight: '800' },
  itin: { color: theme.color.onDarkMuted, fontSize: theme.type.sm },
  body: { padding: 16, maxWidth: 640, width: '100%', alignSelf: 'center' },
  req: { flexDirection: 'row', gap: 10, alignItems: 'center', padding: 12 },
  reqBorder: { borderTopWidth: 1, borderTopColor: theme.color.border },
  reqTitle: { fontWeight: '800', color: theme.color.text },
  reqSub: { color: theme.color.muted, fontSize: theme.type.sm },
  prov: { color: theme.color.red, fontSize: theme.type.xs, fontWeight: '700' },
  link: { color: theme.color.red, fontSize: theme.type.sm, fontWeight: '800' },
  stop: { flexDirection: 'row', gap: 12, alignItems: 'center', padding: 14 },
  num: { width: 30, height: 30, borderRadius: 15, backgroundColor: theme.color.red, alignItems: 'center', justifyContent: 'center' },
  numText: { color: theme.color.onDark, fontWeight: '900' },
  stopTitle: { fontWeight: '900', color: theme.color.ink, fontSize: theme.type.md, flex: 1, marginRight: 8 },
  time: { color: theme.color.muted, fontWeight: '700', fontSize: theme.type.sm },
  meta: { color: theme.color.muted, fontSize: theme.type.sm },
  footnote: { color: theme.color.faint, fontSize: theme.type.xs, textAlign: 'center', marginVertical: 18, lineHeight: 16 },
});
