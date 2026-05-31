import os
import subprocess
from pathlib import Path


def test_daily_hks_script_uses_dated_output_and_parent_hks_root(tmp_path: Path) -> None:
    script = Path("scripts/daily-session-memory-to-hks.sh")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log_path = tmp_path / "uv.log"
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        "#!/bin/sh\n"
        "printf '%s ' \"cwd=$PWD\" \"KS_ROOT=$KS_ROOT\" >> \"$UV_LOG\"\n"
        "printf '%s\\n' \"HKS_EMBEDDING_MODEL=$HKS_EMBEDDING_MODEL args=$*\" >> \"$UV_LOG\"\n"
        "case \"$*\" in\n"
        "  *\"session2memory import\"*) "
        "printf 'date=2026-05-28 tools=4 sessions=1 written=1 candidates=1\\n' ;;\n"
        "  *\"ks update\"*) "
        "printf '{\"answer\":\"update ok: 1 completed / 0 failed\"}\\n' ;;\n"
        "  *\"ks source list\"*) printf '{\"answer\":\"source catalog：1 / 1 sources\"}\\n' ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    hks_repo = tmp_path / "hks"
    hks_repo.mkdir()
    output_root = tmp_path / "out" / "session-memory"
    ks_root = tmp_path / "ks"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "UV_LOG": str(log_path),
        "HKS_REPO": str(hks_repo),
        "KS_ROOT": str(ks_root),
        "SESSION2MEMORY_OUTPUT_ROOT": str(output_root),
        "HKS_EMBEDDING_MODEL": "simple",
    }

    result = subprocess.run(
        ["bash", str(script), "--date", "2026-05-28"],
        check=False,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    log = log_path.read_text(encoding="utf-8")
    dated_output = output_root / "2026-05-28"
    assert f"session2memory import --date 2026-05-28 --output {dated_output} --dry-run" in log
    assert f"session2memory import --date 2026-05-28 --output {dated_output}" in log
    assert f"ks update {output_root}" in log
    assert "ks source list --relpath-query 2026-05-28" in log
    assert f"KS_ROOT={ks_root}" in log
