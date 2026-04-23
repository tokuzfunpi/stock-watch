from __future__ import annotations

import unittest
from unittest.mock import patch

from run_local_daily import build_verification_argv
from run_local_daily import main
from run_local_daily import parse_args
from run_local_daily import should_run_step


class RunLocalDailyTests(unittest.TestCase):
    def test_parse_args_defaults_to_full_mode(self) -> None:
        args = parse_args([])
        self.assertEqual(args.mode, "full")

    def test_should_run_step_uses_mode_defaults_and_skip_overrides(self) -> None:
        preopen_args = parse_args(["--mode", "preopen"])
        self.assertTrue(should_run_step(preopen_args, "watchlist"))
        self.assertTrue(should_run_step(preopen_args, "verification"))
        self.assertFalse(should_run_step(preopen_args, "portfolio"))

        postclose_args = parse_args(["--mode", "postclose", "--skip-portfolio"])
        self.assertTrue(should_run_step(postclose_args, "watchlist"))
        self.assertTrue(should_run_step(postclose_args, "verification"))
        self.assertFalse(should_run_step(postclose_args, "portfolio"))

    def test_build_verification_argv_maps_local_mode(self) -> None:
        args = parse_args(
            [
                "--mode",
                "postclose",
                "--horizons",
                "1,5",
                "--weights",
                "70:30,60:40",
                "--all-dates",
            ]
        )

        argv = build_verification_argv(args)

        self.assertIn("--mode", argv)
        self.assertIn("postclose", argv)
        self.assertIn("1,5", argv)
        self.assertIn("70:30,60:40", argv)
        self.assertIn("--all-dates", argv)

    def test_main_runs_preopen_steps_in_order(self) -> None:
        calls: list[str] = []

        def _runner(name: str):
            def _inner(argv: list[str] | None = None) -> int:
                calls.append(name)
                return 0

            return _inner

        with patch("run_local_daily.daily_theme_watchlist.main", side_effect=_runner("watchlist")), patch(
            "run_local_daily.portfolio_check.main", side_effect=_runner("portfolio")
        ), patch("run_local_daily.run_daily_verification.main", side_effect=_runner("verification")):
            code = main(["--mode", "preopen"])

        self.assertEqual(code, 0)
        self.assertEqual(calls, ["watchlist", "verification"])

    def test_main_runs_postclose_steps_in_order(self) -> None:
        calls: list[str] = []

        def _runner(name: str):
            def _inner(argv: list[str] | None = None) -> int:
                calls.append(name)
                return 0

            return _inner

        with patch("run_local_daily.daily_theme_watchlist.main", side_effect=_runner("watchlist")), patch(
            "run_local_daily.portfolio_check.main", side_effect=_runner("portfolio")
        ), patch("run_local_daily.run_daily_verification.main", side_effect=_runner("verification")):
            code = main(["--mode", "postclose"])

        self.assertEqual(code, 0)
        self.assertEqual(calls, ["watchlist", "portfolio", "verification"])

    def test_main_runs_portfolio_only_mode(self) -> None:
        calls: list[str] = []

        def _runner(name: str):
            def _inner(argv: list[str] | None = None) -> int:
                calls.append(name)
                return 0

            return _inner

        with patch("run_local_daily.daily_theme_watchlist.main", side_effect=_runner("watchlist")), patch(
            "run_local_daily.portfolio_check.main", side_effect=_runner("portfolio")
        ), patch("run_local_daily.run_daily_verification.main", side_effect=_runner("verification")):
            code = main(["--mode", "portfolio"])

        self.assertEqual(code, 0)
        self.assertEqual(calls, ["portfolio"])
