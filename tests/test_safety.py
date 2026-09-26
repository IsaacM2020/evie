"""P2-G: one policy by effect class, generalizing the existing binary is_risky/risky_words
checks (which stay in place -- nothing calling them changes) into a category future code
(a not-yet-built objective.py's display/confirmation policy) can read instead of a bare bool."""
from evie.computer.safety import EffectClass, classify, is_risky


def test_a_plain_link_is_read_effect():
    assert classify("press", {"label": "Home"}, "") == EffectClass.READ


def test_typing_into_an_ordinary_search_box_is_reversible():
    assert classify("set_text", {"label": "Search"}, "hello") == EffectClass.REVERSIBLE


def test_pressing_a_send_button_is_send_effect():
    assert classify("press", {"label": "Send"}, "") == EffectClass.SEND


def test_typing_into_a_message_box_that_would_be_sent_is_send_effect():
    assert classify("set_text", {"label": "Message"}, "hi") == EffectClass.SEND


def test_pressing_delete_is_destructive_effect():
    assert classify("press", {"label": "Delete"}, "") == EffectClass.DESTRUCTIVE


def test_pressing_buy_or_checkout_is_financial_effect():
    assert classify("press", {"label": "Buy now"}, "") == EffectClass.FINANCIAL
    assert classify("press", {"label": "Checkout"}, "") == EffectClass.FINANCIAL


def test_typing_into_a_password_field_is_credential_effect():
    assert classify("set_text", {"label": "Password", "role": "input:password"}, "x") == EffectClass.CREDENTIAL


def test_typing_into_an_email_field_on_a_login_screen_is_credential_effect():
    """Matches the same login-screen rule planner.py's _is_credential_field already uses (any
    screen with a real password field bans every field on it) -- classify() takes the same
    screen_has_password flag rather than duplicating screen-scanning logic here."""
    assert classify("set_text", {"label": "Email or mobile number"}, "x",
                    screen_has_password=True) == EffectClass.CREDENTIAL


def test_an_explicitly_flagged_step_is_at_least_destructive():
    """The planner's own risky flag (a plan step marked "risky": true) must not be downgraded to
    a milder class just because the label doesn't match a keyword."""
    assert classify("press", {"label": "Confirm"}, "", flagged=True) in (
        EffectClass.SEND, EffectClass.DESTRUCTIVE, EffectClass.FINANCIAL)


def test_classify_agrees_with_is_risky_on_every_case_is_risky_already_covers():
    """is_risky stays exactly as-is (nothing calling it changes) -- this just pins that
    classify()'s notion of "not READ/REVERSIBLE" lines up with is_risky's existing bool for the
    same inputs, so the two checks can never silently disagree."""
    cases = [("press", {"label": "Home"}, ""), ("press", {"label": "Send"}, ""),
             ("set_text", {"label": "Message"}, "hi"), ("press", {"label": "Delete"}, ""),
             ("set_text", {"label": "Search"}, "hello")]
    for op, el, text in cases:
        risky = is_risky(op, el, text)
        confirmable = classify(op, el, text) not in (EffectClass.READ, EffectClass.REVERSIBLE)
        assert risky == confirmable, (op, el, text)
