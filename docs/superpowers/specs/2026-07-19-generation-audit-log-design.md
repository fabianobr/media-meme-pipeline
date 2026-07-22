# Relatório de auditoria de geração (prompt + modelo + parâmetros por etapa/vídeo)

Data: 2026-07-19. Status: rascunho de design, feito em paralelo a uma validação em
andamento; decisões tomadas de forma autônoma a pedido do usuário ("decida e me avise").
Aguardando revisão do usuário antes de virar plano de implementação.

## Pedido original

> "quero um relatorio que armazene o prompt usado em cada etapa da cada video, junto com o
> modelo usado e os parametros. Não precisa para tudo e fazer pode ser depois."

Escopo explicitamente flexível: não precisa cobrir todas as etapas na v1.

## Descoberta importante: já existe um mecanismo parcial

`improve_humor_concept()` (`scripts/daily_reddit_meme_pipeline.py:1378`) já mantém
`concept["execution"]["llm_calls"]`, uma lista de registros por chamada ao escritor/críticos
de humor, com `stage`, `round`, `model`, `timeout_seconds`, `started_at`, `finished_at`,
`elapsed_seconds`, `state`, `response_chars`, `response_preview` (`:1397-1428`). Essa lista já
atravessa `concept_document`/`persist_concepts`/`hydrate_concept_record` sem mudança de schema,
porque `execution` é gravado como dict livre — só `execution.state` é validado
(`validate_concepts_document`, `daily_reddit_meme_pipeline.py:3812`).

O que falta nesse mecanismo, e é exatamente o que o usuário pediu: o **prompt enviado** (hoje
só o preview da resposta é guardado) e os **parâmetros** (`options`: temperature, num_predict,
seed — hoje só `timeout_seconds` é guardado). E ele cobre só uma etapa; as outras não têm
nenhum registro.

Decisão: generalizar esse padrão existente em vez de inventar um mecanismo novo. DRY, sem
bump de schema version (diferente do `publish` da Fase 1, que precisou de v3 porque era uma
seção nova e validada — aqui é só enriquecer um dict já livre de schema).

## Inventário das etapas com chamada a modelo (levantado no código)

| Etapa | Função | Backend | Modelo (flag) | Parâmetros hoje | Prompt hoje |
|---|---|---|---|---|---|
| Descrição visual da fonte | `describe_source_image` (`:2547`) | Ollama `/api/chat` | `--vision-model` | `temperature=0.2` | string fixa com instruções anti-alucinação |
| Gate de adequação da fonte | `assess_source_suitability` (`:2624`) | Ollama `/api/chat` | `--source-critic-model` | `temperature=0, seed=20260705, num_predict=250` | monta a partir da descrição visual |
| Escritor + críticos de humor | `improve_humor_concept` (`:1378`) | Ollama `/api/chat` | `--humor-model`/`--humor-critic-model`/`--humor-second-critic-model` | varia por chamada (rounds) | já tem `execution.llm_calls`, falta o texto do prompt e `options` |
| Metadados de publicação | `generate_publish_metadata` (`:1209`, Fase 1) | Ollama `/api/chat` | `--publish-model` | `temperature=0.4, num_predict=700` | `compose_publish_prompt` |
| Roteiro semântico + prompt LTX literal | `build_video_script` + `compose_ltx23_segment_prompts` (`:2151`) | template determinístico (sem LLM) | n/a | n/a | texto literal final enviado ao ComfyUI |
| Render LTX 2.3 I2V | `queue_comfy_ltx23_native_video` (`:2258`) | ComfyUI `/prompt` | grafo `workflows/05-*.json` | seed, width/height, frames, segmentos | grafo + prompt literal acima |
| Narração TTS | `synthesize_ptbr_speech` (`:2982`) | Piper local / Edge hospedado | `--tts-backend`, voz | `--tts-rate` | texto por frase (setup/escalada/punchline) |
| Geração de imagem-base (caminho legado, não-default) | `queue_comfy_image` | ComfyUI `/prompt` | checkpoint T2I | seed | `compose_image_prompt`/`compose_scripted_image_prompt` |

## Escopo da v1 (decidido, dado "não precisa para tudo")

**Dentro:**
1. Descrição visual da fonte
2. Gate de adequação da fonte
3. Escritor + críticos de humor (generalizar o mecanismo existente)
4. Metadados de publicação (Fase 1)
5. Prompt LTX literal + parâmetros de render (seed/resolução/frames/segmentos) — não é uma
   chamada a LLM, mas é exatamente "prompt + parâmetros por etapa" que o usuário pediu, e é a
   etapa mais cara (GPU) e mais valiosa de auditar quando um vídeo sai errado.

**Fora da v1 (adiado, baixo custo/benefício agora):**
- TTS por frase (texto já está em `video_script`/`narration`, redundante auditar de novo)
- Caminho legado de imagem-base T2I (não é o default; `--ltx23-input-mode source` pula essa
  etapa)
- Fallback legado `generate_concepts` em lote (raramente usado; `improve_humor_concept` é o
  caminho primário)
- Caminho legado n8n

## Design

