import json

import numpy as np

from evals.metrics import wer
from evie.recorder import SegmentRecorder


class Clock:
    t = 1_000_000.0

    def __call__(self):
        return self.t


def test_off_by_default_saves_nothing(tmp_path):
    r = SegmentRecorder(tmp_path)
    assert r.enabled is False
    assert r.save(np.zeros(16000, dtype=np.float32), "isaac", 0.8, "hi", evie_speaking=False) is None
    assert list(tmp_path.glob("*.wav")) == []


def test_saves_isaac_segment_with_its_facts(tmp_path):
    r = SegmentRecorder(tmp_path, clock=Clock())
    r.set(True)
    name = r.save(np.zeros(16000, dtype=np.float32), "isaac", 0.81, "pause the music", evie_speaking=True)
    assert (tmp_path / f"{name}.wav").exists()
    row = json.loads((tmp_path / "segments.jsonl").read_text().splitlines()[0])
    assert row == {"name": name, "t": 1_000_000.0, "speaker": "isaac", "sim": 0.81, "text": "pause the music",
                   "evie_speaking": True, "seconds": 1.0, "source": "live"}


def test_never_saves_other_people(tmp_path):
    r = SegmentRecorder(tmp_path)
    r.set(True)
    assert r.save(np.zeros(16000, dtype=np.float32), "other", 0.1, "", evie_speaking=False) is None
    assert list(tmp_path.glob("*.wav")) == []


def test_switch_survives_restart(tmp_path):
    SegmentRecorder(tmp_path).set(True)
    assert SegmentRecorder(tmp_path).enabled is True


def test_prune_deletes_after_seven_days(tmp_path):
    c = Clock()
    r = SegmentRecorder(tmp_path, clock=c)
    r.set(True)
    old = r.save(np.zeros(8000, dtype=np.float32), "isaac", 0.8, "old", evie_speaking=False)
    c.t += 8 * 86400
    new = r.save(np.zeros(8000, dtype=np.float32), "isaac", 0.8, "new", evie_speaking=False)
    r.prune()
    assert not (tmp_path / f"{old}.wav").exists()
    assert (tmp_path / f"{new}.wav").exists()
    names = [json.loads(l)["name"] for l in (tmp_path / "segments.jsonl").read_text().splitlines()]
    assert names == [new]


def test_wer_counts_word_edits():
    assert wer("pause the music", "pause the music") == 0.0
    assert wer("pause the music", "pause music") == 1 / 3
    assert wer("Remind me at 5 p.m.", "remind me at 5pm") == 0.0  # case, punctuation, p.m. ignored
    assert wer("set a timer", "") == 1.0
    assert wer("my code is four one two nine", "my code is 4129") == 0.0
    assert wer("question six", "question 6") == 0.0


def test_talk_key_clips_are_kept_and_marked(tmp_path):
    """Isaac, 2026-09-24: the talk key hears him well, Live doesn't. Keeping both kinds lets
    evals/replay.py compare them on his real voice."""
    r = SegmentRecorder(tmp_path)
    r.set(True)
    r.save(np.zeros(16000, dtype=np.float32), "isaac", 1.0, "open a video by Parrot", False, source="ptt")
    r.save(np.zeros(16000, dtype=np.float32), "isaac", 0.8, "open a video by Barrett", False)
    assert [row["source"] for row in r.rows()] == ["ptt", "live"]
