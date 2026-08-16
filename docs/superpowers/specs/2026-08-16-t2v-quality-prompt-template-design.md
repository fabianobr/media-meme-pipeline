# Template de qualidade para prompts T2V (LTX 2.3) — spec

**Data:** 2026-08-16
**Status:** proposto (aguardando revisão do usuário)
**Escopo:** documento de referência / guia de escrita de prompt. **Não altera código do pipeline.**

## Contexto

O pipeline de memes hoje gera prompts T2V a partir de arquétipos determinísticos em
`build_video_script()`/`compose_ltx23_segment_prompts()`. O veredito do usuário em
2026-07-23 (`docs/roadmap.md`, item 20) sobre um candidato desses arquétipos foi "eu não
gostei da continuidade do script, é uma imagem com movimento de câmera bem fraco... capriche
em um roteiro criativo" — nota 7/10 num roteiro escrito manualmente como correção pontual.
Generalizar essa riqueza de roteiro no sistema de arquétipos foi explicitamente adiado
("no futuro... não iniciar sem ele trazer de volta").

Esta sessão é esse retorno. O usuário indicou que o projeto de meme atual "não funcionou e
tem instabilidade" e pediu para abrir a exploração sem se prender às regras já fixadas no
pipeline — inclusive reabrindo a decisão anterior "voz sempre do narrador, nunca lip-sync"
(roadmap, seção "Lane de notícias"). O objetivo desta etapa é **estudar o melhor vídeo já
gerado no ComfyUI local e extrair dele uma biblioteca de blocos de prompt reutilizável**, para
ser aplicada depois — em outra sessão — com inputs parametrizáveis a cenas de meme.

## Fonte de estudo

Workflow ComfyUI confirmado por UUID (`3a1535df-9c7b-4181-8ae4-53ce711a8e7a`, batendo com o
`id` salvo em `Aprovado video_ltx2_3_t2v, lab1 ficou bom.json` no userdata local do ComfyUI):

- Grafo: template oficial LTX 2.3 T2V+Áudio (`ltx-2.3-22b-dev-fp8.safetensors` + LoRA
  distilled `strength_model=0.5`, `CFGGuider cfg=1.0`, sigmas manuais em duas passadas —
  base em meia-resolução + `LTXVLatentUpsampler` x2 + refino). Mesma família do
  `workflows/05-ltx23-official-i2v-audio-api.json` do repo, mas variante T2V (nós de
  I2V-inplace com `bypass=true`) **com áudio/diálogo nativo do modelo**, não mudo+TTS Piper.
- Resolução base 360×720 → upsample x2, 25 fps, 25s (626 frames) — renderizou limpo.
- Prompt-enhancer nativo do LTX (`TextGenerateLTX2Prompt`, LoRA
  `gemma-3-12b-it-abliterated` sobre o encoder de texto) estava **ativo** (switch=true): o
  texto realmente enviado ao modelo foi reescrito automaticamente a partir do roteiro
  original, com pequenos artefatos de corrupção (ex. "1080x1920" virou "1080x192",
  "no on-screen text" virou "no on-00:05:00:00"). **Não replicar esse passo sem
  ressalva** — ele nem sempre preserva o texto fielmente; o roteiro original (escrito à mão)
  é a referência confiável para este guia, não a versão reescrita pela LLM.
- Negativo fixo: `"pc game, console game, video game, cartoon, childish, ugly"`.

### Parâmetros técnicos completos do grafo

Extraídos node a node via `/history` da API do ComfyUI (execução real, não o arquivo salvo
na UI, que guarda outra representação). Referência para qualquer render futuro que reuse este
grafo.

**Componentes/modelos:**
- Checkpoint: `ltx-2.3-22b-dev-fp8.safetensors`
- LoRA distilled (aplicada só no model, via `LoraLoaderModelOnly`):
  `ltx_2.3_22b_distilled_1.1_lora_dynamic_fro09_avg_rank_111_bf16.safetensors`,
  `strength_model=0.5`
- Text encoder (áudio+texto): `gemma_3_12B_it_fp4_mixed.safetensors` via `LTXAVTextEncoderLoader`
- Upscaler latente: `ltx-2.3-spatial-upscaler-x2-1.1.safetensors` via `LTXVLatentUpsampler`
- LoRA de prompt-enhancer (`gemma-3-12b-it-abliterated_lora_rank64_bf16`) presente no grafo mas
  **não usada** neste guia — ver ressalva acima sobre corrupção de texto.

