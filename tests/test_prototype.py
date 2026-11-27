import io
import json
import unittest
from unittest.mock import Mock, patch

from hybrid_inference.__main__ import verify_range_output
from hybrid_inference.core import Check, Feedback, ModelError, ModelReply, Task, ThresholdRouter, run_task
from hybrid_inference.models import ChatCompletionsModel


class SchedulerTests(unittest.TestCase):
    def run_demo(self, fast, strong, *, score=0.25, policy="feedback"):
        return run_task(
            Task("Return the Python range output as JSON.", score),
            router=ThresholdRouter(0.7),
            models={"fast": fast, "strong": strong},
            verify=verify_range_output,
            policy=policy,
        )

    def test_feedback_escalates_on_repeated_failure_and_passes_candidate_to_strong(self):
        fast = Mock(return_value=ModelReply("fast", "[7, 4]"))
        strong = Mock(return_value=ModelReply("strong", "[7, 4, 1]"))

        run = self.run_demo(fast, strong)

        self.assertEqual(run.status, "passed")
        self.assertEqual([attempt.decision for attempt in run.attempts], ["retry", "escalate", "accept"])
        self.assertEqual(run.attempts[1].reason, "repeated_failure")
        feedback = strong.call_args.args[1]
        self.assertEqual(feedback.candidate, "[7, 4]")
        self.assertEqual(feedback.check, run.attempts[1].check)

    def test_fixed_policy_waits_for_the_attempt_limit(self):
        fast = Mock(return_value=ModelReply("fast", "[7, 4]"))
        strong = Mock(return_value=ModelReply("strong", "[7, 4, 1]"))

        run = self.run_demo(fast, strong, policy="fixed")

        self.assertEqual(fast.call_count, 3)
        self.assertEqual(run.attempts[2].reason, "fast_attempt_limit")
        self.assertEqual(run.status, "passed")

    def test_one_fast_attempt_escalates_with_verification_feedback(self):
        fast = Mock(return_value=ModelReply("fast", "[7, 4]"))
        strong = Mock(return_value=ModelReply("strong", "[7, 4, 1]"))

        run = run_task(
            Task("Return the Python range output as JSON.", 0.25),
            router=ThresholdRouter(0.7),
            models={"fast": fast, "strong": strong},
            verify=verify_range_output,
            max_fast_attempts=1,
        )

        self.assertEqual(run.status, "passed")
        self.assertEqual([a.decision for a in run.attempts], ["escalate", "accept"])
        self.assertEqual(run.attempts[0].reason, "fast_attempt_limit")
        self.assertEqual(strong.call_args.args[1], Feedback("[7, 4]", run.attempts[0].check))

    def test_truncated_candidate_retries_then_escalates_without_verification(self):
        fast = Mock(return_value=ModelReply("fast", "partial", finish_reason="length"))
        strong = Mock(return_value=ModelReply("strong", "[7, 4, 1]"))
        verify = Mock(wraps=verify_range_output)

        run = run_task(
            Task("Return the Python range output as JSON.", 0.25),
            router=ThresholdRouter(0.7),
            models={"fast": fast, "strong": strong}, verify=verify,
        )

        self.assertEqual(run.status, "passed")
        self.assertEqual([a.decision for a in run.attempts], ["retry", "escalate", "accept"])
        self.assertEqual(run.attempts[0].check.kind, "candidate_error")
        self.assertEqual(run.attempts[1].reason, "repeated_failure")
        verify.assert_called_once_with("[7, 4, 1]")
        self.assertEqual(strong.call_args.args[1].check, run.attempts[1].check)

    def test_different_candidate_failures_allow_another_fast_attempt(self):
        fast = Mock(side_effect=[
            ModelReply("fast", "[7]"),
            ModelReply("fast", "[7, 4]"),
            ModelReply("fast", "[7, 4, 1]"),
        ])
        strong = Mock()

        run = self.run_demo(fast, strong)

        self.assertEqual(run.status, "passed")
        self.assertEqual([attempt.decision for attempt in run.attempts], ["retry", "retry", "accept"])
        strong.assert_not_called()

    def test_threshold_routes_directly_to_strong_and_stops_after_failure(self):
        fast = Mock()
        strong = Mock(return_value=ModelReply("strong", "[7, 4]"))

        run = self.run_demo(fast, strong, score=0.7)

        fast.assert_not_called()
        strong.assert_called_once()
        self.assertEqual(run.route.model, "strong")
        self.assertEqual(run.status, "failed")
        self.assertIsNone(run.output)

    def test_provider_error_does_not_count_as_a_failed_verification(self):
        fast = Mock(side_effect=ModelError("http_429"))
        strong = Mock()

        run = self.run_demo(fast, strong)

        self.assertEqual(run.status, "model_error")
        self.assertIsNone(run.attempts[0].check)
        self.assertEqual(run.attempts[0].reason, "http_429")
        strong.assert_not_called()

    def test_provider_filter_stops_with_observable_status(self):
        fast = Mock(return_value=ModelReply("fast", None, finish_reason="content_filter"))
        strong = Mock()

        run = self.run_demo(fast, strong)

        self.assertEqual(run.status, "model_error")
        self.assertEqual(run.attempts[0].finish_reason, "content_filter")
        self.assertIsNone(run.attempts[0].check)
        strong.assert_not_called()


class ChatCompletionsTests(unittest.TestCase):
    @patch("hybrid_inference.models.urlopen")
    def test_extracts_optional_metadata_and_sends_verification_feedback(self, urlopen):
        payload = {
            "model": "actual-model",
            "choices": [{
                "message": {"content": "[7, 4, 1]", "reasoning_content": "provider trace"},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 8,
                "completion_tokens_details": {"reasoning_tokens": 3},
            },
        }
        urlopen.return_value = io.BytesIO(json.dumps(payload).encode())
        model = ChatCompletionsModel("https://example.com/v1/", "configured-model", "key")
        feedback = Feedback("[7, 4]", Check(False, "wrong", "Missing the last value."))

        reply = model(Task("Evaluate the code.", 0.25), feedback)

        request = urlopen.call_args.args[0]
        messages = json.loads(request.data)["messages"]
        self.assertEqual(request.full_url, "https://example.com/v1/chat/completions")
        self.assertIn("Missing the last value.", messages[1]["content"])
        self.assertEqual(reply.model, "actual-model")
        self.assertEqual(reply.usage.reasoning_tokens, 3)
        self.assertEqual(reply.finish_reason, "stop")
        self.assertNotIn("provider trace", repr(reply))

    @patch("hybrid_inference.models.urlopen")
    def test_missing_usage_is_unknown(self, urlopen):
        payload = {"choices": [{"message": {"content": "[7, 4, 1]"}, "finish_reason": "stop"}]}
        urlopen.return_value = io.BytesIO(json.dumps(payload).encode())
        model = ChatCompletionsModel("https://example.com/v1", "configured-model", "key")

        reply = model(Task("Evaluate the code.", 0.25), None)

        self.assertEqual(reply.model, "configured-model")
        self.assertIsNone(reply.usage.prompt_tokens)
        self.assertIsNone(reply.usage.reasoning_tokens)


if __name__ == "__main__":
    unittest.main()
