"""The questions Jev answers about every sentence. All asked in ONE call, answered in parallel.

Wording matters more than anything else here. It gets tuned against evals/cases.jsonl,
and every change is logged in evals/TUNING.md.
"""

ROUTES = {
    "not_for_evie": "Not meant for Evie: Isaac talking to another person, a class or a call, "
                    "or speech from someone else or a video",
    "quick_action": "One quick thing on the computer: play, pause or skip music, change volume, "
                    "open an app or website, set a timer, send a short message",
    "answer": "A question Evie answers by talking: the time, the date, maths, a fact, "
              "what is on Isaac's calendar",
    "deep_job": "Real work that takes minutes: fixing or writing code, researching a topic, "
                "editing files or documents, multi-step tasks, or checking on one of Isaac's own "
                "projects, websites or files",
    "job_control": "About a task Evie is already working on: asking how it is going, stopping it, "
                   "or adding an instruction to it. Only possible while Evie is working on something; "
                   "then a follow-up that starts with 'also' or 'and' adds to that task",
    "remember": "Isaac wants something remembered, scheduled, or added as a task or reminder",
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
    "volume": "Change the volume: louder, quieter, a number, mute or unmute",
    "open_app": "Open or switch to an app on the Mac",
    "open_website": "Open a website, or search a site like YouTube or Google",
    "timer_set": "Start a timer or countdown",
    "timer_cancel": "Cancel or stop a timer",
    "undo": "Undo or reverse the last thing Evie did",
    "other": "Anything else, for example sending a message, changing a setting, or a multi-step "
             "task on the computer",
}

REMEMBER_TO = {
    "task": "A to-do or reminder: something Isaac has to do, with or without a due date",
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
                        "without asking a follow-up question? 'play that song' is not complete (which "
                        "song?). 'remember I have the dentist on wednesday' is not complete (what "
                        "time?). 'pause the music', 'what time is it' and 'play some lofi' are complete. "
                        "Real work that names what to work on is complete, because Evie figures out "
                        "the rest herself: 'fix the chase bug in my cricket model' and 'check why the "
                        "tests fail in the anchor repo' are complete, but 'fix it' is not.",
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
