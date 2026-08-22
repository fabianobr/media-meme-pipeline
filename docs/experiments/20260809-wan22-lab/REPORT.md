# Relatório final — Lab Wan 2.2 vs LTX 2.3

**Período:** 2026-08-09 19:27 → 2026-08-10 07:57 · **Status:** setup e renders completos, aguardando veredito de qualidade do usuário

## Tabela item × etapa

| # | Etapa | Status | Notas |
|---|---|---|---|
| 1 | Plano + rollback registrados | ✅ | `PLAN.md`, memória persistente |
| 2 | Download modelos (~31 GB) | ✅ | 6/6 arquivos, sha256 oficial do HF verificado em todos (`checksums.sha256`) |
| 3 | Instalar `ComfyUI-GGUF` + restart | ✅ | node `UnetLoaderGGUF` confirmado na API |
| 4 | Workflow + smoke test | ✅ | 480×832, 2s, 90s de render, sem OOM |
| 5 | A/B baseline (galáxia + cavalo) | ✅ | mesma imagem-base do `reference-baseline/`, 768×448→ajustado, ~5,3–8 min/clipe |
| 6 | A/B 3 cenas "recuperação muscular" | ✅ | imagens-base geradas localmente (Flux schnell), 6 renders (3 Wan + 3 LTX), todos válidos |
| 7 | Relatório + decisão | 🔄 | este documento; decisão promover/rollback depende do seu julgamento de qualidade |

## Resultados quantitativos (3 cenas finais)

| Cena | Motor | Tempo | RAM pico | VRAM pico | Duração saída |
|---|---|---|---|---|---|
| 1 microlesões | Wan 2.2 | 230,0 s | 24,2/27 GiB | 14186/16311 MiB | 5,06 s |
| 1 microlesões | LTX 2.3 | 250,1 s | 26,0/27 GiB | 15462/16311 MiB | 4,84 s |
| 2 reparo | Wan 2.2 | 230,1 s | 21,5/27 GiB | 14188/16311 MiB | 5,06 s |
| 2 reparo | LTX 2.3 | 250,1 s | 25,3/27 GiB | 15462/16311 MiB | 4,84 s |
| 3 resultado | Wan 2.2 | 230,2 s | 21,1/27 GiB | 14188/16311 MiB | 5,06 s |
| 3 resultado | LTX 2.3 | 250,2 s | 25,2/27 GiB | 15462/16311 MiB | 4,84 s |

Todos os 6 renders da etapa final completaram sem erro e sem OOM. **Ambos os tetos (RAM do container 27 GiB, VRAM da GPU 16 GiB) ficaram raspando** — LTX 2.3 chegou a 26,0/27 GiB de RAM e 15462/16311 MiB de VRAM. Isso é um risco operacional real para uso em produção paralelo a outros serviços (Ollama), não um problema deste lab especificamente.

## Diferenças estruturais entre os dois motores (não é só "qual renderizou melhor")

- **Áudio:** LTX 2.3 gera áudio nativo sincronizado; Wan 2.2 não tem esse recurso — qualquer adoção do Wan exigiria acoplar TTS externo (kokoro/Spark-TTS/CosyVoice já instalados no ComfyUI, mas não testados neste lab).
- **Grafo:** Wan usou um grafo GGUF montado para este lab (`wan22-i2v-gguf-lightning-api.json`, template oficial `video_wan2_2_14B_i2v` + LoRAs Lightning); LTX usou o **grafo checked-in do repositório sem alteração** (`workflows/05`), só parametrizando os inputs declarados — mesma disciplina que o pipeline real usa.
- **Resolução:** Wan renderizou 480×832; LTX renderizou 512×896 (dimensão ajustada para múltiplo de 64, exigência do grafo oficial no passe de metade de resolução). Ambos ~9:16, não idênticos pixel a pixel — atente a isso ao comparar nitidez.
- **Velocidade:** Wan foi ~8% mais rápido por clipe (230s vs 250s) nesta configuração (4 steps Lightning vs 8 steps default do LTX).

## Incidentes de processo (transparência)

1. **Corrupção de arquivo por troca de estratégia de download**: ao migrar de download sequencial para paralelo, um `pkill` matou o comando errado e dois processos escreveram simultaneamente no mesmo arquivo GGUF (10,8 GB), corrompendo-o. Detectado porque o hash sha256 pós-download não bateu com o oficial do HuggingFace — o arquivo foi apagado e baixado de novo do zero antes de qualquer uso. **Nenhum artefato usado nos renders tem hash não verificado.**
2. **Symlink quebrado pré-existente**: `models/vae/qwen_image_vae.safetensors` aponta para `/mnt/models/comfyui/vae/...`, um mount que não existe neste host. Não foi causado por este lab — é um problema de infraestrutura anterior, fora do escopo do rollback. Contornei gerando as 3 imagens-base com `flux1-schnell-fp8.safetensors` (checkpoint local íntegro) em vez de Qwen-Image. Se você usa geração de imagem Qwen em outro fluxo, vale investigar esse mount separadamente.
3. **Loop de status ocioso**: o cron de status a cada 5 min ficou repetindo "sem novidade" por ~9h durante a madrugada em vez de eu avançar a etapa 6. Cancelado ao retomar a sessão; o trabalho real (geração de imagens + 6 renders) foi concluído em ~25 min de execução efetiva depois disso.

## O que decide promover vs. rollback

Isto é julgamento seu, não automatizável (mesmo racional do baseline: motion score automático não discrimina "estático" de "vivo" de forma confiável). Assista aos 5 pares enviados e avalie:

- Movimento do Wan 2.2 é comparável ou melhor que o LTX 2.3 nas mesmas cenas?
- A resolução/nitidez do Wan é aceitável mesmo sendo mais baixa?
- Vale o custo de engenharia de acoplar TTS externo para compensar a falta de áudio nativo?

**Se promover:** próximos passos seriam mover o workflow para `workflows/06-wan22-i2v-gguf-lightning-api.json`, adicionar entrada no `infra/models.lock.yaml` com os sha256 já coletados, e decidir a estratégia de áudio (TTS local já instalado no ComfyUI).

**Se rollback:** comando pronto em `PLAN.md` (seção Rollback) — remove os ~31 GB de modelos e o custom node, reinicia o container. **Só executo mediante sua aprovação explícita.**
