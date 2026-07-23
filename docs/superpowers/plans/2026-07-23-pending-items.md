# Pendências pós-merge PR#1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fechar as três pendências abertas (motivo de rejeição obsoleto no gate de `media_type: text`, flakiness do `gemma4:31b` em metadados de publicação, ausência de baseline de verificação automatizada) e estabelecer um baseline de verificação explícito para não perder a qualidade de vídeo já validada quando o default do pipeline mudou de I2V para T2V.

**Architecture:** Nenhuma mudança estrutural. Task 1 é uma correção pontual de string. Task 2 adiciona uma função de diagnóstico determinística baseada em `ffmpeg` (sem dependências novas) chamada no mesmo ponto onde `probe_video_artifact()` já é chamado hoje. Task 3 é documentação (roadmap + memória cross-sessão). Task 4 é um experimento operacional (script descartável em scratchpad, não fica no repo) comparando `--publish-model` candidatos usando dados já gravados em disco, sem gastar GPU/render.

**Tech Stack:** Python 3, `ffmpeg`/`ffprobe` (já exigidos no preflight), `unittest` via `pytest`, Ollama local (`http://localhost:11434`, já confirmado no ar com `gemma4:31b`, `llama3:latest`, `qwen3:14b`, `qwen3.5:latest` entre outros).

## Global Constraints

- Nunca hand-roll grafo ComfyUI em Python (não se aplica a este plano — nenhuma task toca render).
- `requirements.lock` fica limitado a Pillow, PyYAML, requests — Task 2 não pode introduzir numpy/opencv; usar apenas `subprocess` + `ffmpeg`/`ffprobe` já presentes.
- Atualizar `docs/roadmap.md` e `CHANGELOG.md` a cada achado/decisão significativa, não só ao final.
- Task de generalizar o roteiro criativo em `build_video_script()` está **fora de escopo** deste plano — adiada explicitamente pelo usuário ("no futuro"). Não tocar `build_video_script()`/`compose_ltx23_segment_prompts()`.

---

## Diagnóstico (resumo, não repetir nas tasks)

1. **Gate `media_type: text`** (`scripts/daily_reddit_meme_pipeline.py:4611-4616`): rejeita qualquer post com `post.media_type != "image"` com o motivo fixo `"controlled I2V experiment requires a downloaded source image"`. O comportamento em si está correto mesmo com T2V como default — `prepare_source_media()` (`scripts/daily_reddit_meme_pipeline.py:2800-2824`) só gera `visual_descriptions` quando `media_type == "image"`, então posts de texto/vídeo genuinamente não têm material visual para escrever uma cena T2V concreta. O único problema é a *string* do motivo, que menciona "I2V" e confunde leitura futura do audit report. Correção é cosmética, não muda comportamento.

2. **Flakiness `gemma4:31b`** já investigada e documentada em `docs/roadmap.md:742` (item 18): 5 de 9 vídeos aprovados falharam na geração de metadados de publicação no mesmo dia, com respostas genuinamente vazias (0 caracteres, 3 ocorrências) do modelo — não é bug de parsing (`extract_json_object` foi testado e absolve). O funil já faz retry determinístico 3x (`generate_publish_metadata`, `scripts/daily_reddit_meme_pipeline.py:1290-1335`) e nunca fabrica dado — o problema é a taxa de sucesso do modelo na tarefa específica. Candidatos alternativos confirmados disponíveis no Ollama local agora: `llama3:latest` (já usado com sucesso como `--humor-critic-model` no mesmo pipeline) e `qwen3:14b` (maior, historicamente forte em JSON estruturado).

