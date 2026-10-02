"use client";

import { createContext, useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { apply, createState, type GameEvent, type State } from "./state.ts";

export type Status = "connecting" | "live" | "closed" | "bad url";

type Store = {
  state: State;
  version: number;
  status: Status;
  url: string;
  paused: boolean;
  clockAt: number;
  selected: number | null;
  listeners: Set<() => void>;
  sock: WebSocket | null;
  retry: ReturnType<typeof setTimeout> | null;
};

export type Game = {
  state: State;
  version: number;
  status: Status;
  url: string;
  paused: boolean;
  clockAt: number;
  selected: number | null;
  setUrl: (url: string) => void;
  setPaused: (paused: boolean) => void;
  select: (eventId: number | null) => void;
};

const defaultUrl = () => {
  const fromQuery = new URLSearchParams(location.search).get("ws");
  if (fromQuery) return fromQuery;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const host = process.env.NODE_ENV === "development" ? "localhost:8777" : location.host || "localhost:8777";
  return `${proto}://${host}/events`;
};

const GameContext = createContext<Store | null>(null);

function notify(store: Store) {
  store.version += 1;
  for (const l of store.listeners) l();
}

function connect(store: Store) {
  store.sock?.close();
  store.state = createState();
  store.status = "connecting";
  let sock: WebSocket;
  try {
    sock = new WebSocket(store.url);
  } catch {
    store.status = "bad url";
    notify(store);
    return;
  }
  store.sock = sock;
  let pending = false;
  sock.onopen = () => {
    store.status = "live";
    notify(store);
  };
  sock.onmessage = (msg) => {
    let e: GameEvent;
    try {
      e = JSON.parse(msg.data);
    } catch {
      return;
    }
    if (!e || typeof e !== "object" || typeof e.type !== "string") return;
    apply(store.state, e);
    if (e.type === "clock") store.clockAt = performance.now();
    if (store.paused || pending) return;
    pending = true;
    requestAnimationFrame(() => {
      pending = false;
      notify(store);
    });
  };
  sock.onclose = () => {
    if (store.sock !== sock) return;
    store.status = "closed";
    notify(store);
    store.retry = setTimeout(() => connect(store), 2000);
  };
}

export function GameProvider({ children }: { children: ReactNode }) {
  const ref = useRef<Store | null>(null);
  ref.current ??= {
    state: createState(), version: 0, status: "connecting", url: "", paused: false, clockAt: 0,
    selected: null, listeners: new Set(), sock: null, retry: null,
  };
  const store = ref.current;
  useEffect(() => {
    store.url = defaultUrl();
    connect(store);
    return () => {
      if (store.retry) clearTimeout(store.retry);
      const sock = store.sock;
      store.sock = null;
      sock?.close();
    };
  }, [store]);
  return <GameContext.Provider value={store}>{children}</GameContext.Provider>;
}

export function useGame(): Game {
  const store = useContext(GameContext);
  if (!store) throw new Error("useGame outside GameProvider");
  const version = useSyncExternalStore(
    (l) => {
      store.listeners.add(l);
      return () => store.listeners.delete(l);
    },
    () => store.version,
    () => 0,
  );
  return useMemo(() => ({
    state: store.state, version, status: store.status, url: store.url, paused: store.paused,
    clockAt: store.clockAt, selected: store.selected,
    setUrl: (url: string) => {
      store.url = url;
      const u = new URL(location.href);
      u.searchParams.set("ws", url);
      history.replaceState(null, "", u);
      if (store.retry) clearTimeout(store.retry);
      connect(store);
      notify(store);
    },
    setPaused: (paused: boolean) => {
      store.paused = paused;
      notify(store);
    },
    select: (eventId: number | null) => {
      store.selected = eventId;
      notify(store);
    },
  }), [store, version]);
}

export function useNow(intervalMs = 250): number {
  const [now, setNow] = useState(0);
  useEffect(() => {
    setNow(performance.now());
    const id = setInterval(() => setNow(performance.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}
