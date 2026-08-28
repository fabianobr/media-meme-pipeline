import importlib.util
import json
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = (
    ROOT
    / "workflows"
    / "09-qwen2512-edge-tts-wan22-s2v-duration-presets-frontend.json"
)
NODE_PATH = (
    ROOT
    / "infra"
    / "comfyui-custom-nodes"
    / "ComfyUI-EdgeTTS-PTBR"
    / "__init__.py"
)


def load_workflow():
    return json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))


def load_node_module():
    spec = importlib.util.spec_from_file_location("comfy_duration_presets", NODE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_duration_switch_is_lazy_and_returns_frame_limit():
    module = load_node_module()
    inputs = module.DurationPresetLatentSwitch.INPUT_TYPES()["required"]

    assert inputs["video_8s"][1]["lazy"] is True
    assert inputs["video_12s"][1]["lazy"] is True
    assert inputs["video_25s"][1]["lazy"] is True
    assert module.DurationPresetLatentSwitch.check_lazy_status(
        "8 segundos"
    ) == ["video_8s"]
    assert module.DurationPresetLatentSwitch.check_lazy_status(
        "12 segundos"
    ) == ["video_12s"]
    assert module.DurationPresetLatentSwitch.check_lazy_status(
        "25 segundos"
    ) == ["video_25s"]

    marker = object()
    switch = module.DurationPresetLatentSwitch()
    assert switch.select("8 segundos", video_8s=marker) == (marker, 128)
    assert switch.select("12 segundos", video_12s=marker) == (marker, 192)
    assert switch.select("25 segundos", video_25s=marker) == (marker, 400)


def test_resolution_control_exposes_only_validated_vertical_presets():
    module = load_node_module()
    control = module.VideoResolutionControl()

    assert control.select("368 x 640 — recomendado (9:16)") == (368, 640)
    assert control.select("432 x 768 — mais qualidade e VRAM (9:16)") == (
        432,
        768,
    )


def test_workflow_has_one_lazy_chain_for_three_duration_presets():
    workflow = load_workflow()
    nodes = {node["id"]: node for node in workflow["nodes"]}
    links = {link[0]: link for link in workflow["links"]}

    assert nodes[265]["type"] == "DurationPresetLatentSwitch"
    assert nodes[269]["widgets_values"] == ["12 segundos"]
    assert nodes[270]["widgets_values"] == [
        "368 x 640 — recomendado (9:16)"
    ]

    # 8 s stops after block 2, 12 s after block 3, and 25 s after block 6.
    assert links[528][1:5] == [79, 0, 265, 1]
    assert links[529][1:5] == [85, 0, 265, 2]
    assert links[530][1:5] == [85, 0, 266, 4]
    assert links[540][1:5] == [266, 0, 267, 4]
    assert links[550][1:5] == [267, 0, 268, 4]
    assert links[560][1:5] == [268, 0, 265, 3]

    assert links[563][1:5] == [265, 1, 96, 2]
    assert links[566][1:5] == [265, 1, 191, 4]
    assert links[564][1:5] == [270, 0, 93, 7]
    assert links[565][1:5] == [270, 1, 93, 8]
    assert nodes[93]["widgets_values"][3] == 1
    assert nodes[189]["type"] == "ComfyUnloadModels"
    assert nodes[191]["inputs"][4]["link"] == 566


def test_most_changed_inputs_are_first_and_inside_the_first_box():
    workflow = load_workflow()
    nodes = {node["id"]: node for node in workflow["nodes"]}
    first_group = next(group for group in workflow["groups"] if group["id"] == 1)

    assert "INPUTS PRINCIPAIS" in first_group["title"]
    assert [nodes[node_id]["pos"][0] for node_id in (269, 270, 58, 52, 6)] == [
        30,
        30,
        30,
        30,
        30,
    ]
    assert nodes[269]["title"].startswith("INPUT 1")
    assert nodes[270]["title"].startswith("INPUT 2")
    assert nodes[58]["title"].startswith("INPUT 3")
    assert nodes[52]["title"].startswith("INPUT 4")
    assert nodes[6]["title"].startswith("INPUT 5")


def test_all_links_are_mirrored_and_have_valid_endpoints():
    workflow = load_workflow()
    nodes = {node["id"]: node for node in workflow["nodes"]}
    links = {link[0]: link for link in workflow["links"]}

    assert len(links) == len(workflow["links"])
    for link_id, link in links.items():
        source_id, source_slot, target_id, target_slot = link[1:5]
        assert source_id in nodes
        assert target_id in nodes
        assert link_id in (nodes[source_id]["outputs"][source_slot].get("links") or [])
        assert nodes[target_id]["inputs"][target_slot]["link"] == link_id

    for node in nodes.values():
        for node_input in node.get("inputs", []):
            if node_input.get("link") is not None:
                assert node_input["link"] in links
        for node_output in node.get("outputs", []):
            for link_id in node_output.get("links") or []:
                assert link_id in links


def test_visual_layout_has_no_overlaps_and_every_node_is_grouped():
    workflow = load_workflow()

    for node in workflow["nodes"]:
        x, y = node["pos"]
        width, height = node["size"]
        assert any(
            x >= group["bounding"][0]
            and y >= group["bounding"][1]
            and x + width <= group["bounding"][0] + group["bounding"][2]
            and y + height <= group["bounding"][1] + group["bounding"][3]
            for group in workflow["groups"]
        )

    for index, left in enumerate(workflow["nodes"]):
        lx, ly = left["pos"]
        lw, lh = left["size"]
        for right in workflow["nodes"][index + 1 :]:
            rx, ry = right["pos"]
            rw, rh = right["size"]
            overlaps = (
                lx < rx + rw
                and lx + lw > rx
                and ly < ry + rh
                and ly + lh > ry
            )
            assert not overlaps, f"nodes {left['id']} and {right['id']} overlap"


def test_fixed_duration_fills_silence_and_rejects_overlong_speech():
    module = load_node_module()

    class FakeTensor:
        def __init__(self, shape):
            self.shape = shape

        def __getitem__(self, item):
            if isinstance(item, slice):
                start, stop, step = item.indices(self.shape[0])
                length = len(range(start, stop, step))
            else:
                length = 1
            return FakeTensor((length, *self.shape[1:]))

        def repeat(self, repeats):
            return FakeTensor((repeats[0], *self.shape[1:]))

    fake_torch = types.SimpleNamespace(
        cat=lambda tensors, dim=0: FakeTensor(
            (sum(tensor.shape[0] for tensor in tensors), *tensors[0].shape[1:])
        )
    )
    previous_torch = sys.modules.get("torch")
    sys.modules["torch"] = fake_torch
    try:
        images = FakeTensor((481, 640, 368, 3))
        short_audio = {
            "waveform": FakeTensor((1, 1, 176_000)),
            "sample_rate": 16_000,
        }
        (fitted,) = module.TrimImageSequenceToAudio().trim(
            images, short_audio, fps=16.0, end_padding_frames=2, target_frames=400
        )
        assert fitted.shape[0] == 400

        exact_audio = {
            "waveform": FakeTensor((1, 1, 400_000)),
            "sample_rate": 16_000,
        }
        (exact,) = module.TrimImageSequenceToAudio().trim(
            images, exact_audio, fps=16.0, end_padding_frames=2, target_frames=400
        )
        assert exact.shape[0] == 400

        long_audio = {
            "waveform": FakeTensor((1, 1, 416_000)),
            "sample_rate": 16_000,
        }
        try:
            module.TrimImageSequenceToAudio().trim(
                images, long_audio, fps=16.0, end_padding_frames=2, target_frames=400
            )
        except ValueError as exc:
            assert "acima do preset" in str(exc)
        else:
            raise AssertionError("fala acima do preset deveria interromper o workflow")
    finally:
        if previous_torch is None:
            del sys.modules["torch"]
        else:
            sys.modules["torch"] = previous_torch
