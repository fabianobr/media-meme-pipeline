# Media Meme Pipeline — Modelo C4

Este documento descreve a arquitetura do pipeline usando o modelo C4 (Contexto, Contêiner,
Componente/Sequência). É voltado a engenheiros que vão modificar ou operar o sistema, não a
usuários finais.

## Introdução

O sistema transforma posts públicos do Reddit em vídeos-meme curtos e revisáveis. O design
central é um funil adversarial: cada etapa (fonte, humor, texto) tem um gate de rejeição
antes da etapa seguinte, porque o render de vídeo é a operação mais cara do pipeline (minutos
de GPU por tentativa) e a aposta arquitetural é gastar esse custo só depois que a piada já
sobreviveu a crítica automatizada e o texto já foi revisado. O pipeline roda inteiramente
local — modelos de linguagem e visão via Ollama, render de vídeo via ComfyUI/LTX 2.3 numa GPU
local — sem chamada a nenhum serviço de IA hospedado.

## C4 Nível 1 — Diagrama de Contexto

```mermaid
C4Context
    title Media Meme Pipeline — Contexto do Sistema

    Person(operador, "Operador", "Curador e revisor humano; aprova texto e vídeo antes da publicação")

    System(pipeline, "Media Meme Pipeline", "Seleciona posts, gera e critica humor, produz e valida vídeo-meme, monta pacote de publicação")

    System_Ext(reddit, "Reddit", "Feeds RSS públicos (r/popular e afins)")
    System_Ext(ollama, "Ollama", "Servidor local de modelos de linguagem e visão (localhost:11434)")
    System_Ext(comfyui, "ComfyUI", "Servidor local de render de vídeo, GPU (localhost:8188)")
    System_Ext(telegram, "Telegram Bot API", "Entrega opcional de vídeo/imagem para revisão remota")

    Rel(operador, pipeline, "Invoca via CLI, revisa texto e vídeo, aprova ou descarta")
    Rel(pipeline, reddit, "Lê posts e metadados via RSS (HTTP GET)")
    Rel(pipeline, ollama, "Descrição visual, crítica de humor, geração de texto, metadados de publicação (HTTP REST)")
    Rel(pipeline, comfyui, "Enfileira e consulta renders LTX 2.3 I2V (HTTP REST)")
    Rel(pipeline, telegram, "Envia vídeo final + caption, opt-in via --telegram (HTTP REST)")
```

| Relação | O que passa |
|---|---|
| Operador → Pipeline | Linha de comando (`daily_reddit_meme_pipeline.py`, `reddit_popular_curation.py`), decisões de aprovação de texto |
| Pipeline → Reddit | Requisição RSS (`?limit=100`), resposta em Atom/XML com posts, título, mídia |
| Pipeline → Ollama | Prompts de chat (texto e imagem base64), resposta em JSON estruturado |
| Pipeline → ComfyUI | Grafo de workflow parametrizado (`/prompt`), polling de status (`/history`), download do MP4 |
| Pipeline → Telegram | Upload de vídeo/imagem via `sendVideo`/`sendMediaGroup`, opt-in explícito |

## C4 Nível 2 — Diagrama de Contêineres

