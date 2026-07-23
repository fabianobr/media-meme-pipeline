from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import daily_reddit_meme_pipeline as pipeline  # noqa: E402
import reddit_meme_dry_run as reddit  # noqa: E402
import reddit_popular_curation as curation  # noqa: E402


class ServiceUrlTests(unittest.TestCase):
    def test_telegram_is_opt_in(self) -> None:
        self.assertFalse(pipeline.build_parser().parse_args([]).telegram)
        self.assertTrue(pipeline.build_parser().parse_args(["--telegram"]).telegram)

    def test_ltx23_defaults_to_t2v_prompt_with_tts_narration(self) -> None:
        # User-validated recipe (2026-07-21): T2V from a detailed literal scene description
        # (no reference image), replacing the unreliable native audio with a measured
        # local-TTS narration. Supersedes the 2026-07-18 I2V-from-source-photo default —
        # T2V with a detailed prompt reads as clearly better ("nitidamente melhor") than I2V
        # for the same source photo. See docs/roadmap.md item 20.
        args = pipeline.build_parser().parse_args([])
        self.assertEqual(args.video_engine, "ltx23")
        self.assertEqual(args.ltx23_input_mode, "prompt")
        self.assertEqual(args.ltx23_audio_mode, "tts")
        self.assertEqual(args.tts_backend, "piper")
        self.assertEqual(args.ltx23_audio_cfg, 3.0)

    def test_environment_overrides_localhost(self) -> None:
        args = argparse.Namespace(ollama_url=None, comfyui_url=None, n8n_url=None)
        with patch.dict(
            os.environ,
            {"OLLAMA_URL": "http://ollama.test/", "COMFYUI_URL": "http://comfy.test/", "N8N_URL": "http://n8n.test/"},
            clear=False,
        ):
            pipeline.configure_service_urls(args)
        self.assertEqual(pipeline.OLLAMA_URL, "http://ollama.test")
        self.assertEqual(pipeline.COMFYUI_VIEW_URL, "http://comfy.test/view")
        self.assertEqual(pipeline.N8N_GENERATE_URL, "http://n8n.test/webhook/comfyui-media-generate")

    def test_cli_overrides_environment(self) -> None:
        args = argparse.Namespace(
            ollama_url="http://cli-ollama/", comfyui_url="http://cli-comfy/", n8n_url="http://cli-n8n/"
        )
        with patch.dict(os.environ, {"OLLAMA_URL": "http://ignored"}, clear=False):
            pipeline.configure_service_urls(args)
        self.assertEqual(pipeline.OLLAMA_URL, "http://cli-ollama")
        self.assertEqual(pipeline.COMFYUI_URL, "http://cli-comfy")
        self.assertEqual(pipeline.N8N_STATUS_URL, "http://cli-n8n/webhook/comfyui-media-status")


class ConceptQualityTests(unittest.TestCase):
    def test_quality_rubric_approves_grounded_concept(self) -> None:
        post = pipeline.reddit.RedditPost(
            subreddit="popular",
            id="t3_demo",
            title="What are the nicknames of your cats that have devolved into nonsense over time?",
            author="/u/demo",
            url="https://example.com",
            updated="2026-06-26T00:00:00+00:00",
            summary="Penelope -> Nelly -> Nelly Bean -> Nellicus Beanster",
            rank=1,
            media_type="image",
            media_url="https://example.com/cat.jpg",
        )
        concept = {
            "top_text": "MEU GATO VIROU UM APELIDO",
            "bottom_text": "E NINGUEM SABE MAIS O NOME REAL",
            "rationale": "Apelido crescendo ate virar outra coisa.",
            "meme_logic": "nickname spiral com humor cotidiano",
            "meme_archetype": "pov_spiral",
            "image_prompt": "A funny cat-owner scene with a blank phone screen and no text.",
            "source_brief": "Reddit post media type: image. Post context paraphrase: Penelope -> Nelly -> Nelly Bean -> Nellicus Beanster.",
            "humor_review": {
                "approved": True,
                "winner_id": 1,
                "scores": {"source_fit": 9, "natural_ptbr": 9, "surprise": 8, "laugh": 8, "visual_payoff": 8},
                "reason": "specific and concise",
            },
        }

        quality = pipeline.evaluate_concept_quality(post, concept)

        self.assertTrue(quality["approved"])
        self.assertGreaterEqual(quality["scores"]["readability"], 4)
        self.assertGreaterEqual(quality["scores"]["source_fit"], 4)
        self.assertGreaterEqual(quality["scores"]["humor"], 4)

    def test_quality_rubric_rejects_generic_meirl_style_concept_without_review(self) -> None:
        post = pipeline.reddit.RedditPost(
            subreddit="popular",
            id="t3_demo",
            title="Meirl",
            author="/u/demo",
            url="https://example.com",
            updated="2026-06-26T00:00:00+00:00",
            summary="submitted by /u/demo to r/meirl [link] [comments]",
            rank=2,
            media_type="image",
            media_url="https://example.com/meirl.jpg",
        )
        concept = {
            "top_text": "EU TENTANDO SER NORMAL",
            "bottom_text": "A INTERNET: CALMA AÍ",
            "rationale": "POV: você tenta uma rotina normal e a internet bagunça tudo.",
            "meme_logic": "POV de rotina interrompida por absurdo online",
            "meme_archetype": "pov_spiral",
            "image_prompt": "A tired person with a phone and no text, clearly in a relatable internet situation.",
            "source_brief": "Reddit post media type: image. Treat the source as a still-image meme/news reference; translate it into a cleaner original scene.",
        }

        quality = pipeline.evaluate_concept_quality(post, concept)

        self.assertFalse(quality["approved"])
        self.assertFalse(quality["checks"]["independent_review_valid"])
        self.assertIn("critica independente", quality["reason"])

    def test_quality_rubric_rejects_generic_concept_for_specific_post(self) -> None:
        post = pipeline.reddit.RedditPost(
            subreddit="popular",
            id="t3_demo",
            title="Cop pulls over Lamborghini on Dubai plates but doesn’t know the law",
            author="/u/demo",
            url="https://example.com",
            updated="2026-06-26T00:00:00+00:00",
            summary="submitted by /u/demo to r/interestingasfuck [link] [comments]",
            rank=3,
            media_type="image",
            media_url="https://example.com/lambo.jpg",
        )
        concept = {
            "top_text": "EU ABRI O MUNDO",
            "bottom_text": "NAO ERA PRA ENTENDER TUDO",
            "rationale": "Arquetipo local de meme: pov_spiral; midia: image.",
            "meme_logic": "generic viral-news POV confusion",
            "meme_archetype": "pov_spiral",
            "image_prompt": "A generic confused person in a room with a phone and no text.",
            "source_brief": "Reddit post media type: image. Post context paraphrase: Penelope -> Nelly -> Nelly Bean -> Nellicus Beanster.",
        }

        quality = pipeline.evaluate_concept_quality(post, concept)

        self.assertFalse(quality["approved"])
        self.assertLessEqual(quality["scores"]["source_fit"], 2)
        self.assertTrue(quality["checks"]["generic_risk"])

    def test_quality_rubric_rejects_paraphrase(self) -> None:
        post = pipeline.reddit.RedditPost(
            subreddit="popular", id="t3_para", title="Policial para Lamborghini com placa de Dubai",
            author="/u/demo", url="https://example.com", updated="2026-06-26T00:00:00+00:00",
            summary="O policial nao conhecia a lei", rank=1, media_type="image", media_url="",
        )
        concept = {
            "top_text": "POLICIAL PARA LAMBORGHINI DE DUBAI",
            "bottom_text": "MAS NÃO CONHECIA A LEI",
            "meme_logic": "repete a noticia",
            "image_prompt": "car and officer, no text",
            "humor_review": {
                "approved": True,
                "winner_id": 1,
                "scores": {"source_fit": 10, "natural_ptbr": 9, "surprise": 3, "laugh": 3, "visual_payoff": 8},
                "reason": "paraphrase",
            },
        }
        quality = pipeline.evaluate_concept_quality(post, concept)
        self.assertFalse(quality["approved"])
        self.assertTrue(quality["checks"]["paraphrase_risk"])


class HumorGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.post = pipeline.reddit.RedditPost(
            subreddit="popular", id="t3_gate", title="Cat nickname becomes nonsense", author="/u/demo",
            url="https://example.com", updated="2026-06-26T00:00:00+00:00", summary="Nelly became Beanster",
            rank=1, media_type="image", media_url="",
        )
        self.concept = {"top_text": "MEU GATO TINHA NOME", "bottom_text": "AGORA É BEANSTER", "meme_logic": "apelido escala"}

    @patch.object(pipeline, "request_json", side_effect=ValueError("critic offline"))
    def test_unavailable_writer_or_critic_never_approves(self, request: Mock) -> None:
        result = pipeline.improve_humor_concept(self.post, self.concept, "model", 1, "cat")
        self.assertFalse(result["humor_approved"])
        self.assertTrue(result["humor_review"]["error"])
        self.assertEqual(request.call_count, 1)
        self.assertEqual(result["execution"]["generation_calls"][0]["stage"], "writer")
        self.assertEqual(result["execution"]["generation_calls"][0]["state"], "failed")
        self.assertIn("critic offline", result["execution"]["generation_calls"][0]["error"])

    def test_generation_stops_after_three_rounds(self) -> None:
        candidates = [
            {"id": i, "mechanic": "contrast", "setup": "MEU GATO TINHA NOME", "escalation": "CADA APELIDO CRIOU OUTRO",
             "punchline": "AGORA É BEANSTER", "comic_turn": "O apelido substitui o nome ate ninguém lembrar dele",
             "scene_payoff": "cat and owner"} for i in range(1, 6)
        ]
        review = {"approved": False, "winner_id": 1,
                  "scores": {"source_fit": 8, "natural_ptbr": 8, "surprise": 7, "laugh": 7, "visual_payoff": 8},
                  "reason": "not surprising enough"}
        responses = []
        for _ in range(3):
            responses.extend([{"message": {"content": pipeline.json.dumps({"candidates": candidates})}},
                              {"message": {"content": pipeline.json.dumps(review)}}])
        with patch.object(pipeline, "request_json", side_effect=responses) as request:
            result = pipeline.improve_humor_concept(
                self.post, self.concept, "writer-model", 1, "cat", critic_model="critic-model"
            )
        self.assertFalse(result["humor_approved"])
        self.assertEqual(len(result["humor_rounds"]), 3)
        self.assertEqual(request.call_count, 6)
        self.assertEqual(len(result["execution"]["generation_calls"]), 6)
        self.assertTrue(all(call["state"] == "completed" for call in result["execution"]["generation_calls"]))
        self.assertTrue(all(call.kwargs["json"]["think"] is False for call in request.call_args_list))
        self.assertEqual(
            [call.kwargs["json"]["model"] for call in request.call_args_list],
            ["writer-model", "critic-model"] * 3,
        )

    def test_frozen_seeds_bypass_writer_but_not_critic(self) -> None:
        candidates = [{
            "id": 1,
            "mechanic": "contraste",
            "setup": "GERALD CHEGOU PARA A REUNIÃO",
            "escalation": "SENTOU NO TRAVESSEIRO E ENCAROU TODO MUNDO",
            "punchline": "O RH PEDIU A CAIXA DE AREIA",
            "comic_turn": "O nome formal transforma o gato sério em executivo entrevistado.",
            "scene_payoff": "gato laranja no travesseiro com expressão séria",
        }]
        review = {
            "approved": True,
            "winner_id": 1,
            "scores": {"source_fit": 9, "natural_ptbr": 9, "surprise": 9, "laugh": 8, "visual_payoff": 8},
            "reason": "specific and concise",
        }
        with patch.object(
            pipeline, "request_json", return_value={"message": {"content": pipeline.json.dumps(review)}}
        ) as request:
            result = pipeline.improve_humor_concept(
                self.post, self.concept, "writer-model", 1, "cat", critic_model="critic-model",
                seed_candidates=candidates,
            )
        self.assertTrue(result["humor_approved"])
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.kwargs["json"]["model"], "critic-model")
        self.assertEqual(result["execution"]["humor_source"], "frozen_seeds")

    def test_two_critics_must_select_same_passing_winner(self) -> None:
        candidates = [
            {
                "id": candidate_id, "mechanic": "contraste", "setup": f"GERALD CANDIDATO {candidate_id}",
                "escalation": "ENCARA A CÂMERA SOBRE O TRAVESSEIRO", "punchline": f"A CASA É DELE {candidate_id}",
                "comic_turn": "O nome formal transforma o gato sério no proprietário da casa.",
                "scene_payoff": "gato laranja sério sobre o travesseiro",
            }
            for candidate_id in (1, 2)
        ]
        def review(winner_id: int) -> dict:
            return {"message": {"content": pipeline.json.dumps({
                "approved": True, "winner_id": winner_id,
                "scores": {"source_fit": 9, "natural_ptbr": 9, "surprise": 9, "laugh": 9, "visual_payoff": 8},
                "reason": "passes",
            })}}
        with patch.object(pipeline, "request_json", side_effect=[review(1), review(2)]) as request:
            result = pipeline.improve_humor_concept(
                self.post, self.concept, "writer", 1, "cat", critic_model="critic-a",
                second_critic_model="critic-b", seed_candidates=candidates,
            )
        self.assertFalse(result["humor_approved"])
        self.assertEqual(result["humor_review"]["winner_id"], 0)
        self.assertEqual(len(result["humor_review"]["critics"]), 2)
        self.assertEqual(request.call_count, 2)

    def test_vision_capable_critic_receives_the_actual_image(self) -> None:
        candidates = [{
            "id": 1, "mechanic": "contraste", "setup": "GERALD CANDIDATO",
            "escalation": "ENCARA A CÂMERA SOBRE O TRAVESSEIRO", "punchline": "A CASA É DELE",
            "comic_turn": "O nome formal transforma o gato sério no proprietário da casa.",
            "scene_payoff": "gato laranja sério sobre o travesseiro",
        }]
        review = {"message": {"content": pipeline.json.dumps({
            "approved": True, "winner_id": 1,
            "scores": {"source_fit": 9, "natural_ptbr": 9, "surprise": 9, "laugh": 9, "visual_payoff": 8},
            "reason": "passes",
        })}}
        with tempfile.TemporaryDirectory() as tmpdir:
            image_path = Path(tmpdir) / "source.jpg"
            pipeline.Image.new("RGB", (32, 32), color="orange").save(image_path)
            with patch.object(pipeline, "request_json", side_effect=[review, review]) as request:
                pipeline.improve_humor_concept(
                    self.post, self.concept, "writer", 1, "cat", critic_model="llama3:latest",
                    second_critic_model="qwen2.5vl:7b", seed_candidates=candidates,
                    image_path=image_path,
                )
        text_critic_message = request.call_args_list[0].kwargs["json"]["messages"][1]
        vision_critic_message = request.call_args_list[1].kwargs["json"]["messages"][1]
        self.assertNotIn("images", text_critic_message)
        self.assertIn("images", vision_critic_message)
        self.assertEqual(len(vision_critic_message["images"]), 1)

    def test_no_image_path_means_no_critic_ever_gets_images(self) -> None:
        candidates = [{
            "id": 1, "mechanic": "contraste", "setup": "GERALD CANDIDATO",
            "escalation": "ENCARA A CÂMERA SOBRE O TRAVESSEIRO", "punchline": "A CASA É DELE",
            "comic_turn": "O nome formal transforma o gato sério no proprietário da casa.",
            "scene_payoff": "gato laranja sério sobre o travesseiro",
        }]
        review = {"message": {"content": pipeline.json.dumps({
            "approved": True, "winner_id": 1,
            "scores": {"source_fit": 9, "natural_ptbr": 9, "surprise": 9, "laugh": 9, "visual_payoff": 8},
            "reason": "passes",
        })}}
        with patch.object(pipeline, "request_json", side_effect=[review, review]) as request:
            pipeline.improve_humor_concept(
                self.post, self.concept, "writer", 1, "cat", critic_model="llama3:latest",
                second_critic_model="qwen2.5vl:7b", seed_candidates=candidates,
            )
        for call in request.call_args_list:
            self.assertNotIn("images", call.kwargs["json"]["messages"][1])


