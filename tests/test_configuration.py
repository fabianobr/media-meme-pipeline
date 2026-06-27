from __future__ import annotations

import argparse
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import daily_reddit_meme_pipeline as pipeline  # noqa: E402


class ServiceUrlTests(unittest.TestCase):
    def test_telegram_is_opt_in(self) -> None:
        self.assertFalse(pipeline.build_parser().parse_args([]).telegram)
        self.assertTrue(pipeline.build_parser().parse_args(["--telegram"]).telegram)

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


if __name__ == "__main__":
    unittest.main()
