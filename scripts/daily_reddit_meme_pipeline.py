#!/usr/bin/env python3
"""Daily Reddit meme pipeline.

V1 flow:
1. Read Reddit RSS candidates using reddit_meme_dry_run helpers.
2. Ask local Ollama for meme concepts.
3. Queue image generation through the existing n8n -> ComfyUI webhooks.
4. Overlay readable meme text locally with Pillow.
5. Send the final images to Telegram using Hermes env vars, unless disabled.

Video note:
The local ffmpeg video path is review-only. The final target for publishable
video memes is ComfyUI image-to-video with real generated motion, not a slide
composition.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import math
import os
import re
import shutil
import subprocess
import time
import unicodedata
import urllib.parse
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import requests
from PIL import Image, ImageDraw, ImageFont

import reddit_meme_dry_run as reddit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "data" / "media-pipeline" / "reddit-memes"
DEFAULT_FONT = Path("/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf")
DEFAULT_EDGE_TTS_BIN = PROJECT_ROOT / "data" / "media-pipeline" / ".venv-edge-tts" / "bin" / "edge-tts"
DEFAULT_OLLAMA_MODEL = "qwen3:8b"
DEFAULT_VISION_MODEL = "qwen2.5vl:7b"
DEFAULT_CKPT_NAME = "flux1-schnell-fp8.safetensors"
DEFAULT_LTX_CKPT_NAME = "ltx-video-2b-v0.9.5.safetensors"
DEFAULT_LTX_TEXT_ENCODER = "t5xxl_fp16.safetensors"
DEFAULT_LTX23_CKPT_NAME = "ltx-2.3-22b-dev-fp8.safetensors"
DEFAULT_LTX23_TEXT_ENCODER = "gemma_3_12B_it_fp4_mixed.safetensors"
DEFAULT_LTX23_LORA = "ltx_2.3_22b_distilled_1.1_lora_dynamic_fro09_avg_rank_111_bf16.safetensors"
DEFAULT_LTX23_UPSCALER = "ltx-2.3-spatial-upscaler-x2-1.1.safetensors"
LTX23_API_WORKFLOW = PROJECT_ROOT / "workflows" / "03-ltx23-native-t2v-audio-api.json"
LTX23_I2V_API_WORKFLOW = PROJECT_ROOT / "workflows" / "05-ltx23-official-i2v-audio-api.json"
N8N_URL = "http://localhost:5678"
N8N_GENERATE_URL = f"{N8N_URL}/webhook/comfyui-media-generate"
N8N_STATUS_URL = f"{N8N_URL}/webhook/comfyui-media-status"
COMFYUI_URL = "http://localhost:8188"
COMFYUI_VIEW_URL = f"{COMFYUI_URL}/view"
OLLAMA_URL = "http://localhost:11434"
VIDEO_FPS = 30
MIN_LTX_VIDEO_SECONDS = 10.0
CONCEPT_SCHEMA_VERSION = 2
MAX_HUMOR_ROUNDS = 3
MAX_CONCEPTS_PER_POST = 5
VALID_STAGE_STATES = {"pending", "running", "approved", "rejected", "failed"}
HOMELAB_COMPOSE_ROOT = Path(os.environ.get("HOMELAB_COMPOSE_ROOT", "/home/fabiano/homelab-ai"))
HOMELAB_COMPOSE_FILE = HOMELAB_COMPOSE_ROOT / "infra" / "docker" / "docker-compose.yml"


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def configure_service_urls(args: argparse.Namespace) -> None:
    """Apply endpoint precedence: CLI, environment, then localhost defaults."""

    global COMFYUI_URL, COMFYUI_VIEW_URL, N8N_URL, N8N_GENERATE_URL, N8N_STATUS_URL, OLLAMA_URL
    OLLAMA_URL = (args.ollama_url or os.environ.get("OLLAMA_URL") or OLLAMA_URL).rstrip("/")
    COMFYUI_URL = (args.comfyui_url or os.environ.get("COMFYUI_URL") or COMFYUI_URL).rstrip("/")
    N8N_URL = (args.n8n_url or os.environ.get("N8N_URL") or N8N_URL).rstrip("/")
    COMFYUI_VIEW_URL = f"{COMFYUI_URL}/view"
    N8N_GENERATE_URL = f"{N8N_URL}/webhook/comfyui-media-generate"
    N8N_STATUS_URL = f"{N8N_URL}/webhook/comfyui-media-status"


def slugify(value: str, max_len: int = 64) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return (value or "meme")[:max_len].strip("-") or "meme"


def request_json(method: str, url: str, **kwargs: Any) -> Any:
    response = requests.request(method, url, timeout=kwargs.pop("timeout", 60), **kwargs)
    response.raise_for_status()
    return response.json() if response.content else {}


def ltx_valid_frame_count(min_frames: int) -> int:
    """LTXV lengths are accepted as 9 + 8n frames in this workflow."""

    if min_frames <= 9:
        return 9
    return 9 + (((min_frames - 9) + 7) // 8) * 8


def ltx23_workflow_path_for_mode(mode: str) -> Path:
    return LTX23_I2V_API_WORKFLOW if mode in {"image", "source"} else LTX23_API_WORKFLOW


def download_comfy_file(ref: dict[str, str], output_path: Path) -> None:
    params = {
        "filename": ref["filename"],
        "subfolder": ref.get("subfolder", ""),
        "type": ref.get("type", "output"),
    }
    response = requests.get(COMFYUI_VIEW_URL, params=params, timeout=180)
    response.raise_for_status()
    output_path.write_bytes(response.content)


def select_candidates(args: argparse.Namespace) -> list[reddit.RedditPost]:
    posts: list[reddit.RedditPost] = []
    for index, subreddit in enumerate(args.subreddits, 1):
        if index > 1 and args.delay > 0:
            time.sleep(args.delay)
        status, body, _headers, attempts = reddit.fetch_feed(
            subreddit,
            timeout=args.timeout,
            retries=args.retries,
            backoff_base=args.backoff_base,
            backoff_max=args.backoff_max,
            jitter=args.jitter,
        )
        source = "live"
        if status != 200:
            cached = reddit.load_cached_feed(args.cache_dir, subreddit) if args.cache_on_failure else None
            if cached:
                body = cached
                source = "cache"
            else:
                print(f"WARN r/{subreddit}: status={status} attempts={attempts}")
                continue

        parsed = reddit.parse_feed(subreddit, body)
        filtered = reddit.filter_posts(parsed, args.max_age_hours, args.include_automoderator)
        if source == "live" and args.write_cache:
            reddit.write_cached_feed(args.cache_dir, subreddit, body)
        posts.extend(filtered)
        print(f"r/{subreddit}: source={source} usable={len(filtered)} attempts={attempts}")

    return reddit.select_posts(posts, limit=args.limit, max_per_subreddit=args.max_per_subreddit)


def clean_post_summary(value: str, max_len: int = 260) -> str:
    value = re.sub(r"\s+", " ", value or "").strip()
    value = re.sub(r"submitted by /u/[^[]+", "", value, flags=re.IGNORECASE).strip()
    value = value.replace("[link]", "").replace("[comments]", "").strip()
    if len(value) > max_len:
        return value[:max_len].rsplit(" ", 1)[0] + "..."
    return value


def build_source_brief(post: reddit.RedditPost, visual_description: str = "") -> str:
    summary = clean_post_summary(post.summary)
    parts = [f"Reddit post media type: {post.media_type}."]
    if summary:
        parts.append(f"Post context paraphrase: {summary}.")
    if visual_description:
        parts.append(f"Detailed source image description: {visual_description}.")
    if post.media_type == "image":
        parts.append("Treat the source as a still-image meme/news reference; translate it into a cleaner original scene.")
    elif post.media_type == "video":
        parts.append("Treat the source as a short-video moment; create one clear frozen frame, not a sequence.")
    else:
        parts.append("Treat the source as a text-only trend; create a simple visual metaphor.")
    return " ".join(parts)


def compact_visual_description(value: str, max_len: int = 220) -> str:
    value = sanitize_visual_description(value)
    value = re.sub(r"\s*[-•]\s*", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" .,:;-")
    if not value:
        return ""
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", value) if part.strip()]
    if len(sentences) >= 2:
        value = " ".join(sentences[:2])
    return value[:max_len]


def compose_image_prompt(topic: str, visual: str, media_context: str, source_brief: str) -> str:
    return (
        f"{media_context}"
        "Create a clean, focused, meme-ready Brazilian editorial comedy image. "
        f"Source brief: {source_brief} "
        f"Main visual scene: {visual}. "
        "Theme: Brazilian internet news translated into one simple visual joke. "
        "Use exactly one main subject or one clear subject pair, with readable body language and one obvious action. "
        "Camera: medium shot, eye-level or slightly low angle, 35mm lens, sharp focus, natural depth of field. "
        "Composition: central subject, simple physical background, no collage, no split-screen, no busy crowd, no tiny faces, "
        "clean empty space at the top and bottom for later meme text overlay. "
        "Avoid text-prone props; if a screen, paper, chart, poster, uniform, storefront, or sign appears, "
        "make it blank or use only abstract shapes with no readable marks. "
        "Style: realistic photo, Brazilian cultural context, expressive face, coherent hands, coherent eyes, "
        "natural skin texture, believable lighting, controlled colors, high detail, crisp subject edges. "
        "Use fictional generic people; do not recreate an exact real-person likeness. "
        "Do not render words from the prompt as visible objects. "
        "Do not generate any letters, words, captions, subtitles, logos, signs, UI, watermarks, or speech bubbles."
    )


def compact_phrase(value: str, max_len: int = 34) -> str:
    value = re.sub(r"https?://\S+", "", value or "")
    value = re.sub(r"[^\wÀ-ÿ$%ºª/ -]+", " ", value)
    value = " ".join(value.upper().split())
    if len(value) <= max_len:
        return value
    return value[:max_len].rsplit(" ", 1)[0] or value[:max_len]


def context_text(post: reddit.RedditPost) -> str:
    return f"{post.title} {clean_post_summary(post.summary, max_len=420)}".lower()


def score_any(text: str, *needles: str) -> int:
    return sum(1 for needle in needles if needle in text)


STOPWORDS = {
    "a",
    "ao",
    "aos",
    "as",
    "com",
    "da",
    "das",
    "de",
    "do",
    "dos",
    "e",
    "em",
    "era",
    "es",
    "esta",
    "este",
    "eu",
    "for",
    "from",
    "in",
    "is",
    "it",
    "la",
    "las",
    "los",
    "na",
    "nas",
    "no",
    "nos",
    "of",
    "o",
    "os",
    "para",
    "por",
    "pra",
    "que",
    "se",
    "sem",
    "sua",
    "suas",
    "the",
    "to",
    "um",
    "uma",
    "umas",
    "uns",
    "y",
}

GENERIC_SHARE_WORDS = {
    "aí",
    "agora",
    "calma",
    "eu",
    "internet",
    "mundo",
    "normal",
    "pov",
    "quem",
    "todo",
    "todo mundo",
    "vida",
    "virou",
}

ABSTRACT_RELATABLE_WORDS = {
    "chatting",
    "date",
    "misread",
    "overreacting",
    "relationship",
    "slow",
    "moment",
    "confusion",
    "confused",
    "normal",
}

CONCRETE_TOPIC_WORDS = {
    "lamborghini",
    "dubai",
    "nasa",
    "supergirl",
    "mortician",
    "waiter",
    "doctor",
    "cuban",
    "pool",
    "iran",
    "captain",
    "climate",
    "harry",
    "law",
    "movie",
    "cop",
}

CONTRAST_WORDS = {
    "agora",
    "aí",
    "calma",
    "mas",
    "não",
    "nao",
    "pior",
    "virou",
    "só",
    "so",
}

PROMPT_LEAK_WORDS = {
    "caption",
    "censura",
    "do not",
    "logo",
    "no captions",
    "no subtitle",
    "no subtitles",
    "prompt",
    "speech bubble",
    "subtitle",
    "text",
    "ui",
    "watermark",
}


def token_set(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", value.lower()).encode("ascii", "ignore").decode("ascii")
    tokens = re.findall(r"[a-z0-9]+", normalized)
    return {token for token in tokens if token not in STOPWORDS and len(token) > 2}


def score_bucket(score: float, *, lower: float = 0.0, upper: float = 5.0) -> int:
    return max(0, min(5, int(round(max(lower, min(upper, score))))))


def _evaluate_concept_quality_legacy(post: reddit.RedditPost, concept: dict[str, Any]) -> dict[str, Any]:
    top_text = str(concept.get("top_text") or "").strip()
    bottom_text = str(concept.get("bottom_text") or "").strip()
    rationale = str(concept.get("rationale") or "").strip()
    meme_logic = str(concept.get("meme_logic") or "").strip()
    meme_archetype = str(concept.get("meme_archetype") or "").strip()
    image_prompt = str(concept.get("image_prompt") or "").strip()
    source_brief = str(concept.get("source_brief") or "").strip()
    source_text = f"{post.title} {post.summary}"
    source_tokens = token_set(source_text)
    concept_tokens = token_set(" ".join([top_text, bottom_text, rationale, meme_logic, meme_archetype]))
    prompt_tokens = token_set(" ".join([top_text, bottom_text, rationale, meme_logic, meme_archetype, image_prompt, source_brief]))
    overlap = sorted(source_tokens & concept_tokens)

    top_words = len(top_text.split())
    bottom_words = len(bottom_text.split())
    combined_words = top_words + bottom_words
    readability = 5.0
    if not top_text or not bottom_text:
        readability = 0.0
    else:
        if not 2 <= top_words <= 8:
            readability -= 1.0
        if not 2 <= bottom_words <= 10:
            readability -= 1.0
        if combined_words > 16:
            readability -= 1.0
        if combined_words > 20:
            readability -= 1.0
        if top_text.lower() == bottom_text.lower():
            readability -= 2.0
        if re.search(r"\.\.\.|!!+|\?\?+|::+|;;+", f"{top_text} {bottom_text}"):
            readability -= 1.0
        if len({word.lower() for word in top_text.split()}) < max(2, top_words // 2):
            readability -= 1.0
        if len({word.lower() for word in bottom_text.split()}) < max(2, bottom_words // 2):
            readability -= 1.0
    readability = score_bucket(readability)

    laugh = 2.0
    source_fit = 1.0
    if source_tokens:
        coverage = len(overlap) / max(1, min(4, len(source_tokens)))
        source_fit = 1.0 + coverage * 4.0
        source_lower = source_text.lower()
        if any(word in source_lower for word in ("meirl", "internet", "meme", "reddit", "popular")):
            source_fit = max(source_fit, 3.0)
        if source_tokens & ABSTRACT_RELATABLE_WORDS and concept_tokens & {"pov", "internet", "normal", "calma", "eu"}:
            source_fit = max(source_fit, 3.0)
        if source_tokens & CONCRETE_TOPIC_WORDS and concept_tokens & {"pov", "internet", "normal", "calma", "eu"} and not overlap:
            source_fit = min(source_fit, 0.0)
            laugh = max(0.0, laugh - 1.0)
        if source_tokens & {"cats", "cat", "gato", "gatos", "nicknames", "apelidos", "nonsense"}:
            if concept_tokens & {"gato", "gatos", "apelido", "apelidos", "nome", "nomes", "nonsense"}:
                source_fit = max(source_fit, 4.0)
            elif overlap:
                source_fit = max(source_fit, 3.0)
            elif concept_tokens & {"pov", "spiral"}:
                source_fit = min(source_fit, 1.0)
            else:
                source_fit = min(source_fit, 2.0)
    if source_tokens & {"meirl", "meme", "reddit"} and concept_tokens & {"eu", "normal", "internet", "pov", "calma"}:
        source_fit = max(source_fit, 4.0)
    source_fit = score_bucket(source_fit)

    if top_text and bottom_text and top_text.lower() != bottom_text.lower():
        laugh += 1.0
    if any(word in f"{top_text} {bottom_text} {rationale} {meme_logic}".lower() for word in CONTRAST_WORDS):
        laugh += 1.0
    if meme_archetype in {item["id"] for item in VIRAL_MEME_ARCHETYPES}:
        laugh += 0.5
    if rationale and not rationale.lower().startswith("arquetipo local de meme"):
        laugh += 0.5
    if combined_words <= 16:
        laugh += 0.5
    if "?" in top_text or "?" in bottom_text:
        laugh -= 0.5
    laugh = score_bucket(laugh)

    share = 1.0
    if combined_words <= 14:
        share += 1.0
    if len(top_text) <= 42 and len(bottom_text) <= 42:
        share += 1.0
    if prompt_tokens & GENERIC_SHARE_WORDS:
        share += 1.0
    if any(token in source_tokens for token in {"cats", "gato", "gatos", "meirl", "world", "internet"}):
        share += 0.5
    if len(source_tokens) >= 4 and not overlap:
        share -= 1.0
    if len({*top_text.split(), *bottom_text.split()}) <= 4:
        share -= 1.0
    share = score_bucket(share)

    artifact = 5.0
    prompt_text = f"{top_text} {bottom_text} {rationale} {meme_logic} {meme_archetype}".lower()
    prompt_body = image_prompt.lower()
    if any(word in prompt_body for word in PROMPT_LEAK_WORDS):
        artifact -= 1.0
    if len(image_prompt.split()) > 220:
        artifact -= 1.0
    if len(prompt_body) > 1800:
        artifact -= 1.0
    if re.search(r"\b(fake|fake text|readable|logo|watermark|ui)\b", prompt_text):
        artifact -= 1.0
    artifact = score_bucket(artifact)

    weighted_total = round(
        readability * 0.25
        + source_fit * 0.30
        + laugh * 0.20
        + share * 0.15
        + artifact * 0.10,
        2,
    )
    approved = (
        readability >= 4
        and source_fit >= 1
        and laugh >= 3
        and artifact >= 2
        and weighted_total >= 3.0
    )
    reasons: list[str] = []
    if readability < 4:
        reasons.append("texto pouco legivel")
    if source_fit < 1:
        reasons.append("pouco ancorado no post")
    if laugh < 3:
        reasons.append("piada fraca")
    if share < 3:
        reasons.append("baixa compartilhabilidade")
    if artifact < 2:
        reasons.append("alto risco de artefato")
    if weighted_total < 3.0:
        reasons.append("nota ponderada baixa")

    return {
        "approved": approved,
        "scores": {
            "readability": readability,
            "source_fit": source_fit,
            "laugh": laugh,
            "share": share,
            "artifact": artifact,
            "weighted_total": weighted_total,
        },
        "reason": "; ".join(reasons) if reasons else "rubric aprovada",
    }


def evaluate_concept_quality(post: reddit.RedditPost, concept: dict[str, Any]) -> dict[str, Any]:
    """Apply hard quality gates; an unavailable independent critic can never approve."""

    baseline = _evaluate_concept_quality_legacy(post, concept)
    review = concept.get("humor_review")
    scores10 = review.get("scores") if isinstance(review, dict) else None
    required = ("source_fit", "natural_ptbr", "surprise", "laugh", "visual_payoff")
    valid_review = (
        isinstance(review, dict)
        and review.get("approved") is True
        and not review.get("fallback_used")
        and isinstance(review.get("winner_id"), int)
        and review.get("winner_id", 0) > 0
        and isinstance(scores10, dict)
        and all(
            isinstance(scores10.get(name), (int, float))
            and math.isfinite(float(scores10[name]))
            and 0 <= float(scores10[name]) <= 10
            for name in required
        )
    )

    independent = {
        name: round(max(0.0, min(10.0, float(scores10.get(name, 0)))) / 2.0, 1)
        if isinstance(scores10, dict)
        else 0.0
        for name in required
    }
    top = str(concept.get("top_text") or "").strip()
    middle = str(concept.get("middle_text") or "").strip()
    bottom = str(concept.get("bottom_text") or "").strip()
    joke_text = " ".join((top, middle, bottom))
    source_tokens = token_set(f"{post.title} {clean_post_summary(post.summary)}")
    joke_tokens = token_set(joke_text)
    overlap_ratio = len(source_tokens & joke_tokens) / max(1, len(joke_tokens))
    paraphrase_risk = bool(joke_tokens) and overlap_ratio >= 0.8 and independent["surprise"] < 4
    generic_risk = (
        len(source_tokens & joke_tokens) == 0
        and len(source_tokens) >= 3
        and (not valid_review or independent["source_fit"] < 4)
    )

    readability = float(baseline["scores"]["readability"])
    source_fit = independent["source_fit"] if valid_review else 0.0
    humor = min(independent["surprise"], independent["laugh"])
    share = round((independent["laugh"] + independent["visual_payoff"] + independent["natural_ptbr"]) / 3, 1)
    reasons: list[str] = []
    if not valid_review:
        reasons.append("critica independente ausente ou invalida")
    if readability < 4:
        reasons.append("legibilidade abaixo de 4/5")
    if source_fit < 4:
        reasons.append("ligacao com a fonte abaixo de 4/5")
    if humor < 4:
        reasons.append("humor ou surpresa abaixo de 4/5")
    if share < 3:
        reasons.append("compartilhamento abaixo de 3/5")
    if independent["natural_ptbr"] < 4:
        reasons.append("PT-BR pouco natural")
    if generic_risk:
        reasons.append("texto generico sem detalhe reconhecivel do post")
    if paraphrase_risk:
        reasons.append("possivel traducao ou parafrase sem virada")

    approved = not reasons
    return {
        "approved": approved,
        "scores": {
            "readability": readability,
            "source_fit": source_fit,
            "humor": humor,
            "share": share,
            "natural_ptbr": independent["natural_ptbr"],
            "surprise": independent["surprise"],
            "punchline": independent["laugh"],
            "visual_payoff": independent["visual_payoff"],
        },
        "checks": {
            "independent_review_valid": valid_review,
            "generic_risk": generic_risk,
            "paraphrase_risk": paraphrase_risk,
        },
        "reason": "; ".join(reasons) if reasons else "limiares obrigatorios atendidos",
    }


VIRAL_MEME_ARCHETYPES: list[dict[str, Any]] = [
    {
        "id": "this_is_fine",
        "format": "calm denial inside obvious chaos",
        "use_when": "crisis, alerts, geopolitics, systems failing, people pretending everything is normal",
        "top_pattern": "EU FINGINDO NORMALIDADE",
        "bottom_pattern": "O CONTEXTO: PEGANDO FOGO",
        "visual_pattern": (
            "one calm person doing a normal tiny task while the environment around them is clearly collapsing in a safe, "
            "cartoonishly dramatic way; comedy comes from denial versus visible chaos"
        ),
        "needles": [
            "alerta",
            "defesa civil",
            "iran",
            "ormuz",
            "israel",
            "moscow",
            "ataques",
            "closed",
            "crise",
            "sick",
            "subway",
            "bit me",
            "crazy",
            "not surprising",
        ],
    },
    {
        "id": "drake_yes_no",
        "format": "reject obvious thing, approve absurd alternative",
        "use_when": "a decision, policy, company behavior, regulation, preference, bad tradeoff",
        "top_pattern": "SOLUÇÃO SIMPLES? NÃO",
        "bottom_pattern": "A MAIS ABSURDA? AGORA SIM",
        "visual_pattern": (
            "one person dismissing a simple blank option and enthusiastically approving a more absurd blank option, "
            "clear two-choice body language without split-screen or readable text"
        ),
        "needles": ["empresa", "ceo", "taxa", "gratu", "tip", "regra", "rule", "var", "economiz", "passageiro", "20%"],
    },
    {
        "id": "galaxy_brain",
        "format": "escalating logic until it becomes ridiculous",
        "use_when": "overthinking, bureaucracy, tech, plans, optimization, official process",
        "top_pattern": "PENSARAM DEMAIS",
        "bottom_pattern": "E FUNCIONOU PIOR",
        "visual_pattern": (
            "one person in front of increasingly dramatic abstract idea lights, as a tiny normal problem becomes an absurd "
            "over-engineered solution; no diagrams or readable text"
        ),
        "needles": [
            "kpi",
            "planilha",
            "fiscal",
            "prova",
            "intervenção",
            "penal",
            "logística",
            "suprimentos",
            "advice",
            "men in their 40s",
            "men in their 20s",
        ],
    },
    {
        "id": "boss_fight",
        "format": "ordinary problem presented like final boss",
        "use_when": "mundane task becomes extreme, sports pressure, exam, delivery, travel, logistics",
        "top_pattern": "ERA SÓ UMA TAREFA",
        "bottom_pattern": "VIROU BOSS FINAL",
        "visual_pattern": (
            "one ordinary person facing a hilariously overdramatic challenge staged like a final boss moment, cinematic scale, "
            "but the actual problem remains everyday and readable"
        ),
        "needles": [
            "prova",
            "stress",
            "estresse",
            "antárt",
            "antarct",
            "kc-390",
            "world cup",
            "final",
            "major",
            "furia",
            "40,000",
            "taking over",
            "houston",
        ],
    },
    {
        "id": "pov_spiral",
        "format": "POV: you try normal life, internet/trend ruins it",
        "use_when": "relatable posts, meirl, online culture, absurd image, viral phrase, daily frustration",
        "top_pattern": "EU TENTANDO SER NORMAL",
        "bottom_pattern": "A INTERNET: CALMA AÍ",
        "visual_pattern": (
            "one tired person attempting a normal daily activity while one absurd internet-shaped situation interrupts it, "
            "relatable POV comedy with strong facial expression"
        ),
        "needles": [
            "meirl",
            "what the hell",
            "internet",
            "touch grass",
            "benefits of egg",
            "palavra nova",
            "meme",
            "found the strength",
            "don’t feel any better",
            "don't feel any better",
        ],
    },
    {
        "id": "starter_pack",
        "format": "recognizable stereotype compressed into a scene",
        "use_when": "trend describes a type of person, fandom, tourist, worker, voter, student, online group",
        "top_pattern": "O PERSONAGEM:",
        "bottom_pattern": "100% RECONHECÍVEL",
        "visual_pattern": (
            "one archetypal person surrounded by three or four simple symbolic props that reveal the stereotype instantly, "
            "not a collage, no labels, no readable objects"
        ),
        "needles": [
            "tourist",
            "turista",
            "torcedor",
            "student",
            "professor",
            "doutoranda",
            "fan",
            "worker",
            "dutchmen",
            "men in their 40s",
            "baseball",
            "national anthem",
        ],
    },
    {
        "id": "expectation_reality",
        "format": "expected outcome contradicted by immediate reality",
        "use_when": "cleaned but dirty again, fixed but broken, plan versus result, promise versus delivery",
        "top_pattern": "PROBLEMA RESOLVIDO",
        "bottom_pattern": "POR 7 MINUTOS",
        "visual_pattern": (
            "one proud person presenting a freshly solved problem while the exact same problem immediately returns behind them, "
            "clear expectation versus reality in one scene"
        ),
        "needles": ["green", "cleaned", "limpeza", "barreira", "calçada", "natureza", "already"],
    },
    {
        "id": "npc_side_quest",
        "format": "real life suddenly becomes a side quest",
        "use_when": "unexpected kindness, bus, small rescue, odd daily event, surreal task",
        "top_pattern": "MISSÃO ALEATÓRIA",
        "bottom_pattern": "RECOMPENSA: FÉ NA HUMANIDADE",
        "visual_pattern": (
            "one ordinary person suddenly receiving a small real-life side quest in the street and completing it with wholesome "
            "comedic timing, warm readable action"
        ),
        "needles": [
            "helped",
            "catch",
            "bus",
            "ônibus",
            "salvou",
            "saved",
            "ajudou",
            "caretakers",
            "orphaned chicks",
            "swapped",
            "exchange caps",
            "national anthem",
        ],
    },
    {
        "id": "chill_guy_energy",
        "format": "absurd situation met with total calm",
        "use_when": "sports nerves, chaos, political drama, logistical extremes, someone underreacting",
        "top_pattern": "EU TENTANDO FICAR DE BOA",
        "bottom_pattern": "O ROTEIRO: DUVIDO",
        "visual_pattern": (
            "one relaxed person with hands in pockets staying strangely calm while an absurd dramatic event unfolds nearby, "
            "relatable deadpan contrast, original human character"
        ),
        "needles": ["gol", "vini", "endrick", "furia", "final", "debate", "payback", "moscow", "found the strength"],
    },
    {
        "id": "brainrot_absurd",
        "format": "surreal AI-era absurdity, but still tied to the topic",
        "use_when": "post is already nonsensical, vague, very online, egg/object/animal/object absurdity",
        "top_pattern": "O ALGORITMO COZINHOU",
        "bottom_pattern": "E EU COMI A PIADA",
        "visual_pattern": (
            "one surreal but clean AI-era object metaphor representing the topic, absurd enough to be funny but still readable, "
            "no grotesque details, no text"
        ),
        "needles": ["egg", "ovo", "what the hell", "brainrot", "absurd", "unexpected"],
    },
]


def archetype_catalog() -> str:
    lines = []
    for item in VIRAL_MEME_ARCHETYPES:
        lines.append(
            f"- {item['id']}: {item['format']}. Use quando: {item['use_when']}. "
            f"Texto exemplo: {item['top_pattern']} / {item['bottom_pattern']}"
        )
    return "\n".join(lines)


def meme_strategy(post: reddit.RedditPost) -> dict[str, str]:
    """Choose a viral meme archetype, then adapt it to the post."""

    title = post.title.strip()
    text = context_text(post)
    subject = compact_phrase(title)
    default_scope = "MUNDO" if post.subreddit == "popular" else "BRASIL"

    scored = [
        {
            **item,
            "score": score_any(text, *item["needles"]),
        }
        for item in VIRAL_MEME_ARCHETYPES
    ]
    best = max(scored, key=lambda item: item["score"])
    if int(best["score"]) > 0:
        return {
            "top_text": str(best["top_pattern"]),
            "bottom_text": str(best["bottom_pattern"]),
            "visual": str(best["visual_pattern"]),
            "meme_logic": str(best["id"]),
            "meme_format": str(best["format"]),
        }

    return {
        "top_text": f"EU ABRI O {default_scope}",
        "bottom_text": "NÃO ERA PRA ENTENDER TUDO",
        "visual": (
            f"one tired person staring at a blank phone screen while a simple symbolic prop hints at {subject.lower()}, "
            "relatable internet-news confusion, clean background, no readable text"
        ),
        "meme_logic": "pov_spiral",
        "meme_format": "generic viral-news POV confusion",
    }


def build_video_script(post: reddit.RedditPost, concept: dict[str, str], visual_description: str = "") -> dict[str, Any]:
    title = compact_phrase(post.title, max_len=48).lower()
    archetype = str(concept.get("meme_archetype") or concept.get("meme_logic") or "pov_spiral")
    setup = str(concept.get("top_text") or "EU TENTANDO SER NORMAL")
    escalation = str(concept.get("middle_text") or "AÍ EU OLHEI MAIS DE PERTO")
    punchline = str(concept.get("bottom_text") or "A INTERNET: CALMA AÍ")
    visual_summary = compact_visual_description(visual_description, max_len=140)

    if visual_summary:
        base_scene = (
            f"Clean fictionalized version of the source image: {visual_summary}. "
            "Keep the same composition, lighting, and object placement. "
            "No readable text, labels, screens, signs, posters, or extra objects."
        )
        cat_scene = bool(re.search(r"\b(cat|gato|gatos|cat-like|feline)\b", f"{title} {visual_summary}", re.I))
        if cat_scene:
            character = "The orange cat stays seated in the same place, blinks once, and makes a tiny ear twitch."
            prop = "The cat itself is the main subject, preserved clearly and not replaced."
        elif re.search(r"\b(homem|mulher|pessoa|adulto|jovem|criança|person|man|woman)\b", visual_summary, re.I):
            character = (
                "The visible human subject, kept generic and fictional, with the same approximate pose, stable face, "
                "and small readable reaction."
            )
            prop = "The visible human subject is the main subject, preserved clearly and not replaced."
        else:
            character = (
                "One fictional cat or person already present in the scene, mostly still, with a serious readable reaction."
            )
            prop = "The main visible subject from the source image, preserved clearly and not replaced."

        if archetype == "this_is_fine":
            action = [
                "0-3s: subject holds a fixed stare toward the camera.",
                "3-7s: subject blinks once and makes a tiny shoulder or ear shift while staying in place.",
                "7-10s: subject freezes again with a deadpan look.",
            ]
            beat = "deadpan reaction inside the exact source-image situation"
        elif archetype == "boss_fight":
            action = [
                "0-3s: the cat notices the scene and holds a serious stare.",
                "3-7s: camera slowly pushes in while the cat makes one tiny cautious movement.",
                "7-10s: the cat pauses, then gives a defeated look to camera.",
            ]
            beat = "ordinary source-image subject staged like a final boss"
        elif archetype == "expectation_reality":
            action = [
                "0-3s: subject makes one small confident motion as if the situation is under control.",
                "3-7s: the same visible situation remains unchanged and the subject notices it.",
                "7-10s: subject stops and stares at the camera with a resigned expression.",
            ]
            beat = "confidence contradicted by the exact visible reality"
        elif archetype == "npc_side_quest":
            action = [
                "0-3s: subject notices the source-image prop as if receiving a tiny mission.",
                "3-7s: subject completes one small motion using the existing prop with exaggerated seriousness.",
                "7-10s: subject waits, gets nothing, then gives a confused satisfied look.",
            ]
            beat = "source-image situation becomes a tiny side quest"
        else:
            action = [
                "0-3s: the cat holds a fixed stare toward the camera.",
                "3-7s: the cat blinks once and makes a tiny shift while staying seated.",
                "7-10s: the cat pauses, then gives a tiny defeated nod.",
            ]
            beat = "the source-image situation becomes absurd without changing location"

        dialogue = f"{setup}. {escalation}. {punchline}."
        return {
            "scene": base_scene,
            "character": character,
            "main_prop": prop,
            "source_visual_description": visual_description,
            "camera": "Single continuous shot, preserve source framing, very slow push-in, no cuts, no scene transition.",
            "timeline": action,
            "comedy_beat": beat,
            "dialogue": dialogue,
            "audio": "Brazilian Portuguese narrator reads the dialogue naturally with dry comic timing.",
            "visual_rules": (
                "Preserve the source-image location and objects. No captions, no subtitles, no readable text, no logos, no UI, "
                "no split screen, no panels, no montage, no scene change, no extra characters."
            ),
        }

    if "boiling water" in title or "ramen" in title:
        base_scene = (
            "A small Brazilian kitchen at night, warm overhead light, plain stove, one pot of steaming water, "
            "simple counter, no labels, no readable numbers, no visible text anywhere."
        )
        character = (
            "One fictional Brazilian adult, tired but expressive, casual T-shirt, standing near the stove, "
            "natural hands, readable facial reactions, looking from the pot to the camera."
        )
    elif archetype == "this_is_fine":
        base_scene = (
            "A modest Brazilian living room at night, warm lamp, plain wall, small table with a coffee cup, "
            "orange light and safe moving shadows in the background, no visible text anywhere."
        )
        character = (
            "One fictional Brazilian adult, calm but visibly tense, casual hoodie, seated with coffee, "
            "natural hands, expressive eyes, looking between the room and the camera."
        )
    else:
        base_scene = (
            "A modest Brazilian apartment room at night, warm desk lamp, plain wall, small table, "
            "one smartphone with an unreadable blank screen glow, no visible text anywhere."
        )
        character = (
            "One fictional Brazilian adult, tired but expressive, casual hoodie, seated at the table, "
            "large readable facial reactions, natural hands, looking between the phone and the camera."
        )
    prop = f"one simple symbolic prop related to {title}, kept abstract and unreadable"

    if archetype == "this_is_fine":
        action = [
            "0-3s: character calmly sips coffee and gives a tiny forced smile while the room lighting subtly warms up.",
            "3-7s: safe cartoon-like chaos builds in the background through orange light and moving shadows, character pretends not to notice.",
            "7-10s: character slowly turns to camera, smile freezes, eyes widen, then a tiny defeated nod.",
        ]
        beat = "deadpan denial while the situation visibly gets worse"
    elif archetype == "boss_fight":
        action = [
            "0-3s: character notices the ordinary prop on the table and leans back like it is intimidating.",
            "3-7s: camera pushes in as the prop is lit dramatically, character raises hands like facing a final challenge.",
            "7-10s: character takes one tiny brave move, instantly regrets it, and looks at camera in defeat.",
        ]
        beat = "ordinary problem staged like a final boss"
    elif archetype == "expectation_reality":
        action = [
            "0-3s: character proudly fixes or organizes one simple thing on the table, relaxed smile.",
            "3-7s: the same problem immediately returns in a small visual way, character notices too late.",
            "7-10s: character's smile collapses into a slow stare at camera, then a small sigh.",
        ]
        beat = "promise versus immediate reality"
    elif archetype == "npc_side_quest":
        action = [
            "0-3s: character is doing a normal daily task and suddenly notices the symbolic prop calling for attention.",
            "3-7s: character accepts the absurd small task with exaggerated seriousness and completes one simple action.",
            "7-10s: character receives no real reward, just a confused satisfied look to camera.",
        ]
        beat = "daily life suddenly becomes a side quest"
    elif archetype == "starter_pack":
        action = [
            "0-3s: character enters frame holding the symbolic prop with confident over-specific energy.",
            "3-7s: two or three simple props slide or appear around the character, all blank and unreadable.",
            "7-10s: character proudly poses, then breaks character with a self-aware embarrassed glance.",
        ]
        beat = "recognizable internet stereotype compressed into one person"
    else:
        action = [
            "0-3s: character sits calmly, unlocks the phone, and expects a normal moment.",
            "3-7s: phone glow intensifies, character's eyebrows rise, smile slowly disappears, shoulders tense.",
            "7-10s: character lowers the phone, stares directly at camera, defeated pause, then tiny awkward nod.",
        ]
        beat = "normal life interrupted by internet absurdity"

    dialogue = ""
    return {
        "scene": base_scene,
        "character": character,
        "main_prop": prop,
        "source_visual_description": visual_description,
        "camera": "Single continuous shot, medium shot to subtle close-up, slow handheld push-in, no cuts.",
        "timeline": action,
        "comedy_beat": beat,
        "dialogue": dialogue,
        "audio": "Dry short narrator voice reads the dialogue; add a tiny awkward pause after the punchline.",
        "visual_rules": (
            "No captions, no subtitles, no readable text, no logos, no UI, no split screen, no panels, "
            "no montage, no scene change, no extra characters."
        ),
    }


def compose_scripted_image_prompt(script: dict[str, Any], source_brief: str) -> str:
    return (
        "Create the first frame for a short realistic meme video. "
        f"Source brief: {source_brief} "
        f"Scene: {script.get('scene')} "
        f"Character: {script.get('character')} "
        f"Main prop: {script.get('main_prop')} "
        "Frame moment: the first second before the reaction begins, subject is stable and clearly visible. "
        "Preserve the source-image setting and visible prop family; do not relocate to an unrelated apartment, desk, or phone scene. "
        "Composition: medium shot, central subject, simple background, clean top and bottom space for later caption overlay. "
        "Lighting: natural warm practical lighting, controlled colors, sharp subject edges, coherent hands, coherent eyes. "
        "Style: realistic photo, Brazilian everyday context, no dramatic transformation, no surreal clouds, no abstract smoke. "
        "Do not render any letters, captions, subtitles, logos, signs, UI, watermarks, or speech bubbles."
    )


def fallback_concept(post: reddit.RedditPost, visual_description: str = "") -> dict[str, str]:
    title = post.title.strip()
    strategy = meme_strategy(post)
    top_text = strategy["top_text"]
    bottom_text = strategy["bottom_text"]
    visual = strategy["visual"]

    media_context = {
        "image": "Use the source post only for the idea, not for literal visual details. ",
        "video": "Use the source video only for the situation, as one clean frozen frame. ",
        "text": "",
    }.get(post.media_type, "")
    source_brief = build_source_brief(post, visual_description)
    concept = {
        "top_text": top_text,
        "bottom_text": bottom_text,
        "image_prompt": compose_image_prompt(title, visual, media_context, source_brief),
        "source_brief": source_brief,
        "meme_logic": strategy["meme_logic"],
        "meme_format": strategy["meme_format"],
        "rationale": f"Arquetipo local de meme: {strategy['meme_logic']}; midia: {post.media_type}.",
    }
    concept["source_visual_description"] = visual_description
    concept["video_script"] = build_video_script(post, concept, visual_description)
    concept["image_prompt"] = compose_scripted_image_prompt(concept["video_script"], source_brief)
    return concept


def extract_json_array(text: str) -> list[dict[str, Any]] | None:
    text = text.strip()
    match = re.search(r"\[[\s\S]*\]", text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


def extract_json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def humor_candidate_issues(candidate: dict[str, Any], source_text: str = "") -> list[str]:
    setup = str(candidate.get("setup") or "").strip()
    escalation = str(candidate.get("escalation") or "").strip()
    punchline = str(candidate.get("punchline") or "").strip()
    comic_turn = str(candidate.get("comic_turn") or "").strip()
    combined = f"{setup} {escalation} {punchline}".lower()
    issues: list[str] = []
    if len(setup.split()) > 9 or len(escalation.split()) > 12 or len(punchline.split()) > 9:
        issues.append("frases longas")
    if not escalation:
        issues.append("falta escalada narrativa")
    if "..." in combined or re.search(r"\be\s*\?+$", setup.lower()):
        issues.append("setup incompleto ou com reticencias")
    if re.search(r"\b(ó|hein|né)\b", combined):
        issues.append("bordao solto usado para forcar naturalidade")
    invented_concrete_terms = (
        "pizza",
        "adubo",
        "jardineiro",
        "formulario",
        "licenca",
        "vizinho",
        "drink",
        "cheiro",
        "agua",
    )
    invented = [term for term in invented_concrete_terms if re.search(rf"\b{term}\w*\b", combined)]
    if invented:
        issues.append("inventa elemento concreto ausente: " + ", ".join(invented))
    named_services = ("amazon", "correios", "ifood", "uber", "mercado livre", "shopee", "fedex", "ups")
    invented_services = [
        service
        for service in named_services
        if service in combined and service not in source_text.lower()
    ]
    if invented_services:
        issues.append("inventa marca ou servico ausente: " + ", ".join(invented_services))
    if len(comic_turn.split()) < 7 or comic_turn.count(",") >= 3:
        issues.append("comic_turn nao explica uma mudanca de sentido")
    punchline_tokens = token_set(punchline)
    if punchline_tokens and source_text:
        described = punchline_tokens & token_set(source_text)
        if len(described) / len(punchline_tokens) >= 0.6:
            issues.append("punchline apenas descreve a cena visivel; falta reinterpretacao")
    return issues


def is_vision_capable_model(model_name: str) -> bool:
    lowered = model_name.lower()
    return any(marker in lowered for marker in ("vl", "vision", "llava"))


def encode_image_for_vision(image_path: Path, max_side: int = 1280) -> str | None:
    if not image_path.exists():
        return None
    try:
        image = Image.open(image_path).convert("RGB")
        image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=92)
        return base64.b64encode(buffer.getvalue()).decode("ascii")
    except Exception:
        return None


def improve_humor_concept(
    post: reddit.RedditPost,
    concept: dict[str, Any],
    model: str,
    timeout: int,
    visual_description: str,
    critic_model: str | None = None,
    second_critic_model: str | None = None,
    seed_candidates: list[dict[str, Any]] | None = None,
    image_path: Path | None = None,
) -> dict[str, Any]:
    critic_model = critic_model or model
    round_limit = 1 if seed_candidates is not None else MAX_HUMOR_ROUNDS
    execution = concept.setdefault("execution", {"state": "pending", "attempts": {}})
    llm_calls = execution.setdefault("llm_calls", [])
    encoded_critic_image = encode_image_for_vision(image_path) if image_path is not None else None
    if seed_candidates is not None:
        execution["humor_source"] = "frozen_seeds"

    def timed_humor_request(call_kind: str, round_number: int, payload: dict[str, Any]) -> dict[str, Any]:
        call_model = str(payload.get("model") or model)
        call_record: dict[str, Any] = {
            "stage": call_kind,
            "round": round_number,
            "model": call_model,
            "timeout_seconds": timeout,
            "started_at": datetime.now().astimezone().isoformat(),
            "state": "running",
        }
        llm_calls.append(call_record)
        started = time.monotonic()
        print(f"Humor {call_kind} round {round_number}/{round_limit} started ({call_model}, timeout={timeout}s)")
        try:
            response = request_json("POST", f"{OLLAMA_URL}/api/chat", json=payload, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            call_record["state"] = "failed"
            call_record["error"] = str(exc)
            raise
        else:
            call_record["state"] = "completed"
            content = str((response.get("message") or {}).get("content") or "")
            call_record["response_chars"] = len(content)
            call_record["response_preview"] = content[:500]
        finally:
            call_record["elapsed_seconds"] = round(time.monotonic() - started, 3)
            call_record["finished_at"] = datetime.now().astimezone().isoformat()
            print(
                f"Humor {call_kind} round {round_number}/{round_limit} "
                f"{call_record['state']} in {call_record['elapsed_seconds']:.3f}s"
            )
        return response

    safety_context = f"{post.title} {post.summary}".lower()
    sensitive_terms = (
        "earthquake",
        "terremoto",
        "killed",
        "dead",
        "death",
        "fatal",
        "hospital",
        "disaster",
        "desastre",
        "war ",
        "guerra",
    )
    matched_sensitive = [term.strip() for term in sensitive_terms if term in safety_context]
    if matched_sensitive:
        concept["humor_approved"] = False
        concept["humor_review"] = {
            "approved": False,
            "reason": "tema sensivel bloqueado para humor automatico: " + ", ".join(matched_sensitive),
        }
        return concept

    candidates_schema = {
        "type": "array",
        "minItems": MAX_CONCEPTS_PER_POST,
        "maxItems": MAX_CONCEPTS_PER_POST,
        "items": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "mechanic": {"type": "string"},
                "setup": {"type": "string"},
                "escalation": {"type": "string"},
                "punchline": {"type": "string"},
                "comic_turn": {"type": "string"},
                "scene_payoff": {"type": "string"},
            },
            "required": ["id", "mechanic", "setup", "escalation", "punchline", "comic_turn", "scene_payoff"],
        },
    }
    review_schema = {
        "type": "object",
        "properties": {
            "evaluations": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_CONCEPTS_PER_POST,
                "items": {
                    "type": "object",
                    "properties": {
                        "candidate_id": {"type": "integer"},
                        "approved": {"type": "boolean"},
                        "scores": {
                            "type": "object",
                            "properties": {
                                "source_fit": {"type": "number"},
                                "natural_ptbr": {"type": "number"},
                                "surprise": {"type": "number"},
                                "laugh": {"type": "number"},
                                "visual_payoff": {"type": "number"},
                            },
                            "required": ["source_fit", "natural_ptbr", "surprise", "laugh", "visual_payoff"],
                        },
                        "reason": {"type": "string"},
                    },
                    "required": ["candidate_id", "approved", "scores", "reason"],
                },
            },
        },
        "required": ["evaluations"],
    }
    source = {
        "title": post.title,
        "summary": post.summary[:700],
        "visual_description": visual_description,
        "current_setup": concept.get("top_text", ""),
        "current_punchline": concept.get("bottom_text", ""),
        "current_logic": concept.get("meme_logic", ""),
    }
    try:
        rounds: list[dict[str, Any]] = []
        feedback = ""
        for round_number in range(1, round_limit + 1):
            writer_prompt = f"""
