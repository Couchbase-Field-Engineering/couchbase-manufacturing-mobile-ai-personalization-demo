import { useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { ScrollView, StyleSheet, Text, TextInput } from 'react-native';
import { useData } from '@/src/data/DataContext';
import { theme } from '@/src/theme';
import { Button, Card, Empty, money2, Pill } from '@/src/ui/components';
import { TOOL_LABEL } from '@/src/ui/nav';
import { ProductionOrderTracker } from '@/src/ui/ProductionOrderTracker';

const NEEDS_APPROVAL = new Set(['request_budget_override', 'request_overtime_shift']);
const BLURB: Record<string, string> = {
  request_budget_override: 'The Plant Controller reviews budget overrides in the Couchbase Labs Agent Operations Manager before the production order is released. You\'ll see the decision here.',
  request_overtime_shift: 'The Plant Manager reviews overtime shifts in the Couchbase Labs Agent Operations Manager. Once approved, the line\'s capacity for the week goes up and the larger build can be released.',
  set_standing_order: 'The material is ordered automatically on each due date, and drops off the model\'s recommendations.',
  create_supplier_return: 'Creates a supplier return, credits the cost back to the material budget and sets the warehouse dock date five business days out.',
  log_line_check: 'Marks the stop checked on today\'s floor walk and schedules the next plan review.',
  release_production_order: 'Sets this week\'s build on the line and costs the materials against the product\'s material budget; warehouse stock is issued when it syncs.',
};

/** One-tap actions from the Assistant and the Production orders list land here before they're queued. */
export default function Confirm() {
  const { tool = '', args = '{}', title = '' } = useLocalSearchParams<{ tool: string; args: string; title: string }>();
  const { request, snap } = useData();
  const [reqId, setReqId] = useState<string | null>(null);
  const parsed = JSON.parse(args) as Record<string, unknown> & { product_id?: string; reason?: string; lines?: { sku: string; qty: number }[] };
  const [reason, setReason] = useState(String(parsed.reason ?? ''));
  const a = parsed.product_id ? snap.products[parsed.product_id] : null;
  if (!parsed.product_id || !a) return <Empty icon="alert-circle-outline" title="No product" body="This action needs a product from your plant." />;

  const submit = async () => {
    const req = await request(tool, { ...parsed, product_id: parsed.product_id!, ...(reason ? { reason } : {}) }, title || `${TOOL_LABEL[tool] ?? tool} · ${a.product_name}`);
    setReqId(req._id);
  };
  const lines = parsed.lines ?? [];
  const materials = snap.catalog?.materials ?? [];

  return (
    <ScrollView contentContainerStyle={styles.body} keyboardShouldPersistTaps="handled">
      {reqId ? <ProductionOrderTracker id={reqId} onRetry={() => setReqId(null)} /> : (
        <Card style={{ padding: 18, gap: 12 }}>
          <Pill label={TOOL_LABEL[tool] ?? tool} tone={NEEDS_APPROVAL.has(tool) ? 'red' : 'info'} />
          <Text style={styles.h2}>{title}</Text>
          <Text style={styles.meta}>{a.product_name} · {a.line.name} · {a.service.tier_name}</Text>
          {lines.length ? (
            <Text style={styles.lines}>
              {lines.map((l) => `${l.qty} × ${(materials.find((p) => p.sku === l.sku)?.name ?? l.sku).split(',')[0]}`).join('\n')}
            </Text>
          ) : null}
          {Object.entries(parsed).filter(([k]) => !['product_id', 'lines', 'reason'].includes(k)).map(([k, v]) => (
            <Text key={k} style={styles.meta}>{k.replace(/_/g, ' ')}: {typeof v === 'number' && k.endsWith('usd') ? money2(v) : Array.isArray(v) ? `${v.length} recorded` : String(v)}</Text>
          ))}
          {'reason' in parsed || NEEDS_APPROVAL.has(tool) ? (
            <TextInput value={reason} onChangeText={setReason} placeholder="Reason (goes on the audit trail)" placeholderTextColor={theme.color.faint} style={styles.field} />
          ) : null}
          <Text style={styles.meta}>{BLURB[tool] ?? 'Saved on this phone and sent as soon as you\'re connected.'}</Text>
          <Button label={NEEDS_APPROVAL.has(tool) ? 'Submit for approval' : 'Confirm'} icon="checkmark" onPress={() => void submit()} />
        </Card>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  body: { padding: 16, maxWidth: 640, width: '100%', alignSelf: 'center' },
  h2: { fontSize: theme.type.xl, fontWeight: '900', color: theme.color.ink },
  meta: { color: theme.color.muted, fontSize: theme.type.sm, lineHeight: 19 },
  lines: { color: theme.color.text, fontSize: theme.type.sm, lineHeight: 20, backgroundColor: theme.color.bg, borderRadius: theme.radiusSm, padding: 10 },
  field: { borderWidth: 1, borderColor: theme.color.border, borderRadius: theme.radiusSm, padding: 11, fontSize: theme.type.md, color: theme.color.text },
});
