from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import daily_reddit_meme_pipeline as pipeline  # noqa: E402
import reddit_meme_dry_run as reddit  # noqa: E402


def make_post(**overrides) -> reddit.RedditPost:
    fields = {
        "subreddit": "brasil",
        "id": "t3_abc123",
        "title": "Gato dormiu em cima do mapa do Brasil",
        "author": "u_teste",
        "url": "https://reddit.com/r/brasil/t3_abc123",
        "updated": "2026-07-19T00:00:00+00:00",
        "summary": "",
        "rank": 1,
        "media_type": "image",
        "media_url": "https://i.redd.it/abc123.jpg",
    }
    fields.update(overrides)
    return reddit.RedditPost(**fields)


def make_concept(**overrides) -> dict:
    concept = {
        "top_text": "EU ABRI O MAPA DO BRASIL",
        "middle_text": "O GATO DEITOU EM CIMA DE TUDO",
        "bottom_text": "AGORA O PAIS TEM NOVO DONO",
        "source_brief": "Foto real de um gato deitado sobre um mapa aberto.",
        "source_visual_description": "Um gato laranja dormindo sobre um mapa de papel numa mesa.",
        "humor_approved": True,
        "quality_approved": True,
    }
    concept.update(overrides)
    return concept


VALID_CANDIDATE = {
    "title": "O gato decidiu que o mapa agora é dele",
    "description": "Ele só queria um lugar quentinho. Escolheu um país inteiro.",
    "interest_topics": ["gatos", "humor absurdo", "flagras de animais"],
    "hashtags": ["#gatos", "#humor", "#pets", "#brasil"],
}


class PublishValidationTests(unittest.TestCase):
    def test_accepts_valid_candidate(self) -> None:
        self.assertEqual(pipeline.publish_metadata_issues(dict(VALID_CANDIDATE)), [])

    def test_rejects_non_dict(self) -> None:
        self.assertTrue(pipeline.publish_metadata_issues(None))
        self.assertTrue(pipeline.publish_metadata_issues([VALID_CANDIDATE]))

    def test_rejects_long_title(self) -> None:
        candidate = dict(VALID_CANDIDATE, title="x" * 101)
        self.assertTrue(any("title" in issue for issue in pipeline.publish_metadata_issues(candidate)))

    def test_rejects_topic_count_out_of_range(self) -> None:
        self.assertTrue(pipeline.publish_metadata_issues(dict(VALID_CANDIDATE, interest_topics=["gatos"])))
        self.assertTrue(
            pipeline.publish_metadata_issues(dict(VALID_CANDIDATE, interest_topics=["a", "b", "c", "d", "e", "f"]))
        )

    def test_rejects_hashtag_count_out_of_range(self) -> None:
        self.assertTrue(pipeline.publish_metadata_issues(dict(VALID_CANDIDATE, hashtags=["#a", "#b", "#c"])))
        self.assertTrue(
            pipeline.publish_metadata_issues(dict(VALID_CANDIDATE, hashtags=[f"#t{i}" for i in range(9)]))
        )

    def test_rejects_empty_description(self) -> None:
        self.assertTrue(pipeline.publish_metadata_issues(dict(VALID_CANDIDATE, description="  ")))

    def test_normalize_adds_hash_prefix_and_dedupes(self) -> None:
        raw = dict(VALID_CANDIDATE, hashtags=["gatos", "#gatos", " humor ", "#pets", "#brasil"])
        normalized = pipeline.normalize_publish_candidate(raw)
        self.assertEqual(normalized["hashtags"], ["#gatos", "#humor", "#pets", "#brasil"])


class PublishGenerationTests(unittest.TestCase):
    def _ollama_reply(self, payload: dict) -> dict:
        import json

        return {"message": {"content": json.dumps(payload, ensure_ascii=False)}}

    def test_success_builds_full_publish_section(self) -> None:
        with patch.object(pipeline, "request_json", return_value=self._ollama_reply(VALID_CANDIDATE)):
            publish = pipeline.generate_publish_metadata(
                make_post(), make_concept(), "runtag-01", "gemma4:31b", timeout=60
            )
        self.assertEqual(publish["status"], "approved")
        self.assertEqual(publish["publish_id"], "runtag-01")
        self.assertEqual(publish["language"], "pt-BR")
        self.assertEqual(publish["model"], "gemma4:31b")
        self.assertEqual(publish["attempts"], 1)
        self.assertEqual(publish["title"], VALID_CANDIDATE["title"])
        self.assertIn(VALID_CANDIDATE["description"], publish["description_with_hashtags"])
        for tag in VALID_CANDIDATE["hashtags"]:
            self.assertIn(tag, publish["description_with_hashtags"])

    def test_retries_then_succeeds(self) -> None:
        bad = self._ollama_reply({"title": "sem os outros campos"})
        good = self._ollama_reply(VALID_CANDIDATE)
        with patch.object(pipeline, "request_json", side_effect=[bad, good]):
            publish = pipeline.generate_publish_metadata(
                make_post(), make_concept(), "runtag-01", "gemma4:31b", timeout=60
            )
        self.assertEqual(publish["status"], "approved")
        self.assertEqual(publish["attempts"], 2)

    def test_three_failures_mark_failed_without_fabricating(self) -> None:
        bad = {"message": {"content": "isso nao e json"}}
        with patch.object(pipeline, "request_json", side_effect=[bad, bad, bad]) as mocked:
            publish = pipeline.generate_publish_metadata(
                make_post(), make_concept(), "runtag-01", "gemma4:31b", timeout=60
            )
        self.assertEqual(mocked.call_count, 3)
        self.assertEqual(publish["status"], "failed")
        self.assertEqual(publish["attempts"], 3)
        self.assertTrue(publish["issues"])
        self.assertNotIn("title", publish)

    def test_request_exception_counts_as_attempt(self) -> None:
        good = self._ollama_reply(VALID_CANDIDATE)
        with patch.object(pipeline, "request_json", side_effect=[RuntimeError("ollama off"), good]):
            publish = pipeline.generate_publish_metadata(
                make_post(), make_concept(), "runtag-01", "gemma4:31b", timeout=60
            )
        self.assertEqual(publish["status"], "approved")
        self.assertEqual(publish["attempts"], 2)


