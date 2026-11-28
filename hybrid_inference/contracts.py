"""Data shared by the router, model adapter, verifier, and scheduler."""

from dataclasses import dataclass
from typing import Callable, Literal


Role = Literal["fast", "strong"]
Policy = Literal["fixed", "feedback"]
FailureKind = Literal["candidate_error", "test_failed", "environment_error"]
RunStatus = Literal["passed", "failed", "model_error", "verification_error"]


@dataclass(frozen=True)
class SourceFile:
    path: str
    text: str


@dataclass(frozen=True)
class Task:
    prompt: str
    difficulty: float
    task_id: str | None = None
    base_revision: str | None = None
    allowed_paths: tuple[str, ...] = ()
    files: tuple[SourceFile, ...] = ()
    check_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Route:
    model: Role
    score: float
    threshold: float
    reason: str | None = None


@dataclass(frozen=True)
class Check:
    passed: bool
    code: str
    summary: str
    kind: FailureKind | None = None


@dataclass(frozen=True)
class Feedback:
    candidate: str | None
    check: Check


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None


@dataclass(frozen=True)
class ModelReply:
    model: str
    text: str | None
    finish_reason: str | None = None
    usage: Usage = Usage()


class ModelError(Exception):
    """A transport or response error, distinct from a failed candidate check."""


Router = Callable[[Task], Route]
Model = Callable[[Task, Feedback | None], ModelReply]
PatchVerifier = Callable[[Task, str], Check]


@dataclass(frozen=True)
class Attempt:
    role: Role
    model: str | None
    model_ms: float
    finish_reason: str | None
    usage: Usage | None
    check: Check | None
    decision: str
    reason: str


@dataclass(frozen=True)
class Run:
    route: Route
    policy: Policy
    attempts: tuple[Attempt, ...]
    status: RunStatus
    output: str | None
    elapsed_ms: float
