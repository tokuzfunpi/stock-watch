from __future__ import annotations

import unittest
from unittest.mock import patch

from stock_watch.cli.main import main


class StockWatchCliTests(unittest.TestCase):
    def test_daily_command_delegates_to_local_daily(self) -> None:
        with patch("stock_watch.cli.main.local_daily.main", return_value=0) as mock_daily:
            code = main(["daily", "--mode", "preopen"])

        self.assertEqual(code, 0)
        mock_daily.assert_called_once_with(["--mode", "preopen"])

    def test_portfolio_alias_delegates_to_daily_portfolio_mode(self) -> None:
        with patch("stock_watch.cli.main.local_daily.main", return_value=0) as mock_daily:
            code = main(["portfolio"])

        self.assertEqual(code, 0)
        mock_daily.assert_called_once_with(["--mode", "portfolio"])

    def test_verification_subcommand_delegates_to_verification_cli(self) -> None:
        with patch("stock_watch.cli.main.summarize_outcomes.main", return_value=0) as mock_summary:
            code = main(["verification", "summary", "--outcomes-csv", "sample.csv"])

        self.assertEqual(code, 0)
        mock_summary.assert_called_once_with(["--outcomes-csv", "sample.csv"])

    def test_unknown_command_returns_usage_error(self) -> None:
        code = main(["unknown"])

        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
