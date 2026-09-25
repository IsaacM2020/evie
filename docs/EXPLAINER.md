# Evie, explained (the whole system, 2026-09-25)

This is a plain-English explanation of Evie, a Jarvis-style voice assistant that Isaac (16, Singapore) built on his MacBook Pro (M4). It covers what runs where, the path of one sentence from the mic to her answer, every model and what it does, the parts (hands, jobs, proactive, text mode, the on-screen capsule), where the logs are, how to debug it, and what's still weak. Code: private GitHub repo `IsaacM2020/evie`, on disk at `~/Elemental/Water/evie`.

---

## 1. What Evie is, in 6 lines

1. You talk (hold left Control+Option, click her capsule, or just talk with the open mic on). She answers out loud in about a second.
2. Every sentence gets one fast **decision**: is it for Evie, and if so, what kind of thing is it (answer, quick action, remember, background job, job control)?
3. Quick things run on the spot: Spotify, volume, apps, websites, timers, to-dos, calendar events, messages, and clicking around in Safari or any Mac app.
4. Big things (coding, research, fixing stuff) go to **Claude Code running in the background**. She tells you it's started, narrates only what matters, and sums it up at the end. You can keep talking to her while it works.
5. She **brings things up by herself** (a class in 15 minutes, what's due, a plan she overheard) but only when you're free, never in class, a call or a conversation.
6. In class (from his calendar) she goes **text only**: nothing is spoken, answers appear on the capsule's card.

---

## 2. The machines and processes

Everything runs on the MacBook. (The Mac Mini and Raspberry Pi in Isaac's setup are NOT part of Evie v2; the Mac Mini's `~/jarvis` is the old v1.)

| Process | What it is | Kept alive by |
|---|---|---|
| **evie-core** | Python 3 FastAPI server on `127.0.0.1:8765` (local only). The brain: decisions, speech-to-text, the words, her voice, jobs, proactive engine. Exactly ONE process, about 930 MB of RAM. | launchd `com.isaac.evie.core` (restarts it in ~6 s if it dies) |
| **Evie.app** | Swift menu bar app in `~/Applications/Evie.app` (built with Command Line Tools only, no Xcode). It owns the mic, the keyboard shortcuts, the Calendar, Accessibility and Automation permissions, and draws the capsule. It's also "the hands": it runs AppleScript, reads screens and presses buttons when the core asks. | launchd `com.isaac.evie.app` |
| **Claude Code jobs** | Started by the core through the Claude Agent SDK, working in `~/IsaacOS` (Isaac's notes/projects folder). One job at a time. A hook blocks `rm`, `sudo` and force-push. | the core |

Why split it this way: macOS only grants mic, calendar and "control this app" permissions to a signed app, not to a background Python process. So the app holds the permissions and does the physical actions; the core holds the models and the logic. They talk over local HTTP + WebSockets:

- `/ws` pushes events from the core to the app (heard, said, state, job steps, "Which one?" rows, lists, follow-ups).
- `/ws/ears` streams the open mic from the app to the core (16 kHz, 32 ms frames).
- `/ws/mouth` streams her voice from the core to the app, so it plays through the same audio engine as the mic (that's what lets Apple's echo canceller remove her own voice).
- `do` commands go core → app ("open this url", "press element w5", "add this calendar event"), each with an id and an expiry so nothing is ever replayed twice.

---

## 3. Every model and what it's for

| Model | Where | Job | Why this one |
|---|---|---|---|
| **Jev** (`jev-1.13`, via OpenRouter) | cloud, ~0.4 s | The decider. It doesn't write text: it answers typed questions with probabilities. For every sentence, ONE call answers ~13 questions at once: is it for Evie, which route, is it complete, is there an event in it, which skill, where a "remember" goes (task/event/fact), which context packs to load (calendar, to-dos, projects, screen, web), is it hard, is it a long job. Also: "is this worth saying out loud?" for job narration, "which of these on-screen items did he mean?", "was that an answer to her question?", "is now a good moment?" for proactive, and which Claude runs a job. | Fast, cheap (~$0.00003 a call), and it can't invent an option that isn't on the list. |
| **Groq Whisper large-v3-turbo** | cloud, ~0.3 s | Speech to text. It gets a "prompt" of names to expect (Parrot, Darryl, Dada, Mamma, Jev, Netflix...) built from names he spelled out loud and his `people.json`. A local Whisper small.en is only a fallback. | Measured word error 0.054 vs 0.084 for the local one; only Isaac's own voice is ever sent. |
| **Groq Qwen 3.8 27B** (`qwen/qwen3.8-27b`, thinking off) | cloud, ~0.2 s | The words: answers, clarifying questions, narration lines, job summaries, pulling details out as JSON (a song title, a date, a URL), rewriting "play that song" into "play Trance by Travis Scott", finding things on screen when plain code can't. | Fastest good writer. |
| **Groq gpt-oss-120b** | cloud, ~1-2 s | Hard questions (Jev flags them), and writing the plan for screen tasks. Falls back to smaller models when Groq's per-minute limit is hit. | Actually reasons, still fast. |
| **Pocket TTS** (Kyutai), voice **"eve"** | local CPU | Her voice. Streams: first sound ~30 ms after a line starts. "On it." style lines are pre-rendered. | Local, instant, and Isaac picked the voice by ear. |
| **Silero VAD** | local | Open mic: "is someone talking?" every 32 ms. | 2 MB, under 1 ms per frame. |
| **WeSpeaker ResNet34** | local | Voice ID: 256 numbers describing a voice. Isaac ≥ 0.65 similarity, other < 0.45, in between = unknown. Other people's speech is dropped before Whisper. | Small, no big audio libraries. |
| **Claude (via Claude Code)** | cloud | Background jobs. Jev picks the model per job: **Haiku 4.5 (effort low)** for quick lookups and simple screen tasks, **Sonnet 5 (medium)** by default, **Sonnet 5 (high)** for coding/debugging. **Opus is refused in code.** | Right-sized: quick jobs finish fast and cheap. |

The safety rules are NOT in any model. A small plain-code file (`switchboard/policy.py`) turns Jev's probabilities into act / ask / ignore, with thresholds tuned on an eval set. "Only Isaac's voice can make her do things" is an `if` statement, so no sentence (or YouTube video) can talk its way past it.

---

## 4. One sentence, from the mic to her answer

Example: Isaac holds left ⌃⌥ and says **"What's due today?"**

1. **Key down.** The app plays a small "tink", keeps the last 0.4 s of mic audio (so the first syllable isn't lost) and tells the core he's talking (`state: listening`, which also stops her if she was speaking).
2. **Key up.** The app sends a 16 kHz WAV to `POST /voice`. The capsule shows "Thinking".
3. **Speech to text.** Groq Whisper with the names prompt: "What's due today?" (~300 ms). Spelled-out names ("P-A-R-R-O-T") are joined and remembered for next time.
4. **Two things start at the same time:**
   - **Jev** gets a short description of the moment ("Isaac held the talk key", who's speaking, whether she just asked something, what job is running, the last few lines of conversation) and answers all its questions in one call: for_evie 0.97, route **answer**, need_tasks yes.
   - **A draft answer** starts on Groq in parallel, so if Jev says "answer", the words are nearly ready.
5. **The policy** (plain code): held the key → it's for her; route answer with good confidence → ACT.
6. **Context packs.** Jev said to-dos are needed, so the core loads Todoist (due today/overdue) and adds it to the facts (time, calendar now/today/tomorrow/week, today's conversation, things he asked her to remember, what she can and can't do).
7. **The words.** Qwen answers with one spoken sentence plus a list: "You have five things due today, Chem tuition HW first." + 5 items. Code splits it: the sentence is spoken, the items go to the card ("say it short, list it all"). Any maths is written as `[[expression]]` and computed by code, never by the model.
8. **Her voice.** Pocket TTS "eve" streams the sentence through the app's audio engine. The capsule grows into a card with the sentence and a numbered list (scroll to see more). It stays up long enough to read.
9. **Logged.** The turn goes to `turns.jsonl` and into today's conversation memory, so "tick off the second one" works next.

Typical time from releasing the key to her first sound: about 1 second.

**The same sentence on the open mic** (no key): the VAD finds the sentence, voice ID says it's Isaac (someone else's voice would be dropped right there), and it goes to Jev WITHOUT the "held the key" hint, so Jev also has to judge whether it was even for her. Starting with "Evie," or saying it within 12 seconds of her last reply counts as talking to her.

---

## 5. The routes (what happens after the decision)

- **answer**: the steps above. Hard questions go to gpt-oss-120b with "Let me think." if it takes a while; web questions say "Let me look that up." first.
- **quick_action**: a fast skill (Spotify play/pause/next, volume, open app, open site, timers, undo, move/delete events, tick off tasks) or the **computer** skill (screen control, below). Every action is checked (is that song actually playing?) and logged to `actions.jsonl`; most can be undone ("undo that").
- **remember**: Jev says where it goes. A task → Todoist. An event → Google Calendar "Isaac" through the Mac's Calendar (EventKit), with a clash check. A fact → a local file that feeds every answer. Missing a time? She asks once ("What time?") and merges his answer.
- **deep_job**: read back first ("Checking why your website deploy failed. Say stop if that's wrong."), 3 s to say stop, then a Claude Code job.
- **job_control**: "how's it going", "also add a test", "stop".
- **not for Evie**: ignored. If it contained a plan ("Mom, I've got the dentist Wednesday"), the proactive engine may follow up later.

**Conversation rules** (the part that makes her feel less robotic):
- She asks **at most one** question per request. After he answers, she acts on her best guess.
- An answer to her own question ("Tell him what's up", "the island one", "4pm") is treated as an answer, keeps the original request's route, and is never met with "Was that for me?".
- "That song", "open it", "the other one" are filled in from the last 2 minutes of speech (a rewrite runs in parallel with Jev, so it costs no time).
- "No, the other one" moves to her runner-up with no model call.
- If an answer promises an action ("Opening that article now"), it actually does it.

---

## 6. The hands (controlling the Mac)

Goal: do anything on the Mac, fast, in the background, without screenshots.

1. **Look once.** One `world` read from the app: which apps are open, windows front to back, every Safari tab.
2. **Plan once.** gpt-oss-120b writes the whole route in one call, using an "app card" (how YouTube, Netflix, Notion, WhatsApp, Mail, Finder, Settings... work). Steps are things like `open_url`, `expect` (check the screen), `find` (an element by name), `pick` (choose among similar rows), `press`, `set_text`, `key`, `done`.
3. **Eyes are text, not pixels.** In Safari a page script lists every link/button/field; in other apps the Accessibility tree does. Each element gets an id valid only for that snapshot, so nothing imaginary or stale can be clicked.
4. **Code first, models for choices.** A button with an obvious name is found by plain code (with fuzzy matching: "Darrell" finds "Darryl"). A real choice ("which video?") goes to Jev over the real ids. A screenshot with numbered boxes is the very last resort.
5. **Check, then replan.** `expect` re-reads for up to 6 s (pages load slowly); an app that just launched gets up to 8 s to show a window. A real miss means one replan that sees the failed screen (max 2), then Claude Code takes over with what was tried.
6. **"Which one?"** With no hint ("play a MrBeast video") she opens the list first, then asks, and his answer (voice or tap) picks from those same rows.
7. **Safety.** Sends, buys and deletes are caught twice (a word list on the button + the model's own flag), read back out loud, and wait 3 s for "stop". Messages (WhatsApp, iMessage) use his real Contacts ("my father" → Dada via `people.json`), never a guessed number.

---

## 7. Background jobs (Claude Code)

- Started with the Claude Agent SDK in `~/IsaacOS`, so it has his notes and projects as context.
- Jev picks Haiku / Sonnet / Sonnet high per job (never Opus); the capsule's job card shows which, plus "Step 2 of 5".
- Each tool call becomes a one-line event. Jev decides if it's worth saying; plain rules cap it (gaps, a max per job). Long jobs get a "still going" line every ~25 s.
- At the end Groq sums it up in two spoken sentences. If he was busy, it waits as a follow-up; if narration already told him the ending, it doesn't ask again.
- You can talk to her during a job. "Stop" cancels it.

---

## 8. Proactive (she brings things up)

Sources: a class/meeting in 15 min, what's due today (after school and evening), deadlines in 2 days, the morning brief (first activity after 5 am), a finished job while he was busy, a plan she overheard ("dentist Wednesday" → "What time?"), being stuck on the same error for 10 minutes, and "pick up where you left off?" after a 20+ minute break.

When: code gates first (he's present, not talking, nobody spoke for a minute, not in a call or class or at dinner, 5 minutes since her last one, max 3 an hour), then one Jev "good moment?". In class they become silent chips on the capsule. Chips expire (task chips after 2 hours) and a chip's Yes only counts after it has been on screen 0.6 s (so a click meant for the capsule can't start a job). Overheard sentences are never stored as text, only the extracted facts.

---

## 9. Text mode, the capsule, the menu

- **Text mode:** automatic when his calendar says class/school/exam, or ⌃⌥T. Nothing is synthesised; the words appear on the card with a type box; the open mic pauses.
- **The capsule:** a small glass pill on a screen edge with one line (her state: listening bars, thinking dash, working ring). It grows into one glass card for replies, lists, "Which one?" rows, follow-ups (Yes / Later / No), a Cancel bar, or the job with Stop. Drag it and it snaps to an edge.
- **Theme:** Auto (follows the Mac's light/dark), Light, Dark, in the right-click menu.
- **Right-click menu:** how she answers (by calendar / out loud / text only), mic mode (Live / Shadow / Off), what she brings up, Theme, show her work, record for tuning, hide, quit.
- **Crash-proofing:** AppKit owns every mouse event and every window size; SwiftUI only draws (it never receives a click). One layout function decides both what's drawn and what's tappable. A 60 s stress test drags and clicks the real panels.

**Shortcuts:** hold left ⌃⌥ = talk. Left ⌃⌥⌘ = Live open mic on/off. ⌃⌥T = text only. Click the capsule = talk (click again to send), or type in text mode. Right Option is a different tool (Ripple), untouched.

---

## 10. Files (where things live)

| Path | What |
|---|---|
| `src/evie/server.py` | builds everything and serves the HTTP/WebSocket API |
| `src/evie/brain.py` | one sentence in: decide, act, pending questions, follow-ups, lists |
| `src/evie/switchboard/` | Jev questions, the state text, the policy (act/ask/ignore) |
| `src/evie/jev.py`, `talk.py`, `stt.py`, `voice.py` | the model clients: Jev, Groq words, Whisper, Pocket TTS + the speech queue |
| `src/evie/open_mic.py`, `ears.py`, `voiceid.py` | open mic: sentences, voice ID, echo guard |
| `src/evie/skills/` | fast skills (music, system, timers, events, tasks, undo) |
| `src/evie/computer/` | the hands: world, app cards, find, planner, recipes, messages |
| `src/evie/jobs.py`, `narrator.py` | Claude Code jobs, tiers, narration |
| `src/evie/proactive/` | the follow-up queue, the engine (when), the sources (what) |
| `src/evie/remember.py`, `memory.py`, `context_packs.py`, `calendar_store.py`, `quiet.py` | remembering, today's conversation, knowledge packs, the calendar, text mode |
| `mac/EvieBar/Sources/EvieBar/` | the Swift app: `AppModel` (state), `Orb` (capsule + card), `Ears`, `Eyes`, `Hands`, `PushToTalk`, `SelfTest` |
| `evals/` | eval sets and runners (switchboard, hands, tiers, answers, narration, proactive, ears), recorded model answers in `cassettes/` |
| `tests/` | 752 offline tests |
| `docs/HOW-IT-WORKS.md` | the detailed build log, phase by phase |
| `~/Library/Application Support/Evie/` | voiceprint, `people.json` (dad → Dada, mom → Mamma), `vocab.json` (learned names), follow-ups, recordings (only when "Record for tuning" is on) |

---

## 11. Logs and how to debug

Logs are in `~/Library/Logs/Evie/`:

- `core.log`: everything the core does (errors, timings, rate limits).
- `turns.jsonl`: one line per sentence: what she heard, Jev's decision, what she did and said, timings.
- `actions.jsonl`: every skill action and its check.
- `speech.jsonl`: her speech timeline (catches two voices at once; `"ev":"text"` = shown instead of spoken).
- `app.log`: the Swift app.

Handy checks:

- Is it alive? `curl -s localhost:8765/status` (Jev, Whisper, voice ready, mic mode). Exactly one core: `pgrep -f evie/.venv/bin/evie-core`.
- What's on screen, as she sees it: `curl -s -X POST localhost:8765/debug/do -H 'content-type: application/json' -d '{"op":"world"}'`.
- Calendar she sees: `curl -s localhost:8765/debug/calendar`. Follow-ups waiting: `curl -s localhost:8765/followups`.
- Open mic health: `curl -s localhost:8765/ears/stats`.
- App self-test (62 checks): `~/Applications/Evie.app/Contents/MacOS/EvieBar --selftest`. Stress: `--orbstress 60`.
- Tests: `uv run pytest`. Evals: `uv run python -m evals.run --split all --label x` (switchboard), `evals.run_computer` (35 hands tasks on a pretend Mac), `evals.run_tiers`, `evals.run_answers`, `evals.run_narration`, `evals.run_proactive`, `evals.run_ears`.
- Restart the core: `launchctl kickstart -k gui/$(id -u)/com.isaac.evie.core`. Rebuild the app: `cd mac && ./build.sh`.

How bugs get fixed: reproduce first (logs, a replayed turn, or a failing test), then the smallest fix, test first. Evals replay recorded model answers so they're free and repeatable; anything reworded goes live automatically.

---

## 12. Current numbers (2026-09-25)

| What | Result |
|---|---|
| Switchboard eval (~190 labelled sentences) | false actions **0**, route accuracy 0.968 |
| Hands eval (35 tasks on a pretend Mac) | **35/35**, unsafe 0, median 2 model calls |
| Job model picks (15 goals) | 15/15, Opus 0 |
| Answers to her own questions (12) | 12/12 |
| Narration eval | 0.92 |
| Simulated proactive day | pass (max 3 an hour, 0 in class/call/conversation) |
| Tests | 752 offline + 62 app self-checks |
| Core RAM | ~930 MB, one process |
| Key release → first sound | ~1 s typical |

---

## 13. Known gaps (honest list)

- **Live mic vs talk key.** Both use the same mic and echo canceller. The measured Live problems were mostly addressing (answers ignored) and names, and both are fixed. But Apple's echo canceller zeroes short stretches of audio (up to 28% of some sentences), which can clip soft word starts. A 2-minute "ear test" (12 lines read with the key and in Live, recorded) decides whether a raw mic is better.
- **Screen control still meets new apps it has no card for.** It works from the Accessibility tree, but some apps expose little; then it falls back to a screenshot or Claude Code.
- **Groq per-minute limits.** gpt-oss-120b and Qwen have per-minute token caps; heavy bursts fall back to smaller models or wait.
- **If Jev can't be reached, she does nothing** (by design: no decision, no action).
- **One background job at a time.**
- **The stuck-on-an-error detector** reads Terminal; VS Code's terminal usually hides its text from Accessibility.
- **Classroom calendars:** only some sync to the Mac, so deadlines lean on Todoist.
- **Needs Isaac for live checks:** anything that plays music, drives his screen, sends a message or writes his calendar is only tested on a pretend Mac until he tries it.