class FrozenConceptSeedTests(unittest.TestCase):
    def test_loader_rejects_more_than_five_candidates(self) -> None:
        candidate = {
            "id": 1, "mechanic": "contrast", "setup": "setup", "escalation": "escalation",
            "punchline": "punchline", "comic_turn": "a complete comic turn with enough words here",
            "scene_payoff": "cat on pillow",
        }
        payload = [{"post_id": "t3_demo", "candidates": [{**candidate, "id": index} for index in range(1, 7)]}]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "seeds.json"
            path.write_text(pipeline.json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "1 to 5"):
                pipeline.load_frozen_concept_seeds(path)


class SourceSuitabilityTests(unittest.TestCase):
    def test_source_gate_requires_every_minimum(self) -> None:
        review = pipeline.finalize_source_suitability_review({
            "approved": True,
            "scores": {"source_match": 5, "visual_clarity": 5, "motion_potential": 2, "text_independence": 5},
            "reason": "static",
        })
        self.assertFalse(review["approved"])

    def test_source_gate_approves_clear_visual_action(self) -> None:
        review = pipeline.finalize_source_suitability_review({
            "approved": True,
            "scores": {"source_match": 5, "visual_clarity": 4, "motion_potential": 4, "text_independence": 5},
            "reason": "clear action",
        })
        self.assertTrue(review["approved"])

    def test_embedded_caption_caps_text_independence_deterministically(self) -> None:
        review = pipeline.finalize_source_suitability_review({
            "approved": True,
            "embedded_text_carries_meaning": True,
            "scores": {"source_match": 5, "visual_clarity": 5, "motion_potential": 4, "text_independence": 5},
            "reason": "model scored generously despite baked-in caption",
        })
        self.assertFalse(review["approved"])
        self.assertEqual(review["scores"]["text_independence"], 2.0)

    def test_descriptive_dialogue_body_is_rejected_deterministically(self) -> None:
        source = "um cao paraplegico em pe na praia com cadeira de rodas ao lado e pessoa ajudando"
        issues = pipeline.humor_candidate_issues(
            {
                "setup": "CAO EM PE NA PRAIA",
                "escalation": "CADEIRA DE RODAS AO LADO, PESSOA AJUDANDO",
                "punchline": "ELE E O REI DO ASFALTO",
                "comic_turn": "a virada da ao cao um papel inesperado de dono da rua",
            },
            source,
        )
        self.assertIn("setup e escalada apenas descrevem a cena; falta narrador com opiniao", issues)

    def test_narrator_with_attitude_dialogue_passes_the_description_check(self) -> None:
        source = "um cavalo e um gato juntos em um estabulo"
        issues = pipeline.humor_candidate_issues(
            {
                "setup": "TREINADOR MANDOU FOTO DE AMIZADE",
                "escalation": "HUMMM MUITO ESTRANHO ISSO AQUI",
                "punchline": "TREINAMENTO PARA COEXISTENCIA SERA",
                "comic_turn": "o narrador desconfia da amizade e especula um programa secreto",
            },
            source,
        )
        self.assertNotIn("setup e escalada apenas descrevem a cena; falta narrador com opiniao", issues)

    def test_multi_photo_collage_caps_clarity_and_text_independence(self) -> None:
        review = pipeline.finalize_source_suitability_review({
            "approved": True,
            "multi_photo_collage": True,
            "scores": {"source_match": 5, "visual_clarity": 5, "motion_potential": 4, "text_independence": 5},
            "reason": "side-by-side comparison",
        })
        self.assertFalse(review["approved"])
        self.assertEqual(review["scores"]["text_independence"], 2.0)
        self.assertEqual(review["scores"]["visual_clarity"], 3.0)


