from pathlib import Path


def test_gitignore_excludes_generated_outputs_and_python_artifacts() -> None:
    patterns = Path(".gitignore").read_text(encoding="utf-8").splitlines()

    assert "out/" in patterns
    assert "__pycache__/" in patterns
    assert "*.py[cod]" in patterns
    assert "src/*.egg-info/" in patterns
