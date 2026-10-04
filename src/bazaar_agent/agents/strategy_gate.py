"""Jev decides which strategy runs (SG1): one `questions/strategies.json` noul per strategy, asked with the
live state, again every `strategy_jev_refresh_ticks`. Only a decided yes turns a strategy on until the next
ask; undecided, no, a timeout or an exception keep it off (fail closed). Every answer is one
`strategy_gate` decision row (verdict, confidence, the state digest's inputs), so the log says why a strategy
ran or not. The verdict never lifts a limit: each send still passes `guardrails.check()`."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from bazaar_agent.agents.runtime import JevAdvice

QUESTIONS_FILE = "strategies.json"  # under questions/
LADDER_PROBE = "ladder_probe_worth_it"
DEALER_SELL = "dealer_sell_duplicates_worth_it"

AskFn = Callable[[str, dict[str, Any]], JevAdvice]


@dataclass(frozen=True)
class GateAnswer:
    tick: int
    on: bool
    advice: JevAdvice


class StrategyGate:
    """The cached Jev gate of each strategy. `allows(name, tick, state)` asks Jev at most once per
    `refresh_ticks` per strategy (a failed or undecided ask is asked again on the next refresh, not every tick:
    a Jev call costs tick budget). `state` is built only when an ask is due."""

    def __init__(self, ask: AskFn, rec: Any, refresh_ticks: int, posture: str = "") -> None:
        self.ask, self.rec, self.refresh_ticks = ask, rec, max(1, refresh_ticks)
        self.posture = posture  # `risk_posture` (GUARDRAILS.md): every state Jev reads carries it
        self.answers: dict[str, GateAnswer] = {}

    def due(self, name: str, tick: int) -> bool:
        last = self.answers.get(name)
        # No model answered when the tick budget ran out. Retry on the next tick,
        # but still coalesce repeated callers in this tick.
        refresh = 1 if last and last.advice.reason == "no tick budget for jev" else self.refresh_ticks
        return last is None or tick - last.tick >= refresh or tick < last.tick

    def allows(self, name: str, tick: int, state: Callable[[], Mapping[str, Any]]) -> bool:
        if not self.due(name, tick):
            return self.answers[name].on
        try:
            facts = dict(state())
            if self.posture:
                facts["risk_posture"] = self.posture
            advice = self.ask(name, facts)
        except Exception as e:  # noqa: BLE001 - a broken state or Jev call keeps the strategy off
            facts, advice = {}, JevAdvice("undecided", 0.0, reason=f"gate error: {type(e).__name__}")
        on = advice.verdict == "yes"
        self.answers[name] = GateAnswer(tick, on, advice)
        why = f"Jev {name}: {advice.verdict} ({advice.value:.2f})" + (f", {advice.reason}" if advice.reason else "")
        self.rec.decide(
            tick,
            "strategy_gate",
            f"strategy {name} {'ON' if on else 'off'}: {why}",
            inputs={"strategy": name, "verdict": advice.verdict, "value": advice.value, "state": facts},
            reason=why,
            guardrail="-",
            chosen=on,
            status="approved" if on else "rejected",
            jev=advice,
        )
        return on


def closed_gate(name: str, state: dict[str, Any]) -> JevAdvice:
    """No Jev (the agent ran with `--no-jev`, or no key): every strategy stays off."""
    return JevAdvice("undecided", 0.0, reason="jev off")
