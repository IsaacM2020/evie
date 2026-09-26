from evie.procedures import ProcedureStore


def test_first_success_is_learning_not_yet_reused(tmp_path):
    s = ProcedureStore(path=tmp_path / "p.json")
    proc = s.learn("play a video by mrbeast", [{"do": "open_url", "url": "x"}])
    assert proc.status == "learning" and proc.success_count == 1
    assert s.find("play a video by mrbeast") is None  # not trusted yet


def test_second_success_promotes_it_to_active_and_reusable(tmp_path):
    s = ProcedureStore(path=tmp_path / "p.json")
    s.learn("play a video by mrbeast", [{"do": "open_url", "url": "x"}])
    proc = s.learn("play a video by mrbeast", [{"do": "open_url", "url": "x"}, {"do": "pick"}])
    assert proc.status == "active" and proc.success_count == 2 and proc.version == 2
    found = s.find("play a video by mrbeast")
    assert found is not None and found.id == proc.id
    assert found.steps == [{"do": "open_url", "url": "x"}, {"do": "pick"}]


def test_learn_reinforces_a_fuzzy_matching_existing_record_instead_of_duplicating(tmp_path):
    s = ProcedureStore(path=tmp_path / "p.json")
    s.learn("play a video by mrbeast", [{"do": "x"}])
    s.learn("play a video by MrBeast", [{"do": "x"}])  # near-identical phrasing, different case
    assert len(s.list_procedures()) == 1


def test_unrelated_goal_never_matches(tmp_path):
    s = ProcedureStore(path=tmp_path / "p.json")
    s.learn("play a video by mrbeast", [{"do": "x"}])
    s.learn("play a video by mrbeast", [{"do": "x"}])
    assert s.find("open my email") is None
    assert s.find_any("open my email") is None


def test_record_success_on_an_active_procedure_keeps_it_reusable(tmp_path):
    s = ProcedureStore(path=tmp_path / "p.json")
    proc = s.learn("close netflix", [{"do": "x"}])
    proc = s.learn("close netflix", [{"do": "x"}])
    s.record_success(proc.id)
    got = s.get(proc.id)
    assert got.status == "active" and got.success_count == 3 and got.consecutive_failures == 0


def test_a_single_failure_does_not_retire_a_proven_procedure(tmp_path):
    s = ProcedureStore(path=tmp_path / "p.json")
    proc = s.learn("open spotify", [{"do": "x"}])
    proc = s.learn("open spotify", [{"do": "x"}])
    s.record_failure(proc.id)
    assert s.get(proc.id).status == "active"
    assert s.find("open spotify") is not None  # still trusted after one miss


def test_repeated_failures_retire_it_and_it_stops_being_offered(tmp_path):
    s = ProcedureStore(path=tmp_path / "p.json")
    proc = s.learn("open spotify", [{"do": "x"}])
    proc = s.learn("open spotify", [{"do": "x"}])
    s.record_failure(proc.id)
    s.record_failure(proc.id)
    assert s.get(proc.id).status == "retired"
    assert s.find("open spotify") is None
    assert s.find_any("open spotify") is None  # retired records don't even count as "close enough"


def test_a_success_after_a_retirement_learns_a_fresh_one(tmp_path):
    s = ProcedureStore(path=tmp_path / "p.json")
    proc = s.learn("open spotify", [{"do": "old"}])
    proc = s.learn("open spotify", [{"do": "old"}])
    s.record_failure(proc.id)
    s.record_failure(proc.id)
    fresh = s.learn("open spotify", [{"do": "new"}])
    assert fresh.id != proc.id and fresh.status == "learning" and fresh.steps == [{"do": "new"}]


def test_state_survives_a_restart(tmp_path):
    p = tmp_path / "p.json"
    s1 = ProcedureStore(path=p)
    proc = s1.learn("open spotify", [{"do": "x"}])
    s1.learn("open spotify", [{"do": "x"}])
    s2 = ProcedureStore(path=p)
    assert s2.get(proc.id).status == "active"