**Amostragem (duas passadas, regime distilled):**
- Passada base: `KSamplerSelect sampler_name=euler`, `ManualSigmas=[0.85, 0.725, 0.4219, 0.0]`
  (3 steps efetivos), `CFGGuider cfg=1.0`, resolução de trabalho = metade da base final (ex.
  180×360 quando a base é 360×720)
- `LTXVLatentUpsampler`: upscale x2 do latente (ex. 180×360 → 360×720)
- Passada de refino: `KSamplerSelect sampler_name=euler`, `ManualSigmas` de 9 valores
  `[1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0]` (8 steps efetivos),
  `CFGGuider cfg=1.0`
- Decode: `VAEDecodeTiled` (`tile_size=768, overlap=64, temporal_size=4096,
  temporal_overlap=4`). **A resolução final do MP4 não é a resolução "aspiracional" declarada
  no bloco de Formato do prompt** (ex. texto diz 1080×1920, arquivo real sai em 320×704) — é
  determinada pelos números de largura/altura base × 2 (upsample), não pelo texto do prompt.

**Áudio nativo (não é TTS):**
`LTXVEmptyLatentAudio` + `LTXVAudioVAELoader`/`LTXVAudioVAEDecode`, processado junto do vídeo
no mesmo latente durante a amostragem (`LTXVConcatAVLatent` antes, `LTXVSeparateAVLatent`
depois de cada passada) e só separado para decodificar cada stream. Diálogo/voz sai desse
mesmo processo de geração, não é sobreposto depois.

**Duração/fps:** fps fixo (usado 25 em todos os testes). `frames = fps × segundos + 1`
(fórmula do grafo, dois `PrimitiveInt` + `ComfyMathExpression`). 25s → 626 frames renderizou
limpo (ver ressalva de teto de duração já registrada acima).

**Negativo:** o texto fixo listado acima está presente no grafo, mas é **inerte** —
`CFGGuider cfg=1.0` desativa a orientação por classificador (sem guidance negativa), achado já
documentado em `docs/experiments/2026-07-07-ltx23-official-template-i2v.md`. Não vale a pena
customizar o negativo neste regime; a defesa contra deriva de cena/espécie deve vir só da
descrição positiva concreta (ver blocos 2/3 abaixo).

Roteiro original (verbatim, é o material que este guia decompõe em blocos):

```
Vertical video, 9:16, 1080x1920. Text-to-video. Single continuous take, one flowing scene.

[CHARACTER: young Brazilian woman, fair skin, blonde hair, attractive, mid-20s, casual light
beige top, warm natural smile, picture-in-picture inset in the upper-left corner of frame,
visible from face to torso and arms, gesturing naturally while speaking, soft studio lighting
on her face]

Scene flow:
0-8s: Background shows a cozy home maker desk, warm daytime light, a smartphone screen
displaying a 3D printing app with a decorative vase model rotating. The woman gestures
toward the phone screen, excited expression.

8-18s: Camera shifts to a close macro shot of a 3D printer actively printing, warm LED
enclosure lighting, shallow depth of field. The nozzle moves in smooth precise passes,
extruding filament layer by layer, the decorative vase gradually taking shape with visible
geometric pattern detail building up layer by layer. Steady, continuous printing motion,
realistic mechanical movement, consistent normal speed.

18-25s: Camera pulls back to a wooden table in soft golden-hour light. The finished vase —
glossy PLA finish, intricate geometric texture — sits center frame. The woman picks it up
with both hands, smiling proudly, slight head tilt, then sets it down gently.

Dialogue (Brazilian Portuguese, natural conversational pace, relaxed and warm, like a real
person talking casually to a friend — not an announcer): "Oi, gente! Hoje eu vou mostrar como
eu imprimi esse vasinho lindo que eu baixei aqui no aplicativo... mandei imprimir e olha só
como ele vai formando as camadas, uma por uma, super detalhado... e prontinho, olha como
ficou lindo! Se você quiser imprimir o seu, o link tá aqui embaixo."

Voice style: single natural human female voice, close-mic clarity, no echo, no reverb, no
robotic or synthetic filtering, no dual/layered voice artifacts, warm and unscripted delivery.

Audio: soft room tone, printer motor hum and stepper motor whirring appearing only during the
printing section, faint natural background music underneath, no echo anywhere in the mix.

Camera: mostly static with subtle slow push-in during each scene transition. Natural motion,
no chaotic camera shake.
```

