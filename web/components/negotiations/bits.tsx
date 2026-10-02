import styles from "../../app/negotiations/negotiations.module.css";
import { setOf } from "../../lib/game.ts";
import { Badge } from "../ui.tsx";

export function RefChip({ topic }: { topic: string }) {
  const set = setOf(topic);
  return (
    <span className={styles.ref} style={set ? { borderLeftColor: set.color } : undefined} title={set?.name ?? topic}>
      {topic}
    </span>
  );
}

export function Expiry({ left }: { left: number | null }) {
  if (left == null) return null;
  if (left < 0) return <Badge tone="bad" title="Offer has expired">expired</Badge>;
  return <Badge tone={left <= 1 ? "warn" : "neutral"} title="Ticks until the last offer expires">⏱ {left}t</Badge>;
}

export function Injection({ on }: { on: boolean }) {
  if (!on) return null;
  return <Badge tone="warn" title="Counterparty text carries instructions. Only the structured offer counts.">⚠ injection?</Badge>;
}
