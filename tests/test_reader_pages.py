"""Reader v2 on saved pages, run in the app's own hidden WebKit view (`EvieBar --webtest`): the
same script Safari runs. Skipped when the app isn't built."""
import json
import subprocess
from pathlib import Path

import pytest

APP = Path.home() / "Applications/Evie.app/Contents/MacOS/EvieBar"
PAGES = Path(__file__).parent.parent / "evals/computer/pages"
pytestmark = pytest.mark.skipif(not APP.exists(), reason="Evie.app not built")


def read(page: str) -> dict:
    out = subprocess.run([str(APP), "--webtest", str(PAGES / page)], capture_output=True, text=True, timeout=30).stdout
    return json.loads(out.strip().splitlines()[-1])


def test_videos_carry_their_channel_card_details_and_duplicates_are_listed_once():
    els = read("youtube_channel_videos.html")["elements"]
    vids = [e for e in els if "/watch?v=" in e["href"] and e["label"] != "thumbnail"]
    labels = [v["label"] for v in vids]
    assert labels.count("you need to learn Linux RIGHT NOW!!") == 1
    first = next(v for v in vids if v["href"].endswith("n1"))
    assert "2 days ago" in first["meta"] and first["group"]
    assert next(e for e in els if e["label"] == "Videos").get("selected") is True


def test_news_headlines_come_with_their_teaser():
    els = read("bbc_home.html")["elements"]
    ai = next(e for e in els if e["label"].startswith("AI model beats doctors"))
    assert "1 hr ago" in ai["meta"] or "10,000 cases" in ai["meta"]
    assert ai["region"] == "main"
