# Lab: Wan 2.2 I2V A14B (GGUF Q5_K_M + Lightning) — A/B vs LTX 2.3

**Data:** 2026-08-09 · **Status:** setup e renders concluídos ([relatório final](REPORT.md)), aguardando veredito do usuário · **Aprovado pelo usuário:** sim (plano + ~31 GB de disco + restart do container comfyui)

## Objetivo

Avaliar se o Wan 2.2 I2V A14B supera o LTX 2.3 em qualidade de movimento e aderência ao
prompt no hardware local (RTX 5060 Ti 16 GB, 32 GB RAM), como possível motor de vídeo
alternativo. Wan não tem áudio nativo — comparação é somente visual; áudio viria de TTS
(nodes kokoro/Spark-TTS/CosyVoice já instalados no ComfyUI).

## Etapas

1. ✅ Preflight: container `comfyui` healthy (v0.30.2), 102 GB livres, nomes/tamanhos confirmados no HF.
2. Download dos modelos (~31 GB) + sha256 (ver tabela abaixo).
3. Instalar custom node `ComfyUI-GGUF` (city96) + `pip install gguf` no container + restart.
4. Workflow API a partir do template oficial `video_wan2_2_14B_i2v` com `UnetLoaderGGUF`
   + LoRAs Lightning (4 steps, cfg 1.0, sem negative prompt efetivo). Smoke test 480×832.
5. A/B baseline: mesmas imagens/prompts do baseline canônico
   (`data/media-pipeline/reference-baseline/`, galáxia + cavalo).
6. A/B final (definido pelo usuário): 3 cenas do roteiro "recuperação muscular"
   (ver `SCRIPT.md` neste diretório), 9:16, Wan vs LTX com os mesmos insumos.
   Somente os prompts visuais vão ao modelo; legenda/locução ficam para composição
   posterior (contrato de prompt literal do pipeline).
7. Relatório item×etapa + decisão: promover (workflow em `workflows/` + entrada no
   `infra/models.lock.yaml`) ou rollback.

## Artefatos criados por este lab

| Item | Caminho |
|---|---|
| Experts GGUF (2×10.79 GB) | `/home/fabiano/AI/ComfyUI/models/diffusion_models/Wan2.2-I2V-A14B-{HighNoise,LowNoise}-Q5_K_M.gguf` |
| Text encoder (6.74 GB) | `/home/fabiano/AI/ComfyUI/models/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors` |
| VAE (0.25 GB) | `/home/fabiano/AI/ComfyUI/models/vae/wan_2.1_vae.safetensors` |
| LoRAs Lightning (2×1.23 GB) | `/home/fabiano/AI/ComfyUI/models/loras/wan2.2_i2v_lightning_4steps_{high,low}_noise_seko_v1.safetensors` |
| Custom node | `/home/fabiano/AI/ComfyUI/custom_nodes/ComfyUI-GGUF` (+ pacote pip `gguf` dentro do container — efêmero, some em rebuild) |
| Workflow do lab | `docs/experiments/20260809-wan22-lab/` (este repo) |
| Saídas de render | `data/media-pipeline/20260809-*-wan22-lab/` (gitignored) |

Fontes (HuggingFace): `QuantStack/Wan2.2-I2V-A14B-GGUF`, `Comfy-Org/Wan_2.2_ComfyUI_Repackaged`,
`lightx2v/Wan2.2-Lightning` (pasta `Wan2.2-I2V-A14B-4steps-lora-rank64-Seko-V1`).
sha256 registrados em `checksums.sha256` neste diretório após o download.

## Rollback (somente sob aprovação do usuário)

```bash
# 1. Remover modelos (~31 GB)
rm /home/fabiano/AI/ComfyUI/models/diffusion_models/Wan2.2-I2V-A14B-HighNoise-Q5_K_M.gguf \
   /home/fabiano/AI/ComfyUI/models/diffusion_models/Wan2.2-I2V-A14B-LowNoise-Q5_K_M.gguf \
   /home/fabiano/AI/ComfyUI/models/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors \
   /home/fabiano/AI/ComfyUI/models/vae/wan_2.1_vae.safetensors \
   /home/fabiano/AI/ComfyUI/models/loras/wan2.2_i2v_lightning_4steps_high_noise_seko_v1.safetensors \
   /home/fabiano/AI/ComfyUI/models/loras/wan2.2_i2v_lightning_4steps_low_noise_seko_v1.safetensors
# 2. Remover custom node e reiniciar
rm -rf /home/fabiano/AI/ComfyUI/custom_nodes/ComfyUI-GGUF
docker restart comfyui
# 3. Saídas de render (opcional, gitignored)
rm -rf data/media-pipeline/20260809-*-wan22-lab/
# O pacote pip `gguf` dentro do container não precisa de remoção (some no próximo rebuild).
# Nada do pipeline LTX nem do docker-compose foi alterado por este lab.
```

## Riscos monitorados

- **OOM exit 137**: RAM do host é 32 GB (não 64); container limitado a 27 GB. Dois experts
  de ~11 GB alternam durante o sampling. Mitigação: smoke test primeiro; se 137, propor ao
  usuário subir limite ou `--cache-none` (nenhuma edição no homelab-ai sem aprovação).
- **VRAM 16 GB compartilhada com Ollama** (`OLLAMA_KEEP_ALIVE=300s` libera sozinho).
