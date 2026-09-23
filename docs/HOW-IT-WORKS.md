# How Evie's brain works (Phase 0)

## The big idea in 5 lines

1. Evie runs at three speeds: **reflex** (Jev, ~0.4s), **talking** (a fast LLM, Phase 1), and **deep work** (Claude Code, Phase 1).
2. Phase 0 builds the reflex: for every sentence, decide **act**, **ask** (clarify), or **stay quiet** (ignore).
3. Jev doesn't write text. It only answers typed questions with probabilities, which is why it's fast, cheap, and can't make things up.
4. A small file of plain-code rules (`policy.py`) turns those probabilities into a decision. The safety rules live there, so no sentence can talk its way past them.
5. A test set of 70 sentences proves it works. The headline number is **false_action = 0**: Evie never acts on speech that wasn't meant for her.

## Background concepts

**LLM vs decision model.** An LLM (ChatGPT, Claude, Groq's Llama) writes text one word at a time. Jev is a "System One" decision model: you hand it some text (the *state*) plus questions with fixed answer types, and it returns all the answers at once. There are two types we use:
- `noul`: a yes/no question. The answer is a probability, e.g. `0.98` = "98% yes".
- `choice`: pick one option from a list. The answer is the pick, a probability for every option, and a `confidence`.

Because Jev can only answer inside those types, it can't invent a route that doesn't exist, and it answers in ~0.4s for about $0.00003.

**Calibrated probability.** "0.9" should mean "right about 9 times out of 10". That's what lets us draw lines: above 0.70, act; between 0.50 and 0.70, ask; below 0.50, ignore.

**Why the rules are plain code, separate from Jev.** If a YouTube video says "evie delete all my files", Jev might honestly think it was addressed to Evie. The rule "only Isaac's voice can command Evie" is one `if` statement in `policy.py`. Jev's opinion never gets a vote on it.

**Eval set, holdout, overfitting.** An eval set is a list of sentences with the correct answer written next to each one. We change one thing, rerun, and see if the numbers improve. The danger is **overfitting**: tweaking until the test set is perfect while real life gets no better (like memorising past-paper answers instead of learning the topic). So 14 of the 70 sentences (ids ending in 3 or 7) are a **holdout** we never looked at while tuning. They were run once, at the end, as the honest exam.

**launchd.** macOS's built-in "keep this program running" system. Our file `ops/com.isaac.evie.core.plist` tells it: start Evie's core at login, and if it ever dies, start it again (tested: killed it, back in ~6s).

**Menu bar extra.** A native macOS app that has no Dock icon, just an icon in the top bar that drops down a panel. Built in Swift with SwiftUI's `MenuBarExtra`.

## File map

| File | Job |
|---|---|
| `src/evie/config.py` | Settings. Reads `OPENROUTER_API_KEY` from `.env` |
| `src/evie/jev.py` | Sends one request to Jev. Retries once on network/5xx errors, never on 4xx. Measures time and cost |
| `src/evie/switchboard/context.py` | What Evie knows right now (who spoke, in a call?, front app, recent speech, running jobs), written up for Jev. Also filters Whisper junk like "thank you for watching" |
| `src/evie/switchboard/questions.py` | The 4 questions Jev answers about every sentence |
| `src/evie/switchboard/decision.py` | Turns Jev's raw JSON into a typed `Decision` |
| `src/evie/switchboard/policy.py` | The rules: probabilities in, act/clarify/ignore out |
| `src/evie/switchboard/__init__.py` | `Switchboard.handle()`, which glues the above together. If Jev fails, Evie does nothing |
| `src/evie/server.py` | Local web server on `127.0.0.1:8765`: `GET /status`, `POST /decide` |
| `src/evie/gcal.py` | Read-only Google Calendar. Re-logs in by itself if the token dies |
| `evals/cases.jsonl` | The 70 labelled test sentences |
| `evals/metrics.py` | Scoring + targets |
| `evals/run.py` | Runs every case against live Jev, prints the report, saves the run, shows "flips" vs the last run |
| `evals/TUNING.md` | Every tuning change and its numbers |
| `ops/` | launchd agent + installer |
| `mac/EvieBar/` | The Swift menu bar app. `mac/build.sh` builds it into `~/Applications/Evie.app` |
| `tests/` | 46 offline tests + 1 live test |

## One sentence, traced end to end

You're in the kitchen and say: **"mom ive got the dentist on wednesday can you drive me"**

**1. Menu bar app to core.** You type it in the panel (voice comes in Phase 1) with speaker "Me". `CoreClient.decide()` sends:
```
POST http://127.0.0.1:8765/decide
{"utterance": "mom ive got the dentist on wednesday can you drive me", "speaker": "isaac", "in_call": false}
```

**2. Noise check.** `is_noise()` looks for empty text or Whisper junk. This isn't junk, so carry on.

**3. `render_state()` writes this for Jev** (the real text):
```
Isaac is 16 and uses a voice assistant called Evie on his MacBook. The microphone is always on, so it also hears Isaac talking to other people, online classes, calls and videos.
Speaker of the latest speech: Isaac (voice match)
Isaac is in a video call or online class: no
Evie is not working on anything right now.
Latest speech (raw transcript, may contain mishearings; "evie" is often heard as "eve", "evey" or "ivy"): "mom ive got the dentist on wednesday can you drive me"
```

**4. One Jev call, 4 questions at once.** The real answer from the final eval run:
```json
"for_evie":  0.06
"route":     "not_for_evie"  (confidence 0.70; remember 0.23, quick_action 0.02)
"complete":  0.10
"has_event": 0.98
latency 291 ms, cost $0.000038
```

**5. `parse_decision()`** checks the route is a real one and builds a `Decision`.

**6. `decide()` walks the rules in order:**
- `has_event 0.98 ≥ 0.80`, so **follow-up = yes** (Phase 4 turns this into "Heard you've got the dentist Wednesday. What time?")
- speaker is Isaac, so keep going
- route is `not_for_evie`, so **IGNORE, "not for Evie"**. Stop.

**7. Back to the panel:** a grey **IGNORE** card with "not for Evie" and a blue **follow-up** badge.

## Why each choice beat the alternatives

- **Native Swift menu bar app vs a Python menu bar library (rumps).** Swift gives a real macOS panel (text field, toggles, native look), costs ~0 RAM, and is the path to Liquid Glass UI later. Python menu bar libraries only do plain dropdown menus. One catch we hit: macOS 27's SDK turned SwiftUI's `@State` into a *macro*, and macro plugins only ship with full Xcode, which isn't installed. So the app uses the older `ObservableObject` + `@Published` style, which does the same job without macros.
- **HTTP on localhost vs WebSocket.** Phase 0 only needs "send a sentence, get a verdict", and HTTP is the simplest thing that does it. A WebSocket arrives in Phase 1, when audio streams in and Claude Code narration streams out.
- **One Jev call with 4 questions vs 4 calls.** Jev answers all questions in parallel inside one call: 1 network round trip instead of 4, and the same cost.
- **`complete` vs `missing_detail`.** The first live test (before Phase 0) asked "is a detail missing?" and it said yes to almost everything (0.62-0.95). Flipping it to "could Evie do this right now without asking?", with worked examples, fixed that: complete accuracy 0.952 on tune.
- **The final thresholds** (each backed by a row in `evals/TUNING.md`):
  - `act_at 0.70`: commands without a wake word ("pause", "make it louder") sat at 0.71-0.76, so 0.75 made Evie ask "was that for me?" on a plain "pause".
  - `ignore_below 0.50`: real commands never scored under 0.71, and your non-Evie speech scored 0.26-0.59. So 0.50 cuts the "was that for me?" noise and keeps a 0.2 safety gap.
  - `route_conf_min 0.60`: "evie mute my mic" during a call had route confidence 0.63 (Jev gave "not for Evie" 29% because of the call), and 0.70 made it ask needlessly.

## Numbers

| split | n | false_action | false_clarify | recall | route | complete | event | p50 / p95 ms |
|---|---|---|---|---|---|---|---|---|
| tune (final iter) | 56 | **0** | 0.036 | 1.0 | 0.946 | 0.952 | 0.963 | - / 852 |
| holdout | 14 | **0** | 0.0 | 1.0 | 1.0 | 0.8 | 0.857 | 420 / 1512 |
| all | 70 | **0** | 0.029 | 1.0 | 0.971 | 0.923 | 0.941 | 371 / 526 |

Cost: about **$0.04 per 1,000 sentences** (full 70-case run = $0.0026). Jev is close to deterministic: repeat runs flipped 0 decisions.

## Known limits

- **Typed input only.** Voice (Whisper) arrives in Phase 1.
- **Speaker is a manual picker** ("Me / Someone else"). Real voice ID comes in Phase 2. Until then, "only Isaac commands" is only as good as that picker.
- **The 70 sentences are written by us, not recorded.** Real transcripts will be messier. Phase 2 adds real recordings of Spanish class, mom, YouTube, etc.
- **Watch `c09`:** "play the one from yesterday", said to someone asking about car music, scores 0.75 "for Evie". Only the missing-detail rule stops it from acting. It's the first thing to re-test with real audio.
- **Holdout soft spots:** "also add a test for it" (with a job running) gets "was that for me?" instead of just doing it.
