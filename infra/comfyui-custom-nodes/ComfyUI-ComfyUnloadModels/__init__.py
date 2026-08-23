"""ComfyUI node that releases loaded Comfy models before the next GPU stage."""

import gc


class ComfyUnloadModels:
    """Pass an image through after unloading every model currently held by ComfyUI."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"image": ("IMAGE",)}}

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "unload"
    CATEGORY = "utils/vram"
    DESCRIPTION = (
        "Moves all loaded ComfyUI models out of VRAM, clears Python garbage and "
        "empties the CUDA cache before passing the image to the next stage."
    )

    def unload(self, image):
        import comfy.model_management as model_management

        model_management.unload_all_models()
        gc.collect()
        model_management.soft_empty_cache(force=True)
        print("[ComfyUnloadModels] Modelos do ComfyUI descarregados da VRAM.")
        return (image,)

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # VRAM state is external to ComfyUI's graph cache, so this must always run.
        return float("nan")


NODE_CLASS_MAPPINGS = {"ComfyUnloadModels": ComfyUnloadModels}
NODE_DISPLAY_NAME_MAPPINGS = {
    "ComfyUnloadModels": "Comfy Unload Models (Image Passthrough)"
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
