"""The switchboard: sentence + context in, (Jev's decision, Evie's verdict) out."""
from dataclasses import asdict, dataclass

from evie.jev import JevError
from evie.switchboard.context import Context, is_noise, render_state
from evie.switchboard.decision import Decision, parse_decision
from evie.switchboard.policy import Action, Thresholds, Verdict, decide
from evie.switchboard.questions import QUESTIONS


@dataclass(frozen=True)
class Outcome:
    context: Context
    decision: Decision | None
    verdict: Verdict

    def to_dict(self) -> dict:
        return {
            "action": self.verdict.action.value,
            "reason": self.verdict.reason,
            "followup": self.verdict.followup,
            "decision": None if self.decision is None else asdict(self.decision),
        }


class Switchboard:
    def __init__(self, jev, thresholds: Thresholds = Thresholds()):
        self._jev = jev
        self._t = thresholds

    async def handle(self, ctx: Context) -> Outcome:
        if is_noise(ctx.utterance):
            return Outcome(ctx, None, Verdict(Action.IGNORE, "noise"))
        try:
            res = await self._jev.ask(render_state(ctx), QUESTIONS)
            d = parse_decision(res.answers, res.latency_ms, res.cost_usd)
        except (JevError, ValueError, KeyError, TypeError) as e:
            return Outcome(ctx, None, Verdict(Action.IGNORE, f"jev unavailable: {e}"))
        return Outcome(ctx, d, decide(d, ctx.speaker, self._t))

    async def aclose(self) -> None:
        await self._jev.aclose()
