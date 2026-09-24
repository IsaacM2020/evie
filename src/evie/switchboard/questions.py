"""The questions Jev answers about every sentence. All asked in ONE call, answered in parallel.

Wording matters more than anything else here. It gets tuned against evals/cases.jsonl,
and every change is logged in evals/TUNING.md.
"""

ROUTES = {
    "not_for_evie": "Not meant for Evie: Isaac talking to another person, a class or a call, "
                    "or speech from someone else or a video",
    "quick_action": "One quick thing on the computer: play, pause or skip music, ask what song is "
                    "playing, change volume, open an app or website, set or cancel a timer, undo the "
                    "last thing Evie did, move or delete a calendar event, tick off a to-do Isaac says he "
                 "finished, send a short message, or do something in an app (draft an email, play a video, "
                 "turn on Wi-Fi)",
    "answer": "A question Evie answers by talking: the time, the date, maths, a fact, "
              "what is on Isaac's calendar, or summarising or explaining the text on his screen ('summarise this')",
    "deep_job": "Real work that takes minutes: fixing or writing code, researching a topic, "
                "editing files or documents, multi-step tasks, or checking on one of Isaac's own "
                "projects, websites or files",
    "job_control": "About a task Evie is already working on: asking how it is going, stopping it, "
                   "or adding an instruction to it. Only possible while Evie is working on something; "
                   "then a follow-up that starts with 'also' or 'and' adds to that task",
    "remember": "Isaac wants something NEW remembered, scheduled, reminded, or added as a task or to-do "
                "(\"add a task\", \"put X on my list\", \"remind me in 20 minutes to...\"). Not for "
                "ticking off something he already finished, and not for making a note or document in an app",
}

# Which fast skill a quick_action needs. Asked in the SAME Jev call as everything else (answered
# in parallel, so it adds no time). Anything not listed is "other": Claude Code does it.
SKILLS = {
    "music_play": "Play a specific song, artist, album, playlist or kind of music",
    "music_pause": "Pause or stop the music",
    "music_resume": "Resume or unpause the music that was playing",
    "music_next": "Skip to the next song",
    "music_previous": "Go back to the previous song or restart it",
    "now_playing": "Ask what song is playing",
    "volume": "Change how loud the Mac is: louder, quieter, a number, mute or unmute (a bare "
              "'mute' means the sound)",
    "open_app": "Open or switch to an app on the Mac, and nothing more (not doing something inside it)",
    "open_website": "Open a website by name, and nothing more",
    "timer_set": "Start a timer or countdown",
    "timer_cancel": "Cancel or stop a timer",
    "undo": "Undo or reverse the last thing Evie did",
    "event_move": "Move or reschedule something already on Isaac's calendar to another time or day",
    "event_delete": "Delete, remove or cancel something already on Isaac's calendar",
    "task_done": "Isaac says he finished or did one of his to-dos, or asks to tick one off",
    "computer": "Do something inside an app or on a web page, even in several steps: play a video on YouTube, "
                "open a new tab and search, open or summarise an article, click or type something, fill in a form, "
                "turn Wi-Fi or dark mode on or off, work in Finder, Notes, Mail or Notion",
    "message_send": "Send a WhatsApp message, a text or an iMessage to someone",
    "other": "Anything else, like a long job with files or code",
}

REMEMBER_TO = {
    "task": "A to-do: something Isaac has to do, with or without a due date",
    "reminder": "Isaac wants to be reminded or nudged at a certain time or after a while (\"remind me in "
                "20 minutes to...\", \"remind me at 5 to...\", a timer that should say what it's for)",
    "event": "Something happening at a specific time or day: an appointment, class, match, test or meeting",
    "fact": "A fact about Isaac or his life to keep in mind, with nothing to do and no time attached, "
            "like a locker code or a friend's birthday",
}

