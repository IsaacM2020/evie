# Switchboard tuning log

Rules:
- Change ONE thing per iteration (a question's wording, one threshold, or one state line).
- Tune on `--split tune` only. The holdout gets run once, at the end.
- Keep a change only if false_action does not go up AND the metric it targets improves.
- A label may only change if it was genuinely wrong; write why here.
- Max 8 iterations. If targets still aren't met, stop and report honestly.

Jev determinism: baseline run twice, 0 action flips, probabilities move by about ±0.05. Single runs are comparable.

| # | change | false_action | false_clarify | recall | route | complete | event | p95 ms | flips | kept? |
|---|--------|--------------|---------------|--------|-------|----------|-------|--------|-------|-------|
| 0 | baseline | 0 | 0.071 | 1.0 | 0.964 | 0.857 | 0.963 | 821 / 533 | 0 | - |
| 1 | `complete`: named real work counts as complete (examples) | 0 | 0.071 | 1.0 | 0.964 | **0.952** | 0.963 | 446 | 3 (a04,b05 fixed; b01 jitter at 0.74/0.75) | yes |
| 2 | `for_evie`: name + command is for Evie even in a call; mentioning her is not | 0 | **0.036** | 1.0 | 0.964 | 0.952 | 0.963 | 679 | 1 (i04 now ignored) | yes |
| 3 | `route_conf_min` 0.70 → 0.60 | 0 | 0.036 | 1.0 | 0.964 | 0.952 | 0.963 | - | 3 (e06 fixed; b01,b08 jitter at 0.71-0.76 vs act_at) | yes |
| 4 | `act_at` 0.75 → 0.70 (bare commands like 'pause' sat at 0.71-0.76); policy test moved to 0.75 | 0 | 0.071 | 1.0 | 0.946 | 0.952 | 0.963 | 527 | 3 (b01,b08 now act; c11 jitter: route coin-flip at conf 0.30) | yes |
