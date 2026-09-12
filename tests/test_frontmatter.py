from ctx_cli import frontmatter as fm


def test_parses_frontmatter_block():
    text = "---\nid: x\ntitle: Y\nsummary: Z\n---\n\n# body\n"
    data = fm.parse_frontmatter(text)
    assert data == {"id": "x", "title": "Y", "summary": "Z"}


def test_no_frontmatter_returns_empty_dict():
    assert fm.parse_frontmatter("# just a heading\n") == {}


def test_unterminated_frontmatter_returns_empty_dict():
    assert fm.parse_frontmatter("---\nid: x\n") == {}


def test_strip_frontmatter_leaves_body_only():
    text = "---\nid: x\n---\nbody text\n"
    assert fm.strip_frontmatter(text).strip() == "body text"


def test_as_list_handles_scalar_and_list():
    assert fm.as_list(None) == []
    assert fm.as_list("a") == ["a"]
    assert fm.as_list(["a", "b"]) == ["a", "b"]