```mermaid
C4Container
    title Media Meme Pipeline — Contêineres

    Person(operador, "Operador")

    System_Boundary(pipeline, "Media Meme Pipeline") {
        Container(curation, "reddit_popular_curation.py", "Python CLI", "Curadoria progressiva do r/popular com backlog persistente entre execuções")
        Container(main, "daily_reddit_meme_pipeline.py", "Python CLI (~4300 linhas)", "Motor do funil: seleção, gate de fonte, humor, produção, render, validação, entrega")
        Container(auditreport, "render_audit_report.py", "Python CLI standalone", "Converte concepts.json em relatório markdown legível por vídeo/etapa")
        Container(perflog, "record_performance.py", "Python CLI standalone", "Grava métricas de engajamento fornecidas manualmente, append-only")
        ContainerDb(conceptsjson, "concepts.json", "Arquivo JSON por run", "Contrato versionado (schema v3): post, joke, evaluations, production, artifacts, execution, publish")
        ContainerDb(perfstore, "performance-log.json", "Arquivo JSON cross-run", "Log append-only de métricas por publish_id")
    }

    System_Ext(ollama, "Ollama", "API REST local :11434")
    System_Ext(comfyui, "ComfyUI", "API REST local :8188, executa workflows/05-ltx23-official-i2v-audio-api.json")
    System_Ext(piper, "Piper", "Binário TTS local, subprocess")
    System_Ext(ffmpeg, "ffmpeg", "Binário de mux/formatação, subprocess")
    System_Ext(telegram, "Telegram Bot API", "Externo, opt-in")
    System_Ext(reddit, "Reddit RSS", "Externo")

    Rel(operador, curation, "Roda periodicamente para acumular backlog")
    Rel(operador, main, "Roda com --posts-file ou seleção ao vivo")
    Rel(operador, auditreport, "Roda sob demanda para investigar um run")
    Rel(operador, perflog, "Roda manualmente após observar métricas publicadas")

    Rel(curation, reddit, "RSS")
    Rel(curation, ollama, "descrição visual e gate de fonte")
    Rel(curation, main, "backlog reaproveitado como --posts-file")

    Rel(main, reddit, "RSS (seleção ao vivo)")
    Rel(main, ollama, "HTTP REST /api/chat")
    Rel(main, comfyui, "HTTP REST /prompt, /history")
    Rel(main, piper, "subprocess (síntese de narração)")
    Rel(main, ffmpeg, "subprocess (mux, blur-pad 9:16)")
    Rel(main, telegram, "HTTP REST, opt-in")
    Rel(main, conceptsjson, "escreve por checkpoint e ao final do run")

    Rel(auditreport, conceptsjson, "leitura apenas")
    Rel(perflog, perfstore, "leitura+escrita, append-only")
```

| Contêiner | Responsabilidade | Tecnologia/protocolo |
|---|---|---|
| `reddit_popular_curation.py` | Curadoria progressiva: aplica o gate de fonte antecipadamente e acumula um backlog aprovado entre execuções | Python, HTTP (RSS + Ollama), checkpoint incremental em JSON |
| `daily_reddit_meme_pipeline.py` | Orquestra o funil completo — do post ao pacote de publicação | Python, HTTP REST (Ollama, ComfyUI, Telegram), subprocess (Piper, ffmpeg) |
| `render_audit_report.py` | Observabilidade pós-facto: transforma o log de auditoria em documento legível | Python, leitura de arquivo, sem rede |
| `record_performance.py` | Captura manual de métricas de engajamento para uso futuro (Fase 3, não implementada) | Python, leitura+escrita de arquivo, sem rede |
| `concepts.json` | Fonte da verdade por run — todo o estado do funil, incluindo o log de auditoria de geração | Arquivo JSON, schema versionado (`CONCEPT_SCHEMA_VERSION`) |
| `performance-log.json` | Histórico de métricas cross-run, chaveado por `publish_id` | Arquivo JSON, lista append-only |
| Ollama | Inferência local de LLM/VLM | HTTP REST, `/api/chat`, payload `{model, messages, options}` |
| ComfyUI | Render de vídeo via grafo LTX 2.3 | HTTP REST, grafo de nós parametrizado, polling assíncrono |
| Piper | Síntese de voz local (narração pt-BR) | Binário CLI via subprocess |
| ffmpeg | Mux de áudio, extração de frame, formatação 9:16 | Binário CLI via subprocess |
| Telegram Bot API | Entrega de revisão remota | HTTP REST, multipart upload |

## Diagrama de Sequência — Caso de uso principal