class SourceSuitabilityMotionCapTests(unittest.TestCase):
    def test_open_scene_flag_caps_motion_potential_to_two(self) -> None:
        review = {
            "approved": True,
            "embedded_text_carries_meaning": False,
            "multi_photo_collage": False,
            "open_scene_no_intrinsic_motion": True,
            "scores": {"source_match": 5, "visual_clarity": 5, "motion_potential": 3, "text_independence": 5},
            "reason": "cena clara",
        }
        result = pipeline.finalize_source_suitability_review(review)
        self.assertEqual(result["scores"]["motion_potential"], 2.0)
        self.assertFalse(result["approved"])  # 2.0 < required minimum of 3
        self.assertIn("cena aberta", result["reason"])

    def test_open_scene_flag_false_does_not_cap(self) -> None:
        review = {
            "approved": True,
            "embedded_text_carries_meaning": False,
            "multi_photo_collage": False,
            "open_scene_no_intrinsic_motion": False,
            "scores": {"source_match": 5, "visual_clarity": 5, "motion_potential": 4, "text_independence": 5},
            "reason": "cena clara",
        }
        result = pipeline.finalize_source_suitability_review(review)
        self.assertEqual(result["scores"]["motion_potential"], 4.0)
        self.assertTrue(result["approved"])

    def test_open_scene_flag_missing_defaults_to_no_cap(self) -> None:
        # Backward compatibility: old persisted reviews (before this field existed) must
        # not retroactively get capped just because the key is absent.
        review = {
            "approved": True,
            "embedded_text_carries_meaning": False,
            "multi_photo_collage": False,
            "scores": {"source_match": 5, "visual_clarity": 5, "motion_potential": 4, "text_independence": 5},
            "reason": "cena clara",
        }
        result = pipeline.finalize_source_suitability_review(review)
        self.assertEqual(result["scores"]["motion_potential"], 4.0)

    def test_open_scene_cap_combines_with_existing_caps(self) -> None:
        # A source can trip more than one deterministic rule at once; both caps must apply.
        review = {
            "approved": True,
            "embedded_text_carries_meaning": True,
            "multi_photo_collage": False,
            "open_scene_no_intrinsic_motion": True,
            "scores": {"source_match": 5, "visual_clarity": 5, "motion_potential": 3, "text_independence": 4},
            "reason": "cena clara",
        }
        result = pipeline.finalize_source_suitability_review(review)
        self.assertEqual(result["scores"]["motion_potential"], 2.0)
        self.assertEqual(result["scores"]["text_independence"], 2.0)

    def test_assess_source_suitability_schema_requires_open_scene_flag(self) -> None:
        post = pipeline.reddit.RedditPost(
            subreddit="popular", id="t3_motion", title="A person in a wide patio", author="/u/demo",
            url="https://example.com", updated="2026-07-19T00:00:00+00:00",
            summary="", rank=1, media_type="image", media_url="",
        )
        review_payload = {
            "approved": True, "reason": "ok",
            "scores": {"source_match": 5, "visual_clarity": 5, "motion_potential": 2, "text_independence": 5},
            "embedded_text_carries_meaning": False, "multi_photo_collage": False,
            "open_scene_no_intrinsic_motion": False,
        }
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "photo.jpg"
            pipeline.Image.new("RGB", (32, 32), color="green").save(image_path)
            with patch.object(
                pipeline, "request_json",
                return_value={"message": {"content": pipeline.json.dumps(review_payload)}},
            ) as mock_request:
                pipeline.assess_source_suitability(post, image_path, "a woman in a patio", "critic-model", 30)
        self.assertEqual(mock_request.call_count, 1)
        _, kwargs = mock_request.call_args
        payload = kwargs["json"]
        self.assertIn("open_scene_no_intrinsic_motion", payload["format"]["properties"])
        self.assertEqual(payload["format"]["properties"]["open_scene_no_intrinsic_motion"]["type"], "boolean")
        self.assertIn("open_scene_no_intrinsic_motion", payload["format"]["required"])

    def test_non_image_post_rejected_with_media_type_aware_reason(self) -> None:
        post = reddit.RedditPost(
            subreddit="test",
            id="t3_textonly",
            title="a text post",
            author="someone",
            url="https://example.com/textonly",
            updated="2026-07-23T00:00:00Z",
            summary="",
            rank=1,
            media_type="text",
            media_url="",
        )
        rejection = pipeline.reject_non_image_source(post, "")
        self.assertIsNotNone(rejection)
        self.assertFalse(rejection["approved"])
        self.assertIn("media_type='text'", rejection["reason"])
        self.assertNotIn("I2V", rejection["reason"])
        self.assertEqual(
            rejection["scores"],
            {name: 0.0 for name in ("source_match", "visual_clarity", "motion_potential", "text_independence")},
        )

    def test_image_post_with_source_path_returns_none(self) -> None:
        post = reddit.RedditPost(
            subreddit="test",
            id="t3_hasimage",
            title="an image post",
            author="someone",
            url="https://example.com/hasimage",
            updated="2026-07-23T00:00:00Z",
            summary="",
            rank=1,
            media_type="image",
            media_url="https://example.com/hasimage.jpg",
        )
        self.assertIsNone(pipeline.reject_non_image_source(post, "/tmp/hasimage.jpg"))


class SourceSuitabilityDriftRiskPassthroughTests(unittest.TestCase):
    def test_resting_domestic_animal_flag_passes_through_without_affecting_approval(self) -> None:
        review = {
            "approved": True,
            "embedded_text_carries_meaning": False,
            "multi_photo_collage": False,
            "open_scene_no_intrinsic_motion": False,
            "resting_domestic_animal_scene": True,
            "scores": {"source_match": 5, "visual_clarity": 5, "motion_potential": 4, "text_independence": 5},
            "reason": "cachorro deitado no carro",
        }
        result = pipeline.finalize_source_suitability_review(review)
        self.assertTrue(result["approved"])
        self.assertEqual(result["scores"]["motion_potential"], 4.0)
        self.assertTrue(result["resting_domestic_animal_scene"])

    def test_flag_defaults_to_false_when_missing(self) -> None:
        review = {
            "approved": True,
            "embedded_text_carries_meaning": False,
            "multi_photo_collage": False,
            "scores": {"source_match": 5, "visual_clarity": 5, "motion_potential": 4, "text_independence": 5},
            "reason": "ok",
        }
        result = pipeline.finalize_source_suitability_review(review)
        self.assertFalse(result["resting_domestic_animal_scene"])


