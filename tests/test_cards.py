import pytest

from evie.computer.cards import ACTIONS, Action, card_for, render_action


def test_each_specialist_app_has_a_card_and_unknown_apps_get_the_general_one():
    for app in ["Safari", "WhatsApp", "Messages", "Finder", "Notes", "System Settings", "Spotify", "Notion", "Mail"]:
        assert app in card_for(app, "anything")
    assert "Accessibility" in card_for("Calculator", "work out 23 times 19")


def test_the_youtube_and_news_guides_come_with_the_safari_card_when_the_goal_needs_them():
    assert "/@" in card_for("Safari", "play the newest networkchuck video")
    assert "headline" in card_for("Safari", "open the most interesting bbc article").lower()


def test_actions_quote_every_argument_so_nothing_can_break_out():
    a = render_action("notes_new", {"title": 'x" & do shell script "rm -rf ~', "body": "hi"})
    assert isinstance(a, Action)
    assert 'do shell script "rm' not in a.script.replace('\\"', "")  # the quote is escaped, it stays text


def test_unknown_actions_and_missing_arguments_are_refused():
    with pytest.raises(KeyError):
        render_action("format_disk", {})
    with pytest.raises(ValueError):
        render_action("finder_open", {})


def test_destructive_actions_are_marked_risky():
    assert ACTIONS["finder_trash"].risky and not ACTIONS["notes_new"].risky
    assert render_action("finder_trash", {"path": "~/Desktop/old.txt"}).risky


def test_wifi_and_dark_mode_are_simple_switches():
    assert "networksetup -setairportpower" in render_action("wifi", {"on": False}).script
    assert "dark mode" in render_action("dark_mode", {"on": True}).script


def test_cards_also_come_from_what_the_goal_is_about():
    """The eval's 'turn on do not disturb' (with Finder in front) never saw the Settings card."""
    assert "settings_open" in card_for("Finder", "turn on do not disturb")
    assert "notes_new" in card_for("Safari", "make a note called groceries")
    assert "mail_unread" in card_for("Finder", "any unread emails in mail")
