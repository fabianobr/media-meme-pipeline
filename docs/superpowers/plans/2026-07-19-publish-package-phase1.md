# Pacote de Publicação Fase 1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cada vídeo aprovado sai como pacote de publicação Shorts/Reels/TikTok: `final_916.mp4` (1080×1920) + `publish.json` + `publish.txt` (título, descrição, assuntos de interesse, hashtags em PT-BR), com `publish_id` rastreável.

**Architecture:** Metadados gerados por Ollama após a aprovação da piada e antes do render (seção nova `publish` no contrato versionado de `concepts.json`, v3); formatação 9:16 por ffmpeg blur-pad como passo final pós-validação (nunca toca no caminho LTX); curadoria passa a priorizar fotos retrato no backlog; Telegram ganha envio de vídeo com caption colável.

**Tech Stack:** Python 3 (stdlib + Pillow/requests já pinados), Ollama via HTTP (`request_json`), ffmpeg via `run_ffmpeg`, unittest/pytest.

**Spec:** `docs/superpowers/specs/2026-07-19-publish-package-phase1-design.md`

## Global Constraints

- Idioma dos metadados: **pt-BR**.
- `title` ≤ **100** chars; `interest_topics` **3–5** itens; `hashtags` **4–8** itens.
- Geração de `publish`: máximo **3 tentativas** (1 + 2 re-tentativas); na falha final `status: "failed"` — **nunca** completar valores artificialmente, **nunca** bloquear o render.
- Falha do ffmpeg de formatação **não aborta o run** (o MP4 nativo validado permanece o entregável).
- Canvas 9:16 fixo: **1080×1920**, fundo desfocado do próprio vídeo quando não preenche.
- **Não alterar** o caminho de render LTX (grafos em `workflows/`, orientação seguindo a foto-fonte).
- `CONCEPT_SCHEMA_VERSION` passa a **3**; leitura aceita **{2, 3}**; escrita sempre 3.
- `scripts/daily_reddit_meme_pipeline.py` deve continuar importável sem efeitos colaterais (os testes importam via `sys.path`).
- Caption Telegram truncada em **1024** chars.
- Repo público: nenhum artefato gerado, token ou ID commitado; rodar `./scripts/check_public_ready.sh` antes do commit final.

---

### Task 1: Metadados de publicação — validação, geração com re-tentativa, texto colável

**Files:**
- Modify: `scripts/daily_reddit_meme_pipeline.py` (constantes junto a `CONCEPT_SCHEMA_VERSION` ~linha 66; funções novas logo após `extract_json_object`, ~linha 1117)
- Test: `tests/test_publish_package.py` (novo)

**Interfaces:**
- Produces: `publish_metadata_issues(payload: Any) -> list[str]`
- Produces: `normalize_publish_candidate(payload: Any) -> dict[str, Any]`
- Produces: `generate_publish_metadata(post: reddit.RedditPost, concept: dict[str, Any], publish_id: str, model: str, timeout: int, max_attempts: int = 3) -> dict[str, Any]` — retorna dict com `status: "approved"` (todos os campos do schema `publish`) ou `status: "failed"` (com `issues`).
- Produces: `render_publish_text(publish: dict[str, Any]) -> str`
- Produces: constantes `PUBLISH_TITLE_MAX_CHARS = 100`, `PUBLISH_TOPICS_RANGE = (3, 5)`, `PUBLISH_HASHTAGS_RANGE = (4, 8)`
- Consumes: `request_json`, `extract_json_object`, `OLLAMA_URL` (existentes)

- [ ] **Step 1: Write the failing tests**

Criar `tests/test_publish_package.py`:

