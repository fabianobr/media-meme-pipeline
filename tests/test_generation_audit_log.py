from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import daily_reddit_meme_pipeline as pipeline  # noqa: E402


class RedactPromptImagesTests(unittest.TestCase):
    def test_non_list_passthrough(self) -> None:
        self.assertEqual(pipeline.redact_prompt_images("plain text"), "plain text")
        self.assertIsNone(pipeline.redact_prompt_images(None))

    def test_redacts_images_in_messages(self) -> None:
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "describe this", "images": ["QUJDRA=="]},
        ]
        redacted = pipeline.redact_prompt_images(messages)
        self.assertEqual(redacted[0], {"role": "system", "content": "sys"})
        self.assertEqual(redacted[1]["content"], "describe this")
        self.assertEqual(redacted[1]["images"], ["[image omitted, 8 base64 chars]"])
        # original untouched
        self.assertEqual(messages[1]["images"], ["QUJDRA=="])

    def test_messages_without_images_untouched(self) -> None:
        messages = [{"role": "user", "content": "hi"}]
        self.assertEqual(pipeline.redact_prompt_images(messages), messages)


class TimedGenerationRequestTests(unittest.TestCase):
    def test_success_records_prompt_model_options_and_response(self) -> None:
        calls: list[dict] = []
        payload = {
            "model": "gemma4:31b",
            "messages": [{"role": "user", "content": "hello"}],
            "options": {"temperature": 0.7, "num_predict": 1500},
        }
        with patch.object(
            pipeline, "request_json", return_value={"message": {"content": "world"}}
        ) as mocked:
            response = pipeline.timed_generation_request(
                calls, backend="ollama", stage="writer", round_number=1,
                payload=payload, timeout=600, url="http://localhost:11434/api/chat",
            )
        self.assertEqual(response, {"message": {"content": "world"}})
        mocked.assert_called_once_with(
            "POST", "http://localhost:11434/api/chat", json=payload, timeout=600
        )
        self.assertEqual(len(calls), 1)
        record = calls[0]
        self.assertEqual(record["backend"], "ollama")
        self.assertEqual(record["stage"], "writer")
        self.assertEqual(record["round"], 1)
        self.assertEqual(record["model"], "gemma4:31b")
        self.assertEqual(record["prompt"], payload["messages"])
        self.assertEqual(record["options"], {"temperature": 0.7, "num_predict": 1500})
        self.assertEqual(record["timeout_seconds"], 600)
        self.assertEqual(record["state"], "completed")
        self.assertEqual(record["response_chars"], 5)
        self.assertEqual(record["response_preview"], "world")
        self.assertIn("elapsed_seconds", record)
        self.assertIn("started_at", record)
        self.assertIn("finished_at", record)

    def test_redacts_images_before_storing(self) -> None:
        calls: list[dict] = []
        payload = {
            "model": "qwen2.5vl:7b",
            "messages": [{"role": "user", "content": "look", "images": ["QUJDRA=="]}],
            "options": {},
        }
        with patch.object(pipeline, "request_json", return_value={"message": {"content": "ok"}}):
            pipeline.timed_generation_request(
                calls, backend="ollama", stage="vision_description",
                payload=payload, timeout=60, url="http://localhost:11434/api/chat",
            )
        self.assertEqual(calls[0]["prompt"][0]["images"], ["[image omitted, 8 base64 chars]"])

    def test_failure_records_error_and_reraises(self) -> None:
        calls: list[dict] = []
        payload = {"model": "m", "messages": [], "options": {}}
        with patch.object(pipeline, "request_json", side_effect=ValueError("offline")):
            with self.assertRaises(ValueError):
                pipeline.timed_generation_request(
                    calls, backend="ollama", stage="critic_1",
                    payload=payload, timeout=10, url="http://x/api/chat",
                )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["state"], "failed")
        self.assertIn("offline", calls[0]["error"])
        self.assertNotIn("response_preview", calls[0])

    def test_round_number_omitted_when_none(self) -> None:
        calls: list[dict] = []
        payload = {"model": "m", "messages": [], "options": {}}
        with patch.object(pipeline, "request_json", return_value={"message": {"content": ""}}):
            pipeline.timed_generation_request(
                calls, backend="ollama", stage="publish_metadata",
                payload=payload, timeout=10, url="http://x/api/chat",
            )
        self.assertNotIn("round", calls[0])


if __name__ == "__main__":
    unittest.main()
