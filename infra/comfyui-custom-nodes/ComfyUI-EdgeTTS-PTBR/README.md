# ComfyUI Edge TTS PT-BR

Node pequeno para transformar texto em `AUDIO` usando as vozes brasileiras do
Microsoft Edge TTS. Ele não carrega nenhum modelo na GPU.

## Instalação

Copie esta pasta para `ComfyUI/custom_nodes/` e instale a dependência no mesmo
ambiente Python usado pelo ComfyUI:

```bash
python -m pip install -r custom_nodes/ComfyUI-EdgeTTS-PTBR/requirements.txt
```

Reinicie o ComfyUI. Procure por `Edge TTS — Português do Brasil`.

O node precisa de acesso à internet durante a síntese. A voz recomendada para o
workflow de personagem é `pt-BR-ThalitaMultilingualNeural`, com velocidade `-3%`,
pitch `0 Hz` e volume `0%`.

A mesma extensão fornece `Trim Frames to Audio Duration`, usado para impedir que
o vídeo continue movimentando a boca depois do fim da fala.

Os nodes `Duração do vídeo — 8 / 12 / 25 s` e `Selecionar ramificação por duração
(lazy)` permitem que um único workflow ofereça três limites de duração. Somente a
cadeia Wan escolhida é avaliada; as extensões mais longas não consomem tempo nem
VRAM nos presets curtos.

`Resolução final do vídeo — vertical` centraliza as opções `368 x 640` e
`432 x 768`. A primeira continua sendo o padrão seguro para a GPU de 16 GB.
