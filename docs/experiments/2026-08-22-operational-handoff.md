# 2026-08-22 — Handoff operacional do pipeline de memes

Este documento é a pista curta para retomar o projeto sem repetir os erros já
diagnosticados. Ele complementa, não substitui, o histórico detalhado em
`docs/roadmap.md`.

## Objetivo do projeto

Gerar vídeos-meme curtos a partir de posts populares, com estes critérios:

- entendimento imediato;
- ligação clara com o post original;
- piada específica e natural em PT-BR;
- execução audiovisual legível e compartilhável;
- evidências suficientes para avaliação humana.

Qualidade continua acima de volume. Se o material não sustenta uma piada boa ou
um vídeo bom, o comportamento correto é rejeitar, não renderizar por inércia.

## Estado operacional no momento deste handoff

- Pipeline principal: `scripts/daily_reddit_meme_pipeline.py`.
- Fonte de posts decidida pelo usuário: `r/popular`.
- Funil de qualidade existe: schema versionado, estados por etapa, crítica dupla
  de humor, crítico com visão real, checkpoint incremental, pacote de publicação
  e auditoria de chamadas em `execution.generation_calls`.
- Default atual do código para LTX 2.3: `--ltx23-input-mode prompt` + `--ltx23-audio-mode tts`
  + `--tts-backend piper`.
- O default T2V foi portado para o pipeline, mas ainda não tem o mesmo baseline
  humano consolidado que os melhores renders anteriores. O roteiro manual rico
  funcionou melhor que os templates determinísticos; a generalização desse padrão
  ainda é pendência.
- I2V com o grafo oficial `workflows/05-ltx23-official-i2v-audio-api.json`
  continua sendo uma alternativa validada para foto real / imagem de referência,
  mas não deve voltar ao grafo antigo `04`.
- Lab Wan 2.2 A/B concluído em `docs/experiments/20260809-wan22-lab/`; promoção
  ou rollback dependem de veredito humano sobre qualidade.
- A spec de prompt T2V rica está em
  `docs/superpowers/specs/2026-08-16-t2v-quality-prompt-template-design.md`.
  Ela é referência de aprendizado; não alterou o pipeline por si só.
- Existem mudanças locais não consolidadas no repositório no momento deste
  handoff. Não descarte nem sobrescreva sem revisar `git status` e `git diff`.

## Caminhos percorridos

### 1. Contratos e crítica de humor

O pipeline começou permitindo falsos positivos: fallback de crítico inventava
notas, média ponderada deixava passar conceito com ligação fraca com a fonte, e
traduções/paráfrases do post eram aprovadas como se fossem piadas.

Correções aplicadas:

- resposta inválida/vazia/off-schema rejeita; nunca fabrica aprovação;
- schema separa post, piada, avaliações, produção, artefatos e execução;
- estados válidos por etapa: `pending`, `running`, `approved`, `rejected`, `failed`;
- máximo de 3 rodadas e até 5 conceitos por post;
- crítico de visão (`qwen2.5vl:7b`) recebe a imagem real, não só descrição textual;
- punchline descritiva, tradução/paráfrase e falta de narrador com opinião são
  rejeitadas antes de gastar GPU.

### 2. Fonte visual e seleção

`r/popular` tem muito screenshot, placar, tweet, colagem, documento e post de
texto. O problema não era só amostragem: o pipeline precisava rejeitar fontes que
dependem de texto embutido ou que não têm potencial real de movimento.

Correções aplicadas:

- backlog progressivo para `r/popular`;
- `?limit=100` no RSS;
- checkpoint incremental para não perder 15–60 minutos em timeout/kill;
- booleanos explícitos no gate de fonte:
  `embedded_text_carries_meaning`, `multi_photo_collage`,
  `open_scene_no_intrinsic_motion`, `resting_domestic_animal_scene`;
- tetos determinísticos para texto embutido, colagem e cena aberta sem movimento.

### 3. Render ComfyUI/LTX

O erro principal foi tentar consertar por prompt um problema de grafo. O grafo
I2V hand-built `04` usava um regime incompatível com o LoRA distilled e gerava
pseudo-texto/drift. A correção foi usar grafo oficial/exportado e tratar o grafo
como fonte de verdade.

Regras estabelecidas:

- Python pode parametrizar inputs declarados do workflow, mas não manter um
  segundo grafo ComfyUI construído em código.
- `workflows/04-ltx23-native-i2v-audio-api.json` é aposentado; não ressuscitar.
- `workflows/05-ltx23-official-i2v-audio-api.json` é o caminho I2V oficial.
- `workflows/03-ltx23-native-t2v-audio-api.json` é o caminho T2V atual.
- T2V+TTS tem teto seguro de 353 frames / ~14,12s no pipeline atual.
- Áudio nativo do LTX em PT-BR é instável; Piper TTS local é o padrão de produção
  atual quando `--ltx23-audio-mode tts`.

### 4. Pivôs de arquitetura

Três arquiteturas foram aprendidas empiricamente:

1. **Imagem-base re-gerada + I2V:** frágil. A imagem-base pode perder a âncora da
   piada, trocar espécie, inventar gato ou mudar relações espaciais.
