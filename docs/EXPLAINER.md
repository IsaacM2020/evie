# Evie, explained (the whole system, 2026-09-26)

This is a plain-English explanation of Evie, a Jarvis-style voice assistant that Isaac (16, Singapore) built on his MacBook Pro (M4). It covers what runs where, the path of one sentence from the mic to her answer, every model and what it does, the parts (hands, jobs, proactive, text mode, the on-screen capsule), the persistent-state layer added in Phase 6 (world model, goals, procedural memory, a real job supervisor, memory v2, self-monitoring), where the logs are, how to debug it, and what's still weak. Code: private GitHub repo `IsaacM2020/evie`, on disk at `~/Elemental/Water/evie`.

---

## 1. What Evie is, in 8 lines

1. You talk (hold left Control+Option, click her capsule, or just talk with the open mic on). She answers out loud in about a second.
2. Every sentence gets one fast **decision**: is it for Evie, and if so, what kind of thing is it (answer, quick action, remember, background job, job control)?
3. Quick things run on the spot: Spotify, volume, apps, websites, timers, to-dos, calendar events, messages, and clicking around in Safari or any Mac app.
4. Big things (coding, research, fixing stuff) go to **Claude Code**. One runs in the foreground (she narrates it, you can keep talking); others can now run **concurrently in the background** — pausable and resumable as a genuine continuation of the same Claude Code session, not a restart.
5. She keeps a small **persistent picture of the world** (what app you're in, what's running, your goals, recent actions, system health) that survives a restart of her own process — not just a request/response loop anymore.
6. She tracks **goals** you tell her about ("new goal: get the NOI qualification") across days, and after a couple of successes on a screen task she **remembers the plan** instead of replanning from scratch.
7. She **brings things up by herself** (a class in 15 minutes, what's due, a plan she overheard, a goal gone quiet) but only when you're free, never in class, a call or a conversation.
8. In class (from his calendar) she goes **text only**: nothing is spoken, answers appear on the capsule's card.

---

## 2. The machines and processes

Everything runs on the MacBook. (The Mac Mini and Raspberry Pi in Isaac's setup are NOT part of Evie v2; the Mac Mini's `~/jarvis` is the old v1.)

| Process | What it is | Kept alive by |
|---|---|---|
| **evie-core** | Python 3 FastAPI server on `127.0.0.1:8765` (local only). The brain: decisions, speech-to-text, the words, her voice, jobs, proactive engine, persistent world state. Exactly ONE process, about 930 MB of RAM. | launchd `com.isaac.evie.core` (restarts it in ~6 s if it dies) |
| **Evie.app** | Swift menu bar app in `~/Applications/Evie.app` (built with Command Line Tools only, no Xcode). It owns the mic, the keyboard shortcuts, the Calendar, Accessibility and Automation permissions, and draws the capsule. It's also "the hands": it runs AppleScript, reads screens and presses buttons when the core asks. | launchd `com.isaac.evie.app` |
| **Claude Code jobs** | Started by the core through the Claude Agent SDK, working in `~/IsaacOS` (Isaac's notes/projects folder). One in the **foreground** (Isaac is talking to it), up to two more concurrently in the **background** (bounded — a resource limit, not an ambition). A hook blocks `rm`, `sudo` and force-push regardless of which lane a job runs in. | the core |

Why split it this way: macOS only grants mic, calendar and "control this app" permissions to a signed app, not to a background Python process. So the app holds the permissions and does the physical actions; the core holds the models, the logic, and now the persistent state. They talk over local HTTP + WebSockets:

- `/ws` pushes events from the core to the app (heard, said, state, job steps, "Which one?" rows, lists, follow-ups, health).
- `/ws/ears` streams the open mic from the app to the core (16 kHz, 32 ms frames).
- `/ws/mouth` streams her voice from the core to the app, so it plays through the same audio engine as the mic (that's what lets Apple's echo canceller remove her own voice).
- `do` commands go core → app ("open this url", "press element w5", "add this calendar event"), each with an id and an expiry so nothing is ever replayed twice.

---

## 3. Every model and what it's for

Phase 6 added no new model and changed no model's job — this table is unchanged on purpose. The whole point of the persistent-state layer was to sit *beside* the existing decision-making, never inside it.

| Model | Where | Job | Why this one |
|---|---|---|---|
| **Jev** (`jev-1.13`, via OpenRouter) | cloud, ~0.4 s | The decider. It doesn't write text: it answers typed questions with probabilities. For every sentence, ONE call answers ~13 questions at once: is it for Evie, which route, is it complete, is there an event in it, which skill, where a "remember" goes (task/event/fact), which context packs to load (calendar, to-dos, projects, screen, web), is it hard, is it a long job. Also: "is this worth saying out loud?" for job narration, "which of these on-screen items did he mean?", "was that an answer to her question?", "is now a good moment?" for proactive, and which Claude runs a job. | Fast, cheap (~$0.00003 a call), and it can't invent an option that isn't on the list. |
| **Groq Whisper large-v3-turbo** | cloud, ~0.3 s | Speech to text. It gets a "prompt" of names to expect (Parrot, Darryl, Dada, Mamma, Jev, Netflix...) built from names he spelled out loud and his `people.json`. A local Whisper small.en is only a fallback. | Measured word error 0.054 vs 0.084 for the local one; only Isaac's own voice is ever sent. |
| **Groq Qwen 3.8 27B** (`qwen/qwen3.8-27b`, thinking off) | cloud, ~0.2 s | The words: answers, clarifying questions, narration lines, job summaries, pulling details out as JSON (a song title, a date, a URL), rewriting "play that song" into "play Trance by Travis Scott", finding things on screen when plain code can't. | Fastest good writer. |
| **Groq gpt-oss-120b** | cloud, ~1-2 s | Hard questions (Jev flags them), and writing the plan for screen tasks. Falls back to smaller models when Groq's per-minute limit is hit. | Actually reasons, still fast. |
| **Pocket TTS** (Kyutai), voice **"eve"** | local CPU | Her voice. Streams: first sound ~30 ms after a line starts. "On it." style lines are pre-rendered. | Local, instant, and Isaac picked the voice by ear. |
| **Silero VAD** | local | Open mic: "is someone talking?" every 32 ms. | 2 MB, under 1 ms per frame. |
| **WeSpeaker ResNet34** | local | Voice ID: 256 numbers describing a voice. Isaac ≥ 0.65 similarity, other < 0.45, in between = unknown. Other people's speech is dropped before Whisper. | Small, no big audio libraries. |
| **Claude (via Claude Code)** | cloud | Foreground + background jobs. Jev picks the model per job: **Haiku 4.5 (effort low)** for quick lookups and simple screen tasks, **Sonnet 5 (medium)** by default, **Sonnet 5 (high)** for coding/debugging. **Opus is refused in code.** | Right-sized: quick jobs finish fast and cheap. |

The safety rules are NOT in any model. A small plain-code file (`switchboard/policy.py`) turns Jev's probabilities into act / ask / ignore, with thresholds tuned on an eval set. "Only Isaac's voice can make her do things" is an `if` statement, so no sentence (or YouTube video) can talk its way past it. Goal commands and background-job voice control (pause/resume/stop) are a **second**, separate plain-code layer that never reaches Jev at all — see §9 and §7.

---

## 4. One sentence, from the mic to her answer

Example: Isaac holds left ⌃⌥ and says **"What's due today?"**

1. **Key down.** The app plays a small "tink", keeps the last 0.4 s of mic audio (so the first syllable isn't lost) and tells the core he's talking (`state: listening`, which also stops her if she was speaking).
2. **Key up.** The app sends a 16 kHz WAV to `POST /voice`. The capsule shows "Thinking".
3. **Speech to text.** Groq Whisper with the names prompt: "What's due today?" (~300 ms). Spelled-out names ("P-A-R-R-O-T") are joined and remembered for next time.
4. **Plain-code short-circuits run first.** Before any model is asked anything: is this "stop"? the answer to a question she just asked? "no, the other one"? a goal command ("how's my goal on X")? a job command ("pause that job")? None of those match here, so it falls through.
5. **Two things start at the same time:**
   - **Jev** gets a short description of the moment ("Isaac held the talk key", who's speaking, whether she just asked something, what job is running, the last few lines of conversation) and answers all its questions in one call: for_evie 0.97, route **answer**, need_tasks yes.
   - **A draft answer** starts on Groq in parallel, so if Jev says "answer", the words are nearly ready.
6. **The policy** (plain code): held the key → it's for her; route answer with good confidence → ACT.
7. **Context packs.** Jev said to-dos are needed, so the core loads Todoist (due today/overdue) and adds it to the facts (time, calendar now/today/tomorrow/week, today's conversation, things he asked her to remember, what she can and can't do).
8. **The words.** Qwen answers with one spoken sentence plus a list: "You have five things due today, Chem tuition HW first." + 5 items. Code splits it: the sentence is spoken, the items go to the card ("say it short, list it all"). Any maths is written as `[[expression]]` and computed by code, never by the model.
9. **Her voice.** Pocket TTS "eve" streams the sentence through the app's audio engine. The capsule grows into a card with the sentence and a numbered list (scroll to see more). It stays up long enough to read.
10. **Logged, twice.** The turn goes to `turns.jsonl` and into today's conversation memory (so "tick off the second one" works next) — **and** a small `heard`/`verdict` event goes onto the internal event bus, which the persistent world model (§8) folds into state that survives a restart. Nothing about *what was said* is stored there beyond what she acted on; see §11.

Typical time from releasing the key to her first sound: about 1 second.

**The same sentence on the open mic** (no key): the VAD finds the sentence, voice ID says it's Isaac (someone else's voice would be dropped right there), and it goes to Jev WITHOUT the "held the key" hint, so Jev also has to judge whether it was even for her. Starting with "Evie," or saying it within 12 seconds of her last reply counts as talking to her.

---

## 5. The routes (what happens after the decision)

- **answer**: the steps above. Hard questions go to gpt-oss-120b with "Let me think." if it takes a while; web questions say "Let me look that up." first.
- **quick_action**: a fast skill (Spotify play/pause/next, volume, open app, open site, timers, undo, move/delete events, tick off tasks) or the **computer** skill (screen control, §6). Every action is checked (is that song actually playing?) and logged to `actions.jsonl`; most can be undone ("undo that").
- **remember**: Jev says where it goes. A task → Todoist. An event → Google Calendar "Isaac" through the Mac's Calendar (EventKit), with a clash check. A fact → a local file that feeds every answer, now with a provenance tag (who/what it came from). Missing a time? She asks once ("What time?") and merges his answer.
- **deep_job**: read back first ("Checking why your website deploy failed. Say stop if that's wrong."), 3 s to say stop, then a Claude Code job in the foreground.
- **job_control**: "how's it going", "also add a test", "stop" — for the one **foreground** job, decided by Jev (unchanged since Phase 1).
- **goal command / job command** (Phase 6, plain code, never reaches Jev): "new goal: X", "how's my goal on X going", "pause/resume my goal on X", "mark the goal on X done" (§9); "pause that job", "pause job two", "resume it", "stop that job" for a **background** job (§7). Both work the same way `is_stop()`/`is_other()` already did before Phase 6: a regex-shaped trigger phrase is matched in plain code and the whole request never touches the switchboard, so none of Jev's tuned thresholds can drift.
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

1. **Check memory first.** Before planning anything, `Recipes` asks the new **procedural memory** (§10) if this exact request has succeeded on-screen before. If it has (2+ times) and hasn't recently failed, it replays that saved plan directly — no planning model call at all — but still runs every step through the planner's own `expect` checks, so a changed screen is caught, not blindly trusted.
2. **Look once.** One `world` read from the app: which apps are open, windows front to back, every Safari tab.
3. **Plan once.** gpt-oss-120b writes the whole route in one call, using an "app card" (how YouTube, Netflix, Notion, WhatsApp, Mail, Finder, Settings... work). Steps are things like `open_url`, `expect` (check the screen), `find` (an element by name), `pick` (choose among similar rows), `press`, `set_text`, `key`, `done`.
4. **Eyes are text, not pixels.** In Safari a page script lists every link/button/field; in other apps the Accessibility tree does. Each element gets an id valid only for that snapshot, so nothing imaginary or stale can be clicked.
5. **Code first, models for choices.** A button with an obvious name is found by plain code (with fuzzy matching: "Darrell" finds "Darryl"). A real choice ("which video?") goes to Jev over the real ids. Cloud vision is the deliberate last resort (max 2 calls per task, logged): a screenshot with numbered boxes when structured reading comes back too thin (Isaac's example: on a PDF, Accessibility gives nothing), or, for a specific visual question ("what does this diagram show") rather than finding something to press, one targeted question against the same screenshot with no numbered boxes at all (§13).
6. **Check, then replan.** `expect` re-reads for up to 6 s (pages load slowly); an app that just launched gets up to 8 s to show a window. A real miss means one replan that sees the failed screen (max 2), then Claude Code takes over with what was tried.
7. **On success, learn.** If a fresh plan (not a memory replay) finishes cleanly, procedural memory records it; a second clean success on the same request promotes it to "reuse me next time" (§10).
8. **"Which one?"** With no hint ("play a MrBeast video") she opens the list first, then asks, and his answer (voice or tap) picks from those same rows.
9. **Safety.** Sends, buys and deletes are caught twice (a word list on the button + the model's own flag), read back out loud, and wait 3 s for "stop". Messages (WhatsApp, iMessage) use his real Contacts ("my father" → Dada via `people.json`), never a guessed number.

---

## 7. Background jobs (Claude Code) — the Phase 6 job supervisor

Through Phase 5, this was simple: one job at a time, in the foreground, queued if another came in ("do this after"). **Phase 6 rewrote the job runner (`jobs.py`) to add a real supervisor beside that, without changing the foreground path at all** — every pre-existing test of "one job, narrated, stoppable" still passes byte-for-byte.

**Foreground (unchanged):**
- Started with the Claude Agent SDK in `~/IsaacOS`, so it has his notes and projects as context.
- Jev picks Haiku / Sonnet / Sonnet high per job (never Opus); the capsule's job card shows which, plus "Step 2 of 5".
- Each tool call becomes a one-line event. Jev decides if it's worth saying; plain rules cap it (gaps, a max per job). Long jobs get a "still going" line every ~25 s.
- At the end Groq sums it up in two spoken sentences. You can talk to her during a job; "stop" cancels it, dropping anything queued too.

**Background (new):**
- Up to **two** run concurrently (a resource limit shared with everything else on the Mac and with Groq's own rate limits — not a hard architectural cap), each with its own tools, its own conversation, its own narration.
- **Priority** — a higher-priority job jumps the queue when several are waiting for a free slot.
- **Dependencies** — a job can be told "wait for job X to finish first"; it sits `blocked` until X reaches a real `done`, then starts. If X fails instead, the dependent job fails too rather than running on bad assumptions.
- **Timeouts** — a job can be given a time budget; overrunning it cancels the job cleanly and marks it `timeout` rather than letting it run forever.
- **Retries** — a failed job can be told to retry itself up to N times, with a backoff pause between attempts.
- **Pause / resume — the genuinely novel part.** Pausing a background job doesn't kill it: it cancels the current turn but keeps the exact same Claude Code session id. Resuming reconnects to **that same session** (the Claude Agent SDK's own `resume`/`continue_conversation`, not a fresh restart), so the model still has everything it already did in memory. This was proved for real this session, not just in a mocked test: a job was paused mid-way through writing a file, and on resume it said *"1 is already appended"* and picked up exactly where it left off, instead of starting over.
- **Voice control** (this session's addition, `evie/job_commands.py`): "pause that job", "pause the background job", "pause job two", "resume it" / "continue that job", "stop that job" — matched by plain code, the same trick as `is_stop()`, so Jev's tuned decision thresholds are never touched. If the reference is ambiguous (two background jobs both running, and you just say "pause that job" with no number), she asks exactly one clarifying question ("Job one, or job two?") rather than guessing. A bare "resume it" only fires if there's exactly one job it could sensibly mean; otherwise it's left alone so it can't accidentally hijack an unrelated "resume the music."
- **What's still missing:** nothing in the app currently *starts* a background job — the mechanism (`start_background()`) is fully built and voice-controllable once a job exists, but there's no voice phrase or UI trigger yet that decides "run this one in the background instead of the usual one-at-a-time foreground queue." That's a real product decision (a new phrase? automatic for certain kinds of requests?) that was deliberately left open rather than guessed at, since it would mean touching Jev's schema.

---

## 8. Persistent state: the World Model (Phase 6, `world_model.py`)

Before Phase 6, restarting the core process meant starting from a blank slate every time — no memory of what app was open, what job was running, or anything else, beyond what lived in a log file. `WorldStore` fixes that, deliberately kept as small and boring as possible:

- It's a **pure subscriber** on the same internal event bus the WebSocket already uses (`events.py` itself was never touched). Every `heard`, `verdict`, `job_started/progress/done`, and `health` event that already flows through the app also gets folded into a small persisted state object.
- What's actually stored: current app / front window / in a call or not, the "active thread" (what's being talked about right now), which jobs are running and the last one that finished, system health per component, and a short capped list of recent event *kinds* (never their content — see §11).
- Calendar, tasks, projects and people are **not copied in** — the world model reads them live through injected functions pointing at their own real stores, so there's still only one place each fact actually lives. (Todoist tasks specifically needed a small extra piece — see §14.)
- It writes itself to `~/Library/Application Support/Evie/world.json` on every change and reloads it on startup, so a core restart (a crash, a `launchctl kickstart`) doesn't lose the picture.
- Exposed read-only at `GET /world` for debugging, and available to the brain as *typed slices* (`brief({"calendar","tasks"})`) rather than one giant blob dumped into a prompt — nothing here is meant to become a model's whole-world context.

---

## 9. Goals & Initiatives (Phase 6, `goals.py`)

A goal is a standing thing ("get the NOI regional qualification," "ship the cricket win predictor v2"), different from a to-do (Todoist) or a calendar event.

- Talked about entirely in plain code, never through Jev: "new goal: X", "how's my goal on X going", "pause/resume my goal on X", "mark the goal on X done", "what are my goals". The trigger regexes deliberately require the literal word "goal" (or "goals") to appear, so they can never hijack an unrelated existing command like "pause the music" or "mark that done" (a to-do).
- A goal you refer to by a short nickname ("the cricket predictor") is matched against the full stored outcome by fuzzy text matching (a mix of sequence similarity and word overlap), not an exact string match.
- Each goal tracks status (active/paused/done/dropped), an optional deadline, milestones, a "next action," and when it was last touched.
- The proactive engine (§13) reads goals too: one that's gone quiet for 10+ days with no progress update gets a gentle re-nudge, the same way an upcoming deadline does.
- A growth cap (300 goals) only ever prunes finished/dropped ones, oldest first — a live goal is never silently deleted just because the list got long.

---

## 10. Procedural memory (Phase 6, `procedures.py`)

The rule Isaac set for this: *never run a memorized plan without checking the current state first* — reuse is an optimization, not a replacement for looking.

- After a screen task (§6) succeeds with a fresh plan, it's recorded. A **second** clean success on a matching request promotes it to a reusable procedure.
- Next time a matching request comes in, `Recipes` tries the saved plan first — but it still runs through the planner's own `expect` checks step by step, exactly like a freshly-planned run would. If the screen doesn't match what the procedure expects, it's treated as a real miss and falls through to normal planning, not blindly forced through.
- Two failures in a row retires a procedure automatically — a task that used to work but no longer does (an app updated its UI) stops being trusted.
- Matching a request to a stored procedure uses the same kind of fuzzy scoring as goals do, tuned so a short version of the same request still finds it.
- A growth cap (200 procedures) keeps this from growing without bound.

---

## 11. Memory, formalized (Phase 6 "Memory V2": `retention.py`, `preferences.py`, plus updates to `facts.py`/`goals.py`)

Isaac's instruction going in was blunt: **never store raw overheard speech.** Phase 6 didn't change what's remembered so much as put a name and some discipline on the five kinds of memory that already existed in different files, and add the plumbing every kind of memory needs so none of them grow forever:

| Kind | Where it lives | What's new in Phase 6 |
|---|---|---|
| Semantic (facts) | `facts.py` | Each fact now carries a source ("isaac" vs. inferred), not just the text. |
| Episodic (today's conversation) | `memory.py` | Unchanged — still RAM/today only, never a raw transcript. |
| Procedural (screen-task plans) | `procedures.py` | New this phase — see §10. |
| Project (goals, calendar, to-dos) | `goals.py`, `calendar_store.py`, `remember.py` | Goals are new; growth caps added. |
| Preference | `preferences.py` (new) | A small key → value store (with a timestamp) for standing preferences, capped at 100 keys. |

`retention.py` is the shared plumbing all of the above lean on: `safe_to_store()` (a last-line check before anything is written), `prune_by_age()`, and `cap_count()`. The `recent_events` list inside the world model (§8) is the clearest example of the discipline this enforces: it only ever stores `{"kind", "timestamp"}` for each event — structurally, not by convention, so even a bug elsewhere can't leak raw speech into it.

---

## 12. Self-monitoring and recovery (Phase 6, `health.py`)

Isaac's rule here: *failures surface through the narration/UI that already exists, never silently.* So this doesn't invent new health checks — it rides the pings that were already happening every 20 seconds to keep the Jev/Groq/Whisper connections warm, plus Todoist's own refresh (§14):

- `HealthMonitor.record()` tracks, per component: ok/fail, how many **consecutive** failures, and a rolling p50 latency.
- A component only flips to "degraded" after **3 consecutive** failures (not the first blip — that would just be noise), and back to "recovered" the moment it succeeds again. Both are one-shot events, published on the bus only on the actual transition, so it doesn't spam a health line every 20 seconds.
- The world model (§8) already subscribes to that same bus, so `system_health` in `/world` updates itself automatically — no new wiring needed on that side.
- Separately, it watches running jobs: one with no new event for 5 minutes is flagged as possibly stuck.
- Exposed at `GET /health`.
- Verified for real this session, not just in a unit test: fed it a deliberately invalid Todoist key, watched the `todoist` component cross the degraded threshold live (3 consecutive real failures), then restored the real key and confirmed it recovered cleanly.

---

## 13. Vision fallback — built and wired (`computer/planner.py`: `_look_for`, `_look_at`)

Isaac's own example for when this should exist: *"he's on a PDF, Accessibility gives nothing."* Genuinely built now, not a no-op: the Swift side's `marked_shot` op takes a real screenshot of the app's window (Screen Recording permission required), and Groq's `qwen3.8-27b` (`GroqClient.look()`) reads it. Two distinct capabilities, matching Computer Use V2's architecture spec §4:

- **Level 3 — find a pressable element** (`_look_for`, `planner.py`): when structured reading (Accessibility/DOM) has too little to work with — fewer than 5 labelled elements on a native app screen, or a `find` step exhausted its normal candidate matching — a screenshot gets a numbered red box drawn over every element Accessibility DID report, and Qwen says which number matches what Evie's looking for. The number maps straight back to a real, pressable element id.
- **Level 2 — answer a specific visual question** (`_look_at`, `planner.py`): for a `read`/question-shaped goal ("what does this diagram show", "where is the play button", "what does question 7 say") where structured text came back empty or unhelpful, Evie asks Qwen that EXACT question against the same screenshot and gets a free-text answer back — no numbered boxes, no element selection, since nothing is being pressed. Reuses the identical `marked_shot` screenshot infrastructure Level 3 already has.

Both share one per-task budget (`self._vision_calls`, max 2 cloud-vision calls per task — Level 2 and Level 3 combined, not 2+2) and log every call with which level fired and why (`"computer vision call N/2 [level 2/3]: ..."`). Structured perception is always tried first (`computer/perception.py`'s `choose_source` routing); vision is a deliberate, logged escalation, never the default. If both levels genuinely can't resolve it, the failure flows through the normal `recovery.py` classification (`VISUAL_ONLY`) and replan loop like any other failure — eventually reaching Claude Code's stuck-task handoff if nothing else works.

---

## 14. Todoist inside the World Model (this session's fix, `remember.py`)

A small but real gap after the first Phase 6 pass: `world.tasks` was always empty, because Todoist's client (`Todoist.list()`) is async, but `WorldStore.snapshot()` is a plain synchronous method (it backs a debug HTTP route and gets called from ordinary code, not from inside an `async def`).

The fix is a small cache, `TodoistCache`, that:
- Refreshes itself on the exact same 20-second background tick already used for the Jev/Groq/Whisper keep-warm pings (§12) — no new timer, no new loop.
- Hands the world model a plain, already-fetched view: the task list, when it was last successfully fetched, whether it's "stale" (old), and whether Todoist is currently "available" at all.
- On a real failure, keeps serving the last good list rather than going blank — Todoist stays the single source of truth, this is just a memory of its last good answer.
- A genuine failure (bad key, unreachable) is reported to `health_tick` (§12) so it shows up as component health like anything else; simply never having a Todoist key configured at all is treated as "off," not a fault, so it doesn't spam a permanent false alarm.

---

## 15. Proactive (she brings things up)

Sources: a class/meeting in 15 min, what's due today (after school and evening), deadlines in 2 days, the morning brief (first activity after 5 am), a finished job while he was busy, a plan she overheard ("dentist Wednesday" → "What time?"), being stuck on the same error for 10 minutes, "pick up where you left off?" after a 20+ minute break, and — new in Phase 6 — a **goal** that's gone quiet for 10+ days (§9).

When: code gates first (he's present, not talking, nobody spoke for a minute, not in a call or class or at dinner, 5 minutes since her last one, max 3 an hour), then one Jev "good moment?". In class they become silent chips on the capsule. Chips expire (task chips after 2 hours) and a chip's Yes only counts after it has been on screen 0.6 s (so a click meant for the capsule can't start a job). Overheard sentences are never stored as text, only the extracted facts.

---

## 16. Text mode, the capsule, the menu

- **Text mode:** automatic when his calendar says class/school/exam, or ⌃⌥T. Nothing is synthesised; the words appear on the card with a type box; the open mic pauses.
- **The capsule:** a small glass pill on a screen edge with one line (her state: listening bars, thinking dash, working ring). It grows into one glass card for replies, lists, "Which one?" rows, follow-ups (Yes / Later / No), a Cancel bar, or the job with Stop. Drag it and it snaps to an edge.
- **Theme:** Auto (follows the Mac's light/dark), Light, Dark, in the right-click menu.
- **Right-click menu:** how she answers (by calendar / out loud / text only), mic mode (Live / Shadow / Off), what she brings up, Theme, show her work, record for tuning, hide, quit.
- **Crash-proofing:** AppKit owns every mouse event and every window size; SwiftUI only draws (it never receives a click). One layout function decides both what's drawn and what's tappable. A 60 s stress test drags and clicks the real panels.
- **Not yet true of Phase 6:** goals, procedural-memory reuse, background jobs and health are all real and voice-controllable, but nothing in the Swift app *shows* them yet (no goals list, no "2 jobs running" indicator, no health dot). It's data + API only on that side so far.

**Shortcuts:** hold left ⌃⌥ = talk. Left ⌃⌥⌘ = Live open mic on/off. ⌃⌥T = text only. Click the capsule = talk (click again to send), or type in text mode. Right Option is a different tool (Ripple), untouched.

---

## 17. Files (where things live)

| Path | What |
|---|---|
| `src/evie/server.py` | builds everything and serves the HTTP/WebSocket API |
| `src/evie/brain.py` | one sentence in: plain-code short-circuits, decide, act, pending questions, follow-ups, lists |
| `src/evie/switchboard/` | Jev questions, the state text, the policy (act/ask/ignore) |
| `src/evie/jev.py`, `talk.py`, `stt.py`, `voice.py` | the model clients: Jev, Groq words, Whisper, Pocket TTS + the speech queue |
| `src/evie/open_mic.py`, `ears.py`, `voiceid.py` | open mic: sentences, voice ID, echo guard |
| `src/evie/skills/` | fast skills (music, system, timers, events, tasks, undo) |
| `src/evie/computer/` | the hands: world, app cards, find, planner, recipes, messages, vision fallback |
| `src/evie/jobs.py`, `job_commands.py`, `narrator.py` | the job supervisor (foreground + background, pause/resume, dependencies, timeouts, retries), its voice control, narration |
| `src/evie/world_model.py` | persistent world state: subscribes to the event bus, survives a restart, `/world` |
| `src/evie/goals.py` | goals/initiatives: plain-code commands + fuzzy matching |
| `src/evie/procedures.py` | procedural memory for screen tasks |
| `src/evie/health.py` | component health + stuck-job detection, `/health` |
| `src/evie/retention.py`, `preferences.py` | shared memory-growth discipline, the preference store |
| `src/evie/proactive/` | the follow-up queue, the engine (when), the sources (what — now including goal nudges) |
| `src/evie/remember.py`, `memory.py`, `context_packs.py`, `calendar_store.py`, `quiet.py` | remembering, today's conversation, knowledge packs, the calendar, text mode, the Todoist cache |
| `mac/EvieBar/Sources/EvieBar/` | the Swift app: `AppModel` (state), `Orb` (capsule + card), `Ears`, `Eyes`, `Hands`, `PushToTalk`, `SelfTest` |
| `evals/` | eval sets and runners (switchboard, hands, tiers, answers, narration, proactive, ears), recorded model answers in `cassettes/` |
| `tests/` | 926 offline tests |
| `docs/HOW-IT-WORKS.md` | the detailed build log, phase by phase |
| `~/Library/Application Support/Evie/` | voiceprint, `people.json` (dad → Dada, mom → Mamma), `vocab.json` (learned names), follow-ups, goals, procedures, preferences, world state, recordings (only when "Record for tuning" is on) |

---

## 18. Logs and how to debug

Logs are in `~/Library/Logs/Evie/`:

- `core.log`: everything the core does (errors, timings, rate limits).
- `turns.jsonl`: one line per sentence: what she heard, Jev's decision, what she did and said, timings.
- `actions.jsonl`: every skill action and its check.
- `speech.jsonl`: her speech timeline (catches two voices at once; `"ev":"text"` = shown instead of spoken).
- `app.log`: the Swift app.

Handy checks:

- Is it alive? `curl -s localhost:8765/status` (Jev, Whisper, voice ready, mic mode). Exactly one core: `pgrep -f evie/.venv/bin/evie-core`.
- What's on screen, as she sees it: `curl -s -X POST localhost:8765/debug/do -H 'content-type: application/json' -d '{"op":"world"}'`.
- Her persistent picture of the world (Phase 6): `curl -s localhost:8765/world`. Component health: `curl -s localhost:8765/health`.
- Calendar she sees: `curl -s localhost:8765/debug/calendar`. Follow-ups waiting: `curl -s localhost:8765/followups`.
- Open mic health: `curl -s localhost:8765/ears/stats`.
- App self-test (62 checks): `~/Applications/Evie.app/Contents/MacOS/EvieBar --selftest`. Stress: `--orbstress 60`.
- Tests: `uv run pytest`. Evals: `uv run python -m evals.run --split all --label x` (switchboard), `evals.run_computer` (35 hands tasks on a pretend Mac), `evals.run_tiers`, `evals.run_answers`, `evals.run_narration`, `evals.run_proactive`, `evals.run_ears`.
- Restart the core: `launchctl kickstart -k gui/$(id -u)/com.isaac.evie.core`. Rebuild the app: `cd mac && ./build.sh`.

How bugs get fixed: reproduce first (logs, a replayed turn, or a failing test), then the smallest fix, test first. Evals replay recorded model answers so they're free and repeatable; anything reworded goes live automatically. Phase 6 close-out found three bugs this way that the offline suite structurally couldn't: a session id format the real Claude CLI rejected, a phrasing gap in goal-status parsing, and a health check that could never actually detect a real Todoist outage — all three only showed up once tested against the real Mac and real APIs, not fakes standing in for them.

---

## 19. Current numbers (2026-09-26)

| What | Result |
|---|---|
| Switchboard eval (~190 labelled sentences) | false actions **0**, route accuracy 0.968, 0 flips vs. previous run |
| Hands eval (35 tasks on a pretend Mac) | 34/35 (one known flake), unsafe 0, median 2 model calls |
| Job model picks (15 goals) | 15/15, Opus 0 |
| Answers to her own questions (12) | 12/12 |
| Narration eval | 0.92 |
| Simulated proactive day | pass (9 spoken, 3 chips, max 3 an hour, 0 in class/call/conversation) |
| Tests | 926 offline (127 → 129 of which are Phase 6) + 62 app self-checks |
| Core RAM | ~930 MB, one process |
| Key release → first sound | ~1 s typical |

---

## 20. Known gaps (honest list)

- **No trigger to *start* a background job yet.** The supervisor (concurrency, pause/resume, dependencies, timeouts, retries) is complete and voice-controllable once a job exists — nothing decides *when* a request should become a background job instead of the usual one-at-a-time foreground queue. A real product/UX decision, not a bug.
- ~~**Vision fallback has no real implementation.**~~ Built (Computer Use V2, `computer-use-v2-phase2` branch): `marked_shot` (Swift, real screenshot capture) + Qwen (`GroqClient.look()`) power both Level 3 (`_look_for`, find a pressable element) and Level 2 (`_look_at`, answer a specific visual question) — see §13.
- **No Swift UI for goals, procedures, health, or background jobs.** All data + API only right now — voice works, nothing renders on the capsule yet.
- **Live mic vs talk key.** Both use the same mic and echo canceller. The measured Live problems were mostly addressing (answers ignored) and names, and both are fixed. But Apple's echo canceller zeroes short stretches of audio (up to 28% of some sentences), which can clip soft word starts.
- **Screen control still meets new apps it has no card for.** It works from the Accessibility tree, but some apps expose little; then it falls back to a screenshot or Claude Code.
- **Groq per-minute limits.** gpt-oss-120b and Qwen have per-minute token caps; heavy bursts fall back to smaller models or wait.
- **If Jev can't be reached, she does nothing** (by design: no decision, no action).
- **The stuck-on-an-error detector** reads Terminal; VS Code's terminal usually hides its text from Accessibility.
- **Classroom calendars:** only some sync to the Mac, so deadlines lean on Todoist.
- **Needs Isaac for live checks:** anything that plays music, drives his screen, sends a message or writes his calendar is only tested on a pretend Mac (or, for the job supervisor and health monitor, verified directly against real APIs from the terminal) until he tries it hands-on.
