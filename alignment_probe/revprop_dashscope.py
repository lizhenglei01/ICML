"""Run RevPropBench's original JSON-and-history baseline over Qwen streaming."""

from __future__ import annotations

import argparse
import asyncio
from getpass import getpass
import json
import os
from pathlib import Path
import sys
from time import monotonic
from unittest.mock import patch

from openai import APIError, AsyncOpenAI


UPSTREAM = Path(__file__).resolve().parents[1] / "三方依赖代码" / "llm-revision-propagation"
FERRY_CASE = UPSTREAM / "benchmark/data/annotated/travel_itinerary_L100_06.json"
DEFAULT_BASE_URL = "https://maas.qianwenaiapi.com/compatible-mode/v1"


class IncompleteStreamError(RuntimeError):
    """The provider stopped streaming before sending a finish reason."""


async def stream_answer(client, *, model, messages, temperature, max_tokens, enable_thinking, seed):
    async with await client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        seed=seed,
        stream=True,
        stream_options={"include_usage": True},
        extra_body={"enable_thinking": enable_thinking},
    ) as stream:
        content, reasoning = [], []
        usage = finish = None
        async for chunk in stream:
            if chunk.usage is not None:
                usage = chunk.usage
            for choice in chunk.choices:
                if choice.index != 0:
                    continue
                if choice.delta.content:
                    content.append(choice.delta.content)
                if thought := getattr(choice.delta, "reasoning_content", None):
                    reasoning.append(thought)
                if choice.finish_reason is not None:
                    finish = choice.finish_reason
    if finish is None:
        raise IncompleteStreamError("Qwen stream ended without a finish reason")

    from agents.llm import CompletionUsage

    raw = "".join(content)
    details = getattr(usage, "completion_tokens_details", None)
    return raw, CompletionUsage(
        input_tokens=getattr(usage, "prompt_tokens", 0),
        output_tokens=getattr(usage, "completion_tokens", 0),
        reasoning_tokens=getattr(details, "reasoning_tokens", None) or 0,
        finish_reason=finish,
        reasoning="".join(reasoning) or None,
        raw_content=raw,
    )


async def evaluate(client, case, model, seed):
    os.environ["LITELLM_CACHE"] = "0"
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    sys.path.insert(0, str(UPSTREAM))
    from benchmark import evaluator

    transport_error = None

    async def complete(**kwargs):
        nonlocal transport_error
        try:
            return await stream_answer(client, **kwargs)
        except (APIError, IncompleteStreamError) as error:
            transport_error = {
                "type": type(error).__name__,
                "http_status": getattr(error, "status_code", None),
                "code": getattr(error, "code", None),
            }
            # Upstream writes exception details into the result. Keep provider
            # responses and any authentication information out of that field.
            raise RuntimeError("Qwen API completion failed") from None

    start = monotonic()
    with patch.object(evaluator, "complete", complete):
        result = await evaluator.eval_one(
            case, method="sub_hist_json", model=model, temperature=0.6, max_retries=1, seed=seed
        )

    metrics = result["metrics"]
    passed = (
        result["parse_ok"] and metrics["apply_ok"] and metrics["miss"] == 0
        and metrics["over_edit"] == 0 and (metrics["value_recall"] in (None, 1.0))
    )
    return {
        "status": "api_error" if transport_error else "scored",
        "transport_error": transport_error,
        "model": model,
        "base_url": str(client.base_url),
        "seed": seed,
        "wall_seconds": round(monotonic() - start, 3),
        "passed": None if transport_error else passed,
        "result": result,
    }


async def run(args, key):
    async with AsyncOpenAI(api_key=key, base_url=args.base_url, max_retries=0, timeout=1200) as client:
        case = json.loads(FERRY_CASE.read_text(encoding="utf-8"))
        result = await evaluate(client, case, args.model, args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
        output.write("\n")
    print(json.dumps({k: v for k, v in result.items() if k != "result"}, ensure_ascii=False))
    print(args.out.resolve())
    return 1 if result["status"] == "api_error" else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3.5-27b")
    parser.add_argument("--base-url", default=os.getenv("DASHSCOPE_API_BASE", DEFAULT_BASE_URL))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error(f"Output already exists: {args.out}")
    key = os.getenv("DASHSCOPE_API_KEY") or getpass("Qwen API key (hidden) ").strip()
    if not key:
        parser.error("Set DASHSCOPE_API_KEY or enter a key at the prompt")
    raise SystemExit(asyncio.run(run(args, key)))


if __name__ == "__main__":
    main()