def test_growth_cap_drops_the_least_recently_used_first(tmp_path):
    s = ProcedureStore(path=tmp_path / "p.json", max_procedures=2)
    s.learn("play a video by mrbeast", [{"do": "a"}])
    s.learn("open my email", [{"do": "b"}])
    s.learn("close netflix", [{"do": "c"}])
    patterns = {p.goal_pattern for p in s.list_procedures()}
    assert len(patterns) == 2 and "play a video by mrbeast" not in patterns and "close netflix" in patterns


def test_unknown_id_operations_are_a_no_op(tmp_path):
    s = ProcedureStore(path=tmp_path / "p.json")
    s.record_success("nope")
    s.record_failure("nope")
    assert s.get("nope") is None


def test_different_named_channels_are_never_treated_as_the_same_procedure(tmp_path):
    """Code review finding C2: 'play a video by mrbeast' and 'play a video by networkchuck'
    both had empty _entities() (no on/off word, no digit), so they matched on SequenceMatcher
    similarity alone and merged into ONE record -- learn() then overwrote mrbeast's steps with
    networkchuck's, so 'play a video by mrbeast' would silently run NetworkChuck's steps."""
    s = ProcedureStore(path=tmp_path / "p.json")
    s.learn("play a video by mrbeast", [{"do": "mrbeast_steps"}])
    s.learn("play a video by networkchuck", [{"do": "networkchuck_steps"}])
    assert len(s.list_procedures()) == 2
    found = s.find_any("play a video by mrbeast")
    assert found is not None and found.steps == [{"do": "mrbeast_steps"}]


def test_different_profile_names_are_never_treated_as_the_same_procedure(tmp_path):
    """The exact P0 #1 target ('Daryl') and a different real profile ('Isaac') must never share
    a procedure record just because the surrounding words match."""
    s = ProcedureStore(path=tmp_path / "p.json")
    s.learn("open netflix and click the profile daryl", [{"do": "x"}])
    assert s.find_any("open netflix and click the profile isaac") is None


def test_opposite_verbs_without_on_off_are_never_treated_as_the_same_procedure(tmp_path):
    """_entities() only special-cased the literal words 'on'/'off' -- 'enable'/'disable',
    'mute'/'unmute', 'lock'/'unlock', 'show'/'hide', 'open'/'close' all produced the SAME (empty)
    entity set and were indistinguishable by that gate alone."""
    s = ProcedureStore(path=tmp_path / "p.json")
    s.learn("enable dark mode", [{"do": "enable_steps"}])
    assert s.find_any("disable dark mode") is None


def test_spelled_out_numbers_are_never_treated_as_the_same_procedure(tmp_path):
    s = ProcedureStore(path=tmp_path / "p.json")
    s.learn("set volume to five", [{"do": "x"}])
    assert s.find_any("set volume to ten") is None


def test_on_and_off_are_never_treated_as_the_same_procedure(tmp_path):
    """P0 #4 (core.log): 'wifi on' matched a learned 'wifi off' procedure at 0.88 similarity.
    On/off (and other opposite-entity pairs) must never fuzzy-match each other, however similar
    the surrounding words are."""
    s = ProcedureStore(path=tmp_path / "p.json")
    s.learn("turn the wifi off", [{"do": "x"}])
    s.learn("turn the wifi off", [{"do": "x"}])  # promote to active
    assert s.find("turn the wifi on") is None
    assert s.find_any("turn the wifi on") is None


def test_different_numbers_are_never_treated_as_the_same_procedure(tmp_path):
    s = ProcedureStore(path=tmp_path / "p.json")
    s.learn("set the volume to 20", [{"do": "x"}])
    s.learn("set the volume to 20", [{"do": "x"}])
    assert s.find("set the volume to 80") is None


def test_same_entities_different_phrasing_still_matches(tmp_path):
    """The fix must not become so strict it breaks the existing near-identical-phrasing case."""
    s = ProcedureStore(path=tmp_path / "p.json")
    s.learn("play a video by mrbeast", [{"do": "x"}])
    s.learn("play a video by MrBeast", [{"do": "x"}])
    assert len(s.list_procedures()) == 1
