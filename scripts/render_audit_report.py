"""Render a human-readable markdown audit report from a concepts.json file.

Reads any concepts.json produced by daily_reddit_meme_pipeline.py (schema v2 or later)
and, per video, lists every recorded generation call — stage, backend, model,
parameters, timing, and the exact prompt sent — as reviewable markdown. Never sends or
modifies anything; this is a read-only report over data already persisted.

Usage:
    python3 scripts/render_audit_report.py --concepts-file <path/to/concepts.json>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def format_options(options: Any) -> str:
    if not isinstance(options, dict) or not options:
        return "(nenhum)"
    return ", ".join(f"{key}={value}" for key, value in options.items())


def format_prompt_block(prompt: Any) -> str:
    if isinstance(prompt, str):
        return prompt
    return json.dumps(prompt, ensure_ascii=False, indent=2)


def render_call(call: dict[str, Any]) -> str:
    lines: list[str] = []
    stage = str(call.get("stage") or "?")
    round_number = call.get("round")
    header = f"### {stage}" + (f" (round {round_number})" if round_number is not None else "")
    lines.append(header)
    lines.append("")
    lines.append(f"- backend: `{call.get('backend', '?')}`")
    lines.append(f"- modelo: `{call.get('model', '?')}`")
    lines.append(f"- parametros: {format_options(call.get('options'))}")
    lines.append(f"- estado: `{call.get('state', '?')}`")
    if call.get("elapsed_seconds") is not None:
        lines.append(f"- tempo: {call['elapsed_seconds']}s")
    if call.get("error"):
        lines.append(f"- erro: {call['error']}")
    lines.append("")
    lines.append("<details><summary>Prompt</summary>")
    lines.append("")
    lines.append("```")
    lines.append(format_prompt_block(call.get("prompt")))
    lines.append("```")
    lines.append("")
    lines.append("</details>")
    if call.get("response_preview"):
        lines.append("")
        lines.append("<details><summary>Resposta (preview)</summary>")
        lines.append("")
        lines.append("```")
        lines.append(str(call["response_preview"]))
        lines.append("```")
        lines.append("")
        lines.append("</details>")
    lines.append("")
    return "\n".join(lines)


def render_video_section(index: int, record: dict[str, Any]) -> str:
    post = record.get("post") if isinstance(record.get("post"), dict) else {}
    title = str(post.get("title") or f"video {index}")
    execution = record.get("execution") if isinstance(record.get("execution"), dict) else {}
    calls = execution.get("generation_calls") or execution.get("llm_calls") or []
    lines = [f"## {index}. {title}", ""]
    if not calls:
        lines.append("_Nenhuma chamada de geracao registrada para este video._")
        lines.append("")
        return "\n".join(lines)
    for call in calls:
        if isinstance(call, dict):
            lines.append(render_call(call))
    return "\n".join(lines)


def render_audit_report(document: list[dict[str, Any]]) -> str:
    lines = ["# Relatorio de auditoria de geracao", ""]
    if not document:
        lines.append("_concepts.json vazio._")
        return "\n".join(lines)
    for index, record in enumerate(document, 1):
        if isinstance(record, dict):
            lines.append(render_video_section(index, record))
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--concepts-file", type=Path, required=True, help="Path to a concepts.json to read.")
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Output markdown path. Default: audit-report.md next to --concepts-file.",
    )
    return parser


def main_with_args(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    document = json.loads(args.concepts_file.read_text(encoding="utf-8"))
    if not isinstance(document, list):
        print("ERROR concepts file must contain a JSON array")
        return 1
    report_text = render_audit_report(document)
    output_path = args.output or args.concepts_file.with_name("audit-report.md")
    output_path.write_text(report_text, encoding="utf-8")
    print(f"Audit report written to {output_path}")
    return 0


def main() -> int:
    import sys

    return main_with_args(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