Crie exatamente {MAX_CONCEPTS_PER_POST} alternativas de piada curta para um meme brasileiro baseado somente nos fatos deste post.
Cubra abordagens diferentes: contraste, inversao, escalada, observacao especifica e expectativa versus realidade.

Regras obrigatorias:
- A piada deve acrescentar uma interpretacao inesperada; nunca apenas descrever a imagem.
- Cada alternativa deve depender de ao menos um detalhe reconhecivel deste post.
- Rejeite mentalmente traducao, parafrase e frases que serviriam para qualquer noticia.
- Nao invente pizza, vizinhos, formularios, letreiros, funcionarios, objetos ou acontecimentos ausentes.
- Setup com no maximo 9 palavras, escalada com no maximo 12 e punchline com no maximo 9.
- A escalada deve acrescentar uma observacao concreta e preparar a virada, sem revelar a punchline.
- A punchline deve reinterpretar uma palavra ou premissa do setup.
- Prefira frases comprimidas e faladas no Brasil; evite slogans e explicacoes.
- O payoff visual deve usar somente sujeito, veiculo, pacote, terreno e objetos realmente visiveis.
- comic_turn deve explicar a virada em uma frase completa; nao liste objetos.
- Nao invente adubo, pizza, jardineiro, vizinhos, formularios, bebidas ou outros elementos concretos.
- Nao use "recebi o pacote com", "nao era pra entender", "crise existencial", "o algoritmo cozinhou" ou equivalentes.
- Nao ataque pessoa privada nem use aparencia fisica como piada.
- Responda somente com o array JSON solicitado, sem introducao, markdown ou comentario final.
- Modelo de compressao (nao copie os substantivos): para uma foto de guarda-chuva enorme sob garoa,
  setup "CHUVA: DOIS PINGOS", escalada "ELE ABRIU O EQUIPAMENTO", punchline "DEFESA CIVIL PARTICULAR".
  Note que a punchline nomeia uma interpretacao nova; ela nao diz apenas que o guarda-chuva e grande.
