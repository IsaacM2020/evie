from evie.retention import cap_count, prune_by_age, safe_to_store


def test_safe_to_store_allows_addressed_speech():
    assert safe_to_store("what's on friday", addressed=True) is True


def test_safe_to_store_blocks_unaddressed_or_ignored_speech():
    assert safe_to_store("mom I have the dentist Wednesday", addressed=False) is False
    assert safe_to_store("anything", addressed=True, ignored=True) is False


def test_safe_to_store_blocks_empty_text():
    assert safe_to_store("", addressed=True) is False


def test_prune_by_age_drops_only_whats_older_than_the_cutoff():
    now = [1000.0]
    items = [{"t": 100.0}, {"t": 990.0}, {"t": 999.0}]
    out = prune_by_age(items, lambda i: i["t"], max_age_s=50, now=lambda: now[0])
    assert out == [{"t": 990.0}, {"t": 999.0}]


def test_prune_by_age_keeps_items_with_no_timestamp():
    items = [{"t": None}, {"t": 1.0}]
    out = prune_by_age(items, lambda i: i["t"], max_age_s=10, now=lambda: 1000.0)
    assert out == [{"t": None}]


def test_cap_count_keeps_the_most_recent_n():
    items = [{"id": 1, "t": 1}, {"id": 2, "t": 3}, {"id": 3, "t": 2}]
    out = cap_count(items, 2, sort_key=lambda i: i["t"])
    assert {i["id"] for i in out} == {2, 3}


def test_cap_count_is_a_no_op_under_the_limit():
    items = [{"id": 1, "t": 1}]
    assert cap_count(items, 5, sort_key=lambda i: i["t"]) == items