```python
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `. .venv/bin/activate && python3 -m pytest tests/test_publish_package.py -v`
Expected: FAIL/ERROR com `AttributeError: ... has no attribute 'publish_metadata_issues'`

- [ ] **Step 3: Implement**

Em `scripts/daily_reddit_meme_pipeline.py`, junto a `CONCEPT_SCHEMA_VERSION` (linha ~66):

```python
PUBLISH_TITLE_MAX_CHARS = 100
PUBLISH_TOPICS_RANGE = (3, 5)
PUBLISH_HASHTAGS_RANGE = (4, 8)
```

Logo após `extract_json_object` (linha ~1117):

```python
def normalize_publish_candidate(payload: Any) -> dict[str, Any]:
    """Best-effort cleanup before validation: trim strings, prefix hashtags, dedupe."""

    if not isinstance(payload, dict):
        return {}
    candidate = dict(payload)
    for field in ("title", "description"):
        if isinstance(candidate.get(field), str):
            candidate[field] = candidate[field].strip()
    topics = candidate.get("interest_topics")
    if isinstance(topics, list):
        candidate["interest_topics"] = [str(item).strip() for item in topics if str(item).strip()]
    hashtags = candidate.get("hashtags")
    if isinstance(hashtags, list):
        seen: set[str] = set()
        cleaned: list[str] = []
        for item in hashtags:
            tag = str(item).strip().replace(" ", "")
            if not tag:
                continue
            if not tag.startswith("#"):
                tag = f"#{tag}"
            if tag.lower() in seen:
                continue
            seen.add(tag.lower())
            cleaned.append(tag)
        candidate["hashtags"] = cleaned
    return candidate


def publish_metadata_issues(payload: Any) -> list[str]:
    """Deterministic gate for model-produced publish metadata. Empty list means valid."""

    if not isinstance(payload, dict):
        return ["payload must be a JSON object"]
    issues: list[str] = []
    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        issues.append("title is required")
    elif len(title) > PUBLISH_TITLE_MAX_CHARS:
        issues.append(f"title exceeds {PUBLISH_TITLE_MAX_CHARS} chars")
    description = payload.get("description")
    if not isinstance(description, str) or not description.strip():
        issues.append("description is required")
    topics = payload.get("interest_topics")
    lo, hi = PUBLISH_TOPICS_RANGE
    if not isinstance(topics, list) or not all(isinstance(item, str) and item.strip() for item in topics):
        issues.append("interest_topics must be a list of non-empty strings")
    elif not lo <= len(topics) <= hi:
        issues.append(f"interest_topics must have {lo}-{hi} items")
    hashtags = payload.get("hashtags")
    lo, hi = PUBLISH_HASHTAGS_RANGE
    if not isinstance(hashtags, list) or not all(
        isinstance(item, str) and item.startswith("#") and len(item) > 1 for item in hashtags
    ):
        issues.append("hashtags must be a list of #-prefixed strings")
    elif not lo <= len(hashtags) <= hi:
        issues.append(f"hashtags must have {lo}-{hi} items")
    return issues


def compose_publish_prompt(post: reddit.RedditPost, concept: dict[str, Any]) -> str:
    return f"""
Voce cria metadados de publicacao para videos curtos de humor (YouTube Shorts, Instagram Reels, TikTok), em portugues do Brasil.

O video e uma piada narrada em 3 frases sobre uma foto real:
- Setup: {concept.get('top_text', '')}
- Escalada: {concept.get('middle_text', '')}
- Punchline: {concept.get('bottom_text', '')}
Contexto da foto: {concept.get('source_brief', '')}
Descricao visual: {concept.get('source_visual_description', '')}

Responda APENAS um objeto JSON neste formato:
{{
  "title": "gancho curto e curioso, maximo {PUBLISH_TITLE_MAX_CHARS} caracteres, sem clickbait enganoso",
  "description": "1 a 3 frases que despertam curiosidade sem entregar a punchline",
  "interest_topics": ["{PUBLISH_TOPICS_RANGE[0]} a {PUBLISH_TOPICS_RANGE[1]} assuntos em linguagem natural, ex: gatos, humor absurdo"],
  "hashtags": ["{PUBLISH_HASHTAGS_RANGE[0]} a {PUBLISH_HASHTAGS_RANGE[1]} hashtags misturando populares e especificas do tema"]
}}

Regras:
- Nao mencione IA, Reddit nem o processo de producao.
- Hashtags sem espacos.
""".strip()