- Segundo modelo aprovado em producao (nao copie os substantivos): para uma foto de gato de nome
  humano encarando a camera, setup "GERALD NAO E NOME DE GATO", escalada "E NOME DE QUEM TE ENCARA ASSIM",
  punchline "ANTES DE NEGAR SEU EMPRESTIMO". A virada da ao sujeito uma profissao, papel social ou
  intencao inesperada e especifica; esse e o padrao que mais aprova.
- A punchline nunca pode ser uma frase aleatoria: se ela nao se conecta ao setup por uma palavra ou
  premissa, descarte e escreva outra.
- A punchline nunca pode descrever o que a imagem ja mostra nem apenas revelar a cena; o climax
  precisa estar na punchline. Se o setup for a parte engracada e a punchline so "explicar", descarte.
- A virada (papel, profissao ou intencao inesperada) precisa se ancorar em algo literalmente
  visivel na cena (um objeto, uma pose, uma cor, uma expressao) — nunca em um lugar, tema ou
  conceito abstrato sem nenhuma pista visual correspondente. Exemplo do que descartar: usar
  "Brasil" ou "astronomo" para um gato sonolento com fundo escuro de luzes, sem nada na cena
  que sugira essas ideias.
{f"- Corrija estes problemas apontados na rodada anterior: {feedback}" if feedback else ""}

Fonte:
{json.dumps(source, ensure_ascii=False)}
""".strip()
            if seed_candidates is not None:
                candidates = deepcopy(seed_candidates)
            else:
                writer_data = timed_humor_request(
                    "writer",
                    round_number,
                    {
                    "model": model,
                    "stream": False,
                    "think": False,
                    "format": candidates_schema,
                    "messages": [
                        {"role": "system", "content": "Voce e um redator de humor brasileiro conciso e observacional."},
                        {"role": "user", "content": writer_prompt},
                    ],
                    "options": {"temperature": 0.85, "num_predict": 1500},
                    },
                )
                writer_content = (writer_data.get("message") or {}).get("content") or ""
                candidates = extract_json_array(writer_content)
                if not candidates:
                    writer_object = extract_json_object(writer_content)
                    nested_candidates = writer_object.get("candidates") if isinstance(writer_object, dict) else None
                    if isinstance(nested_candidates, list):
                        candidates = [item for item in nested_candidates if isinstance(item, dict)]
            if not candidates:
                raise ValueError("humor writer did not return candidates")

            critic_prompt = f"""
Avalie as alternativas como editor de memes brasileiro rigoroso.
Rejeite frases que descrevem o post, explicam a imagem, inventam elementos ausentes,
parecem slogan, usam portugues artificial ou nao possuem uma virada clara.
{"Se uma imagem estiver anexada a esta mensagem, baseie source_fit, laugh, surprise e visual_payoff no que você realmente ve na imagem, nao apenas na descricao textual abaixo." if encoded_critic_image else ""}

Pontue de 0 a 10:
- source_fit: usa apenas fatos concretos do post
- natural_ptbr: soa como fala brasileira
- surprise: muda o sentido do setup
- laugh: tem potencial real de humor
- visual_payoff: funciona com os elementos ja visiveis

Teste da punchline (obrigatorio antes de pontuar laugh e surprise): se a punchline apenas
descreve o que a imagem ja mostra, ou desfaz a expectativa do setup sem dar uma
reinterpretacao nova (papel, profissao ou intencao inesperada para o sujeito), entao
laugh e surprise valem no maximo 5. A parte engracada precisa estar na punchline,
nao no setup.

Teste de ancoragem visual (obrigatorio antes de pontuar visual_payoff): identifique a palavra
ou ideia central da virada (setup + escalada + punchline). Ela precisa apontar para algo
literalmente visivel na imagem/descricao (um objeto, uma pose, uma cor, uma expressao). Se a
virada usa um conceito abstrato ou uma referencia (um lugar, uma profissao, um tema) que nao
tem nenhum elemento correspondente na cena, ela nao esta ancorada — visual_payoff vale no
maximo 4, mesmo que a piada pareca engracada isolada do contexto. Uma virada ancorada pode
muito bem dar um papel/intencao inesperada ao sujeito (ver exemplo do Gerald), mas a pista
que sustenta esse papel tem que estar na cena, nao inventada do nada.

Use esta ancora de escala de forma literal em todos os criterios:
- 5: apenas funcional ou descritivo;
- 6: reconhecivel, mas fraco;
- 7: bom, porem previsivel ou pouco compartilhavel;
- 8: claramente forte, natural e compartilhavel; nao precisa ser excepcional;
- 9: excelente;
- 10: excepcional e raro.

Avalie TODAS as alternativas separadamente. Para cada candidate_id, approved somente se
source_fit, natural_ptbr, surprise e laugh forem pelo menos 8, e visual_payoff pelo menos 6.
Nao escolha vencedor e nao compare notas entre alternativas.

Post:
{json.dumps(source, ensure_ascii=False)}

Alternativas:
{json.dumps(candidates, ensure_ascii=False)}
""".strip()
            critic_models = [critic_model]
            if second_critic_model:
                if second_critic_model == critic_model:
                    raise ValueError("humor critics must use distinct models")
                critic_models.append(second_critic_model)
            critic_reviews: list[dict[str, Any]] = []
            passing_ids_by_critic: list[set[int]] = []
            score_names = ("source_fit", "natural_ptbr", "surprise", "laugh", "visual_payoff")
            for critic_index, active_critic_model in enumerate(critic_models, 1):
                critic_user_message: dict[str, Any] = {"role": "user", "content": critic_prompt}
                if encoded_critic_image and is_vision_capable_model(active_critic_model):
                    critic_user_message["images"] = [encoded_critic_image]
                critic_data = timed_humor_request(
                    f"critic_{critic_index}",
                    round_number,
                    {
                        "model": active_critic_model,
                        "stream": False,
                        "think": False,
                        "format": review_schema,
                        "messages": [
                            {"role": "system", "content": "Voce elimina memes fracos antes que gastem tempo de renderizacao."},
                            critic_user_message,
                        ],
                        "options": {"temperature": 0.1, "num_predict": 900},
                    },
                )
                critic_review = extract_json_object((critic_data.get("message") or {}).get("content") or "")
                if not critic_review:
                    raise ValueError(f"humor critic {active_critic_model} did not return a review")
                evaluations = critic_review.get("evaluations")
                if not isinstance(evaluations, list):
                    # Backward-compatible normalization for persisted/tests using the former winner-only contract.
                    evaluations = [{
                        "candidate_id": int(critic_review.get("winner_id") or 0),
                        "approved": bool(critic_review.get("approved")),
                        "scores": critic_review.get("scores") or {},
                        "reason": str(critic_review.get("reason") or ""),
                    }]
                normalized_evaluations: list[dict[str, Any]] = []
                passing_ids: set[int] = set()
                for evaluation in evaluations:
                    if not isinstance(evaluation, dict):
                        continue
                    candidate_id = int(evaluation.get("candidate_id") or 0)
                    scores = evaluation.get("scores") if isinstance(evaluation.get("scores"), dict) else {}
                    normalized_scores = {name: float(scores.get(name) or 0) for name in score_names}
                    passes = (
                        bool(evaluation.get("approved"))
                        and candidate_id > 0
                        and min(normalized_scores[name] for name in score_names[:4]) >= 8
                        and normalized_scores["visual_payoff"] >= 6
                    )
                    if passes:
                        passing_ids.add(candidate_id)
                    normalized_evaluations.append({
                        "candidate_id": candidate_id,
                        "approved": passes,
                        "scores": normalized_scores,
                        "reason": str(evaluation.get("reason") or ""),
                    })
                critic_reviews.append({"model": active_critic_model, "evaluations": normalized_evaluations})
                passing_ids_by_critic.append(passing_ids)

            consensus_ids = set.intersection(*passing_ids_by_critic) if passing_ids_by_critic else set()
            candidate_consensus_scores: dict[int, dict[str, float]] = {}
            for candidate_id in consensus_ids:
                candidate_consensus_scores[candidate_id] = {
                    name: min(
                        evaluation["scores"][name]
                        for critic_review in critic_reviews
                        for evaluation in critic_review["evaluations"]
                        if evaluation["candidate_id"] == candidate_id
                    )
                    for name in score_names
                }
            winner_id = max(
                consensus_ids,
                key=lambda candidate_id: (
                    candidate_consensus_scores[candidate_id]["laugh"],
                    candidate_consensus_scores[candidate_id]["surprise"],
                    candidate_consensus_scores[candidate_id]["source_fit"],
                    candidate_consensus_scores[candidate_id]["visual_payoff"],
                    -candidate_id,
                ),
                default=0,
            )
            consensus_scores = candidate_consensus_scores.get(winner_id, {name: 0.0 for name in score_names})
            consensus_approved = winner_id > 0
            review = {
                "approved": consensus_approved,
                "winner_id": winner_id,
                "scores": consensus_scores,
                "reason": "consenso aprovado" if consensus_approved else "criticos sem consenso ou abaixo dos minimos",
                "critics": critic_reviews,
            }
            winner = next((item for item in candidates if int(item.get("id") or 0) == winner_id), None)
            approved = consensus_approved and winner is not None
            deterministic_issues = (
                humor_candidate_issues(winner or {}, json.dumps(source, ensure_ascii=False))
                if winner
                else ["critico nao selecionou candidata"]
            )
            approved = approved and not deterministic_issues
            rounds.append(
                {
                    "round": round_number,
                    "candidates": candidates,
                    "review": review,
                    "deterministic_issues": deterministic_issues,
                    "approved": approved,
                }
            )
            if winner:
                concept["top_text"] = str(winner.get("setup") or "").strip()[:80]
                concept["middle_text"] = str(winner.get("escalation") or "").strip()[:120]
                concept["bottom_text"] = str(winner.get("punchline") or "").strip()[:120]
                concept["meme_logic"] = str(winner.get("comic_turn") or concept.get("meme_logic") or "")
                concept["scene_payoff"] = str(winner.get("scene_payoff") or "")
                concept["humor_candidates"] = candidates
                concept["humor_review"] = review
                concept["humor_rounds"] = rounds
            if approved and winner:
                concept["humor_approved"] = True
                return concept
            feedback_parts = [str(review.get("reason") or "falta uma virada curta, concreta e inesperada")]
            feedback_parts.extend(deterministic_issues)
            feedback = "; ".join(feedback_parts)

        concept["humor_candidates"] = rounds[-1]["candidates"]
        concept["humor_rounds"] = rounds
        concept["humor_review"] = deepcopy(rounds[-1]["review"])
        concept["humor_review"]["approved"] = False
        concept["humor_review"]["reason"] = (
            f"rejeitado apos {round_limit} rodada(s): "
            + str(rounds[-1]["review"].get("reason") or "nenhuma alternativa atingiu os minimos")
        )
        concept["humor_approved"] = False
        return concept
    except Exception as exc:  # noqa: BLE001
        concept["humor_candidates"] = []
        concept["humor_rounds"] = locals().get("rounds", [])
        concept["humor_approved"] = False
        concept["humor_review"] = {
            "approved": False,
            "error": True,
            "reason": f"humor gate failed: {exc}",
        }
        print(f"WARN humor gate failed for {post.id}: {exc}")
        return concept


def generate_concepts(
    posts: list[reddit.RedditPost],
    model: str,
    timeout: int,
    visual_descriptions: dict[str, str] | None = None,
    humor_model: str = "gemma3:12b",
    humor_critic_model: str | None = None,
    humor_second_critic_model: str | None = None,
    seed_candidates_by_post: dict[str, list[dict[str, Any]]] | None = None,
    source_reviews: dict[str, dict[str, Any]] | None = None,
    image_paths: dict[str, str] | None = None,
    checkpoint: Callable[[list[dict[str, Any]]], None] | None = None,
) -> list[dict[str, str]]:
    visual_descriptions = visual_descriptions or {}
    seed_candidates_by_post = seed_candidates_by_post or {}
    source_reviews = source_reviews or {}
    image_paths = image_paths or {}
    compact_posts = [
        {
            "index": idx,
            "subreddit": post.subreddit,
            "title": post.title,
            "summary": post.summary[:400],
            "media_type": post.media_type,
            "media_url": post.media_url,
            "source_visual_description": visual_descriptions.get(post.id, ""),
        }
        for idx, post in enumerate(posts, 1)
    ]
    prompt = f"""
