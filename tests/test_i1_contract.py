import json
from subprocess import TimeoutExpired
import unittest
from unittest.mock import Mock

from examples.pagination.demo import FIXTURE, load_task, run_demo
from hybrid_inference.contracts import Check, ModelReply
from hybrid_inference.core import ThresholdRouter, run_task


class I1ContractTests(unittest.TestCase):
    def test_pagination_fixtures_match_the_expected_trace(self):
        run = run_demo()
        trace = {
            "initial_role": run.route.model,
            "status": run.status,
            "attempts": [
                {
                    "role": attempt.role,
                    "check_code": attempt.check.code if attempt.check else None,
                    "decision": attempt.decision,
                    "reason": attempt.reason,
                }
                for attempt in run.attempts
            ],
        }

        expected = json.loads((FIXTURE / "expected_trace.json").read_text(encoding="utf-8"))
        self.assertEqual(trace, expected)
        self.assertEqual(run.output, (FIXTURE / "complete.patch").read_text(encoding="utf-8"))

    def test_pagination_stops_when_strong_candidate_still_fails(self):
        run = run_demo(scenario="strong-failure")

        self.assertEqual(run.status, "failed")
        self.assertIsNone(run.output)
        self.assertEqual(
            [(attempt.role, attempt.decision, attempt.reason) for attempt in run.attempts],
            [
                ("fast", "retry", "fast_attempts_remaining"),
                ("fast", "escalate", "repeated_failure"),
                ("strong", "stop", "strong_verification_failed"),
            ],
        )
        self.assertEqual(run.attempts[-1].check.kind, "test_failed")

    def test_verifier_environment_error_stops_without_escalating(self):
        fast = Mock(return_value=ModelReply("fast", "patch"))
        strong = Mock()
        verify = Mock(return_value=Check(
            False, "runner_unavailable", "Runner is offline.", kind="environment_error",
        ))

        run = run_task(
            load_task(), router=ThresholdRouter(0.7),
            models={"fast": fast, "strong": strong}, verify=verify,
        )

        self.assertEqual(run.status, "verification_error")
        self.assertEqual(run.attempts[0].reason, "verification_environment_error")
        self.assertIsNone(run.output)
        fast.assert_called_once()
        strong.assert_not_called()

    def test_verifier_execution_failures_return_a_recorded_stop(self):
        for error in (OSError("runner missing"), TimeoutExpired("check", 10)):
            with self.subTest(error=type(error).__name__):
                fast = Mock(return_value=ModelReply("fast", "patch"))
                strong = Mock()
                verify = Mock(side_effect=error)

                run = run_task(
                    load_task(), router=ThresholdRouter(0.7),
                    models={"fast": fast, "strong": strong}, verify=verify,
                )

                self.assertEqual(run.status, "verification_error")
                self.assertIsNone(run.output)
                self.assertEqual(len(run.attempts), 1)
                self.assertEqual(run.attempts[0].decision, "stop")
                self.assertEqual(run.attempts[0].reason, "verification_environment_error")
                self.assertEqual(run.attempts[0].check.kind, "environment_error")
                self.assertEqual(run.attempts[0].check.code, "verifier_execution_error")
                verify.assert_called_once_with("patch")
                strong.assert_not_called()


if __name__ == "__main__":
    unittest.main()