### A. Captura — generalizar `execution.llm_calls` → `execution.generation_calls`

Renomear e generalizar o helper interno de `improve_humor_concept` num utilitário
top-level reutilizável pelas 4 etapas Ollama da v1:

```python
def record_generation_call(
    concept: dict[str, Any],
    *,
    backend: str,          # "ollama" | "comfyui"
    stage: str,            # "vision_description" | "source_suitability" | "humor_writer" |
                            # "humor_critic" | "publish_metadata" | "ltx_render"
    model: str,
    prompt: str | list[dict[str, Any]],  # texto puro ou lista de messages (chat)
    options: dict[str, Any] | None = None,
    round_number: int | None = None,
) -> dict[str, Any]:
    ...
```

Retorna um `call_record` que o chamador preenche com `state`/`elapsed_seconds` como já faz
`timed_humor_request` hoje (o padrão existente é bom, só falta os dois campos novos).

Campos do registro (superset do que já existe hoje):

```json
{
  "backend": "ollama",
  "stage": "humor_writer",
  "round": 1,
  "model": "gemma4:31b",
  "prompt": "<texto completo ou messages, com imagens base64 substituídas por '[image omitted, N bytes]'>",
  "options": {"temperature": 0.7, "num_predict": 1500},
  "timeout_seconds": 600,
  "started_at": "...",
  "finished_at": "...",
  "elapsed_seconds": 12.4,
  "state": "completed",
  "response_chars": 812,
  "response_preview": "..."
}
```

Decisão: guardar o **prompt completo**, mas **redigir imagens base64** (substituir por um
marcador com o tamanho em bytes) — o pedido do usuário é auditar o prompt, não inflar
`concepts.json` em megabytes por causa de uma imagem já salva em disco como artefato próprio.
Manter `response_preview` como já é hoje (não expandir para resposta completa — fora do que
foi pedido; se precisar depois, é aditivo).

Para a etapa de render LTX (`backend: "comfyui"`), o registro troca `options` por
`render_params` (`seed`, `width`, `height`, `frames`, `segment_index`, `workflow_path`) e
`prompt` é o texto literal enviado ao grafo.

### B. Onde plugar cada etapa

- `describe_source_image`, `assess_source_suitability`, `generate_publish_metadata`: hoje
  não recebem `concept` como parâmetro em todos os casos (`describe_source_image` roda antes
  do conceito existir). Ajuste mínimo: essas funções passam a aceitar um `record: dict|None`
  opcional (o `concept` quando existir, ou um dict solto para a etapa de visão que roda antes
  do conceito) e anexam a `record.setdefault("execution", {}).setdefault("generation_calls", [])`.
- `improve_humor_concept`: troca `timed_humor_request` por `record_generation_call`,
  preservando o comportamento atual (só adiciona os 2 campos que faltam).
- `compose_ltx23_segment_prompts` + `queue_comfy_ltx23_native_video`: o ponto de chamada em
  `render_ltx_video_meme` (`main()`) grava um `generation_calls` com `backend: "comfyui"` por
  segmento renderizado.

Sem mudança de contrato: `generation_calls` vive dentro de `execution`, que já é
persistido/hidratado como dict livre. Nenhum bump de `CONCEPT_SCHEMA_VERSION`.

### C. Relatório legível — script novo `scripts/render_audit_report.py`

Os dados brutos ficam em `concepts.json` (fonte da verdade, já versionada por run). Um
"relatório" precisa de apresentação legível, então um script standalone (mesmo padrão de
`reddit_popular_curation.py`) lê um `concepts.json` e emite `audit-report.md` no mesmo
diretório:

```
python3 scripts/render_audit_report.py --concepts-file <run_dir>/concepts.json
```

Formato: uma seção por vídeo (índice + título do post + slug), com uma tabela por etapa —
modelo, parâmetros, tempo decorrido, estado — e o prompt completo em bloco de código
recolhível (markdown `<details>`) para não poluir a leitura corrida. Rodar sobre um run
antigo (schema v2 ou v3 sem `generation_calls`) simplesmente produz seções vazias para as
etapas sem dado — sem quebrar.

Decisão de não gerar o relatório automaticamente a cada run: mantém o custo de geração fora
do caminho crítico (GPU/render) e permite reprocessar relatórios de runs passados sem
re-rodar nada.

## Fora de escopo (explícito)

- Alterar o que é enviado aos modelos (isto é só instrumentação/observabilidade).
- Dashboard web ou UI — o output é markdown.
- Cobertura das etapas adiadas (TTS, imagem-base legada, fallback em lote, n8n).

## Testes

Padrão unittest existente:
1. `record_generation_call` grava os campos esperados e redige imagens embutidas em `prompt`.
2. `improve_humor_concept` continua produzindo `execution.llm_calls`→`generation_calls` com
   os campos antigos intactos (não quebra nada que já lê esse campo).
3. `render_audit_report.py` sobre um `concepts.json` fixture com 1 vídeo e 2 etapas produz
   markdown com as duas seções; sobre um `concepts.json` sem `generation_calls` produz
   relatório vazio sem erro.
