"""Exercise the API adapter locally with sample source and preset feedback.

The HTTP server returns bundled patches; it is not a real model provider.
"""

from contextlib import contextmanager
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from threading import Thread
from typing import Iterator

from examples.pagination.demo import FIXTURE, load_task, verify_fixture
from hybrid_inference.contracts import Check, Feedback
from hybrid_inference.models import ChatCompletionsModel


def preset_feedback() -> Feedback:
    return Feedback(
        (FIXTURE / "partial.patch").read_text(encoding="utf-8"),
        Check(
            False, "test_failed:test_invalid_page,test_invalid_page_size",
            "Failing pagination checks: test_invalid_page, test_invalid_page_size",
            "test_failed",
        ),
    )


@contextmanager
def stub_server() -> Iterator[tuple[str, list[dict]]]:
    """Serve canned responses on loopback and record request bodies only."""
    requests: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if self.path != "/v1/chat/completions":
                self.send_error(404)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload["model"], str) or not isinstance(payload["messages"], list):
                    raise ValueError("Invalid model or messages.")
            except (ValueError, KeyError, TypeError):
                self.send_error(400)
                return
            requests.append(payload)
            filename = "partial.patch" if len(requests) == 1 else "complete.patch"
            candidate = (FIXTURE / filename).read_text(encoding="utf-8")
            response = json.dumps({
                "model": "stub-" + payload["model"],
                "choices": [{"message": {"content": candidate}, "finish_reason": "stop"}],
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def run_adapter_demo() -> dict:
    task = load_task()
    feedback = preset_feedback()
    with stub_server() as (base_url, requests):
        model = ChatCompletionsModel(base_url, "pagination", "local-only", timeout_seconds=5)
        first_reply = model(task, None)
        first_check = verify_fixture(task, first_reply.text or "")
        revised_reply = model(task, feedback)
        revised_check = verify_fixture(task, revised_reply.text or "")

    passed = first_check == feedback.check and revised_check.passed
    return {
        "backend": "local_stub",
        "live_provider_called": False,
        "task_id": task.task_id,
        "requests_sent": len(requests),
        "status": "passed" if passed else "failed",
        "attempts": [
            {"feedback": None, "reply": asdict(first_reply), "check": asdict(first_check)},
            {"feedback": asdict(feedback), "reply": asdict(revised_reply), "check": asdict(revised_check)},
        ],
    }


def main() -> int:
    result = run_adapter_demo()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
