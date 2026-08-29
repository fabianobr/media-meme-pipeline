"""ComfyUI node for lightweight Brazilian Portuguese speech synthesis."""

from __future__ import annotations

import hashlib
import os
import tempfile


PT_BR_VOICES = [
    "pt-BR-ThalitaMultilingualNeural",
    "pt-BR-FranciscaNeural",
    "pt-BR-AntonioNeural",
]

DURATION_PRESETS = {
    "8 segundos": 128,
    "12 segundos": 192,
    "25 segundos": 400,
}

VIDEO_RESOLUTIONS = {
    "368 x 640 — recomendado (9:16)": (368, 640),
    "432 x 768 — mais qualidade e VRAM (9:16)": (432, 768),
}


class EdgeTTSBrazilianPortuguese:
    """Generate an AUDIO value from text without allocating a GPU model."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "text": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "Escreva aqui exatamente o que o personagem deve falar.",
                    },
                ),
                "voice": (PT_BR_VOICES, {"default": PT_BR_VOICES[0]}),
                "rate_percent": (
                    "INT",
                    {"default": -3, "min": -50, "max": 50, "step": 1},
                ),
                "pitch_hz": (
                    "INT",
                    {"default": 0, "min": -50, "max": 50, "step": 1},
                ),
                "volume_percent": (
                    "INT",
                    {"default": 0, "min": -50, "max": 50, "step": 1},
                ),
            }
        }

    RETURN_TYPES = ("AUDIO",)
    RETURN_NAMES = ("audio",)
    FUNCTION = "generate"
    CATEGORY = "audio/tts"
    DESCRIPTION = (
        "Gera fala PT-BR pelo serviço on-line Microsoft Edge TTS. "
        "Não carrega modelo na GPU."
    )

    @staticmethod
    async def _save_audio(text, voice, rate, pitch, volume, output_path):
        import edge_tts

        communicate = edge_tts.Communicate(
            text=text,
            voice=voice,
            rate=rate,
            pitch=pitch,
            volume=volume,
        )
        await communicate.save(output_path)

    async def generate(
        self,
        text,
        voice,
        rate_percent,
        pitch_hz,
        volume_percent,
    ):
        if not text.strip():
            raise ValueError("O texto da fala não pode ficar vazio.")

        try:
            import edge_tts  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "Dependência ausente: instale edge-tts==7.2.7 no Python do ComfyUI."
            ) from exc

        from comfy_extras.nodes_audio import load

        handle, output_path = tempfile.mkstemp(prefix="comfy-edge-tts-", suffix=".mp3")
        os.close(handle)
        try:
            await self._save_audio(
                text=text.strip(),
                voice=voice,
                rate=f"{rate_percent:+d}%",
                pitch=f"{pitch_hz:+d}Hz",
                volume=f"{volume_percent:+d}%",
                output_path=output_path,
            )
            waveform, sample_rate = load(output_path)
            return ({"waveform": waveform.unsqueeze(0), "sample_rate": sample_rate},)
        except Exception as exc:
            raise RuntimeError(
                "Falha ao gerar a voz PT-BR. Verifique a conexão com a internet "
                "e tente novamente."
            ) from exc
        finally:
            try:
                os.unlink(output_path)
            except FileNotFoundError:
                pass

    @classmethod
    def IS_CHANGED(
        cls,
        text,
        voice,
        rate_percent,
        pitch_hz,
        volume_percent,
    ):
        values = "\0".join(
            map(
                str,
                (text, voice, rate_percent, pitch_hz, volume_percent),
            )
        )
        return hashlib.sha256(values.encode("utf-8")).hexdigest()


class TrimImageSequenceToAudio:
    """Fit frames to the speech or to a selected fixed duration."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "audio": ("AUDIO",),
                "fps": ("FLOAT", {"default": 16.0, "min": 1.0, "max": 120.0}),
                "end_padding_frames": (
                    "INT",
                    {"default": 2, "min": 0, "max": 30, "step": 1},
                ),
            },
            "optional": {
                "target_frames": (
                    "INT",
                    {"default": 0, "min": 0, "max": 4096, "step": 1},
                ),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("images",)
    FUNCTION = "trim"
    CATEGORY = "image/video"
    DESCRIPTION = (
        "Corta à fala no modo compatível; com target_frames, preserva a duração "
        "selecionada preenchendo o silêncio com o último frame."
    )

    def trim(self, images, audio, fps, end_padding_frames, target_frames=None):
        waveform = audio["waveform"]
        sample_rate = int(audio["sample_rate"])
        if sample_rate <= 0 or waveform.shape[-1] <= 0:
            raise ValueError("O áudio precisa conter amostras e sample_rate válido.")

        duration_seconds = waveform.shape[-1] / sample_rate
        audio_frames = max(1, round(duration_seconds * fps))
        speech_frames = audio_frames + end_padding_frames

        if target_frames is None or int(target_frames) <= 0:
            wanted_frames = min(int(images.shape[0]), speech_frames)
            return (images[: max(1, wanted_frames)],)

        target_frames = int(target_frames)
        if audio_frames > target_frames:
            raise ValueError(
                f"A fala ocupa aproximadamente {duration_seconds:.2f} s, acima do "
                f"preset de {target_frames / fps:.2f} s. Escolha uma duração maior "
                "ou reduza o texto/velocidade da voz."
            )

        source_frames = max(1, min(int(images.shape[0]), speech_frames))
        source = images[:source_frames]
        if source_frames >= target_frames:
            return (source[:target_frames],)

        import torch

        padding = images[source_frames - 1 : source_frames].repeat(
            (target_frames - source_frames,) + (1,) * (len(images.shape) - 1)
        )
        return (torch.cat((source, padding), dim=0),)


class DurationPresetControl:
    """Expose the duration choice as a prominent workflow input."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "duration": (
                    list(DURATION_PRESETS),
                    {"default": "12 segundos"},
                )
            }
        }

    RETURN_TYPES = ("DURATION_PRESET",)
    RETURN_NAMES = ("duration",)
    FUNCTION = "select"
    CATEGORY = "video/duration"
    DESCRIPTION = "Seleciona o limite de 8, 12 ou 25 segundos."

    def select(self, duration):
        return (duration,)


class VideoResolutionControl:
    """Expose safe vertical Wan resolutions as a prominent workflow input."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "resolution": (
                    list(VIDEO_RESOLUTIONS),
                    {"default": "368 x 640 — recomendado (9:16)"},
                )
            }
        }

    RETURN_TYPES = ("INT", "INT")
    RETURN_NAMES = ("width", "height")
    FUNCTION = "select"
    CATEGORY = "video/duration"
    DESCRIPTION = "Controla a largura e a altura do MP4 gerado pelo Wan."

    def select(self, resolution):
        return VIDEO_RESOLUTIONS[resolution]