Fluxo: gerar um vídeo-meme aprovado, do post do Reddit ao pacote de publicação pronto.

```mermaid
sequenceDiagram
    participant Operador
    participant CLI as CLI (daily_reddit_meme_pipeline.main)
    participant Reddit
    participant Ollama
    participant ComfyUI
    participant Piper
    participant ConceptsJSON as concepts.json

    Operador->>CLI: executa run (posts ao vivo ou --posts-file)
    CLI->>Reddit: fetch_feed (RSS)
    Reddit-->>CLI: posts candidatos
    CLI->>CLI: download_source_media(post)

    CLI->>Ollama: describe_source_image (qwen2.5vl:7b, imagem)
    Ollama-->>CLI: descrição visual (via timed_generation_request)

    CLI->>Ollama: assess_source_suitability (qwen2.5vl:7b)
    Ollama-->>CLI: scores + embedded_text_carries_meaning + multi_photo_collage + open_scene_no_intrinsic_motion + resting_domestic_animal_scene
    CLI->>CLI: finalize_source_suitability_review (tetos determinísticos)

    alt fonte reprovada
        CLI->>ConceptsJSON: persist_concepts (estado rejeitado)
        CLI-->>Operador: post descartado, sem render
    else fonte aprovada
        loop até 3 rounds
            CLI->>Ollama: improve_humor_concept — escritor (gemma4:31b)
            Ollama-->>CLI: candidatos de piada
            CLI->>CLI: checagem determinística de overlap (≥60% = descarte)
            CLI->>Ollama: crítico 1 — llama3:latest (texto)
            Ollama-->>CLI: avaliação
            CLI->>Ollama: crítico 2 — qwen2.5vl:7b (texto + imagem base64)
            Ollama-->>CLI: avaliação
            CLI->>CLI: evaluate_concept_quality (consenso)
        end

        alt humor reprovado em todos os rounds
            CLI->>ConceptsJSON: persist_concepts (estado rejeitado)
            CLI-->>Operador: piada descartada, sem render
        else humor aprovado
            Operador->>Operador: revisão de texto (até 2 correções)
            CLI->>CLI: build_video_script + compose_ltx23_segment_prompts

            CLI->>Ollama: generate_publish_metadata (qwen3:14b, até 3 tentativas)
            Ollama-->>CLI: título, descrição, tópicos, hashtags (ou status=failed)

            CLI->>Piper: synthesize_narration_track (texto pt-BR)
            Piper-->>CLI: áudio + duração medida

            CLI->>ComfyUI: render_ltx_video_meme → queue_comfy_ltx23_native_video
            ComfyUI-->>CLI: prompt_id
            CLI->>ComfyUI: wait_for_comfy_video (polling)
            ComfyUI-->>CLI: referência do vídeo renderizado
            CLI->>ComfyUI: download_comfy_file
            ComfyUI-->>CLI: MP4 bruto

            CLI->>CLI: finish_ltx23_with_tts (mux narração)
            CLI->>CLI: format_video_916 (ffmpeg blur-pad)
            CLI->>CLI: probe_video_artifact (Whisper + silencedetect)

            CLI->>ConceptsJSON: persist_concepts (inclui execution.generation_calls de toda chamada Ollama/ComfyUI acima)

            opt --telegram
                CLI->>Operador: send_telegram_videos (vídeo + caption)
            end

            CLI-->>Operador: pacote de publicação pronto (final_916.mp4 + publish.json + publish.txt)
        end
    end
```

## Entidades principais

**`RedditPost`** (dataclass, `reddit_meme_dry_run.py`) — um post candidato.

| Campo | Tipo | Propósito |
|---|---|---|
| `id` | str | Identificador do post no Reddit |
| `title` | str | Título, insumo direto do humor e dos metadados de publicação |
| `subreddit` | str | Origem |
| `media_url` / `media_type` | str | Localização e tipo da mídia-fonte |
| `summary` | str | Corpo/resumo do post |