class PopularCurationBacklogTests(unittest.TestCase):
    def _post(self, post_id: str, media_type: str, rank: int) -> reddit.RedditPost:
        return reddit.RedditPost(
            subreddit="popular",
            id=post_id,
            title=f"title {post_id}",
            author="someone",
            url="https://reddit.com/x",
            updated="2026-07-15T00:00:00+00:00",
            summary="",
            rank=rank,
            media_type=media_type,
            media_url="https://i.redd.it/x.jpg" if media_type == "image" else "https://v.redd.it/x",
        )

    def test_video_and_text_posts_are_skipped_without_calling_the_vision_model(self) -> None:
        posts = [self._post("v1", "video", 1), self._post("t1", "text", 2)]
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(reddit, "fetch_feed", return_value=(200, "<feed/>", {}, [])), \
                 patch.object(reddit, "parse_feed", return_value=posts), \
                 patch.object(reddit, "filter_posts", return_value=posts), \
                 patch.object(pipeline, "describe_source_image") as mock_describe, \
                 patch.object(pipeline, "assess_source_suitability") as mock_assess, \
                 patch("sys.argv", ["reddit_popular_curation.py"] + [
                     "--backlog-file", f"{tmp}/backlog.json", "--media-dir", tmp,
                 ]):
                curation.main()
            mock_describe.assert_not_called()
            mock_assess.assert_not_called()
            backlog = curation.load_backlog(Path(f"{tmp}/backlog.json"))
            self.assertEqual(backlog["seen_ids"], ["t1", "v1"])
            self.assertEqual(backlog["approved"], [])

    def test_approved_image_post_is_added_and_already_seen_ids_are_not_reevaluated(self) -> None:
        post = self._post("i1", "image", 1)
        with tempfile.TemporaryDirectory() as tmp:
            from PIL import Image as PILImage
            fake_source = f"{tmp}/i1.jpg"
            PILImage.new("RGB", (800, 800), (120, 90, 60)).save(fake_source)
            argv = ["reddit_popular_curation.py", "--backlog-file", f"{tmp}/backlog.json", "--media-dir", tmp]
            with patch.object(reddit, "fetch_feed", return_value=(200, "<feed/>", {}, [])), \
                 patch.object(reddit, "parse_feed", return_value=[post]), \
                 patch.object(reddit, "filter_posts", return_value=[post]), \
                 patch.object(pipeline, "download_source_media", return_value=fake_source), \
                 patch.object(pipeline, "describe_source_image", return_value="a cat"), \
                 patch.object(
                     pipeline,
                     "assess_source_suitability",
                     return_value={"approved": True, "scores": {}, "reason": "ok"},
                 ) as mock_assess, \
                 patch("sys.argv", argv):
                curation.main()
            self.assertEqual(mock_assess.call_count, 1)
            backlog = curation.load_backlog(Path(f"{tmp}/backlog.json"))
            self.assertEqual(len(backlog["approved"]), 1)
            self.assertEqual(backlog["seen_ids"], ["i1"])

            # Second run: same post must not be re-evaluated (already seen).
            with patch.object(reddit, "fetch_feed", return_value=(200, "<feed/>", {}, [])), \
                 patch.object(reddit, "parse_feed", return_value=[post]), \
                 patch.object(reddit, "filter_posts", return_value=[post]), \
                 patch.object(pipeline, "download_source_media") as mock_download, \
                 patch.object(pipeline, "assess_source_suitability") as mock_assess_again, \
                 patch("sys.argv", argv):
                curation.main()
            mock_download.assert_not_called()
            mock_assess_again.assert_not_called()
            backlog = curation.load_backlog(Path(f"{tmp}/backlog.json"))
            self.assertEqual(len(backlog["approved"]), 1)


class GenerateConceptsCheckpointTests(unittest.TestCase):
    def test_checkpoint_called_after_each_post(self) -> None:
        posts = [
            reddit.RedditPost(
                subreddit="test", id="p1", title="title1", author="author1", url="url1",
                updated="2023-01-01", summary="summary1", rank=1, media_type="image"
            ),
            reddit.RedditPost(
                subreddit="test", id="p2", title="title2", author="author2", url="url2",
                updated="2023-01-01", summary="summary2", rank=2, media_type="image"
            )
        ]

        source_reviews = {
            "p1": {"approved": False, "scores": {}, "reason": "test rejection"},
            "p2": {"approved": False, "scores": {}, "reason": "test rejection"}
        }

        seed_candidates_by_post = {
            "p1": [{"id": 1}],
            "p2": [{"id": 1}]
        }

        checkpoint_calls = []

        def checkpoint_callback(partial_list):
            checkpoint_calls.append(len(partial_list))

        result = pipeline.generate_concepts(
            posts=posts,
            model="unused-model",
            timeout=5,
            visual_descriptions={},
            seed_candidates_by_post=seed_candidates_by_post,
            source_reviews=source_reviews,
            checkpoint=checkpoint_callback
        )

        self.assertEqual(checkpoint_calls, [1, 2])
        self.assertEqual(len(result), 2)
        for concept in result:
            self.assertFalse(concept["humor_approved"])


class PhotomotionBeatPlanTests(unittest.TestCase):
    def test_shots_cover_lead_gaps_and_tail_contiguously(self) -> None:
        shots, total = pipeline.photomotion_beat_plan([2.0, 3.0, 2.5], lead=0.4, gap=0.35, tail=1.2)
        self.assertEqual(len(shots), 3)
        self.assertEqual(shots[0][0], 0.4)  # first audio starts after the lead
        # shots are contiguous: each shot ends where the next begins, last ends at total
        self.assertAlmostEqual(shots[0][1], shots[1][0])
        self.assertAlmostEqual(shots[1][0] + shots[1][1], shots[2][0])
        self.assertAlmostEqual(shots[2][0] + shots[2][1], total)
        self.assertAlmostEqual(total, 0.4 + 2.0 + 0.35 + 3.0 + 0.35 + 2.5 + 1.2)

    def test_empty_narration_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            pipeline.photomotion_beat_plan([])

    def test_piper_normalization_lowers_caps_and_respells_loanwords(self) -> None:
        normalized = pipeline.normalize_piper_text("AIRBNB COM PISCINA, HMMM... STAR WARS")
        self.assertNotIn("AIRBNB", normalized)
        self.assertIn("humm", normalized)
        self.assertNotIn("...", normalized)


