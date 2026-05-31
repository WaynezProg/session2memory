import shutil
from pathlib import Path

from session2memory.adapters.openclaw import OpenClawAdapter
from session2memory.adapters.text_log import touch_file_mtime
from session2memory.extraction import extract_candidates
from session2memory.workspace import resolve_workspace

FIXTURE = Path("tests/fixtures/openclaw/sample.log")


def test_openclaw_adapter_reads_fixture(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    root.mkdir()
    target = root / "sample.log"
    shutil.copy(FIXTURE, target)
    touch_file_mtime(target, "2026-05-22")

    records = list(OpenClawAdapter(root).iter_sessions("2026-05-22"))
    assert len(records) == 1
    assert any("Decision:" in message.text for message in records[0].messages)

    workspace = resolve_workspace(records[0])
    candidates = extract_candidates(records[0], workspace)
    assert any(candidate.kind == "decision" for candidate in candidates)