## Achados sobre o que faz esse prompt funcionar

1. **Cabeçalho de formato explícito primeiro.** Aspecto, resolução e "single continuous
   take" nas duas primeiras frases — ancora o modelo antes de qualquer conteúdo.
2. **Fluxo de cena em beats de ~8-10s, sem cortes.** Cada beat muda enquadramento/câmera,
   mas a ação é contínua (a impressora não "aparece do nada" no beat 2 — a mulher já
   apontava pro app no beat 1). Cada beat combina 4 ingredientes: enquadramento, cenário,
   ação física concreta, detalhe visual fino (textura, padrão geométrico, camada por
   camada). Nada de ação abstrata ("ela demonstra o produto") — sempre o gesto físico
   específico.
3. **Áudio descrito por trecho, não genérico.** "printer motor hum... appearing only during
   the printing section" — o áudio é amarrado ao beat, não é uma linha solta no fim.
4. **Bloco de câmera no final resume a política geral** ("mostly static with subtle slow
   push-in... no chaotic camera shake") — reforça no fim o que já foi implícito nos beats,
   funciona como uma trava contra deriva de câmera.
5. **Quando há fala: bloco de Voice style é uma lista de anti-artefatos, não de estilo
   positivo** ("no echo, no reverb, no robotic... no dual/layered voice artifacts") — é uma
   correção de defeitos conhecidos do modelo, não uma descrição criativa.
6. **Negativo curto e estável**, não uma lista longa — foca em afastar do registro
   "jogo/cartoon" e manter fotorrealismo.

## Biblioteca de blocos

Cada bloco abaixo é uma unidade independente: o que faz, o que preencher, checklist de
qualidade. A ideia é que, na etapa futura de parametrização, cada bloco vire uma função ou
um slot de template com esses mesmos campos.

### 1. Formato
**Função:** ancorar aspecto/resolução/estrutura de tomada antes de qualquer conteúdo.
**Preencher:** aspecto (9:16 vertical é o padrão de meme), resolução, "single continuous
take, one flowing scene" (ou "static single shot" para cenas de um beat só).
**Checklist:** uma frase, sem ambiguidade, sempre a primeira linha do prompt.

### 2. Sujeito
**Função:** descrever quem/o que ocupa o quadro, com detalhe físico suficiente para o
modelo manter consistência entre beats.
**Preencher (variante muda/narrador):** aparência, posição no quadro, expressão inicial,
sem instrução de fala.
**Preencher (variante com diálogo, ver seção de reconsideração abaixo):** os mesmos campos
+ nota de enquadramento tipo "picture-in-picture" ou "close talking to camera" que
justifique lip-sync.
**Checklist:** específico (idade aproximada, roupa, tom de pele, expressão) — sujeitos
genéricos ("a person") produzem deriva maior, conforme já documentado no roadmap para
cenas de animal (item 17-18) e correção de arquétipo (item 19).

### 3. Fluxo de cena
**Função:** o corpo do prompt — a ação real, quadro a quadro no tempo.
**Preencher:** 2-3 beats com timestamp (`0-Xs`, `X-Ys`...), cada um com enquadramento +
cenário + ação física concreta + detalhe visual fino. Beats devem fluir sem corte
implícito — o beat seguinte deve poder ser lido como continuação natural do anterior.
**Checklist:** cada beat tem um verbo de ação física concreto (não "demonstra", "mostra
que", "reage" sozinho — sempre com o gesto explícito); nenhum metadado interno (labels
como "setup/escalada/punchline", timestamps de produção) vaza para o texto — mesma regra
já aplicada em `validate_ltx23_prompts()`.

### 4. Diálogo (opcional — ver reconsideração de guardrail)
**Função:** roteirizar fala atribuída ao sujeito, com lip-sync nativo do LTX.
**Preencher:** idioma, tom de entrega (ex. "conversational, like talking to a friend, not
an announcer"), texto verbatim entre aspas.
**Checklist:** texto curto o bastante para caber na duração do beat correspondente; tom de
entrega descrito, não só o texto.

### 5. Estilo de voz (acompanha o bloco 4)
**Função:** suprimir artefatos conhecidos de síntese de voz do modelo.
**Preencher:** lista fixa de negativos de voz — "no echo, no reverb, no robotic/synthetic
filtering, no dual/layered voice artifacts" — ajustando gênero/timbre da voz descrita.
**Checklist:** é sempre uma lista de restrições, nunca uma descrição de personalidade da
voz (isso já está implícito no tom de entrega do bloco 4).

### 6. Áudio ambiente
**Função:** som de cena, amarrado a cada beat, não solto no fim.
**Preencher:** som por trecho (ex. "stepper motor whirring only during the printing
section"), nota final de limpeza tipo "no echo anywhere in the mix".
**Checklist:** cada som mencionado tem uma fonte visível na cena correspondente — nunca
introduzir som sem imagem que o explique.

### 7. Câmera
**Função:** trava final da política de movimento de câmera, resumindo o que os beats já
implicam.
**Preencher:** uma frase — grau de estabilidade (ex. "mostly static with subtle slow
push-in during transitions") + uma negação explícita de artefato (ex. "no chaotic camera
shake"). Para o teto de qualidade do pipeline hoje, câmera estática é o padrão validado
(`docs/roadmap.md`, "static camera, no push-in" — commit `d3bed04`); usar push-in sutil só
quando o caso justificar.
**Checklist:** sempre a última linha do prompt.

### 8. Negativo
**Função:** afastar registros indesejados (jogo, cartoon, baixa qualidade).
**Preencher:** lista curta e estável — reaproveitar `"pc game, console game, video game,
cartoon, childish, ugly"` como base, ajustando por caso só quando houver um problema
recorrente específico (ex. texto de placar borrado, já documentado no roadmap).
**Checklist:** curto (não uma lista longa genérica) — o exemplo estudado usa só 6 termos.

## Receitas de montagem

**Receita A — meme mudo + narrador (arquitetura atual do pipeline):**
blocos 1, 2, 3, 6, 7, 8. Pula 4/5. O bloco 6 (áudio ambiente) descreve só som de cena, sem
fala — a narração entra depois via TTS Piper sobre o vídeo mudo, fora do prompt do LTX.

**Receita B — sujeito com diálogo nativo do LTX:**
todos os 8 blocos. Usada quando o meme pede o próprio sujeito "falando" a piada/legenda
(reconsideração de guardrail, ver abaixo) em vez de narração over-the-top.

## Reconsideração do guardrail de lip-sync

O roadmap registra, na seção "Lane de notícias" (2026-07-18/19), a decisão "voz sempre do
narrador, nunca lip-sync atribuindo fala à pessoa" como guardrail de design mantido
deliberadamente. Nesta sessão, a pedido explícito do usuário ("parta do princípio de que o
projeto atual de meme não funcionou e tem instabilidade, se abrir para aprender aqui"), essa
decisão está sendo reaberta: a Receita B (diálogo nativo) fica documentada como opção válida
do template, não descartada por essa regra anterior.

**Isto não é ainda uma decisão de produção** — é o registro de que a porta foi reaberta para
exploração. Qual receita usar por padrão no pipeline de memes (A, B, ou por caso) continua
em aberto e deve ser decidido numa etapa futura, quando este template for de fato
parametrizado e testado com cenas de meme reais. Recomendo atualizar
`docs/roadmap.md` com uma entrada curta registrando essa reabertura, para o guardrail
antigo não ser tratado como ainda vigente por engano numa sessão futura.

## Log de testes manuais (lab)

Depois de escrito o guia acima, o usuário pediu para aplicar de fato numa cena de meme, no
mesmo grafo estudado (via API do ComfyUI, script avulso fora do repo — mesmo padrão do
laboratório manual já documentado em `docs/roadmap.md` item 20, nunca hand-rollando grafo
novo, só reusando os nós reais de uma execução histórica desse workflow e trocando os inputs
declarados). Personagem: Gerald, gato já testado no projeto (`docs/experiments/
frozen-gerald-concept-seeds-v2.json`, candidato #3 — piada "chamei o gato de Gerald / agora
ele senta desse jeito / e pede para falar com meu gerente").

**Teste 1 — 9s, Receita B, cena única (sentado encarando a câmera).** Resultado: qualidade
visual excelente (sujeito consistente, sem deriva de espécie/cena), mas cenário pobre — só um
gato parado numa poltrona. Veredito do usuário: "ficou excelente, mas a cena e cenário estão
muito pobres... pode colocar uma história para 25s".

**Teste 2 — 25s, 3 atos (cozinha → balcão com correspondência → close falando pra câmera).**
Resultado: ambiente rico, história com progressão real (andar até a tigela vazia, pular no
balcão, bater na papelada, encarar a câmera), sujeito consistente nos 3 atos. `freezedetect`
do ffmpeg acusou vários trechos "parados" no ato 1 — inspeção frame a frame mostrou que era
falso positivo (o gato estava genuinamente andando); reforça o achado já registrado no
roadmap de que esse detector não serve como veredito automático aqui. Veredito do usuário:
história melhor, mas o andar do gato "ficou parecendo vídeo de IA" (descrição de passos vaga
demais no prompt) — pediu troca do desfecho: em vez de falar direto pra câmera, o gato vai ao
celular, abre um chat estilo WhatsApp com o contato "Gerente" e grava a reclamação como áudio.

**Achado operacional (não é bug do template, é do host):** durante os dois primeiros testes o
ComfyUI caiu duas vezes por falta de RAM do host (32GB total, caiu a ~0,6-2GB livre com
múltiplos renders simultâneos — pelo menos um deles não iniciado por este teste, sugerindo uso
concorrente do mesmo ComfyUI local). Sem correção de código possível aqui; mitigação usada:
laço de espera que só enfileira quando a fila do ComfyUI está vazia e a RAM livre está acima
de um piso seguro antes de cada render.

**Teste 3 — em andamento:** gait do gato reescrito com detalhe cinestésico concreto (marcha
diagonal alternada, quadril balançando, cauda contrabalançando, almofadinha tocando o chão) e
novo desfecho com celular/WhatsApp/áudio de voz.

## Divergências / candidatos a investigação futura (fora de escopo hoje)

- **Teto de duração.** O grafo oficial usado no vídeo estudado rodou 25s/626 frames limpo
  com áudio nativo, bem acima do teto de 353 frames (14,12s) já validado no pipeline para o
  grafo caseiro `workflows/03-ltx23-native-t2v-audio-api.json` (NaN no encode AAC em
  durações maiores, ver roadmap item 20). Candidato a investigação: se o teto do grafo
  caseiro é uma limitação do grafo em si (`LTXVConcatAVLatent`/`LTXVSeparateAVLatent`
  estruturados de forma diferente no template oficial) ou se há outro fator. Não investigado
  aqui — o template desta spec assume o teto atual (≤353 frames) como default seguro.
- **Prompt-enhancer nativo do LTX** (`TextGenerateLTX2Prompt` + LoRA
  `gemma-3-12b-it-abliterated`) — não avaliado se vale a pena adotar no pipeline; o exemplo
  observado mostrou corrupção de texto ao reescrever. Ficou fora deste guia; o roteiro
  escrito à mão continua sendo a referência.

## Fora de escopo (explicitamente, para esta sessão)

- Nenhuma mudança em `build_video_script()`, `compose_ltx23_segment_prompts()` ou em
  qualquer workflow JSON.
- Nenhuma decisão final sobre Receita A vs B como default de produção.
- Nenhuma parametrização em código (Jinja/f-string/função) — isso é o "depois" mencionado
  pelo usuário, uma etapa futura separada.

## Próximos passos

1. Fechar o teste do Gerald (ver "Log de testes manuais" acima) com veredito humano explícito
   nos 3 eixos já usados no projeto (piada, voz, movimento).
2. Atualizar `docs/roadmap.md` com uma entrada curta registrando a reabertura do guardrail
   de lip-sync, o achado do teto de duração do grafo oficial e o resultado do lab do Gerald.
3. Só depois de 2-3 cenas de meme aprovadas manualmente com esta biblioteca de blocos,
   considerar parametrizar em código (fora de escopo até lá).