Voce cria conceitos de memes brasileiros leves para revisao humana.
Escolha UM arquetipo viral da lista abaixo antes de criar o texto e a cena.
Nao copie personagens, fotos, logos ou frases de memes famosos; copie a mecanica.

Arquetipos permitidos:
{archetype_catalog()}

Regras:
- Responda SOMENTE um array JSON valido.
- Gere exatamente {len(posts)} objetos.
- Nao copie texto integral dos posts.
- Nao ataque pessoas privadas.
- Nao use corpo, peso, deficiencia, raça, genero ou aparencia de pessoa comum como punchline.
- top_text e bottom_text devem ser curtos, em portugues, estilo meme.
- top_text deve funcionar como setup; bottom_text deve funcionar como punchline.
- A imagem deve encenar a punchline, nao apenas ilustrar a noticia.
- O meme_archetype deve ser um id da lista de arquetipos permitidos.
- image_prompt deve estar em ingles e pedir imagem SEM texto.
- Use title, summary, media_type, media_url e source_visual_description como fonte conceitual.
- Se source_visual_description existir, use detalhes concretos da foto original para criar cena, props, expressao e timing.
- Se media_type=image, descreva uma cena que aproveite a ideia visual da imagem original.
- Se media_type=video, descreva um frame congelado engraçado inspirado na situacao.
- Se media_type=text, transforme o assunto em uma metafora visual clara.
- image_prompt deve ser detalhado: um unico sujeito principal, cenario simples, expressao, acao, estilo, composicao, cores e restricoes.
- Evite multidoes, colagens, muitos rostos pequenos, texto dentro da imagem e cenas poluidas.
- Reserve espaco limpo visual no topo e embaixo para overlay de texto.

Formato:
[
  {{
    "index": 1,
    "top_text": "...",
    "bottom_text": "...",
    "image_prompt": "...",
    "meme_archetype": "this_is_fine|drake_yes_no|galaxy_brain|boss_fight|pov_spiral|starter_pack|expectation_reality|npc_side_quest|chill_guy_energy|brainrot_absurd",
    "meme_logic": "por que esse arquetipo combina com o post",
    "rationale": "..."
  }}
]

