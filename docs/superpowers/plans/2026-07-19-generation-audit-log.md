# Generation Audit Log Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every video's `concepts.json` records, per generation stage, the exact prompt sent, the model, and the parameters used — generalizing the existing (humor-only, prompt-less) `execution.llm_calls` mechanism into `execution.generation_calls` covering vision description, the source-suitability gate, the humor writer/critics, publish-metadata generation, and the LTX render prompt/params.

**Architecture:** A single shared helper (`timed_generation_request`) wraps every Ollama `/api/chat` call site, appending a call record (backend, stage, model, prompt with embedded images redacted, options, timing, state, response preview) to a caller-supplied list. The five call sites thread that list through to wherever `concept["execution"]["generation_calls"]` lives — for the two stages that run before a concept object exists (vision description, source gate), the list is collected per-post and merged into the concept the moment it is created inside `generate_concepts()`. The LTX render stage isn't an Ollama call, so it appends directly using the render params already computed by `render_ltx_video_meme`. A new standalone script, `scripts/render_audit_report.py`, turns any `concepts.json` into a readable `audit-report.md`.

**Tech Stack:** Python 3 stdlib only (`json`, `copy.deepcopy`, `datetime`, `time`), existing `request_json` HTTP helper, unittest/pytest.

## Global Constraints

- No `CONCEPT_SCHEMA_VERSION` bump — `execution` is already a free-form dict in the persistence contract (only `execution.state` is validated), so adding/renaming keys inside it does not change the schema.
- Rename the existing `execution.llm_calls` key to `execution.generation_calls` everywhere (writer + both critics) — this is a deliberate breaking rename of an internal field, not a new parallel list. Update the two existing tests in `tests/test_configuration.py` that assert on `llm_calls`.
- Base64 images embedded in a stored prompt must be redacted to `[image omitted, N base64 chars]` — never write raw image bytes into `concepts.json`.
- `scripts/daily_reddit_meme_pipeline.py` must remain importable with no side effects at import time.
- Existing call sites outside the render pipeline (`scripts/reddit_popular_curation.py`, which calls `describe_source_image`/`assess_source_suitability` directly) must keep working unmodified — new parameters are optional with safe defaults.
- Never change what is sent to a model — this is instrumentation only. No prompt text, model choice, or parameter value changes.
- `scripts/render_audit_report.py` must not error on old `concepts.json` files that have no `generation_calls`/`llm_calls` at all (schema v2/v3 pre-audit-log).

---

### Task 1: Core helper — `redact_prompt_images` + `timed_generation_request`

**Files:**
- Modify: `scripts/daily_reddit_meme_pipeline.py` (new functions, placed right after `extract_json_object`, ~line 1117 — before `generate_publish_metadata`)
- Test: `tests/test_generation_audit_log.py` (new)

**Interfaces:**
- Produces: `redact_prompt_images(messages: Any) -> Any` — returns `messages` unchanged if not a list; otherwise a deep copy with every `message["images"]` list replaced by placeholder strings.
- Produces: `timed_generation_request(calls: list[dict[str, Any]], *, backend: str, stage: str, payload: dict[str, Any], timeout: int, url: str, round_number: int | None = None) -> dict[str, Any]` — appends a call record to `calls`, performs the HTTP call via the existing `request_json`, returns the raw response (same shape callers already expect from `request_json`), re-raises on failure after recording it.
- Consumes: `request_json` (existing, `scripts/daily_reddit_meme_pipeline.py:107`), `deepcopy` (already imported at module level), `time`, `datetime` (already imported).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_generation_audit_log.py`:

```python
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import daily_reddit_meme_pipeline as pipeline  # noqa: E402


class RedactPromptImagesTests(unittest.TestCase):
    def test_non_list_passthrough(self) -> None:
        self.assertEqual(pipeline.redact_prompt_images("plain text"), "plain text")
        self.assertIsNone(pipeline.redact_prompt_images(None))

    def test_redacts_images_in_messages(self) -> None:
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "describe this", "images": ["QUJDRA=="]},
        ]
        redacted = pipeline.redact_prompt_images(messages)
        self.assertEqual(redacted[0], {"role": "system", "content": "sys"})
        self.assertEqual(redacted[1]["content"], "describe this")
        self.assertEqual(redacted[1]["images"], ["[image omitted, 8 base64 chars]"])
        # original untouched
        self.assertEqual(messages[1]["images"], ["QUJDRA=="])

    def test_messages_without_images_untouched(self) -> None:
        messages = [{"role": "user", "content": "hi"}]
        self.assertEqual(pipeline.redact_prompt_images(messages), messages)


