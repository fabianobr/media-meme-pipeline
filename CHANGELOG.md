# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/). Este arquivo
lista mudanças funcionais (código, comportamento, defaults); o raciocínio completo por trás de
cada uma — o que foi tentado, o que falhou, o que o usuário corrigiu — está em
`docs/roadmap.md` e `docs/experiments/`.

## [Unreleased]

### Added
- Pacote de publicação Fase 1: seção `publish` no contrato de `concepts.json` (v3; leitura
  aceita v2) gerada por modelo local (`--publish-model`, default = `--humor-model`) com
  validação determinística e até 3 tentativas — título ≤100 chars, 3–5 assuntos, 4–8
  hashtags, pt-BR; falha nunca bloqueia o render. Por vídeo aprovado: diretório
  `NN-slug/` com `publish.json`, `publish.txt` (colável) e `final_916.mp4` (1080×1920,
  blur-pad via ffmpeg pós-validação — o MP4 nativo validado permanece intacto). Telegram
  (`--telegram`) envia o vídeo 9:16 com caption colável (truncada em 1024). Curadoria do
  r/popular prioriza fotos retrato no backlog (prioridade branda, paisagem segue elegível).
- Arquitetura "foto real narrada": engine `photomotion` (foto real, cortes secos por frase,
  legendas, narração TTS local — CPU), modo `--ltx23-audio-mode tts` (I2V da foto real
  só-vídeo com trilha TTS medida no mux), TTS plugável `--tts-backend piper|edge` com Piper
  local como default, contagem de frames derivada da duração real da narração, upgrade de
  URLs preview→i.redd.it e gate de resolução ≥640px na curadoria.
- Checkpoint incremental em `generate_concepts()`: `concepts.json` parcial é persistido após
  cada conceito concluído (best-effort), eliminando a perda do lote inteiro quando um timeout
  ou kill interrompe a rodada no meio.
- Gate de fonte com dois booleanos explícitos no schema (`embedded_text_carries_meaning`,
  `multi_photo_collage`) e tetos determinísticos em `finalize_source_suitability_review`:
  legenda embutida que carrega o significado limita `text_independence` a 2; colagem de fotos
  distintas limita também `visual_clarity` a 3 — ambos abaixo dos mínimos, forçando rejeição.
- Curadoria progressiva do `r/popular` (`scripts/reddit_popular_curation.py`) com backlog
  persistente entre execuções, checkpoint incremental e stdout sem buffer.
- Feed RSS solicitado com `?limit=100` (teto real do Reddit) em vez do default de 25
  entradas; exposto como `--rss-limit`.
- Segundo crítico de humor com visão real (`qwen2.5vl:7b` por default): recebe a imagem-fonte
  via base64, não só a descrição textual gerada uma vez no início do funil.
- Render LTX 2.3 em 2 segmentos (`--ltx23-segments 2`) para vídeos além do teto de memória de
  uma tacada só (~8-10s).
- Contrato de onboarding reproduzível (`scripts/check_environment.py`, `scripts/bootstrap.sh`).

### Changed
- Defaults do render mudados para a receita validada pelo usuário (2026-07-18):
  `--ltx23-input-mode source` (anima a foto real baixada, não uma imagem re-gerada) e
  `--ltx23-audio-mode tts` (narração Piper local medida no lugar do áudio nativo). Render
  segue a orientação da foto-fonte (retrato→retrato).
- Funil de humor recalibrado para voz narrativa: regra determinística rejeita diálogo cujo
  setup+escalada apenas descrevem a cena (≥60% de overlap com a fonte); prompt do escritor e
  rubrica dos críticos exigem narrador com opinião (suspeita/ironia/reação) em vez de
  audiodescrição; gate de fonte pontua `motion_potential` pelo que o I2V realmente anima
  (rosto em close ou elementos móveis; cena aberta com sujeitos distantes ≤2).
- Adotado o grafo oficial do template ComfyUI `video_ltx2_3_i2v`
  (`workflows/05-ltx23-official-i2v-audio-api.json`) como caminho I2V default, substituindo o
  grafo hand-built (`04`, aposentado) que rodava um regime de guidance incompatível com o LoRA
  distilled (CFG>1 + STG), causando pseudo-texto e drift.
- Rubrica dos críticos de humor endurecida contra punchlines puramente descritivas (regra
  determinística de overlap de tokens + teste explícito na rubrica).
- Rubrica dos críticos e prompt do escritor endurecidos contra viradas sem ancoragem visual —
  a virada (papel/profissão/intenção inesperada) precisa apontar para algo literalmente
  visível na cena; conceitos abstratos sem pista visual correspondente limitam `visual_payoff`
  a 4.
- `--concept-timeout` default: 60s → 600s (modelos "pensantes" como `qwen3:14b` precisavam de
  mais tempo).
- Orçamento de tokens do escritor de humor: `num_predict` 750 → 1500 (candidatas verbosas em
  rodadas de autocorreção eram truncadas e descartadas por engano).

### Fixed
- Prompt de áudio LTX passou a pedir silêncio explícito antes/depois da fala (tentativa
  parcial — medido como insuficiente sozinho; a correção real foi calibrar a duração do clipe
  pela contagem de palavras do diálogo, verificada por transcrição Whisper + `silencedetect`).

## [0.1.0] - 2026-06-26

### Added
- Extração inicial do pipeline media-meme (Reddit RSS → seleção → humor → imagem → vídeo →
  validação → Telegram opcional).
