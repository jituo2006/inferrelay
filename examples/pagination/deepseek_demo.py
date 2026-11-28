"""Call DeepSeek 4.1 Flash with sample source and preset Feedback.

This makes two real API requests. It does not execute model-generated code.
"""

from dataclasses import asdict
from getpass import getpass
import json
import os
import sys

from examples.pagination.demo import load_task
from examples.pagination.model_adapter_demo import preset_feedback
from hybrid_inference.contracts import ModelError
from hybrid_inference.models import ChatCompletionsModel


BASE_URL = "https://api.deepseek.com"
MODEL_ID = "deepseek-flash"


def run_deepseek_demo(api_key: str) -> dict:
    api_key = api_key.strip()
    if not api_key:
        raise ValueError("API Key must not be empty.")
    model = ChatCompletionsModel(
        base_url=BASE_URL, model=MODEL_ID, api_key=api_key, timeout_seconds=180,
    )
    task = load_task()
    attempts = []
    for stage, feedback in (("initial", None), ("preset_feedback", preset_feedback())):
        reply = model(task, feedback)
        attempts.append({"stage": stage, "reply": asdict(reply)})
    return {
        "backend": "deepseek_api",
        "configured_model": MODEL_ID,
        "task_id": task.task_id,
        "status": "responses_received",
        "patch_verification": "not_run",
        "attempts": attempts,
    }


def main() -> int:
    try:
        api_key = os.getenv("DEEPSEEK_API_KEY") or getpass("DeepSeek API Key (hidden): ")
        if not api_key.strip():
            raise ValueError("API Key must not be empty.")
        print(
            "Calling DeepSeek 4.1 Flash twice; API usage may be billed. "
            "Returned patches will NOT be executed or verified.",
            file=sys.stderr, flush=True,
        )
        result = run_deepseek_demo(api_key)
    except (EOFError, KeyboardInterrupt):
        print("Cancelled.", file=sys.stderr)
        return 2
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    except ModelError as error:
        print(json.dumps({"status": "model_error", "reason": str(error)}))
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