def generate_publish_metadata(
    post: reddit.RedditPost,
    concept: dict[str, Any],
    publish_id: str,
    model: str,
    timeout: int,
    max_attempts: int = 3,
) -> dict[str, Any]:
    """Publish metadata via local Ollama. Off-schema output is rejected and retried; the
    final failure is recorded as status=failed — values are never fabricated."""

    prompt = compose_publish_prompt(post, concept)
    last_issues: list[str] = []
    for attempt in range(1, max_attempts + 1):
        payload = {
            "model": model,
            "stream": False,
            "messages": [
                {"role": "system", "content": "Voce escreve metadados de publicacao em pt-BR e responde apenas JSON."},
                {"role": "user", "content": prompt},
            ],
            "options": {"temperature": 0.4, "num_predict": 700},
        }
        try:
            data = request_json("POST", f"{OLLAMA_URL}/api/chat", json=payload, timeout=timeout)
            content = (data.get("message") or {}).get("content") or ""
            candidate = normalize_publish_candidate(extract_json_object(content))
            issues = publish_metadata_issues(candidate)
        except Exception as exc:  # noqa: BLE001 - each failed round-trip is one attempt
            issues = [str(exc)]
            candidate = {}
        if not issues:
            hashtags = list(candidate["hashtags"])
            return {
                "publish_id": publish_id,
                "language": "pt-BR",
                "title": candidate["title"],
                "description": candidate["description"],
                "description_with_hashtags": f"{candidate['description']}\n\n{' '.join(hashtags)}",
                "interest_topics": list(candidate["interest_topics"]),
                "hashtags": hashtags,
                "model": model,
                "status": "approved",
                "attempts": attempt,
            }
        last_issues = issues
    return {
        "publish_id": publish_id,
        "language": "pt-BR",
        "model": model,
        "status": "failed",
        "issues": last_issues,
        "attempts": max_attempts,
    }


