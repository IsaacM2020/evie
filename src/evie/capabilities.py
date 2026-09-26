"""What Evie can and can't do, in plain words, for every reply and clarifying question.

Without this the model guessed: "I don't have a calendar connected" (she does), "who do you want
to message?" (Isaac only said "open WhatsApp"). Keep it true: update it when a skill ships.
"""
CAN = [
    "answer questions, do exact maths, and know the date and time",
    "read and change Isaac's calendar (his Google calendar and Classroom): see any day, add, move or delete events",
    "add to-dos to Todoist, read what's due, and tick things off",
    "set spoken reminders and timers",
    "remember facts Isaac tells her and use them later",
    "play, pause and skip Spotify music, change the volume, open apps and websites",
    "undo the last thing she did",
    "operate apps and web pages on the Mac: play a YouTube video, open tabs, search, click and type in apps "
    "(Safari first), and send WhatsApp or iMessage messages after reading them back",
    "look at a screenshot when the screen has too little to read structurally, to find something on it",
    "hand bigger work (coding, research, files, anything multi-step on the Mac) to Claude Code in the "
    "background and keep talking while it runs",
]
CANT_YET = [
    "describe or explain what's in an image, photo or diagram (she can only find a numbered "
    "screen element by looking, never describe visual content)",
    "pay for things or log in to accounts",
]


def sheet() -> str:
    return ("What Evie can do: " + "; ".join(CAN) + ". What she can't yet: " + "; ".join(CANT_YET) +
            ". Never claim she can't do something she can.")
