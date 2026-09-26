from evie.job_commands import JobCommand, extract_ordinal, parse_job_command


def test_pause_that_job():
    assert parse_job_command("pause that job") == JobCommand("pause", text="pause that job")


def test_pause_the_background_job():
    assert parse_job_command("pause the background job") == JobCommand("pause", text="pause the background job")


def test_pause_job_two_has_an_ordinal():
    assert parse_job_command("pause job two") == JobCommand("pause", ordinal=2, text="pause job two")


def test_pause_job_number_three():
    assert parse_job_command("pause job number three") == JobCommand("pause", ordinal=3,
                                                                      text="pause job number three")


def test_pause_job_digit():
    assert parse_job_command("pause job 2") == JobCommand("pause", ordinal=2, text="pause job 2")


def test_resume_it_is_bare():
    assert parse_job_command("resume it") == JobCommand("resume", bare=True, text="resume it")


def test_continue_that_job_is_explicit_not_bare():
    assert parse_job_command("continue that job") == JobCommand("resume", text="continue that job")


def test_stop_that_job():
    assert parse_job_command("stop that job") == JobCommand("stop", text="stop that job")


def test_cancel_the_job_is_a_stop():
    assert parse_job_command("cancel the job") == JobCommand("stop", text="cancel the job")


def test_unpause_is_a_resume():
    assert parse_job_command("unpause the job") == JobCommand("resume", text="unpause the job")


def test_evie_prefix_is_tolerated():
    assert parse_job_command("evie, pause the background job") == JobCommand(
        "pause", text="evie, pause the background job")


def test_trailing_question_mark_is_tolerated():
    assert parse_job_command("resume the job?") == JobCommand("resume", text="resume the job")


# -- must never shadow existing verbs that mean something else --------------------------------

def test_pause_the_music_is_not_a_job_command():
    assert parse_job_command("pause the music") is None


def test_pause_alone_is_not_a_job_command():
    assert parse_job_command("pause") is None


def test_stop_alone_is_not_a_job_command():
    """Bare "stop"/"stop it" stays owned entirely by brain.is_stop(); this parser never sees it
    reached that way, but must not accidentally match it either if it ever is."""
    assert parse_job_command("stop") is None
    assert parse_job_command("stop it") is None


def test_stop_talking_is_not_a_job_command():
    assert parse_job_command("stop talking") is None


def test_cancel_that_is_not_a_job_command():
    assert parse_job_command("cancel that") is None


def test_pause_it_bare_is_not_supported_only_resume_is():
    """pause/stop collide with real meanings (music, "stop talking") even bare, so only resume's
    bare form (nothing else in Evie means "resume") is allowed without the word "job"."""
    assert parse_job_command("pause it") is None
    assert parse_job_command("stop that") is None


def test_resume_the_music_bare_word_not_matched_without_it_or_that():
    assert parse_job_command("resume playing") is None


def test_unrelated_sentence_is_none():
    assert parse_job_command("what time is it") is None
    assert parse_job_command("mark that goal done") is None


# -- extract_ordinal: answering Evie's own clarification question -------------------------------

def test_extract_ordinal_digit():
    assert extract_ordinal("job two") == 2
    assert extract_ordinal("the second one") == 2
    assert extract_ordinal("2") == 2
    assert extract_ordinal("number one") == 1
    assert extract_ordinal("no idea") is None
