from __future__ import annotations

import argparse
import io
import logging
import sys
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run unittest discovery quietly unless tests fail.")
    parser.add_argument("-s", "--start-directory", default="tests")
    parser.add_argument("-p", "--pattern", default="test*.py")
    parser.add_argument("-t", "--top-level-directory", default=None)
    return parser.parse_args(argv)


def _restore_logging(root_logger: logging.Logger, old_handlers: list[logging.Handler], old_level: int) -> None:
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        handler.close()
    for handler in old_handlers:
        root_logger.addHandler(handler)
    root_logger.setLevel(old_level)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    real_stdout = sys.stdout
    real_stderr = sys.stderr
    captured_stdout = io.StringIO()
    captured_stderr = io.StringIO()
    runner_output = io.StringIO()
    root_logger = logging.getLogger()
    old_handlers = list(root_logger.handlers)
    old_level = root_logger.level
    log_handler = logging.StreamHandler(captured_stderr)
    log_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))

    sys.stdout = captured_stdout
    sys.stderr = captured_stderr
    for handler in old_handlers:
        root_logger.removeHandler(handler)
    root_logger.addHandler(log_handler)
    root_logger.setLevel(logging.INFO)
    started_at = time.perf_counter()

    try:
        loader = unittest.defaultTestLoader
        suite = loader.discover(
            start_dir=str(Path(args.start_directory)),
            pattern=args.pattern,
            top_level_dir=args.top_level_directory,
        )
        result = unittest.TextTestRunner(stream=runner_output, verbosity=1, buffer=True).run(suite)
    finally:
        elapsed = time.perf_counter() - started_at
        sys.stdout = real_stdout
        sys.stderr = real_stderr
        _restore_logging(root_logger, old_handlers, old_level)

    if result.wasSuccessful():
        print(f"OK: Ran {result.testsRun} tests in {elapsed:.3f}s")
        return 0

    real_stderr.write(runner_output.getvalue())
    extra_output = captured_stdout.getvalue()
    extra_error = captured_stderr.getvalue()
    if extra_output:
        real_stderr.write("\n--- captured stdout ---\n")
        real_stderr.write(extra_output)
    if extra_error:
        real_stderr.write("\n--- captured stderr/logs ---\n")
        real_stderr.write(extra_error)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
