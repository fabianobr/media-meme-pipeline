import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = (
    ROOT / "workflows" / "08-qwen2512-edge-tts-wan22-s2v-ptbr-frontend.json"
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
    spec = importlib.util.spec_from_file_location("comfy_edge_tts_ptbr", NODE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_workflow_uses_thalita_and_no_kokoro_nodes():
    workflow = load_workflow()
    nodes = {node["id"]: node for node in workflow["nodes"]}

    assert not any(node["type"].startswith("Kokoro") for node in nodes.values())
    assert nodes[58]["type"] == "EdgeTTSBrazilianPortuguese"
    assert nodes[58]["widgets_values"][1:] == [
        "pt-BR-ThalitaMultilingualNeural",
        -3,
        0,
        0,
    ]
    assert nodes[93]["widgets_values"][3] == 1
    assert nodes[189]["type"] == "ComfyUnloadModels"


def test_workflow_trims_frames_to_generated_audio_duration():
    workflow = load_workflow()
    nodes = {node["id"]: node for node in workflow["nodes"]}
    links = {link[0]: link for link in workflow["links"]}

    assert nodes[191]["type"] == "TrimImageSequenceToAudio"
    assert links[524][1:5] == [96, 0, 191, 0]
    assert links[525][1:5] == [58, 0, 191, 1]
    assert links[526][1:5] == [191, 0, 82, 0]


def test_workflow_has_no_dangling_main_graph_links():
    workflow = load_workflow()
    node_ids = {node["id"] for node in workflow["nodes"]}
    link_ids = {link[0] for link in workflow["links"]}

    for link in workflow["links"]:
        assert link[1] in node_ids
        assert link[3] in node_ids

    for node in workflow["nodes"]:
        for node_input in node.get("inputs", []):
            if node_input.get("link") is not None:
                assert node_input["link"] in link_ids
        for node_output in node.get("outputs", []):
            for link_id in node_output.get("links") or []:
                assert link_id in link_ids


def test_trim_node_matches_audio_duration_and_caps_to_available_frames():
    module = load_node_module()

    class FakeTensor:
        def __init__(self, shape):
            self.shape = shape

        def __getitem__(self, item):
            return FakeTensor((len(range(*item.indices(self.shape[0]))), *self.shape[1:]))

    images = FakeTensor((192, 640, 368, 3))
    audio = {"waveform": FakeTensor((1, 1, 240_000)), "sample_rate": 24_000}

    (trimmed,) = module.TrimImageSequenceToAudio().trim(
        images, audio, fps=16.0, end_padding_frames=2
    )
    assert trimmed.shape[0] == 162

    short_images = FakeTensor((80, 640, 368, 3))
    (capped,) = module.TrimImageSequenceToAudio().trim(
        short_images, audio, fps=16.0, end_padding_frames=2
    )
    assert capped.shape[0] == 80
