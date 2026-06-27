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

## Plataforma Suportada

O caminho oficial inicial requer Ubuntu, GPU NVIDIA com pelo menos 16 GB de VRAM, 16 GB de RAM, Docker Compose v2, NVIDIA Container Toolkit/CDI e cerca de 100 GB livres. Os serviços ficam no repositório público `homelab-ai`; este repositório contém a aplicação e o contrato de modelos.

O bootstrap aceita somente a tag compatível do homelab. Isso impede que mudanças futuras da infraestrutura quebrem uma instalação reproduzível.

## Instalação Oficial

```bash
mkdir media-workspace && cd media-workspace

git clone --branch v1.0.0 https://github.com/fabianobr/homelab-ai.git
git clone https://github.com/fabianobr/media-meme-pipeline.git

cd media-meme-pipeline
export HF_TOKEN=seu_token_temporario
./scripts/bootstrap.sh \
  --homelab-root ../homelab-ai \
  --homelab-tag v1.0.0 \
  --install-models \
  --accept-model-licenses

. .venv/bin/activate
python3 scripts/check_environment.py --mode render --homelab-root ../homelab-ai
python3 scripts/reddit_meme_dry_run.py --subreddit popular --limit 3
```

Antes de usar `--accept-model-licenses`, revise os campos `license_url` em [infra/models.lock.yaml](infra/models.lock.yaml). `HF_TOKEN` é lido somente do ambiente, não é salvo pelo instalador e nunca deve entrar em `.env` versionado.

O bootstrap é idempotente, não executa `sudo`, não inicia n8n ou Hermes e sobe somente Ollama e ComfyUI pelo profile `media-pipeline`.

### Instalação para contribuir com código

Sem download de modelos (a validação os mostra como `OPTIONAL`):

```bash
./scripts/bootstrap.sh --homelab-root ../homelab-ai --homelab-tag v1.0.0
```

Para trabalhar somente nos scripts Python, sem infraestrutura:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
```

## Serviços Locais

URLs seguem a precedência argumento CLI, variável de ambiente e default localhost:

- ComfyUI: `http://localhost:8188`
- Ollama: `http://localhost:11434`
- n8n opcional: `http://localhost:5678`
- Hermes/Telegram env: `~/.hermes/.env`

Exemplo: `--ollama-url`, `OLLAMA_URL`, depois `http://localhost:11434`. Os equivalentes são `--comfyui-url`/`COMFYUI_URL` e `--n8n-url`/`N8N_URL`.

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

Este modo não exige n8n, Telegram nem ComfyUI. O render LTX chama ComfyUI diretamente; os webhooks n8n permanecem apenas para fluxos legados/opcionais.

## Validação

```bash
python3 scripts/check_environment.py --mode dry-run
python3 scripts/check_environment.py --mode render --homelab-root ../homelab-ai
python3 scripts/check_environment.py --mode full --homelab-root ../homelab-ai
```

`dry-run` verifica Python, Reddit, Ollama e seus modelos. `render` adiciona GPU, ComfyUI, custom nodes e modelos LTX. `full` adiciona n8n e reporta Telegram como integração opcional. A saída usa `OK`, `MISSING` e `OPTIONAL`, sempre com uma correção objetiva.

### n8n e Telegram opcionais

O caminho oficial não inicia nenhum dos dois. Para subir o n8n deliberadamente, exporte os mesmos paths usados no bootstrap e execute:

```bash
docker compose -f ../homelab-ai/infra/docker/docker-compose.yml --profile optional up -d n8n
```

Credenciais do Hermes/Telegram permanecem em `~/.hermes/.env`. O pipeline só envia ao Telegram quando `--telegram` é informado explicitamente; os valores nunca atravessam o contrato entre os repositórios.

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
- Não colocar tokens em argumentos de linha de comando, URLs, logs ou arquivos de manifesto.

## Segurança

Nunca exponha ComfyUI, Ollama, n8n ou Docker socket diretamente na internet. Se houver acesso remoto, use um túnel autenticado com MFA e mantenha os serviços restritos ao host/rede confiável.

Veja [SECURITY.md](SECURITY.md).

## Atualização Controlada

Não acompanhe automaticamente o branch principal do `homelab-ai`. Para atualizar:

1. escolha uma tag publicada do `homelab-ai`;
2. atualize `homelab.tag` em `infra/models.lock.yaml` e o default de `--homelab-tag`;
3. compare o contrato e os commits de custom nodes;
4. execute o bootstrap duas vezes e os três modos de validação;
5. publique uma nova versão deste pipeline somente após os testes.

O download inicial fica próximo de 100 GB considerando imagens, modelos Ollama e LTX. O tempo depende da conexão; um render de 15 segundos depende fortemente da GPU e pode levar vários minutos.
