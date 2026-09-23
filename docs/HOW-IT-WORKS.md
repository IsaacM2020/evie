# How Evie works

Phase 0 (the decider) is first; Phase 1 (voice + deep work) is below it.

# Phase 0: the decider

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
| `evals/cases.jsonl` | The 70 labelled test sentences |
| `evals/metrics.py` | Scoring + targets |
| `evals/run.py` | Runs every case against live Jev, prints the report, saves the run, shows "flips" vs the last run |
| `evals/TUNING.md` | Every tuning change and its numbers |
| `ops/` | launchd agent + installer |
| `mac/EvieBar/` | The Swift menu bar app. `mac/build.sh` builds it into `~/Applications/Evie.app` |
| `tests/` | 50 offline tests + 1 live test |

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

- **Speaker is a manual picker** ("Me / Someone else"). Real voice ID comes in Phase 2. Until then, "only Isaac commands" is only as good as that picker.
- **The 70 sentences are written by us, not recorded.** Real transcripts will be messier. Phase 2 adds real recordings of Spanish class, mom, YouTube, etc.
- **Watch `c09`:** "play the one from yesterday", said to someone asking about car music, scores 0.75 "for Evie". Only the missing-detail rule stops it from acting. It's the first thing to re-test with real audio.
- **Holdout soft spots:** "also add a test for it" (with a job running) gets "was that for me?" instead of just doing it.

---

# Phase 1: talk while it works

## The big idea in 5 lines