def render_publish_text(publish: dict[str, Any]) -> str:
    """Paste-ready text block for manual publishing on the three platforms."""

    return "\n".join(
        [
            f"TITULO: {publish.get('title', '')}",
            "",
            "DESCRICAO:",
            str(publish.get("description", "")),
            "",
            "DESCRICAO COM HASHTAGS (TikTok/Reels):",
            str(publish.get("description_with_hashtags", "")),
            "",
            f"ASSUNTOS: {', '.join(publish.get('interest_topics') or [])}",
            f"HASHTAGS: {' '.join(publish.get('hashtags') or [])}",
            "",
            f"ID: {publish.get('publish_id', '')}",
        ]
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_publish_package.py -v`
Expected: PASS (todos)

Run: `python3 -m pytest tests/`
Expected: PASS (nenhuma regressão)

- [ ] **Step 5: Commit**

```bash
git add tests/test_publish_package.py scripts/daily_reddit_meme_pipeline.py
git commit -m "feat: publish metadata generation with deterministic validation and retry"
```

---

### Task 2: Contrato `concepts.json` v3 — seção `publish`, leitura compatível com v2

**Files:**
- Modify: `scripts/daily_reddit_meme_pipeline.py`:
  - `CONCEPT_SCHEMA_VERSION` (linha ~66)
  - `concept_document` (linha ~3592)
  - `validate_concepts_document` (linha ~3635)
  - `hydrate_concept_record` (linha ~3675)
- Test: `tests/test_publish_package.py` (adicionar classe)

**Interfaces:**
- Produces: `CONCEPT_SCHEMA_VERSION = 3`, `SUPPORTED_CONCEPT_SCHEMA_VERSIONS = frozenset({2, 3})`
- Produces: seção opcional `publish` no documento (dict; `concept_document` sempre emite, vazio se ausente); `hydrate_concept_record` repõe `concept["publish"]`
- Consumes: `generate_publish_metadata` shape da Task 1 (o dict retornado é gravado como está)

- [ ] **Step 1: Write the failing tests**

Adicionar em `tests/test_publish_package.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_publish_package.py::ContractTests -v`
Expected: FAIL (`CONCEPT_SCHEMA_VERSION` ainda é 2; `publish` ausente)

- [ ] **Step 3: Implement**

Em `scripts/daily_reddit_meme_pipeline.py`:

Linha ~66, trocar:

```python
CONCEPT_SCHEMA_VERSION = 3
SUPPORTED_CONCEPT_SCHEMA_VERSIONS = frozenset({2, 3})
```

Em `concept_document`, após a chave `"artifacts": {...}` (linha ~3627), adicionar:

```python
        "publish": deepcopy(concept.get("publish") or {}),
```

Em `validate_concepts_document`, trocar a checagem de versão (linha ~3644):

```python
        if item.get("schema_version") not in SUPPORTED_CONCEPT_SCHEMA_VERSIONS:
            errors.append(
                f"{prefix}.schema_version must be one of {sorted(SUPPORTED_CONCEPT_SCHEMA_VERSIONS)}"
            )
```

E após o loop das seções obrigatórias (linha ~3648), adicionar:

```python
        if "publish" in item and not isinstance(item.get("publish"), dict):
            errors.append(f"{prefix}.publish must be an object when present")
```

Em `hydrate_concept_record`, dentro do dict `concept = {...}` (após `"execution": execution,`, linha ~3703), adicionar:

```python
        "publish": deepcopy(record.get("publish") or {}),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/ -v`
Expected: PASS (incluindo os testes existentes de `test_configuration.py` — se algum fixar `schema_version == 2`, atualizar esse teste para 3 faz parte deste passo)

- [ ] **Step 5: Commit**

```bash
git add tests/test_publish_package.py scripts/daily_reddit_meme_pipeline.py tests/test_configuration.py
git commit -m "feat: concepts.json schema v3 with optional publish section (reads v2)"
```

---

### Task 3: `format_video_916` — formatação ffmpeg blur-pad 1080×1920

**Files:**
- Modify: `scripts/daily_reddit_meme_pipeline.py` (após `finish_ltx23_with_tts`, linha ~3168)
- Test: `tests/test_publish_package.py` (adicionar classe)

**Interfaces:**
- Produces: `format_video_916(input_path: Path, output_path: Path) -> Path`
- Consumes: `run_ffmpeg(args: list[str]) -> None` (existente, linha ~2624)

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_publish_package.py::Format916Tests -v`
Expected: FAIL com `AttributeError: ... 'format_video_916'`

- [ ] **Step 3: Implement**

Após `finish_ltx23_with_tts` (linha ~3168):

```python
def format_video_916(input_path: Path, output_path: Path) -> Path:
    """Fit any validated render into a 1080x1920 canvas for Shorts/Reels/TikTok: blurred
    cover of the clip as background, the clip itself contained and centered on top. One
    graph handles portrait and landscape sources alike; audio is copied untouched."""

    filter_complex = (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,boxblur=luma_radius=24:luma_power=2[bg];"
        "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2[vout]"
    )
    run_ffmpeg(
        [
            "-i", str(input_path),
            "-filter_complex", filter_complex,
            "-map", "[vout]", "-map", "0:a?",
            "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(output_path),
        ]
    )
    return output_path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_publish_package.py -v`
Expected: PASS

Verificação real (opcional mas recomendada se houver qualquer MP4 local de run anterior em `data/media-pipeline/`):

```bash
python3 - <<'EOF'
import sys
from pathlib import Path
sys.path.insert(0, "scripts")
import daily_reddit_meme_pipeline as pipeline
src = sorted(Path("data/media-pipeline").rglob("*.mp4"))
if src:
    out = Path("/tmp/claude-916-check.mp4")
    pipeline.format_video_916(src[0], out)
    print("ok:", out, out.stat().st_size, "bytes")
else:
    print("sem mp4 local; pulei a verificacao real")
EOF
```
Expected: `ok: /tmp/claude-916-check.mp4 <n> bytes` e, inspecionando com `ffprobe`, 1080x1920.

- [ ] **Step 5: Commit**

```bash
git add tests/test_publish_package.py scripts/daily_reddit_meme_pipeline.py
git commit -m "feat: 9:16 blur-pad formatting step (1080x1920) for publish packages"
```

---

### Task 4: Curadoria prioriza fotos retrato no backlog

**Files:**
- Modify: `scripts/reddit_popular_curation.py` (entrada aprovada, linha ~170; ordenação no save, linha ~185)
- Test: `tests/test_publish_package.py` (adicionar classe)

**Interfaces:**
- Produces: `prioritize_portrait(approved: list[dict]) -> list[dict]` em `reddit_popular_curation.py`; campo novo `"portrait": bool` nas entradas aprovadas do backlog
- Consumes: nada das tasks anteriores

- [ ] **Step 1: Write the failing test**

Adicionar em `tests/test_publish_package.py` (o import de `reddit_popular_curation as curation` segue o padrão do header — adicioná-lo ao topo do arquivo):

```python
import reddit_popular_curation as curation  # noqa: E402  (junto aos imports do topo)


class CurationPortraitTests(unittest.TestCase):
    def test_portrait_entries_come_first_stably(self) -> None:
        entries = [
            {"post": {"id": "a"}, "portrait": False},
            {"post": {"id": "b"}, "portrait": True},
            {"post": {"id": "c"}, "portrait": False},
            {"post": {"id": "d"}, "portrait": True},
            {"post": {"id": "e"}},  # entrada antiga sem o campo: trata como paisagem
        ]
        ordered = curation.prioritize_portrait(entries)
        self.assertEqual([item["post"]["id"] for item in ordered], ["b", "d", "a", "c", "e"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_publish_package.py::CurationPortraitTests -v`
Expected: FAIL com `AttributeError: ... 'prioritize_portrait'`

- [ ] **Step 3: Implement**

Em `scripts/reddit_popular_curation.py`, antes de `main()`:

```python
def prioritize_portrait(approved: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Portrait sources first (they fill the 9:16 canvas without blur-pad), preserving
    curation order within each group. Soft priority only — landscape stays eligible."""

    return sorted(approved, key=lambda item: 0 if item.get("portrait") else 1)
```

Na entrada aprovada (dict da linha ~170), adicionar após `"media_resolution": [width, height],`:

```python
                    "portrait": height > width,
```

No save dentro do loop (linha ~185), trocar `backlog["approved"] = approved` por:

```python
        backlog["approved"] = prioritize_portrait(approved)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_publish_package.py scripts/reddit_popular_curation.py
git commit -m "feat: curation backlog prioritizes portrait sources for 9:16 output"
```

---

### Task 5: Wiring no pipeline — flag `--publish-model`, geração pré-render, pacote por vídeo, 9:16 pós-render

**Files:**
- Modify: `scripts/daily_reddit_meme_pipeline.py`:
  - `build_parser()` — junto às flags `--humor-model`/`--humor-critic-model`
  - `main()` — imediatamente antes de `persist_concepts(run_dir / "concepts.json", posts, concepts)` (linha ~4289)
  - `main()` — bloco de flush (linha ~4290) e loop de render após `concept["artifact_metadata"] = probe_video_artifact(video_output_path)` (linha ~4372)
- Test: `tests/test_publish_package.py` (adicionar classe)

**Interfaces:**
- Consumes: `generate_publish_metadata`, `render_publish_text` (Task 1); `format_video_916` (Task 3); contrato `publish` (Task 2)
- Produces: flag `--publish-model` (default `None` → usa `args.humor_model`); paths `concept["publish_json_path"]`, `concept["publish_text_path"]`, `concept["final_916_path"]` (sufixo `_path` entra automaticamente em `artifacts.paths` via `concept_document`); diretório de pacote `run_dir / f"{idx:02d}-{slug}"`.

- [ ] **Step 1: Write the failing tests**

```python
class PipelineWiringTests(unittest.TestCase):
    def test_publish_model_flag_defaults_to_none(self) -> None:
        args = pipeline.build_parser().parse_args([])
        self.assertIsNone(args.publish_model)
        args = pipeline.build_parser().parse_args(["--publish-model", "qwen3:14b"])
        self.assertEqual(args.publish_model, "qwen3:14b")

    def test_prepare_publish_package_writes_json_and_txt(self) -> None:
        import json
        import tempfile

        publish = {
            "publish_id": "runtag-01",
            "language": "pt-BR",
            "title": "Titulo",
            "description": "Desc.",
            "description_with_hashtags": "Desc.\n\n#a #b #c #d",
            "interest_topics": ["a", "b", "c"],
            "hashtags": ["#a", "#b", "#c", "#d"],
            "model": "m",
            "status": "approved",
            "attempts": 1,
        }
        with tempfile.TemporaryDirectory() as tmp:
            concept = make_concept(publish=publish)
            package_dir = pipeline.prepare_publish_package(Path(tmp), 1, "gato-no-mapa", concept)
            self.assertEqual(package_dir, Path(tmp) / "01-gato-no-mapa")
            data = json.loads((package_dir / "publish.json").read_text(encoding="utf-8"))
            self.assertEqual(data["publish_id"], "runtag-01")
            text = (package_dir / "publish.txt").read_text(encoding="utf-8")
            self.assertIn("Titulo", text)
            self.assertEqual(concept["publish_json_path"], str(package_dir / "publish.json"))
            self.assertEqual(concept["publish_text_path"], str(package_dir / "publish.txt"))

    def test_prepare_publish_package_skips_failed_publish(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            concept = make_concept(publish={"status": "failed", "publish_id": "x", "issues": ["bad"]})
            package_dir = pipeline.prepare_publish_package(Path(tmp), 1, "slug", concept)
            self.assertIsNone(package_dir)
            self.assertFalse((Path(tmp) / "01-slug").exists())
            self.assertNotIn("publish_json_path", concept)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_publish_package.py::PipelineWiringTests -v`
Expected: FAIL (`publish_model` inexistente; `prepare_publish_package` inexistente)

- [ ] **Step 3: Implement**

Flag em `build_parser()`, junto a `--humor-model`:

```python
    parser.add_argument(
        "--publish-model",
        default=None,
        help="Ollama model for publish metadata (title/description/topics/hashtags); defaults to --humor-model.",
    )
```

Helper após `render_publish_text` (Task 1):

```python
def prepare_publish_package(run_dir: Path, index: int, slug: str, concept: dict[str, Any]) -> Path | None:
    """Write publish.json + publish.txt into the per-video package directory. Returns the
    directory, or None when the concept has no approved publish metadata."""

    publish = concept.get("publish") if isinstance(concept.get("publish"), dict) else {}
    if publish.get("status") != "approved":
        return None
    package_dir = run_dir / f"{index:02d}-{slug}"
    package_dir.mkdir(parents=True, exist_ok=True)
    publish_json_path = package_dir / "publish.json"
    write_json(publish_json_path, publish)
    publish_text_path = package_dir / "publish.txt"
    publish_text_path.write_text(render_publish_text(publish), encoding="utf-8")
    concept["publish_json_path"] = str(publish_json_path)
    concept["publish_text_path"] = str(publish_text_path)
    return package_dir
```

Em `main()`, imediatamente ANTES de `persist_concepts(run_dir / "concepts.json", posts, concepts)` (linha ~4289):

```python
    publish_model = args.publish_model or args.humor_model
    for idx, (post, concept) in enumerate(zip(posts, concepts), 1):
        if not (concept.get("humor_approved") and concept.get("quality_approved")):
            continue
        existing = concept.get("publish") if isinstance(concept.get("publish"), dict) else {}
        if existing.get("status") == "approved":
            prepare_publish_package(run_dir, idx, slugify(post.title), concept)
            continue
        print(f"Generating publish metadata {idx}/{len(posts)}: {post.title[:60]}")
        concept["publish"] = generate_publish_metadata(
            post, concept, f"{run_tag}-{idx:02d}", publish_model, args.concept_timeout
        )
        if concept["publish"]["status"] == "approved":
            prepare_publish_package(run_dir, idx, slugify(post.title), concept)
        else:
            issues = "; ".join(concept["publish"].get("issues") or [])
            print(f"WARN publish metadata failed (render continues): {issues[:200]}")
```

No bloco de flush (linha ~4290, dentro de `if not approved_resume:`), adicionar após o flush de `args.vision_model`:

```python
        if publish_model not in {
            args.ollama_model, args.humor_model, args.humor_critic_model,
            args.humor_second_critic_model, args.vision_model,
        }:
            flush_ollama(publish_model)
```

No loop de render, logo após `concept["artifact_metadata"] = probe_video_artifact(video_output_path)` (linha ~4372):

```python
                    publish = concept.get("publish") if isinstance(concept.get("publish"), dict) else {}
                    if publish.get("status") == "approved":
                        package_dir = run_dir / f"{idx:02d}-{slug}"
                        package_dir.mkdir(parents=True, exist_ok=True)
                        try:
                            final_916_path = format_video_916(video_output_path, package_dir / "final_916.mp4")
                            concept["final_916_path"] = str(final_916_path)
                        except Exception as exc:  # noqa: BLE001 - native validated MP4 stays the deliverable
                            print(f"WARN 9:16 formatting failed; keeping native MP4: {exc}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/ -v`
Expected: PASS

Smoke de importabilidade e dry-run sem rede/modelos:

```bash
python3 -c "import sys; sys.path.insert(0, 'scripts'); import daily_reddit_meme_pipeline; print('import ok')"
```
Expected: `import ok`

- [ ] **Step 5: Commit**

```bash
git add tests/test_publish_package.py scripts/daily_reddit_meme_pipeline.py
git commit -m "feat: wire publish package generation and 9:16 output into the pipeline"
```

---

### Task 6: Telegram — envio de vídeo com caption colável

**Files:**
- Modify: `scripts/daily_reddit_meme_pipeline.py`:
  - nova função após `send_telegram_album` (linha ~3564)
  - `main()` — bloco Telegram (linha ~4388)
- Test: `tests/test_publish_package.py` (adicionar classe)

**Interfaces:**
- Produces: `send_telegram_videos(entries: list[tuple[Path, str]]) -> None` — envia cada `final_916.mp4` via `sendVideo` com caption truncada em 1024 chars
- Consumes: `concept["final_916_path"]` e `concept["publish"]` (Task 5)

- [ ] **Step 1: Write the failing test**

```python
class TelegramPublishTests(unittest.TestCase):
    def test_send_telegram_videos_truncates_caption_at_1024(self) -> None:
        import os
        import tempfile

        calls = []

        class FakeResponse:
            def raise_for_status(self) -> None:
                pass

        def fake_post(url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse()

        with tempfile.NamedTemporaryFile(suffix=".mp4") as fh:
            entries = [(Path(fh.name), "T" * 2000)]
            with patch.dict(
                os.environ, {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "123"}
            ), patch.object(pipeline.requests, "post", side_effect=fake_post):
                pipeline.send_telegram_videos(entries)
        self.assertEqual(len(calls), 1)
        url, kwargs = calls[0]
        self.assertIn("sendVideo", url)
        self.assertEqual(kwargs["data"]["chat_id"], "123")
        self.assertEqual(len(kwargs["data"]["caption"]), 1024)
        self.assertIn("video", kwargs["files"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_publish_package.py::TelegramPublishTests -v`
Expected: FAIL com `AttributeError: ... 'send_telegram_videos'`

- [ ] **Step 3: Implement**

Após `send_telegram_album` (linha ~3564):

```python
def send_telegram_videos(entries: list[tuple[Path, str]]) -> None:
    """Send publish-ready 9:16 videos, one message each, caption = paste-ready text."""

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    raw_users = os.environ.get("TELEGRAM_ALLOWED_USERS", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", raw_users.split(",")[0].strip() if raw_users else "").strip()
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID/TELEGRAM_ALLOWED_USERS are required")
    for path, caption in entries:
        with path.open("rb") as handle:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendVideo",
                data={"chat_id": chat_id, "caption": caption[:1024], "supports_streaming": True},
                files={"video": (path.name, handle, "video/mp4")},
                timeout=300,
            )
        response.raise_for_status()
```

Em `main()`, no bloco Telegram (substituir o trecho da linha ~4388 mantendo o elif final):

```python
    publish_videos: list[tuple[Path, str]] = []
    for concept in concepts:
        publish = concept.get("publish") if isinstance(concept.get("publish"), dict) else {}
        final_916 = str(concept.get("final_916_path") or "")
        if publish.get("status") == "approved" and final_916 and Path(final_916).is_file():
            caption = f"{publish.get('title', '')}\n\n{publish.get('description_with_hashtags', '')}"
            publish_videos.append((Path(final_916), caption))

    if args.telegram:
        if final_paths:
            send_telegram_album(final_paths, summary)
            print(f"Telegram sent: {len(final_paths)} images")
        if publish_videos:
            send_telegram_videos(publish_videos)
            print(f"Telegram sent: {len(publish_videos)} publish videos")
    else:
        print("Telegram disabled by default; not sending. Use --telegram to opt in.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_publish_package.py scripts/daily_reddit_meme_pipeline.py
git commit -m "feat: telegram delivery of 9:16 publish videos with paste-ready caption"
```

---

### Task 7: Docs, gate público e fechamento

**Files:**
- Modify: `CHANGELOG.md` (seção `[Unreleased]` → `Added`/`Changed`)
- Modify: `docs/roadmap.md` (tabela "Estado atual" + item novo em "O que avançamos")
- Modify: `docs/architecture.md` (uma frase no Data Flow: passo de formatação 9:16 + pacote de publicação)

**Interfaces:**
- Consumes: tudo das Tasks 1–6 (documenta o comportamento novo)

- [ ] **Step 1: CHANGELOG**

Adicionar em `[Unreleased] > Added`:

```markdown
- Pacote de publicação Fase 1: seção `publish` no contrato de `concepts.json` (v3; leitura
  aceita v2) gerada por modelo local (`--publish-model`, default = `--humor-model`) com
  validação determinística e até 3 tentativas — título ≤100 chars, 3–5 assuntos, 4–8
  hashtags, pt-BR; falha nunca bloqueia o render. Por vídeo aprovado: diretório
  `NN-slug/` com `publish.json`, `publish.txt` (colável) e `final_916.mp4` (1080×1920,
  blur-pad via ffmpeg pós-validação — o MP4 nativo validado permanece intacto). Telegram
  (`--telegram`) envia o vídeo 9:16 com caption colável (truncada em 1024). Curadoria do
  r/popular prioriza fotos retrato no backlog (prioridade branda, paisagem segue elegível).
```

- [ ] **Step 2: roadmap + architecture**

`docs/roadmap.md`: na tabela "Estado atual", adicionar linha:

```markdown
| 6. Pacote de publicação (Fase 1) | **Implementado.** `publish` no contrato v3, `final_916.mp4` blur-pad, curadoria prioriza retrato, Telegram com caption colável. Fases 2–3 (métricas e loop de feedback) não iniciadas. |
```

E em "O que avançamos, em ordem", item novo ao final descrevendo a Fase 1 e a referência ao spec (`docs/superpowers/specs/2026-07-19-publish-package-phase1-design.md`).

`docs/architecture.md`: no Data Flow, após o passo 8, adicionar:

```markdown
9. Approved videos also get a publish package: locally generated pt-BR title/description/
   interest topics/hashtags (`publish.json`/`publish.txt`) and a 1080×1920 blur-padded
   `final_916.mp4` for Shorts/Reels/TikTok; the validated native MP4 is kept unchanged.
```

- [ ] **Step 3: Gate público e suite completa**

```bash
python3 -m pytest tests/
./scripts/check_public_ready.sh
```
Expected: tudo PASS / `OK: public-readiness checks passed`

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md docs/roadmap.md docs/architecture.md
git commit -m "docs: changelog/roadmap/architecture for publish package phase 1"
```

---

## Self-Review (executado na escrita do plano)

- **Cobertura do spec:** metadados+validação+re-tentativa (T1), contrato v3+compat v2 (T2), 9:16 blur-pad (T3), curadoria retrato (T4), wiring+pacote+`--publish-model`+`publish_id` (T5), Telegram caption 1024 (T6), docs (T7). `publish_id` estável entre replays: coberto pelo skip de regeneração quando `status == "approved"` (T5) + persistência (T2).
- **Placeholders:** nenhum; todo passo tem código/comando completo.
- **Consistência de tipos:** `generate_publish_metadata` retorna o dict gravado como `concept["publish"]` e lido por `prepare_publish_package`/Telegram; paths `*_path` fluem para `artifacts.paths` automaticamente via `concept_document` (comportamento existente, linha 3595).
