import * as Crypto from 'expo-crypto';
import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { api, ApiError, type LoginResponse } from '../api';
import { createLocalDb } from '../db/engine';
import { EMPTY, type LocalDb, type Snapshot } from '../db/localDb';
import { answerOffline } from '../features/assistant/offline';
import { useAuth } from '../session/AuthContext';
import type { ChatMessage, ProductionOrderRequest, SyncState } from '../types';

type Ctx = {
  snap: Snapshot;
  sync: SyncState;
  offline: boolean;
  setOffline: (v: boolean) => void;
  dbLocation: string | null;
  /** Queue a planning action (offline-first): a `production_orders` doc in the local DB, pushed by the replicator. */
  request: (tool: string, args: Record<string, unknown> & { product_id: string }, title: string) => Promise<ProductionOrderRequest>;
  /** The product the assistant answers for (the one the planner has open). */
  currentProductId: string | null;
  setCurrentProductId: (id: string | null) => void;
  ask: (message: string, productId?: string | null) => Promise<void>;
  thinking: boolean;
  liveChat: ChatMessage[];
  refresh: () => Promise<void>;
};

const DataContext = createContext<Ctx | null>(null);
export const SESSION_EXPIRED = 'session-expired';
const nowIso = () => new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');

export function DataProvider({ children }: { children: React.ReactNode }) {
  const { session, renew, expire } = useAuth();
  const dbRef = useRef<LocalDb | null>(null);
  // The app token in use: the stored one until /api/auth/refresh renews it.
  const tokenRef = useRef<string | null>(null);
  const [snap, setSnap] = useState<Snapshot>(EMPTY);
  const [sync, setSync] = useState<SyncState>({ engine: 'preview', status: 'stopped', lastSyncAt: null, pendingPush: 0 });
  const [offline, setOfflineState] = useState(false);
  const [thinking, setThinking] = useState(false);
  const [liveChat, setLiveChat] = useState<ChatMessage[]>([]);
  const [dbLocation, setDbLocation] = useState<string | null>(null);
  const [currentProductId, setCurrentProductId] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (dbRef.current) setSnap(await dbRef.current.read());
  }, []);

  useEffect(() => {
    if (!session) return;
    let cancelled = false;
    const db = createLocalDb();
    dbRef.current = db;
    const offs: (() => void)[] = [];
    void (async () => {
      await db.open(session.planner_id);
      if (cancelled) return;
      setDbLocation(db.location());
      offs.push(db.onChange(() => void reload()));
      offs.push(db.onSyncState(setSync));
      await reload(); // whatever is already on the device renders immediately
      let sg = session.sync_gateway;
      tokenRef.current = session.token;
      try {
        // Fresh app token + Sync Gateway session when there is connectivity
        // (sliding expiry); offline, the stored ones are used.
        const r = await api<Pick<LoginResponse, 'token' | 'sync_gateway'>>('/api/auth/refresh', { token: session.token, method: 'POST', timeoutMs: 6000 });
        sg = r.sync_gateway ?? sg;
        if (r.token) tokenRef.current = r.token;
        await renew({ token: r.token, sync_gateway: r.sync_gateway });
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) {
          // Not "offline": the backend no longer accepts this session.
          await expire();
          return;
        }
        /* offline at launch - fine */
      }
      if (cancelled) return;
      await db.startSync({ token: tokenRef.current ?? session.token, plannerId: session.planner_id, sg });
    })();
    return () => {
      cancelled = true;
      offs.forEach((f) => f());
      void db.close();
      dbRef.current = null;
      setSnap(EMPTY);
      setLiveChat([]);
      setCurrentProductId(null);
      setSync((st) => ({ ...st, status: 'stopped', detail: undefined }));
    };
  }, [session, reload, renew, expire]);

  // The sync loop reports a rejected session the same way.
  useEffect(() => {
    if (sync.status === 'error' && sync.detail === SESSION_EXPIRED) void expire();
  }, [sync.status, sync.detail, expire]);

  const setOffline = useCallback((v: boolean) => {
    setOfflineState(v);
    void dbRef.current?.setPaused(v);
  }, []);

  const request = useCallback(
    async (tool: string, args: Record<string, unknown> & { product_id: string }, title: string) => {
      if (!session || !dbRef.current) throw new Error('Not signed in');
      const req: ProductionOrderRequest = {
        _id: `order::${session.planner_id}::${Crypto.randomUUID().replace(/-/g, '').slice(0, 16)}`,
        type: 'request',
        planner_id: session.planner_id,
        product_id: args.product_id,
        product_name: snap.products[args.product_id]?.product_name,
        tool,
        args,
        title,
        status: 'pending',
        created_at: nowIso(),
      };
      await dbRef.current.saveRequest(req);
      await reload();
      return req;
    },
    [session, reload, snap.products],
  );

  const ask = useCallback(
    async (message: string, productId?: string | null) => {
      if (!session) return;
      const acct = productId === undefined ? currentProductId : productId;
      const techMsg: ChatMessage = { id: `l${Date.now()}`, role: 'planner', text: message, at: nowIso(), product_id: acct };
      setLiveChat((c) => [...c, techMsg]);
      setThinking(true);
      try {
        if (offline) throw new ApiError('offline', 0);
        const r = await api<any>('/api/assistant', {
          token: tokenRef.current ?? session.token,
          body: { message, product_id: acct, session_id: `app-${session.planner_id}` },
          timeoutMs: 45000,
        });
        setLiveChat((c) => [
          ...c,
          { id: `r${Date.now()}`, role: 'assistant', text: r.reply, at: nowIso(), intent: r.intent, actions: r.actions,
            context: r.context, llm: r.llm, sources: r.sources, product_id: r.product_id },
        ]);
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) {
          void expire();
          return;
        }
        // No connectivity: answer from Couchbase Lite (context cache, production plan, FAQ, earlier answers).
        const local = answerOffline(message, snap, acct, session.planner_id);
        setLiveChat((c) => [...c, { id: `o${Date.now()}`, role: 'assistant', at: nowIso(), product_id: acct, ...local }]);
        void e;
      } finally {
        setThinking(false);
      }
    },
    [session, offline, snap, expire, currentProductId],
  );

  return (
    <DataContext.Provider
      value={{ snap, sync, offline, setOffline, dbLocation, request, currentProductId, setCurrentProductId, ask, thinking, liveChat, refresh: reload }}
    >
      {children}
    </DataContext.Provider>
  );
}

export function useData(): Ctx {
  const c = useContext(DataContext);
  if (!c) throw new Error('useData outside DataProvider');
  return c;
}
