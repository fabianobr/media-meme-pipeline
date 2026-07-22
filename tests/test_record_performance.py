from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import record_performance as perf  # noqa: E402


class ParseMetricTests(unittest.TestCase):
    def test_parse_int_value(self) -> None:
        key, value = perf.parse_metric("views=1200")
        self.assertEqual(key, "views")
        self.assertEqual(value, 1200)
        self.assertIsInstance(value, int)

    def test_parse_float_value(self) -> None:
        key, value = perf.parse_metric("engagement=0.85")
        self.assertEqual(key, "engagement")
        self.assertEqual(value, 0.85)
        self.assertIsInstance(value, float)

    def test_parse_string_value(self) -> None:
        key, value = perf.parse_metric("status=n/a")
        self.assertEqual(key, "status")
        self.assertEqual(value, "n/a")
        self.assertIsInstance(value, str)

    def test_parse_malformed_no_equals(self) -> None:
        result = perf.parse_metric("invalid_format")
        self.assertIsNone(result)

    def test_parse_negative_int(self) -> None:
        key, value = perf.parse_metric("delta=-42")
        self.assertEqual(key, "delta")
        self.assertEqual(value, -42)
        self.assertIsInstance(value, int)

    def test_parse_negative_float(self) -> None:
        key, value = perf.parse_metric("ratio=-1.5")
        self.assertEqual(key, "ratio")
        self.assertEqual(value, -1.5)
        self.assertIsInstance(value, float)