QUESTIONS = {
    "for_evie": {
        "type": "noul",
        "instructions": "Is the latest speech Isaac talking directly to his assistant Evie, asking the "
                        "computer to do or answer something? Answer no if he is talking to another "
                        "person, to a class or call, or if the speech is from someone else or a video. "
                        "When Isaac starts with Evie's name and then gives a command, it is for Evie, "
                        "even during a call. Just mentioning Evie while talking about her is not.",
    },
    "route": {
        "type": "choice",
        "instructions": "What kind of request is the latest speech?",
        "criteria": ROUTES,
    },
    "complete": {
        "type": "noul",
        "instructions": "Assuming the latest speech is a request to Evie, could she do it right now "
                        "without asking a follow-up question? Use the speech just before: 'play that song' "
                        "is complete if a song was just mentioned, and not complete if nothing says which "
                        "song. A song title alone is complete ('play trance': she plays the most popular "
                        "match). A creator or site without a specific item is complete ('play a mrbeast "
                        "video': she opens his videos and asks there). "
                        "'remember I have the dentist on wednesday' is not complete (what "
                        "time?), but 'I have the dentist on wednesday at 4' and 'vedant's birthday is on sunday' are "
                        "complete. Quick controls are complete as they are: 'pause', 'resume the music', "
                        "'skip this song', 'turn it up', 'undo that', 'cancel the timer', 'what song is "
                        "this', 'what time is it' and 'play some lofi'. "
                        "Real work that names what to work on is complete, because Evie figures out "
                        "the rest herself: 'fix the chase bug in my cricket model', 'check why the "
                        "tests fail in the anchor repo' and 'how's the igem website looking' are "
                        "complete, but 'fix it' is not.",
    },
    "has_event": {
        "type": "noul",
        "instructions": "Does the latest speech mention a specific appointment, deadline, test, "
                        "commitment or task that Isaac has or promised, for example a dentist visit, "
                        "a quiz on Monday, or 'I will send it tonight'?",
    },
    "skill": {
        "type": "choice",
        "instructions": "If the latest speech asks Evie to do one quick thing on the computer, which one?",
        "criteria": SKILLS,
    },
    "remember_to": {
        "type": "choice",
        "instructions": "If Isaac wants something remembered, scheduled, or added as a task, where does it go?",
        "criteria": REMEMBER_TO,
    },
}


# Which knowledge the answer needs (evie/context_packs.py). Asked in the same call, in parallel.
PACK_QUESTIONS = {
    "need_calendar": "Would answering or doing this need Isaac's calendar (his schedule, classes, events, free time)?",
    "need_tasks": "Would answering this need Isaac's to-do list (tasks, homework, what's due)?",
    "need_projects": "Is Isaac asking about his own projects, notes, goals or subjects, or what he has been working "
                     "on? No for his to-do list, his calendar, the time, general knowledge, maths and small talk.",
    "need_screen": "Is this about what's on Isaac's screen right now (this page, this tab, this video, this email)?",
    "need_web": "Can this ONLY be answered with fresh information from the internet that changes over time "
                "(news, sports results, weather, prices, recent events)? No for the time or date, Isaac's own "
                "schedule, tasks or projects, maths, and general knowledge that doesn't change.",
    "hard_question": "Would a good answer need careful reasoning: a multi-step maths or physics problem, an "
                     "explanation of how or why something works, or advice weighing trade-offs? Simple facts, "
                     "the time and small talk are not hard.",
    # Kept short on purpose: Jev's time grows with the question set (2026-09-24: 14 questions and a
    # long wording took the median from ~340 to ~740 ms). long_job left the set for this: every deep
    # job now starts with a read-back instead of "this might take a minute".
    "multi_request": "Does Isaac ask for two or more separate things at once (like 'pause the music and open "
                     "WhatsApp')? One request with details is not two.",
}
for _k, _v in PACK_QUESTIONS.items():
    QUESTIONS[_k] = {"type": "noul", "instructions": _v}
