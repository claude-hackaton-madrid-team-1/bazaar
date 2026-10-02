import { RARITY_COLOR } from "../../lib/game.ts";
import type { AlbumRow } from "../../lib/views/album.ts";
import { Badge } from "../ui.tsx";
import css from "../../app/album/album.module.css";

function Cells({ row }: { row: AlbumRow }) {
  return (
    <div className={css.cells}>
      {row.slots.flatMap((c, i) => {
        const cell = (
          <span
            key={c.ref}
            className={`${css.cell}${c.count ? ` ${css.have}` : ""}`}
            title={c.title}
            style={c.count ? { background: c.color } : { borderColor: `color-mix(in srgb, ${c.color} 55%, var(--border))` }}
          >
            {c.num}
            {c.spare && <span className={css.dup}>×{c.count}</span>}
          </span>
        );
        return i === 10 ? [<span key="gap" />, cell] : [cell];
      })}
    </div>
  );
}

export function AlbumGrid({ rows }: { rows: AlbumRow[] }) {
  return (
    <>
      <div className={css.rows}>
        {rows.map((row) => (
          <div key={row.set} className={css.row}>
            <span className={css.name}>
              <i className={css.swatch} style={{ background: row.color }} />
              <span className={css.code}>{row.set}</span>
              {row.name}
            </span>
            <span className={css.count}>
              {row.master ? <Badge tone="warn">MASTER</Badge> : row.complete && <Badge tone="good">COMPLETE</Badge>}
              <span className={row.complete ? css.done : undefined}><b>{row.have}</b>/{row.of}</span>
            </span>
            <Cells row={row} />
          </div>
        ))}
      </div>
      <div className={css.legend}>
        {Object.entries(RARITY_COLOR).map(([r, c]) => <span key={r}><i style={{ background: c }} />{r}</span>)}
        <span><i className={css.outline} />missing</span>
        <span>×2 spare copies</span>
      </div>
    </>
  );
}