class TimedGenerationRequestTests(unittest.TestCase):
    def test_success_records_prompt_model_options_and_response(self) -> None:
        calls: list[dict] = []
        payload = {
            "model": "gemma4:31b",
            "messages": [{"role": "user", "content": "hello"}],
            "options": {"temperature": 0.7, "num_predict": 1500},
        }
        with patch.object(
            pipeline, "request_json", return_value={"message": {"content": "world"}}
        ) as mocked:
            response = pipeline.timed_generation_request(
                calls, backend="ollama", stage="writer", round_number=1,
                payload=payload, timeout=600, url="http://localhost:11434/api/chat",
            )
        self.assertEqual(response, {"message": {"content": "world"}})
        mocked.assert_called_once_with(
            "POST", "http://localhost:11434/api/chat", json=payload, timeout=600
        )
        self.assertEqual(len(calls), 1)
        record = calls[0]
        self.assertEqual(record["backend"], "ollama")
        self.assertEqual(record["stage"], "writer")
        self.assertEqual(record["round"], 1)
        self.assertEqual(record["model"], "gemma4:31b")
        self.assertEqual(record["prompt"], payload["messages"])
        self.assertEqual(record["options"], {"temperature": 0.7, "num_predict": 1500})
        self.assertEqual(record["timeout_seconds"], 600)
        self.assertEqual(record["state"], "completed")
        self.assertEqual(record["response_chars"], 5)
        self.assertEqual(record["response_preview"], "world")
        self.assertIn("elapsed_seconds", record)
        self.assertIn("started_at", record)
        self.assertIn("finished_at", record)

    def test_redacts_images_before_storing(self) -> None:
        calls: list[dict] = []
        payload = {
            "model": "qwen2.5vl:7b",
            "messages": [{"role": "user", "content": "look", "images": ["QUJDRA=="]}],
            "options": {},
        }
        with patch.object(pipeline, "request_json", return_value={"message": {"content": "ok"}}):
            pipeline.timed_generation_request(
                calls, backend="ollama", stage="vision_description",
                payload=payload, timeout=60, url="http://localhost:11434/api/chat",
            )
        self.assertEqual(calls[0]["prompt"][0]["images"], ["[image omitted, 8 base64 chars]"])

    def test_failure_records_error_and_reraises(self) -> None:
        calls: list[dict] = []
        payload = {"model": "m", "messages": [], "options": {}}
        with patch.object(pipeline, "request_json", side_effect=ValueError("offline")):
            with self.assertRaises(ValueError):
                pipeline.timed_generation_request(
                    calls, backend="ollama", stage="critic_1",
                    payload=payload, timeout=10, url="http://x/api/chat",
                )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["state"], "failed")
        self.assertIn("offline", calls[0]["error"])
        self.assertNotIn("response_preview", calls[0])

    def test_round_number_omitted_when_none(self) -> None:
        calls: list[dict] = []
        payload = {"model": "m", "messages": [], "options": {}}
        with patch.object(pipeline, "request_json", return_value={"message": {"content": ""}}):
            pipeline.timed_generation_request(
                calls, backend="ollama", stage="publish_metadata",
                payload=payload, timeout=10, url="http://x/api/chat",
            )
        self.assertNotIn("round", calls[0])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `. .venv/bin/activate && python3 -m pytest tests/test_generation_audit_log.py -v`
Expected: FAIL with `AttributeError: ... has no attribute 'redact_prompt_images'`

- [ ] **Step 3: Implement**

In `scripts/daily_reddit_meme_pipeline.py`, right after `extract_json_object` (search for that function; it currently ends just before `def humor_candidate_issues`, ~line 1117):

```python
def redact_prompt_images(messages: Any) -> Any:
    """Deep-copies a chat messages list with any embedded base64 images replaced by a
    size placeholder, so a stored prompt never carries raw image bytes."""

    if not isinstance(messages, list):
        return messages
    redacted = deepcopy(messages)
    for message in redacted:
        if isinstance(message, dict) and isinstance(message.get("images"), list):
            message["images"] = [
                f"[image omitted, {len(image)} base64 chars]" if isinstance(image, str) else "[image omitted]"
                for image in message["images"]
            ]
    return redacted


def timed_generation_request(
    calls: list[dict[str, Any]],
    *,
    backend: str,
    stage: str,
    payload: dict[str, Any],
    timeout: int,
    url: str,
    round_number: int | None = None,
) -> dict[str, Any]:
    """Ollama request wrapper shared by every stage that calls a local model. Records
    the exact prompt (images redacted), model, and options into `calls` before the
    request, and timing/outcome after — regardless of whether validation downstream of
    the response succeeds."""

    call_record: dict[str, Any] = {
        "backend": backend,
        "stage": stage,
        "model": str(payload.get("model") or ""),
        "prompt": redact_prompt_images(payload.get("messages")),
        "options": deepcopy(payload.get("options") or {}),
        "timeout_seconds": timeout,
        "started_at": datetime.now().astimezone().isoformat(),
        "state": "running",
    }
    if round_number is not None:
        call_record["round"] = round_number
    calls.append(call_record)
    started = time.monotonic()
    try:
        response = request_json("POST", url, json=payload, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - failure itself is the useful signal here
        call_record["state"] = "failed"
        call_record["error"] = str(exc)
        raise
    else:
        call_record["state"] = "completed"
        content = str((response.get("message") or {}).get("content") or "")
        call_record["response_chars"] = len(content)
        call_record["response_preview"] = content[:500]
    finally:
        call_record["elapsed_seconds"] = round(time.monotonic() - started, 3)
        call_record["finished_at"] = datetime.now().astimezone().isoformat()
    return response
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_generation_audit_log.py -v`
Expected: PASS (all 6)

Run: `python3 -m pytest tests/`
Expected: PASS (no regressions)

- [ ] **Step 5: Commit**

```bash
git add tests/test_generation_audit_log.py scripts/daily_reddit_meme_pipeline.py
git commit -m "feat: shared timed_generation_request helper for the audit log"
```

---

### Task 2: Wire the humor writer/critics — rename `llm_calls` to `generation_calls`