class DurationPresetLatentSwitch:
    """Lazily evaluate only the Wan extension chain selected by the user."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "duration": ("DURATION_PRESET",),
                "video_8s": ("LATENT", {"lazy": True}),
                "video_12s": ("LATENT", {"lazy": True}),
                "video_25s": ("LATENT", {"lazy": True}),
            }
        }

    RETURN_TYPES = ("LATENT", "INT")
    RETURN_NAMES = ("video_latent", "max_frames")
    FUNCTION = "select"
    CATEGORY = "video/duration"
    DESCRIPTION = (
        "Escolhe uma cadeia Wan de forma lazy: ramificações não selecionadas "
        "não são renderizadas."
    )

    @staticmethod
    def _input_name(duration):
        if duration == "8 segundos":
            return "video_8s"
        if duration == "12 segundos":
            return "video_12s"
        if duration == "25 segundos":
            return "video_25s"
        raise ValueError(f"Preset de duração inválido: {duration}")

    @classmethod
    def check_lazy_status(
        cls,
        duration,
        video_8s=None,
        video_12s=None,
        video_25s=None,
    ):
        selected_name = cls._input_name(duration)
        selected_value = {
            "video_8s": video_8s,
            "video_12s": video_12s,
            "video_25s": video_25s,
        }[selected_name]
        if selected_value is None:
            return [selected_name]

    def select(
        self,
        duration,
        video_8s=None,
        video_12s=None,
        video_25s=None,
    ):
        selected_name = self._input_name(duration)
        selected_value = {
            "video_8s": video_8s,
            "video_12s": video_12s,
            "video_25s": video_25s,
        }[selected_name]
        if selected_value is None:
            raise RuntimeError(f"A ramificação {selected_name} não foi avaliada.")
        return (selected_value, DURATION_PRESETS[duration])


NODE_CLASS_MAPPINGS = {
    "EdgeTTSBrazilianPortuguese": EdgeTTSBrazilianPortuguese,
    "TrimImageSequenceToAudio": TrimImageSequenceToAudio,
    "DurationPresetControl": DurationPresetControl,
    "VideoResolutionControl": VideoResolutionControl,
    "DurationPresetLatentSwitch": DurationPresetLatentSwitch,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "EdgeTTSBrazilianPortuguese": "Edge TTS — Português do Brasil",
    "TrimImageSequenceToAudio": "Trim Frames to Audio Duration",
    "DurationPresetControl": "Duração do vídeo — 8 / 12 / 25 s",
    "VideoResolutionControl": "Resolução final do vídeo — vertical",
    "DurationPresetLatentSwitch": "Selecionar ramificação por duração (lazy)",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
