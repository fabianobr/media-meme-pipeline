# Media Meme Pipeline

Pipeline local para gerar memes em vídeo a partir de posts públicos, usando Reddit RSS, Ollama, ComfyUI/LTX, narração PT-BR e revisão humana.

Este repositório é preparado para ser público. Ele não deve conter tokens, outputs gerados, dados de execução, modelos, arquivos `.env` ou identificadores privados.

## Estado Atual

Marco alcançado:

- contratos de conceito, crítica e artefatos endurecidos contra aprovação fabricada;
- curadoria progressiva de `r/popular` com backlog e checkpoints;
- crítico de humor com visão real (`qwen2.5vl:7b`) e revisão humana de texto antes de render;
- render ComfyUI/LTX com TTS Piper local e auditoria de geração;
- default atual do código: T2V (`--ltx23-input-mode prompt`) + TTS (`--ltx23-audio-mode tts`).

O estado operacional mais recente está em
[`docs/roadmap.md`](docs/roadmap.md) e
[`docs/experiments/2026-08-22-operational-handoff.md`](docs/experiments/2026-08-22-operational-handoff.md).
O default T2V está portado, mas ainda precisa de um baseline humano maior antes de ser tratado
como qualidade consolidada para todos os arquétipos.

Backlog futuro, fora do escopo atual:

- música de fundo cômica;
- entonação de voz mais cômica.
- parametrizar roteiros T2V ricos a partir da spec de prompt de 2026-08-16.

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

Para manter a avaliação de humor independente, configure modelos distintos para escritor
e crítico com `--humor-model` e `--humor-critic-model`. Respostas inválidas, vazias ou fora
do schema são rejeitadas e nunca recebem notas artificiais.

O segundo crítico (`--humor-second-critic-model`, default `qwen2.5vl:7b`) recebe a imagem-fonte
real, não só a descrição textual gerada pelo modelo de visão no início do funil — um crítico
cego à imagem subestima sistematicamente piadas que dependem de nuance visual. Isso reduz
falsos negativos (piada boa rejeitada), mas não elimina falsos positivos (o funil ainda pode
aprovar com nota alta uma virada que não se conecta à cena visível); revisar o texto antes de
renderizar continua recomendado. `--concept-timeout` (default `600`) evita que modelos mais
lentos/"pensantes" (ex. `qwen3:14b`) percam candidatas por timeout prematuro.

Para avaliar conceitos previamente curados sem chamar o escritor, use `--concepts-file`.
O arquivo deve associar cada `post_id` a 1-5 candidatas com `id`, `mechanic`, `setup`,
`escalation`, `punchline`, `comic_turn` e `scene_payoff`. As sementes ainda passam pelo
crítico independente, verificações determinísticas e rubrica; sua origem não concede
aprovação automática.

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

O caminho LTX 2.3 chama o ComfyUI diretamente. O default atual do código gera vídeo T2V a
partir de prompt cinematográfico literal e muxa narração Piper medida; I2V da foto real
continua disponível como alternativa/baseline. `ffprobe` valida o MP4; `ffmpeg` permanece
disponível para diagnóstico, mux e modos legados de revisão:

```bash
docker build -f infra/Dockerfile.runtime -t media-meme-pipeline:local .
```

O pipeline não instala dependências durante uma execução. Para execução no host, ative a `.venv` criada pelo bootstrap.

## Dry-Run Reddit

Seleciona candidatos sem gerar mídia, sem Telegram e sem cron:

```bash
python3 scripts/reddit_meme_dry_run.py \
  --subreddit popular \
  --limit 10 \
  --cache-on-failure
```

## Curadoria de Backlog do r/popular

`r/popular` é a fonte fixa de posts (decisão de produto), mas seu RSS devolve só ~25 entradas
por busca, sem paginação, e mistura imagem/vídeo/texto — a maioria não passa no gate de fonte
(screenshots, placares, conteúdo dependente de texto). `scripts/reddit_popular_curation.py`
resolve isso com curadoria progressiva: cada execução busca o feed atual, pula posts já
avaliados (aprovados ou rejeitados) em execuções anteriores, roda os posts de imagem novos
pela mesma descrição de visão + gate de fonte do pipeline principal, e acumula aprovados num
backlog persistente até atingir `--target` (default 20). Rode repetidamente (ex.: diário) até
o backlog fechar:

```bash
python3 scripts/reddit_popular_curation.py --target 20
```

Posts de vídeo e texto são pulados, não avaliados: mesmo com T2V como default de render, o
funil atual ainda precisa de uma cena visual concreta baixada/descritível para escrever o
prompt cinematográfico e avaliar potencial de movimento. O backlog fica em
`data/media-pipeline/popular-curated-backlog.json` (gitignored); cada entrada aprovada
carrega o post, o caminho da mídia baixada, a descrição visual e a revisão do gate de fonte,
prontos para alimentar `--concepts-file`/`--approved-concepts-file` do pipeline principal.

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

