"""Verify transport boundaries without treating synthetic replies as model results."""
import json
import os

import pytest

from alignment_probe import codex_transport as transport


def fake_transport(monkeypatch, event="agentMessage", reported_model="test-model"):
    instances = []

    class RelayError(RuntimeError):
        pass

    class Backend:
        def __init__(self, **kwargs):
            self.config = kwargs
            self.closed = False
            instances.append(self)

        async def request(self, **kwargs):
            self._request("thread/start", {}, 1)
            self._request("turn/start", {"input": [{"type": "text", "text": kwargs["prompt"]}]}, 1)
            self._read_message(1)
            return {"status": "completed", "text": "synthetic unit-test response",
                    "thread_reused": False, "latency_ms": 1}

        def _request(self, method, params, timeout):
            if method == "thread/start":
                return {"model": reported_model, "modelProvider": "test-provider",
                        "reasoningEffort": "high", "instructionSources": [],
                        "thread": {"id": f"test-thread-{len(instances)}"}}
            return {}

        def _read_message(self, timeout):
            return {"method": "item/started", "params": {"item": {"type": event}}}

        def _close_sync(self):
            self.closed = True

    monkeypatch.setattr(transport, "relay_class", lambda: (Backend, RelayError))
    monkeypatch.setattr(transport, "configured_route", lambda: {"use_saved_chatgpt_login": True})
    monkeypatch.setattr(transport.subprocess, "check_output", lambda *a, **kw: "test-cli-version")
    return instances, RelayError


REQUEST = {"messages": [{"role": "system", "content": "test-system"},
                        {"role": "user", "content": "test-table-and-claim"}]}


def test_calls_have_separate_threads_and_do_not_change_parent_credentials(monkeypatch, tmp_path):
    instances, _ = fake_transport(monkeypatch)
    monkeypatch.setenv("CODEX_API_KEY", "synthetic-test-key")
    _, first = transport.call_codex(REQUEST, "test-model", "high", tmp_path / "first")
    _, second = transport.call_codex(REQUEST, "test-model", "high", tmp_path / "second")
    assert first["thread_id"] != second["thread_id"]
    assert os.environ["CODEX_API_KEY"] == "synthetic-test-key"
    for instance in instances:
        assert instance.closed
        assert instance.config["disable_host_tools"] and instance.config["disable_apps"]
        assert "CODEX_API_KEY" not in instance.config["process_env"]
    audit = json.loads((tmp_path / "first/transport_audit.json").read_text())
    start = audit["wire_requests"][0]["params"]
    assert start["baseInstructions"] == "test-system"
    assert start["config"]["project_doc_max_bytes"] == 0
    assert audit["wire_requests"][1]["params"]["input"][0]["text"] == "test-table-and-claim"


def test_tool_event_is_rejected_even_if_backend_would_return_completed(monkeypatch, tmp_path):
    instances, error = fake_transport(monkeypatch, event="commandExecution")
    with pytest.raises(error, match="forbidden"):
        transport.call_codex(REQUEST, "test-model", "high", tmp_path)
    assert instances[0].closed
    audit = json.loads((tmp_path / "transport_audit.json").read_text())
    assert audit["rejected_item_types"] == ["commandExecution"]


def test_silent_model_substitution_is_rejected(monkeypatch, tmp_path):
    instances, error = fake_transport(monkeypatch, reported_model="different-model")
    with pytest.raises(error, match="differs"):
        transport.call_codex(REQUEST, "test-model", "high", tmp_path)
    assert instances[0].closed