Posts:
{json.dumps(compact_posts, ensure_ascii=False)}
""".strip()
    concepts: list[dict[str, Any]] = []
    if not seed_candidates_by_post:
        try:
            payload = {
                "model": model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": "Voce e um redator de memes BR leve e seguro."},
                    {"role": "user", "content": prompt},
                ],
                "options": {"temperature": 0.7},
            }
            data = request_json("POST", f"{OLLAMA_URL}/api/chat", json=payload, timeout=timeout)
            content = (data.get("message") or {}).get("content") or ""
            concepts = extract_json_array(content)
            if not concepts:
                raise ValueError("Ollama did not return a JSON array")
        except Exception as exc:  # noqa: BLE001 - rejected fallback remains useful for diagnosis
            print(f"WARN batch concept generation failed; continuing with per-post humor gate: {exc}")
            concepts = []

    by_index = {int(item.get("index", 0)): item for item in concepts if isinstance(item, dict)}
    normalized = []
    for idx, post in enumerate(posts, 1):
        item = by_index.get(idx) or {}
        visual_description = visual_descriptions.get(post.id, "")
        fallback = fallback_concept(post, visual_description)
        generated_scene = str(item.get("image_prompt") or "").strip()
        media_context = {
            "image": "Use the source post only for the idea, not for literal visual details. ",
            "video": "Use the source video only for the situation, as one clean frozen frame. ",
            "text": "",
        }.get(post.media_type, "")
        image_prompt = (
            compose_image_prompt(post.title, generated_scene, media_context, fallback["source_brief"])
            if generated_scene
            else fallback["image_prompt"]
        )
        concept = {
            "top_text": str(item.get("top_text") or fallback["top_text"])[:80],
            "bottom_text": str(item.get("bottom_text") or fallback["bottom_text"])[:120],
            "image_prompt": image_prompt,
            "source_brief": fallback["source_brief"],
            "meme_archetype": str(item.get("meme_archetype") or fallback.get("meme_logic") or ""),
            "meme_logic": str(item.get("meme_logic") or fallback.get("meme_format") or ""),
            "rationale": str(item.get("rationale") or ""),
            "source_visual_description": visual_description,
            "source_review": deepcopy(source_reviews.get(post.id) or {}),
        }
        if isinstance(item.get("video_script"), dict):
            concept["video_script"] = item["video_script"]
            concept["video_script"].setdefault("source_visual_description", visual_description)
        else:
            concept["video_script"] = build_video_script(post, concept, visual_description)
        source_review = source_reviews.get(post.id)
        if source_review and not source_review.get("approved"):
            reason = str(source_review.get("reason") or "source suitability rejected")
            concept["humor_approved"] = False
            concept["humor_review"] = {"approved": False, "reason": reason, "source_rejected": True}
            concept["quality_review"] = evaluate_concept_quality(post, concept)
            concept["quality_approved"] = False
            set_stage_state(concept, "source_gate", "rejected", reason)
            normalized.append(concept)
            if checkpoint:
                checkpoint(normalized)
            continue
        image_path_str = image_paths.get(post.id)
        concept = improve_humor_concept(
            post,
            concept,
            humor_model,
            timeout,
            visual_description,
            critic_model=humor_critic_model,
            second_critic_model=humor_second_critic_model,
            seed_candidates=seed_candidates_by_post.get(post.id),
            image_path=Path(image_path_str) if image_path_str else None,
        )
        if concept.get("humor_approved"):
            concept["video_script"] = build_video_script(post, concept, visual_description)
        concept["image_prompt"] = compose_scripted_image_prompt(concept["video_script"], fallback["source_brief"])
        concept["quality_review"] = evaluate_concept_quality(post, concept)
        concept["quality_approved"] = bool((concept.get("quality_review") or {}).get("approved"))
        normalized.append(concept)
        if checkpoint:
            checkpoint(normalized)
    return normalized


def flush_ollama(model: str) -> None:
    try:
        request_json(
            "POST",
            f"{OLLAMA_URL}/api/generate",
            json={"model": model, "prompt": "", "stream": False, "keep_alive": 0},
            timeout=60,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"WARN could not flush Ollama model {model}: {exc}")


def free_comfy_memory() -> None:
    try:
        requests.post(
            f"{COMFYUI_URL}/free",
            json={"unload_models": True, "free_memory": True},
            timeout=60,
        ).raise_for_status()
    except Exception as exc:  # noqa: BLE001
        print(f"WARN could not release ComfyUI models before Ollama work: {exc}")


def queue_comfy_image(concept: dict[str, str], prefix: str, seed: int, args: argparse.Namespace) -> str:
    payload = {
        "mode": "txt2img",
        "prompt": concept["image_prompt"],
        "negative": (
            "text, letters, words, caption, watermark, logo, signature, UI, speech bubble, "
            "numbers, fake alphabet, fake typography, readable signs, storefront signs, posters, "
            "charts with text, spreadsheet, branded uniforms, brand names, "
            "low quality, blurry, distorted, deformed face, bad anatomy, extra fingers, missing fingers, "
            "crossed eyes, duplicated people, cloned faces, crowd of tiny malformed faces, cluttered collage, "
            "oversaturated chaos, cropped head, out of frame, unreadable subject"
        ),
        "ckpt_name": args.ckpt_name,
        "width": args.width,
        "height": args.height,
        "steps": args.steps,
        "cfg": args.cfg,
        "sampler_name": args.sampler_name,
        "scheduler": args.scheduler,
        "seed": seed,
        "filename_prefix": prefix,
    }
    data = request_json("POST", N8N_GENERATE_URL, json=payload, timeout=90)
    prompt_id = data.get("prompt_id")
    if not prompt_id:
        raise RuntimeError(f"n8n did not return prompt_id: {data}")
    return str(prompt_id)


def wait_for_comfy_output(prompt_id: str, timeout_seconds: int, poll_seconds: float) -> dict[str, str]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        history = request_json("POST", N8N_STATUS_URL, json={"prompt_id": prompt_id}, timeout=60)
        run = history.get(prompt_id) if isinstance(history, dict) else None
        if run:
            for output in (run.get("outputs") or {}).values():
                for image in output.get("images") or []:
                    return {
                        "filename": image["filename"],
                        "subfolder": image.get("subfolder", ""),
                        "type": image.get("type", "output"),
                    }
        time.sleep(poll_seconds)
    raise TimeoutError(f"ComfyUI output not ready for prompt_id={prompt_id}")


def download_comfy_image(ref: dict[str, str], output_path: Path) -> None:
    download_comfy_file(ref, output_path)


def upload_comfy_image(image_path: Path) -> str:
    with image_path.open("rb") as file_obj:
        response = requests.post(
            f"{COMFYUI_URL}/upload/image",
            files={"image": (image_path.name, file_obj, "image/png")},
            data={"overwrite": "true"},
            timeout=120,
        )
    response.raise_for_status()
    data = response.json()
    return str(data["name"])


def compose_video_prompt(post: reddit.RedditPost, concept: dict[str, str]) -> str:
    script = concept.get("video_script")
    if not isinstance(script, dict):
        script = build_video_script(post, concept)
        concept["video_script"] = script
    timeline = script.get("timeline") if isinstance(script.get("timeline"), list) else []
    timeline_text = " ".join(str(item) for item in timeline)
    return (
        "Generate exactly one continuous 10-second text-to-video shot. "
        "Create the first frame directly from the scene description, then keep the same character identity, face, hands, "
        "clothing, location layout, colors, and lighting for the full shot. "
        "Use minimal controlled motion only. Do not invent a different story or cut to another scene. Follow this shot plan precisely. "
        f"Scene and setting: {script.get('scene')}. "
        f"Main character: {script.get('character')}. "
        f"Main prop: {script.get('main_prop')}. "
        f"Camera direction: {script.get('camera')}. "
        f"Timeline and acting beats: {timeline_text} "
        f"Comedy beat: {script.get('comedy_beat')}. "
        f"Dialogue/audio intention, not visible text: {script.get('dialogue')}. {script.get('audio')} "
        "Motion limits: subtle eye movement, eyebrows, small head turn, small hand movement, slight shoulder tension, "
        "very slow camera push-in. Keep the background stable. "
        "Never transition to a different room, object set, costume, person, or location. "
        "Visual style: realistic social-media comedy, stable identity, coherent hands, coherent eyes, natural motion, "
        "controlled lighting, sharp subject, readable facial acting, no morphing. "
        f"Strict visual rules: {script.get('visual_rules')} "
        "The video itself must contain no generated words; all captions and audio are added later."
    )


def compose_ltx23_av_prompt(post: reddit.RedditPost, concept: dict[str, str]) -> str:
    return compose_ltx23_segment_prompts(post, concept)[0]


def should_rebuild_ltx23_video_script(script: Any) -> bool:
    if not isinstance(script, dict):
        return True
    timeline = script.get("timeline")
    if not isinstance(timeline, list) or len(timeline) < 3:
        return True
    prompt_text = " ".join(str(item) for item in timeline[:3]).lower()
    if not re.search(
        r"\b(turns?|looks?|points?|holds?|walks?|enters?|stops?|moves?|smiles?|raises?|lowers?|studies?|shifts?|pauses?|nods?|sips?|leans?|pushes?|freezes?|regrets?|accepts?|completes?|waits?|poses?|stares?|notices?|glances?|blinks?|interacts?|sits?|unlocks?|expects?|gestures?)\b",
        prompt_text,
    ):
        return True
    for field in ("scene", "character", "main_prop", "camera", "audio", "visual_rules"):
        value = str(script.get(field) or "").strip()
        if not value:
            return True
    return False


def ltx_negative_prompt() -> str:
    return (
        "slideshow, presentation, static cards, cuts, panels, split screen, captions, subtitles, text, "
        "letters, logos, watermark, UI, blurry, distorted face, extra limbs, malformed hands, flicker, "
        "warped background, low quality, colorful smoke, abstract clouds, melting shapes, morphing background, "
        "liquid artifacts, dreamlike fog, unstable identity, changing room, changing clothes, changing face, "
        "new characters, scene transformation, unrelated apartment, unrelated phone, unrelated desk, teleporting props"
    )


def ltx23_negative_prompt() -> str:
    return (
        "pc game, console game, video game, cartoon, childish, ugly, subtitles, captions, text-like marks, "
        "letters, glyphs, typography, readable text, logos, brands, watermark, product packaging, signs, posters, "
        "labels, distorted face, malformed hands, flicker, morphing, abstract clouds, colorful smoke, "
        "scene change, slideshow, panels, split screen, robotic voice, English accent, noisy audio, "
        "distorted speech, fast motion, camera shake, motion blur, soft focus"
    )


def normalize_ltx_action(value: str) -> str:
    value = re.sub(r"^\s*\d+(?:\.\d+)?\s*-\s*\d+(?:\.\d+)?s?\s*:\s*", "", str(value), flags=re.IGNORECASE)
    value = re.sub(
        r"\b(setup|complication|punchline|comedy beat|comic turn|visual contradiction)\b",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\s+", " ", value).strip(" .,:;-")
    return value[:1].upper() + value[1:] if value else value


def validate_ltx23_prompts(prompts: list[str]) -> list[str]:
    errors: list[str] = []
    forbidden_patterns = {
        "semantic shot label": r"\b(setup|complication|punchline)\s+shot\b",
        "global timeline offset": r"\b(?:5|10)\s*-\s*(?:10|15)s?\b",
        "delegated humor reasoning": r"\b(make|show|explain|deliver)\b.{0,40}\b(contradiction|punchline|joke|funny)\b",
    }
    required_patterns = {
        "observable action": r"\b(turns?|looks?|points?|holds?|walks?|enters?|stops?|moves?|smiles?|raises?|lowers?|studies?|shifts?|pauses?|nods?|sips?|leans?|pushes?|freezes?|regrets?|accepts?|completes?|waits?|poses?|stares?|notices?|glances?|blinks?|interacts?|sits?|unlocks?|expects?|gestures?)\b",
        "camera description": r"\bcamera\b",
        "lighting description": r"\blight(?:ing)?\b",
        "sound description": r"\b(ambience|sound|speech|voice-over|narration|room tone)\b",
    }
    for index, prompt in enumerate(prompts, 1):
        if len(prompt.split()) > 200:
            errors.append(f"segment {index}: prompt exceeds 200 words")
        for label, pattern in forbidden_patterns.items():
            if re.search(pattern, prompt, flags=re.IGNORECASE):
                errors.append(f"segment {index}: contains {label}")
        for label, pattern in required_patterns.items():
            if not re.search(pattern, prompt, flags=re.IGNORECASE):
                errors.append(f"segment {index}: missing {label}")
    return errors


def compose_ltx23_segment_prompts(post: reddit.RedditPost, concept: dict[str, Any], segments: int = 1) -> list[str]:
    if segments not in (1, 2):
        raise ValueError("ltx23 supports 1 or 2 segments per concept")
    script = concept.get("video_script")
    if should_rebuild_ltx23_video_script(script):
        script = build_video_script(post, concept)
        concept["video_script"] = script
    timeline = script.get("timeline") if isinstance(script.get("timeline"), list) else []
    while len(timeline) < 3:
        timeline.append("The character holds a clear readable reaction while the location and objects remain unchanged.")

    def limit_words(value: Any, count: int) -> str:
        chunks = [
            chunk.strip()
            for chunk in re.split(r"[.;]|,\s+(?=(?:and|then|with|beside|near|while|plus|the|a|one)\b)", str(value or ""), flags=re.IGNORECASE)
            if chunk.strip()
        ]
        selected: list[str] = []
        used = 0
        for chunk in chunks:
            size = len(chunk.split())
            if selected and used + size > count:
                break
            if not selected and size > count:
                words = chunk.split()[:count]
                while words and words[-1].lower() in {"a", "an", "the", "and", "or", "with", "from", "to", "in"}:
                    words.pop()
                return " ".join(words)
            selected.append(chunk)
            used += size
        return ", ".join(selected).strip().rstrip(".")

    actions = [limit_words(normalize_ltx_action(item), 30) for item in timeline[:3]]
    scene = limit_words(script.get("scene"), 28)
    character = limit_words(script.get("character"), 24)
    prop = limit_words(script.get("main_prop"), 20)
    camera = limit_words(script.get("camera"), 14)
    # Distilled CFG 1.0 regime: the negative prompt is inert, so the positive
    # prompt must stay purely descriptive. Naming forbidden artifacts
    # ("no captions, posters, subtitles") or shouting the joke in caps makes
    # the model render them; spoken lines go in as quoted lowercase voice-over.
    dialogue = str(concept.get("video_script", {}).get("dialogue") or "").strip()

    def audio_sentence(text: str) -> str:
        if text:
            return (
                "Audio: it stays silent for a brief beat first, then a calm adult Brazilian "
                "Portuguese voice-over says unhurried, with a clear pause between each sentence: "
                f'"{text.lower().rstrip(".")}". '
                "A brief silent beat after the voice-over finishes speaking. Quiet indoor room tone."
            )
        return "Audio: natural Brazilian Portuguese narration with dry comic timing. Quiet indoor room tone."

    def segment_prompt(action_text: str, dialogue_text: str) -> str:
        return (
            f"{action_text}. {character}. {prop}. {scene}. "
            f"Camera: {camera}. Keep natural light, sharp focus, and minimal motion. "
            "One continuous shot. "
            f"{audio_sentence(dialogue_text)}"
        )

    if segments == 1:
        prompts = (segment_prompt(actions[0], dialogue),)
    else:
        # Segment 2 anchors on the last frame of segment 1 and carries the
        # punchline: the final sentence of the dialogue stays in segment 2,
        # everything before it in segment 1.
        sentences = [chunk.strip() for chunk in re.split(r"(?<=[.!?])\s+", dialogue) if chunk.strip()]
        if len(sentences) >= 2:
            dialogue_head, dialogue_tail = " ".join(sentences[:-1]), sentences[-1]
        else:
            dialogue_head, dialogue_tail = "", dialogue
        prompts = (
            segment_prompt(f"{actions[0]}, then {actions[1]}", dialogue_head),
            segment_prompt(actions[2], dialogue_tail),
        )
    concept["ltx23_prompt_contract"] = {
        "style": "literal-chronological-cinematography",
        "semantic_labels_sent_to_model": False,
        "global_timeline_offsets_sent_to_model": False,
        "mode": "native-audio-video-prompt",
        "workflows": [
            str(LTX23_API_WORKFLOW.relative_to(PROJECT_ROOT)),
            str(LTX23_I2V_API_WORKFLOW.relative_to(PROJECT_ROOT)),
        ],
    }
    compiled = [re.sub(r"\s+", " ", prompt).strip() for prompt in prompts]
    errors = validate_ltx23_prompts(compiled)
    concept["ltx23_prompt_validation"] = {"approved": not errors, "errors": errors}
    if errors:
        raise ValueError("invalid LTX prompt contract: " + "; ".join(errors))
    return compiled


def queue_comfy_ltx23_native_video(
    concept: dict[str, Any],
    post: reddit.RedditPost,
    prefix: str,
    seed: int,
    args: argparse.Namespace,
    *,
    video_prompt_override: str | None = None,
    frames_override: int | None = None,
    reference_image_path: Path | None = None,
) -> str:
    """Queue the repository's validated native audio/video workflow.

    The checked-in workflow is the source of truth. Python may parameterize
    declared inputs but must not maintain a second hand-built graph.
    """

    workflow_path = LTX23_I2V_API_WORKFLOW if reference_image_path is not None else LTX23_API_WORKFLOW
    uploaded_reference: str | None = None
    if reference_image_path is not None:
        uploaded_reference = upload_comfy_image(reference_image_path)
    video_prompt = video_prompt_override or compose_ltx23_av_prompt(post, concept)
    negative_prompt = ltx23_negative_prompt()
    frames = frames_override or args.ltx23_frames
    concept["ltx_prompt"] = video_prompt
    concept["ltx_negative_prompt"] = negative_prompt
    concept["ltx_video_input_mode"] = (
        "ltx23-native-i2v-audio-video" if reference_image_path is not None else "ltx23-native-audio-video"
    )
    document = json.loads(workflow_path.read_text(encoding="utf-8"))
    prompt = deepcopy(document.get("prompt"))
    if not isinstance(prompt, dict):
        raise ValueError(f"invalid ComfyUI API workflow: {workflow_path}")
    expected_nodes = {str(index) for index in range(1, 37)} if reference_image_path else {str(index) for index in range(1, 23)}
    if set(prompt) != expected_nodes:
        raise ValueError("validated LTX 2.3 workflow node set changed unexpectedly")

    if uploaded_reference is not None:
        # Official-template I2V graph (workflows/05). Distilled regime: CFG,
        # sigmas and both inplace strengths are part of the graph contract and
        # are intentionally not parameterized.
        prompt["1"]["inputs"]["ckpt_name"] = args.ltx23_ckpt_name
        prompt["2"]["inputs"].update({"lora_name": args.ltx23_lora_name, "strength_model": args.ltx23_lora_strength})
        prompt["3"]["inputs"].update(
            {"text_encoder": args.ltx23_text_encoder, "ckpt_name": args.ltx23_ckpt_name, "device": args.ltx23_text_encoder_device}
        )
        prompt["4"]["inputs"]["ckpt_name"] = args.ltx23_ckpt_name
        prompt["5"]["inputs"]["text"] = video_prompt
        prompt["6"]["inputs"]["text"] = negative_prompt
        prompt["7"]["inputs"]["frame_rate"] = float(args.ltx23_fps)
        prompt["8"]["inputs"]["image"] = uploaded_reference
        prompt["9"]["inputs"].update({"width": args.ltx23_width, "height": args.ltx23_height})
        prompt["11"]["inputs"]["img_compression"] = args.ltx23_reference_compression
        prompt["12"]["inputs"].update(
            {"width": args.ltx23_width // 2, "height": args.ltx23_height // 2, "length": frames, "batch_size": 1}
        )
        prompt["14"]["inputs"].update({"frames_number": frames, "frame_rate": int(args.ltx23_fps), "batch_size": 1})
        prompt["16"]["inputs"]["noise_seed"] = seed
        prompt["22"]["inputs"]["model_name"] = args.ltx23_upscaler_name
        prompt["35"]["inputs"]["fps"] = float(args.ltx23_fps)
        prompt["36"]["inputs"].update({"filename_prefix": prefix, "format": "mp4", "codec": "h264"})
        concept["ltx23_reference_image_path"] = str(reference_image_path)
        concept["ltx23_uploaded_reference"] = uploaded_reference
    else:
        prompt["1"]["inputs"]["ckpt_name"] = args.ltx23_ckpt_name
        prompt["2"]["inputs"].update({"lora_name": args.ltx23_lora_name, "strength_model": args.ltx23_lora_strength})
        prompt["3"]["inputs"].update(
            {"text_encoder": args.ltx23_text_encoder, "ckpt_name": args.ltx23_ckpt_name, "device": args.ltx23_text_encoder_device}
        )
        prompt["4"]["inputs"]["text"] = video_prompt
        prompt["5"]["inputs"]["text"] = negative_prompt
        prompt["6"]["inputs"]["frame_rate"] = args.ltx23_fps
        prompt["7"]["inputs"].update(
            {"width": args.ltx23_width, "height": args.ltx23_height, "length": frames, "batch_size": 1}
        )
        prompt["8"]["inputs"]["ckpt_name"] = args.ltx23_ckpt_name
        prompt["9"]["inputs"].update({"frames_number": frames, "frame_rate": int(args.ltx23_fps), "batch_size": 1})
        prompt["11"]["inputs"]["cfg"] = args.ltx23_audio_cfg
        prompt["12"]["inputs"]["cfg"] = args.ltx23_video_cfg
        prompt["14"]["inputs"]["sampler_name"] = args.ltx23_sampler_name
        prompt["15"]["inputs"]["steps"] = args.ltx23_steps
        prompt["16"]["inputs"]["noise_seed"] = seed
        prompt["19"]["inputs"].update(
            {"horizontal_tiles": args.ltx23_decode_tiles, "vertical_tiles": args.ltx23_decode_tiles}
        )
        prompt["21"]["inputs"]["fps"] = args.ltx23_fps
        prompt["22"]["inputs"].update({"filename_prefix": prefix, "format": "mp4", "codec": "h264"})
    concept["ltx23_api_prompt"] = prompt
    concept["ltx23_workflow"] = str(workflow_path.relative_to(PROJECT_ROOT))
    data = request_json("POST", f"{COMFYUI_URL}/prompt", json={"prompt": prompt}, timeout=90)
    prompt_id = data.get("prompt_id")
    if not prompt_id:
        raise RuntimeError(f"ComfyUI did not return prompt_id: {data}")
    return str(prompt_id)


def ltx_base_prompt_graph(
    video_prompt: str,
    prefix: str,
    seed: int,
    args: argparse.Namespace,
) -> dict[str, Any]:
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": args.ltx_ckpt_name}},
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": args.ltx_text_encoder, "type": "ltxv", "device": args.ltx_text_encoder_device},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["2", 0], "text": video_prompt},
        },
        "4": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["2", 0], "text": ltx_negative_prompt()},
        },
        "5": {
            "class_type": "LTXVConditioning",
            "inputs": {"positive": ["3", 0], "negative": ["4", 0], "frame_rate": args.ltx_fps},
        },
        "8": {
            "class_type": "ModelSamplingLTXV",
            "inputs": {"model": ["1", 0], "max_shift": 2.05, "base_shift": 0.95},
        },
        "9": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["8", 0],
                "seed": seed,
                "steps": args.ltx_steps,
                "cfg": args.ltx_cfg,
                "sampler_name": args.ltx_sampler_name,
                "scheduler": args.ltx_scheduler,
                "positive": ["5", 0],
                "negative": ["5", 1],
                "denoise": 1.0,
            },
        },
        "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["1", 2]}},
        "11": {"class_type": "CreateVideo", "inputs": {"images": ["10", 0], "fps": args.ltx_fps}},
        "12": {
            "class_type": "SaveVideo",
            "inputs": {"video": ["11", 0], "filename_prefix": prefix, "format": "mp4", "codec": "h264"},
        },
    }


def queue_comfy_ltx_prompt_video(
    concept: dict[str, str],
    post: reddit.RedditPost,
    prefix: str,
    seed: int,
    args: argparse.Namespace,
) -> str:
    video_prompt = compose_video_prompt(post, concept)
    concept["ltx_prompt"] = video_prompt
    concept["ltx_negative_prompt"] = ltx_negative_prompt()
    concept["ltx_video_input_mode"] = "prompt"
    prompt = ltx_base_prompt_graph(video_prompt, prefix, seed, args)
    prompt.update(
        {
            "6": {
                "class_type": "EmptyLTXVLatentVideo",
                "inputs": {
                    "width": args.ltx_width,
                    "height": args.ltx_height,
                    "length": args.ltx_frames,
                    "batch_size": 1,
                },
            }
        }
    )
    prompt["8"]["inputs"]["latent"] = ["6", 0]
    prompt["9"]["inputs"]["latent_image"] = ["6", 0]
    data = request_json("POST", f"{COMFYUI_URL}/prompt", json={"prompt": prompt}, timeout=90)
    prompt_id = data.get("prompt_id")
    if not prompt_id:
        raise RuntimeError(f"ComfyUI did not return prompt_id: {data}")
    return str(prompt_id)


def queue_comfy_ltx_image_video(
    image_path: Path,
    concept: dict[str, str],
    post: reddit.RedditPost,
    prefix: str,
    seed: int,
    args: argparse.Namespace,
) -> str:
    uploaded_name = upload_comfy_image(image_path)
    video_prompt = compose_video_prompt(post, concept)
    concept["ltx_prompt"] = video_prompt
    concept["ltx_negative_prompt"] = ltx_negative_prompt()
    concept["ltx_video_input_mode"] = "image"
    prompt = ltx_base_prompt_graph(video_prompt, prefix, seed, args)
    prompt.update(
        {
        "6": {"class_type": "LoadImage", "inputs": {"image": uploaded_name}},
        "7": {
            "class_type": "LTXVImgToVideo",
            "inputs": {
                "positive": ["5", 0],
                "negative": ["5", 1],
                "vae": ["1", 2],
                "image": ["6", 0],
                "width": args.ltx_width,
                "height": args.ltx_height,
                "length": args.ltx_frames,
                "batch_size": 1,
                "strength": args.ltx_strength,
            },
        }}
    )
    prompt["8"]["inputs"]["latent"] = ["7", 2]
    prompt["9"]["inputs"]["positive"] = ["7", 0]
    prompt["9"]["inputs"]["negative"] = ["7", 1]
    prompt["9"]["inputs"]["latent_image"] = ["7", 2]
    data = request_json("POST", f"{COMFYUI_URL}/prompt", json={"prompt": prompt}, timeout=90)
    prompt_id = data.get("prompt_id")
    if not prompt_id:
        raise RuntimeError(f"ComfyUI did not return prompt_id: {data}")
    return str(prompt_id)


def wait_for_comfy_video(prompt_id: str, timeout_seconds: int, poll_seconds: float) -> dict[str, str]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        history = request_json("GET", f"{COMFYUI_URL}/history/{prompt_id}", timeout=60)
        run = history.get(prompt_id) if isinstance(history, dict) else None
        if run:
            status = run.get("status") or {}
            if status.get("status_str") == "error":
                messages = status.get("messages") or []
                raise RuntimeError(f"ComfyUI video render failed: {messages[-1] if messages else status}")
            for output in (run.get("outputs") or {}).values():
                for video in output.get("images") or []:
                    if str(video.get("filename", "")).lower().endswith(".mp4"):
                        return {
                            "filename": video["filename"],
                            "subfolder": video.get("subfolder", ""),
                            "type": video.get("type", "output"),
                        }
        time.sleep(poll_seconds)
    raise TimeoutError(f"ComfyUI video output not ready for prompt_id={prompt_id}")


def download_source_media(post: reddit.RedditPost, output_path: Path) -> str:
    if post.media_type not in {"image", "video"} or not post.media_url:
        return ""
    try:
        response = requests.get(post.media_url, timeout=60, headers={"User-Agent": "media-meme-pipeline/0.1"})
        response.raise_for_status()
        suffix = ".mp4" if post.media_type == "video" else ".jpg"
        target = output_path.with_suffix(suffix)
        target.write_bytes(response.content)
        return str(target)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN could not download source media for {post.id}: {exc}")
        return ""


def sanitize_visual_description(value: str) -> str:
    value = " ".join((value or "").split())
    replacements = {
        r"\barma de caça\b": "objeto manual sem detalhes",
        r"\barma\b": "objeto manual sem detalhes",
        r"\brifle\b": "objeto manual sem detalhes",
        r"\brev[oó]lver\b": "objeto manual sem detalhes",
        r"\bpistola\b": "objeto manual sem detalhes",
        r"\bfaca\b": "ferramenta manual sem detalhes",
        r"\bknife\b": "handheld tool without details",
        r"\bgun\b": "handheld prop without details",
        r"\bweapon\b": "handheld prop without details",
    }
    for pattern, replacement in replacements.items():
        value = re.sub(pattern, replacement, value, flags=re.IGNORECASE)
    return value[:1200]


def describe_source_image(image_path: Path, model: str, timeout: int) -> str:
    if not image_path.exists():
        return ""
    try:
        vision_path = image_path.with_suffix(".vision.jpg")
        try:
            image = Image.open(image_path).convert("RGB")
            image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
            image.save(vision_path, quality=92)
            image_bytes = vision_path.read_bytes()
        except Exception:
            image_bytes = image_path.read_bytes()
        encoded = base64.b64encode(image_bytes).decode("ascii")
        prompt = (
            "Describe this source image for a meme/video generation pipeline. "
            "Be concrete and visual. Include: setting, main subjects, facial expressions, body posture, "
            "objects/props, colors, lighting, composition, what seems funny or visually notable. "
            "Do not identify real people. Do not transcribe labels, signs, captions, usernames, or brand names verbatim. "
            "If the image is primarily a note, document, screenshot, sign, or other readable-text object, summarize its message "
            "without quoting it and explicitly state whether any person, body part, or depicted action is actually visible. "
            "Never infer a person, body part, stain, action, or setting that is mentioned by text but not visible in the image. "
            "For incidental readable text in an otherwise visual scene, describe it generically as a blank label, screen, sign, or printed object. "
            "If a dangerous object appears, describe it only as a generic handheld prop without operational detail. "
            "Respond in concise Portuguese, 5 to 8 bullet-like clauses, no markdown."
        )
        data = request_json(
            "POST",
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": model,
                "stream": False,
                "messages": [{"role": "user", "content": prompt, "images": [encoded]}],
                "options": {"temperature": 0.2},
            },
            timeout=timeout,
        )
        return sanitize_visual_description((data.get("message") or {}).get("content") or "")
    except Exception as exc:  # noqa: BLE001
        print(f"WARN could not describe source image {image_path.name}: {exc}")
        return ""
    finally:
        vision_path = image_path.with_suffix(".vision.jpg")
        if vision_path.exists():
            vision_path.unlink()


def finalize_source_suitability_review(review: dict[str, Any]) -> dict[str, Any]:
    scores = review.get("scores") if isinstance(review.get("scores"), dict) else {}
    normalized_scores = {
        name: max(0.0, min(5.0, float(scores.get(name) or 0)))
        for name in ("source_match", "visual_clarity", "motion_potential", "text_independence")
    }
    reason = str(review.get("reason") or "source suitability review did not justify approval")
    # Deterministic caps: prompt-level "score at most N" instructions proved unreliable
    # (the Neymar/Haaland caption collage still scored text_independence=3), so the model
    # only answers the binary questions and the caps are enforced here.
    if bool(review.get("embedded_text_carries_meaning")):
        normalized_scores["text_independence"] = min(normalized_scores["text_independence"], 2.0)
        reason = "texto embutido carrega o significado do post; " + reason
    if bool(review.get("multi_photo_collage")):
        normalized_scores["text_independence"] = min(normalized_scores["text_independence"], 2.0)
        normalized_scores["visual_clarity"] = min(normalized_scores["visual_clarity"], 3.0)
        reason = "colagem de fotos distintas nao serve para I2V de cena unica; " + reason
    approved = (
        bool(review.get("approved"))
        and normalized_scores["source_match"] >= 4
        and normalized_scores["visual_clarity"] >= 3
        and normalized_scores["motion_potential"] >= 3
        and normalized_scores["text_independence"] >= 3
    )
    return {
        "approved": approved,
        "scores": normalized_scores,
        "reason": reason,
    }


def assess_source_suitability(
    post: reddit.RedditPost,
    image_path: Path,
    visual_description: str,
    model: str,
    timeout: int,
) -> dict[str, Any]:
    schema = {
        "type": "object",
        "properties": {
            "approved": {"type": "boolean"},
            "embedded_text_carries_meaning": {"type": "boolean"},
            "multi_photo_collage": {"type": "boolean"},
            "scores": {
                "type": "object",
                "properties": {
                    "source_match": {"type": "number"},
                    "visual_clarity": {"type": "number"},
                    "motion_potential": {"type": "number"},
                    "text_independence": {"type": "number"},
                },
                "required": ["source_match", "visual_clarity", "motion_potential", "text_independence"],
            },
            "reason": {"type": "string"},
        },
        "required": ["approved", "embedded_text_carries_meaning", "multi_photo_collage", "scores", "reason"],
    }
    try:
        with Image.open(image_path) as image:
            width, height = image.size
        prompt = f"""
Avalie se a descricao de uma imagem serve como fonte para um video-meme I2V fiel ao post.
Titulo: {post.title}
Resumo: {post.summary[:500]}
Descricao visual previa: {visual_description}
Dimensoes da imagem: {width}x{height}

Pontue de 0 a 5:
- source_match: a imagem mostra os elementos ou a acao centrais descritos pelo post;
- visual_clarity: sujeito e objetos principais sao claros e utilizaveis;
- motion_potential: existe acao simples e visual que pode ser animada sem inventar outra cena;
- text_independence: a premissa pode ser entendida visualmente sem depender de OCR.

Rejeite miniatura que nao mostre a acao do titulo, documento/screenshot dependente de leitura,
imagem ambigua ou fonte que exigiria inventar personagem, objeto ou local.
Responda tambem dois campos booleanos obrigatorios, de forma literal:
- embedded_text_carries_meaning: true se a imagem contem legenda, manchete, tweet ou texto
  embutido que carrega o significado do post (a piada so faz sentido lendo esse texto).
- multi_photo_collage: true se a imagem e uma colagem/lado-a-lado de duas ou mais fotos ou
  pessoas distintas comparadas entre si (I2V anima uma unica cena, nao uma comparacao).
