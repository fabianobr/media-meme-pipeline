# 2026-07-07 — LTX 2.3 I2V: causa raiz do pseudo-texto e adoção do template oficial

## Contexto

O grafo I2V anterior (`workflows/04-ltx23-native-i2v-audio-api.json`) gerava MP4 tecnicamente
válido, mas reprovado no gate visual: pseudo-texto, texturas degeneradas e drift. Seis rodadas
de prompt tuning não mudaram o resultado.

## Diagnóstico

O grafo 04 foi derivado à mão do grafo T2V e comparado contra o template oficial
`video_ltx2_3_i2v` (comfyui-workflow-templates 0.9.94, instalado junto do ComfyUI 0.24.0):

| Parâmetro | Grafo 04 (reprovado) | Template oficial |
|---|---|---|
| Resolução base | 384×224 | metade da final (ex.: 640×360) |
| Passe de refine | nenhum | upscale latente espacial ×2 + 3 steps |
| Guidance | MultimodalGuider, CFG 3.0/7.0, STG, perturbed attention | CFGGuider `cfg=1.0`, euler puro |
| Schedule | LTXVScheduler 8 steps | ManualSigmas distilled (8 base + 3 refine) |
| Condicionamento I2V | ConditionOnly strength 0.92 | Inplace 0.7 (base) / 1.0 (refine) |

Causa raiz: regime híbrido inválido — LoRA distilled com CFG>1 + STG, a um quarto da
resolução de referência e sem refine. Não era problema de prompt.

## Mudanças

- `workflows/05-ltx23-official-i2v-audio-api.json`: conversão fiel do template oficial para
  formato API (enhancer Gemma de prompt omitido; `ResizeImageMaskNode` v3 substituído por
  `ImageScale` center-crop).
- `scripts/daily_reddit_meme_pipeline.py`: I2V passa a parametrizar o grafo 05; defaults
  1280×720 @ 25 fps; T2V continua no grafo 03 sem mudança.
- `scripts/ltx23_visual_smoke_test.py`: smoke visual barato — render curto, ffprobe e stills
  em 0/25/50/75/95% para inspeção de pseudo-texto/drift antes de render real.
- `infra/models.lock.yaml`: pinado o LoRA realmente usado
  (`ltx_2.3_22b_distilled_1.1_lora_dynamic_fro09_avg_rank_111_bf16`, Comfy-Org/ltx-2.3) no
  lugar do `-384` (não referenciado por nenhum grafo) e adicionado o upscaler espacial
  `ltx-2.3-spatial-upscaler-x2-1.1` (Lightricks/LTX-2.3). sha256 verificados contra os
  arquivos locais.

## Smoke visual (gate)

Comando:

```bash
python3 scripts/ltx23_visual_smoke_test.py \
  --image data/media-pipeline/e2e-gerald-calibrated-consensus-stage5c/2026-07-05/01-cats-with-very-human-names-base.png \
  --output data/media-pipeline/ltx23-official-smoke/2026-07-07/gerald-smoke.mp4 \
  --seconds 2 --seed 2028070701
```

Resultado técnico: 1280×704, h264 + aac, 51 frames, sem OOM na RTX 5060 Ti 16 GB.

Resultado visual (stills 0/50/75%): sem pseudo-texto, sem marcas de UI, identidade e cena
estáveis, movimento coerente com o prompt (inclinação lenta em direção à câmera). O pote com
pseudo-texto presente na imagem-base 1:1 saiu do enquadramento com o center-crop 16:9.

Observações:

- A imagem de referência domina a composição: o prompt descrevia outro cenário e o modelo
  preservou o da referência — bom para ancoragem I2V.
- `height` final sai 704 (arredondamento interno de 720); tratar 1280×704 como resolução
  efetiva.
- Frames aceitos fora do contrato 8n+1 (51 e 126 passaram na validação do node).

## Envelope de VRAM (RTX 5060 Ti 16 GB)