**Files:**
- Modify: `scripts/daily_reddit_meme_pipeline.py:1378-1428` (`improve_humor_concept`'s `timed_humor_request` closure and its 2 call sites, ~1565 and ~1654)
- Modify: `tests/test_configuration.py:190-215` (existing assertions on `llm_calls`)
- Test: `tests/test_generation_audit_log.py` (add class)

**Interfaces:**
- Consumes: `timed_generation_request` (Task 1)
- Produces: `concept["execution"]["generation_calls"]` populated by the humor stage with `stage` values `"writer"`, `"critic_1"`, `"critic_2"` (unchanged from today's `"writer"`/`"critic_N"` naming) plus the new `prompt`/`options` fields from Task 1.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_generation_audit_log.py`:

```python
class HumorWiringTests(unittest.TestCase):
    def test_writer_and_critic_calls_carry_prompt_and_options(self) -> None:
        import json as jsonlib

        post = pipeline.reddit.RedditPost(
            subreddit="popular", id="t3_x", title="A cat", author="/u/demo",
            url="https://example.com", updated="2026-07-19T00:00:00+00:00",
            summary="", rank=1, media_type="image", media_url="",
        )
        concept = {"top_text": "A", "bottom_text": "B", "meme_logic": "c"}
        candidates = [
            {"id": i, "mechanic": "contrast", "setup": "A", "escalation": "B",
             "punchline": "C", "comic_turn": "D", "scene_payoff": "E"} for i in range(1, 6)
        ]
        review = {"approved": True, "winner_id": 1,
                  "scores": {"source_fit": 9, "natural_ptbr": 9, "surprise": 9, "laugh": 9, "visual_payoff": 9},
                  "reason": "ok"}
        responses = [
            {"message": {"content": jsonlib.dumps({"candidates": candidates})}},
            {"message": {"content": jsonlib.dumps(review)}},
        ]
        with patch.object(pipeline, "request_json", side_effect=responses):
            result = pipeline.improve_humor_concept(
                post, concept, "writer-model", 5, "a cat", critic_model="critic-model"
            )
        calls = result["execution"]["generation_calls"]
        self.assertEqual([c["stage"] for c in calls], ["writer", "critic_1"])
        self.assertEqual(calls[0]["model"], "writer-model")
        self.assertIsInstance(calls[0]["prompt"], list)
        self.assertEqual(calls[0]["options"], {"temperature": 0.85, "num_predict": 1500})
        self.assertEqual(calls[1]["model"], "critic-model")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_generation_audit_log.py::HumorWiringTests -v`
Expected: FAIL — `improve_humor_concept` still writes `llm_calls`, not `generation_calls`

- [ ] **Step 3: Implement**

In `scripts/daily_reddit_meme_pipeline.py`, inside `improve_humor_concept`:

Replace (line ~1391-1428, the `execution`/`llm_calls` setup and the entire `timed_humor_request` closure):

```python
    execution = concept.setdefault("execution", {"state": "pending", "attempts": {}})
    llm_calls = execution.setdefault("llm_calls", [])
```

and the closure through its final `return response` — with:

```python
    execution = concept.setdefault("execution", {"state": "pending", "attempts": {}})
    generation_calls = execution.setdefault("generation_calls", [])
```

(delete the `timed_humor_request` nested function entirely).

Replace the writer call site (~line 1565):

```python
                writer_data = timed_humor_request(
                    "writer",
                    round_number,
                    {
                    "model": model,
                    "stream": False,
                    "think": False,
                    "format": candidates_schema,
                    "messages": [
                        {"role": "system", "content": "Voce e um redator de humor brasileiro conciso e observacional."},
                        {"role": "user", "content": writer_prompt},
                    ],
                    "options": {"temperature": 0.85, "num_predict": 1500},
                    },
                )
```

with:

```python
                writer_data = timed_generation_request(
                    generation_calls,
                    backend="ollama",
                    stage="writer",
                    round_number=round_number,
                    payload={
                        "model": model,
                        "stream": False,
                        "think": False,
                        "format": candidates_schema,
                        "messages": [
                            {"role": "system", "content": "Voce e um redator de humor brasileiro conciso e observacional."},
                            {"role": "user", "content": writer_prompt},
                        ],
                        "options": {"temperature": 0.85, "num_predict": 1500},
                    },
                    timeout=timeout,
                    url=f"{OLLAMA_URL}/api/chat",
                )
```

Replace the critic call site (~line 1654):

```python
                critic_data = timed_humor_request(
                    f"critic_{critic_index}",
                    round_number,
                    {
                        "model": active_critic_model,
                        "stream": False,
                        "think": False,
                        "format": review_schema,
                        "messages": [
                            {"role": "system", "content": "Voce elimina memes fracos antes que gastem tempo de renderizacao."},
                            critic_user_message,
                        ],
                        "options": {"temperature": 0.1, "num_predict": 900},
                    },
                )
```

with:

```python
                critic_data = timed_generation_request(
                    generation_calls,
                    backend="ollama",
                    stage=f"critic_{critic_index}",
                    round_number=round_number,
                    payload={
                        "model": active_critic_model,
                        "stream": False,
                        "think": False,
                        "format": review_schema,
                        "messages": [
                            {"role": "system", "content": "Voce elimina memes fracos antes que gastem tempo de renderizacao."},
                            critic_user_message,
                        ],
                        "options": {"temperature": 0.1, "num_predict": 900},
                    },
                    timeout=timeout,
                    url=f"{OLLAMA_URL}/api/chat",
                )
```

In `tests/test_configuration.py`, update the 5 `llm_calls` references to `generation_calls`:

```python
        self.assertEqual(result["execution"]["generation_calls"][0]["stage"], "writer")
        self.assertEqual(result["execution"]["generation_calls"][0]["state"], "failed")
        self.assertIn("critic offline", result["execution"]["generation_calls"][0]["error"])
```

and:

```python
        self.assertEqual(len(result["execution"]["generation_calls"]), 6)
        self.assertTrue(all(call["state"] == "completed" for call in result["execution"]["generation_calls"]))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_generation_audit_log.py tests/test_configuration.py -v`
Expected: PASS (all)

Run: `python3 -m pytest tests/`
Expected: PASS (no regressions)

- [ ] **Step 5: Commit**

```bash
git add tests/test_generation_audit_log.py tests/test_configuration.py scripts/daily_reddit_meme_pipeline.py
git commit -m "feat: humor writer/critics record full prompt+options via generation_calls"
```

---

### Task 3: Wire vision description + source-suitability gate

**Files:**
- Modify: `scripts/daily_reddit_meme_pipeline.py`:
  - `describe_source_image` (~line 2547)
  - `assess_source_suitability` (~line 2624)
  - `prepare_source_media` (~line 2708)
  - `generate_concepts` (~line 1794, signature + concept-creation point ~1905)
  - `main()` (~lines 4465, 4477-4522)
- Test: `tests/test_generation_audit_log.py` (add class)

**Interfaces:**
- Consumes: `timed_generation_request` (Task 1)
- Produces: `prepare_source_media(posts, run_dir, args) -> tuple[dict[str,str], dict[str,str], dict[str, list[dict[str, Any]]]]` (third return value: `generation_calls_by_post`); `describe_source_image(image_path, model, timeout, calls: list[dict[str, Any]] | None = None) -> str`; `assess_source_suitability(post, image_path, description, model, timeout, calls: list[dict[str, Any]] | None = None) -> dict[str, Any]`; `generate_concepts(..., generation_calls_by_post: dict[str, list[dict[str, Any]]] | None = None)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_generation_audit_log.py`:

```python
class VisionAndSourceGateWiringTests(unittest.TestCase):
    def test_describe_source_image_records_call(self) -> None:
        import tempfile
        from PIL import Image

        calls: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "photo.jpg"
            Image.new("RGB", (10, 10), color="red").save(image_path)
            with patch.object(
                pipeline, "request_json", return_value={"message": {"content": "a red square"}}
            ):
                description = pipeline.describe_source_image(image_path, "vision-model", 30, calls)
        self.assertEqual(description, "a red square")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["stage"], "vision_description")
        self.assertEqual(calls[0]["model"], "vision-model")
        self.assertIsInstance(calls[0]["prompt"], list)
        self.assertTrue(
            calls[0]["prompt"][0]["images"][0].startswith("[image omitted,")
        )

    def test_describe_source_image_without_calls_list_still_works(self) -> None:
        import tempfile
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "photo.jpg"
            Image.new("RGB", (10, 10), color="blue").save(image_path)
            with patch.object(
                pipeline, "request_json", return_value={"message": {"content": "a blue square"}}
            ):
                description = pipeline.describe_source_image(image_path, "vision-model", 30)
        self.assertEqual(description, "a blue square")

    def test_assess_source_suitability_records_call(self) -> None:
        import tempfile
        from PIL import Image
        import json as jsonlib

        post = pipeline.reddit.RedditPost(
            subreddit="popular", id="t3_y", title="A dog", author="/u/demo",
            url="https://example.com", updated="2026-07-19T00:00:00+00:00",
            summary="", rank=1, media_type="image", media_url="",
        )
        review_payload = {
            "approved": True, "reason": "ok",
            "scores": {"source_match": 5, "visual_clarity": 5, "motion_potential": 5, "text_independence": 5},
            "embedded_text_carries_meaning": False, "multi_photo_collage": False,
        }
        calls: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "photo.jpg"
            Image.new("RGB", (10, 10), color="green").save(image_path)
            with patch.object(
                pipeline, "request_json", return_value={"message": {"content": jsonlib.dumps(review_payload)}}
            ):
                pipeline.assess_source_suitability(post, image_path, "a dog photo", "critic-model", 30, calls)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["stage"], "source_suitability")
        self.assertEqual(calls[0]["model"], "critic-model")

    def test_generate_concepts_attaches_pre_concept_calls(self) -> None:
        post = pipeline.reddit.RedditPost(
            subreddit="popular", id="t3_pre", title="A bird", author="/u/demo",
            url="https://example.com", updated="2026-07-19T00:00:00+00:00",
            summary="", rank=1, media_type="image", media_url="",
        )
        pre_call = {"backend": "ollama", "stage": "vision_description", "model": "v", "state": "completed"}
        with patch.object(pipeline, "request_json", side_effect=ValueError("writer offline")):
            concepts = pipeline.generate_concepts(
                [post], "writer-model", 5,
                generation_calls_by_post={post.id: [pre_call]},
            )
        self.assertEqual(len(concepts), 1)
        self.assertEqual(concepts[0]["execution"]["generation_calls"][0], pre_call)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_generation_audit_log.py::VisionAndSourceGateWiringTests -v`
Expected: FAIL — `describe_source_image`/`assess_source_suitability` don't accept a `calls` param yet; `generate_concepts` doesn't accept `generation_calls_by_post`.

- [ ] **Step 3: Implement**

In `describe_source_image` (~line 2547), change the signature and the request call:

```python
def describe_source_image(
    image_path: Path, model: str, timeout: int, calls: list[dict[str, Any]] | None = None
) -> str:
```

and replace its `request_json(...)` call:

```python
        data = request_json(
            "POST",
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": model,
                "stream": False,
                "messages": [{"role": "user", "content": prompt, "images": [encoded]}],
                "options": {"temperature": 0.2},
            },
            timeout=timeout,
        )
```

with:

```python
        payload = {
            "model": model,
            "stream": False,
            "messages": [{"role": "user", "content": prompt, "images": [encoded]}],
            "options": {"temperature": 0.2},
        }
        if calls is not None:
            data = timed_generation_request(
                calls, backend="ollama", stage="vision_description",
                payload=payload, timeout=timeout, url=f"{OLLAMA_URL}/api/chat",
            )
        else:
            data = request_json("POST", f"{OLLAMA_URL}/api/chat", json=payload, timeout=timeout)
```

In `assess_source_suitability` (~line 2624), add the same optional `calls` parameter to its signature:

```python
def assess_source_suitability(
    post: reddit.RedditPost,
    image_path: Path,
    visual_description: str,
    model: str,
    timeout: int,
    calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
```

and replace its internal `request_json(...)` call:

```python
        data = request_json(
            "POST",
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": model,
                "stream": False,
                "think": False,
                "format": schema,
                "messages": [{"role": "user", "content": prompt}],
                "options": {"temperature": 0, "seed": 20260705, "num_predict": 250},
            },
            timeout=timeout,
        )
```

with:

```python
        payload = {
            "model": model,
            "stream": False,
            "think": False,
            "format": schema,
            "messages": [{"role": "user", "content": prompt}],
            "options": {"temperature": 0, "seed": 20260705, "num_predict": 250},
        }
        if calls is not None:
            data = timed_generation_request(
                calls, backend="ollama", stage="source_suitability",
                payload=payload, timeout=timeout, url=f"{OLLAMA_URL}/api/chat",
            )
        else:
            data = request_json("POST", f"{OLLAMA_URL}/api/chat", json=payload, timeout=timeout)
```

(the surrounding `try:`/`except Exception as exc:` block that wraps this whole function body is unchanged — a re-raise from `timed_generation_request` on failure is still caught there and turned into the existing rejected-with-`error: True` fallback result).

In `prepare_source_media` (~line 2708), change the return type and body:

```python
def prepare_source_media(
    posts: list[reddit.RedditPost],
    run_dir: Path,
    args: argparse.Namespace,
) -> tuple[dict[str, str], dict[str, str], dict[str, list[dict[str, Any]]]]:
    source_media_paths: dict[str, str] = {}
    visual_descriptions: dict[str, str] = {}
    generation_calls_by_post: dict[str, list[dict[str, Any]]] = {}
    only_indexes = set(args.only_index or [])
    for idx, post in enumerate(posts, 1):
        if only_indexes and idx not in only_indexes:
            continue
        slug = slugify(post.title)
        path = download_source_media(post, run_dir / f"{idx:02d}-{slug}-source")
        if path:
            source_media_paths[post.id] = path
        if args.describe_source_images and post.media_type == "image" and path:
            calls: list[dict[str, Any]] = []
            description = describe_source_image(Path(path), args.vision_model, args.vision_timeout, calls)
            if calls:
                generation_calls_by_post[post.id] = calls
            if description:
                visual_descriptions[post.id] = description
                print(f"Source image described {idx}/{len(posts)}: {post.title[:70]}")
    return source_media_paths, visual_descriptions, generation_calls_by_post
```

In `generate_concepts` (~line 1794), add the parameter:

```python
def generate_concepts(
    posts: list[reddit.RedditPost],
    model: str,
    timeout: int,
    visual_descriptions: dict[str, str] | None = None,
    humor_model: str = "gemma3:12b",
    humor_critic_model: str | None = None,
    humor_second_critic_model: str | None = None,
    seed_candidates_by_post: dict[str, list[dict[str, Any]]] | None = None,
    source_reviews: dict[str, dict[str, Any]] | None = None,
    image_paths: dict[str, str] | None = None,
    generation_calls_by_post: dict[str, list[dict[str, Any]]] | None = None,
    checkpoint: Callable[[list[dict[str, Any]]], None] | None = None,
) -> list[dict[str, str]]:
    visual_descriptions = visual_descriptions or {}
    seed_candidates_by_post = seed_candidates_by_post or {}
    source_reviews = source_reviews or {}
    image_paths = image_paths or {}
    generation_calls_by_post = generation_calls_by_post or {}
```

and right after the `concept = {...}` literal is built (~line 1905-1915, immediately after the closing `}` of that dict and before the `if isinstance(item.get("video_script"), dict):` line), insert:

```python
        pre_concept_calls = generation_calls_by_post.get(post.id) or []
        if pre_concept_calls:
            concept.setdefault("execution", {"state": "pending", "attempts": {}})["generation_calls"] = list(
                pre_concept_calls
            )
```

In `main()`:

Change line 4465 from:

```python
    source_media_paths, visual_descriptions = prepare_source_media(posts, run_dir, args)
```

to:

```python
    source_media_paths, visual_descriptions, generation_calls_by_post = prepare_source_media(posts, run_dir, args)
```

In the `else:` branch that builds `source_reviews` (~line 4477-4498), pass a `calls` list into `assess_source_suitability` and merge it in:

```python
        for post in posts:
            source_path = source_media_paths.get(post.id, "")
            if post.media_type != "image" or not source_path:
                source_reviews[post.id] = {
                    "approved": False,
                    "scores": {name: 0.0 for name in ("source_match", "visual_clarity", "motion_potential", "text_independence")},
                    "reason": "controlled I2V experiment requires a downloaded source image",
                }
                continue
            source_calls = generation_calls_by_post.setdefault(post.id, [])
            source_reviews[post.id] = assess_source_suitability(
                post,
                Path(source_path),
                visual_descriptions.get(post.id, ""),
                args.source_critic_model,
                args.vision_timeout,
                source_calls,
            )
            print(
                f"Source gate {post.id}: "
                f"{'approved' if source_reviews[post.id]['approved'] else 'rejected'} - "
                f"{source_reviews[post.id]['reason']}"
            )
```

Right after that loop, before the `if args.skip_ollama_concepts:` branch, no change needed there — but inside the `if args.skip_ollama_concepts:` branch (fallback path that bypasses `generate_concepts`), attach the collected calls manually since `generate_concepts` is never invoked on that path:

```python
        if args.skip_ollama_concepts:
            concepts = [fallback_concept(post, visual_descriptions.get(post.id, "")) for post in posts]
            for post, concept in zip(posts, concepts):
                pre_concept_calls = generation_calls_by_post.get(post.id) or []
                if pre_concept_calls:
                    concept.setdefault("execution", {"state": "pending", "attempts": {}})["generation_calls"] = list(
                        pre_concept_calls
                    )
        else:
```

and pass the dict through to `generate_concepts` in the `else:` branch:

```python
            concepts = generate_concepts(
                posts,
                args.ollama_model,
                args.concept_timeout,
                visual_descriptions,
                humor_model=args.humor_model,
                humor_critic_model=args.humor_critic_model,
                humor_second_critic_model=args.humor_second_critic_model,
                seed_candidates_by_post=seed_candidates_by_post,
                source_reviews=source_reviews,
                image_paths=source_media_paths,
                generation_calls_by_post=generation_calls_by_post,
                checkpoint=checkpoint_partial,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_generation_audit_log.py -v`
Expected: PASS (all)

Run: `python3 -m pytest tests/`
Expected: PASS (no regressions)

- [ ] **Step 5: Commit**

```bash
git add tests/test_generation_audit_log.py scripts/daily_reddit_meme_pipeline.py
git commit -m "feat: vision description and source-suitability gate record generation calls"
```

---

### Task 4: Wire publish-metadata generation

**Files:**
- Modify: `scripts/daily_reddit_meme_pipeline.py:1209-1262` (`generate_publish_metadata`)
- Test: `tests/test_publish_package.py` (existing `PublishGenerationTests` — verify still passes; add one assertion)

**Interfaces:**
- Consumes: `timed_generation_request` (Task 1)
- Produces: `concept["execution"]["generation_calls"]` gains entries with `stage="publish_metadata"` for every attempt (including failed ones), each carrying the exact prompt sent and the raw response preview — this is the gap that caused the real validation failure (2026-07-19) where a 3-attempts-failed publish generation left no trace of what the model actually returned.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_publish_package.py`, inside `PublishGenerationTests`:

```python
    def test_records_generation_calls_on_concept_including_failed_attempts(self) -> None:
        bad = {"message": {"content": "not json at all"}}
        good = self._ollama_reply(VALID_CANDIDATE)
        concept = make_concept()
        with patch.object(pipeline, "request_json", side_effect=[bad, good]):
            pipeline.generate_publish_metadata(
                make_post(), concept, "runtag-01", "gemma4:31b", timeout=60
            )
        calls = concept["execution"]["generation_calls"]
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["stage"], "publish_metadata")
        self.assertEqual(calls[0]["state"], "completed")
        self.assertEqual(calls[0]["response_preview"], "not json at all")
        self.assertEqual(calls[1]["state"], "completed")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_publish_package.py::PublishGenerationTests::test_records_generation_calls_on_concept_including_failed_attempts -v`
Expected: FAIL — `concept["execution"]` doesn't exist / `generate_publish_metadata` doesn't record anything yet.

- [ ] **Step 3: Implement**

In `scripts/daily_reddit_meme_pipeline.py`, inside `generate_publish_metadata` (~line 1209), replace the loop body's request call:

```python
    prompt = compose_publish_prompt(post, concept)
    last_issues: list[str] = []
    for attempt in range(1, max_attempts + 1):
        payload = {
            "model": model,
            "stream": False,
            "messages": [
                {"role": "system", "content": "Voce escreve metadados de publicacao em pt-BR e responde apenas JSON."},
                {"role": "user", "content": prompt},
            ],
            "options": {"temperature": 0.4, "num_predict": 700},
        }
        try:
            data = request_json("POST", f"{OLLAMA_URL}/api/chat", json=payload, timeout=timeout)
            content = (data.get("message") or {}).get("content") or ""
            candidate = normalize_publish_candidate(extract_json_object(content))
            issues = publish_metadata_issues(candidate)
        except Exception as exc:  # noqa: BLE001 - each failed round-trip is one attempt
            issues = [str(exc)]
            candidate = {}
```

with:

```python
    prompt = compose_publish_prompt(post, concept)
    generation_calls = concept.setdefault("execution", {"state": "pending", "attempts": {}}).setdefault(
        "generation_calls", []
    )
    last_issues: list[str] = []
    for attempt in range(1, max_attempts + 1):
        payload = {
            "model": model,
            "stream": False,
            "messages": [
                {"role": "system", "content": "Voce escreve metadados de publicacao em pt-BR e responde apenas JSON."},
                {"role": "user", "content": prompt},
            ],
            "options": {"temperature": 0.4, "num_predict": 700},
        }
        try:
            data = timed_generation_request(
                generation_calls, backend="ollama", stage="publish_metadata", round_number=attempt,
                payload=payload, timeout=timeout, url=f"{OLLAMA_URL}/api/chat",
            )
            content = (data.get("message") or {}).get("content") or ""
            candidate = normalize_publish_candidate(extract_json_object(content))
            issues = publish_metadata_issues(candidate)
        except Exception as exc:  # noqa: BLE001 - each failed round-trip is one attempt
            issues = [str(exc)]
            candidate = {}
```

(the rest of the function — the `if not issues:` success path and the final `status: "failed"` return — is unchanged).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_publish_package.py -v`
Expected: PASS (all, including the existing `test_three_failures_mark_failed_without_fabricating` — verify it still passes since `concept["execution"]` now gets populated as a side effect, which that test doesn't assert against)

Run: `python3 -m pytest tests/`
Expected: PASS (no regressions)

- [ ] **Step 5: Commit**

```bash
git add tests/test_publish_package.py scripts/daily_reddit_meme_pipeline.py
git commit -m "feat: publish-metadata generation records every attempt's prompt and response"
```

---

### Task 5: Wire the LTX render prompt/params

**Files:**
- Modify: `scripts/daily_reddit_meme_pipeline.py:3594-3725` (`render_ltx_video_meme`, inside the `ltx23` segment loop)
- Test: `tests/test_generation_audit_log.py` (add class)

**Interfaces:**
- Consumes: nothing from earlier tasks (this stage isn't an Ollama call, so it doesn't use `timed_generation_request`) — it appends directly to `concept["execution"]["generation_calls"]`.
- Produces: one `generation_calls` entry per rendered segment with `backend="comfyui"`, `stage="ltx_render"`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_generation_audit_log.py`:

```python
class LtxRenderWiringTests(unittest.TestCase):
    def test_segment_render_appends_generation_call(self) -> None:
        import tempfile

        post = pipeline.reddit.RedditPost(
            subreddit="popular", id="t3_ltx", title="A frog", author="/u/demo",
            url="https://example.com", updated="2026-07-19T00:00:00+00:00",
            summary="", rank=1, media_type="image", media_url="",
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_photo = tmp_path / "source.jpg"
            from PIL import Image
            Image.new("RGB", (100, 100), color="green").save(source_photo)
            concept = {
                "top_text": "A", "middle_text": "B", "bottom_text": "C",
                "source_media_path": str(source_photo),
            }
            output_path = tmp_path / "out.mp4"
            args = pipeline.build_parser().parse_args([
                "--ltx23-input-mode", "source", "--ltx23-audio-mode", "native",
            ])
            with patch.object(
                pipeline, "compose_ltx23_segment_prompts", return_value=["a frog on a leaf, literal prompt"]
            ), patch.object(
                pipeline, "queue_comfy_ltx23_native_video", return_value="prompt-id-1"
            ), patch.object(
                pipeline, "wait_for_comfy_video", return_value={"filename": "x", "subfolder": "", "type": "output"}
            ), patch.object(
                pipeline, "download_comfy_file"
            ):
                pipeline.render_ltx_video_meme(post, concept, str(source_photo), None, output_path, args)
        calls = concept["execution"]["generation_calls"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["backend"], "comfyui")
        self.assertEqual(calls[0]["stage"], "ltx_render")
        self.assertEqual(calls[0]["prompt"], "a frog on a leaf, literal prompt")
        self.assertEqual(calls[0]["options"]["width"], args.ltx23_width)
        self.assertEqual(calls[0]["options"]["height"], args.ltx23_height)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_generation_audit_log.py::LtxRenderWiringTests -v`
Expected: FAIL — `concept["execution"]` not populated by `render_ltx_video_meme` yet.

- [ ] **Step 3: Implement**

In `scripts/daily_reddit_meme_pipeline.py`, inside `render_ltx_video_meme`'s segment loop, right after the existing `segment_records.append({...})` call (the dict literal with `"segment"`, `"prompt_id"`, `"prompt"`, `"negative_prompt"`, `"seed"`, `"frames"`, `"fps"`, `"width"`, `"height"`, `"sampling_steps"`, `"sampling_profile"`, `"input_mode"`, `"reference_image_path"`), add:

```python
            concept.setdefault("execution", {"state": "pending", "attempts": {}}).setdefault(
                "generation_calls", []
            ).append(
                {
                    "backend": "comfyui",
                    "stage": "ltx_render",
                    "round": segment_index,
                    "model": (LTX23_I2V_API_WORKFLOW if current_reference else LTX23_API_WORKFLOW).name,
                    "prompt": segment_prompt_text,
                    "options": {
                        "seed": segment_seed,
                        "frames": frames,
                        "fps": args.ltx23_fps,
                        "width": args.ltx23_width,
                        "height": args.ltx23_height,
                        "input_mode": "image-to-video" if current_reference else "text-to-video",
                    },
                    "state": "completed",
                }
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_generation_audit_log.py -v`
Expected: PASS (all)

Run: `python3 -m pytest tests/`
Expected: PASS (no regressions)

- [ ] **Step 5: Commit**

```bash
git add tests/test_generation_audit_log.py scripts/daily_reddit_meme_pipeline.py
git commit -m "feat: LTX render records literal prompt and render params per segment"
```

---

### Task 6: Standalone report renderer — `scripts/render_audit_report.py`

**Files:**
- Create: `scripts/render_audit_report.py`
- Test: `tests/test_render_audit_report.py` (new)

**Interfaces:**
- Produces: `render_call(call: dict[str, Any]) -> str`, `render_video_section(index: int, record: dict[str, Any]) -> str`, `render_audit_report(document: list[dict[str, Any]]) -> str`, `build_parser() -> argparse.ArgumentParser`, `main() -> int`.
- Consumes: any `concepts.json` document shape (list of records with `post`/`execution` keys, per the existing persistence contract in `daily_reddit_meme_pipeline.concept_document`). Reads `execution.generation_calls` and falls back to the legacy `execution.llm_calls` key name for files written before Task 2.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_render_audit_report.py`:

```python
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import render_audit_report as report  # noqa: E402


def make_record(title: str, calls: list[dict] | None = None, legacy_key: bool = False) -> dict:
    execution: dict = {"state": "approved", "attempts": {}}
    if calls is not None:
        execution["llm_calls" if legacy_key else "generation_calls"] = calls
    return {
        "schema_version": 3,
        "post": {"title": title},
        "execution": execution,
    }


CALL = {
    "backend": "ollama",
    "stage": "writer",
    "model": "gemma4:31b",
    "prompt": [{"role": "user", "content": "hi"}],
    "options": {"temperature": 0.85},
    "state": "completed",
    "elapsed_seconds": 1.234,
    "response_preview": "candidatos aqui",
}


class RenderCallTests(unittest.TestCase):
    def test_includes_model_stage_and_prompt(self) -> None:
        text = report.render_call(CALL)
        self.assertIn("gemma4:31b", text)
        self.assertIn("writer", text)
        self.assertIn("temperature=0.85", text)
        self.assertIn("candidatos aqui", text)

    def test_handles_string_prompt(self) -> None:
        call = dict(CALL, prompt="literal ltx prompt text")
        text = report.render_call(call)
        self.assertIn("literal ltx prompt text", text)

    def test_includes_error_when_present(self) -> None:
        call = dict(CALL, state="failed", error="timeout")
        text = report.render_call(call)
        self.assertIn("timeout", text)


class RenderVideoSectionTests(unittest.TestCase):
    def test_video_with_calls(self) -> None:
        record = make_record("A cat video", calls=[CALL])
        text = report.render_video_section(1, record)
        self.assertIn("1. A cat video", text)
        self.assertIn("writer", text)

    def test_video_without_calls_shows_placeholder(self) -> None:
        record = make_record("No calls video", calls=None)
        text = report.render_video_section(2, record)
        self.assertIn("2. No calls video", text)
        self.assertIn("Nenhuma chamada", text)

    def test_legacy_llm_calls_key_still_renders(self) -> None:
        record = make_record("Old run video", calls=[CALL], legacy_key=True)
        text = report.render_video_section(3, record)
        self.assertIn("writer", text)


class RenderAuditReportTests(unittest.TestCase):
    def test_full_document_has_one_section_per_video(self) -> None:
        document = [make_record("First", calls=[CALL]), make_record("Second", calls=[CALL])]
        text = report.render_audit_report(document)
        self.assertIn("1. First", text)
        self.assertIn("2. Second", text)

    def test_empty_document(self) -> None:
        text = report.render_audit_report([])
        self.assertIn("vazio", text)


class MainCliTests(unittest.TestCase):
    def test_main_writes_output_file(self) -> None:
        document = [make_record("CLI video", calls=[CALL])]
        with tempfile.TemporaryDirectory() as tmp:
            concepts_path = Path(tmp) / "concepts.json"
            concepts_path.write_text(json.dumps(document), encoding="utf-8")
            exit_code = report.main_with_args(["--concepts-file", str(concepts_path)])
            self.assertEqual(exit_code, 0)
            output_path = Path(tmp) / "audit-report.md"
            self.assertTrue(output_path.is_file())
            self.assertIn("CLI video", output_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_render_audit_report.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'render_audit_report'`

- [ ] **Step 3: Implement**

Create `scripts/render_audit_report.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_render_audit_report.py -v`
Expected: PASS (all)

Run: `python3 -m pytest tests/`
Expected: PASS (no regressions)

Run a real smoke test against the validated Fase 1 run directory produced 2026-07-19:

```bash
python3 scripts/render_audit_report.py --concepts-file data/media-pipeline/phase1-validation/2026-07-19/concepts.json
```

Expected: `Audit report written to data/media-pipeline/phase1-validation/2026-07-19/audit-report.md`, and the file should show the `publish_metadata` stage's 3 failed attempts for concept 1 with response previews — this is the concrete diagnostic value the whole feature exists for.

- [ ] **Step 5: Commit**

```bash
git add tests/test_render_audit_report.py scripts/render_audit_report.py
git commit -m "feat: standalone script rendering a concepts.json into a readable audit report"
```

---

### Task 7: Docs and public-readiness gate

**Files:**
- Modify: `CHANGELOG.md` (`[Unreleased] > Added`)
- Modify: `docs/roadmap.md` (add a row/item referencing the spec and the real diagnostic win)
- Modify: `docs/architecture.md` (one sentence noting the audit trail)

**Interfaces:**
- Consumes: everything from Tasks 1-6.

- [ ] **Step 1: CHANGELOG**

Add under `[Unreleased] > Added` in `CHANGELOG.md`:

```markdown
- Relatório de auditoria de geração: `execution.generation_calls` (renomeado de
  `execution.llm_calls`) agora cobre descrição visual, gate de fonte, escritor+críticos de
  humor, metadados de publicação e prompt/parâmetros do render LTX — cada chamada grava
  modelo, prompt completo (imagens base64 redigidas), parâmetros, timing e preview da
  resposta, inclusive em tentativas que falharam. Script novo
  `scripts/render_audit_report.py` lê qualquer `concepts.json` e gera `audit-report.md`
  legível por vídeo/etapa. Sem bump de `CONCEPT_SCHEMA_VERSION` (`execution` já é um dict
  livre no contrato).
```

- [ ] **Step 2: roadmap + architecture**

`docs/roadmap.md`: add a new item at the end of "O que avançamos, em ordem" describing that the 2026-07-19 Fase 1 validation run exposed a real, previously-invisible publish-metadata failure (post 1 of the 2-video batch: 3/3 attempts failed with no recorded reason), which motivated this audit log — reference `docs/superpowers/specs/2026-07-19-generation-audit-log-design.md`.

`docs/architecture.md`: in the Data Flow list, add one line after the publish-package sentence noting that every stage's exact prompt/model/params is recorded in `execution.generation_calls` for later inspection via `scripts/render_audit_report.py`.

- [ ] **Step 3: Full verification**

```bash
python3 -m pytest tests/
./scripts/check_public_ready.sh
```
Expected: all PASS / `OK: public-readiness checks passed`

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md docs/roadmap.md docs/architecture.md
git commit -m "docs: changelog/roadmap/architecture for the generation audit log"
```

---

## Self-Review (executed while writing this plan)

- **Spec coverage:** vision description (Task 3), source gate (Task 3), humor writer/critics (Task 2), publish metadata (Task 4), LTX render prompt+params (Task 5), image redaction (Task 1), standalone report script (Task 6) — every v1-scope item from the spec has a task. TTS/legacy-image/batch-fallback/n8n stages remain explicitly out of scope, per the spec.
- **Placeholders:** none; every step carries complete code.
- **Type/name consistency:** `timed_generation_request(calls, *, backend, stage, payload, timeout, url, round_number=None)` signature is identical across Tasks 2, 3, 4. `generation_calls_by_post: dict[str, list[dict[str, Any]]]` name and shape is identical between `prepare_source_media`'s return, `generate_concepts`'s new parameter, and `main()`'s local variable. Stage name strings (`"writer"`, `"critic_1"`, `"critic_2"`, `"vision_description"`, `"source_suitability"`, `"publish_metadata"`, `"ltx_render"`) are used consistently between implementation and tests.
- **Real-world validation hook:** Task 6's Step 4 smoke test runs directly against the actual `concepts.json` from the 2026-07-19 Fase 1 validation run that exposed the original blind spot — this closes the loop the feature was built for.
