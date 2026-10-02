import styles from "../../app/negotiations/negotiations.module.css";
import { fmtP } from "../../lib/game.ts";
import type { Bubble, Conversation } from "../../lib/views/negotiations.ts";
import { Badge, Empty, EventLink } from "../ui.tsx";
import { Expiry, Injection, RefChip } from "./bits.tsx";
import { PriceRail } from "./PriceRail.tsx";

function BubbleItem({ b, label }: { b: Bubble; label: string }) {
  return (
    <li className={`${styles.bubble} ${b.side === "us" ? styles.us : styles.them}`}>
      <div className={styles.bubbleHead}>
        <span className={styles.price}>{fmtP(b.price)}</span>
        <span className="muted">{b.side === "us" ? "we" : b.maker} · {label}</span>
        {b.final && <Badge tone="warn">FINAL</Badge>}
        <Injection on={b.suspicious} />
      </div>
      {b.text != null && <blockquote className={styles.text}>“{b.text}”</blockquote>}
      <div className={styles.meta}>
        <span>r{b.round}</span>
        <span>tick {b.tick ?? "—"}</span>
        <span>o{b.offerId ?? "—"}</span>
        <span>m{b.messageId ?? "—"}</span>
        <span>{b.maker} → {b.to}</span>
        <span>t{b.createdTick ?? "—"} → exp t{b.expiresTick ?? "—"}</span>
        {b.assets.length > 0 && <span>assets {b.assets.map((a) => `#${a}`).join(" ")}</span>}
        <EventLink id={b.eventId} />
      </div>
    </li>
  );
}

export function ThreadDetail({ convo }: { convo: Conversation | null }) {
  if (!convo) return <Empty>No thread selected. Our threads appear here as soon as the agent opens one.</Empty>;
  const { thread: t, bubbles, lastText } = convo;
  const last = bubbles.at(-1);
  return (
    <div className={styles.detail}>
      <div className={styles.head}>
        <span className={styles.tid}>#{t.id}</span>
        <span className={styles.who}>{t.with}</span>
        <Badge tone={t.side === "buy" ? "us" : "them"}>{t.side.toUpperCase()}</Badge>
        <RefChip topic={t.topic} />
        {t.set && <span className="muted">{t.set.name}</span>}
        {t.final && t.status === "open" && <Badge tone="warn">FINAL</Badge>}
        {t.status === "closed" ? <Badge>closed</Badge> : <Expiry left={t.expiresIn} />}
        <span className={styles.spacer} />
        <EventLink id={last?.eventId}>{last ? `last #${last.eventId}` : undefined}</EventLink>
      </div>
      <dl className={styles.stats}>
        <div><dt>their {t.theirLabel}</dt><dd className="them-text">{fmtP(t.theirPrice)}</dd></div>
        <div><dt>our {t.ourLabel}</dt><dd className="us-text">{fmtP(t.ourPrice)}</dd></div>
        <div><dt>gap</dt><dd>{fmtP(t.gap)}</dd></div>
        <div><dt>rounds</dt><dd>{t.rounds}</dd></div>
      </dl>
      {lastText != null && (
        <div className={styles.line}>
          <Injection on={t.suspicious} />
          <blockquote className={styles.quote}>“{lastText}”</blockquote>
        </div>
      )}
      <PriceRail bubbles={bubbles} threadId={t.id} theirLabel={t.theirLabel} ourLabel={t.ourLabel} />
      {bubbles.length === 0 ? <Empty>No offers yet.</Empty> : (
        <ol className={styles.convo} aria-label={`Conversation in thread ${t.id}`}>
          {bubbles.map((b) => <BubbleItem key={b.eventId} b={b} label={b.side === "us" ? t.ourLabel : t.theirLabel} />)}
        </ol>
      )}
    </div>
  );
}