Responda somente JSON.
Formato exato: {{"approved": false, "scores": {{"source_match": 0, "visual_clarity": 0,
"motion_potential": 0, "text_independence": 0}}, "reason": "..."}}
""".strip()
        data = request_json(
            "POST",
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": model,
                "stream": False,
                "think": False,
                "format": schema,
                "messages": [{"role": "user", "content": prompt}],
                "options": {"temperature": 0, "seed": 20260705, "num_predict": 250},
            },
            timeout=timeout,
        )
        review = extract_json_object((data.get("message") or {}).get("content") or "")
        if not review:
            raise ValueError("source suitability model did not return JSON")
        return finalize_source_suitability_review(review)
    except Exception as exc:  # noqa: BLE001
        return {
            "approved": False,
            "error": True,
            "scores": {name: 0.0 for name in ("source_match", "visual_clarity", "motion_potential", "text_independence")},
            "reason": f"source suitability failed: {exc}",
        }


def prepare_source_media(
    posts: list[reddit.RedditPost],
    run_dir: Path,
    args: argparse.Namespace,
) -> tuple[dict[str, str], dict[str, str]]:
    source_media_paths: dict[str, str] = {}
    visual_descriptions: dict[str, str] = {}
    only_indexes = set(args.only_index or [])
    for idx, post in enumerate(posts, 1):
        if only_indexes and idx not in only_indexes:
            continue
        slug = slugify(post.title)
        path = download_source_media(post, run_dir / f"{idx:02d}-{slug}-source")
        if path:
            source_media_paths[post.id] = path
        if args.describe_source_images and post.media_type == "image" and path:
            description = describe_source_image(Path(path), args.vision_model, args.vision_timeout)
            if description:
                visual_descriptions[post.id] = description
                print(f"Source image described {idx}/{len(posts)}: {post.title[:70]}")
    return source_media_paths, visual_descriptions


def load_font(size: int, font_path: Path) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(str(font_path), size=size)
    except Exception:  # noqa: BLE001
        return ImageFont.load_default()


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def draw_meme_text(image_path: Path, output_path: Path, top_text: str, bottom_text: str, font_path: Path) -> None:
    image = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    width, height = image.size
    margin = max(24, width // 24)
    max_text_width = width - 2 * margin

    font_size = max(34, width // 11)
    font = load_font(font_size, font_path)
    stroke = max(2, font_size // 16)

    def draw_block(text: str, y: int, anchor_bottom: bool = False) -> None:
        nonlocal font
        text = text.upper().strip()
        local_size = font_size
        while local_size >= 24:
            font = load_font(local_size, font_path)
            lines = wrap_text(draw, text, font, max_text_width)
            line_height = int(local_size * 1.12)
            block_height = line_height * len(lines)
            if block_height <= height * 0.34:
                break
            local_size -= 4
        start_y = y - block_height if anchor_bottom else y
        for idx, line in enumerate(lines):
            bbox = draw.textbbox((0, 0), line, font=font, stroke_width=stroke)
            x = (width - (bbox[2] - bbox[0])) / 2
            draw.text(
                (x, start_y + idx * line_height),
                line,
                fill="white",
                font=font,
                stroke_width=stroke,
                stroke_fill="black",
            )

    draw_block(top_text, margin)
    draw_block(bottom_text, height - margin, anchor_bottom=True)
    image.save(output_path, quality=95)


def normalize_tts_text(value: str) -> str:
    value = " ".join((value or "").split())
    return value[:240] or "Meme gerado para revisão."


def normalize_ascii_tts_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = re.sub(r"[^A-Za-z0-9 .,!?%-]+", " ", value)
    value = value.replace(":", ",").replace(";", ",")
    value = " ".join(value.split())
    return value[:240] or "Meme gerado para revisao."


def run_ffmpeg(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def extract_video_frame(video_path: Path, output_path: Path) -> Path:
    run_ffmpeg(["-i", str(video_path), "-frames:v", "1", str(output_path)])
    return output_path


def fit_image_contain(image: Image.Image, size: tuple[int, int], fill: tuple[int, int, int]) -> Image.Image:
    image = image.convert("RGB")
    image.thumbnail(size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", size, fill)
    x = (size[0] - image.width) // 2
    y = (size[1] - image.height) // 2
    canvas.paste(image, (x, y))
    return canvas


def draw_video_text_block(
    draw: ImageDraw.ImageDraw,
    text: str,
    y: int,
    width: int,
    font_path: Path,
    size: int,
    anchor_bottom: bool = False,
) -> None:
    text = text.upper().strip()
    margin = max(28, width // 20)
    max_width = width - 2 * margin
    local_size = size
    while local_size >= 24:
        font = load_font(local_size, font_path)
        lines = wrap_text(draw, text, font, max_width)
        line_height = int(local_size * 1.1)
        block_height = line_height * len(lines)
        if block_height <= width * 0.22:
            break
        local_size -= 4
    start_y = y - block_height if anchor_bottom else y
    stroke = max(2, local_size // 14)
    for idx, line in enumerate(lines):
        font = load_font(local_size, font_path)
        bbox = draw.textbbox((0, 0), line, font=font, stroke_width=stroke)
        x = (width - (bbox[2] - bbox[0])) / 2
        draw.text(
            (x, start_y + idx * line_height),
            line,
            fill="white",
            font=font,
            stroke_width=stroke,
            stroke_fill="black",
        )


def make_source_card(
    post: reddit.RedditPost,
    concept: dict[str, str],
    source_media_path: str,
    output_path: Path,
    font_path: Path,
    size: int,
) -> Path:
    card = Image.new("RGB", (size, size), (18, 18, 22))
    media_path = Path(source_media_path) if source_media_path else None
    frame_path = output_path.with_name(output_path.stem + "-frame.jpg")

    if media_path and media_path.exists():
        try:
            if media_path.suffix.lower() in {".mp4", ".mov", ".webm"}:
                media_path = extract_video_frame(media_path, frame_path)
            source = Image.open(media_path)
            card = fit_image_contain(source, (size, size), (18, 18, 22))
        except Exception as exc:  # noqa: BLE001
            print(f"WARN could not use source media in video card: {exc}")

    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    mask = ImageDraw.Draw(overlay)
    mask.rectangle((0, 0, size, int(size * 0.23)), fill=(0, 0, 0, 145))
    mask.rectangle((0, int(size * 0.72), size, size), fill=(0, 0, 0, 165))
    card = Image.alpha_composite(card.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(card)
    draw_video_text_block(draw, "O POST ORIGINAL", max(24, size // 28), size, font_path, max(38, size // 14))
    source_line = compact_phrase(post.title, max_len=58)
    logic = str(concept.get("meme_logic") or concept.get("meme_archetype") or "meme")
    draw_video_text_block(
        draw,
        f"{source_line} / {logic}",
        size - max(24, size // 28),
        size,
        font_path,
        max(26, size // 22),
        anchor_bottom=True,
    )
    card.save(output_path, quality=95)
    return output_path


def make_video_audio(text: str, output_path: Path, duration: float) -> Path:
    narration = output_path.with_suffix(".narration.wav")
    clean_text = normalize_tts_text(text)
    try:
        edge_bin = resolve_tts_binary()
        if edge_bin:
            edge_mp3 = output_path.with_suffix(".edge.mp3")
            subprocess.run(
                [
                    str(edge_bin),
                    "--voice",
                    os.environ.get("EDGE_TTS_VOICE", "pt-BR-AntonioNeural"),
                    "--rate",
                    os.environ.get("EDGE_TTS_RATE", "+8%"),
                    "--text",
                    clean_text,
                    "--write-media",
                    str(edge_mp3),
                ],
                check=True,
            )
            run_ffmpeg(["-i", str(edge_mp3), "-af", "apad", "-t", f"{duration:.2f}", "-c:a", "aac", str(output_path)])
            edge_mp3.unlink(missing_ok=True)
        else:
            clean_ascii = normalize_ascii_tts_text(text)
            run_ffmpeg(["-f", "lavfi", "-i", f"flite=text='{clean_ascii}':voice=kal", str(narration)])
            run_ffmpeg(["-i", str(narration), "-af", "apad", "-t", f"{duration:.2f}", "-c:a", "aac", str(output_path)])
    except Exception as exc:  # noqa: BLE001
        print(f"WARN could not create TTS audio, using tone bed: {exc}")
        run_ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=440:duration={duration:.2f}",
                "-af",
                "volume=0.08,afade=t=in:st=0:d=0.15,afade=t=out:st={:.2f}:d=0.35".format(max(0.0, duration - 0.35)),
                "-c:a",
                "aac",
                str(output_path),
            ]
        )
    finally:
        if narration.exists():
            narration.unlink()
    return output_path


def synthesize_ptbr_speech(text: str, output_path: Path, rate: str) -> Path:
    edge_bin = resolve_tts_binary()
    if not edge_bin:
        raise RuntimeError(
            "PT-BR speech synthesizer unavailable. Set EDGE_TTS_BIN to a persistent executable "
            "or install edge-tts in the runtime image."
        )
    subprocess.run(
        [
            str(edge_bin),
            "--voice",
            os.environ.get("EDGE_TTS_VOICE", "pt-BR-AntonioNeural"),
            "--rate",
            rate,
            "--text",
            normalize_tts_text(text),
            "--write-media",
            str(output_path),
        ],
        check=True,
    )
    return output_path


def concatenate_video_segments(segment_paths: list[Path], output_path: Path) -> Path:
    concat_path = output_path.with_suffix(".concat.txt")
    concat_path.write_text(
        "\n".join(f"file '{path.resolve().as_posix()}'" for path in segment_paths) + "\n",
        encoding="utf-8",
    )
    try:
        run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(concat_path), "-c", "copy", str(output_path)])
    finally:
        concat_path.unlink(missing_ok=True)
    return output_path


def clean_ltx_segment(raw_path: Path, output_path: Path, bottom_pixels: int) -> Path:
    if bottom_pixels <= 0:
        shutil.copy2(raw_path, output_path)
        return output_path
    run_ffmpeg(
        [
            "-i",
            str(raw_path),
            "-vf",
            f"crop=iw:ih-{bottom_pixels}:0:0,pad=iw:ih+{bottom_pixels}:0:{bottom_pixels // 2}:black",
            "-c:v",
            "libx264",
            "-crf",
            "17",
            "-preset",
            "medium",
            "-c:a",
            "copy",
            str(output_path),
        ]
    )
    return output_path


def extract_ltx_continuation_frame(video_path: Path, output_path: Path, bottom_pixels: int) -> Path:
    crop_height = f"ih-{max(0, bottom_pixels)}" if bottom_pixels > 0 else "ih"
    run_ffmpeg(
        [
            "-sseof",
            "-0.08",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-vf",
            f"crop=iw:{crop_height}:0:0,scale=iw:ih+{max(0, bottom_pixels)}:flags=lanczos",
            str(output_path),
        ]
    )
    return output_path


def mix_ptbr_narration(
    ambient_video_path: Path,
    output_path: Path,
    setup_text: str,
    escalation_text: str,
    punchline_text: str,
    duration: float,
    args: argparse.Namespace,
) -> dict[str, Any]:
    setup_audio = output_path.with_name(output_path.stem + "-setup.mp3")
    escalation_audio = output_path.with_name(output_path.stem + "-escalation.mp3")
    punchline_audio = output_path.with_name(output_path.stem + "-punchline.mp3")
    synthesize_ptbr_speech(setup_text, setup_audio, args.tts_rate)
    synthesize_ptbr_speech(escalation_text, escalation_audio, args.tts_rate)
    synthesize_ptbr_speech(punchline_text, punchline_audio, args.tts_rate)
    setup_delay_ms = int(args.tts_setup_at * 1000)
    escalation_delay_ms = int(args.tts_escalation_at * 1000)
    punchline_delay_ms = int(args.tts_punchline_at * 1000)
    filter_complex = (
        f"[0:a]volume={args.ambient_volume}[amb];"
        f"[1:a]adelay={setup_delay_ms}|{setup_delay_ms},volume={args.narration_volume}[setup];"
        f"[2:a]adelay={escalation_delay_ms}|{escalation_delay_ms},volume={args.narration_volume}[middle];"
        f"[3:a]adelay={punchline_delay_ms}|{punchline_delay_ms},volume={args.narration_volume}[punch];"
        f"[amb][setup][middle][punch]amix=inputs=4:duration=longest:dropout_transition=0,"
        f"atrim=0:{duration:.3f},apad[aout]"
    )
    try:
        run_ffmpeg(
            [
                "-i",
                str(ambient_video_path),
                "-i",
                str(setup_audio),
                "-i",
                str(escalation_audio),
                "-i",
                str(punchline_audio),
                "-filter_complex",
                filter_complex,
                "-map",
                "0:v:0",
                "-map",
                "[aout]",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-t",
                f"{duration:.3f}",
                str(output_path),
            ]
        )
    finally:
        setup_audio.unlink(missing_ok=True)
        escalation_audio.unlink(missing_ok=True)
        punchline_audio.unlink(missing_ok=True)
    return {
        "voice": os.environ.get("EDGE_TTS_VOICE", "pt-BR-AntonioNeural"),
        "rate": args.tts_rate,
        "setup_text": setup_text,
        "setup_at_seconds": args.tts_setup_at,
        "escalation_text": escalation_text,
        "escalation_at_seconds": args.tts_escalation_at,
        "punchline_text": punchline_text,
        "punchline_at_seconds": args.tts_punchline_at,
        "ambient_volume": args.ambient_volume,
        "narration_volume": args.narration_volume,
    }


def make_ltx_caption_overlay(
    concept: dict[str, str],
    output_path: Path,
    font_path: Path,
    width: int,
    height: int,
) -> Path:
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    shade = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    mask = ImageDraw.Draw(shade)
    mask.rectangle((0, 0, width, int(height * 0.26)), fill=(0, 0, 0, 115))
    mask.rectangle((0, int(height * 0.68), width, height), fill=(0, 0, 0, 145))
    overlay = Image.alpha_composite(overlay, shade)
    draw = ImageDraw.Draw(overlay)
    text_size = max(24, width // 13)
    margin = max(16, width // 28)
    draw_video_text_block(draw, concept.get("top_text", ""), margin, width, font_path, text_size)
    draw_video_text_block(
        draw,
        concept.get("bottom_text", ""),
        height - margin,
        width,
        font_path,
        text_size,
        anchor_bottom=True,
    )
    overlay.save(output_path)
    return output_path


def finish_ltx_video(
    raw_video_path: Path,
    output_path: Path,
    concept: dict[str, str],
    args: argparse.Namespace,
) -> Path:
    duration = max(1.0, args.ltx_frames / max(args.ltx_fps, 1.0))
    overlay_path = output_path.with_name(output_path.stem + "-overlay.png")
    audio_path = output_path.with_suffix(".m4a")
    script = concept.get("video_script") if isinstance(concept.get("video_script"), dict) else {}
    narration = str(script.get("dialogue") or f"{concept.get('top_text', '')}. {concept.get('bottom_text', '')}.")
    make_ltx_caption_overlay(concept, overlay_path, args.font, args.ltx_width, args.ltx_height)
    make_video_audio(narration, audio_path, duration)
    run_ffmpeg(
        [
            "-i",
            str(raw_video_path),
            "-i",
            str(overlay_path),
            "-i",
            str(audio_path),
            "-filter_complex",
            "[0:v]unsharp=5:5:0.7:3:3:0.3[vb];[vb][1:v]overlay=0:0:format=auto,format=yuv420p[v]",
            "-map",
            "[v]",
            "-map",
            "2:a",
            "-t",
            f"{duration:.2f}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
    return output_path


def make_meme_video(
    source_card_path: Path,
    final_image_path: Path,
    audio_path: Path,
    output_path: Path,
    duration: float,
    size: int,
) -> Path:
    intro_duration = min(2.25, max(1.4, duration * 0.35))
    punch_duration = max(1.5, duration - intro_duration)
    intro_frames = max(1, int(intro_duration * VIDEO_FPS))
    punch_frames = max(1, int(punch_duration * VIDEO_FPS))
    filter_complex = (
        f"[0:v]scale={size + 80}:{size + 80},"
        f"zoompan=z='min(zoom+0.0012,1.07)':d={intro_frames}:s={size}x{size}:fps={VIDEO_FPS},"
        f"trim=duration={intro_duration:.2f},setpts=PTS-STARTPTS[v0];"
        f"[1:v]scale={size + 96}:{size + 96},"
        f"zoompan=z='min(zoom+0.0010,1.08)':d={punch_frames}:s={size}x{size}:fps={VIDEO_FPS},"
        f"trim=duration={punch_duration:.2f},setpts=PTS-STARTPTS[v1];"
        "[v0][v1]concat=n=2:v=1:a=0,format=yuv420p[v]"
    )
    run_ffmpeg(
        [
            "-loop",
            "1",
            "-t",
            f"{intro_duration:.2f}",
            "-i",
            str(source_card_path),
            "-loop",
            "1",
            "-t",
            f"{punch_duration:.2f}",
            "-i",
            str(final_image_path),
            "-i",
            str(audio_path),
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            "-map",
            "2:a",
            "-t",
            f"{duration:.2f}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
    return output_path


def render_review_video_meme(
    post: reddit.RedditPost,
    concept: dict[str, str],
    source_media_path: str,
    final_image_path: Path,
    output_path: Path,
    args: argparse.Namespace,
) -> Path:
    source_card_path = output_path.with_name(output_path.stem + "-source-card.jpg")
    audio_path = output_path.with_suffix(".m4a")
    narration = f"{concept['top_text']}. {concept['bottom_text']}."
    make_source_card(post, concept, source_media_path, source_card_path, args.font, args.video_size)
    make_video_audio(narration, audio_path, args.video_duration)
    make_meme_video(source_card_path, final_image_path, audio_path, output_path, args.video_duration, args.video_size)
    return output_path


def render_ltx_video_meme(
    post: reddit.RedditPost,
    concept: dict[str, str],
    _source_media_path: str,
    final_image_path: Path | None,
    output_path: Path,
    args: argparse.Namespace,
) -> Path:
    prefix = str(output_path.with_suffix("").relative_to(args.output_root.parent))
    seed = int(concept.get("seed") or int(time.time() * 1000) % 2_000_000_000)
    if args.video_engine == "ltx23":
        frames = ltx_valid_frame_count(args.ltx23_frames)
        segment_count = args.ltx23_segments
        if segment_count > 1 and args.ltx23_input_mode == "prompt":
            raise RuntimeError("ltx23 multi-segment rendering requires an image input mode")
        prompts = compose_ltx23_segment_prompts(post, concept, segments=segment_count)
        reference_path: Path | None = None
        if args.ltx23_input_mode == "image":
            if final_image_path is None:
                raise RuntimeError("ltx23 image mode requires a generated base image")
            reference_path = Path(concept.get("base_image_path") or final_image_path)
            if not reference_path.is_file():
                raise RuntimeError(f"ltx23 reference image not found: {reference_path}")
        elif args.ltx23_input_mode == "source":
            source_reference = Path(concept.get("source_media_path") or _source_media_path or "")
            if not source_reference.is_file():
                raise RuntimeError(f"ltx23 source reference image not found: {source_reference}")
            reference_path = source_reference
        print(
            f"  LTX 2.3 native A/V validation: {segment_count} segment(s) x {frames} frames "
            f"({'I2V' if reference_path else 'T2V'})"
        )
        segment_paths: list[Path] = []
        segment_records: list[dict[str, Any]] = []
        current_reference = reference_path
        for segment_index, segment_prompt_text in enumerate(prompts, 1):
            if segment_count == 1:
                segment_prefix, segment_output = prefix, output_path
            else:
                segment_prefix = f"{prefix}-segment-{segment_index}"
                segment_output = output_path.with_name(f"{output_path.stem}-segment-{segment_index}.mp4")
            segment_seed = seed + segment_index - 1
            prompt_id = queue_comfy_ltx23_native_video(
                concept,
                post,
                segment_prefix,
                segment_seed,
                args,
                video_prompt_override=segment_prompt_text,
                frames_override=frames,
                reference_image_path=current_reference,
            )
            ref = wait_for_comfy_video(prompt_id, args.video_render_timeout, args.poll_seconds)
            download_comfy_file(ref, segment_output)
            segment_paths.append(segment_output)
            segment_records.append(
                {
                    "segment": segment_index,
                    "prompt_id": prompt_id,
                    "prompt": segment_prompt_text,
                    "negative_prompt": ltx23_negative_prompt(),
                    "seed": segment_seed,
                    "frames": frames,
                    "fps": args.ltx23_fps,
                    "width": args.ltx23_width,
                    "height": args.ltx23_height,
                    "sampling_steps": None if current_reference else args.ltx23_steps,
                    "sampling_profile": (
                        "official-template-distilled-base8-refine3"
                        if current_reference
                        else "validated-native-av-workflow"
                    ),
                    "input_mode": "image-to-video" if current_reference else "text-to-video",
                    "reference_image_path": str(current_reference) if current_reference else None,
                }
            )
            concept["video_prompt_id"] = prompt_id
            if segment_index < segment_count:
                continuation_path = output_path.with_name(
                    f"{output_path.stem}-segment-{segment_index}-continuation.png"
                )
                current_reference = extract_ltx_continuation_frame(segment_output, continuation_path, 0)
        if segment_count > 1:
            concatenate_video_segments(segment_paths, output_path)
        concept["video_duration_seconds"] = segment_count * frames / args.ltx23_fps
        concept["native_audio"] = True
        concept["ltx23_segments"] = segment_records
        return output_path
    if args.ltx_input_mode == "image":
        if final_image_path is None:
            raise RuntimeError("ltx image mode requires a generated final image")
        reference_path = Path(concept.get("base_image_path") or final_image_path)
        prompt_id = queue_comfy_ltx_image_video(reference_path, concept, post, prefix, seed, args)
    else:
        prompt_id = queue_comfy_ltx_prompt_video(concept, post, prefix, seed, args)
    concept["video_prompt_id"] = prompt_id
    ref = wait_for_comfy_video(prompt_id, args.video_render_timeout, args.poll_seconds)
    raw_video_path = output_path.with_name(output_path.stem + "-ltx-raw.mp4")
    download_comfy_file(ref, raw_video_path)
    concept["raw_video_path"] = str(raw_video_path)
    finish_ltx_video(raw_video_path, output_path, concept, args)
    return output_path


def send_telegram_album(paths: list[Path], summary: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    raw_users = os.environ.get("TELEGRAM_ALLOWED_USERS", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", raw_users.split(",")[0].strip() if raw_users else "").strip()
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID/TELEGRAM_ALLOWED_USERS are required")

    media = []
    files = {}
    for idx, path in enumerate(paths):
        key = f"photo{idx}"
        item = {"type": "photo", "media": f"attach://{key}"}
        if idx == 0:
            item["caption"] = "Memes Reddit do dia para revisao"
        media.append(item)
        files[key] = (path.name, path.open("rb"), "image/png")

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMediaGroup",
            data={"chat_id": chat_id, "media": json.dumps(media, ensure_ascii=False)},
            files=files,
            timeout=120,
        )
        response.raise_for_status()
    finally:
        for file_tuple in files.values():
            file_tuple[1].close()

    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": summary[:3900], "disable_web_page_preview": True},
        timeout=30,
    )
    response.raise_for_status()


def build_summary(posts: list[reddit.RedditPost], concepts: list[dict[str, str]]) -> str:
    lines = ["Memes Reddit do dia - revisao", ""]
    for idx, (post, concept) in enumerate(zip(posts, concepts), 1):
        lines.append(f"{idx}. r/{post.subreddit}: {post.title[:90]}")
        lines.append(f"   {post.url}")
        lines.append(f"   Midia: {post.media_type}" + (f" {post.media_url}" if post.media_url else ""))
        review = concept.get("quality_review") if isinstance(concept.get("quality_review"), dict) else {}
        scores = review.get("scores") if isinstance(review, dict) else {}
        if scores:
            lines.append(
                "   Rubrica: "
                f"read={scores.get('readability', '?')}/5 "
                f"fit={scores.get('source_fit', '?')}/5 "
                f"laugh={scores.get('laugh', '?')}/5 "
                f"share={scores.get('share', '?')}/5 "
                f"artifact={scores.get('artifact', '?')}/5 "
                f"total={scores.get('weighted_total', '?')}"
            )
        if review.get("reason"):
            lines.append(f"   Corte: {str(review['reason'])[:140]}")
        if concept.get("rationale"):
            lines.append(f"   Ideia: {concept['rationale'][:140]}")
    return "\n".join(lines)


def concept_document(post: reddit.RedditPost, concept: dict[str, Any], index: int) -> dict[str, Any]:
    """Convert the renderer's working dictionary to the versioned persistence contract."""

    path_fields = {name: value for name, value in concept.items() if name.endswith("_path") and value not in (None, "")}
    narration = concept.get("narration") if isinstance(concept.get("narration"), dict) else {}
    return {
        "schema_version": CONCEPT_SCHEMA_VERSION,
        "id": f"{post.id}:{index}",
        "post": asdict(post),
        "joke": {
            "setup": str(concept.get("top_text") or ""),
            "escalation": str(concept.get("middle_text") or ""),
            "punchline": str(concept.get("bottom_text") or ""),
            "logic": str(concept.get("meme_logic") or ""),
            "archetype": str(concept.get("meme_archetype") or ""),
            "rationale": str(concept.get("rationale") or ""),
            "scene_payoff": str(concept.get("scene_payoff") or ""),
        },
        "evaluations": {
            "source": deepcopy(concept.get("source_review") or {}),
            "humor": deepcopy(concept.get("humor_review") or {}),
            "quality": deepcopy(concept.get("quality_review") or {}),
            "rounds": deepcopy(concept.get("humor_rounds") or []),
            "approved": bool(concept.get("humor_approved") and concept.get("quality_approved")),
        },
        "production": {
            "image_prompt": str(concept.get("image_prompt") or ""),
            "source_brief": str(concept.get("source_brief") or ""),
            "source_visual_description": str(concept.get("source_visual_description") or ""),
            "video_script": deepcopy(concept.get("video_script") or {}),
            "narration": deepcopy(narration),
        },
        "artifacts": {
            "paths": path_fields,
            "metadata": deepcopy(concept.get("artifact_metadata") or {}),
        },
        "execution": deepcopy(
            concept.get("execution")
            or {"state": "approved" if concept.get("quality_approved") else "rejected", "attempts": {}}
        ),
    }


