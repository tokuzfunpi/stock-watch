from __future__ import annotations

import unittest

from verification.backfill_from_git import parse_git_log_dates


class BackfillFromGitTests(unittest.TestCase):
    def test_parse_git_log_dates_parses_sha_and_date(self) -> None:
        text = "\n".join(
            [
                "acbe56600000000000000000000000000000000 2026-04-19",
                "db6307c00000000000000000000000000000000 2026-04-15",
                "badline",
                "",
            ]
        )
        items = parse_git_log_dates(text)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].signal_date, "2026-04-19")
        self.assertTrue(items[0].commit_sha.startswith("acbe566"))

