"use client";

import { signed } from "../../lib/game.ts";
import { LANE_LABEL, type Line, type TickCard as Card } from "../../lib/views/agent.ts";
import { Badge, EventLink } from "../ui.tsx";
import css from "../../app/agent.module.css";

const TEXT_TONE: Record<string, string | undefined> = { us: css.us, them: css.them };

function LineRow({ line }: { line: Line }) {
  const textClass = line.type === "agent.thought" ? css.thought : TEXT_TONE[line.tone];
  return (
    <li className={css.line}>
      <span className={css.icon} aria-hidden="true">{line.icon}</span>
      <span className={css.body}>
        <span className={textClass}>{line.text}</span>
        {line.final && <Badge tone="warn">FINAL</Badge>}
        {line.suspicious && <Badge tone="bad" title="Counterparty text looks like an injection; only the structured offer is trusted">INJECTION?</Badge>}
        {line.gain != null && <span className={`${css.gain} ${line.gain > 0 ? "good" : line.gain < 0 ? "bad" : "muted"}`}>{signed(line.gain)}</span>}
        {line.quote && <span className={`${css.quote} ${line.suspicious ? css.flagged : ""}`}>“{line.quote}”</span>}
      </span>
      <EventLink id={line.eventId} />
    </li>
  );
}

export function TickCard({ card, current }: { card: Card; current: boolean }) {
  return (
    <article className={`${css.card} ${current ? css.current : ""}`}>
      <header className={css.cardHead}>
        <span className={css.tickNo}>tick {card.tick}{current && <span className="muted"> · now</span>}</span>
        {card.deals > 0 && (
          <span className={css.cardMeta}>
            <span className="muted">{card.deals} {card.deals === 1 ? "deal" : "deals"}</span>
            <span className={card.gain > 0 ? "good" : card.gain < 0 ? "bad" : "muted"}>{signed(card.gain)}</span>
          </span>
        )}
      </header>
      {card.lanes.map(({ lane, lines }) => (
        <section key={lane} className={`${css.lane} ${css[lane]}`}>
          <div className={css.laneName}>{LANE_LABEL[lane]}</div>
          <ul className={css.lines}>
            {lines.map((line, i) => <LineRow key={`${line.eventId}-${i}`} line={line} />)}
          </ul>
        </section>
      ))}
    </article>
  );
}
