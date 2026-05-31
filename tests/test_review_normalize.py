from session2memory.review_normalize import normalize_review_text


def test_normalize_collapses_whitespace() -> None:
    assert normalize_review_text("  foo \n  bar  ") == "foo bar"