class VideoScriptSpeciesPreservationTests(unittest.TestCase):
    def _post(self, title: str) -> reddit.RedditPost:
        return reddit.RedditPost(
            subreddit="popular", id="t3_x", title=title, author="x", url="", updated="",
            summary="", rank=1, media_type="image",
        )

    def test_non_cat_non_human_subject_never_offers_cat_as_an_alternative(self) -> None:
        post = self._post("A Scottish Highland Cow Born Just Two Hours Ago")
        concept = {"top_text": "A", "middle_text": "B", "bottom_text": "C", "meme_archetype": "pov_spiral"}
        script = pipeline.build_video_script(
            post, concept,
            visual_description="Um bezerro pequeno esta em uma area coberta de serragem, com pelucio cinza escuro.",
        )
        self.assertNotIn("cat", script["character"].lower())
        self.assertIn("bezerro", script["character"].lower())

    def test_plural_human_subject_is_detected_not_treated_as_animal(self) -> None:
        post = self._post("A traditional technique used by young shepherds of Ethiopia's Banna tribe")
        concept = {"top_text": "A", "middle_text": "B", "bottom_text": "C", "meme_archetype": "boss_fight"}
        script = pipeline.build_video_script(
            post, concept,
            visual_description=(
                "Dois homens estao em uma paisagem montanhosa com um ceu claro. "
                "Eles seguram longos bastoes de madeira."
            ),
        )
        self.assertIn("human subject", script["character"].lower())
        self.assertNotIn("stays mostly still", script["character"].lower())
        self.assertNotIn("species", script["character"].lower())

    def test_boss_fight_and_default_archetype_timelines_never_hardcode_cat(self) -> None:
        post = self._post("A traditional technique used by young shepherds of Ethiopia's Banna tribe")
        visual_description = "Dois homens estao em uma paisagem montanhosa segurando bastoes de madeira."
        for archetype in ("boss_fight", "pov_spiral"):
            concept = {"top_text": "A", "middle_text": "B", "bottom_text": "C", "meme_archetype": archetype}
            script = pipeline.build_video_script(post, concept, visual_description=visual_description)
            timeline_text = " ".join(script["timeline"]).lower()
            self.assertNotIn("the cat", timeline_text, f"archetype={archetype}")

    def test_cat_scene_preserves_specific_markings_instead_of_generic_orange(self) -> None:
        post = self._post("A cat with fur and eyes that are split into two distinct colors")
        concept = {"top_text": "A", "middle_text": "B", "bottom_text": "C", "meme_archetype": "pov_spiral"}
        script = pipeline.build_video_script(
            post, concept,
            visual_description="Um gato com metade do rosto de uma cor e metade de outra, olhos heterocromicos.",
        )
        self.assertIn("heterocromicos", script["character"].lower())