2. **Foto real narrada + I2V:** melhor para preservar o ativo visual; validada em
   alguns casos com voz Piper e revisão humana.
3. **T2V com roteiro cinematográfico rico + TTS:** direção atual do código, melhor
   quando o prompt tem beats físicos concretos. O template determinístico ainda é
   mais fraco que prompts manuais ricos.

O ponto central: a qualidade audiovisual não vem só do modelo. Ela depende de
roteiro visual concreto, fonte adequada, prompt sem abstração e veredito humano.

## Acertos que devem ser preservados

- Rejeição explícita é sucesso quando o conceito é fraco.
- Nenhum fallback deve fabricar nota, vídeo, publicação ou aprovação.
- Conceito aprovado pelo funil ainda passa por revisão humana de texto antes de
  render, salvo decisão explícita do usuário em contrário.
- Render só usa workflows ComfyUI versionados/exportados.
- `ffprobe`, Whisper, `silencedetect`, motion/freeze e `human-review.md` são
  evidência; não substituem julgamento humano de qualidade.
- Checkpoint incremental e timeout são obrigatórios em qualquer etapa longa.
- Se uma variável está sendo testada, fixe o resto: imagem, pasta correta da data,
  MP4 anterior apagado quando mudar frames/parâmetros, seed quando aplicável.
- Sempre verificar fila ComfyUI, RAM/VRAM e processos concorrentes antes de render
  longo.
- Sempre passar `--ltx23-width`/`--ltx23-height` explicitamente em experimentos; não dependa
  de default implícito quando estiver comparando engines, orientação ou memória.
- Para downloads/modelos grandes: baixar um arquivo por vez ou garantir nomes
  distintos; validar sha256 antes de usar.

## Erros que não devem se repetir

- Tratar média de nota como aprovação quando mínimos obrigatórios falham.
- Aprovar tradução, paráfrase ou audiodescrição como se fosse punchline.
- Renderizar porque o pipeline “precisa avançar”.
- Ficar ajustando prompt quando o grafo/workflow está no regime errado.
- Reusar `workflow 04`.
- Confiar em `negative prompt` no regime distilled CFG 1.0.
- Medir apenas volume/silêncio e concluir que o texto do áudio não mudou; transcreva
  com Whisper quando a pergunta for conteúdo.
- Usar motion score/freezedetect como gate automático universal.
- Re-renderizar em pasta com MP4 existente e achar que parâmetros novos foram
  aplicados; o pipeline pode reaproveitar vídeo por nome.
- Fixar imagem-base na pasta de data errada; o `output-root` cria subpasta por data
  corrente.
- Rodar loops de status indefinidos ou waiters órfãos. Use timeout e monitoramento
  com estado terminal claro.
- Usar `pkill` com padrão que pode casar a própria shell/comando.
- Instalar dependência efêmera dentro da execução normal; dependência de runtime deve
  estar em imagem/serviço reproduzível.

## Próximos passos recomendados

1. **Escolher a lane de render antes de mexer mais no pipeline.**
   - T2V+TTS atual: continuar a partir da spec de 2026-08-16, validar 2–3 cenas de
     meme com prompts ricos manuais e só depois parametrizar.
   - Wan 2.2: assistir os pares A/B do lab, decidir promover ou rollback.
   - I2V foto real: manter como alternativa/baseline, não como solução universal.

2. **Se seguir com T2V+TTS, não generalizar o template ainda.**
   Primeiro fechar o teste Gerald/2–3 cenas com veredito humano nos três eixos:
   piada, voz, movimento. Depois transformar a biblioteca de blocos da spec em
   código com testes.

3. **Atualizar os docs de alto nível depois da decisão de lane.**
   README e architecture foram ajustados para não apontar cegamente ao caminho
   antigo, mas a documentação pública ainda deve ser consolidada quando o default
   final for decidido.

4. **Revisar o gate de fonte à luz de T2V.**
   Hoje posts sem imagem continuam rejeitados porque falta material visual concreto.
   Isso é defensável, mas a mensagem/critério deve permanecer explícita: T2V não
   precisa de imagem de referência para render, mas precisa de uma cena visual
   confiável para escrever o prompt.

5. **Manter os gates humanos combinados com o usuário.**
   O usuário aprovou menos confirmações, mas pediu parada para mostrar resultados
   positivos nas etapas 4 e 5. Interpretação operacional: seguir sozinho em checks
   baratos/retries dentro do limite, mas parar quando houver candidatos positivos
   de texto/roteiro e quando houver vídeos positivos para avaliação.

## Arquivos que o próximo agente deve ler primeiro

1. `docs/roadmap.md`
2. `docs/architecture.md`
3. `agents/comfyui-specialist.md`
4. `docs/superpowers/specs/2026-08-16-t2v-quality-prompt-template-design.md`
5. `docs/experiments/20260809-wan22-lab/REPORT.md`
6. `docs/experiments/20260809-wan22-lab/PLAN.md`
7. `scripts/daily_reddit_meme_pipeline.py` — especialmente parser defaults,
   `build_video_script()`, `compose_ltx23_segment_prompts()` e render LTX.
