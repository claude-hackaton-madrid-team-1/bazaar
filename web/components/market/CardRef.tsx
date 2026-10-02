import { setOf } from "../../lib/game.ts";
import css from "../../app/market/market.module.css";

export function CardRef({ code, name }: { code: string; name?: string }) {
  const set = setOf(code);
  return (
    <span className={css.card} title={set ? `${set.name} · ${name ?? code}` : name ?? code}>
      <i className={css.swatch} style={{ background: set?.color ?? "var(--text-muted)" }} />
      <b>{code}</b>
      {name && name !== code && <span className={css.name}>{name}</span>}
    </span>
  );
}