def validate_concepts_document(document: Any, *, require_artifacts: bool = False) -> list[str]:
    errors: list[str] = []
    if not isinstance(document, list):
        return ["document must be a list"]
    for index, item in enumerate(document, 1):
        prefix = f"concept[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix} must be an object")
            continue
        if item.get("schema_version") != CONCEPT_SCHEMA_VERSION:
            errors.append(f"{prefix}.schema_version must be {CONCEPT_SCHEMA_VERSION}")
        for section in ("post", "joke", "evaluations", "production", "artifacts", "execution"):
            if not isinstance(item.get(section), dict):
                errors.append(f"{prefix}.{section} must be an object")
        joke = item.get("joke") if isinstance(item.get("joke"), dict) else {}
        for field in ("setup", "punchline", "logic"):
            if not isinstance(joke.get(field), str) or not joke.get(field).strip():
                errors.append(f"{prefix}.joke.{field} is required")
        execution = item.get("execution") if isinstance(item.get("execution"), dict) else {}
        if execution.get("state") not in VALID_STAGE_STATES:
            errors.append(f"{prefix}.execution.state is invalid")
        paths = ((item.get("artifacts") or {}).get("paths") or {}) if isinstance(item.get("artifacts"), dict) else {}
        for name, value in paths.items():
            if not isinstance(value, str):
                errors.append(f"{prefix}.artifacts.paths.{name} must be a string path")
            elif name == "video_path" and not value.lower().endswith(".mp4"):
                errors.append(f"{prefix}.artifacts.paths.video_path must end in .mp4")
            elif require_artifacts and not Path(value).is_file():
                errors.append(f"{prefix}.artifacts.paths.{name} does not exist")
    return errors


def persist_concepts(path: Path, posts: list[reddit.RedditPost], concepts: list[dict[str, Any]], *, require_artifacts: bool = False) -> None:
    document = [concept_document(post, concept, index) for index, (post, concept) in enumerate(zip(posts, concepts), 1)]
    errors = validate_concepts_document(document, require_artifacts=require_artifacts)
    if errors:
        raise ValueError("invalid concepts document: " + "; ".join(errors))
    write_json(path, document)


def hydrate_concept_record(record: dict[str, Any]) -> tuple[reddit.RedditPost, dict[str, Any]]:
    """Rehydrate a persisted concept document item into the working render shape."""

    post_data = record.get("post")
    joke = record.get("joke") if isinstance(record.get("joke"), dict) else {}
    evaluations = record.get("evaluations") if isinstance(record.get("evaluations"), dict) else {}
    production = record.get("production") if isinstance(record.get("production"), dict) else {}
    artifacts = record.get("artifacts") if isinstance(record.get("artifacts"), dict) else {}
    execution = deepcopy(record.get("execution") or {"state": "approved", "attempts": {}})
    post = reddit.RedditPost(**post_data)
    concept = {
        "top_text": str(joke.get("setup") or ""),
        "middle_text": str(joke.get("escalation") or ""),
        "bottom_text": str(joke.get("punchline") or ""),
        "meme_logic": str(joke.get("logic") or ""),
        "meme_archetype": str(joke.get("archetype") or ""),
        "rationale": str(joke.get("rationale") or ""),
        "scene_payoff": str(joke.get("scene_payoff") or ""),
        "source_review": deepcopy(evaluations.get("source") or {}),
        "humor_review": deepcopy(evaluations.get("humor") or {}),
        "quality_review": deepcopy(evaluations.get("quality") or {}),
        "humor_rounds": deepcopy(evaluations.get("rounds") or []),
        "source_visual_description": str(production.get("source_visual_description") or ""),
        "source_brief": str(production.get("source_brief") or ""),
        "image_prompt": str(production.get("image_prompt") or ""),
        "video_script": deepcopy(production.get("video_script") or {}),
        "narration": deepcopy(production.get("narration") or {}),
        "artifact_metadata": deepcopy(artifacts.get("metadata") or {}),
        "execution": execution,
        "humor_approved": bool((evaluations.get("humor") or {}).get("approved")),
        "quality_approved": bool((evaluations.get("quality") or {}).get("approved")),
    }
    if isinstance(artifacts.get("paths"), dict):
        for name, value in artifacts["paths"].items():
            if isinstance(value, str) and value:
                concept[name] = value
    return post, concept


