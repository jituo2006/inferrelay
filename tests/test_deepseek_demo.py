import io
import json
import unittest
from unittest.mock import patch

from examples.pagination.deepseek_demo import main, run_deepseek_demo
from examples.pagination.demo import load_task
from examples.pagination.model_adapter_demo import preset_feedback
from hybrid_inference.contracts import ModelError, ModelReply


class DeepSeekDemoTests(unittest.TestCase):
    @patch("examples.pagination.deepseek_demo.ChatCompletionsModel")
    def test_fixed_model_receives_source_and_preset_feedback(self, factory):
        factory.return_value.side_effect = [
            ModelReply("deepseek-flash", "first patch", "stop"),
            ModelReply("deepseek-flash", "revised patch", "stop"),
        ]
        result = run_deepseek_demo("local-test-key")
        factory.assert_called_once_with(
            base_url="https://api.deepseek.com", model="deepseek-flash",
            api_key="local-test-key", timeout_seconds=180,
        )
        calls = factory.return_value.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].args, (load_task(), None))
        self.assertEqual(calls[1].args, (load_task(), preset_feedback()))
        self.assertEqual(result["status"], "responses_received")
        self.assertEqual(result["patch_verification"], "not_run")
        self.assertEqual(result["attempts"][1]["reply"]["text"], "revised patch")
        self.assertNotIn("local-test-key", json.dumps(result))

    @patch("examples.pagination.deepseek_demo.ChatCompletionsModel")
    def test_blank_key_is_rejected_before_creating_a_client(self, factory):
        with self.assertRaisesRegex(ValueError, "API Key must not be empty"):
            run_deepseek_demo(" ")
        factory.assert_not_called()

    @patch("examples.pagination.deepseek_demo.ChatCompletionsModel")
    def test_provider_error_stops_without_a_second_request(self, factory):
        factory.return_value.side_effect = ModelError("http_401")
        with self.assertRaisesRegex(ModelError, "http_401"):
            run_deepseek_demo("local-test-key")
        self.assertEqual(factory.return_value.call_count, 1)

    @patch("examples.pagination.deepseek_demo.os.getenv", return_value="")
    @patch("examples.pagination.deepseek_demo.getpass", return_value="local-test-key")
    @patch("examples.pagination.deepseek_demo.run_deepseek_demo", return_value={"status": "responses_received"})
    def test_one_command_prompts_for_a_hidden_key(self, run, prompt, getenv):
        output = io.StringIO()
        with patch("sys.stdout", output), patch("sys.stderr", io.StringIO()):
            code = main()
        self.assertEqual(code, 0)
        prompt.assert_called_once()
        run.assert_called_once_with("local-test-key")
        self.assertNotIn("local-test-key", output.getvalue())

    @patch("examples.pagination.deepseek_demo.os.getenv", return_value="local-test-key")
    @patch("examples.pagination.deepseek_demo.getpass")
    @patch("examples.pagination.deepseek_demo.run_deepseek_demo", return_value={"status": "responses_received"})
    def test_optional_single_environment_variable_skips_prompt(self, run, prompt, getenv):
        with patch("sys.stdout", io.StringIO()), patch("sys.stderr", io.StringIO()):
            self.assertEqual(main(), 0)
        prompt.assert_not_called()
        run.assert_called_once_with("local-test-key")

    @patch("examples.pagination.deepseek_demo.os.getenv", return_value="local-test-key")
    @patch("examples.pagination.deepseek_demo.run_deepseek_demo", side_effect=ModelError("http_401"))
    def test_cli_reports_model_error_without_exposing_key(self, run, getenv):
        output = io.StringIO()
        with patch("sys.stdout", output), patch("sys.stderr", io.StringIO()):
            self.assertEqual(main(), 1)
        self.assertEqual(json.loads(output.getvalue()), {"status": "model_error", "reason": "http_401"})
        self.assertNotIn("local-test-key", output.getvalue())


if __name__ == "__main__":
    unittest.main()
