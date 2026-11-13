import { Ionicons } from '@expo/vector-icons';
import { router } from 'expo-router';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';
import { useData } from '../data/DataContext';
import { theme } from '../theme';
import { Button, Card, Pill, STATUS_UI, money2 } from './components';
import { openAction } from './nav';

/**
 * Live status of one offline-first production order or planning action, read from the
 * local database: "On device" -> pushed -> processed by the backend through
 * AOM -> synced back to this card.
 */
export function ProductionOrderTracker({ id, onRetry, retryLabel = 'Try again' }: { id: string; onRetry?: () => void; retryLabel?: string }) {
  const { snap, sync } = useData();
  const q = snap.orders.find((r) => r._id === id);
  if (!q) return null;
  const ui = STATUS_UI[q.status] ?? STATUS_UI.pending;
  const waiting = q._local || q.status === 'pending' || q.status === 'processing';
  const steps = [
    { label: 'Saved in Couchbase Lite on this phone', done: true },
    { label: sync.status === 'paused' || sync.status === 'offline' ? 'Waiting for a connection…' : 'Synced to Couchbase through Sync Gateway', done: !q._local },
    { label: q.governed === false ? 'Processed by the backend (AOM not configured - ungoverned)' : 'Run by the Planning Assistant agent through Couchbase AOM', done: !waiting },
    { label: q.status === 'awaiting_approval' ? (q.tool === 'request_overtime_shift' ? 'Waiting for the Plant Manager\'s approval' : 'Waiting for the Plant Controller\'s approval') : 'Result synced back to this phone', done: !waiting && q.status !== 'awaiting_approval' },
  ];
  const budgetLimit = q.status === 'failed' && q.reason_code === 'budget_limit';
  return (
    <Card style={{ padding: 16, gap: 12, marginTop: 16 }}>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
        <Text style={styles.title}>{q.title}</Text>
        <Pill label={q._local ? 'On device' : ui.label} tone={q._local ? 'warn' : ui.tone} icon={ui.icon} />
      </View>
      {steps.map((s, i) => (
        <View key={i} style={{ flexDirection: 'row', gap: 10, alignItems: 'center' }}>
          {s.done ? (
            <Ionicons name="checkmark-circle" size={18} color={theme.color.ok} />
          ) : i === steps.findIndex((x) => !x.done) && sync.status !== 'paused' ? (
            <ActivityIndicator size="small" color={theme.color.red} />
          ) : (
            <Ionicons name="ellipse-outline" size={18} color={theme.color.faint} />
          )}
          <Text style={{ color: s.done ? theme.color.text : theme.color.muted, flex: 1 }}>{s.label}</Text>
        </View>
      ))}
      {q.summary ? <Text style={[styles.summary, q.status === 'failed' && { color: theme.color.danger }]}>{q.summary}</Text> : null}
      {q.item?.backordered?.length ? (
        <View style={styles.bo}>
          {q.item.backordered.map((b) => (
            <Text key={b.sku} style={styles.boText}>On order: {b.qty} × {b.name.split(',')[0]} · ETA {b.eta}</Text>
          ))}
        </View>
      ) : null}
      {q.governed ? <Text style={styles.tiny}>Governed by Agent Operations Manager{q.trace_id ? ` · trace ${q.trace_id}` : ''}</Text> : null}
      {budgetLimit ? (
        <Button
          label={`Submit as budget override${q.auth ? ` · ${money2(q.auth.shortfall_usd)} over` : ''}`}
          icon="shield-checkmark-outline"
          onPress={() =>
            openAction(
              { label: 'Budget override', tool: 'request_budget_override', args: { ...q.args, reason: 'Materials exceed the remaining material budget; stockout predicted inside the supplier lead time' } },
              `Budget override · ${q.product_name ?? q.product_id}`,
            )
          }
        />
      ) : null}
      {q.status === 'failed' && onRetry && !budgetLimit ? <Button label={retryLabel} icon="refresh" onPress={onRetry} /> : null}
      <Button
        label={waiting ? 'Done - it will finish in the background' : 'Done'}
        kind={waiting || q.status === 'failed' ? 'ghost' : 'dark'}
        onPress={() => (router.canGoBack() ? router.back() : router.replace('/(tabs)'))}
      />
    </Card>
  );
}

const styles = StyleSheet.create({
  title: { fontWeight: '900', color: theme.color.ink, fontSize: theme.type.lg, flex: 1, marginRight: 8 },
  summary: { color: theme.color.text, fontSize: theme.type.md, lineHeight: 21 },
  tiny: { color: theme.color.faint, fontSize: theme.type.xs },
  bo: { backgroundColor: theme.color.infoSoft, borderRadius: theme.radiusSm, padding: 10, gap: 4 },
  boText: { color: theme.color.info, fontSize: theme.type.sm, fontWeight: '600' },
});
