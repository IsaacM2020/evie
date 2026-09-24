import asyncio

from evie.countdown import Countdown, Countdowns


async def test_a_send_window_never_cancels_a_pending_delete():
    """3.5 deferred minor: one shared countdown meant a 3b send's window cancelled a pending delete."""
    deletes, sends = Countdown(seconds=0.2), Countdown(seconds=0.05)
    done = []

    async def delete():
        done.append("deleted")

    deletes.start(delete)
    assert await sends.wait() is True
    await asyncio.sleep(0.25)
    assert done == ["deleted"]


async def test_stop_calls_off_whichever_is_waiting():
    deletes, sends = Countdown(seconds=0.3), Countdown(seconds=0.3)
    both = Countdowns(deletes, sends)
    waiting = asyncio.create_task(sends.wait())
    await asyncio.sleep(0.01)
    assert both.pending and both.cancel() is True
    assert await waiting is False and not both.pending
    assert both.cancel() is False
