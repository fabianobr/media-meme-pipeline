import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = (
    ROOT / "workflows" / "07-qwen2512-kokoro-wan22-s2v-ptbr-frontend.json"
)


def load_workflow():
    return json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))


def test_wan_batch_size_is_one():
    workflow = load_workflow()
    wan = next(node for node in workflow["nodes"] if node["id"] == 93)

    assert wan["type"] == "WanSoundImageToVideo"
    assert wan["widgets_values"][3] == 1


def test_qwen_image_passes_through_forced_comfy_unload_before_wan():
    workflow = load_workflow()
    nodes = {node["id"]: node for node in workflow["nodes"]}
    links = {link[0]: link for link in workflow["links"]}

    assert nodes[189]["type"] == "ComfyUnloadModels"
    assert nodes[52]["outputs"][0]["links"] == [522]
    assert links[522][1:5] == [52, 0, 189, 0]

    for link_id in (252, 261, 262):
        assert links[link_id][1] == 189


def test_ollama_is_flushed_before_qwen_text_encoding():
    workflow = load_workflow()
    qwen = next(
        subgraph
        for subgraph in workflow["definitions"]["subgraphs"]
        if subgraph["id"] == "c3c58f7e-2004-43ae-8b06-a956294bf7f4"
    )
    nodes = {node["id"]: node for node in qwen["nodes"]}
    links = {link["id"]: link for link in qwen["links"]}

    assert nodes[264]["type"] == "OllamaFlushVRAM"
    assert nodes[264]["widgets_values"][1] == 120.0
    assert links[376]["origin_id"] == 219
    assert links[376]["target_id"] == 264
    assert links[314]["origin_id"] == 264
    assert links[315]["origin_id"] == 264
