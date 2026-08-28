#!/usr/bin/env python3
"""Build workflow 09 from the last validated workflow 08 frontend graph."""

from __future__ import annotations

import copy
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "workflows" / "08-qwen2512-edge-tts-wan22-s2v-ptbr-frontend.json"
TARGET = ROOT / "workflows" / "09-qwen2512-edge-tts-wan22-s2v-duration-presets-frontend.json"


def node_by_id(workflow, node_id):
    return next(node for node in workflow["nodes"] if node["id"] == node_id)


def set_input_link(node, name, link_id):
    next(item for item in node["inputs"] if item["name"] == name)["link"] = link_id


def add_output_link(node, output_index, link_id):
    links = node["outputs"][output_index].get("links")
    if links is None:
        links = []
        node["outputs"][output_index]["links"] = links
    links.append(link_id)


def duration_control_node():
    return {
        "id": 269,
        "type": "DurationPresetControl",
        "pos": [30, 70],
        "size": [460, 82],
        "flags": {},
        "order": 0,
        "mode": 0,
        "inputs": [
            {
                "name": "duration",
                "type": "COMBO",
                "widget": {"name": "duration"},
                "link": None,
            }
        ],
        "outputs": [
            {
                "name": "duration",
                "type": "DURATION_PRESET",
                "links": [527],
                "slot_index": 0,
            }
        ],
        "properties": {"Node name for S&R": "DurationPresetControl"},
        "widgets_values": ["12 segundos"],
        "title": "INPUT 1 · DURAÇÃO ALVO — 8 / 12 / 25 segundos",
    }


def resolution_control_node():
    return {
        "id": 270,
        "type": "VideoResolutionControl",
        "pos": [30, 180],
        "size": [520, 100],
        "flags": {},
        "order": 1,
        "mode": 0,
        "inputs": [
            {
                "name": "resolution",
                "type": "COMBO",
                "widget": {"name": "resolution"},
                "link": None,
            }
        ],
        "outputs": [
            {
                "name": "width",
                "type": "INT",
                "links": [564],
                "slot_index": 0,
            },
            {
                "name": "height",
                "type": "INT",
                "links": [565],
                "slot_index": 1,
            },
        ],
        "properties": {"Node name for S&R": "VideoResolutionControl"},
        "widgets_values": ["368 x 640 — recomendado (9:16)"],
        "title": "INPUT 2 · RESOLUÇÃO DO MP4 — vertical",
    }


def duration_switch_node():
    return {
        "id": 265,
        "type": "DurationPresetLatentSwitch",
        "pos": [4370, 80],
        "size": [340, 180],
        "flags": {},
        "order": 26,
        "mode": 0,
        "inputs": [
            {"name": "duration", "type": "DURATION_PRESET", "link": 527},
            {"name": "video_8s", "type": "LATENT", "link": 528},
            {"name": "video_12s", "type": "LATENT", "link": 529},
            {"name": "video_25s", "type": "LATENT", "link": 560},
        ],
        "outputs": [
            {
                "name": "video_latent",
                "type": "LATENT",
                "links": [561, 562],
                "slot_index": 0,
            },
            {
                "name": "max_frames",
                "type": "INT",
                "links": [563, 566],
                "slot_index": 1,
            },
        ],
        "properties": {"Node name for S&R": "DurationPresetLatentSwitch"},
        "widgets_values": [],
        "title": "Executa somente a duração selecionada (lazy)",
    }


def extension_node(template, node_id, title, pos, seed, links, output_link):
    node = copy.deepcopy(template)
    node["id"] = node_id
    node["title"] = title
    node["pos"] = pos
    node["order"] = node_id - 243
    node["widgets_values"] = [seed, 4, 1, "uni_pc", "simple", 77]
    for item in node["inputs"]:
        item["link"] = links.get(item["name"])
    node["outputs"][0]["links"] = [output_link]
    return node


