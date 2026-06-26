# Media Meme Pipeline

Pipeline local para gerar memes em vídeo a partir de posts públicos, usando Reddit RSS, Ollama, ComfyUI/LTX, narração PT-BR e revisão humana.

Este repositório é preparado para ser público. Ele não deve conter tokens, outputs gerados, dados de execução, modelos, arquivos `.env` ou identificadores privados.

## Estado Atual

Marco alcançado:

- vídeo em qualidade aceitável para revisão;
- texto/meme melhorado com crítica de humor;
- áudio PT-BR entendível;
- prompts LTX compilados como descrições cinematográficas literais.

Backlog futuro, fora do escopo atual:

- música de fundo cômica;
- entonação de voz mais cômica.

## Dependências Locais

Este projeto usa serviços externos locais. Ele não instala modelos nem publica serviços.

- ComfyUI: `http://localhost:8188`
- Ollama: `http://localhost:11434`
- n8n: `http://localhost:5678`
- Hermes/Telegram env: `~/.hermes/.env`

Modelos, custom nodes e outputs do ComfyUI ficam fora do Git. Antes de rodar vídeo, confirme que o host tem os modelos LTX e nodes necessários.

## Estrutura

```text
agents/
  comfyui-specialist.md
scripts/
  daily_reddit_meme_pipeline.py
  reddit_meme_dry_run.py
workflows/
  *.json
docs/
  architecture.md
data/
  media-pipeline/        # local e ignorado pelo Git
```

## Instalação Local

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install requests pillow
```

Para narração PT-BR com `edge-tts`:

```bash
python3 -m venv data/media-pipeline/.venv-edge-tts
data/media-pipeline/.venv-edge-tts/bin/pip install edge-tts
```

## Dry-Run Reddit

Seleciona candidatos sem gerar mídia, sem Telegram e sem cron:

```bash
python3 scripts/reddit_meme_dry_run.py \
  --subreddit popular \
  --limit 10 \
  --cache-on-failure
```

## Pipeline Sem Render

Gera seleção, descrições/conceitos e resumo, mas não chama ComfyUI:

```bash
python3 scripts/daily_reddit_meme_pipeline.py \
  --subreddit popular \
  --limit 10 \
  --max-per-subreddit 10 \
  --max-age-hours 48 \
  --output-root data/media-pipeline/reddit-dry-run \
  --run-tag dry-run \
  --no-render \
  --no-telegram
```

## Render de Vídeo LTX 2.3

Renderiza um caso de teste com duração alvo de 15s:

```bash
python3 scripts/daily_reddit_meme_pipeline.py \
  --subreddit popular \
  --limit 3 \
  --max-per-subreddit 3 \
  --max-age-hours 48 \
  --make-video \
  --video-engine ltx23 \
  --video-duration 15 \
  --only-index 1 \
  --output-root data/media-pipeline/reddit-ltx-test \
  --run-tag ltx-test \
  --no-telegram
```

Os artefatos de execução são gravados em `data/media-pipeline/`, que é ignorado pelo Git.

## Contrato LTX 2.3

O pipeline separa semântica de humor do prompt enviado ao modelo:

```text
post + descrição da imagem
  -> candidatos de humor e crítica
  -> roteiro semântico do vídeo
  -> compilador de prompt cinematográfico literal
  -> segmento T2V inicial
  -> continuação I2V pelo último frame
  -> continuação I2V pelo último frame
  -> mix de narração PT-BR
```

Prompts enviados ao LTX devem conter somente ação observável, personagem, objeto, ambiente, câmera, luz e som. Labels como `setup`, `complication`, `punchline` e offsets globais como `5-10s` são metadados internos e não devem ir para o modelo.

## Privacidade

- Não commitar `data/media-pipeline/`.
- Não commitar vídeos, imagens, áudio, caches, `.env`, logs ou payloads de execução.
- Não commitar tokens, chat IDs, headers privados ou dumps de respostas com dados sensíveis.
- Posts públicos podem ser processados localmente, mas outputs e revisões continuam privados por padrão.

## Segurança

Nunca exponha ComfyUI, Ollama, n8n ou Docker socket diretamente na internet. Se houver acesso remoto, use um túnel autenticado com MFA e mantenha os serviços restritos ao host/rede confiável.

Veja [SECURITY.md](SECURITY.md).
