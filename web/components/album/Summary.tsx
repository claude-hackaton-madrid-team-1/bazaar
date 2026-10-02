import type { AlbumSummary } from "../../lib/views/album.ts";
import { fmtP } from "../../lib/game.ts";
import css from "../../app/album/album.module.css";

export function Summary({ sum }: { sum: AlbumSummary }) {
  return (
    <div className={css.summary}>
      <div className={css.tile}>
        <div className={css.label}>Pages complete</div>
        <div className={css.value}>{sum.complete}<small>/ {sum.pages}</small></div>
        <div className={css.foot}>{sum.master} master</div>
      </div>
      <div className={css.tile}>
        <div className={css.label}>Missing slots</div>
        <div className={css.value}>{sum.missing}</div>
        <div className={css.foot}>page slots still to fill</div>
      </div>
      <div className={css.tile}>
        <div className={css.label}>Duplicates to sell</div>
        <div className={css.value}>{sum.duplicates.count}</div>
        <div className={`${css.foot} ${css.refs}`}>
          {sum.duplicates.refs.length
            ? sum.duplicates.refs.map((d) => <span key={d.ref}>{d.ref} ×{d.spare}</span>)
            : <span className="muted">none spare</span>}
        </div>
      </div>
      <div className={css.tile}>
        <div className={css.label}>Cheapest missing</div>
        <div className={css.value}>{sum.cheapest ? sum.cheapest.ref : "—"}</div>
        <div className={css.foot}>{sum.cheapest ? `${sum.cheapest.rarity} · book ${fmtP(sum.cheapest.book)} · ${sum.cheapest.page}` : "nothing missing"}</div>
      </div>
    </div>
  );
}
