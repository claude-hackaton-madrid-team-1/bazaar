import { Suspense } from "react";
import { NegotiationsView } from "../../components/negotiations/NegotiationsView.tsx";
import { Empty, Panel } from "../../components/ui.tsx";

export default function NegotiationsPage() {
  return (
    <Suspense fallback={<Panel title="Negotiations"><Empty>Loading…</Empty></Panel>}>
      <NegotiationsView />
    </Suspense>
  );
}
