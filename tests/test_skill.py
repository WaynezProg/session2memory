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
    assert "uv run session2memory import" in text
    assert '--date "$date"' in text
    assert "HKS_SESSION2MEMORY_EXPORT_ROOT" in text
    assert "uv run session2memory review list" in text
    assert "uv run session2memory review inspect" in text
    assert "uv run session2memory review approve" in text
    assert "uv run session2memory review promote" in text
    assert "hks_workspace_ingest_session_memory" in text
    assert "hks_workspace_query" in text
    assert "writeback=no" in text
    assert "## Security" in text
    assert "Do not ingest raw session stores into HKS" not in text
    assert "~/.codex" in text
    assert "~/.claude" in text
    assert "~/.cursor" in text
    assert "Operator batch ingest" in text
    assert "scripts/daily-session-memory-to-hks.sh" in text
    assert "seesion2memory" not in text
    assert "uv run ks ingest" not in text
    assert "uv run ks update" not in text
    assert "unsupported" in text


def test_session2memory_skill_has_cross_agent_metadata() -> None:
    openai_yaml = OPENAI_YAML_PATH.read_text(encoding="utf-8")
    skill_json = json.loads(SKILL_JSON_PATH.read_text(encoding="utf-8"))

    assert 'display_name: "Session2Memory"' in openai_yaml
    assert "allow_implicit_invocation: true" in openai_yaml
    assert "$session2memory" in openai_yaml
    assert "HKS_SESSION2MEMORY_EXPORT_ROOT" in openai_yaml
    assert "hks_workspace_ingest_session_memory" in openai_yaml

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
    assert skill_json["outputs"]["generated_source"] == (
        "$HKS_SESSION2MEMORY_EXPORT_ROOT/<workspace_id>"
    )
    assert skill_json["hks_policy"]["default_ks_root"].endswith(
        ".hks-runs/session-memory/ks"
    )
    assert skill_json["hks_policy"]["do_not_ingest_raw_session_stores"] is True
    assert "hks_workspace_ingest_session_memory" in skill_json["commands"]["hks_ingest"]
    assert set(skill_json["supported_tools"]) == {
        "codex",
        "claude",
        "claude-desktop",
        "qwen",
        "opencode",
        "cursor",
        "cursor-cli",
        "openclaw",
        "hermes",
    }