def main():
    workflow = json.loads(SOURCE.read_text(encoding="utf-8"))
    workflow["id"] = "3d98378b-9f52-5bc0-98e8-c4c6f84285bf"
    workflow["revision"] = 0
    workflow["last_node_id"] = 270
    workflow["last_link_id"] = 566

    nodes = workflow["nodes"]
    template = node_by_id(workflow, 79)

    # Replace the fixed 12-second endpoint with the lazy duration selector.
    node85 = node_by_id(workflow, 85)
    node85["outputs"][0]["links"] = [529, 530]
    set_input_link(node_by_id(workflow, 94), "samples", 561)
    set_input_link(node_by_id(workflow, 95), "samples2", 562)
    set_input_link(node_by_id(workflow, 96), "length", 563)
    node_by_id(workflow, 96)["title"] = "Corte máximo Wan — 8 / 12 / 25 segundos"
    node_by_id(workflow, 95)["title"] = "Concatena a ramificação selecionada"
    node191 = node_by_id(workflow, 191)
    node191["title"] = "Duração fixa — preenche o silêncio com o último frame"
    node191["inputs"].append(
        {
            "localized_name": "frames-alvo",
            "name": "target_frames",
            "type": "INT",
            "widget": {"name": "target_frames"},
            "link": 566,
        }
    )

    # The 8-second preset stops after block 2; 12 seconds stops after block 3.
    add_output_link(node_by_id(workflow, 79), 0, 528)

    extension_specs = [
        (
            266,
            "Bloco 4 · usado somente no preset de 25 s",
            [3350, 80],
            20260827,
            {
                "model": 531,
                "positive": 532,
                "negative": 533,
                "vae": 534,
                "video_latent": 530,
                "audio_encoder_output": 535,
                "ref_image": 536,
                "steps": 537,
                "cfg": 538,
                "length": 539,
            },
            540,
        ),
        (
            267,
            "Bloco 5 · usado somente no preset de 25 s",
            [3690, 80],
            20260828,
            {
                "model": 541,
                "positive": 542,
                "negative": 543,
                "vae": 544,
                "video_latent": 540,
                "audio_encoder_output": 545,
                "ref_image": 546,
                "steps": 547,
                "cfg": 548,
                "length": 549,
            },
            550,
        ),
        (
            268,
            "Bloco 6 · usado somente no preset de 25 s",
            [4030, 80],
            20260829,
            {
                "model": 551,
                "positive": 552,
                "negative": 553,
                "vae": 554,
                "video_latent": 550,
                "audio_encoder_output": 555,
                "ref_image": 556,
                "steps": 557,
                "cfg": 558,
                "length": 559,
            },
            560,
        ),
    ]
    extensions = [extension_node(template, *spec) for spec in extension_specs]

    nodes.extend(extensions)
    nodes.extend([duration_switch_node(), duration_control_node(), resolution_control_node()])

    source_links = {
        54: [531, 541, 551],
        6: [532, 542, 552],
        7: [533, 543, 553],
        39: [534, 544, 554],
        56: [535, 545, 555],
        189: [536, 546, 556],
        103: [537, 547, 557],
        105: [538, 548, 558],
        104: [539, 549, 559],
    }
    for node_id, link_ids in source_links.items():
        for link_id in link_ids:
            add_output_link(node_by_id(workflow, node_id), 0, link_id)

    node93 = node_by_id(workflow, 93)
    set_input_link(node93, "width", 564)
    set_input_link(node93, "height", 565)

    workflow["links"] = [
        link for link in workflow["links"] if link[0] not in {279, 280}
    ]
    workflow["links"].extend(
        [
            [527, 269, 0, 265, 0, "DURATION_PRESET"],
            [528, 79, 0, 265, 1, "LATENT"],
            [529, 85, 0, 265, 2, "LATENT"],
            [530, 85, 0, 266, 4, "LATENT"],
            [531, 54, 0, 266, 0, "MODEL"],
            [532, 6, 0, 266, 1, "CONDITIONING"],
            [533, 7, 0, 266, 2, "CONDITIONING"],
            [534, 39, 0, 266, 3, "VAE"],
            [535, 56, 0, 266, 5, "AUDIO_ENCODER_OUTPUT"],
            [536, 189, 0, 266, 6, "IMAGE"],
            [537, 103, 0, 266, 9, "INT"],
            [538, 105, 0, 266, 10, "FLOAT"],
            [539, 104, 0, 266, 13, "INT"],
            [540, 266, 0, 267, 4, "LATENT"],
            [541, 54, 0, 267, 0, "MODEL"],
            [542, 6, 0, 267, 1, "CONDITIONING"],
            [543, 7, 0, 267, 2, "CONDITIONING"],
            [544, 39, 0, 267, 3, "VAE"],
            [545, 56, 0, 267, 5, "AUDIO_ENCODER_OUTPUT"],
            [546, 189, 0, 267, 6, "IMAGE"],
            [547, 103, 0, 267, 9, "INT"],
            [548, 105, 0, 267, 10, "FLOAT"],
            [549, 104, 0, 267, 13, "INT"],
            [550, 267, 0, 268, 4, "LATENT"],
            [551, 54, 0, 268, 0, "MODEL"],
            [552, 6, 0, 268, 1, "CONDITIONING"],
            [553, 7, 0, 268, 2, "CONDITIONING"],
            [554, 39, 0, 268, 3, "VAE"],
            [555, 56, 0, 268, 5, "AUDIO_ENCODER_OUTPUT"],
            [556, 189, 0, 268, 6, "IMAGE"],
            [557, 103, 0, 268, 9, "INT"],
            [558, 105, 0, 268, 10, "FLOAT"],
            [559, 104, 0, 268, 13, "INT"],
            [560, 268, 0, 265, 3, "LATENT"],
            [561, 265, 0, 94, 0, "LATENT"],
            [562, 265, 0, 95, 1, "LATENT"],
            [563, 265, 1, 96, 2, "INT"],
            [566, 265, 1, 191, 4, "INT"],
            [564, 270, 0, 93, 7, "INT"],
            [565, 270, 1, 93, 8, "INT"],
        ]
    )

    positions = {
        269: ([30, 70], [460, 82]),
        270: ([30, 180], [520, 100]),
        58: ([30, 320], [640, 240]),
        183: ([30, 590], [260, 80]),
        52: ([30, 710], [640, 620]),
        6: ([30, 1370], [640, 180]),
        37: ([770, 70], [390, 82]),
        107: ([770, 180], [390, 90]),
        54: ([770, 300], [390, 60]),
        38: ([770, 400], [390, 106]),
        190: ([770, 530], [390, 110]),
        39: ([770, 680], [390, 60]),
        57: ([770, 780], [390, 70]),
        7: ([1330, 70], [500, 200]),
        104: ([1330, 310], [306, 82]),
        103: ([1330, 420], [306, 82]),
        105: ([1330, 530], [306, 58]),
        56: ([1330, 640], [360, 60]),
        189: ([1330, 730], [400, 82]),
        187: ([1330, 850], [340, 90]),
        93: ([1980, 80], [270, 250]),
        3: ([2280, 80], [280, 440]),
        79: ([2670, 80], [280, 318]),
        85: ([3010, 80], [280, 318]),
        266: ([3350, 80], [280, 318]),
        267: ([3690, 80], [280, 318]),
        268: ([4030, 80], [280, 318]),
        265: ([4370, 80], [340, 180]),
        94: ([4820, 80], [250, 106]),
        95: ([5110, 80], [250, 78]),
        80: ([5110, 200], [250, 60]),
        96: ([5110, 300], [280, 100]),
        191: ([5110, 440], [340, 180]),
        82: ([5510, 80], [310, 100]),
        113: ([5510, 220], [620, 600]),
        188: ([770, 1110], [1060, 280]),
    }
    for node_id, (pos, size) in positions.items():
        node = node_by_id(workflow, node_id)
        node["pos"] = pos
        node["size"] = size

    node_by_id(workflow, 58)["title"] = "INPUT 3 · TEXTO DA FALA → voz PT-BR"
    node_by_id(workflow, 52)["title"] = "INPUT 4 · PROMPT DA IMAGEM → personagem"
    node_by_id(workflow, 6)["title"] = "INPUT 5 · PROMPT DO MOVIMENTO → ações e câmera"
    node_by_id(workflow, 79)["title"] = "Bloco 2 · necessário para 8 / 12 / 25 s"
    node_by_id(workflow, 85)["title"] = "Bloco 3 · necessário para 12 / 25 s"
    node_by_id(workflow, 188)["widgets_values"] = [
        "COMECE PELO BOX 1: escolha duração e resolução; depois edite somente os prompts de fala, imagem e movimento. "
        "8 s executa os blocos 1-2; 12 s executa 1-3; 25 s executa 1-6. O seletor lazy impede que os blocos longos rodem nos presets curtos. "
        "A fala deve caber na duração escolhida; o vídeo mantém o preset preenchendo o silêncio com o último frame. "
        "Se a fala ultrapassar o preset, o node interrompe com uma mensagem clara. Mantenha batch_size=1. "
        "A resolução 368 x 640 é a opção segura para 16 GB; 432 x 768 usa mais VRAM."
    ]

    execution_order = [
        269,
        270,
        38,
        39,
        57,
        104,
        103,
        105,
        37,
        58,
        52,
        188,
        190,
        107,
        56,
        183,
        189,
        6,
        7,
        54,
        187,
        93,
        3,
        79,
        85,
        266,
        267,
        268,
        265,
        94,
        95,
        80,
        96,
        191,
        82,
        113,
    ]
    for order, node_id in enumerate(execution_order):
        node_by_id(workflow, node_id)["order"] = order

    workflow["groups"] = [
        {
            "id": 1,
            "title": "1 · INPUTS PRINCIPAIS — comece aqui e edite estes cinco nós",
            "bounding": [0, 0, 700, 1600],
            "color": "#7a4f35",
            "font_size": 26,
            "flags": {},
        },
        {
            "id": 2,
            "title": "2 · MODELOS E VRAM — normalmente não editar",
            "bounding": [740, 0, 520, 900],
            "color": "#355a7a",
            "font_size": 24,
            "flags": {},
        },
        {
            "id": 3,
            "title": "3 · PREPARAÇÃO — negativo, parâmetros, áudio e retrato",
            "bounding": [1300, 0, 560, 1000],
            "color": "#5f6f3c",
            "font_size": 24,
            "flags": {},
        },
        {
            "id": 4,
            "title": "4 · GUIA RÁPIDO — duração, VRAM e funcionamento lazy",
            "bounding": [740, 1040, 1120, 420],
            "color": "#735b32",
            "font_size": 24,
            "flags": {},
        },
        {
            "id": 5,
            "title": "5 · WAN S2V — bloco inicial",
            "bounding": [1950, 0, 650, 680],
            "color": "#3f789e",
            "font_size": 24,
            "flags": {},
        },
        {
            "id": 6,
            "title": "6 · EXTENSÕES E SELETOR LAZY — só executa até o preset escolhido",
            "bounding": [2640, 0, 2110, 680],
            "color": "#3f6f8e",
            "font_size": 24,
            "flags": {},
        },
        {
            "id": 7,
            "title": "7 · PÓS-PROCESSAMENTO — duração alvo e áudio",
            "bounding": [4790, 0, 660, 700],
            "color": "#4f7280",
            "font_size": 24,
            "flags": {},
        },
        {
            "id": 8,
            "title": "8 · RESULTADO — preview e MP4 salvo",
            "bounding": [5480, 0, 700, 900],
            "color": "#3f7a58",
            "font_size": 24,
            "flags": {},
        },
    ]
    workflow["extra"]["ds"] = {"scale": 0.22, "offset": [50, 110]}

    TARGET.write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