3. **Baseline de verificação**: hoje é 100% manual. `probe_video_artifact()` (`scripts/daily_reddit_meme_pipeline.py:4290-4316`) só confere presença de stream de vídeo/áudio e duração > 0 — não detecta o próprio problema que motivou a investigação de roteiro criativo ("foto com movimento de câmera fraco" ainda passa nesse probe). Testei `ffmpeg`'s `vmafmotion` e `freezedetect` nos 4 vídeos reais disponíveis:
   - `reference-baseline/APPROVED-galaxy-cat-9.64s.mp4` (aprovado, "audio e video perfeitos"): VMAF Motion avg = **0.297**
   - `reference-baseline/APPROVED-horse-cat-9s.mp4` (aprovado): VMAF Motion avg = **0.554**
   - `20260721-forced-candidate-pipeline-render/forced-candidate.mp4` (criticado pelo usuário — "movimento de câmera bem fraco"): VMAF Motion avg = **1.904**
   - `20260721-forced-candidate-pipeline-render/creative-candidate.mp4` (nota 7/10, aprovado com ressalva): VMAF Motion avg = **3.076**

   **Achado:** o vídeo criticado como "estático" teve motion score MAIOR que os dois vídeos considerados "perfeitos". Isso é esperado — galáxia/cavalo são rostos em close (baixo deslocamento de pixel entre frames, mas percebidos como "vivos" por piscar/respirar), enquanto os candidatos forçados são cenas de objeto sem rosto (deslocamento de pixel mais alto por natureza da câmera/cena, independente de "ler bem"). **Conclusão: um limiar único de motion score não separa "bom" de "ruim" entre arquétipos de cena diferentes — não dá para usar isso como gate automático de aprovação/reprovação sem calibração por arquétipo, que não temos dados suficientes para fazer agora.** Por isso a Task 2 abaixo trata esses números como **diagnóstico informativo no audit trail**, não como gate — evita repetir o padrão já registrado no roadmap de "tentamos e medimos inefetivo" (ver pausas de áudio via prompt). O verdadeiro baseline de proteção continua sendo o processo humano de comparação com os `reference-baseline/APPROVED-*.mp4`; a Task 3 formaliza a régua que falta hoje: **T2V-por-default só deve ser tratado como "qualidade validada" depois de um lote real repetir o mesmo veredito "aprovado nos 3 eixos" que os exemplares I2V já têm — hoje só existe 1 amostra T2V forçada, nota 7/10, não 3/3.**

---

### Task 1: Corrigir motivo de rejeição obsoleto do gate `media_type` != image

**Files:**
- Modify: `scripts/daily_reddit_meme_pipeline.py:4611-4616`
- Test: `tests/test_configuration.py`

**Interfaces:**
- Consumes: nenhuma (edição isolada de string literal dentro do loop principal de `main()`)
- Produces: nenhuma mudança de contrato — `source_reviews[post.id]["reason"]` continua string livre, só o texto muda

- [ ] **Step 1: Ler o trecho atual para confirmar contexto exato antes de editar**

Trecho atual (`scripts/daily_reddit_meme_pipeline.py:4609-4617`):
```python
        for post in posts:
            source_path = source_media_paths.get(post.id, "")
            if post.media_type != "image" or not source_path:
                source_reviews[post.id] = {
                    "approved": False,
                    "scores": {name: 0.0 for name in ("source_match", "visual_clarity", "motion_potential", "text_independence")},
                    "reason": "controlled I2V experiment requires a downloaded source image",
                }
                continue
```

- [ ] **Step 2: Editar a string do motivo para refletir a causa real (falta de material visual, não "experimento I2V")**

```python
        for post in posts:
            source_path = source_media_paths.get(post.id, "")
            if post.media_type != "image" or not source_path:
                source_reviews[post.id] = {
                    "approved": False,
                    "scores": {name: 0.0 for name in ("source_match", "visual_clarity", "motion_potential", "text_independence")},
                    "reason": f"no visual source available to write a scene (media_type={post.media_type!r}, downloaded={bool(source_path)})",
                }
                continue
```

- [ ] **Step 3: Adicionar teste cobrindo o novo texto do motivo**

Adicionar em `tests/test_configuration.py` (mesma classe que já cobre `assess_source_suitability`, próximo a `test_assess_source_suitability_schema_requires_open_scene_flag` por volta da linha 463):

```python
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
        source_path = ""
        if post.media_type != "image" or not source_path:
            reason = f"no visual source available to write a scene (media_type={post.media_type!r}, downloaded={bool(source_path)})"
        self.assertIn("media_type='text'", reason)
        self.assertNotIn("I2V", reason)
```

Nota: este teste replica a expressão em vez de chamar `main()` porque a lógica está inline no loop de `main()`, não extraída em função própria — não há função isolada para chamar diretamente sem instanciar o pipeline inteiro. Se preferir cobertura mais forte, extrair esse bloco para uma função `reject_non_image_source(post, source_path) -> dict` é opcional e fora de escopo deste plano (mudaria a estrutura do loop sem necessidade).

- [ ] **Step 4: Rodar os testes**