1. Hold 🌐 (Fn), talk, let go. The menu bar app records your voice and sends the WAV to the core.
2. The core turns it into text (local Whisper), asks Jev what to do (Phase 0's switchboard, unchanged), and acts.
3. Real work goes to **Claude Code running in the background** (Agent SDK, in ~/IsaacOS with all your context). Evie says "On it." right away.
4. While the job runs, you can keep talking: ask questions, check on it, add instructions, or stop it. Jev decides which job steps are worth saying out loud.
5. When the job finishes, Groq squeezes the result into two spoken sentences.

## Background concepts

**STT (speech to text).** Whisper is a model that turns audio into text. `mlx-whisper` runs it on the M4's GPU, so your voice never leaves the Mac. We use the small English model: 0.3s per sentence, vs 1.2s for the big one.

**TTS (text to speech).** Kokoro is a small (82M parameter) voice model that runs locally on CPU. About 0.7s to render a short sentence. The three most common lines ("On it.", "Was that for me?", "Can't do that one yet.") are rendered once at startup, so they play instantly.

**Agent SDK.** Anthropic's Python library that drives Claude Code the same way you do in the terminal, but from code. We get a stream of messages (tool calls, text, final result), which is what the narrator listens to.

**WebSocket.** A connection that stays open so the core can *push* events to the app the moment they happen (heard, verdict, said, job step). HTTP can only answer when asked.

**TCC (macOS permissions).** macOS asks the *app* for Mic, Calendar and Accessibility (needed to see the Fn key while other apps are in front). That's why the Swift app owns those three and the Python core never asks for anything. Signing with your Apple Development cert gives the app a stable identity, so the grants survive rebuilds.

**Barge-in.** Pressing Fn while Evie is talking kills the audio instantly (`afplay` is a process we can kill).

## Who does what

| Part | Where | Job |
|---|---|---|
| Ears + eyes | Swift app | Fn key, mic recording, Calendar, the panel |
| Reflex | Jev | for_evie / route / complete / event (Phase 0), plus `job_op` and `worth_saying` |
| Words | Groq `gpt-oss-20b` | answers, clarifying questions, narration lines, job summaries |
| Voice | Kokoro (local) | text to audio, one line at a time |
| Hands | Claude Code (Agent SDK) | the actual work, one job at a time |
| Rules | plain Python | who can command, thresholds, 10s narration gap, rm guard |

## File map (new in Phase 1)

| File | Job |
|---|---|
| `src/evie/calendar_store.py` | Keeps the calendar snapshot the app pushes; "Tomorrow: 9:00 Math, 14:30 iGEM" |
| `src/evie/talk.py` | Groq client + Talker (reply / clarify / narrate / summarize) + `clean()` so text is safe to speak |
| `src/evie/voice.py` | Kokoro `Synth` + `Mouth`, the speech queue (replies first, old narrations dropped, stop = barge-in) |
| `src/evie/stt.py` | `Transcriber`: local Whisper (Groq as a switch), skips clips under 0.3s, fixes "Eevee" to "Evie" |
| `src/evie/jobs.py` | `JobRunner` for Claude Code, the rm/sudo/force-push guard, and `describe()` (tool call to one short line) |
| `src/evie/narrator.py` | Asks Jev "worth saying?" per job step, plus the gap/cap rules; speaks the summary at the end |
| `src/evie/brain.py` | One sentence in: verdict, then act (answer / job / job control / clarify / not yet). Logs timing |
| `src/evie/events.py` | The event bus the WebSocket reads |
| `src/evie/server.py` | `/voice`, `/voice/start`, `/hear`, `/job`, `/job/stop`, `/calendar`, `/ws`, plus Phase 0's `/decide` |
| `mac/.../PushToTalk.swift` | The Fn state machine (tap or Fn+arrow = cancel) |
| `mac/.../Recorder.swift` | Mic to 16 kHz WAV |
| `mac/.../EventFeed.swift` | WebSocket client, reconnects every 2s |
| `mac/.../CalendarFeed.swift` | EventKit, next 8 days, pushed every 5 min + on change + when the core restarts |
| `evals/narration.jsonl`, `evals/run_narration.py` | 25 labelled job steps for the narrator |

## One sentence, traced end to end

You hold 🌐 and say: **"Evie, look through the Evie repo for functions longer than 40 lines…"** (real run, demo 3).

1. **Fn down.** `PushToTalk` returns `startRecording`. The app starts `Recorder` and calls `POST /voice/start`, which stops anything Evie is saying and publishes `state: listening`. The icon turns into a mic.
2. **Fn up** (held ≥ 0.25s). `stopAndSend`: the WAV goes to `POST /voice`.
3. **Whisper**, 507 ms: "Look through the EV repo for functions longer than 40 lines…" (it dropped the "Evie," and misheard the name, which is fine).
4. **Jev switchboard**, ~630 ms: route `deep_job`, confident, complete. Policy says ACT.
5. **Brain**: plays the cached "On it." clip (instant), strips the wake word, and calls `runner.start(goal)`. Total from audio arriving to "On it." queued: **1.14 s**.
6. **Claude Code** starts in ~/IsaacOS. Each tool call becomes a line: "Ran: cd ~/Elemental/Water/evie && …". The narrator asks Jev whether each line is worth saying: 0.16, 0.13, 0.10… all routine, so she stays quiet.
7. **You ask mid-job**: "Evie, what's on tomorrow?" → answer route → Groq gets the real calendar from the app's EventKit snapshot → "Tomorrow's all-day Vedant's birthday, then school at 8…" spoken 1.5 s later, while the job keeps running.
8. **Job done** at 41 s. Groq summary: "Got the rundown, Isaac. We'll split create_app first, then tackle PanelView." Spoken as a reply. The panel's job card clears.

## Why each choice beat the alternatives

- **App owns permissions, core owns the brain.** A background Python process can't show macOS permission popups properly, and ad-hoc signing forgets grants every build. The app is signed once and asks once.
- **Whisper small.en vs large-v3-turbo vs Groq.** Measured on the same 3s clip: 315 ms / 1190 ms / 340-760 ms. Turbo alone blew the 1s "On it." budget; Groq's time swings with the network. Small.en is local, private and steady.
- **Cached ack clips.** Rendering "On it." takes ~0.7s. Rendering it once at startup makes the ack free.
- **Jev decides narration, not Groq.** Jev answers a yes/no in ~0.4s for $0.00003 and can't ramble. Groq only writes the words once Jev says yes. Plain rules (10 s gap, max 6 per job) stop her chattering even if Jev is keen.
- **Mid-job instructions are queued as the next turn**, not injected mid-stream. The SDK's response stream ends at the first result; injecting mid-stream risked waiting forever for a result that never comes. Cost: "also add a test" runs right after the current step finishes.
- **The rm guard is a Claude Code hook, not a prompt.** A prompt can be ignored; a PreToolUse hook blocks the command before it runs. Tested live: the job tried `rm keep.txt` and the file survived.

## Numbers (2026-09-23)

| what | result |
|---|---|
| Phase 0 eval, all 72 cases (after one Phase 1 tweak) | false_action **0**, recall 1.0, route 0.972, complete 0.923, event 0.943, p95 640 ms |
| Narration eval, 25 steps | accuracy 0.96, false-yes 0.0, recall 0.889 |
| Audio arrives → "On it." queued | 1.14 s, 1.27 s, 1.14 s (Whisper 460-560 ms + Jev 570-800 ms). Target was ≤ 1.0 s: **just missed** |
| Typed question → spoken answer starts | ~1.9 s (Jev + Groq + Kokoro render) |
| Mid-job answer from the calendar | 1.5 s |
| Tests | 158 offline + 12 live |

## Known limits

- **"On it." takes ~1.15 s, not under 1 s.** Jev is slower in real use (~600 ms) than in evals (~400 ms). The next lever is a tiny "got it" click when Fn is released.
- **Narration was silent in all three demos.** Short jobs (20-160 s) mostly run tool steps, which are rightly skipped. The "3-5 useful narrations" bar needs a real multi-minute job to judge.
- **If Jev can't be reached, Evie says nothing** (the Phase 0 safety rule). During one demo a network drop made three questions in a row go silent. That needs a decision: see the Phase 1 wrap-up.
- **One job at a time.** A second deep_job gets "Still on X. Say stop first."
- **quick_action and remember** answer "Can't do that one yet" until Phase 3.
- **Speaker is still always "me"** in voice mode. Voice ID is Phase 2.
