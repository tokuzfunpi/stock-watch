from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from stock_watch.state import run_state


class RunStateTests(unittest.TestCase):
    def test_save_last_success_date_preserves_scoped_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            success_file = Path(tmpdir) / "last_success.json"
            run_state.save_last_success_date(
                success_file=success_file,
                success_date="2026-05-04",
                signature="sig-preopen",
                success_scope="preopen",
            )
            run_state.save_last_success_date(
                success_file=success_file,
                success_date="2026-05-04",
                signature="sig-postclose",
                success_scope="postclose",
            )
            payload = json.loads(success_file.read_text(encoding="utf-8"))

        self.assertEqual(payload["date"], "2026-05-04")
        self.assertEqual(payload["signature"], "sig-postclose")
        self.assertEqual(payload["scopes"]["preopen"]["signature"], "sig-preopen")
        self.assertEqual(payload["scopes"]["postclose"]["signature"], "sig-postclose")

    def test_load_last_success_date_and_signature_support_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            success_file = Path(tmpdir) / "last_success.json"
            success_file.write_text(
                json.dumps(
                    {
                        "date": "2026-05-04",
                        "signature": "sig-default",
                        "scopes": {
                            "preopen": {"date": "2026-05-04", "signature": "sig-preopen"},
                            "postclose": {"date": "2026-05-04", "signature": "sig-postclose"},
                        },
                    }
                ),
                encoding="utf-8",
            )

            default_date = run_state.load_last_success_date(success_file=success_file)
            preopen_date = run_state.load_last_success_date(success_file=success_file, success_scope="preopen")
            postclose_sig = run_state.load_last_success_signature(success_file=success_file, success_scope="postclose")

        self.assertEqual(default_date, "2026-05-04")
        self.assertEqual(preopen_date, "2026-05-04")
        self.assertEqual(postclose_sig, "sig-postclose")


if __name__ == "__main__":
    unittest.main()