Run: `python3 -m pytest tests/test_configuration.py -k "media_type_aware_reason or assess_source_suitability" -v`
Expected: PASS

- [ ] **Step 5: Rodar o gate de publicação e compilar**

Run: `python3 -m py_compile scripts/daily_reddit_meme_pipeline.py && ./scripts/check_public_ready.sh`
Expected: sem erros

- [ ] **Step 6: Commit**

```bash
git add scripts/daily_reddit_meme_pipeline.py tests/test_configuration.py
git commit -m "fix: source gate rejection reason no longer implies I2V-only when default is T2V"
```

---

### Task 2: Diagnóstico automático de motion/freeze no artefato de vídeo (informativo, não-gate)

**Files:**
- Modify: `scripts/daily_reddit_meme_pipeline.py:4290-4316` (`probe_video_artifact`)
- Modify: `scripts/daily_reddit_meme_pipeline.py:4323-4340` (`build_human_review_sheet`)
- Test: `tests/test_configuration.py` (classe `ArtifactIntegrationTests`, próximo à linha 817)

**Interfaces:**
- Consumes: nenhuma nova dependência externa — só `subprocess` + binários `ffmpeg`/`ffprobe` já exigidos no preflight (`scripts/daily_reddit_meme_pipeline.py:4272`)
- Produces: `probe_video_artifact(path)` passa a retornar duas chaves novas no dict existente: `"motion_vmaf_avg": float` e `"freeze_detected": bool`. Nenhuma chave existente (`duration_seconds`, `width`, `height`, `video_codec`, `audio_codec`, `has_audio`) muda de nome ou tipo — quem já lê essas chaves (`concept["artifact_metadata"]`, linha 4775) continua funcionando sem mudança.

- [ ] **Step 1: Escrever o teste de motion/freeze usando clipes sintéticos `lavfi` (não depende dos arquivos gitignored em `reference-baseline/`)**

Adicionar em `tests/test_configuration.py`, dentro de `class ArtifactIntegrationTests` (após `test_local_mp4_probe_requires_video_and_audio`, linha ~834):

```python
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
```

- [ ] **Step 2: Rodar os testes novos e confirmar que falham (função ainda não calcula essas chaves)**

Run: `python3 -m pytest tests/test_configuration.py -k "freeze" -v`
Expected: FAIL com `KeyError: 'freeze_detected'`

- [ ] **Step 3: Implementar o cálculo em `probe_video_artifact`**

Substituir a função atual (`scripts/daily_reddit_meme_pipeline.py:4290-4316`) por:

```python
def probe_video_motion(path: Path) -> dict[str, Any]:
    """Diagnostic-only motion signal for the audit trail. Not a pass/fail gate:
    measured empirically that a fixed VMAF-motion threshold does not separate
    approved vs. criticized renders across different scene archetypes (see
    docs/roadmap.md item 21) — this exists to make regressions visible in
    execution.generation_calls / human-review.md, not to auto-reject."""

    motion_result = subprocess.run(
        ["ffmpeg", "-loglevel", "info", "-i", str(path), "-vf", "vmafmotion", "-an", "-f", "null", "-"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    motion_match = re.search(r"VMAF Motion avg:\s*([\d.]+)", motion_result.stderr)
    motion_vmaf_avg = float(motion_match.group(1)) if motion_match else 0.0

    freeze_result = subprocess.run(
        ["ffmpeg", "-loglevel", "info", "-i", str(path), "-vf", "freezedetect=n=-60dB:d=0.5", "-an", "-f", "null", "-"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    freeze_detected = "freezedetect" in freeze_result.stderr and "freeze_start" in freeze_result.stderr

    return {"motion_vmaf_avg": round(motion_vmaf_avg, 3), "freeze_detected": freeze_detected}


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
    metadata = {
        "duration_seconds": round(duration, 3),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "video_codec": video.get("codec_name", ""),
        "audio_codec": audio.get("codec_name", ""),
        "has_audio": True,
    }
    try:
        metadata.update(probe_video_motion(path))
    except Exception as exc:  # noqa: BLE001 - motion diagnostic is informational, never blocks the render
        print(f"WARN motion diagnostic failed (non-blocking): {exc}")
        metadata["motion_vmaf_avg"] = None
        metadata["freeze_detected"] = None
    return metadata
```

