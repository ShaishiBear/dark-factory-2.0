#!/usr/bin/env python3
"""Independent citation-composition probe.

This file lives above the builder independence line. It deliberately exercises the
assembled message route rather than importing ordinary tests or reconstructing the
citation dictionaries they assert.

It protects one composed product property:

    retrieved chunks + model [c:<id>] markers + same-video collapse
        -> one source pointing at the earliest chunk the model actually cited

A valid-looking source that points at the wrong segment is still a wrong citation.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND = ROOT / "app" / "backend"

if os.environ.get("_CITATION_HOLDOUT_REEXEC") != "1":
    env = dict(os.environ, _CITATION_HOLDOUT_REEXEC="1")
    sys.exit(
        subprocess.call(
            ["uv", "run", "python", str(Path(__file__).resolve())],
            cwd=BACKEND,
            env=env,
        )
    )

for key, value in {
    "JWT_SECRET": "citation-holdout-not-a-real-secret",
    "DATABASE_URL": "postgresql://holdout:holdout@localhost:5432/holdout",
    "SUPADATA_API_KEY": "holdout-placeholder",
    "YOUTUBE_CHANNEL_ID": "UC_holdout",
    "OPENROUTER_API_KEY": "holdout-placeholder",
}.items():
    os.environ.setdefault(key, value)

sys.path.insert(0, str(ROOT / "app"))

ASSERTIONS = 0
FAILURES: list[str] = []


def expect(name: str, ok: bool, detail: str = "") -> None:
    global ASSERTIONS
    ASSERTIONS += 1
    if not ok:
        FAILURES.append(f"{name}: {detail}")


async def scenario_citations_point_to_what_the_model_cited() -> None:
    from backend.routes import messages

    chunks = [
        {
            "chunk_id": "c-uncited",
            "content": "background",
            "video_id": "video-1",
            "video_title": "Grounded Video",
            "video_url": "https://example.invalid/watch?v=1",
            "source_type": "youtube",
            "lesson_url": "",
            "start_seconds": 5.0,
            "end_seconds": 10.0,
            "snippet": "background",
            "chunk_index": 0,
        },
        {
            "chunk_id": "c-late",
            "content": "later cited evidence",
            "video_id": "video-1",
            "video_title": "Grounded Video",
            "video_url": "https://example.invalid/watch?v=1",
            "source_type": "youtube",
            "lesson_url": "",
            "start_seconds": 30.0,
            "end_seconds": 35.0,
            "snippet": "later cited evidence",
            "chunk_index": 2,
        },
        {
            "chunk_id": "c-earliest-cited",
            "content": "earliest cited evidence",
            "video_id": "video-1",
            "video_title": "Grounded Video",
            "video_url": "https://example.invalid/watch?v=1",
            "source_type": "youtube",
            "lesson_url": "",
            "start_seconds": 20.0,
            "end_seconds": 25.0,
            "snippet": "earliest cited evidence",
            "chunk_index": 1,
        },
    ]
    persisted: list[dict[str, Any]] = []

    async def fake_get_conversation(_conv_id: str, user_id: str | None = None):
        return {"id": "conv-1", "title": "Already titled", "user_id": user_id}

    async def fake_create_message(**kwargs):
        persisted.append(dict(kwargs))
        return {"id": f"m-{len(persisted)}", **kwargs}

    async def fake_list_messages(_conv_id: str, user_id: str | None = None):
        return [{"role": "user", "content": "Where is the evidence?"}]

    async def fake_list_videos():
        return [{"id": "video-1"}]

    async def fake_rate_limit(_user_id: str):
        return None

    async def fake_execute_tool(_name: str, _raw_args: str, **_kwargs):
        return {"ok": True, "text": "retrieved", "chunks": [dict(c) for c in chunks]}

    async def fake_stream_chat(
        _messages,
        *,
        tools=None,
        tool_executor=None,
        max_tool_calls=0,
        final_text_out=None,
        is_member=False,
        **_kwargs,
    ):
        assert tools
        assert max_tool_calls > 0
        assert tool_executor is not None
        await tool_executor("search_videos", '{"query":"evidence"}')
        raw = "Answer [c:c-late] then supporting detail [c:c-earliest-cited]."
        if final_text_out is not None:
            final_text_out.append(raw)
        yield f"data: {json.dumps(raw)}\n\n"
        yield "data: [DONE]\n\n"

    messages.repository.get_conversation = fake_get_conversation
    messages.repository.create_message = fake_create_message
    messages.repository.list_messages = fake_list_messages
    messages.repository.list_videos = fake_list_videos
    messages.rate_limit.check_and_record = fake_rate_limit
    messages.execute_tool = fake_execute_tool
    messages.stream_chat = fake_stream_chat
    messages.LLM_TOOLS_ENABLED = True
    messages.LLM_TOOLS_MAX_PER_TURN = 3

    response = await messages.create_message(
        "conv-1",
        messages.MessageCreate(content="Where is the evidence?"),
        {"id": "user-1", "is_member": True},
    )

    emitted: list[str] = []
    async for part in response.body_iterator:
        if isinstance(part, bytes):
            emitted.append(part.decode("utf-8"))
        else:
            emitted.append(str(part))

    sources_events = [e for e in emitted if e.startswith("event: sources\n")]
    expect(
        "exactly one sources event is emitted",
        len(sources_events) == 1,
        f"events={sources_events!r}",
    )
    if not sources_events:
        return

    payload = json.loads(sources_events[0].split("data: ", 1)[1].strip())
    expect("same-video chunks collapse to one source", len(payload) == 1, f"payload={payload!r}")
    if not payload:
        return

    citation = payload[0]
    expect(
        "collapsed source remains cited",
        citation.get("is_cited") is True,
        f"citation={citation!r}",
    )
    expect(
        "source points at earliest chunk the model actually cited",
        citation.get("chunk_id") == "c-earliest-cited"
        and citation.get("start_seconds") == 20.0,
        f"citation={citation!r}",
    )
    expect(
        "all consulted segments are represented by the collapse count",
        citation.get("segment_count") == 3,
        f"citation={citation!r}",
    )

    assistant_rows = [row for row in persisted if row.get("role") == "assistant"]
    expect("assistant response is persisted", len(assistant_rows) == 1, f"rows={persisted!r}")
    if assistant_rows:
        expect(
            "persisted sources exactly match the live sources event",
            assistant_rows[0].get("sources") == payload,
            f"persisted={assistant_rows[0].get('sources')!r} live={payload!r}",
        )


def main() -> int:
    try:
        asyncio.run(scenario_citations_point_to_what_the_model_cited())
    except Exception as exc:  # noqa: BLE001
        FAILURES.append(f"scenario raised {type(exc).__name__}: {exc}")

    if FAILURES:
        for failure in FAILURES:
            print(f"  CITATION_HOLDOUT_FAIL  {failure}", flush=True)
        print(
            f"CITATION_HOLDOUT_FAILED scenarios=1 assertions={ASSERTIONS} "
            f"failures={len(FAILURES)}",
            flush=True,
        )
        return 1

    print(f"CITATION_HOLDOUT_PASSED scenarios=1 assertions={ASSERTIONS}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
