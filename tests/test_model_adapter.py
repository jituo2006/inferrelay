import io
import json
import unittest
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from unittest.mock import patch

from examples.pagination.demo import FIXTURE, load_task, verify_fixture
from hybrid_inference.contracts import Check, Feedback, ModelError, SourceFile, Task
from hybrid_inference.models import ChatCompletionsModel


class ModelAdapterTests(unittest.TestCase):
    def setUp(self):
        self.model = ChatCompletionsModel(
            "https://example.com/v1/", "configured-model", "test-key", timeout_seconds=7,
        )
        self.partial_patch = (FIXTURE / "partial.patch").read_text(encoding="utf-8")
        self.complete_patch = (FIXTURE / "complete.patch").read_text(encoding="utf-8")

    def response(self, content, **metadata):
        payload = {
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            **metadata,
        }
        return io.BytesIO(json.dumps(payload).encode("utf-8"))

    @patch("hybrid_inference.models.urlopen")
    def test_coding_request_contains_the_original_source_and_constraints(self, urlopen):
        urlopen.return_value = self.response(self.complete_patch)
        task = load_task()

        self.model(task, None)

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        content = payload["messages"][0]["content"]
        context = json.loads(content.split("Original source context:\n", 1)[1])
        self.assertEqual(payload["model"], "configured-model")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.full_url, "https://example.com/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 7)
        self.assertIn(task.prompt, content)
        self.assertIn("complete unified diff", content)
        self.assertIn("no Markdown fences", content)
        self.assertEqual(context["task_id"], task.task_id)
        self.assertEqual(context["base_revision"], task.base_revision)
        self.assertEqual(context["allowed_paths"], list(task.allowed_paths))
        self.assertEqual(context["check_ids"], list(task.check_ids))
        self.assertEqual(context["files"], [{"path": f.path, "text": f.text} for f in task.files])

    @patch("hybrid_inference.models.urlopen")
    def test_preset_feedback_contains_candidate_and_complete_check(self, urlopen):
        urlopen.return_value = self.response(self.complete_patch)
        feedback = Feedback(
            self.partial_patch,
            Check(False, "test_failed:test_invalid_page,test_invalid_page_size",
                  "Failing pagination checks: test_invalid_page, test_invalid_page_size",
                  "test_failed"),
        )

        reply = self.model(load_task(), feedback)

        messages = json.loads(urlopen.call_args.args[0].data)["messages"]
        revision = json.loads(messages[1]["content"].split("\n", 1)[1])
        self.assertEqual(revision["previous_candidate"], self.partial_patch)
        self.assertEqual(revision["verification"], feedback.check.summary)
        self.assertEqual(revision["verification_passed"], False)
        self.assertEqual(revision["verification_code"], feedback.check.code)
        self.assertEqual(revision["verification_kind"], "test_failed")
        self.assertIn("original baseline", messages[1]["content"])
        self.assertEqual(reply.text, self.complete_patch)
        self.assertTrue(verify_fixture(load_task(), reply.text).passed)

    @patch("hybrid_inference.models.urlopen")
    def test_plain_text_task_keeps_the_original_first_message(self, urlopen):
        urlopen.return_value = self.response("[7, 4, 1]")
        task = Task("Evaluate the code.", 0.25)

        self.model(task, None)

        self.assertEqual(json.loads(urlopen.call_args.args[0].data)["messages"],
                         [{"role": "user", "content": task.prompt}])

    @patch("hybrid_inference.models.urlopen")
    def test_multiple_unicode_sources_are_preserved(self, urlopen):
        urlopen.return_value = self.response(self.complete_patch)
        files = (SourceFile("分页.py", "# 中文注释\n"), SourceFile("utils.py", "value = 1\n"))
        task = Task("Repair the sources.", 0.25, files=files,
                    allowed_paths=("分页.py", "utils.py"))

        self.model(task, None)

        content = json.loads(urlopen.call_args.args[0].data)["messages"][0]["content"]
        context = json.loads(content.split("Original source context:\n", 1)[1])
        self.assertEqual(context["files"], [{"path": f.path, "text": f.text} for f in files])

    @patch("hybrid_inference.models.urlopen")
    def test_empty_candidate_feedback_is_preserved(self, urlopen):
        urlopen.return_value = self.response(self.complete_patch)
        feedback = Feedback(None, Check(False, "empty_output", "Return a candidate.", "candidate_error"))

        self.model(load_task(), feedback)

        message = json.loads(urlopen.call_args.args[0].data)["messages"][1]["content"]
        revision = json.loads(message.split("\n", 1)[1])
        self.assertIsNone(revision["previous_candidate"])
        self.assertEqual(revision["verification_kind"], "candidate_error")

    @patch("hybrid_inference.models.urlopen")
    def test_diff_is_returned_without_stripping_or_rewriting(self, urlopen):
        candidate = self.complete_patch + "\n"
        urlopen.return_value = self.response(candidate)

        reply = self.model(load_task(), None)

        self.assertEqual(reply.text, candidate)

    @patch("hybrid_inference.models.urlopen")
    def test_diff_without_terminal_newline_is_not_silently_fixed(self, urlopen):
        candidate = self.complete_patch.rstrip("\n")
        urlopen.return_value = self.response(candidate)

        reply = self.model(load_task(), None)

        self.assertEqual(reply.text, candidate)
        self.assertFalse(reply.text.endswith("\n"))

    @patch("hybrid_inference.models.urlopen")
    def test_metadata_is_separate_from_the_patch(self, urlopen):
        urlopen.return_value = self.response(
            self.complete_patch, model="actual-model",
            usage={"prompt_tokens": 120, "completion_tokens": 60,
                   "completion_tokens_details": {"reasoning_tokens": 20}},
        )

        reply = self.model(load_task(), None)

        self.assertEqual(reply.model, "actual-model")
        self.assertEqual(reply.finish_reason, "stop")
        self.assertEqual(reply.usage.prompt_tokens, 120)
        self.assertEqual(reply.usage.completion_tokens, 60)
        self.assertEqual(reply.usage.reasoning_tokens, 20)
        self.assertEqual(reply.text, self.complete_patch)

    @patch("hybrid_inference.models.urlopen")
    def test_missing_optional_metadata_remains_unknown(self, urlopen):
        urlopen.return_value = io.BytesIO(json.dumps({
            "choices": [{"message": {"content": self.complete_patch}}],
        }).encode("utf-8"))

        reply = self.model(load_task(), None)

        self.assertEqual(reply.model, "configured-model")
        self.assertIsNone(reply.finish_reason)
        self.assertIsNone(reply.usage.prompt_tokens)
        self.assertIsNone(reply.usage.completion_tokens)
        self.assertIsNone(reply.usage.reasoning_tokens)

    @patch("hybrid_inference.models.urlopen")
    def test_absent_output_is_left_for_candidate_verification(self, urlopen):
        urlopen.return_value = self.response(None)

        self.assertIsNone(self.model(load_task(), None).text)

    @patch("hybrid_inference.models.urlopen")
    def test_length_finish_reason_is_preserved_for_the_scheduler(self, urlopen):
        urlopen.return_value = io.BytesIO(json.dumps({
            "choices": [{"message": {"content": "--- a/pagination.py\n"}, "finish_reason": "length"}],
        }).encode("utf-8"))

        self.assertEqual(self.model(load_task(), None).finish_reason, "length")

    @patch("hybrid_inference.models.urlopen")
    def test_invalid_responses_are_model_errors(self, urlopen):
        payloads = [
            None, [], {}, {"choices": []}, {"choices": [None]},
            {"choices": [{"message": None}]},
            {"choices": [{"message": {"content": ["not a string"]}}]},
            {"choices": [{"message": {"content": "patch"}}], "usage": 1},
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                urlopen.return_value = io.BytesIO(json.dumps(payload).encode("utf-8"))
                with self.assertRaisesRegex(ModelError, "^invalid_response$"):
                    self.model(load_task(), None)

    @patch("hybrid_inference.models.urlopen")
    def test_non_json_body_is_a_model_error(self, urlopen):
        urlopen.return_value = io.BytesIO(b"not json")

        with self.assertRaisesRegex(ModelError, "^invalid_response$"):
            self.model(load_task(), None)

    @patch("hybrid_inference.models.urlopen")
    def test_transport_failures_are_model_errors(self, urlopen):
        for error in (URLError("unavailable"), TimeoutError("timed out"), HTTPException("bad protocol")):
            with self.subTest(error=type(error).__name__):
                urlopen.side_effect = error
                with self.assertRaisesRegex(ModelError, "^transport_error$"):
                    self.model(load_task(), None)

    @patch("hybrid_inference.models.urlopen")
    def test_http_errors_are_closed_and_do_not_expose_provider_details(self, urlopen):
        body = io.BytesIO(b"private provider details")
        urlopen.side_effect = HTTPError("https://example.com", 429, "rate limited", {}, body)

        with self.assertRaisesRegex(ModelError, "^http_429$"):
            self.model(load_task(), None)
        self.assertTrue(body.closed)


class ModelAdapterIntegrationTests(unittest.TestCase):
    def test_local_http_demo_applies_both_patches_to_a_clean_baseline(self):
        from examples.pagination.model_adapter_demo import run_adapter_demo

        result = run_adapter_demo()

        self.assertEqual(result["backend"], "local_stub")
        self.assertFalse(result["live_provider_called"])
        self.assertEqual(result["requests_sent"], 2)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["attempts"][0]["check"]["kind"], "test_failed")
        self.assertEqual(result["attempts"][0]["check"]["code"],
                         "test_failed:test_invalid_page,test_invalid_page_size")
        self.assertTrue(result["attempts"][1]["check"]["passed"])
        self.assertEqual(result["attempts"][1]["reply"]["text"],
                         (FIXTURE / "complete.patch").read_text(encoding="utf-8"))

    def test_real_http_request_contains_source_and_preset_feedback(self):
        from examples.pagination.model_adapter_demo import preset_feedback, stub_server

        task = load_task()
        with stub_server() as (base_url, requests):
            model = ChatCompletionsModel(base_url, "local-test", "local-only", timeout_seconds=5)
            model(task, None)
            model(task, preset_feedback())

        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0]["messages"][0], requests[1]["messages"][0])
        context = json.loads(requests[0]["messages"][0]["content"].split("Original source context:\n", 1)[1])
        self.assertEqual(context["files"][0]["text"], task.files[0].text)
        revision = json.loads(requests[1]["messages"][1]["content"].split("\n", 1)[1])
        self.assertEqual(revision["previous_candidate"], preset_feedback().candidate)
        self.assertEqual(revision["verification_code"], preset_feedback().check.code)


if __name__ == "__main__":
    unittest.main()
