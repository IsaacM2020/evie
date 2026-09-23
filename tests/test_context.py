from evie.switchboard.context import (
    MAX_RECENT_CHARS, MAX_UTTERANCE_CHARS, Context, is_noise, render_state,
)


def test_state_has_utterance_speaker_and_call():
    s = render_state(Context(utterance="evie pause", speaker="isaac", in_call=True, front_app="Zoom"))
    assert '"evie pause"' in s
    assert "Isaac (voice match)" in s
    assert "online class: yes" in s
    assert "App in front: Zoom" in s
    assert "not working on anything" in s


def test_jobs_and_recent_rendered_and_capped():
    ctx = Context(utterance="hows it going", recent=("one", "two", "three", "four"),
                  active_jobs=("fixing the chase bug",))
    s = render_state(ctx)
    assert "fixing the chase bug" in s
    assert "one" not in s.split("Speech just before:")[1].split("\n")[0]
    assert "four" in s


def test_long_utterance_keeps_tail():
    long = "blah " * 800 + "evie pause the music"
    s = render_state(Context(utterance=long))
    line = [l for l in s.splitlines() if l.startswith("Latest speech")][0]
    quoted = line.rsplit(': "', 1)[1][:-1]  # the utterance is the last quoted thing on the line
    assert len(quoted) <= MAX_UTTERANCE_CHARS
    assert quoted.endswith("evie pause the music")


def test_recent_lines_clipped():
    s = render_state(Context(utterance="x", recent=("y" * 1000,)))
    before = s.split("Speech just before: ")[1].split("\n")[0]
    assert len(before) <= MAX_RECENT_CHARS


def test_whitespace_collapsed():
    assert '"evie   pause"' not in render_state(Context(utterance="evie   pause"))


def test_noise():
    for junk in ["", "  ", "...", "Thank you for watching!", "you", "Bye."]:
        assert is_noise(junk), junk
    for real in ["pause", "evie play lofi", "thank you evie"]:
        assert not is_noise(real), real
