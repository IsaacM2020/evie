import asyncio

from evie.server import keep_warm


async def test_keep_warm_pings_every_interval_and_survives_errors():
    calls = []

    async def ok():
        calls.append("ok")

    async def boom():
        calls.append("boom")
        raise RuntimeError("x")

    task = asyncio.create_task(keep_warm([ok, boom], interval_s=0.01))
    await asyncio.sleep(0.035)
    task.cancel()
    assert calls.count("ok") >= 2 and calls.count("boom") >= 2
