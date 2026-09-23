""""I did the bio email": tick it off Todoist. Jev picks the task from Isaac's real open tasks
(due today or overdue), so it can't tick off something that isn't there. Undo puts it back."""
from evie.jev import JevError
from evie.skills.catalog import Done


class TaskSkills:
    def __init__(self, todoist, jev, undo):
        self._todoist, self._jev, self._undo = todoist, jev, undo

    async def done(self, text: str) -> Done:
        tasks = (await self._todoist.list("today | overdue"))[:100]
        if not tasks:
            return Done("Nothing's due on your Todoist right now.", ok=False, detail="empty")
        options = {t.id: t.content + (f" (due {t.due})" if t.due else "") for t in tasks}
        options["none"] = "None of these tasks"
        q = {"task": {"type": "choice", "instructions": "Which of Isaac's to-dos has he just finished?",
                      "criteria": options}}
        try:
            a = (await self._jev.ask(f'Isaac said: "{text}"', q)).answers["task"]
        except (JevError, KeyError, TypeError):
            return Done("Which task?", ok=False, detail="jev unavailable")
        choice = a.get("choice")
        if choice == "none" or choice not in options:
            return Done("That's not on your Todoist for today.", ok=False, detail="no match")
        if float(a.get("confidence", 0)) < 0.5:
            return Done("Which task?", ok=False, detail="unsure")
        task = next(t for t in tasks if t.id == choice)
        if not await self._todoist.close(task.id):
            return Done("Todoist wouldn't tick it off.", ok=False, detail="close failed")

        async def undo() -> str:
            ok = await self._todoist.reopen(task.id)
            return f"Put {task.content} back on your list." if ok else "Todoist wouldn't put it back."

        self._undo.remember_undo(undo)
        return Done(f"Ticked off: {task.content}.", verified=True)