| Render | Refine (final) | Frames | Resultado |
|---|---|---|---|
| Smoke | 1280×704 | 51 | OK |
| Gerald tentativa 1 | 1280×704 | 129 | OOM no refine (faltaram 234 MiB) |
| Gerald tentativa 2 | 1024×576 | 129 | OK técnico; reprovado visual (prompt, ver abaixo) |
| Gerald tentativa 3 | 1024×576 | 129 | OK — aprovado pelo usuário |
| Gerald 10 s | 768×448 | 257 | processo do ComfyUI morto logo após `got prompt`, sem traceback — suspeita de OOM de **RAM do host** (text encoder cpu ~11 GB + ~19 GB offload + ativações de 257 frames > 29 GB) |
| Gerald 8 s | 768×448 | 201 | OK — 8,04 s, stills limpos (0/2/4/6/7,8 s), áudio com pico −1,3 dB |

Teto prático em 16 GB VRAM + 29 GB RAM: **~201 frames (8 s) @ 768×448** ou
**129 frames (5 s) @ 1024×576** numa tacada só.

Além do teto de VRAM no refine, existe um teto de **RAM do host** para frames longos.
Para >8 s numa tacada só, o caminho é extensão por segmentos (`LTXVExtendSampler`),
não frame count maior.

O passe base (meia resolução) coube em todas; o limite é o refine em resolução final.
Para 5 s (129 frames) em 16 GB, usar `--ltx23-width 1024 --ltx23-height 576`.
1280×720 fica reservado para clipes ≤ ~2 s ou GPUs maiores.

## Contrato de prompt no regime CFG 1.0

A tentativa 2 renderizou limpa no primeiro frame e colapsou em ~2,5 s para uma colagem de
"pôsteres de meme" com pseudo-texto e legendas falsas. Causa: o prompt compilado pelo
pipeline ainda era do regime antigo (CFG 3.0), onde o negative prompt suprimia texto.
Com CFG 1.0 o negative é **inerte**, e duas coisas no prompt positivo induziram os artefatos:

1. a piada literal em CAIXA ALTA no meio do prompt (lida como "texto de meme na tela");
2. a muralha de negações ("No cuts, captions, subtitles, logos, posters...") — nomear
   artefatos proibidos no prompt positivo os injeta, não os remove.

Correção em `compose_ltx23_segment_prompts` (coberta por teste):

- fala entra como voice-over citado em minúsculas:
  `Audio: a calm adult Brazilian Portuguese voice-over says with dry comic timing: "..."`;
- zero sentenças negativas no prompt positivo; encerramento apenas descritivo
  ("One continuous shot", "Quiet indoor room tone");
- o script genérico não usa mais texto-instrução como `dialogue` (era a origem do antigo
  fallback "narration only" vazando como fala).

Regra geral: **no regime distilled, tudo que o prompt positivo menciona tende a aparecer.**
O negative prompt é mantido apenas como metadado/documentação.

## Resultado — Gerald tentativa 3 (prompt corrigido)

`data/media-pipeline/e2e-gerald-official-i2v/2026-07-07/01-cats-with-very-human-names.mp4`
(1024×576, 129 frames @ 25 fps, 5,16 s, h264 + aac, seed base 2028070705).

Inspeção de stills (0 / 1,3 / 2,6 / 3,9 / 4,9 s): sem pseudo-texto, sem colagem, sem drift;
cena e identidade estáveis; o gato articula a fala (o LTX aplicou lip-sync ao voice-over —
avaliar em revisão humana se o gato falante serve ou se a narração deve ficar off-screen).
Áudio não-silencioso com dinâmica de fala (pico −8,1 dB).

## Próximo gate

Revisão humana do MP4 acima (humor, inteligibilidade do PT-BR, gato falante vs narração
off-screen). Sem aprovação humana, o estágio 5 continua aberto. Se o gato falante for
indesejado, o próximo experimento é reformular a sentença de áudio para narrador
explicitamente fora de cena e sem sujeito visível falando.