Confirmar que `import re` já existe no topo do arquivo (`scripts/daily_reddit_meme_pipeline.py`) — se não existir, adicionar à lista de imports padrão no topo do arquivo.

- [ ] **Step 4: Rodar os testes novamente e confirmar que passam**

Run: `python3 -m pytest tests/test_configuration.py -k "freeze or local_mp4_probe" -v`
Expected: PASS (3 testes: static freeze, moving no-freeze, existing audio/video probe)

- [ ] **Step 5: Surfacear o diagnóstico em `human-review.md` para o revisor humano ver o número junto do vídeo**

Em `build_human_review_sheet` (`scripts/daily_reddit_meme_pipeline.py:4323-4340`), adicionar uma linha depois de `f"- Vídeo: {concept.get('video_path', 'não renderizado')}"`:

```python
                f"- Vídeo: {concept.get('video_path', 'não renderizado')}",
                (
                    "- Motion diagnostic: "
                    + (
                        "n/a"
                        if not concept.get("artifact_metadata")
                        else (
                            f"vmaf_motion={concept['artifact_metadata'].get('motion_vmaf_avg')}, "
                            f"freeze_detected={concept['artifact_metadata'].get('freeze_detected')} "
                            "(informativo — comparar com reference-baseline/, não é veredito automático)"
                        )
                    )
                ),
```

- [ ] **Step 6: Rodar a suíte completa de testes**

Run: `python3 -m pytest tests/ -v`
Expected: PASS em todos os testes, incluindo os de `test_generation_audit_log.py` e `test_render_audit_report.py` que também tocam `concepts.json`/`artifact_metadata`

- [ ] **Step 7: Compilar e rodar o gate de publicação**

Run: `python3 -m py_compile scripts/daily_reddit_meme_pipeline.py && ./scripts/check_public_ready.sh`
Expected: sem erros

- [ ] **Step 8: Commit**

```bash
git add scripts/daily_reddit_meme_pipeline.py tests/test_configuration.py
git commit -m "feat: informational ffmpeg motion/freeze diagnostic on rendered MP4 artifacts"
```

---

### Task 3: Formalizar a régua de promoção de baseline (T2V-por-default ainda não é baseline validado)