class ConceptSchemaTests(unittest.TestCase):
    def test_schema_rejects_non_mp4_video_path(self) -> None:
        post = pipeline.reddit.RedditPost(
            subreddit="popular", id="t3_schema", title="Cat nicknames", author="/u/demo", url="https://example.com",
            updated="2026-06-26T00:00:00+00:00", summary="Beanster", rank=1, media_type="image", media_url="",
        )
        concept = {"top_text": "MEU GATO TINHA NOME", "bottom_text": "AGORA É BEANSTER", "meme_logic": "apelido escala",
                   "video_path": "{'voice': 'pt-BR-AntonioNeural'}", "execution": {"state": "rejected", "attempts": {}}}
        document = [pipeline.concept_document(post, concept, 1)]
        errors = pipeline.validate_concepts_document(document)
        self.assertTrue(any("must end in .mp4" in error for error in errors))

    def test_loader_accepts_fully_approved_document(self) -> None:
        post = pipeline.reddit.RedditPost(
            subreddit="popular",
            id="t3_schema_approved",
            title="Cats with very human names",
            author="/u/demo",
            url="https://example.com",
            updated="2026-06-26T00:00:00+00:00",
            summary="This is Gerald.",
            rank=1,
            media_type="image",
            media_url="https://example.com/gerald.jpg",
        )
        concept = {
            "top_text": "GERALD NÃO É NOME DE GATO",
            "middle_text": "É NOME DE QUEM TE ENCARA ASSIM",
            "bottom_text": "ANTES DE NEGAR SEU EMPRÉSTIMO",
            "meme_logic": "A fofura do gato contrasta com a autoridade burocrática sugerida pelo nome e olhar.",
            "meme_archetype": "boss_fight",
            "rationale": "",
            "scene_payoff": "Gerald encara a câmera como alguém avaliando silenciosamente um pedido",
            "source_review": {
                "approved": True,
                "scores": {"source_match": 5, "visual_clarity": 5, "motion_potential": 3, "text_independence": 4},
                "reason": "clear",
            },
            "humor_review": {
                "approved": True,
                "winner_id": 4,
                "scores": {"source_fit": 8, "natural_ptbr": 9, "surprise": 9, "laugh": 8, "visual_payoff": 7},
                "reason": "consenso aprovado",
                "critics": [],
            },
            "quality_review": {
                "approved": True,
                "scores": {
                    "readability": 5,
                    "source_fit": 4,
                    "humor": 4,
                    "share": 4,
                    "natural_ptbr": 4.5,
                    "surprise": 4.5,
                    "punchline": 4,
                    "visual_payoff": 3.5,
                },
                "checks": {"independent_review_valid": True, "generic_risk": False, "paraphrase_risk": False},
                "reason": "limiares obrigatorios atendidos",
            },
            "execution": {"state": "approved", "attempts": {}},
            "production": {"source_visual_description": "orange cat", "video_script": {"scene": "cat", "timeline": []}},
            "artifacts": {"paths": {}, "metadata": {}},
        }
        document = [pipeline.concept_document(post, concept, 1)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "concepts.json"
            path.write_text(pipeline.json.dumps(document), encoding="utf-8")
            posts, concepts = pipeline.load_approved_concepts_document(path)
        self.assertEqual(posts[0].id, "t3_schema_approved")
        self.assertEqual(concepts[0]["top_text"], "GERALD NÃO É NOME DE GATO")
        self.assertTrue(concepts[0]["humor_approved"])

    def test_loader_rejects_unapproved_document(self) -> None:
        post = pipeline.reddit.RedditPost(
            subreddit="popular",
            id="t3_schema_rejected",
            title="Cats with very human names",
            author="/u/demo",
            url="https://example.com",
            updated="2026-06-26T00:00:00+00:00",
            summary="This is Gerald.",
            rank=1,
            media_type="image",
            media_url="https://example.com/gerald.jpg",
        )
        concept = {
            "top_text": "GERALD NÃO É NOME DE GATO",
            "middle_text": "É NOME DE QUEM TE ENCARA ASSIM",
            "bottom_text": "ANTES DE NEGAR SEU EMPRÉSTIMO",
            "meme_logic": "A fofura do gato contrasta com a autoridade burocrática sugerida pelo nome e olhar.",
            "meme_archetype": "boss_fight",
            "execution": {"state": "rejected", "attempts": {}},
            "source_review": {
                "approved": True,
                "scores": {"source_match": 5, "visual_clarity": 5, "motion_potential": 3, "text_independence": 4},
                "reason": "clear",
            },
            "humor_review": {"approved": True, "winner_id": 4, "scores": {"source_fit": 8, "natural_ptbr": 9, "surprise": 9, "laugh": 8, "visual_payoff": 7}, "reason": "ok"},
            "quality_review": {"approved": True, "scores": {"readability": 5, "source_fit": 4, "humor": 4, "share": 4, "natural_ptbr": 4.5, "surprise": 4.5, "punchline": 4, "visual_payoff": 3.5}, "checks": {}, "reason": "ok"},
            "production": {"source_visual_description": "orange cat", "video_script": {"scene": "cat", "timeline": []}},
            "artifacts": {"paths": {}, "metadata": {}},
        }
        document = [pipeline.concept_document(post, concept, 1)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "concepts.json"
            path.write_text(pipeline.json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "approved for resume rendering"):
                pipeline.load_approved_concepts_document(path)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg/ffprobe required")
class ArtifactIntegrationTests(unittest.TestCase):
    def test_local_mp4_probe_requires_video_and_audio(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "candidate.mp4"
            subprocess.run(
                [
                    "ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=size=320x180:duration=0.3",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=0.3", "-shortest", "-c:v", "libx264",
                    "-c:a", "aac", "-pix_fmt", "yuv420p", str(output),
                ],
                check=True,
                timeout=15,
            )
            metadata = pipeline.probe_video_artifact(output)
        self.assertEqual((metadata["width"], metadata["height"]), (320, 180))
        self.assertTrue(metadata["has_audio"])
        self.assertGreater(metadata["duration_seconds"], 0)

    def test_probe_video_artifact_flags_freeze_on_static_clip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "static.mp4"
            subprocess.run(
                [
                    "ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:size=320x180:duration=2:rate=25",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-shortest", "-c:v", "libx264",
                    "-c:a", "aac", "-pix_fmt", "yuv420p", str(output),
                ],
                check=True,
                timeout=15,
            )
            metadata = pipeline.probe_video_artifact(output)
        self.assertTrue(metadata["freeze_detected"])
        self.assertEqual(metadata["motion_vmaf_avg"], 0.0)

    def test_probe_video_artifact_does_not_flag_freeze_on_moving_clip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "moving.mp4"
            subprocess.run(
                [
                    "ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:duration=2:rate=25",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-shortest", "-c:v", "libx264",
                    "-c:a", "aac", "-pix_fmt", "yuv420p", str(output),
                ],
                check=True,
                timeout=15,
            )
            metadata = pipeline.probe_video_artifact(output)
        self.assertFalse(metadata["freeze_detected"])
        self.assertGreater(metadata["motion_vmaf_avg"], 0.0)

    def test_probe_video_motion_raises_on_ffmpeg_failure_instead_of_reporting_zero(self) -> None:
        # A nonexistent input makes the real ffmpeg process exit non-zero without
        # ffmpeg's own stderr ever matching the "VMAF Motion avg:" regex. Before the
        # returncode check this silently produced motion_vmaf_avg=0.0/freeze_detected=False
        # — indistinguishable from a genuinely static clip. Assert it now raises instead,
        # which propagates into probe_video_artifact()'s existing try/except and surfaces
        # as motion_vmaf_avg=None/freeze_detected=None rather than 0.0/False.
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "does-not-exist.mp4"
            with self.assertRaises(RuntimeError):
                pipeline.probe_video_motion(missing)


class ComfyWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.post = pipeline.reddit.RedditPost(
            subreddit="popular", id="t3_workflow", title="Cat nickname becomes Beanster", author="/u/demo",
            url="https://example.com", updated="2026-06-26T00:00:00+00:00", summary="Nelly became Beanster",
            rank=1, media_type="image", media_url="",
        )

    @patch.object(pipeline, "request_json", return_value={"prompt_id": "prompt-123"})
    def test_native_av_workflow_is_parameterized_from_checked_in_graph(self, request: Mock) -> None:
        args = pipeline.build_parser().parse_args([])
        concept: dict[str, object] = {}
        prompt_id = pipeline.queue_comfy_ltx23_native_video(
            concept, self.post, "tests/native-av", 1234, args,
            video_prompt_override='A person looks at the camera. Natural Brazilian Portuguese speech says: "Beanster".',
            frames_override=49,
        )
        payload = request.call_args.kwargs["json"]["prompt"]
        source = pipeline.json.loads(pipeline.LTX23_API_WORKFLOW.read_text(encoding="utf-8"))["prompt"]
        self.assertEqual(prompt_id, "prompt-123")
        self.assertEqual(payload["13"]["class_type"], "MultimodalGuider")
        self.assertEqual(payload["22"]["inputs"]["filename_prefix"], "tests/native-av")
        self.assertEqual(payload["16"]["inputs"]["noise_seed"], 1234)
        self.assertEqual(payload["7"]["inputs"]["length"], 49)
        self.assertNotEqual(source["22"]["inputs"]["filename_prefix"], "tests/native-av")
        self.assertEqual(concept["ltx_video_input_mode"], "ltx23-native-audio-video")

    @patch.object(pipeline, "upload_comfy_image", return_value="uploaded-reference.png")
    @patch.object(pipeline, "request_json", return_value={"prompt_id": "prompt-456"})
    def test_native_i2v_workflow_uses_checked_in_graph(self, request: Mock, upload: Mock) -> None:
        args = pipeline.build_parser().parse_args([])
        concept: dict[str, object] = {}
        reference = Path("tests/reference.png")

        prompt_id = pipeline.queue_comfy_ltx23_native_video(
            concept, self.post, "tests/native-i2v", 5678, args,
            video_prompt_override='A cat owner looks at the camera. Natural Brazilian Portuguese speech says: "Beanster".',
            frames_override=49,
            reference_image_path=reference,
        )

        payload = request.call_args.kwargs["json"]["prompt"]
        source = pipeline.json.loads(pipeline.LTX23_I2V_API_WORKFLOW.read_text(encoding="utf-8"))["prompt"]
        self.assertEqual(prompt_id, "prompt-456")
        self.assertEqual(upload.call_args.args[0], reference)
        self.assertEqual(payload["8"]["inputs"]["image"], "uploaded-reference.png")
        self.assertEqual(payload["11"]["inputs"]["img_compression"], args.ltx23_reference_compression)
        self.assertEqual(payload["36"]["inputs"]["filename_prefix"], "tests/native-i2v")
        self.assertEqual(payload["16"]["inputs"]["noise_seed"], 5678)
        self.assertEqual(payload["12"]["inputs"]["length"], 49)
        self.assertEqual(payload["12"]["inputs"]["width"], args.ltx23_width // 2)
        self.assertEqual(payload["12"]["inputs"]["height"], args.ltx23_height // 2)
        self.assertEqual(payload["9"]["inputs"]["width"], args.ltx23_width)
        self.assertEqual(payload["17"]["inputs"]["cfg"], 1.0)
        self.assertEqual(payload["27"]["inputs"]["cfg"], 1.0)
        self.assertEqual(payload["18"]["inputs"]["sampler_name"], "euler")
        self.assertNotEqual(source["8"]["inputs"]["image"], "uploaded-reference.png")
        self.assertEqual(concept["ltx_video_input_mode"], "ltx23-native-i2v-audio-video")
        self.assertEqual(concept["ltx23_workflow"], "workflows/05-ltx23-official-i2v-audio-api.json")

    @patch.object(pipeline, "upload_comfy_image", return_value="uploaded-source.png")
    @patch.object(pipeline, "request_json", return_value={"prompt_id": "prompt-789"})
    def test_native_source_mode_uses_downloaded_source_reference(self, request: Mock, upload: Mock) -> None:
        args = pipeline.build_parser().parse_args(["--ltx23-input-mode", "source"])
        concept: dict[str, object] = {"source_media_path": str(Path("tests/source.jpg"))}

        prompt_id = pipeline.queue_comfy_ltx23_native_video(
            concept,
            self.post,
            "tests/native-source",
            9012,
            args,
            video_prompt_override='A cat remains still. Natural Brazilian Portuguese narration only.',
            frames_override=49,
            reference_image_path=Path(concept["source_media_path"]),
        )

        payload = request.call_args.kwargs["json"]["prompt"]
        self.assertEqual(prompt_id, "prompt-789")
        self.assertEqual(upload.call_args.args[0], Path(concept["source_media_path"]))
        self.assertEqual(payload["8"]["inputs"]["image"], "uploaded-source.png")
        self.assertEqual(concept["ltx_video_input_mode"], "ltx23-native-i2v-audio-video")
        self.assertEqual(concept["ltx23_workflow"], "workflows/05-ltx23-official-i2v-audio-api.json")

    def test_descriptive_punchline_is_rejected_deterministically(self) -> None:
        source_text = "Um gato branco dormindo em uma cama branca. 2 years difference."
        descriptive = {
            "setup": "EU ABRI O BRASIL",
            "escalation": "ESPERAVA UMA CENA DE TENSÃO",
            "punchline": "ENCONTREI UM GATO DORMINDO",
            "comic_turn": "a expectativa de caos vira uma cena calma de um gato dormindo",
        }
        issues = pipeline.humor_candidate_issues(descriptive, source_text)
        self.assertTrue(any("descreve a cena visivel" in issue for issue in issues))

        reinterpreting = {
            "setup": "GERALD NÃO É NOME DE GATO",
            "escalation": "É NOME DE QUEM TE ENCARA ASSIM",
            "punchline": "ANTES DE NEGAR SEU EMPRÉSTIMO",
            "comic_turn": "a punchline transforma o olhar do gato em um gerente de banco",
        }
        issues = pipeline.humor_candidate_issues(reinterpreting, "Um gato laranja encarando a camera. Cats with very human names.")
        self.assertFalse(any("descreve a cena visivel" in issue for issue in issues))

    def test_native_prompt_requests_ptbr_speech(self) -> None:
        concept = {
            "top_text": "MEU GATO TINHA NOME", "bottom_text": "AGORA É BEANSTER",
            "video_script": {
                "timeline": ["The owner looks at the cat"], "scene": "A simple living room",
                "character": "One Brazilian adult", "main_prop": "One cat",
                "camera": "locked medium shot", "dialogue": "", "audio": "",
            },
        }
        prompts = pipeline.compose_ltx23_segment_prompts(self.post, concept)
        self.assertEqual(len(prompts), 1)
        self.assertIn("Brazilian Portuguese narration", prompts[0])
        self.assertNotIn("AGORA É BEANSTER", prompts[0])
        self.assertNotIn("narrator", pipeline.ltx23_negative_prompt())

    def test_native_prompt_quotes_dialogue_lowercase_without_negations(self) -> None:
        concept = {
            "video_script": {
                "timeline": [
                    "The cat stares at the camera",
                    "The cat blinks once and shifts slightly",
                    "The cat pauses and gives a tiny nod",
                ],
                "scene": "A simple living room", "character": "One calm house cat",
                "main_prop": "One cat", "camera": "locked medium shot",
                "audio": "Quiet room tone", "visual_rules": "Preserve the source scene",
                "dialogue": "GERALD NÃO É NOME DE GATO. É NOME DE QUEM TE ENCARA ASSIM.",
            },
        }
        prompts = pipeline.compose_ltx23_segment_prompts(self.post, concept)
        self.assertIn('"gerald não é nome de gato. é nome de quem te encara assim"', prompts[0])
        self.assertNotIn("GERALD", prompts[0])
        for banned in ("No cuts", "captions", "subtitles", "posters", "no visible text"):
            self.assertNotIn(banned, prompts[0])

    def test_two_segment_prompts_split_punchline_into_second_segment(self) -> None:
        concept = {
            "video_script": {
                "timeline": [
                    "The cat stares at the camera",
                    "The cat blinks once and shifts slightly",
                    "The cat pauses and gives a tiny nod",
                ],
                "scene": "A simple living room", "character": "One calm house cat",
                "main_prop": "One cat", "camera": "locked medium shot",
                "audio": "Quiet room tone", "visual_rules": "Preserve the source scene",
                "dialogue": "GERALD NÃO É NOME DE GATO. É NOME DE QUEM TE ENCARA ASSIM. ANTES DE NEGAR SEU EMPRÉSTIMO.",
            },
        }
        prompts = pipeline.compose_ltx23_segment_prompts(self.post, concept, segments=2)
        self.assertEqual(len(prompts), 2)
        self.assertIn('"gerald não é nome de gato. é nome de quem te encara assim"', prompts[0])
        self.assertNotIn("empréstimo", prompts[0])
        self.assertIn('"antes de negar seu empréstimo"', prompts[1])
        self.assertNotIn("gerald", prompts[1])
        self.assertIn("pauses", prompts[1])
        with self.assertRaises(ValueError):
            pipeline.compose_ltx23_segment_prompts(self.post, concept, segments=3)


if __name__ == "__main__":
    unittest.main()
