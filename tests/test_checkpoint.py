from ctx_cli.checkpoint import append_checkpoint


def test_checkpoint_appends_and_creates_dir(config):
    path = append_checkpoint(config, "did the thing; next: the other thing")
    assert path.exists()
    text = path.read_text()
    assert "did the thing; next: the other thing" in text
    assert text.startswith("## ")


def test_checkpoint_appends_multiple_entries(config):
    append_checkpoint(config, "first note")
    path = append_checkpoint(config, "second note")
    text = path.read_text()
    assert "first note" in text
    assert "second note" in text
    assert text.index("first note") < text.index("second note")
