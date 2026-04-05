import json
import socketserver
import sys
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from oracle.oracle_runner import evaluate_frontend_submission, sha256_string


class _TestHandler(BaseHTTPRequestHandler):
    routes = {}

    def do_GET(self):
        route = self.routes.get(self.path)
        if route is None:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"missing")
            return

        body = route["body"].encode("utf-8")
        self.send_response(route.get("status", 200))
        self.send_header("Content-Type", route.get("content_type", "text/html; charset=utf-8"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


class _ThreadedTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


def _serve(routes):
    _TestHandler.routes = routes
    server = _ThreadedTCPServer(("127.0.0.1", 0), _TestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_frontend_evaluation_pass():
    server, _ = _serve(
        {
            "/": {
                "body": """
                <html>
                  <head><title>Bounty Escrow Agent</title></head>
                  <body>
                    <h1>Trustless Task Completion</h1>
                    <a>Browse Bounties</a>
                  </body>
                </html>
                """
            },
            "/frontend/index.html": {
                "body": """
                <html>
                  <head><title>Bounty Escrow Agent</title></head>
                  <body>Trustless Task Completion Browse Bounties</body>
                </html>
                """
            },
        }
    )
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}/"
        spec = {
            "expect_status": 200,
            "required_title": "Bounty Escrow Agent",
            "required_text": ["Trustless Task Completion", "Browse Bounties"],
            "forbidden_text": ["Unhandled Runtime Error"],
            "required_paths": ["/frontend/index.html"],
            "max_response_ms": 8000,
        }
        spec_text = json.dumps(spec)
        result = evaluate_frontend_submission(base, spec_text, sha256_string(spec_text))
        assert result["verdict"] == "PASS"
        assert result["test_hash_ok"] is True
        assert result["submission_hash_ok"] is True
    finally:
        server.shutdown()
        server.server_close()


def test_frontend_evaluation_fail_on_missing_text():
    server, _ = _serve(
        {
            "/": {
                "body": """
                <html>
                  <head><title>Wrong Title</title></head>
                  <body>
                    <h1>Only partial page</h1>
                  </body>
                </html>
                """
            }
        }
    )
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}/"
        spec = {
            "expect_status": 200,
            "required_title": "Bounty Escrow Agent",
            "required_text": ["Trustless Task Completion"],
            "forbidden_text": [],
            "required_paths": [],
            "max_response_ms": 8000,
        }
        spec_text = json.dumps(spec)
        result = evaluate_frontend_submission(base, spec_text, sha256_string(spec_text))
        assert result["verdict"] == "FAIL"
        assert "Missing required text" in result["reason"] or "Title did not include" in result["reason"]
    finally:
        server.shutdown()
        server.server_close()
