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


class ServiceUrlTests(unittest.TestCase):
    def test_telegram_is_opt_in(self) -> None:
        self.assertFalse(pipeline.build_parser().parse_args([]).telegram)
        self.assertTrue(pipeline.build_parser().parse_args(["--telegram"]).telegram)

    def test_ltx23_defaults_to_image_to_video(self) -> None:
        args = pipeline.build_parser().parse_args([])
        self.assertEqual(args.video_engine, "ltx23")
        self.assertEqual(args.ltx23_input_mode, "image")

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
        self.assertEqual(result["execution"]["llm_calls"][0]["stage"], "writer")
        self.assertEqual(result["execution"]["llm_calls"][0]["state"], "failed")
        self.assertIn("critic offline", result["execution"]["llm_calls"][0]["error"])

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
        self.assertEqual(len(result["execution"]["llm_calls"]), 6)
        self.assertTrue(all(call["state"] == "completed" for call in result["execution"]["llm_calls"]))
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
