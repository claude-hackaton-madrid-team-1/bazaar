import styles from "../../app/negotiations/negotiations.module.css";
import { fmtP } from "../../lib/game.ts";
import type { ThreadRow } from "../../lib/views/negotiations.ts";
import { Badge } from "../ui.tsx";
import { Expiry, Injection, RefChip } from "./bits.tsx";

export function ThreadList({ rows, selected, onSelect }: {
  rows: ThreadRow[]; selected: number | null; onSelect: (id: number) => void;
}) {
  return (
    <ul className={styles.list} aria-label="Our threads">
      {rows.map((r) => {
        const cls = [styles.row, r.id === selected ? styles.sel : "", r.status === "closed" ? styles.closed : ""].filter(Boolean).join(" ");
        return (
          <li key={r.id}>
            <button type="button" className={cls} aria-current={r.id === selected ? "true" : undefined} onClick={() => onSelect(r.id)}>
              <span className={styles.line}>
                <span className={styles.tid}>#{r.id}</span>
                <span className={styles.who} title={r.with}>{r.with}</span>
                <Badge tone={r.side === "buy" ? "us" : "them"}>{r.side.toUpperCase()}</Badge>
                <RefChip topic={r.topic} />
                <span className={styles.spacer} />
                {r.final && r.status === "open" && <Badge tone="warn">FINAL</Badge>}
                {r.status === "closed" ? <Badge>closed</Badge> : <Expiry left={r.expiresIn} />}
              </span>
              <span className={`${styles.line} ${styles.prices}`}>
                <span>{r.theirLabel} <b className="them-text">{fmtP(r.theirPrice)}</b></span>
                <span>{r.ourLabel} <b className="us-text">{fmtP(r.ourPrice)}</b></span>
                <span>gap <b>{fmtP(r.gap)}</b></span>
                <span className={styles.spacer} />
                <span>r{r.rounds}</span>
                <Injection on={r.suspicious} />
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
