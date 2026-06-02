from pathlib import Path

from session2memory.redaction import redact_text


def test_redact_home_and_tokens() -> None:
    text = "key=sk-abcdefghijklmnopqrst path=/Users/me/proj"
    out = redact_text(text, home=Path("/Users/me"))
    assert "sk-abc" not in out
    assert "/Users/me" not in out
    assert "[REDACTED:token]" in out


def test_redact_keeps_bare_slash() -> None:
    out = redact_text("compare input / output and approve/reject")
    assert out == "compare input / output and approve/reject"
    assert redact_text("see /etc/hosts here") == "see [REDACTED:path] here"
