from __future__ import annotations

import unittest
from unittest.mock import patch

from verification.run_daily_verification import (
    build_evaluate_argv,
    build_feedback_argv,
    build_summary_argv,
    build_verify_argv,
    main,
    parse_args,
    should_run_step,
)


class RunDailyVerificationTests(unittest.TestCase):
    def test_parse_args_defaults_to_full_mode(self) -> None:
        args = parse_args([])
        self.assertEqual(args.mode, "full")

    def test_build_verify_argv_includes_snapshot_flags(self) -> None:
        args = parse_args(["--top-n-short", "3", "--top-n-midlong", "4", "--no-snapshot"])
        argv = build_verify_argv(args)
        self.assertIn("--top-n-short", argv)
        self.assertIn("3", argv)
        self.assertIn("--top-n-midlong", argv)
        self.assertIn("4", argv)
        self.assertIn("--no-snapshot", argv)

    def test_build_evaluate_argv_includes_common_options(self) -> None:
        args = parse_args(
            [
                "--all-dates",
                "--since",
                "2026-04-10",
                "--until",
                "2026-04-20",
                "--horizons",
                "1,5",
                "--max-days",
                "2",
            ]
        )
        argv = build_evaluate_argv(args)
        self.assertIn("--all-dates", argv)
        self.assertIn("2026-04-10", argv)
        self.assertIn("2026-04-20", argv)
        self.assertIn("1,5", argv)
        self.assertIn("2", argv)

    def test_build_summary_and_feedback_argv(self) -> None:
        args = parse_args(["--weights", "70:30,50:50"])
        self.assertIn("--outcomes-csv", build_summary_argv(args))
        feedback_argv = build_feedback_argv(args)
        self.assertIn("--weights", feedback_argv)
        self.assertIn("70:30,50:50", feedback_argv)

    def test_should_run_step_uses_mode_defaults_and_skip_overrides(self) -> None:
        preopen_args = parse_args(["--mode", "preopen"])
        self.assertTrue(should_run_step(preopen_args, "verify"))
        self.assertFalse(should_run_step(preopen_args, "evaluate"))

        postclose_args = parse_args(["--mode", "postclose", "--skip-feedback"])
        self.assertTrue(should_run_step(postclose_args, "evaluate"))
        self.assertTrue(should_run_step(postclose_args, "summary"))
        self.assertFalse(should_run_step(postclose_args, "verify"))
        self.assertFalse(should_run_step(postclose_args, "feedback"))

    def test_main_runs_enabled_steps_in_order(self) -> None:
        calls: list[str] = []

        def _runner(name: str):
            def _inner(argv: list[str] | None = None) -> int:
                calls.append(name)
                return 0
            return _inner

        with patch("verification.run_daily_verification.verify_recommendations.main", side_effect=_runner("verify")), patch(
            "verification.run_daily_verification.evaluate_recommendations.main", side_effect=_runner("evaluate")
        ), patch(
            "verification.run_daily_verification.summarize_outcomes.main", side_effect=_runner("summary")
        ), patch(
            "verification.run_daily_verification.feedback_weight_sensitivity.main", side_effect=_runner("feedback")
        ):
            code = main([])

        self.assertEqual(code, 0)
        self.assertEqual(calls, ["verify", "evaluate", "summary", "feedback"])

    def test_main_runs_preopen_mode(self) -> None:
        calls: list[str] = []

        def _runner(name: str):
            def _inner(argv: list[str] | None = None) -> int:
                calls.append(name)
                return 0
            return _inner

        with patch("verification.run_daily_verification.verify_recommendations.main", side_effect=_runner("verify")), patch(
            "verification.run_daily_verification.evaluate_recommendations.main", side_effect=_runner("evaluate")
        ), patch(
            "verification.run_daily_verification.summarize_outcomes.main", side_effect=_runner("summary")
        ), patch(
            "verification.run_daily_verification.feedback_weight_sensitivity.main", side_effect=_runner("feedback")
        ):
            code = main(["--mode", "preopen"])

        self.assertEqual(code, 0)
        self.assertEqual(calls, ["verify"])

    def test_main_runs_postclose_mode(self) -> None:
        calls: list[str] = []

        def _runner(name: str):
            def _inner(argv: list[str] | None = None) -> int:
                calls.append(name)
                return 0
            return _inner

        with patch("verification.run_daily_verification.verify_recommendations.main", side_effect=_runner("verify")), patch(
            "verification.run_daily_verification.evaluate_recommendations.main", side_effect=_runner("evaluate")
        ), patch(
            "verification.run_daily_verification.summarize_outcomes.main", side_effect=_runner("summary")
        ), patch(
            "verification.run_daily_verification.feedback_weight_sensitivity.main", side_effect=_runner("feedback")
        ):
            code = main(["--mode", "postclose"])

        self.assertEqual(code, 0)
        self.assertEqual(calls, ["evaluate", "summary", "feedback"])

    def test_main_respects_skip_flags(self) -> None:
        calls: list[str] = []

        def _runner(name: str):
            def _inner(argv: list[str] | None = None) -> int:
                calls.append(name)
                return 0
            return _inner

        with patch("verification.run_daily_verification.verify_recommendations.main", side_effect=_runner("verify")), patch(
            "verification.run_daily_verification.evaluate_recommendations.main", side_effect=_runner("evaluate")
        ), patch(
            "verification.run_daily_verification.summarize_outcomes.main", side_effect=_runner("summary")
        ), patch(
            "verification.run_daily_verification.feedback_weight_sensitivity.main", side_effect=_runner("feedback")
        ):
            code = main(["--mode", "postclose", "--skip-summary", "--skip-feedback"])

        self.assertEqual(code, 0)
        self.assertEqual(calls, ["evaluate"])
