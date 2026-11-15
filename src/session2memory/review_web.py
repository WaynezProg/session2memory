from __future__ import annotations

import hmac
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from email.message import Message
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from session2memory.review import (
    ReviewNotFoundError,
    approve_review,
    inspect_review,
    list_reviews,
    promote_reviews,
    reject_review,
)

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")
CSRF_FIELD = "csrf_token"


@dataclass(frozen=True)
class ReviewWebConfig:
    output_dir: Path
    date: str
    # Random per server run; embedded in every served form and required on POST.
    csrf_token: str = field(default_factory=lambda: secrets.token_urlsafe(32), repr=False)


@dataclass(frozen=True)
class WebActionResult:
    status_code: int
    location: str
    message: str


def render_review_page(config: ReviewWebConfig, *, selected_id: str | None = None) -> str:
    rows = list_reviews(output_dir=config.output_dir, date=config.date)
    selected = _selected_review_id(rows, selected_id)
    detail = _review_detail(config, selected) if selected else "<p>No review rows.</p>"
    return (
        "<!doctype html>"
        "<html><head>"
        '<meta charset="utf-8">'
        f"<title>session2memory review {escape(config.date)}</title>"
        "<style>"
        "body{font-family:system-ui,sans-serif;margin:0;display:grid;"
        "grid-template-columns:38% 62%;height:100vh}"
        "main,aside{padding:16px;overflow:auto}"
        "aside{border-right:1px solid #ddd;background:#f8f8f8}"
        ".row{display:block;padding:10px;margin:0 0 8px;border:1px solid #ddd;"
        "background:white;color:#111;text-decoration:none}"
        ".selected{border-color:#111}"
        ".meta{color:#666;font-size:12px}"
        "pre{white-space:pre-wrap;background:#f4f4f4;padding:12px;border:1px solid #ddd}"
        "button{margin-right:8px;padding:6px 10px}"
        "textarea{display:block;width:100%;height:70px;margin:8px 0}"
        "</style></head><body>"
        f"<aside><h1>Review {escape(config.date)}</h1>{_review_rows(rows, selected)}</aside>"
        f"<main>{detail}</main>"
        "</body></html>"
    )


def handle_review_action(
    config: ReviewWebConfig,
    params: Mapping[str, Sequence[str]],
) -> WebActionResult:
    action = _first(params, "action")
    review_id = _first(params, "review_id")
    note = _first(params, "note") or None
    try:
        if action == "approve":
            approve_review(
                output_dir=config.output_dir,
                date=config.date,
                review_id=review_id,
                note=note,
                durable=_first(params, "durable") == "on",
            )
            return WebActionResult(HTTPStatus.SEE_OTHER, f"/?id={review_id}", "approved")
        if action == "reject":
            reject_review(
                output_dir=config.output_dir,
                date=config.date,
                review_id=review_id,
                note=note,
            )
            return WebActionResult(HTTPStatus.SEE_OTHER, f"/?id={review_id}", "rejected")
        if action == "promote":
            result = promote_reviews(output_dir=config.output_dir, date=config.date)
            if result.blocked:
                return WebActionResult(HTTPStatus.CONFLICT, "/", "conflict")
            return WebActionResult(HTTPStatus.SEE_OTHER, "/", "promoted")
    except ReviewNotFoundError as exc:
        return WebActionResult(HTTPStatus.NOT_FOUND, "/", str(exc))
    return WebActionResult(HTTPStatus.BAD_REQUEST, "/", f"unsupported action: {action}")


def run_review_web(config: ReviewWebConfig, *, host: str = "127.0.0.1", port: int = 8765) -> None:
    server = create_review_server(config, host=host, port=port)
    print(f"session2memory review web: http://{_url_host(host)}:{server.server_address[1]}/")
    try:
        server.serve_forever()
    finally:
        server.server_close()


def create_review_server(
    config: ReviewWebConfig, *, host: str = "127.0.0.1", port: int = 8765
) -> ThreadingHTTPServer:
    """Bind the review server; requests must name this host and port in ``Host``."""
    return ThreadingHTTPServer((host, port), _handler_for(config, bind_host=host))


def allowed_hosts(bind_host: str, port: int) -> frozenset[str]:
    """``Host`` header values accepted for a server bound to ``bind_host:port``.

    Loopback names are always accepted; a non-loopback bind host is accepted only as
    itself. Anything else (for example a DNS-rebound attacker domain) is rejected.
    """
    names = set(LOOPBACK_HOSTS)
    names.add(_url_host(bind_host))
    return frozenset(f"{name}:{port}".lower() for name in names)


def _url_host(host: str) -> str:
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


def _review_rows(rows: Sequence[dict[str, Any]], selected_id: str | None) -> str:
    if not rows:
        return "<p>No review rows.</p>"
    links: list[str] = []
    for row in rows:
        review_id = str(row.get("id", ""))
        css = "row selected" if review_id == selected_id else "row"
        confidence = row.get("confidence")
        conf = f" conf={escape(str(confidence))}" if confidence is not None else ""
        links.append(
            f'<a class="{css}" href="/?id={escape(review_id)}">'
            f"<strong>{escape(str(row.get('status', '')))}</strong> "
            f"{escape(str(row.get('kind', '')))}"
            f'<div class="meta">{escape(review_id)} '
            f"{escape(str(row.get('extraction', 'marker')))}{conf}</div>"
            f"{escape(str(row.get('text', '')))}"
            "</a>"
        )
    return "\n".join(links)


