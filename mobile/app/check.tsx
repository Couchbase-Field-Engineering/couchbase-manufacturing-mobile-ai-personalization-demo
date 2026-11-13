import { useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { ScrollView, StyleSheet, Text, TextInput } from 'react-native';
import { useData } from '@/src/data/DataContext';
import { theme } from '@/src/theme';
import { ChoiceRow } from '@/src/ui/Choice';
import { Button, Card, Empty, SectionTitle } from '@/src/ui/components';
import { ProductionOrderTracker } from '@/src/ui/ProductionOrderTracker';

const OUTCOMES = [
  { value: 'on_plan', label: 'On plan' },
  { value: 'plan_adjusted', label: 'Plan adjusted' },
  { value: 'follow_up', label: 'Follow up' },
  { value: 'on_hold', label: 'On hold' },
] as const;
const CADENCE = [1, 7, 14, 30].map((d) => ({ value: String(d), label: d === 1 ? 'Tomorrow' : `${d} days` }));

/** Log the line check: marks the stop checked on today's floor walk and schedules the next plan review. Works offline like everything else. */
export default function LineCheck() {
  const { product_id = '' } = useLocalSearchParams<{ product_id: string }>();
  const { snap, request } = useData();
  const a = snap.products[product_id];
  const [outcome, setOutcome] = useState<(typeof OUTCOMES)[number]['value']>(a?.auth.on_hold ? 'on_hold' : 'on_plan');
  const [notes, setNotes] = useState('');
  const [cadence, setCadence] = useState(String([1, 7, 14, 30].includes(a?.review.cadence_days ?? 0) ? a?.review.cadence_days : 7));
  const [reqId, setReqId] = useState<string | null>(null);
  if (!a) return <Empty icon="clipboard-outline" title="Product not on this phone" body="Open a product from today's floor walk first." />;

  const submit = async () => {
    const req = await request('log_line_check', { product_id, outcome, notes: notes.trim(), next_review_days: Number(cadence) }, `Line check · ${a.product_name} · ${outcome.replace(/_/g, ' ')}`);
    setReqId(req._id);
  };

  return (
    <ScrollView contentContainerStyle={styles.body} keyboardShouldPersistTaps="handled">
      <Card style={{ padding: 14, gap: 4 }}>
        <Text style={styles.meta}>LINE CHECK</Text>
        <Text style={styles.h2}>{a.product_name}</Text>
        <Text style={styles.meta}>{a.line.name} · plan last reviewed {a.review.last_review} · every {a.review.cadence_days} days · {a.service.tier_name}</Text>
      </Card>
      {reqId ? (
        <ProductionOrderTracker id={reqId} onRetry={() => setReqId(null)} />
      ) : (
        <>
          <SectionTitle title="Outcome" />
          <ChoiceRow options={OUTCOMES.map((o) => ({ value: o.value, label: o.label }))} value={outcome} onChange={setOutcome} />
          <SectionTitle title="Review the plan again in" />
          <ChoiceRow options={CADENCE} value={cadence} onChange={setCadence} />
          <SectionTitle title="Notes" />
          <Card style={{ padding: 14, gap: 12 }}>
            <TextInput value={notes} onChangeText={setNotes} placeholder="e.g. Supplier is shipping partial pallets; supervisor wants the changeover on second shift" placeholderTextColor={theme.color.faint} style={styles.field} multiline />
            <Button label="Log line check" icon="checkmark" onPress={() => void submit()} />
            <Text style={styles.tiny}>Saved on this phone; marks the stop checked on today's floor walk and syncs when connected.</Text>
          </Card>
        </>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  body: { padding: 16, maxWidth: 640, width: '100%', alignSelf: 'center', paddingBottom: 40 },
  h2: { fontSize: theme.type.xl, fontWeight: '900', color: theme.color.ink },
  meta: { color: theme.color.muted, fontSize: theme.type.sm },
  tiny: { color: theme.color.faint, fontSize: theme.type.xs, textAlign: 'center' },
  field: { borderWidth: 1, borderColor: theme.color.border, borderRadius: theme.radiusSm, padding: 11, fontSize: theme.type.md, color: theme.color.text, minHeight: 80 },
});
