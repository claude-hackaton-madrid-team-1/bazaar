"use client";

import type { ReactNode } from "react";
import { useGame } from "../lib/stream.tsx";

export function Panel({ title, sub, actions, children, className }: {
  title: ReactNode; sub?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={`panel${className ? ` ${className}` : ""}`}>
      <div className="panel-head">
        <h2>{title}{sub != null && <span className="sub">{sub}</span>}</h2>
        {actions && <div className="panel-actions">{actions}</div>}
      </div>
      {children}
    </section>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

export function EventLink({ id, children }: { id: number | null | undefined; children?: ReactNode }) {
  const g = useGame();
  if (id == null) return <span className="mono muted">—</span>;
  return (
    <button type="button" className={`eid${g.selected === id ? " sel" : ""}`} onClick={() => g.select(id)} title="Inspect event">
      {children ?? `#${id}`}
    </button>
  );
}

export function Badge({ tone = "neutral", children, title }: {
  tone?: "neutral" | "us" | "them" | "good" | "bad" | "warn"; children: ReactNode; title?: string;
}) {
  return <span className={`badge ${tone}`} title={title}>{children}</span>;
}