class RecordPerformanceTests(unittest.TestCase):
    def test_create_new_log_file_with_first_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "performance-log.json"
            exit_code = perf.main_with_args([
                "--publish-id", "phase1-01",
                "--platform", "youtube_shorts",
                "--metric", "views=1200",
                "--log-file", str(log_path),
            ])
            self.assertEqual(exit_code, 0)
            self.assertTrue(log_path.is_file())

            data = json.loads(log_path.read_text(encoding="utf-8"))
            self.assertIsInstance(data, list)
            self.assertEqual(len(data), 1)

            record = data[0]
            self.assertEqual(record["publish_id"], "phase1-01")
            self.assertEqual(record["platform"], "youtube_shorts")
            self.assertEqual(record["metrics"]["views"], 1200)
            self.assertIn("captured_at", record)

    def test_append_to_existing_log_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "performance-log.json"

            # First call
            exit_code1 = perf.main_with_args([
                "--publish-id", "phase1-01",
                "--platform", "youtube_shorts",
                "--metric", "views=1200",
                "--log-file", str(log_path),
            ])
            self.assertEqual(exit_code1, 0)

            # Second call
            exit_code2 = perf.main_with_args([
                "--publish-id", "phase1-01",
                "--platform", "youtube_shorts",
                "--metric", "views=1500",
                "--log-file", str(log_path),
            ])
            self.assertEqual(exit_code2, 0)

            data = json.loads(log_path.read_text(encoding="utf-8"))
            self.assertEqual(len(data), 2)
            self.assertEqual(data[0]["metrics"]["views"], 1200)
            self.assertEqual(data[1]["metrics"]["views"], 1500)

    def test_multiple_metrics_in_one_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "performance-log.json"
            exit_code = perf.main_with_args([
                "--publish-id", "phase1-02",
                "--platform", "tiktok",
                "--metric", "views=5000",
                "--metric", "likes=250",
                "--metric", "comments=15",
                "--log-file", str(log_path),
            ])
            self.assertEqual(exit_code, 0)

            data = json.loads(log_path.read_text(encoding="utf-8"))
            record = data[0]
            self.assertEqual(record["metrics"]["views"], 5000)
            self.assertEqual(record["metrics"]["likes"], 250)
            self.assertEqual(record["metrics"]["comments"], 15)

    def test_numeric_and_string_metrics_mixed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "performance-log.json"
            exit_code = perf.main_with_args([
                "--publish-id", "phase1-03",
                "--platform", "instagram_reels",
                "--metric", "views=3000",
                "--metric", "saves=120",
                "--metric", "status=approved",
                "--metric", "engagement_rate=0.04",
                "--log-file", str(log_path),
            ])
            self.assertEqual(exit_code, 0)

            data = json.loads(log_path.read_text(encoding="utf-8"))
            record = data[0]
            self.assertEqual(record["metrics"]["views"], 3000)
            self.assertEqual(record["metrics"]["saves"], 120)
            self.assertEqual(record["metrics"]["status"], "approved")
            self.assertEqual(record["metrics"]["engagement_rate"], 0.04)

    def test_malformed_metric_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "performance-log.json"
            exit_code = perf.main_with_args([
                "--publish-id", "phase1-04",
                "--platform", "youtube_shorts",
                "--metric", "invalid_no_equals",
                "--log-file", str(log_path),
            ])
            self.assertEqual(exit_code, 1)
            self.assertFalse(log_path.is_file())

    def test_malformed_metric_leaves_existing_log_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "performance-log.json"
            perf.main_with_args([
                "--publish-id", "p1",
                "--platform", "youtube_shorts",
                "--metric", "views=100",
                "--log-file", str(log_path),
            ])
            before = log_path.read_text(encoding="utf-8")

            exit_code = perf.main_with_args([
                "--publish-id", "p1",
                "--platform", "youtube_shorts",
                "--metric", "broken",
                "--log-file", str(log_path),
            ])

            self.assertEqual(exit_code, 1)
            self.assertEqual(log_path.read_text(encoding="utf-8"), before)

    def test_captured_at_is_iso8601_timezone_aware(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "performance-log.json"
            before = datetime.now(timezone.utc)

            exit_code = perf.main_with_args([
                "--publish-id", "phase1-05",
                "--platform", "youtube_shorts",
                "--metric", "views=100",
                "--log-file", str(log_path),
            ])
            self.assertEqual(exit_code, 0)

            after = datetime.now(timezone.utc)
            data = json.loads(log_path.read_text(encoding="utf-8"))
            record = data[0]

            # Parse the ISO 8601 string
            captured = datetime.fromisoformat(record["captured_at"])
            self.assertIsNotNone(captured.tzinfo)
            self.assertGreaterEqual(captured, before)
            self.assertLessEqual(captured, after)

    def test_default_log_file_path(self) -> None:
        """Test that without --log-file, main_with_args resolves the default to
        data/media-pipeline/performance-log.json relative to the repo root.

        build_parser() itself leaves --log-file as None (the resolution happens
        in main_with_args), so this exercises main_with_args end-to-end against a
        faked module location to avoid touching the real repo's data directory.
        """
        original_file = perf.__file__
        try:
            with tempfile.TemporaryDirectory() as tmp:
                fake_scripts_dir = Path(tmp) / "scripts"
                fake_scripts_dir.mkdir()
                perf.__file__ = str(fake_scripts_dir / "record_performance.py")

                exit_code = perf.main_with_args([
                    "--publish-id", "x", "--platform", "y", "--metric", "views=1",
                ])

                self.assertEqual(exit_code, 0)
                expected = Path(tmp) / "data" / "media-pipeline" / "performance-log.json"
                self.assertTrue(expected.is_file())
        finally:
            perf.__file__ = original_file

    def test_stdout_confirmation_message(self) -> None:
        """Test that the script prints a confirmation message."""
        import io
        from contextlib import redirect_stdout

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "performance-log.json"

            stdout_capture = io.StringIO()
            with redirect_stdout(stdout_capture):
                exit_code = perf.main_with_args([
                    "--publish-id", "phase1-06",
                    "--platform", "youtube_shorts",
                    "--metric", "views=1000",
                    "--log-file", str(log_path),
                ])

            self.assertEqual(exit_code, 0)
            output = stdout_capture.getvalue()
            self.assertIn("phase1-06", output)
            self.assertIn("youtube_shorts", output)
            self.assertIn("1 metric", output)


if __name__ == "__main__":
    unittest.main()
