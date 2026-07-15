# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/). Este arquivo
lista mudanças funcionais (código, comportamento, defaults); o raciocínio completo por trás de
cada uma — o que foi tentado, o que falhou, o que o usuário corrigiu — está em
`docs/roadmap.md` e `docs/experiments/`.

## [Unreleased]

### Added
- Segundo crítico de humor com visão real (`qwen2.5vl:7b` por default): recebe a imagem-fonte
  via base64, não só a descrição textual gerada uma vez no início do funil.
- Render LTX 2.3 em 2 segmentos (`--ltx23-segments 2`) para vídeos além do teto de memória de
  uma tacada só (~8-10s).
- Contrato de onboarding reproduzível (`scripts/check_environment.py`, `scripts/bootstrap.sh`).

### Changed
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
