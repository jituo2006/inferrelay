from dataclasses import dataclass
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
    "Role", "Route", "Run", "Task", "CodingTaskRouter", "ThresholdRouter", "Usage", "run_task",
]


def _threshold_route(score: float, threshold: float, reason: str | None = None) -> Route:
    if not 0 <= score <= 1 or not 0 <= threshold <= 1:
        raise ValueError("Difficulty and threshold must be between 0 and 1.")
    model: Role = "strong" if score >= threshold else "fast"
    return Route(model, score, threshold, reason)


@dataclass(frozen=True)
class ThresholdRouter:
    threshold: float

    def __call__(self, task: Task) -> Route:
        return _threshold_route(task.difficulty, self.threshold)


@dataclass(frozen=True)
class CodingTaskRouter:
    """Estimate first-attempt difficulty from the edit scope and source context."""

    threshold: float = 0.7

    def __call__(self, task: Task) -> Route:
        if not task.allowed_paths or not task.files:
            return _threshold_route(
                task.difficulty, self.threshold,
                "Missing allowed paths or source snapshots; using Task.difficulty.",
            )

        path_count = len(set(task.allowed_paths))
        line_count = sum(len(source.text.splitlines()) for source in task.files)
        scope_points = min(path_count - 1, 2) * 25
        size_points = 50 if line_count > 500 else 25 if line_count > 200 else 0
        points = 25 + scope_points + size_points
        reason = (
            f"Base 25 + edit scope {scope_points} ({path_count} allowed paths) "
            f"+ source size {size_points} ({line_count} source lines) = {points} points."
        )
        if points > 100:
            reason += " Capped at 100 points."
        return _threshold_route(min(points, 100) / 100, self.threshold, reason)


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
            check = verify(reply.text)

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
