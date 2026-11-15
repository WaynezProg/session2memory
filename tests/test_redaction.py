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


def test_redact_covers_the_shared_secret_patterns() -> None:
    key = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "b3BlbnNzaC1rZXk/abc+def\n"
        "-----END OPENSSH PRIVATE KEY-----"
    )
    text = f"token Bearer abcdefghijklmnopqrstuvwxyz/0123 then\n{key}\nend"
    out = redact_text(text)
    assert "abcdefghijklmnop" not in out
    assert "PRIVATE KEY" not in out
    assert "b3BlbnNzaC1rZXk" not in out
    assert out == "token [REDACTED:token] then\n[REDACTED:token]\nend"
