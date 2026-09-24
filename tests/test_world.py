from evie.computer.world import World

SAFARI_BEHIND = {
    "front_app": "Notes",
    "apps": ["Notes", "Safari", "WhatsApp", "Spotify"],
    "windows": [{"app": "Notes", "title": "Shopping list"}, {"app": "Safari", "title": "BBC News - Home"},
                {"app": "WhatsApp", "title": "WhatsApp"}],
    "tabs": [
        {"window": 11, "order": 1, "index": 1, "current": False, "title": "Inbox (3) - Gmail", "url": "https://mail.google.com/mail/u/0/"},
        {"window": 11, "order": 1, "index": 2, "current": True, "title": "BBC News - Home", "url": "https://www.bbc.com/news"},
        {"window": 12, "order": 2, "index": 1, "current": True, "title": "NetworkChuck - YouTube", "url": "https://www.youtube.com/@NetworkChuck"},
    ],
    "selected": "",
}


def test_this_news_thing_means_the_front_safari_tab_even_when_safari_is_behind():
    """Isaac: 'if I have Safari open but it's not on top, it should bring it to the top'."""
    t = World.from_data(SAFARI_BEHIND).resolve("open the most interesting article in this news thing")
    assert t.kind == "tab" and (t.window, t.index) == (11, 2) and t.app == "Safari" and t.bring_front


def test_this_with_a_browser_in_front_is_its_current_tab():
    w = dict(SAFARI_BEHIND, front_app="Safari")
    t = World.from_data(w).resolve("summarise this page")
    assert t.kind == "tab" and (t.window, t.index) == (11, 2) and not t.bring_front


def test_this_without_web_words_is_the_front_app():
    t = World.from_data(SAFARI_BEHIND).resolve("add milk to this")
    assert t.kind == "app" and t.app == "Notes"


def test_a_named_tab_is_found_by_its_title_or_site():
    t = World.from_data(SAFARI_BEHIND).resolve("switch to my gmail")
    assert t.kind == "tab" and (t.window, t.index) == (11, 1)
    t = World.from_data(SAFARI_BEHIND).resolve("go to the networkchuck tab")
    assert (t.window, t.index) == (12, 1)


def test_two_tabs_that_could_match_are_left_as_choices():
    w = dict(SAFARI_BEHIND, tabs=SAFARI_BEHIND["tabs"] + [
        {"window": 12, "order": 2, "index": 2, "current": False, "title": "BBC Sport", "url": "https://www.bbc.com/sport"}])
    t = World.from_data(w).resolve("switch to the bbc tab")
    assert t.kind == "choose" and len(t.choices) == 2


def test_a_named_app_is_the_target():
    t = World.from_data(SAFARI_BEHIND).resolve("message mom on whatsapp")
    assert t.kind == "app" and t.app == "WhatsApp" and t.running


def test_no_browser_open_means_a_new_one():
    w = {"front_app": "Finder", "apps": ["Finder"], "windows": [], "tabs": [], "selected": ""}
    t = World.from_data(w).resolve("play the newest networkchuck video")
    assert t.kind == "new_tab" and t.app == "Safari"


def test_summary_lists_what_is_open_for_the_planner():
    s = World.from_data(SAFARI_BEHIND).summary()
    assert s.startswith("In front: Notes") and "BBC News - Home (current)" in s and "WhatsApp" in s