Para repetir uma seleção congelada sem consultar o Reddit, use `--posts-file caminho/selected.json`. Toda execução faz um preflight único e grava `preflight.json`; dependências obrigatórias ausentes encerram o processo antes de geração ou renderização.

`concepts.json` usa um contrato versionado que separa `post`, `joke`, `evaluations`, `production`, `artifacts` e `execution`. Estados válidos são `pending`, `running`, `approved`, `rejected` e `failed`. Uma crítica ausente ou inválida sempre rejeita o conceito.

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

O caminho default atual é T2V no ComfyUI: o pipeline compila um prompt cinematográfico
literal, renderiza vídeo sem fala nativa e muxa uma narração Piper medida. I2V continua
disponível com `--ltx23-input-mode source` (foto real baixada) ou `image` (imagem-base
gerada), usando o grafo oficial `video_ltx2_3_i2v` convertido para API em
`workflows/05-ltx23-official-i2v-audio-api.json`.

```bash
python3 scripts/daily_reddit_meme_pipeline.py \
  --subreddit popular \
  --limit 3 \
  --max-per-subreddit 3 \
  --max-age-hours 48 \
  --make-video \
  --video-engine ltx23 \
  --ltx23-input-mode prompt \
  --ltx23-audio-mode tts \
  --ltx23-width 768 \
  --ltx23-height 448 \
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
  -> workflow ComfyUI LTX 2.3 nativo
  -> vídeo renderizado
  -> narração TTS local medida e muxada
  -> validação local do MP4
```

Os grafos em `workflows/03-ltx23-native-t2v-audio-api.json` e
`workflows/05-ltx23-official-i2v-audio-api.json` são as fontes de verdade. O grafo `04`
(I2V construído à mão) foi aposentado após reprovar no gate visual com pseudo-texto e drift;
a causa raiz foi o regime de guidance/schedule incompatível com o LoRA distilled, não o
prompt. O Python apenas parametriza entradas declaradas, enfileira em `/prompt`, consulta
`/history`, baixa e valida o resultado. O modo default atual para `ltx23` é
`--ltx23-input-mode prompt`; `source` e `image` são caminhos I2V alternativos.

Histórico: o smoke T2V de 2026-06-28 executou sem OOM, mas falhou visualmente com pseudo-texto
e marcas de interface; o smoke I2V de 2026-06-29 preservou composição no gate técnico. Depois
disso, o pipeline migrou para T2V+TTS com prompt cinematográfico literal, mas o baseline
humano ainda não cobre todos os arquétipos. Consulte `docs/roadmap.md` itens 20-21 e
`docs/superpowers/specs/2026-08-16-t2v-quality-prompt-template-design.md` antes de tratar
T2V como qualidade consolidada.

### Duração, memória e pausas de áudio

Há dois tetos de memória distintos numa GPU de 16 GB/host de ~32 GB de RAM: o teto de VRAM
afeta o passe de refine em resolução alta, e um teto separado de RAM do host pode derrubar o
processo do ComfyUI silenciosamente. Em I2V, vídeos além de ~8s podem usar
`--ltx23-segments 2`: o grafo renderiza dois segmentos, o segundo ancorado no último frame
extraído do primeiro, e os MP4s são concatenados sem recodificar. Em T2V+TTS, o pipeline
limita o render a 353 frames / ~14,12s (`LTX23_T2V_TTS_MAX_FRAMES`) até o grafo longo ser
validado formalmente.

O áudio nativo do LTX preenche a duração inteira com a fala — não existe uma forma confiável
de pedir uma pausa por instrução no prompt (testado e medido: pausa por texto não muda o
padrão de silêncio). A pausa antes/depois da fala é resultado de sobrar duração além do tempo
que o diálogo leva para ser falado, não de um pedido no prompt. Não existe fórmula fechada
palavras→quadros; calibrar por tentativa com folga generosa e verificar sempre com duas
ferramentas antes de aceitar o resultado — transcrição (`whisper <arquivo> --language
Portuguese --model small`) para confirmar que o conteúdo falado é o esperado, e
`ffmpeg -af silencedetect=noise=-30dB:d=0.2` para confirmar que sobra silêncio real no fim
sem cortar a fala. Ao iterar `--ltx23-frames` na mesma pasta de saída, apague o `.mp4`
anterior antes de re-renderizar — o pipeline reaproveita um vídeo existente pelo nome do
arquivo, não pelos parâmetros de render.

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
