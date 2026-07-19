from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import render_audit_report as report  # noqa: E402


def make_record(title: str, calls: list[dict] | None = None, legacy_key: bool = False) -> dict:
    execution: dict = {"state": "approved", "attempts": {}}
    if calls is not None:
        execution["llm_calls" if legacy_key else "generation_calls"] = calls
    return {
        "schema_version": 3,
        "post": {"title": title},
        "execution": execution,
    }


CALL = {
    "backend": "ollama",
    "stage": "writer",
    "model": "gemma4:31b",
    "prompt": [{"role": "user", "content": "hi"}],
    "options": {"temperature": 0.85},
    "state": "completed",
    "elapsed_seconds": 1.234,
    "response_preview": "candidatos aqui",
}


class RenderCallTests(unittest.TestCase):
    def test_includes_model_stage_and_prompt(self) -> None:
        text = report.render_call(CALL)
        self.assertIn("gemma4:31b", text)
        self.assertIn("writer", text)
        self.assertIn("temperature=0.85", text)
        self.assertIn("candidatos aqui", text)

    def test_handles_string_prompt(self) -> None:
        call = dict(CALL, prompt="literal ltx prompt text")
        text = report.render_call(call)
        self.assertIn("literal ltx prompt text", text)

    def test_includes_error_when_present(self) -> None:
        call = dict(CALL, state="failed", error="timeout")
        text = report.render_call(call)
        self.assertIn("timeout", text)


class RenderVideoSectionTests(unittest.TestCase):
    def test_video_with_calls(self) -> None:
        record = make_record("A cat video", calls=[CALL])
        text = report.render_video_section(1, record)
        self.assertIn("1. A cat video", text)
        self.assertIn("writer", text)

    def test_video_without_calls_shows_placeholder(self) -> None:
        record = make_record("No calls video", calls=None)
        text = report.render_video_section(2, record)
        self.assertIn("2. No calls video", text)
        self.assertIn("Nenhuma chamada", text)

    def test_legacy_llm_calls_key_still_renders(self) -> None:
        record = make_record("Old run video", calls=[CALL], legacy_key=True)
        text = report.render_video_section(3, record)
        self.assertIn("writer", text)


class RenderAuditReportTests(unittest.TestCase):
    def test_full_document_has_one_section_per_video(self) -> None:
        document = [make_record("First", calls=[CALL]), make_record("Second", calls=[CALL])]
        text = report.render_audit_report(document)
        self.assertIn("1. First", text)
        self.assertIn("2. Second", text)

    def test_empty_document(self) -> None:
        text = report.render_audit_report([])
        self.assertIn("vazio", text)


class MainCliTests(unittest.TestCase):
    def test_main_writes_output_file(self) -> None:
        document = [make_record("CLI video", calls=[CALL])]
        with tempfile.TemporaryDirectory() as tmp:
            concepts_path = Path(tmp) / "concepts.json"
            concepts_path.write_text(json.dumps(document), encoding="utf-8")
            exit_code = report.main_with_args(["--concepts-file", str(concepts_path)])
            self.assertEqual(exit_code, 0)
            output_path = Path(tmp) / "audit-report.md"
            self.assertTrue(output_path.is_file())
            self.assertIn("CLI video", output_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
