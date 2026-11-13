import { Ionicons } from '@expo/vector-icons';
import { router } from 'expo-router';
import { useMemo, useState } from 'react';
import { FlatList, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useData } from '@/src/data/DataContext';
import { theme } from '@/src/theme';
import type { Product } from '@/src/types';
import { Card, Empty, money, Pill } from '@/src/ui/components';

/** Every product in the plant - all of it on the phone, searchable offline. */
export default function Products() {
  const { snap, setCurrentProductId } = useData();
  const insets = useSafeAreaInsets();
  const [q, setQ] = useState('');
  const today = snap.planner?.route_date ?? '';
  const list = useMemo(() => {
    const all = Object.values(snap.products);
    const m = q.trim().toLowerCase();
    const f = m ? all.filter((a) => `${a.product_name} ${a.product_code} ${a.line.name} ${a.line_type_name} ${a.program?.program_name ?? ''}`.toLowerCase().includes(m)) : all;
    return f.sort((a, b) => (a.review.next_review_date < b.review.next_review_date ? -1 : a.review.next_review_date > b.review.next_review_date ? 1 : a.product_name.localeCompare(b.product_name)));
  }, [snap.products, q]);

  const open = (a: Product) => {
    setCurrentProductId(a.product_id);
    router.push({ pathname: '/product', params: { product_id: a.product_id } });
  };

  return (
    <View style={{ flex: 1, backgroundColor: theme.color.bg }}>
      <View style={[styles.head, { paddingTop: insets.top + 12 }]}>
        <Text style={styles.h1}>Products</Text>
        <Text style={styles.sub}>{snap.planner?.plant_name ?? ''} · {Object.keys(snap.products).length} on this phone</Text>
        <View style={styles.search}>
          <Ionicons name="search" size={16} color={theme.color.faint} />
          <TextInput value={q} onChangeText={setQ} placeholder="Product, code, line or line type" placeholderTextColor={theme.color.faint} style={styles.input} />
        </View>
      </View>
      <FlatList
        data={list}
        keyExtractor={(a) => a.product_id}
        contentContainerStyle={styles.body}
        ListEmptyComponent={<Empty icon="cube-outline" title="No products yet" body="Your plant syncs from Couchbase in a moment." />}
        renderItem={({ item: a }) => {
          const due = a.review.next_review_date <= today;
          const plan = snap.plans[a.product_id];
          return (
            <Pressable onPress={() => open(a)} style={({ pressed }) => [{ marginBottom: 10, opacity: pressed ? 0.92 : 1 }]}>
              <Card style={styles.card}>
                <View style={{ flex: 1, gap: 3 }}>
                  <Text style={styles.name}>{a.product_name}</Text>
                  <Text style={styles.meta}>{a.line.name} · {a.line.building}{a.program ? ` · ${a.program.program_name}` : ''}</Text>
                  <View style={{ flexDirection: 'row', gap: 6, flexWrap: 'wrap', marginTop: 2 }}>
                    <Pill label={a.service.tier_name} tone="neutral" icon="ribbon-outline" />
                    {a.alerts?.length ? <Pill label={`${a.alerts.length} alert${a.alerts.length > 1 ? 's' : ''}`} tone="danger" icon="warning-outline" /> : null}
                    <Pill label={due ? 'Due today' : `Next ${a.review.next_review_date.slice(5)}`} tone={due ? 'red' : 'neutral'} icon="calendar-outline" />
                    {plan?.plan_total_usd ? <Pill label={`Materials ${money(plan.plan_total_usd)}`} tone="info" icon="cube-outline" /> : null}
                    {a.auth.on_hold ? <Pill label="Quality hold" tone="danger" icon="lock-closed" /> : null}
                  </View>
                </View>
                <Ionicons name="chevron-forward" size={18} color={theme.color.faint} />
              </Card>
            </Pressable>
          );
        }}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  head: { paddingHorizontal: 16, paddingBottom: 10, backgroundColor: theme.color.bg },
  h1: { fontSize: theme.type.title, fontWeight: '900', color: theme.color.ink },
  sub: { color: theme.color.muted, marginTop: 2 },
  search: { flexDirection: 'row', gap: 8, alignItems: 'center', backgroundColor: theme.color.surface, borderRadius: 999, paddingHorizontal: 14, marginTop: 12, borderWidth: 1, borderColor: theme.color.border },
  input: { flex: 1, paddingVertical: 10, fontSize: theme.type.md, color: theme.color.text },
  body: { padding: 16, paddingTop: 6, maxWidth: 640, width: '100%', alignSelf: 'center' },
  card: { flexDirection: 'row', alignItems: 'center', gap: 10, padding: 14 },
  name: { fontWeight: '800', color: theme.color.ink, fontSize: theme.type.md },
  meta: { color: theme.color.muted, fontSize: theme.type.sm },
});
