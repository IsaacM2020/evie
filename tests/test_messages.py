import json

from evie.computer.messages import Messages
from evie.countdown import Countdown
from evie.hands import HandsResult
from evie.jev import JevResult


class FakeHands:
    def __init__(self, contacts):
        self.contacts, self.calls = contacts, []

    async def do(self, op, timeout=5.0, **args):
        self.calls.append((op, args))
        if op == "contacts_find":
            return HandsResult(True, "", {"contacts": json.dumps(self.contacts)})
        if op == "observe":
            return HandsResult(True, "", {"snapshot": "s9", "app": "WhatsApp", "kind": "app", "elements": json.dumps(
                [{"id": "a1", "role": "textarea", "label": "Type a message", "value": "on my way"},
                 {"id": "a2", "role": "button", "label": "Send"}])})
        return HandsResult(True, "ok", {})


class FakeJev:
    def __init__(self, choice):
        self.choice, self.options = choice, None

    async def ask(self, state, q):
        self.options = q["contact"]["criteria"]
        return JevResult({"contact": {"choice": self.choice, "confidence": 0.9}}, 1.0, 0.0)


MOM = {"name": "Mom", "phones": ["+65 9123 4567"]}
MRTAN = {"name": "Mr Tan", "phones": ["+65 8000 1111"]}


def msgs(contacts, choice="0", said=None):
    said = said if said is not None else []
    return Messages(FakeHands(contacts), FakeJev(choice), Countdown(seconds=0.02), say=said.append, window_s=0.02), said


async def test_whatsapp_reads_back_waits_then_sends_to_the_real_number():
    m, said = msgs([MOM])
    r = await m.send("whatsapp mom on my way", {"contact": "mom", "body": "on my way", "via": "whatsapp"})
    ops = [(op, a) for op, a in m._hands.calls]
    assert said == ["Sending 'on my way' to Mom on WhatsApp. Say stop to cancel."]
    assert ("open_url", {"url": "whatsapp://send?phone=6591234567&text=on%20my%20way", "app": "WhatsApp", "front": True}) in ops
    assert ("press", {"id": "a2", "snapshot": "s9"}) in ops
    assert r.ok and r.said == "Sent."


async def test_imessage_goes_through_messages():
    m, _ = msgs([MOM])
    r = await m.send("text mom on my way", {"contact": "mom", "body": "on my way", "via": "imessage"})
    assert ("imessage_send", {"to": "+6591234567", "text": "on my way"}) in m._hands.calls and r.ok


async def test_unknown_contact_is_said_plainly():
    m, _ = msgs([])
    r = await m.send("message bob hi", {"contact": "bob", "body": "hi", "via": "whatsapp"})
    assert not r.ok and r.said == "I can't find bob in your contacts."


async def test_two_matches_let_jev_pick_from_the_real_ones():
    m, _ = msgs([MOM, MRTAN], choice="1")
    await m.send("text mr tan i'm late", {"contact": "tan", "body": "I'm late", "via": "imessage"})
    assert m._jev.options == {"0": "Mom (+65 9123 4567)", "1": "Mr Tan (+65 8000 1111)", "none": "None of these"}
    assert ("imessage_send", {"to": "+6580001111", "text": "I'm late"}) in m._hands.calls


async def test_no_message_body_asks():
    m, _ = msgs([MOM])
    r = await m.send("message mom", {"contact": "mom", "body": None, "via": "whatsapp"})
    assert r.ask and r.said == "What should I say to Mom?"


async def test_stop_means_nothing_is_sent():
    cd = Countdown(seconds=0.3)
    said = []
    m = Messages(FakeHands([MOM]), FakeJev("0"), cd, say=said.append, window_s=0.3)
    import asyncio
    t = asyncio.create_task(m.send("text mom hi", {"contact": "mom", "body": "hi", "via": "imessage"}))
    await asyncio.sleep(0.1)
    cd.cancel()
    r = await t
    assert r.said == "Okay, not sent." and not any(op == "imessage_send" for op, _ in m._hands.calls)
