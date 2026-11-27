from dataclasses import dataclass
from subprocess import SubprocessError
from time import perf_counter
from typing import Callable, Mapping

from .contracts import (
    Attempt,
    Check,
    Feedback,
    Model,
    ModelError,
    ModelReply,
    Policy,
    Role,
    Route,
    Router,
    Run,
    RunStatus,
    Task,
    Usage,
)

# Keep the original public imports available from core.
__all__ = [
    "Attempt", "Check", "Feedback", "Model", "ModelError", "ModelReply", "Policy",
    "Role", "Route", "Run", "Task", "ThresholdRouter", "Usage", "run_task",
]


@dataclass(frozen=True)
class ThresholdRouter:
    threshold: float

    def __call__(self, task: Task) -> Route:
        if not 0 <= task.difficulty <= 1 or not 0 <= self.threshold <= 1:
            raise ValueError("Difficulty and threshold must be between 0 and 1.")
        model: Role = "strong" if task.difficulty >= self.threshold else "fast"
        return Route(model, task.difficulty, self.threshold)


def run_task(
    task: Task,
    *,
    router: Router,
    models: Mapping[Role, Model],
    verify: Callable[[str], Check],
    policy: Policy = "feedback",
    max_fast_attempts: int = 3,
) -> Run:
    if max_fast_attempts < 1:
        raise ValueError("The fast model must be allowed at least one attempt.")
    if policy not in ("fixed", "feedback"):
        raise ValueError(f"Unknown policy: {policy}")

    started = perf_counter()
    route = router(task)
    role = route.model
    attempts: list[Attempt] = []
    feedback = None
    fast_attempts = 0
    output = None
    status: RunStatus = "failed"

    while True:
        call_started = perf_counter()
        try:
            reply = models[role](task, feedback)
        except ModelError as error:
            model_ms = round((perf_counter() - call_started) * 1000, 3)
            attempts.append(
                Attempt(role, None, model_ms, None, None, None, "stop", str(error))
            )
            status = "model_error"
            break

        model_ms = round((perf_counter() - call_started) * 1000, 3)
        if reply.finish_reason not in (None, "stop", "length"):
            attempts.append(
                Attempt(
                    role, reply.model, model_ms, reply.finish_reason, reply.usage,
                    None, "stop", "unexpected_finish_reason",
                )
            )
            status = "model_error"
            break
        if reply.finish_reason == "length":
            check = Check(
                False, "output_truncated", "The model reached its output limit.",
                kind="candidate_error",
            )
        elif reply.text is None or not reply.text.strip():
            check = Check(
                False, "empty_output", "The model returned no candidate.",
                kind="candidate_error",
            )
        else:
            try:
                check = verify(reply.text)
            except (OSError, SubprocessError) as error:
                check = Check(
                    False, "verifier_execution_error",
                    f"The verifier could not complete: {error}",
                    kind="environment_error",
                )

        if role == "fast":
            fast_attempts += 1

        if check.kind == "environment_error":
            decision, reason = "stop", "verification_environment_error"
            status = "verification_error"
        elif check.passed:
            decision, reason = "accept", "verification_passed"
            status, output = "passed", reply.text
        elif role == "strong":
            decision, reason = "stop", "strong_verification_failed"
        elif fast_attempts >= max_fast_attempts:
            decision, reason = "escalate", "fast_attempt_limit"
        elif policy == "feedback" and feedback and (
            feedback.check.kind, feedback.check.code
        ) == (check.kind, check.code):
            decision, reason = "escalate", "repeated_failure"
        else:
            decision, reason = "retry", "fast_attempts_remaining"

        attempts.append(
            Attempt(role, reply.model, model_ms, reply.finish_reason, reply.usage, check, decision, reason)
        )
        if decision in ("accept", "stop"):
            break

        feedback = Feedback(reply.text, check)
        if decision == "escalate":
            role = "strong"

    elapsed_ms = round((perf_counter() - started) * 1000, 3)
    return Run(route, policy, tuple(attempts), status, output, elapsed_ms)
