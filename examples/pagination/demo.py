"""Run the scheduler against the two bundled pagination patches."""

import argparse
from dataclasses import asdict
from functools import partial
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

from hybrid_inference.contracts import Check, Feedback, Model, ModelReply, Run, SourceFile, Task
from hybrid_inference.core import CodingTaskRouter, run_task


FIXTURE = Path(__file__).resolve().parent


def load_task() -> Task:
    data = json.loads((FIXTURE / "task.json").read_text(encoding="utf-8"))
    return Task(
        task_id=data["task_id"],
        base_revision=data["base_revision"],
        prompt=data["prompt"],
        difficulty=data["difficulty"],
        files=tuple(
            SourceFile(path, (FIXTURE / "base" / path).read_text(encoding="utf-8"))
            for path in data["source_paths"]
        ),
        allowed_paths=tuple(data["allowed_paths"]),
        check_ids=tuple(data["check_ids"]),
    )


def verify_fixture(task: Task, candidate: str) -> Check:
    if candidate not in (
        (FIXTURE / "partial.patch").read_text(encoding="utf-8"),
        (FIXTURE / "complete.patch").read_text(encoding="utf-8"),
    ):
        return Check(False, "unknown_fixture_patch", "Select a bundled patch.", "candidate_error")

    try:
        with TemporaryDirectory(prefix="inferrelay-pagination-") as directory:
            workspace = Path(directory)
            for source in task.files:
                (workspace / source.path).write_text(source.text, encoding="utf-8")
            shutil.copyfile(FIXTURE / "pagination_checks.py", workspace / "pagination_checks.py")

            subprocess.run(
                ["git", "apply", "--whitespace=error", "-"],
                cwd=workspace, input=candidate, text=True, encoding="utf-8",
                capture_output=True, check=True, timeout=10,
            )
            checks = subprocess.run(
                [sys.executable, "pagination_checks.py"],
                cwd=workspace, text=True, encoding="utf-8", capture_output=True,
                check=True, timeout=10,
            )
            failed_ids = json.loads(checks.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        detail = error.stderr if isinstance(error, subprocess.CalledProcessError) else None
        detail = detail or str(error)
        return Check(False, "fixture_execution_error", detail.strip(), "environment_error")

    if failed_ids:
        joined_ids = ",".join(failed_ids)
        return Check(
            False, f"test_failed:{joined_ids}",
            "Failing pagination checks: " + ", ".join(failed_ids), "test_failed",
        )
    return Check(True, "passed", "All pagination checks passed.")


def fixture_model(name: str, filename: str) -> Model:
    patch = (FIXTURE / filename).read_text(encoding="utf-8")

    def generate(_task: Task, _feedback: Feedback | None) -> ModelReply:
        return ModelReply(name, patch, finish_reason="stop")

    return generate


def run_demo(*, threshold: float = 0.7) -> Run:
    task = load_task()
    return run_task(
        task,
        router=CodingTaskRouter(threshold),
        models={
            "fast": fixture_model("fixture-fast", "partial.patch"),
            "strong": fixture_model("fixture-strong", "complete.patch"),
        },
        verify=partial(verify_fixture, task),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold", type=float, default=0.7, help="Minimum score for a strong start.")
    args = parser.parse_args()
    if not 0 <= args.threshold <= 1:
        parser.error("--threshold must be between 0 and 1")
    result = run_demo(threshold=args.threshold)
    print(json.dumps(asdict(result), indent=2))
    return 0 if result.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
