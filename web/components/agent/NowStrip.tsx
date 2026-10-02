"use client";

import { Fragment } from "react";
import { PHASES, fmtP, signed } from "../../lib/game.ts";
import type { Now } from "../../lib/views/agent.ts";
import { EventLink, Panel } from "../ui.tsx";
import css from "../../app/agent.module.css";

export function NowStrip({ now }: { now: Now }) {
  return (
    <Panel title="Now" sub={`tick ${now.tick}`}>
      <div className={css.now}>
        <div>
          <ol className={css.steps} aria-label="Agent loop">
            {PHASES.map((p, i) => (
              <Fragment key={p}>
                {i > 0 && <li className={css.arrow} aria-hidden="true">→</li>}
                <li
                  className={`${css.step} ${i === now.phaseIndex ? css.live : i < now.phaseIndex ? css.done : ""}`}
                  aria-current={i === now.phaseIndex ? "step" : undefined}
                >
                  {p}
                </li>
              </Fragment>
            ))}
          </ol>
          <p className={css.goal}>
            <span className={css.goalLabel}>Goal</span>
            {now.goal || <span className="muted">no goal yet</span>}
          </p>
          <div className={css.why}>
            <div>
              <b>Why</b>
              <span>{now.thought ? now.thought.text : <span className="muted">no reasoning yet</span>}</span>
              {now.thought && <EventLink id={now.thought.eventId} />}
            </div>
            <div>
              <b>Did</b>
              <span>{now.action ? `${now.action.icon} ${now.action.text}` : <span className="muted">no action yet</span>}</span>
              {now.action && <EventLink id={now.action.eventId} />}
            </div>
          </div>
        </div>
        <dl className={css.facts}>
          <div><dt>Open threads</dt><dd>{now.openThreads}</dd></div>
          <div><dt>Our trades</dt><dd>{now.trades}</dd></div>
          <div>
            <dt>Value gained</dt>
            <dd className={now.gain > 0 ? "good" : now.gain < 0 ? "bad" : undefined}>{now.trades ? signed(now.gain) : "—"}</dd>
          </div>
          <div><dt>Cash</dt><dd>{fmtP(now.cash)}</dd></div>
        </dl>
      </div>
    </Panel>
  );
}
