from evie.computer.find import candidates, find_in_code, pick_pool
from evie.computer.observe import Screen

CHANNEL = Screen(snapshot="s1", app="Safari", kind="web", url="https://www.youtube.com/@NetworkChuck", elements=[
    {"id": "w1", "role": "input:text", "label": "Search", "typeable": True, "region": "header"},
    {"id": "w2", "role": "tab", "label": "Home", "href": "https://www.youtube.com/@NetworkChuck/featured"},
    {"id": "w3", "role": "tab", "label": "Videos", "href": "https://www.youtube.com/@NetworkChuck/videos"},
    {"id": "w4", "role": "tab", "label": "Shorts", "href": "https://www.youtube.com/@NetworkChuck/shorts"},
    {"id": "w5", "role": "link", "label": "I hacked my own network (don't try this)",
     "href": "https://www.youtube.com/watch?v=n1", "meta": "412K views 2 days ago", "group": "c1", "region": "main"},
    {"id": "w6", "role": "link", "label": "you need to learn Linux RIGHT NOW!!",
     "href": "https://www.youtube.com/watch?v=n2", "meta": "1.2M views 1 week ago", "group": "c2", "region": "main"},
    {"id": "w7", "role": "link", "label": "NetworkChuck", "href": "https://www.youtube.com/@NetworkChuck", "region": "main"},
    {"id": "w8", "role": "button", "label": "Subscribe", "region": "main"},
])


def test_an_exact_label_is_found_without_any_model():
    el, _ = find_in_code(CHANNEL, "Videos tab", role="tab")
    assert el["id"] == "w3"


def test_the_search_box_is_found_by_being_typeable():
    el, _ = find_in_code(CHANNEL, "search box", typeable=True)
    assert el["id"] == "w1"


def test_a_vague_description_is_left_for_jev_with_the_best_candidates_first():
    el, cands = find_in_code(CHANNEL, "the video about linux")
    assert el is None or el["id"] == "w6"
    assert cands[0]["id"] == "w6"


def test_href_patterns_narrow_the_field():
    el, _ = find_in_code(CHANNEL, "NetworkChuck channel", href="/@")
    assert el["id"] in ("w7", "w2")  # a channel link, never a video or the Subscribe button


def test_pick_pool_for_videos_is_only_real_videos_in_page_order():
    pool = pick_pool(CHANNEL, "videos")
    assert [e["id"] for e in pool] == ["w5", "w6"]


def test_pick_pool_for_articles_takes_long_headline_links_in_the_main_part():
    news = Screen(snapshot="s", app="Safari", kind="web", elements=[
        {"id": "w1", "role": "link", "label": "News", "href": "https://bbc.com/news", "region": "nav"},
        {"id": "w2", "role": "link", "label": "Singapore unveils plan to double solar power by 2030",
         "href": "https://bbc.com/news/articles/c1", "region": "main"},
        {"id": "w3", "role": "link", "label": "Terms of Use", "href": "https://bbc.com/terms", "region": "footer"}])
    assert [e["id"] for e in pick_pool(news, "articles")] == ["w2"]


def test_candidates_never_include_ids_that_are_not_on_screen():
    assert all(c["id"] in CHANNEL.ids for c in candidates(CHANNEL, "anything at all"))
