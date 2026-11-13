import type { Product, Catalog, ChatDoc, ContextDoc, ProductionOrderRequest, Planner, ProductionPlanDoc, SyncState } from '../types';

/**
 * The app's local database. Two implementations with the same contract:
 *
 * - `couchbase-lite` (cblDb.native.ts): the real thing. An on-device Couchbase
 *   Lite database (scope `planning`, same collections as the server) kept in sync
 *   by a continuous push/pull replicator against Sync Gateway. Everything
 *   renders from here, so the app works with no connectivity at all.
 * - `preview` (previewDb.ts): a browser/Expo Go stand-in for demos without a
 *   device build - an in-memory store that polls the backend's REST
 *   `/api/sync/pull` and pushes production orders with `/api/sync/push`. Same document
 *   ids and shapes.
 */
export const SCOPE = 'planning';
export const COLLECTIONS = ['products', 'planners', 'production_plans', 'production_orders', 'assistant', 'context_cache', 'catalog'] as const;
export type CollectionName = (typeof COLLECTIONS)[number];

export const INTENTS = ['plan', 'materials', 'alerts', 'overtime', 'line', 'returns', 'budget', 'history', 'standing', 'review', 'forecast', 'inventory', 'general'];

export type Snapshot = {
  planner: Planner | null;
  /** Every product in the planner's plant, by product_id. */
  products: Record<string, Product>;
  /** Production plan per product_id. */
  plans: Record<string, ProductionPlanDoc>;
  orders: ProductionOrderRequest[];
  chat: ChatDoc | null;
  catalog: Catalog | null;
  /** Context cache docs by their id (ctx::<product>::<intent> / ctx::<planner>::playbook_<intent>). */
  contexts: Record<string, ContextDoc>;
};

export const EMPTY: Snapshot = { planner: null, products: {}, plans: {}, orders: [], chat: null, catalog: null, contexts: {} };

export type SyncSession = {
  token: string;
  plannerId: string;
  sg: { session_id: string; cookie_name: string; url: string | null; db: string } | null;
};

export interface LocalDb {
  readonly engine: SyncState['engine'];
  open(plannerId: string): Promise<void>;
  read(): Promise<Snapshot>;
  saveRequest(req: ProductionOrderRequest): Promise<void>;
  startSync(session: SyncSession): Promise<void>;
  setPaused(paused: boolean): Promise<void>;
  onChange(cb: () => void): () => void;
  onSyncState(cb: (s: SyncState) => void): () => void;
  location(): string | null;
  close(): Promise<void>;
}

export const ids = {
  planner: (r: string) => `planner::${r}`,
  product: (a: string) => `product::${a}`,
  plan: (a: string) => `plan::${a}`,
  chat: (r: string) => `chat::${r}`,
  ctx: (scope: string, k: string) => `ctx::${scope}::${k}`,
  catalog: 'catalog::v1',
};

export type Doc = Record<string, unknown> & { _id?: string };

/** Build a Snapshot from a by-id getter and a per-collection lister. */
export async function buildSnapshot(
  plannerId: string,
  get: (col: CollectionName, id: string) => Promise<Doc | null>,
  list: (col: CollectionName) => Promise<Doc[]>,
  orders: ProductionOrderRequest[],
): Promise<Snapshot> {
  const products: Record<string, Product> = {};
  for (const d of await list('products')) {
    const a = d as unknown as Product;
    if (a.product_id) products[a.product_id] = a;
  }
  const plans: Record<string, ProductionPlanDoc> = {};
  for (const d of await list('production_plans')) {
    const s = d as unknown as ProductionPlanDoc;
    if (s.product_id) plans[s.product_id] = s;
  }
  const contexts: Record<string, ContextDoc> = {};
  for (const d of await list('context_cache')) {
    const c = d as unknown as ContextDoc & { _id?: string };
    const key = c._id ?? ids.ctx(c.scope, c.personal ? c.intent : `playbook_${c.intent}`);
    contexts[key] = c;
  }
  return {
    planner: (await get('planners', ids.planner(plannerId))) as unknown as Planner | null,
    products,
    plans,
    chat: (await get('assistant', ids.chat(plannerId))) as unknown as ChatDoc | null,
    catalog: (await get('catalog', ids.catalog)) as unknown as Catalog | null,
    contexts,
    orders: [...orders].sort((a, b) => (a.created_at < b.created_at ? 1 : -1)),
  };
}

type Listener<T> = (v: T) => void;
export class Emitter<T> {
  private ls = new Set<Listener<T>>();
  on(l: Listener<T>) {
    this.ls.add(l);
    return () => {
      this.ls.delete(l);
    };
  }
  emit(v: T) {
    this.ls.forEach((l) => {
      try {
        l(v);
      } catch {
        /* listener errors never break sync */
      }
    });
  }
}
