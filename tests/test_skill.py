import json
from pathlib import Path

SKILL_PATH = Path("skills/session2memory/SKILL.md")
OPENAI_YAML_PATH = Path("skills/session2memory/agents/openai.yaml")
SKILL_JSON_PATH = Path("skills/session2memory/skill.json")


def test_session2memory_skill_exists_with_agent_facing_contract() -> None:
    text = SKILL_PATH.read_text(encoding="utf-8")

    assert text.startswith("---\n")
    assert "name: session2memory" in text
    assert "description: Use when" in text
    assert "uv run session2memory import --date" in text
    assert "uv run session2memory review list" in text
    assert "uv run session2memory review inspect" in text
    assert "uv run session2memory review approve" in text
    assert "uv run session2memory review promote" in text
    assert "./out/session-memory/$date" in text
    assert "Do not ingest raw session stores" in text
    assert "KS_ROOT=" in text
    assert "uv run ks ingest" in text
    assert "uv run ks update" in text
    assert "uv run ks query" in text
    assert "unsupported" in text


def test_session2memory_skill_has_cross_agent_metadata() -> None:
    openai_yaml = OPENAI_YAML_PATH.read_text(encoding="utf-8")
    skill_json = json.loads(SKILL_JSON_PATH.read_text(encoding="utf-8"))

    assert 'display_name: "Session2Memory"' in openai_yaml
    assert "allow_implicit_invocation: true" in openai_yaml
    assert "$session2memory" in openai_yaml

    assert skill_json["name"] == "session2memory"
    assert skill_json["version"] == "0.1.1"
    assert set(skill_json["compatible_agents"]) >= {
        "codex",
        "claude-code",
        "openclaw",
        "hermes",
        "vibe-code",
    }
    assert skill_json["entrypoint"] == "SKILL.md"
    assert skill_json["agents_metadata"] == "agents/openai.yaml"
    assert skill_json["outputs"]["generated_source"] == "out/session-memory/<date>"
    assert skill_json["hks_policy"]["default_ks_root"].endswith(
        ".hks-runs/session-memory/ks"
    )
    assert set(skill_json["supported_tools"]) == {
        "codex",
        "claude",
        "qwen",
        "opencode",
        "cursor",
        "cursor-cli",
        "openclaw",
        "hermes",
    }
