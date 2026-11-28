from dataclasses import replace
import unittest

from examples.pagination.demo import load_task, run_demo
from hybrid_inference.contracts import SourceFile, Task
from hybrid_inference.core import CodingTaskRouter, ThresholdRouter


class CodingTaskRouterTests(unittest.TestCase):
    def test_pagination_uses_metadata_instead_of_manual_difficulty(self):
        task = replace(load_task(), difficulty=0.9)
        route = CodingTaskRouter()(task)

        self.assertEqual((route.model, route.score, route.threshold), ("fast", 0.25, 0.7))
        self.assertIn("edit scope 0 (1 allowed paths)", route.reason)
        self.assertIn("source size 0 (3 source lines)", route.reason)
        self.assertEqual(ThresholdRouter(0.7)(task).model, "strong")

    def test_edit_scope_counts_unique_paths_and_is_capped(self):
        task = load_task()
        for paths, expected in (
            (("a.py", "a.py"), ("fast", 0.25)),
            (("a.py", "b.py"), ("fast", 0.5)),
            (("a.py", "b.py", "c.py", "d.py"), ("strong", 0.75)),
        ):
            with self.subTest(paths=paths):
                route = CodingTaskRouter()(replace(task, allowed_paths=paths))
                self.assertEqual((route.model, route.score), expected)

    def test_source_size_bands_include_read_only_context(self):
        task = load_task()
        for lines, score in ((200, 0.25), (201, 0.5), (500, 0.5), (501, 0.75)):
            with self.subTest(lines=lines):
                files = task.files + (SourceFile("read_only.py", "pass\n" * (lines - 3)),)
                route = CodingTaskRouter()(replace(task, files=files))
                self.assertEqual(route.score, score)
                self.assertIn(f"({lines} source lines)", route.reason)

    def test_combined_score_is_capped(self):
        task = replace(
            load_task(),
            allowed_paths=("pagination.py", "a.py", "b.py"),
            files=(SourceFile("pagination.py", "pass\n" * 501),),
        )
        route = CodingTaskRouter(threshold=1)(task)
        self.assertEqual((route.model, route.score), ("strong", 1.0))
        self.assertIn("Capped at 100 points", route.reason)

    def test_incomplete_metadata_falls_back_to_manual_difficulty(self):
        task = replace(load_task(), difficulty=0.9)
        for incomplete in (replace(task, files=()), replace(task, allowed_paths=())):
            with self.subTest(task=incomplete):
                route = CodingTaskRouter()(incomplete)
                self.assertEqual((route.model, route.score), ("strong", 0.9))
                self.assertIn("using Task.difficulty", route.reason)

    def test_invalid_threshold_or_fallback_score_is_rejected(self):
        with self.assertRaises(ValueError):
            CodingTaskRouter(threshold=float("nan"))(load_task())
        with self.assertRaises(ValueError):
            CodingTaskRouter()(Task("No metadata", difficulty=1.1))

    def test_pagination_can_start_strong_and_pass_on_first_attempt(self):
        run = run_demo(threshold=0.25)

        self.assertEqual((run.route.model, run.route.score, run.route.threshold), ("strong", 0.25, 0.25))
        self.assertEqual(run.status, "passed")
        self.assertEqual([(a.role, a.decision) for a in run.attempts], [("strong", "accept")])


if __name__ == "__main__":
    unittest.main()
