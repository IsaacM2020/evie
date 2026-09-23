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
                "editing files or documents, multi-step tasks",
    "job_control": "About a task Evie is already working on: asking how it is going, stopping it, "
                   "or adding an instruction to it",
    "remember": "Isaac wants something remembered, scheduled, or added as a task or reminder",
}

QUESTIONS = {
    "for_evie": {
        "type": "noul",
        "instructions": "Is the latest speech Isaac talking directly to his assistant Evie, asking the "
                        "computer to do or answer something? Answer no if he is talking to another "
                        "person, to a class or call, or if the speech is from someone else or a video.",
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
                        "time?). 'pause the music', 'what time is it' and 'play some lofi' are complete.",
    },
    "has_event": {
        "type": "noul",
        "instructions": "Does the latest speech mention a specific appointment, deadline, test, "
                        "commitment or task that Isaac has or promised, for example a dentist visit, "
                        "a quiz on Monday, or 'I will send it tonight'?",
    },
}
