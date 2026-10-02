"use client";

import { useRouter, useSearchParams } from "next/navigation";
import styles from "../../app/negotiations/negotiations.module.css";
import { useGame } from "../../lib/stream.tsx";
import { conversation, duelRows, selectedThreadId, threadList } from "../../lib/views/negotiations.ts";
import { Empty, Panel } from "../ui.tsx";
import { DuelsTable } from "./DuelsTable.tsx";
import { ThreadDetail } from "./ThreadDetail.tsx";
import { ThreadList } from "./ThreadList.tsx";

export function NegotiationsView() {
  const { state } = useGame();
  const params = useSearchParams();
  const router = useRouter();
  const rows = threadList(state);
  const selected = selectedThreadId(state, params.get("id"));
  const convo = selected == null ? null : conversation(state, selected);
  const duels = duelRows(state);
  const open = rows.filter((r) => r.status === "open").length;
  const select = (id: number) => {
    const q = new URLSearchParams(params.toString());
    q.set("id", String(id));
    router.replace(`?${q}`, { scroll: false });
  };
  return (
    <>
      <div className={styles.split}>
        <Panel title="Threads" sub={`${open} open · ${rows.length - open} closed`}>
          {rows.length ? <ThreadList rows={rows} selected={selected} onSelect={select} /> : <Empty>No threads yet.</Empty>}
        </Panel>
        <Panel title="Conversation" sub={convo ? `#${convo.thread.id} with ${convo.thread.with}` : undefined}>
          <ThreadDetail convo={convo} />
        </Panel>
      </div>
      <Panel title="Duels" sub={`${duels.filter((d) => d.status === "open").length} live`}>
        <DuelsTable rows={duels} />
      </Panel>
    </>
  );
}
