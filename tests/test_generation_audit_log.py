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


class HumorWiringTests(unittest.TestCase):
    def test_writer_and_critic_calls_carry_prompt_and_options(self) -> None:
        import json as jsonlib

        post = pipeline.reddit.RedditPost(
            subreddit="popular", id="t3_x", title="A cat", author="/u/demo",
            url="https://example.com", updated="2026-07-19T00:00:00+00:00",
            summary="", rank=1, media_type="image", media_url="",
        )
        concept = {"top_text": "A", "bottom_text": "B", "meme_logic": "c"}
        candidates = [
            {"id": i, "mechanic": "contrast", "setup": "MEU GATO TINHA NOME", "escalation": "CADA APELIDO CRIOU OUTRO",
             "punchline": "AGORA É BEANSTER", "comic_turn": "O apelido substitui o nome ate ninguém lembrar dele",
             "scene_payoff": "cat and owner"} for i in range(1, 6)
        ]
        review = {"approved": True, "winner_id": 1,
                  "scores": {"source_fit": 9, "natural_ptbr": 9, "surprise": 9, "laugh": 9, "visual_payoff": 9},
                  "reason": "ok"}
        responses = [
            {"message": {"content": jsonlib.dumps({"candidates": candidates})}},
            {"message": {"content": jsonlib.dumps(review)}},
        ]
        with patch.object(pipeline, "request_json", side_effect=responses):
            result = pipeline.improve_humor_concept(
                post, concept, "writer-model", 5, "a cat", critic_model="critic-model"
            )
        calls = result["execution"]["generation_calls"]
        self.assertEqual([c["stage"] for c in calls], ["writer", "critic_1"])
        self.assertEqual(calls[0]["model"], "writer-model")
        self.assertIsInstance(calls[0]["prompt"], list)
        self.assertEqual(calls[0]["options"], {"temperature": 0.85, "num_predict": 1500})
        self.assertEqual(calls[1]["model"], "critic-model")


class VisionAndSourceGateWiringTests(unittest.TestCase):
    def test_describe_source_image_records_call(self) -> None:
        import tempfile
        from PIL import Image

        calls: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "photo.jpg"
            Image.new("RGB", (10, 10), color="red").save(image_path)
            with patch.object(
                pipeline, "request_json", return_value={"message": {"content": "a red square"}}
            ):
                description = pipeline.describe_source_image(image_path, "vision-model", 30, calls)
        self.assertEqual(description, "a red square")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["stage"], "vision_description")
        self.assertEqual(calls[0]["model"], "vision-model")
        self.assertIsInstance(calls[0]["prompt"], list)
        self.assertTrue(
            calls[0]["prompt"][0]["images"][0].startswith("[image omitted,")
        )

    def test_describe_source_image_without_calls_list_still_works(self) -> None:
        import tempfile
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "photo.jpg"
            Image.new("RGB", (10, 10), color="blue").save(image_path)
            with patch.object(
                pipeline, "request_json", return_value={"message": {"content": "a blue square"}}
            ):
                description = pipeline.describe_source_image(image_path, "vision-model", 30)
        self.assertEqual(description, "a blue square")

    def test_assess_source_suitability_records_call(self) -> None:
        import tempfile
        from PIL import Image
        import json as jsonlib

        post = pipeline.reddit.RedditPost(
            subreddit="popular", id="t3_y", title="A dog", author="/u/demo",
            url="https://example.com", updated="2026-07-19T00:00:00+00:00",
            summary="", rank=1, media_type="image", media_url="",
        )
        review_payload = {
            "approved": True, "reason": "ok",
            "scores": {"source_match": 5, "visual_clarity": 5, "motion_potential": 5, "text_independence": 5},
            "embedded_text_carries_meaning": False, "multi_photo_collage": False,
        }
        calls: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "photo.jpg"
            Image.new("RGB", (10, 10), color="green").save(image_path)
            with patch.object(
                pipeline, "request_json", return_value={"message": {"content": jsonlib.dumps(review_payload)}}
            ):
                pipeline.assess_source_suitability(post, image_path, "a dog photo", "critic-model", 30, calls)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["stage"], "source_suitability")
        self.assertEqual(calls[0]["model"], "critic-model")

    def test_generate_concepts_attaches_pre_concept_calls(self) -> None:
        post = pipeline.reddit.RedditPost(
            subreddit="popular", id="t3_pre", title="A bird", author="/u/demo",
            url="https://example.com", updated="2026-07-19T00:00:00+00:00",
            summary="", rank=1, media_type="image", media_url="",
        )
        pre_call = {"backend": "ollama", "stage": "vision_description", "model": "v", "state": "completed"}
        with patch.object(pipeline, "request_json", side_effect=ValueError("writer offline")):
            concepts = pipeline.generate_concepts(
                [post], "writer-model", 5,
                generation_calls_by_post={post.id: [pre_call]},
            )
        self.assertEqual(len(concepts), 1)
        self.assertEqual(concepts[0]["execution"]["generation_calls"][0], pre_call)


class LtxRenderWiringTests(unittest.TestCase):
    def test_segment_render_appends_generation_call(self) -> None:
        import tempfile

        post = pipeline.reddit.RedditPost(
            subreddit="popular", id="t3_ltx", title="A frog", author="/u/demo",
            url="https://example.com", updated="2026-07-19T00:00:00+00:00",
            summary="", rank=1, media_type="image", media_url="",
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_photo = tmp_path / "source.jpg"
            from PIL import Image
            Image.new("RGB", (100, 100), color="green").save(source_photo)
            concept = {
                "top_text": "A", "middle_text": "B", "bottom_text": "C",
                "source_media_path": str(source_photo),
            }
            output_path = tmp_path / "out.mp4"
            args = pipeline.build_parser().parse_args([
                "--ltx23-input-mode", "source", "--ltx23-audio-mode", "native",
                "--output-root", str(tmp_path),
            ])
            with patch.object(
                pipeline, "compose_ltx23_segment_prompts", return_value=["a frog on a leaf, literal prompt"]
            ), patch.object(
                pipeline, "queue_comfy_ltx23_native_video", return_value="prompt-id-1"
            ), patch.object(
                pipeline, "wait_for_comfy_video", return_value={"filename": "x", "subfolder": "", "type": "output"}
            ), patch.object(
                pipeline, "download_comfy_file"
            ):
                pipeline.render_ltx_video_meme(post, concept, str(source_photo), None, output_path, args)
        calls = concept["execution"]["generation_calls"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["backend"], "comfyui")
        self.assertEqual(calls[0]["stage"], "ltx_render")
        self.assertEqual(calls[0]["prompt"], "a frog on a leaf, literal prompt")
        self.assertEqual(calls[0]["options"]["width"], args.ltx23_width)
        self.assertEqual(calls[0]["options"]["height"], args.ltx23_height)


if __name__ == "__main__":
    unittest.main()
