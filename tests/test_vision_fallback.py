from evie.computer.observe import Screen
from evie.computer.vision_fallback import VisionFallback, needs_visual_fallback
from evie.hands import HandsResult


def screen_with(elements):
    return Screen.from_data({"snapshot": "s1", "app": "Preview", "kind": "app", "url": "", "window": "a.pdf",
                             "elements": __import__("json").dumps(elements)})


def test_true_when_structured_read_found_nothing():
    assert needs_visual_fallback(screen_with([])) is True


def test_false_when_structured_read_found_something():
    assert needs_visual_fallback(screen_with([{"id": "w1", "role": "button", "label": "Print"}])) is False


def test_false_when_never_observed_yet():
    assert needs_visual_fallback(None) is False


class FakeHands:
    def __init__(self, result=None, raises=None):
        self.result, self.raises = result, raises
        self.calls = []

    async def do(self, op, timeout=5.0, **args):
        self.calls.append((op, args))
        if self.raises:
            raise self.raises
        return self.result


async def test_describe_none_when_hands_raises():
    v = VisionFallback(FakeHands(raises=RuntimeError("app not reachable")))
    assert await v.describe("Preview") is None


async def test_describe_none_when_screenshot_not_ok():
    v = VisionFallback(FakeHands(result=HandsResult(False, "Screen Recording not granted", {})))
    assert await v.describe("Preview") is None


async def test_describe_none_when_no_vision_model_is_wired():
    v = VisionFallback(FakeHands(result=HandsResult(True, "", {"image": "base64..."})), describe_image=None)
    assert await v.describe("Preview") is None


async def test_describe_calls_the_vision_model_once_wired():
    calls = []

    async def fake_vision(image):
        calls.append(image)
        return "A PDF titled Chem IA draft, a Print button top right."

    v = VisionFallback(FakeHands(result=HandsResult(True, "", {"image": "b64data"})), describe_image=fake_vision)
    out = await v.describe("Preview")
    assert out == "A PDF titled Chem IA draft, a Print button top right." and calls == ["b64data"]


async def test_describe_none_when_the_vision_model_call_fails():
    async def boom(image):
        raise RuntimeError("model unavailable")

    v = VisionFallback(FakeHands(result=HandsResult(True, "", {"image": "b64data"})), describe_image=boom)
    assert await v.describe("Preview") is None


async def test_describe_none_when_screenshot_ok_but_no_image_returned():
    v = VisionFallback(FakeHands(result=HandsResult(True, "", {})), describe_image=lambda i: "shouldn't be called")
    assert await v.describe("Preview") is None


async def test_hands_asked_for_the_right_app():
    hands = FakeHands(result=HandsResult(True, "", {}))
    await VisionFallback(hands).describe("Preview")
    assert hands.calls == [("screenshot", {"app": "Preview"})]