def _review_detail(config: ReviewWebConfig, review_id: str) -> str:
    try:
        inspection = inspect_review(
            output_dir=config.output_dir,
            date=config.date,
            review_id=review_id,
        )
    except ReviewNotFoundError:
        return "<p>Review row missing.</p>"
    row = inspection.row
    return (
        f"<h2>{escape(str(row.get('kind', '')))} "
        f"<span class=\"meta\">{escape(review_id)}</span></h2>"
        f"<p><strong>{escape(str(row.get('status', '')))}</strong></p>"
        f"<pre>{escape(str(row.get('text', '')))}</pre>"
        f"<h3>Evidence</h3><pre>{escape(inspection.preview)}</pre>"
        f"{_action_forms(review_id, row, csrf_token=config.csrf_token)}"
    )


def _action_forms(review_id: str, row: Mapping[str, Any], *, csrf_token: str) -> str:
    durable_checked = " checked" if row.get("durable_suggestion") is True else ""
    review_id_input = (
        f'<input type="hidden" name="review_id" value="{escape(review_id)}">'
    )
    note = '<textarea name="note" placeholder="review note"></textarea>'
    token_input = f'<input type="hidden" name="{CSRF_FIELD}" value="{escape(csrf_token)}">'
    return (
        '<form method="post" action="/action">'
        f"{token_input}{review_id_input}{note}"
        f'<label><input type="checkbox" name="durable"{durable_checked}> durable</label>'
        '<button name="action" value="approve">Approve</button>'
        '<button name="action" value="reject">Reject</button>'
        "</form>"
        '<form method="post" action="/action">'
        f"{token_input}"
        '<button name="action" value="promote">Promote Approved</button>'
        "</form>"
    )


def _selected_review_id(rows: Sequence[dict[str, Any]], requested: str | None) -> str | None:
    ids = [str(row.get("id", "")) for row in rows]
    if requested in ids:
        return requested
    return ids[0] if ids else None


def _first(params: Mapping[str, Sequence[str]], key: str) -> str:
    values = params.get(key)
    if not values:
        return ""
    return str(values[0])


def _origin_allowed(headers: Message, hosts: frozenset[str]) -> bool:
    """Reject cross-site requests by ``Origin`` (or ``Referer``) when the browser sends one."""
    origin = headers.get("Origin")
    if origin is not None:
        parsed = urlparse(origin)
        return parsed.scheme == "http" and parsed.netloc.lower() in hosts
    referer = headers.get("Referer")
    if referer is not None:
        parsed = urlparse(referer)
        return parsed.scheme == "http" and parsed.netloc.lower() in hosts
    return True


def _token_valid(params: Mapping[str, Sequence[str]], expected: str) -> bool:
    supplied = _first(params, CSRF_FIELD)
    return bool(supplied) and hmac.compare_digest(supplied.encode(), expected.encode())


def _handler_for(config: ReviewWebConfig, *, bind_host: str) -> type[BaseHTTPRequestHandler]:
    class ReviewWebHandler(BaseHTTPRequestHandler):
        def _hosts(self) -> frozenset[str]:
            address = self.server.server_address
            port = int(address[1]) if isinstance(address, tuple) else 0
            return allowed_hosts(bind_host, port)

        def _host_allowed(self) -> bool:
            host = (self.headers.get("Host") or "").strip().lower()
            if host in self._hosts():
                return True
            self._send_text(HTTPStatus.MISDIRECTED_REQUEST, "unexpected Host header")
            return False

        def _send_text(self, status: int, message: str) -> None:
            body = message.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def end_headers(self) -> None:
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def do_GET(self) -> None:
            if not self._host_allowed():
                return
            parsed = urlparse(self.path)
            selected_id = _first(parse_qs(parsed.query), "id")
            body = render_review_page(config, selected_id=selected_id).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            if not self._host_allowed():
                return
            if not _origin_allowed(self.headers, self._hosts()):
                self._send_text(HTTPStatus.FORBIDDEN, "cross-origin request rejected")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send_text(HTTPStatus.BAD_REQUEST, "invalid Content-Length")
                return
            raw = self.rfile.read(max(length, 0)).decode("utf-8", errors="replace")
            params = parse_qs(raw)
            if not _token_valid(params, config.csrf_token):
                self._send_text(HTTPStatus.FORBIDDEN, "missing or invalid CSRF token")
                return
            result = handle_review_action(config, params)
            if result.status_code == HTTPStatus.SEE_OTHER:
                self.send_response(result.status_code)
                self.send_header("Location", result.location)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self._send_text(result.status_code, result.message)

        def log_message(self, format: str, *args: object) -> None:
            return

    return ReviewWebHandler
