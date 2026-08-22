# Próximos passos — retomada segura do pipeline de memes

**Data:** 2026-08-22
**Status:** pista operacional para próxima sessão; não é uma autorização para render em lote.

## Objetivo

Retomar o pipeline sem repetir loops empíricos já descartados, preservando o norte:
qualidade percebida pelo usuário, ComfyUI como executor audiovisual, workflows versionados
como fonte de verdade e avaliação humana nos pontos em que métrica automática não decide.

## Premissas

- `scripts/daily_reddit_meme_pipeline.py` é o pipeline principal.
- `r/popular` permanece a fonte padrão.
- O código atual usa `--ltx23-input-mode prompt` + `--ltx23-audio-mode tts` por default.
- T2V+TTS está portado, mas ainda precisa de baseline humano maior para ser considerado
  qualidade consolidada.
- `workflows/04-ltx23-native-i2v-audio-api.json` está aposentado.
- Qualquer adoção de Wan 2.2 depende de veredito humano do lab e de promoção explícita do
  workflow/modelos para os locais de produção.

## Plano big-picture

### Etapa 0 — Sincronizar contexto local

- Rodar `git status -sb` e revisar diffs locais antes de editar.
- Ler `docs/experiments/2026-08-22-operational-handoff.md`.
- Confirmar defaults reais no parser, não inferir pelo README.
- Confirmar ComfyUI/Ollama/RAM/VRAM/fila antes de qualquer render.

### Etapa 1 — Decidir lane de vídeo

Escolher uma das três:

1. **T2V+TTS atual:** continuar a exploração da spec de prompt rica.
2. **Wan 2.2:** promover se o usuário julgar melhor que LTX nos pares A/B.
3. **I2V foto real:** manter como baseline/alternativa para cenas em que preservar a foto
   real é mais importante que inventar movimento.

Não misturar as lanes no mesmo experimento sem escrever hipótese e critérios antes.

### Etapa 2 — Se lane T2V+TTS for escolhida

- Fechar 2–3 testes manuais com a biblioteca de blocos da spec de 2026-08-16.
- Cada teste precisa ter:
  - prompt visual completo;
  - texto PT-BR final;
  - MP4;
  - transcrição/validação de áudio;
  - frames ou revisão visual;
  - veredito humano nos eixos piada, voz e movimento.
- Só depois parametrizar em `build_video_script()`/`compose_ltx23_segment_prompts()`.

### Etapa 3 — Parametrizar com testes

- Transformar os blocos de prompt em funções pequenas.
- Testar que o prompt não perde sujeito, ação física concreta, âncora visual nem câmera.
- Testar que cenas sem sujeito animado não recebem ações humanas/animais impossíveis.
- Manter teto T2V+TTS de 353 frames até investigar o grafo oficial longo.

### Etapa 4 — Mostrar resultados positivos ao usuário

Parar aqui quando houver candidatos positivos de roteiro/texto, conforme combinado com o
usuário. Entregar poucos itens, com evidência suficiente para escolher o que vai renderizar.

### Etapa 5 — Render controlado e nova parada

Renderizar no máximo os melhores aprovados. Antes de entregar:

- Whisper/transcrição para conteúdo da fala;
- duração e áudio por `ffprobe`;
- silêncio final por `silencedetect` quando aplicável;
- frames-chave para coerência visual;
- `human-review.md`/artefatos corretos.

Parar aqui de novo para o usuário avaliar os vídeos positivos.

## Não fazer

- Não rodar render em lote para compensar conceito fraco.
- Não trocar prompt indefinidamente sem hipótese.
- Não usar workflow hand-built escondido em Python.
- Não usar métricas de motion/freeze como aprovação automática.
- Não instalar dependências efêmeras durante execução normal.
- Não deixar processo sem timeout/estado terminal.
- Não promover Wan, lip-sync nativo ou prompt-enhancer como default sem veredito humano.