**Dict "concept"** (estrutura de trabalho em memória, não persistida diretamente) — acumula o
estado de um candidato ao longo do funil: `top_text`/`middle_text`/`bottom_text` (piada),
`video_script` (roteiro semântico), `source_review`/`humor_review`/`quality_review`
(avaliações), `execution` (estado de estágio + `generation_calls`), `publish` (metadados),
`ltx23_segments` (parâmetros de render por segmento).

**`concepts.json`** (documento persistido por run, schema v3) — a fonte da verdade. Cada
entrada da lista corresponde a um concept, com seções fixas:

| Seção | Conteúdo |
|---|---|
| `post` | `RedditPost` serializado |
| `joke` | setup, escalation, punchline, lógica, arquétipo |
| `evaluations` | reviews de fonte, humor, qualidade, rounds |
| `production` | roteiro de vídeo, prompt de imagem, brief de fonte |
| `artifacts` | paths (`*_path`) e metadados técnicos do vídeo (duração, codec, resolução) |
| `execution` | estado do estágio, tentativas, `generation_calls` (log de auditoria — modelo, prompt redigido, parâmetros, timing, preview de resposta, inclusive falhas) |
| `publish` | título, descrição, tópicos de interesse, hashtags, status (approved/failed) |

**`performance-log.json`** (lista append-only, cross-run) — um registro por medição:
`publish_id`, `platform`, `captured_at` (ISO 8601, timezone-aware), `metrics` (dict livre,
ex.: `views`, `likes`, `comments`).

## Modelos usados

| Modelo | Papel | Backend | Motivo da escolha | Parâmetros típicos |
|---|---|---|---|---|
| `qwen2.5vl:7b` | Descrição visual da fonte (`describe_source_image`) | Ollama | Único modelo com visão real nesta configuração; necessário para descrever a imagem sem alucinar | `temperature=0.2` |
| `qwen2.5vl:7b` | Gate de adequação de fonte (`assess_source_suitability`) | Ollama | Mesmo motivo — decide sobre a imagem, não só sobre texto | `temperature=0, seed=20260705, num_predict=250` |
| `gemma4:31b` | Escritor de humor (`improve_humor_concept`) | Ollama | Melhor desempenho observado para geração de piada em pt-BR (~10% de aprovação orgânica histórica) | `temperature=0.85, num_predict=1500` |
| `llama3:latest` | Crítico 1 de humor (texto) | Ollama | Crítica adversarial exige modelo distinto do escritor, para não repetir o viés de geração | `temperature=0.1, num_predict=900` |
| `qwen2.5vl:7b` | Crítico 2 de humor (visão) | Ollama | Segundo crítico independente, com acesso à imagem real — corrige o falso negativo de crítico só-texto | `temperature=0.1, num_predict=900` |
| `qwen3:14b` | Metadados de publicação (`generate_publish_metadata`) | Ollama | `gemma4:31b` (default anterior) deu 0/5 aprovados num experimento controlado (timeout em toda tentativa); `qwen3:14b` deu 5/5 sem retry — ver `docs/roadmap.md` item 22; configurável via `--publish-model` | `temperature=0.4, num_predict=700`, até 3 tentativas |
| LTX 2.3 (distilled LoRA) | Render de vídeo I2V | ComfyUI | Único motor de vídeo local integrado; regime distilled exige CFG=1.0 e sigmas manuais | grafo `workflows/05-ltx23-official-i2v-audio-api.json`, CFG 1.0, base half-res + upscale ×2 + refine 3 steps |

Todas as chamadas a Ollama passam por `timed_generation_request` (`daily_reddit_meme_pipeline.py:1138`),
que grava o payload completo (prompt com imagens redigidas, modelo, `options`) e o resultado
(estado, tempo decorrido, preview de resposta) em `execution.generation_calls` antes de
qualquer validação de schema — inclusive quando a chamada falha.