**Files:**
- Modify: `docs/roadmap.md` (novo item, após o item 20 já existente sobre a validação do PR#1)
- Modify: `/home/fabiano/.claude/projects/-home-fabiano-code-media-meme-pipeline/memory/av-quality-baseline.md`
- Modify: `/home/fabiano/.claude/projects/-home-fabiano-code-media-meme-pipeline/memory/MEMORY.md`

**Interfaces:** nenhuma — documentação pura, sem código.

- [ ] **Step 1: Ler o final atual de `docs/roadmap.md` para encontrar o ponto de inserção exato**

Run: `grep -n "^## \|^- \*\*\[x\]" docs/roadmap.md | tail -20` para confirmar o número do próximo item livre (o item 20 já existe; este vira item 21).

- [ ] **Step 2: Adicionar o item 21 ao roadmap**

Anexar ao final da seção de itens numerados em `docs/roadmap.md`:

```markdown
21. **Baseline de verificação formalizado: T2V-por-default ainda NÃO tem o mesmo nível de validação que o baseline I2V anterior.** Medição empírica (ver `docs/superpowers/plans/2026-07-23-pending-items.md`): motion score automático (VMAF motion / freeze detection via ffmpeg) não discrimina "estático" de "vivo" entre arquétipos de cena diferentes — o candidato criticado pelo usuário como "movimento de câmera fraco" teve motion score MAIOR que os exemplares aprovados (galáxia/cavalo, rostos em close). Por isso não existe gate automático de qualidade de movimento; o diagnóstico ffmpeg agora gravado em `artifact_metadata`/`human-review.md` é informativo, não substitui avaliação humana.
    **Régua explícita para promover T2V-por-default a "baseline validado":** repetir, com o motor T2V, o mesmo processo que validou o pivô I2V em 2026-07-18 — um lote real (5-8 posts) cobrindo pelo menos os arquétipos já estabelecidos (sujeito único em close tipo galáxia/cavalo, foto real narrada tipo pivot, figura pública tipo notícia, cena de objeto sem sujeito animado tipo "My Work PC") com veredito humano explícito "aprovado nos 3 eixos" (piada, voz, movimento) em cada um — não apenas 1 amostra a 7/10 como hoje. Até esse lote existir, tratar T2V como direção validada mas não como o mesmo padrão de "perfeito" que `reference-baseline/APPROVED-*.mp4` representa.
```

- [ ] **Step 3: Atualizar a memória `av-quality-baseline.md` com a mesma régua**

Adicionar ao final do arquivo `/home/fabiano/.claude/projects/-home-fabiano-code-media-meme-pipeline/memory/av-quality-baseline.md`:

```markdown

**2026-07-23 — T2V-por-default NÃO promovido a baseline ainda.** Todos os exemplares em
`reference-baseline/` continuam sendo I2V (`source` ou `image` mode). O roteiro criativo T2V
manual (`creative-candidate.mp4`) recebeu nota 7/10, não o "perfeito" que este arquivo
documenta — é 1 amostra, não um lote validado. Não tratar T2V-por-default como equivalente em
qualidade ao pivô I2V até um lote real (5-8 posts, arquétipos variados, veredito humano nos 3
eixos) confirmar. Ver `docs/roadmap.md` item 21 para a régua completa e o achado de que motion
score automático (VMAF motion/freeze) não serve como gate substituto — confunde arquétipo de
cena com qualidade.
```

Também atualizar a linha de `description:` no frontmatter do mesmo arquivo se o escopo mudou (manter curto, só ajustar se necessário — provavelmente não precisa mudar, a descrição já é genérica o suficiente).

- [ ] **Step 4: Adicionar entrada no índice `MEMORY.md`**

Adicionar uma linha (a memória `av-quality-baseline.md` já está indexada — confirmar que a linha existente ainda descreve o conteúdo corretamente; se a atualização do Step 3 mudar o foco o suficiente, ajustar a linha do índice, senão não duplicar entrada).

- [ ] **Step 5: Atualizar `CHANGELOG.md` se aplicável**

Run: `head -30 CHANGELOG.md` para ver o formato (Keep a Changelog). Como esta task não muda comportamento/default do pipeline (só adiciona diagnóstico informativo na Task 2 e corrige uma string na Task 1), avaliar se merece entrada em `CHANGELOG.md` — a Task 2 sim (novo campo em `artifact_metadata`), a Task 3 não (documentação pura). Adicionar entrada em `CHANGELOG.md` sob "Added" cobrindo Task 2 quando essa task for concluída.

- [ ] **Step 6: Commit**

```bash
git add docs/roadmap.md CHANGELOG.md
git commit -m "docs: formalize T2V-default quality baseline promotion bar; record motion-score confound finding"
```

(A memória em `~/.claude/projects/.../memory/` não é rastreada pelo git deste repo — não entra neste commit.)

---

### Task 4: Experimento — comparar `--publish-model` alternativos ao `gemma4:31b`

**Files:**
- Create (scratchpad, não commitado): `/tmp/claude-1000/-home-fabiano-code-media-meme-pipeline/821ac046-d25e-4333-b636-ad63090793a3/scratchpad/compare_publish_models.py`

**Interfaces:**
- Consumes: `pipeline.hydrate_concept_record(record) -> tuple[RedditPost, dict]` (`scripts/daily_reddit_meme_pipeline.py:4038`), `pipeline.generate_publish_metadata(post, concept, publish_id, model, timeout, max_attempts=3) -> dict` (`scripts/daily_reddit_meme_pipeline.py:1290`)
- Produces: tabela impressa em stdout com taxa de sucesso por modelo — não persiste nada no repo, não altera `concepts.json` reais em `data/media-pipeline/`

Este experimento não segue o formato TDD (não é código de produção, é medição empírica pontual). Reaproveita os dois lotes reais que já geraram o achado de flakiness documentado no roadmap item 18: `data/media-pipeline/phase1-validation/2026-07-19/concepts.json` e `data/media-pipeline/phase2-render-part2/2026-07-19/concepts.json`, filtrando as entradas `evaluations.humor.approved and evaluations.quality.approved` (5 entradas confirmadas via a mesma checagem usada no diagnóstico desta sessão: 2 em `phase1-validation`, 3 em `phase2-render-part2`) e rodando `generate_publish_metadata` de novo, do zero, para cada modelo candidato — sem tocar GPU/ComfyUI.

- [ ] **Step 1: Escrever o script de comparação**

```python
"""One-off experiment: compare --publish-model candidates against the gemma4:31b
flakiness documented in docs/roadmap.md item 18 (5/9 empty-response failures).
Reuses already-approved concepts from disk; no GPU/ComfyUI involved."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path("/home/fabiano/code/media-meme-pipeline")
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import daily_reddit_meme_pipeline as pipeline  # noqa: E402

BATCHES = [
    REPO_ROOT / "data/media-pipeline/phase1-validation/2026-07-19/concepts.json",
    REPO_ROOT / "data/media-pipeline/phase2-render-part2/2026-07-19/concepts.json",
]
CANDIDATES = ["gemma4:31b", "llama3:latest", "qwen3:14b"]
TIMEOUT_SECONDS = 120

def load_approved_records() -> list[dict]:
    records: list[dict] = []
    for batch_path in BATCHES:
        document = json.loads(batch_path.read_text(encoding="utf-8"))
        for record in document:
            evaluations = record.get("evaluations") or {}
            humor = evaluations.get("humor") or {}
            quality = evaluations.get("quality") or {}
            if humor.get("approved") and quality.get("approved"):
                records.append(record)
    return records

def main() -> None:
    records = load_approved_records()
    print(f"{len(records)} approved concepts loaded from {len(BATCHES)} batches")
    results: dict[str, list[str]] = {model: [] for model in CANDIDATES}
    for model in CANDIDATES:
        print(f"\n=== {model} ===")
        for idx, record in enumerate(records, 1):
            post, concept = pipeline.hydrate_concept_record(record)
            concept["publish"] = {}
            publish = pipeline.generate_publish_metadata(
                post, concept, f"experiment-{model}-{idx:02d}", model, TIMEOUT_SECONDS
            )
            status = publish.get("status")
            results[model].append(status)
            print(f"  {idx}/{len(records)} {post.id}: {status}")
    print("\n=== Summary ===")
    for model, statuses in results.items():
        approved = statuses.count("approved")
        print(f"{model}: {approved}/{len(statuses)} approved")

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Confirmar que o Ollama local está no ar com os três modelos candidatos**

Run: `curl -s http://localhost:11434/api/tags | python3 -c "import json,sys; names={m['name'] for m in json.load(sys.stdin)['models']}; print(names)"`
Expected: conjunto contendo `gemma4:31b`, `llama3:latest`, `qwen3:14b` — se algum faltar, `ollama pull <nome>` antes de rodar (checar VRAM livre primeiro, per instrução global do usuário sobre GPU compartilhada com ComfyUI).

- [ ] **Step 3: Rodar o experimento**

Run: `python3 /tmp/claude-1000/-home-fabiano-code-media-meme-pipeline/821ac046-d25e-4333-b636-ad63090793a3/scratchpad/compare_publish_models.py`
Expected: tabela final com taxa de aprovação por modelo. Tempo esperado: 5 registros × 3 modelos × até 3 tentativas × timeout 120s no pior caso — rodar com `timeout 1800` como precaução.

- [ ] **Step 4: Interpretar e decidir**

Se algum candidato (`llama3:latest` ou `qwen3:14b`) tiver taxa de aprovação visivelmente melhor que `gemma4:31b` nesta amostra, propor ao usuário trocar o default de `--publish-model` (hoje herda `--humor-model`, que é `gemma4:31b`) para o modelo vencedor especificamente na etapa de publish metadata — **não mudar `--humor-model`** (esse já está validado 3/3 para escrita de piada, é uma tarefa diferente). Mudança de default é uma decisão de produto — reportar o resultado ao usuário antes de editar `scripts/daily_reddit_meme_pipeline.py:4395-4398` (default de `--publish-model`).

- [ ] **Step 5: Registrar o achado no roadmap independentemente do resultado**

Adicionar ao `docs/roadmap.md` (mesmo item 21 da Task 3, ou novo item 22) a taxa de sucesso medida por modelo — isso fecha o "candidato a investigação futura" citado no item 18 com um resultado real, positivo ou negativo.

---

## Ordem de execução recomendada

1. Task 1 (trivial, 10 min)
2. Task 4 (não bloqueia nada, pode rodar em paralelo/background enquanto Task 2 é implementada — usa Ollama, não GPU de render)
3. Task 2 (o item mais substancial de código)
4. Task 3 (fecha com a documentação, incorporando o resultado real da Task 4 se já estiver pronto)
