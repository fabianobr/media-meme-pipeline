# Fase 1 — Pacote de publicação (Shorts / Reels / TikTok)

Data: 2026-07-19. Status: design aprovado pelo usuário; aguardando plano de implementação.

## Contexto e objetivo

O pipeline hoje termina em "MP4 validado + revisão humana". A visão maior é uma máquina de
vídeos virais com loop de feedback (gerar → publicar → medir aceitação → recalibrar o funil).
Esta Fase 1 cobre apenas a ponta de saída: cada vídeo aprovado sai como um **pacote de
publicação** pronto para as três plataformas verticais — MP4 9:16 + título + descrição +
assuntos de interesse + hashtags — com um ID rastreável que a Fase 2 (métricas) usará depois.

Ressalva registrada: nada garante viral; o objetivo do sistema é aumentar taxa de acerto
ciclo a ciclo. Publicação é manual (sem APIs de upload nesta fase).

## Escopo

Dentro:

1. Geração de metadados de publicação (PT-BR) por modelo local via Ollama.
2. Seção nova `publish` no contrato versionado de `concepts.json` (bump de versão).
3. Artefatos por vídeo: `publish.json` + `publish.txt` (colável) + `final_916.mp4`.
4. Garantia de saída 9:16 via formatação ffmpeg no passo final (blur-pad), sem tocar no render LTX.
5. Curadoria do `r/popular` passa a **priorizar** (não exigir) fotos retrato no ranking.
6. `publish_id` determinístico (run_tag + índice do conceito) gravado no pacote.
7. Caption do Telegram (quando `--telegram`) passa a incluir título + descrição + hashtags.

Fora (explícito):

- Upload automático por API (YouTube/Meta/TikTok).
- Coleta ou ingestão de métricas de performance (Fase 2).
- Qualquer recalibração do funil por dados de aceitação (Fase 3).

## Componente A — Metadados de publicação

**Quando:** após a aprovação da piada pelo funil adversarial e **antes** do render — o
pacote de texto sobrevive a falha de render e fica disponível em replays
(`--approved-concepts-file`).

**Quem gera:** modelo local via Ollama. Default: o mesmo escritor de humor
(`gemma4:31b`, já validado para texto criativo em PT-BR). Flag nova `--publish-model`
para trocar, seguindo o padrão das flags `--humor-model`/`--humor-critic-model`.

**Schema da seção `publish`:**

```json
{
  "publish_id": "<run_tag>-<indice>",
  "language": "pt-BR",
  "title": "<gancho, max 100 chars (limite YouTube)>",
  "description": "<descricao base, 1-3 frases>",
  "description_with_hashtags": "<descricao + hashtags no corpo (estilo TikTok/Reels)>",
  "interest_topics": ["<3 a 5 assuntos em linguagem natural>"],
  "hashtags": ["<4 a 8, mescla de genericas quentes + especificas do tema>"],
  "model": "<modelo que gerou>",
  "status": "approved | rejected | failed"
}
```

**Validação (mesma filosofia do funil):** resposta vazia, fora do schema, título acima do
limite, contagens fora das faixas (topics 3–5, hashtags 4–8) ⇒ rejeita e re-tenta até 2×;
na terceira falha marca `failed` e o conceito segue sem pacote (o render não é bloqueado —
metadado é complemento, não gate). Nunca inventar/completar valores artificialmente.

**Entrada do prompt:** título do post-fonte, piada aprovada (setup/escalada/punchline),
descrição visual da fonte e `source_brief` — tudo já presente em `production`. O prompt
pede linguagem de descoberta/curiosidade, não clickbait enganoso, e proíbe mencionar que o
conteúdo é gerado por IA na descrição (decisão de honestidade fica com o usuário na
publicação manual; o pipeline não afirma nem esconde).

## Componente B — Saída vertical 9:16 garantida

O render LTX **continua seguindo a orientação da foto-fonte** (regra existente em
`daily_reddit_meme_pipeline.py` — motivada por drift do I2V com retrato espremido em
paisagem; 3 ocorrências documentadas. Não mexer).

A garantia de 9:16 vem de duas pontas:

1. **Curadoria** (`reddit_popular_curation.py`): fotos retrato ganham prioridade no
   ranking. Critério brando — foto horizontal excelente continua passando.
2. **Formatação final no mux (ffmpeg, CPU, segundos):** canvas fixo 1080×1920.
   - Fonte retrato (render 704×1280): escala direta para preencher.
   - Fonte horizontal (render 1280×704): vídeo centrado + fundo desfocado do próprio
     vídeo (visual padrão TikTok).
   - Saída: `final_916.mp4`, único arquivo para as três plataformas.

**Alternativa rejeitada:** croppar a foto-fonte para 9:16 antes do I2V. Risco de cortar o
elemento visual que sustenta a piada e mexeria no caminho de render validado; o blur-pad
não toca no LTX.

O MP4 nativo do render (pré-formatação) continua salvo — é o artefato de validação
(Whisper/silencedetect roda nele, antes da formatação).

## Componente C — Entrega

Por vídeo aprovado, no diretório do run:

```
<run_dir>/<indice>-<slug>/
  final_916.mp4     # pronto para as 3 plataformas
  publish.json      # secao publish completa
  publish.txt       # texto colavel: titulo, descricao, hashtags por bloco
  (mp4 nativo e demais artefatos atuais permanecem como estao)
```

Telegram (opt-in como hoje): o caption do vídeo passa a ser o conteúdo colável
(título + descrição + hashtags), truncado no limite de caption do Telegram (1024 chars).

## Tratamento de erro

- Geração de `publish` falha após re-tentativas ⇒ `status: failed`, pipeline segue,
  `publish.txt` não é escrito, aviso no stdout e no registro do conceito.
- ffmpeg de formatação falha ⇒ o MP4 nativo permanece como entregável; erro registrado;
  run não aborta (o vídeo validado existe).
- Replay de `concepts.json` de versão anterior (sem `publish`) ⇒ seção gerada na hora do
  replay; ausência nunca quebra o parse (compatibilidade coberta por teste).

## Testes

Padrão unittest/pytest existente (`tests/`):

1. Validação do schema `publish`: aceita payload válido; rejeita título longo, contagens
   fora de faixa, campos ausentes; re-tentativa até 2×; `failed` não bloqueia render.
2. Função de formatação 9:16: monta o comando ffmpeg certo para entrada retrato vs
   horizontal (teste de construção de comando; execução real com fixture mínima se barata).
3. Compatibilidade: `concepts.json` da versão anterior (sem `publish`) carrega sem erro.
4. `publish_id` determinístico e estável entre replays do mesmo run.

## Decisões registradas

- Publicação manual; sem API de upload (escopo/risco não compensam nesta fase).
- Metadados em PT-BR (público-alvo atual; piadas já são em PT-BR).
- Um único MP4 9:16 para as três plataformas (não há variação por plataforma no vídeo;
  só a descrição tem variante com hashtags no corpo).
- `publish` gerado pré-render, não é gate do funil.
