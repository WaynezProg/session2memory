import http.client
import json
import threading
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlencode

import pytest

from session2memory.review_web import (
    ReviewWebConfig,
    allowed_hosts,
    create_review_server,
    handle_review_action,
    render_review_page,
)


def _write_review_fixture(output: Path) -> None:
    (output / "review").mkdir(parents=True)
    (output / "evidence").mkdir(parents=True)
    (output / "review" / "2026-05-22.jsonl").write_text(
        json.dumps(
            {
                "id": "r000001",
                "status": "pending",
                "kind": "decision",
                "text": "Use evidence-backed memory compiler.",
                "workspace_id": "repo-123",
                "evidence_id": "e000001",
                "source": {
                    "tool": "codex",
                    "session_id": "s1",
                    "message_start": 2,
                    "message_end": 2,
                },
                "durable_suggestion": True,
                "review_note": "",
                "extraction": "marker",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (output / "evidence" / "index.jsonl").write_text(
        json.dumps(
            {
                "id": "e000001",
                "evidence_id": "e000001",
                "tool": "codex",
                "session_id": "s1",
                "source_path": "/tmp/missing.jsonl",
                "message_start": 2,
                "message_end": 2,
                "digest": "sha256:abc",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def test_render_review_page_lists_rows_and_actions(tmp_path: Path) -> None:
    output = tmp_path / "session-memory"
    _write_review_fixture(output)

    html = render_review_page(ReviewWebConfig(output_dir=output, date="2026-05-22"))

    assert "Use evidence-backed memory compiler." in html
    assert 'name="action" value="approve"' in html
    assert 'name="review_id" value="r000001"' in html


def test_handle_review_action_approves_row(tmp_path: Path) -> None:
    output = tmp_path / "session-memory"
    _write_review_fixture(output)

    result = handle_review_action(
        ReviewWebConfig(output_dir=output, date="2026-05-22"),
        {"action": ["approve"], "review_id": ["r000001"], "note": ["web reviewed"]},
    )

    assert result.status_code == 303
    row = json.loads((output / "review" / "2026-05-22.jsonl").read_text(encoding="utf-8"))
    assert row["status"] == "approved"
    assert row["review_note"] == "web reviewed"


@pytest.fixture
def served(tmp_path: Path) -> Iterator[tuple[ReviewWebConfig, int, Path]]:
    output = tmp_path / "session-memory"
    _write_review_fixture(output)
    config = ReviewWebConfig(output_dir=output, date="2026-05-22")
    server = create_review_server(config, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    try:
        yield config, int(server.server_address[1]), output
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _request(
    port: int,
    method: str,
    path: str,
    *,
    body: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, str]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    payload = urlencode(body).encode("utf-8") if body is not None else None
    sent = {"Host": f"127.0.0.1:{port}"}
    if payload is not None:
        sent["Content-Type"] = "application/x-www-form-urlencoded"
    sent.update(headers or {})
    connection.request(method, path, body=payload, headers=sent)
    response = connection.getresponse()
    text = response.read().decode("utf-8")
    connection.close()
    return response.status, text


def _status(output: Path) -> str:
    row = json.loads((output / "review" / "2026-05-22.jsonl").read_text(encoding="utf-8"))
    return str(row["status"])


def test_rendered_forms_carry_the_server_csrf_token(tmp_path: Path) -> None:
    output = tmp_path / "session-memory"
    _write_review_fixture(output)
    config = ReviewWebConfig(output_dir=output, date="2026-05-22")

    html = render_review_page(config)

    assert html.count(f'name="csrf_token" value="{config.csrf_token}"') == 2
    assert ReviewWebConfig(output_dir=output, date="2026-05-22").csrf_token != config.csrf_token


def test_allowed_hosts_are_loopback_names_with_the_bound_port() -> None:
    assert allowed_hosts("127.0.0.1", 8765) == {
        "127.0.0.1:8765",
        "localhost:8765",
        "[::1]:8765",
    }


@pytest.mark.parametrize("host", ["evil.example:{port}", "127.0.0.1:1", "localhost", ""])
def test_requests_with_foreign_host_header_are_rejected(
    served: tuple[ReviewWebConfig, int, Path], host: str
) -> None:
    config, port, output = served

    get_status, get_body = _request(port, "GET", "/", headers={"Host": host.format(port=port)})
    post_status, _ = _request(
        port,
        "POST",
        "/action",
        body={"action": "approve", "review_id": "r000001", "csrf_token": config.csrf_token},
        headers={"Host": host.format(port=port)},
    )

    assert get_status == 421
    assert "Use evidence-backed memory compiler." not in get_body
    assert post_status == 421
    assert _status(output) == "pending"


def test_get_with_loopback_host_serves_page(served: tuple[ReviewWebConfig, int, Path]) -> None:
    config, port, _ = served

    status, body = _request(port, "GET", "/", headers={"Host": f"localhost:{port}"})

    assert status == 200
    assert config.csrf_token in body


@pytest.mark.parametrize("token", [None, "", "wrong-token"])
def test_post_without_valid_csrf_token_is_forbidden(
    served: tuple[ReviewWebConfig, int, Path], token: str | None
) -> None:
    _, port, output = served
    body = {"action": "approve", "review_id": "r000001"}
    if token is not None:
        body["csrf_token"] = token

    status, _ = _request(port, "POST", "/action", body=body)

    assert status == 403
    assert _status(output) == "pending"


@pytest.mark.parametrize(
    "header",
    [
        {"Origin": "http://evil.example"},
        {"Origin": "null"},
        {"Referer": "http://evil.example/page"},
    ],
)
def test_post_from_foreign_origin_is_forbidden_even_with_token(
    served: tuple[ReviewWebConfig, int, Path], header: dict[str, str]
) -> None:
    config, port, output = served

    status, _ = _request(
        port,
        "POST",
        "/action",
        body={"action": "approve", "review_id": "r000001", "csrf_token": config.csrf_token},
        headers=header,
    )

    assert status == 403
    assert _status(output) == "pending"


def test_post_with_valid_token_and_same_origin_applies_action(
    served: tuple[ReviewWebConfig, int, Path],
) -> None:
    config, port, output = served

    status, _ = _request(
        port,
        "POST",
        "/action",
        body={"action": "approve", "review_id": "r000001", "csrf_token": config.csrf_token},
        headers={"Origin": f"http://127.0.0.1:{port}"},
    )

    assert status == 303
    assert _status(output) == "approved"
