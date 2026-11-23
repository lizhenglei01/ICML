"""Exercise the upstream scorer with synthetic Chat Completions streams."""

from copy import deepcopy
import json
import os
import sys
import unittest
from unittest.mock import patch

import httpx
from openai import AsyncOpenAI

from alignment_probe import revprop_dashscope


def stream_body(answer, *, finish_reason="stop"):
    def chunk(choices, usage=None):
        return {
            "id": "mock-completion",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "qwen3.5-27b",
            "choices": choices,
            "usage": usage,
        }

    def choice(delta, finish=None):
        return [{"index": 0, "delta": delta, "finish_reason": finish}]

    split = len(answer) // 2
    events = [
        chunk(choice({"role": "assistant", "content": None})),
        chunk(choice({"reasoning_content": "Mock thought. "})),
        chunk(choice({"reasoning_content": "Mock conclusion.", "content": answer[:split]})),
        chunk(choice({"content": answer[split:]})),
    ]
    if finish_reason is not None:
        events.append(chunk(choice({}, finish_reason)))
    events.append(chunk([], {
        "prompt_tokens": 142,
        "completion_tokens": 84,
        "total_tokens": 226,
        "completion_tokens_details": {"reasoning_tokens": 23},
    }))
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events) + "data: [DONE]\n\n"


class RevPropDashScopeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"LITELLM_LOCAL_MODEL_COST_MAP": "True"}))
        self.enterContext(patch.object(sys, "path", sys.path.copy()))
        self.case = json.loads(revprop_dashscope.FERRY_CASE.read_text(encoding="utf-8"))
        self.requests = []

    async def evaluate(self, content, *, status=200):
        def handler(request):
            self.requests.append(request)
            content_type = "text/event-stream" if status == 200 else "application/json"
            return httpx.Response(status, headers={"Content-Type": content_type}, content=content)

        transport = httpx.MockTransport(handler)
        async with AsyncOpenAI(
            api_key="mock-test-key",
            base_url="https://dashscope.invalid/compatible-mode/v1",
            max_retries=0,
            http_client=httpx.AsyncClient(transport=transport),
        ) as client:
            return await revprop_dashscope.evaluate(client, self.case, "qwen3.5-27b", 42)

    def full_patch(self):
        return [
            {key: value for key, value in op.items() if key != "kind"}
            for op in self.case["expected_patch"]
        ]

    async def test_full_stream_uses_original_prompt_and_passes_original_scorer(self):
        self.case["annotation_notes"] = "PRIVATE_ANNOTATION_SENTINEL"
        self.case["expected_json"]["destination"] = "PRIVATE_REFERENCE_SENTINEL"
        complete_patch = self.full_patch()
        answer = json.dumps(complete_patch)
        output = await self.evaluate(stream_body(answer))

        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.url.path, "/compatible-mode/v1/chat/completions")
        payload = json.loads(request.content)
        self.assertEqual(payload["model"], "qwen3.5-27b")
        self.assertTrue(payload["stream"])
        self.assertTrue(payload["stream_options"]["include_usage"])
        self.assertTrue(payload["enable_thinking"])
        self.assertEqual(payload["seed"], 42)
        self.assertEqual(payload["temperature"], 0.6)

        from methods.subagent_revision import SUBAGENT_SYSTEM, build_subagent_user

        expected_prompt = build_subagent_user(self.case, include_history=True, include_json=True)
        self.assertEqual(payload["messages"], [
            {"role": "system", "content": SUBAGENT_SYSTEM},
            {"role": "user", "content": expected_prompt},
        ])
        self.assertIn(self.case["conversation"][0]["content"], expected_prompt)
        self.assertIn(self.case["edit_instruction"], expected_prompt)
        self.assertIn(json.dumps(self.case["current_json"], indent=2, ensure_ascii=False), expected_prompt)
        for private_text in ("PRIVATE_ANNOTATION_SENTINEL", "PRIVATE_REFERENCE_SENTINEL", "14:55", answer):
            self.assertNotIn(private_text, expected_prompt)

        self.assertEqual(output["status"], "scored")
        self.assertIsNone(output["transport_error"])
        self.assertTrue(output["passed"])
        result = output["result"]
        self.assertEqual(result["method"], "sub_hist_json")
        self.assertTrue(result["parse_ok"])
        self.assertEqual(result["raw_answer"], answer)
        self.assertEqual(result["model_patch"], complete_patch)
        self.assertEqual(result["reasoning_content"], "Mock thought. Mock conclusion.")
        self.assertEqual(result["finish_reason"], "stop")
        self.assertEqual(result["usage"]["in"], 142)
        self.assertEqual(result["usage"]["out"], 84)
        self.assertEqual(result["usage"]["reasoning"], 23)
        self.assertTrue(result["metrics"]["apply_ok"])
        self.assertEqual(result["metrics"]["miss"], 0)
        self.assertEqual(result["metrics"]["over_edit"], 0)
        self.assertEqual(result["metrics"]["value_recall"], 1.0)

    async def test_partial_and_wrong_patches_are_scored_as_failures(self):
        complete_patch = self.full_patch()
        partial_patch = complete_patch[:1]
        wrong_value_patch = deepcopy(complete_patch)
        day_end = next(op for op in wrong_value_patch if op["path"] == "/summary/day_end_time")
        day_end["value"] = "16:00"
        for label, candidate in (("partial", partial_patch), ("wrong_value", wrong_value_patch)):
            with self.subTest(label=label):
                output = await self.evaluate(stream_body(json.dumps(candidate)))
                self.assertEqual(output["status"], "scored")
                self.assertFalse(output["passed"])
                self.assertTrue(output["result"]["parse_ok"])
                metrics = output["result"]["metrics"]
                self.assertTrue(metrics["apply_ok"])
                if label == "partial":
                    self.assertGreater(metrics["miss"], 0)
                else:
                    self.assertEqual(metrics["miss"], 0)
                    self.assertEqual(metrics["over_edit"], 0)
                    self.assertLess(metrics["value_recall"], 1.0)
        self.assertEqual(len(self.requests), 2)

    async def test_missing_finish_reason_is_transport_error_even_with_complete_patch(self):
        output = await self.evaluate(stream_body(json.dumps(self.full_patch()), finish_reason=None))
        self.assertEqual(output["status"], "api_error")
        self.assertEqual(output["transport_error"]["type"], "IncompleteStreamError")
        self.assertIsNone(output["passed"])
        self.assertFalse(output["result"]["parse_ok"])
        self.assertEqual(len(self.requests), 1)

    async def test_http_auth_failure_is_transport_error_with_sanitized_result(self):
        response = json.dumps({
            "error": {"message": "PRIVATE_PROVIDER_SENTINEL", "code": "invalid_api_key"},
        })
        output = await self.evaluate(response, status=401)
        self.assertEqual(output["status"], "api_error")
        self.assertEqual(output["transport_error"]["http_status"], 401)
        self.assertEqual(output["transport_error"]["code"], "invalid_api_key")
        self.assertIsNone(output["passed"])
        self.assertNotIn("PRIVATE_PROVIDER_SENTINEL", json.dumps(output))
        self.assertEqual(len(self.requests), 1)