def load_approved_concepts_document(path: Path) -> tuple[list[reddit.RedditPost], list[dict[str, Any]]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    errors = validate_concepts_document(document, require_artifacts=False)
    if errors:
        raise ValueError("invalid concepts document: " + "; ".join(errors))
    posts: list[reddit.RedditPost] = []
    concepts: list[dict[str, Any]] = []
    for index, record in enumerate(document, 1):
        post, concept = hydrate_concept_record(record)
        evaluations = record.get("evaluations") if isinstance(record.get("evaluations"), dict) else {}
        source = evaluations.get("source") if isinstance(evaluations.get("source"), dict) else {}
        humor = evaluations.get("humor") if isinstance(evaluations.get("humor"), dict) else {}
        quality = evaluations.get("quality") if isinstance(evaluations.get("quality"), dict) else {}
        if not (source.get("approved") and humor.get("approved") and quality.get("approved")):
            raise ValueError(f"concept[{index}] is not fully approved")
        execution = concept.get("execution") if isinstance(concept.get("execution"), dict) else {}
        if execution.get("state") not in {"approved", "completed"}:
            raise ValueError(f"concept[{index}].execution.state must be approved for resume rendering")
        posts.append(post)
        concepts.append(concept)
    return posts, concepts


def set_stage_state(concept: dict[str, Any], stage: str, state: str, reason: str = "") -> None:
    if state not in VALID_STAGE_STATES:
        raise ValueError(f"invalid state: {state}")
    execution = concept.setdefault("execution", {"state": "pending", "attempts": {}})
    stages = execution.setdefault("stages", {})
    attempts = execution.setdefault("attempts", {})
    if state == "running":
        attempts[stage] = int(attempts.get(stage, 0)) + 1
    stages[stage] = {"state": state, "reason": reason, "updated_at": datetime.now().astimezone().isoformat()}
    execution["state"] = state


def load_frozen_posts(path: Path) -> list[reddit.RedditPost]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("frozen posts file must contain a JSON list")
    return [reddit.RedditPost(**item) for item in data]


def load_frozen_concept_seeds(path: Path) -> dict[str, list[dict[str, Any]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("frozen concepts file must contain a JSON list")
    required = {"id", "mechanic", "setup", "escalation", "punchline", "comic_turn", "scene_payoff"}
    result: dict[str, list[dict[str, Any]]] = {}
    for entry_index, entry in enumerate(data, 1):
        if not isinstance(entry, dict):
            raise ValueError(f"concept seed entry {entry_index} must be an object")
        post_id = str(entry.get("post_id") or "").strip()
        candidates = entry.get("candidates")
        if not post_id:
            raise ValueError(f"concept seed entry {entry_index} requires post_id")
        if post_id in result:
            raise ValueError(f"duplicate concept seed post_id: {post_id}")
        if not isinstance(candidates, list) or not 1 <= len(candidates) <= MAX_CONCEPTS_PER_POST:
            raise ValueError(f"concept seeds for {post_id} must contain 1 to {MAX_CONCEPTS_PER_POST} candidates")
        ids: set[int] = set()
        normalized: list[dict[str, Any]] = []
        for candidate_index, candidate in enumerate(candidates, 1):
            if not isinstance(candidate, dict) or not required.issubset(candidate):
                missing = sorted(required - set(candidate)) if isinstance(candidate, dict) else sorted(required)
                raise ValueError(f"concept seed {post_id}/{candidate_index} missing fields: {', '.join(missing)}")
            candidate_id = candidate.get("id")
            if not isinstance(candidate_id, int) or candidate_id <= 0 or candidate_id in ids:
                raise ValueError(f"concept seed {post_id}/{candidate_index} has invalid or duplicate id")
            ids.add(candidate_id)
            for field in required - {"id"}:
                if not isinstance(candidate.get(field), str) or not candidate[field].strip():
                    raise ValueError(f"concept seed {post_id}/{candidate_index}.{field} must be non-empty text")
            normalized.append({name: candidate[name] for name in required})
        result[post_id] = normalized
    return result


def resolve_tts_binary() -> Path | None:
    configured = os.environ.get("EDGE_TTS_BIN", "").strip()
    candidate = Path(configured) if configured else None
    if candidate and candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate
    system_bin = shutil.which("edge-tts")
    return Path(system_bin) if system_bin else None


def run_preflight(args: argparse.Namespace) -> dict[str, Any]:
    """Check all dependencies once, before any expensive or mutable stage."""

    checks: dict[str, Any] = {}
    errors: list[str] = []
    args.output_root.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(args.output_root)
    checks["disk_free_bytes"] = usage.free
    if usage.free < 2 * 1024**3:
        errors.append("less than 2 GiB free in output filesystem")
    checks["homelab_compose_root"] = str(HOMELAB_COMPOSE_ROOT)
    checks["homelab_compose_file"] = str(HOMELAB_COMPOSE_FILE)
    checks["homelab_compose_present"] = HOMELAB_COMPOSE_FILE.is_file()
    if not HOMELAB_COMPOSE_FILE.is_file():
        errors.append(f"homelab compose file unavailable: {HOMELAB_COMPOSE_FILE}")
    else:
        compose_text = HOMELAB_COMPOSE_FILE.read_text(encoding="utf-8")
        if "ollama:" not in compose_text or "comfyui:" not in compose_text:
            errors.append("homelab compose does not define both Ollama and ComfyUI services")

    required_models: set[str] = set()
    if not args.skip_ollama_concepts and not getattr(args, "approved_concepts_file", None):
        required_models.update(
            (
                args.ollama_model,
                args.humor_model,
                args.humor_critic_model,
                args.humor_second_critic_model,
                args.source_critic_model,
            )
        )
    if args.describe_source_images:
        required_models.add(args.vision_model)
    if required_models:
        try:
            tags = request_json("GET", f"{OLLAMA_URL}/api/tags", timeout=args.preflight_timeout)
            available = {item.get("name") for item in tags.get("models", []) if isinstance(item, dict)}
            checks["ollama_models"] = sorted(name for name in available if name)
            for model in required_models:
                if model not in available and not any(str(name).split(":")[0] == model.split(":")[0] for name in available):
                    errors.append(f"Ollama model unavailable: {model}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Ollama unavailable: {exc}")

    if not args.no_render:
        try:
            response = requests.get(f"{COMFYUI_URL}/system_stats", timeout=args.preflight_timeout)
            response.raise_for_status()
            checks["comfyui"] = "ok"
        except Exception as exc:  # noqa: BLE001
            errors.append(f"ComfyUI unavailable: {exc}")
        ltx23_workflow_path = ltx23_workflow_path_for_mode(getattr(args, "ltx23_input_mode", "image"))
        if args.make_video and args.video_engine == "ltx23" and ltx23_workflow_path.is_file():
            try:
                object_info = request_json("GET", f"{COMFYUI_URL}/object_info", timeout=args.preflight_timeout)
                workflow = json.loads(ltx23_workflow_path.read_text(encoding="utf-8"))["prompt"]
                required_classes = {node["class_type"] for node in workflow.values()}
                missing_classes = sorted(required_classes - set(object_info))
                checks["ltx23_required_node_classes"] = sorted(required_classes)
                checks["ltx23_missing_node_classes"] = missing_classes
                if missing_classes:
                    errors.append("ComfyUI LTX node classes unavailable: " + ", ".join(missing_classes))

                def choices(class_name: str, input_name: str) -> set[str]:
                    spec = (((object_info.get(class_name) or {}).get("input") or {}).get("required") or {}).get(input_name)
                    if not isinstance(spec, list) or not spec:
                        return set()
                    if isinstance(spec[0], list):
                        return set(spec[0])
                    if (
                        len(spec) > 1
                        and isinstance(spec[1], dict)
                        and isinstance(spec[1].get("options"), list)
                    ):
                        return set(spec[1]["options"])
                    return set()

                model_checks = (
                    ("checkpoint", args.ltx23_ckpt_name, choices("CheckpointLoaderSimple", "ckpt_name")),
                    ("LoRA", args.ltx23_lora_name, choices("LoraLoaderModelOnly", "lora_name")),
                    ("text encoder", args.ltx23_text_encoder, choices("LTXAVTextEncoderLoader", "text_encoder")),
                )
                for label, configured, available in model_checks:
                    if available and configured not in available:
                        errors.append(f"ComfyUI {label} unavailable: {configured}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"ComfyUI LTX inventory failed: {exc}")
    if args.make_video:
        for binary in ("ffmpeg", "ffprobe"):
            resolved = shutil.which(binary)
            checks[binary] = resolved or ""
            if not resolved:
                errors.append(f"required binary unavailable: {binary}")
        if args.video_engine == "ltx23":
            ltx23_workflow_path = ltx23_workflow_path_for_mode(args.ltx23_input_mode)
            checks["ltx23_workflow"] = str(ltx23_workflow_path)
            if not ltx23_workflow_path.is_file():
                errors.append(f"validated LTX 2.3 workflow unavailable: {ltx23_workflow_path}")
            if not LTX23_API_WORKFLOW.is_file():
                errors.append(f"validated LTX 2.3 workflow unavailable: {LTX23_API_WORKFLOW}")
    checks["errors"] = errors
    if errors:
        raise RuntimeError("preflight failed: " + "; ".join(errors))
    return checks


def probe_video_artifact(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"video does not exist: {path}")
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    if not video or not audio:
        raise ValueError("MP4 must contain both video and audio streams")
    duration = float((data.get("format") or {}).get("duration") or 0)
    if duration <= 0:
        raise ValueError("MP4 duration is invalid")
    return {
        "duration_seconds": round(duration, 3),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "video_codec": video.get("codec_name", ""),
        "audio_codec": audio.get("codec_name", ""),
        "has_audio": True,
    }


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def build_human_review_sheet(posts: list[reddit.RedditPost], concepts: list[dict[str, Any]]) -> str:
    lines = ["# Avaliação humana", "", "Marque cada item somente após assistir ao vídeo completo.", ""]
    for index, (post, concept) in enumerate(zip(posts, concepts), 1):
        lines.extend(
            [
                f"## {index}. {post.title}",
                "",
                f"- Fonte: {post.url}",
                f"- Texto: {concept.get('top_text', '')} / {concept.get('middle_text', '')} / {concept.get('bottom_text', '')}",
                f"- Relação declarada: {concept.get('meme_logic', '')}",
                f"- Vídeo: {concept.get('video_path', 'não renderizado')}",
                "- [ ] Entendi em até 2 segundos",
                "- [ ] A piada depende desse post",
                "- [ ] Achei engraçado",
                "- [ ] Eu compartilharia",
                "- Observações:",
                "",
            ]
        )
    return "\n".join(lines)


def clean_run_dir(path: Path) -> None:
    for item in path.iterdir():
        if item.is_file() and item.suffix.lower() in {
            ".json",
            ".md",
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".mp4",
            ".m4a",
            ".wav",
        }:
            item.unlink()
        elif item.is_dir() and item.name.startswith("source-"):
            shutil.rmtree(item)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Daily Reddit meme pipeline.")
    parser.add_argument("--subreddit", action="append", dest="subreddits", default=None)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--max-per-subreddit", type=int, default=3)
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--backoff-base", type=float, default=15.0)
    parser.add_argument("--backoff-max", type=float, default=180.0)
    parser.add_argument("--jitter", type=float, default=2.0)
    parser.add_argument("--cache-dir", type=Path, default=reddit.DEFAULT_CACHE_DIR)
    parser.add_argument("--posts-file", type=Path, help="Frozen selected.json input; bypasses live Reddit.")
    parser.add_argument(
        "--concepts-file",
        type=Path,
        help="Frozen 1-5 humor candidates per post; bypasses the humor writer but not critic or quality gates.",
    )
    parser.add_argument(
        "--approved-concepts-file",
        type=Path,
        help="Versioned concepts.json with approved source/humor/quality; bypasses generation and critics for resume rendering.",
    )
    parser.add_argument("--cache-on-failure", action="store_true", default=True)
    parser.add_argument("--write-cache", action="store_true", default=True)
    parser.add_argument("--max-age-hours", type=int, default=72)
    parser.add_argument("--include-automoderator", action="store_true")
    parser.add_argument("--ollama-model", default=DEFAULT_OLLAMA_MODEL)
    parser.add_argument("--vision-model", default=DEFAULT_VISION_MODEL)
    parser.add_argument("--source-critic-model", default="gemma3:12b")
    parser.add_argument("--humor-model", default="qwen3:8b")
    parser.add_argument(
        "--humor-critic-model",
        default="llama3:latest",
        help="Independent Ollama model used only to review humor candidates.",
    )
    parser.add_argument(
        "--humor-second-critic-model",
        default="qwen2.5vl:7b",
        help=(
            "Second Ollama humor critic; approval requires consensus with the first critic. "
            "Vision-capable model names (containing 'vl', 'vision' or 'llava') receive the actual "
            "source image alongside the text so scoring is not blind to visual nuance."
        ),
    )
    parser.add_argument("--vision-timeout", type=int, default=90)
    parser.add_argument("--describe-source-images", action="store_true", default=True)
    parser.add_argument("--no-describe-source-images", action="store_false", dest="describe_source_images")
    parser.add_argument("--concept-timeout", type=int, default=600)
    parser.add_argument("--preflight-timeout", type=int, default=5)
    parser.add_argument("--skip-ollama-concepts", action="store_true")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--ckpt-name", default=DEFAULT_CKPT_NAME)
    parser.add_argument("--width", type=int, default=768)
    parser.add_argument("--height", type=int, default=768)
    parser.add_argument("--steps", type=int, default=6)
    parser.add_argument("--cfg", type=float, default=1.0)
    parser.add_argument("--sampler-name", default="euler")
    parser.add_argument("--scheduler", default="simple")
    parser.add_argument("--seed-base", type=int, default=None)
    parser.add_argument("--run-tag", default=None)
    parser.add_argument("--only-index", action="append", type=int, default=None)
    parser.add_argument("--render-timeout", type=int, default=240)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--font", type=Path, default=DEFAULT_FONT)
    parser.add_argument(
        "--make-video",
        action="store_true",
        help="Generate video artifacts. Default engine is ltx23 native image-to-video with audio.",
    )
    parser.add_argument(
        "--video-engine",
        choices=("ltx23", "ltx", "review"),
        default="ltx23",
        help="ltx23 is native LTX 2.3 audio/video; ltx is legacy LTX 0.9.5; review is local ffmpeg composition only.",
    )
    parser.add_argument("--video-duration", type=float, default=15.0)
    parser.add_argument("--video-size", type=int, default=768)
    parser.add_argument("--video-render-timeout", type=int, default=600)
    parser.add_argument("--ltx-ckpt-name", default=DEFAULT_LTX_CKPT_NAME)
    parser.add_argument("--ltx-text-encoder", default=DEFAULT_LTX_TEXT_ENCODER)
    parser.add_argument("--ltx-text-encoder-device", choices=("default", "cpu"), default="cpu")
    parser.add_argument("--ltx-width", type=int, default=640)
    parser.add_argument("--ltx-height", type=int, default=384)
    parser.add_argument("--ltx-frames", type=int, default=121)
    parser.add_argument("--ltx-fps", type=float, default=12.0)
    parser.add_argument("--ltx-steps", type=int, default=8)
    parser.add_argument("--ltx-cfg", type=float, default=1.0)
    parser.add_argument("--ltx-strength", type=float, default=0.95)
    parser.add_argument("--ltx-sampler-name", default="euler")
    parser.add_argument("--ltx-scheduler", default="simple")
    parser.add_argument(
        "--ltx-input-mode",
        choices=("prompt", "image"),
        default="prompt",
        help="prompt skips base image generation and runs LTX from text only; image uses the older generated-image reference path.",
    )
    parser.add_argument("--ltx23-ckpt-name", default=DEFAULT_LTX23_CKPT_NAME)
    parser.add_argument("--ltx23-text-encoder", default=DEFAULT_LTX23_TEXT_ENCODER)
    parser.add_argument("--ltx23-text-encoder-device", choices=("default", "cpu"), default="cpu")
    parser.add_argument(
        "--ltx23-input-mode",
        choices=("image", "source", "prompt"),
        default="image",
        help="image uses the generated clean base image as the LTX 2.3 I2V reference; source uses the downloaded source image; prompt keeps the T2V validation path.",
    )
    parser.add_argument("--ltx23-lora-name", default=DEFAULT_LTX23_LORA)
    parser.add_argument("--ltx23-lora-strength", type=float, default=0.5)
    parser.add_argument("--ltx23-upscaler-name", default=DEFAULT_LTX23_UPSCALER)
    parser.add_argument("--ltx23-width", type=int, default=1280)
    parser.add_argument("--ltx23-height", type=int, default=720)
    parser.add_argument("--ltx23-frames", type=int, default=49)
    parser.add_argument("--ltx23-segments", type=int, choices=(1, 2), default=1)
    parser.add_argument("--ltx23-fps", type=float, default=25.0)
    parser.add_argument("--ltx23-steps", type=int, default=8)
    parser.add_argument("--ltx23-audio-cfg", type=float, default=7.0)
    parser.add_argument("--ltx23-video-cfg", type=float, default=3.0)
    parser.add_argument("--ltx23-sampler-name", default="euler_cfg_pp")
    parser.add_argument("--ltx23-decode-tiles", type=int, default=2)
    parser.add_argument("--ltx23-segment-seconds", type=float, default=5.0)
    parser.add_argument("--ltx23-clean-bottom", type=int, default=76)
    parser.add_argument("--ltx23-continuation-strength", type=float, default=0.92)
    parser.add_argument("--ltx23-reference-compression", type=int, default=18)
    parser.add_argument("--tts-rate", default="-12%")
    parser.add_argument("--tts-setup-at", type=float, default=0.5)
    parser.add_argument("--tts-escalation-at", type=float, default=4.2)
    parser.add_argument("--tts-punchline-at", type=float, default=10.0)
    parser.add_argument("--ambient-volume", type=float, default=0.22)
    parser.add_argument("--narration-volume", type=float, default=1.0)
    parser.add_argument("--keep-video-segments", action="store_true")
    parser.add_argument(
        "--allow-unapproved-humor",
        action="store_true",
        help="Render concepts rejected by the humor critic. Disabled by default to avoid spending GPU on weak jokes.",
    )
    parser.add_argument("--no-render", action="store_true")
    parser.add_argument("--telegram", action="store_true", help="Opt in to Telegram delivery using private environment values.")
    parser.add_argument("--no-telegram", action="store_false", dest="telegram", default=False, help=argparse.SUPPRESS)
    parser.add_argument("--clean-output", action="store_true")
    parser.add_argument("--env-file", type=Path, default=Path.home() / ".hermes" / ".env")
    parser.add_argument("--ollama-url", help="Ollama base URL (overrides OLLAMA_URL).")
    parser.add_argument("--comfyui-url", help="ComfyUI base URL (overrides COMFYUI_URL).")
    parser.add_argument("--n8n-url", help="Optional n8n base URL (overrides N8N_URL).")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.subreddits = args.subreddits or reddit.DEFAULT_SUBREDDITS
    min_ltx_frames = ltx_valid_frame_count(int(MIN_LTX_VIDEO_SECONDS * max(args.ltx_fps, 1.0)))
    if args.make_video and args.video_engine == "ltx" and args.ltx_frames < min_ltx_frames:
        print(
            f"WARN --ltx-frames={args.ltx_frames} is shorter than {MIN_LTX_VIDEO_SECONDS:.0f}s "
            f"at {args.ltx_fps:g} fps; using {min_ltx_frames} frames."
        )
        args.ltx_frames = min_ltx_frames
    load_env_file(args.env_file)
    configure_service_urls(args)
    if args.approved_concepts_file:
        args.describe_source_images = False

    try:
        preflight = run_preflight(args)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR {exc}")
        return 2

    today = datetime.now().strftime("%Y-%m-%d")
    run_dir = args.output_root / today
    run_dir.mkdir(parents=True, exist_ok=True)
    if args.clean_output:
        clean_run_dir(run_dir)
    run_tag = args.run_tag or datetime.now().strftime("%H%M%S")
    seed_base = args.seed_base if args.seed_base is not None else int(time.time() * 1000) % 2_000_000_000
    write_json(run_dir / "preflight.json", preflight)

    print(f"Daily Reddit meme pipeline - {today}")
    approved_resume = bool(args.approved_concepts_file)
    try:
        if approved_resume:
            posts, concepts = load_approved_concepts_document(args.approved_concepts_file)
            seed_candidates_by_post = {}
        else:
            posts = load_frozen_posts(args.posts_file)[: args.limit] if args.posts_file else select_candidates(args)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR candidate selection failed: {exc}")
        return 3
    if not posts:
        print("No candidates found.")
        return 1
    try:
        if approved_resume and (args.concepts_file or args.skip_ollama_concepts):
            raise ValueError("--approved-concepts-file cannot be combined with concept seeds or skip-ollama-concepts")
        seed_candidates_by_post = {} if approved_resume else load_frozen_concept_seeds(args.concepts_file) if args.concepts_file else {}
        if seed_candidates_by_post:
            missing_seed_posts = [post.id for post in posts if post.id not in seed_candidates_by_post]
            if missing_seed_posts:
                raise ValueError("missing frozen concept seeds for posts: " + ", ".join(missing_seed_posts))
        if not approved_resume and args.concepts_file and args.skip_ollama_concepts:
            raise ValueError("--concepts-file cannot be combined with --skip-ollama-concepts")
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR frozen concept seeds failed validation: {exc}")
        return 4
    write_json(run_dir / "selected.json", [asdict(post) for post in posts])
    if not args.no_render:
        free_comfy_memory()
    source_media_paths, visual_descriptions = prepare_source_media(posts, run_dir, args)
    source_reviews: dict[str, dict[str, Any]] = {}
    if approved_resume:
        for post, concept in zip(posts, concepts):
            source_reviews[post.id] = deepcopy(concept.get("source_review") or {})
            approved = bool(concept.get("humor_approved") and concept.get("quality_approved"))
            set_stage_state(
                concept,
                "quality_gate",
                "approved" if approved else "rejected",
                str((concept.get("quality_review") or {}).get("reason", "")),
            )
    else:
        for post in posts:
            source_path = source_media_paths.get(post.id, "")
            if post.media_type != "image" or not source_path:
                source_reviews[post.id] = {
                    "approved": False,
                    "scores": {name: 0.0 for name in ("source_match", "visual_clarity", "motion_potential", "text_independence")},
                    "reason": "controlled I2V experiment requires a downloaded source image",
                }
                continue
            source_reviews[post.id] = assess_source_suitability(
                post,
                Path(source_path),
                visual_descriptions.get(post.id, ""),
                args.source_critic_model,
                args.vision_timeout,
            )
            print(
                f"Source gate {post.id}: "
                f"{'approved' if source_reviews[post.id]['approved'] else 'rejected'} - "
                f"{source_reviews[post.id]['reason']}"
            )

        if args.skip_ollama_concepts:
            concepts = [fallback_concept(post, visual_descriptions.get(post.id, "")) for post in posts]
        else:
            def checkpoint_partial(partial: list[dict[str, Any]]) -> None:
                # Best-effort: losing a checkpoint must never abort the batch.
                try:
                    persist_concepts(run_dir / "concepts.json", posts, partial)
                except Exception as exc:  # noqa: BLE001
                    print(f"WARN concept checkpoint failed (continuing): {exc}")

            concepts = generate_concepts(
                posts,
                args.ollama_model,
                args.concept_timeout,
                visual_descriptions,
                humor_model=args.humor_model,
                humor_critic_model=args.humor_critic_model,
                humor_second_critic_model=args.humor_second_critic_model,
                seed_candidates_by_post=seed_candidates_by_post,
                source_reviews=source_reviews,
                image_paths=source_media_paths,
                checkpoint=checkpoint_partial,
            )
        for concept in concepts:
            approved = bool(concept.get("humor_approved") and concept.get("quality_approved"))
            set_stage_state(
                concept,
                "quality_gate",
                "approved" if approved else "rejected",
                str((concept.get("quality_review") or {}).get("reason", "")),
            )
    persist_concepts(run_dir / "concepts.json", posts, concepts)
    if not approved_resume:
        flush_ollama(args.ollama_model)
        if args.humor_model != args.ollama_model:
            flush_ollama(args.humor_model)
        if args.humor_critic_model not in {args.ollama_model, args.humor_model}:
            flush_ollama(args.humor_critic_model)
        if args.humor_second_critic_model not in {args.ollama_model, args.humor_model, args.humor_critic_model}:
            flush_ollama(args.humor_second_critic_model)
        if args.vision_model not in {
            args.ollama_model, args.humor_model, args.humor_critic_model, args.humor_second_critic_model
        }:
            flush_ollama(args.vision_model)
    if not args.no_render:
        free_comfy_memory()

    final_paths: list[Path] = []
    video_paths: list[Path] = []
    if args.no_render:
        print("--no-render enabled; stopping before ComfyUI.")
    else:
        only_indexes = set(args.only_index or [])
        for idx, (post, concept) in enumerate(zip(posts, concepts), 1):
            if only_indexes and idx not in only_indexes:
                continue
            quality_review = concept.get("quality_review") if isinstance(concept.get("quality_review"), dict) else {}
            if quality_review and not quality_review.get("approved"):
                reason = quality_review.get("reason", "rubric quality rejected the concept")
                print(f"Skipping {idx}/{len(posts)} before render: {reason}")
                set_stage_state(concept, "render", "rejected", str(reason))
                continue
            if concept.get("humor_approved") is False and not args.allow_unapproved_humor:
                reason = (concept.get("humor_review") or {}).get("reason", "humor critic rejected the concept")
                print(f"Skipping {idx}/{len(posts)} before render: {reason}")
                set_stage_state(concept, "render", "rejected", str(reason))
                continue
            slug = slugify(post.title)
            seed = seed_base + idx
            prefix = f"reddit-memes/{today}/{run_tag}/{idx:02d}-{slug}"
            print(f"Rendering {idx}/{len(posts)}: {post.title[:80]}")
            concept["seed"] = str(seed)
            concept["comfyui_prefix"] = prefix
            source_media_path = source_media_paths.get(post.id, "")
            final_path: Path | None = None
            set_stage_state(concept, "render", "running")
            persist_concepts(run_dir / "concepts.json", posts, concepts)
            try:
                skip_base_image = args.make_video and (
                    (args.video_engine == "ltx23" and args.ltx23_input_mode == "prompt")
                    or (args.video_engine == "ltx" and args.ltx_input_mode == "prompt")
                )
                if skip_base_image:
                    print("Skipping base image generation; video engine will use text prompt inputs.")
                else:
                    base_path = run_dir / f"{idx:02d}-{slug}-base.png"
                    final_path = run_dir / f"{idx:02d}-{slug}.png"
                    if final_path.is_file():
                        print(f"Reusing completed image: {final_path.name}")
                    else:
                        prompt_id = queue_comfy_image(concept, prefix, seed, args)
                        concept["prompt_id"] = prompt_id
                        ref = wait_for_comfy_output(prompt_id, args.render_timeout, args.poll_seconds)
                        download_comfy_image(ref, base_path)
                        draw_meme_text(base_path, final_path, concept["top_text"], concept["bottom_text"], args.font)
                    if base_path.is_file():
                        concept["base_image_path"] = str(base_path)
                    concept["final_image_path"] = str(final_path)
                if source_media_path:
                    concept["source_media_path"] = source_media_path
                if args.make_video:
                    video_output_path = run_dir / f"{idx:02d}-{slug}.mp4"
                    if video_output_path.is_file():
                        print(f"Reusing completed video: {video_output_path.name}")
                    else:
                        print(f"Rendering video {idx}/{len(posts)}: {video_output_path.name}")
                        if args.video_engine == "review":
                            if final_path is None:
                                raise RuntimeError("review video mode requires a generated final image")
                            render_review_video_meme(post, concept, source_media_path, final_path, video_output_path, args)
                        else:
                            render_ltx_video_meme(post, concept, source_media_path, final_path, video_output_path, args)
                    concept["video_path"] = str(video_output_path)
                    concept["artifact_metadata"] = probe_video_artifact(video_output_path)
                    video_paths.append(video_output_path)
                if final_path is not None:
                    final_paths.append(final_path)
                set_stage_state(concept, "render", "approved")
            except Exception as exc:  # noqa: BLE001
                set_stage_state(concept, "render", "failed", str(exc))
                print(f"ERROR render failed for {post.id}: {exc}")
            persist_concepts(run_dir / "concepts.json", posts, concepts)
            time.sleep(1)

    summary = build_summary(posts, concepts)
    persist_concepts(run_dir / "concepts.json", posts, concepts, require_artifacts=True)
    (run_dir / "summary.md").write_text(summary, encoding="utf-8")
    (run_dir / "human-review.md").write_text(build_human_review_sheet(posts, concepts), encoding="utf-8")

    if final_paths and args.telegram:
        send_telegram_album(final_paths, summary)
        print(f"Telegram sent: {len(final_paths)} images")
    elif not args.telegram:
        print("Telegram disabled by default; not sending. Use --telegram to opt in.")

    print(f"Artifacts: {run_dir}")
    if video_paths:
        print(f"Videos: {len(video_paths)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