class PublishTextTests(unittest.TestCase):
    def test_render_publish_text_has_all_blocks(self) -> None:
        publish = {
            "title": "Titulo",
            "description": "Descricao base.",
            "description_with_hashtags": "Descricao base.\n\n#gatos #humor #pets #brasil",
            "interest_topics": ["gatos", "humor absurdo", "pets"],
            "hashtags": ["#gatos", "#humor", "#pets", "#brasil"],
            "publish_id": "runtag-01",
        }
        text = pipeline.render_publish_text(publish)
        self.assertIn("Titulo", text)
        self.assertIn("Descricao base.", text)
        self.assertIn("gatos, humor absurdo, pets", text)
        self.assertIn("#gatos #humor #pets #brasil", text)
        self.assertIn("runtag-01", text)


class ContractTests(unittest.TestCase):
    def _valid_record(self, schema_version: int, publish: dict | None = None) -> dict:
        record = {
            "schema_version": schema_version,
            "id": "t3_abc123:1",
            "post": {
                "subreddit": "brasil",
                "id": "t3_abc123",
                "title": "Gato no mapa",
                "author": "u_teste",
                "url": "https://reddit.com/x",
                "updated": "",
                "summary": "",
                "rank": 1,
                "media_type": "image",
                "media_url": "https://i.redd.it/abc123.jpg",
            },
            "joke": {"setup": "A", "escalation": "B", "punchline": "C", "logic": "D",
                     "archetype": "", "rationale": "", "scene_payoff": ""},
            "evaluations": {"source": {}, "humor": {"approved": True}, "quality": {"approved": True},
                            "rounds": [], "approved": True},
            "production": {"image_prompt": "", "source_brief": "", "source_visual_description": "",
                           "video_script": {}, "narration": {}},
            "artifacts": {"paths": {}, "metadata": {}},
            "execution": {"state": "approved", "attempts": {}},
        }
        if publish is not None:
            record["publish"] = publish
        return record

    def test_current_version_is_3_and_v2_still_validates(self) -> None:
        self.assertEqual(pipeline.CONCEPT_SCHEMA_VERSION, 3)
        self.assertEqual(pipeline.validate_concepts_document([self._valid_record(2)]), [])
        self.assertEqual(pipeline.validate_concepts_document([self._valid_record(3)]), [])
        self.assertTrue(pipeline.validate_concepts_document([self._valid_record(1)]))

    def test_publish_section_must_be_dict_when_present(self) -> None:
        self.assertEqual(pipeline.validate_concepts_document([self._valid_record(3, publish={})]), [])
        errors = pipeline.validate_concepts_document([self._valid_record(3, publish="oops")])
        self.assertTrue(any("publish" in error for error in errors))

    def test_concept_document_emits_publish(self) -> None:
        post = make_post()
        concept = make_concept(publish={"publish_id": "runtag-01", "status": "approved"})
        document = pipeline.concept_document(post, concept, 1)
        self.assertEqual(document["schema_version"], 3)
        self.assertEqual(document["publish"]["publish_id"], "runtag-01")
        empty = pipeline.concept_document(post, make_concept(), 1)
        self.assertEqual(empty["publish"], {})

    def test_hydrate_restores_publish(self) -> None:
        record = self._valid_record(3, publish={"publish_id": "runtag-01", "status": "approved"})
        _post, concept = pipeline.hydrate_concept_record(record)
        self.assertEqual(concept["publish"]["publish_id"], "runtag-01")
        _post, legacy = pipeline.hydrate_concept_record(self._valid_record(2))
        self.assertEqual(legacy["publish"], {})


class Format916Tests(unittest.TestCase):
    def test_builds_blur_pad_ffmpeg_command(self) -> None:
        captured: list[list[str]] = []
        with patch.object(pipeline, "run_ffmpeg", side_effect=lambda args: captured.append(args)):
            result = pipeline.format_video_916(Path("/in/video.mp4"), Path("/out/final_916.mp4"))
        self.assertEqual(result, Path("/out/final_916.mp4"))
        self.assertEqual(len(captured), 1)
        args = captured[0]
        joined = " ".join(args)
        self.assertIn("/in/video.mp4", joined)
        self.assertIn("/out/final_916.mp4", joined)
        filter_arg = args[args.index("-filter_complex") + 1]
        self.assertIn("scale=1080:1920:force_original_aspect_ratio=increase", filter_arg)
        self.assertIn("crop=1080:1920", filter_arg)
        self.assertIn("boxblur", filter_arg)
        self.assertIn("force_original_aspect_ratio=decrease", filter_arg)
        self.assertIn("overlay=(W-w)/2:(H-h)/2", filter_arg)
        self.assertIn("[vout]", filter_arg)
        self.assertEqual(args[args.index("-map") + 1], "[vout]")
        self.assertIn("0:a?", args)
        self.assertIn("+faststart", joined)


if __name__ == "__main__":
    unittest.main()
