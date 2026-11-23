"""Inference-only adapter over the existing OSWorld Codex Relay, not a new agent.

Each call has a new app-server, ephemeral thread and empty working directory.
The original relay owns the wire protocol and event loop. This adapter replaces
its GUI-role instructions and refuses to accept a run containing tool events.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import tomllib
import uuid

RELAY_SOURCE = Path(__file__).resolve().parents[2] / "OSWorld-V2/experiments/longhorizon_osworld/codex_relay_backend.py"


def configured_route():
    home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    cfg = tomllib.loads((home / "config.toml").read_text())
    provider_name = cfg.get("model_provider", "openai")
    provider = cfg.get("model_providers", {}).get(provider_name, {})
    auth_path = home / "auth.json"
    auth = json.loads(auth_path.read_text()) if auth_path.exists() else {}
    saved_chatgpt = (provider_name == "openai" and not provider.get("base_url")
                     and auth.get("auth_mode") == "chatgpt" and bool(auth.get("tokens")))
    return {"model": cfg.get("model"), "effort": cfg.get("model_reasoning_effort"),
            "provider": provider_name, "use_saved_chatgpt_login": bool(saved_chatgpt)}


def relay_class():
    spec = importlib.util.spec_from_file_location("alignment_existing_codex_relay", RELAY_SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CodexRelayBackend, module.CodexRelayError


def call_codex(request, model, effort, out):
    base_class, relay_error = relay_class()
    system = "\n\n".join(m["content"] for m in request["messages"] if m["role"] == "system")
    user = "\n\n".join(m["content"] for m in request["messages"] if m["role"] == "user")
    route = configured_route()
    process_env = os.environ.copy()
    # Same credential precedence handling already present in RSIAgent's relay.
    # Only the child is changed; global Codex config and credentials stay intact.
    if route["use_saved_chatgpt_login"]:
        process_env.pop("CODEX_API_KEY", None)

    class EvidenceRelay(base_class):
        def _request(self, method, params, timeout):
            params = dict(params)
            if method == "thread/start":
                params.update(cwd=str(empty), baseInstructions=system,
                              developerInstructions="", ephemeral=True,
                              config={"project_doc_max_bytes": 0, "web_search": "disabled",
                                      "features.multi_agent": False, "features.multi_agent_v2": False,
                                      "features.code_mode": False, "features.code_mode_host": False},
                              serviceName="icm2027-alignment-probe")
            elif method == "turn/start":
                # Explicitly preserve the selected model at the turn boundary too.
                params["model"] = model
            if method in {"thread/start", "turn/start"}:
                calls.append({"method": method, "params": params})
            result = super()._request(method, params, timeout)
            if method == "thread/start":
                starts.append({k: result.get(k) for k in [
                    "model", "modelProvider", "reasoningEffort", "instructionSources"]})
                starts[-1]["thread_id"] = result["thread"]["id"]
                if result.get("model") != model:
                    raise relay_error("Reported model differs from requested model")
                if result.get("instructionSources"):
                    raise relay_error("Unexpected instruction files loaded")
            return result

        def _read_message(self, timeout):
            message = super()._read_message(timeout)
            params = message.get("params") or {}
            method = message.get("method", "")
            if method in {"item/started", "item/completed"}:
                item_type = (params.get("item") or {}).get("type")
                items.append({"method": method, "type": item_type,
                              "thread_id": params.get("threadId"), "turn_id": params.get("turnId")})
                # userMessage is an input echo; reasoning and agentMessage are inference.
                if item_type not in {"userMessage", "reasoning", "agentMessage"}:
                    rejected.append(item_type)
                    raise relay_error(f"Tool/non-inference item is forbidden: {item_type}")
            if method == "thread/tokenUsage/updated":
                usage.append(params.get("tokenUsage"))
            return message

    out.mkdir(parents=True, exist_ok=True)
    calls, starts, items, rejected, usage = [], [], [], [], []
    with tempfile.TemporaryDirectory(prefix="icm2027_evidence_") as name:
        empty = Path(name)
        backend = EvidenceRelay(workspace=out, run_id="alignment_" + uuid.uuid4().hex,
                                model=model, reasoning_effort=effort,
                                disable_host_tools=True, disable_apps=True,
                                process_env=process_env, role_timeout_seconds=90,
                                max_role_turns=1, max_process_turns=1)
        try:
            result = asyncio.run(backend.request(role="table_evidence", prompt=user))
            if result.get("status") != "completed" or not str(result.get("text") or "").strip():
                raise relay_error("Relay did not return completed nonempty text")
            if result.get("thread_reused") or rejected:
                raise relay_error("Unexpected thread reuse or tool events")
        finally:
            backend._close_sync()
            audit = {"backend_source": str(RELAY_SOURCE),
                     "backend_sha256": hashlib.sha256(RELAY_SOURCE.read_bytes()).hexdigest(),
                     "cli_version": subprocess.check_output(["codex", "--version"], text=True).strip(),
                     "auth_route": route, "requested_model": model, "requested_effort": effort,
                     "thread_start_results": starts, "wire_requests": calls,
                     "item_events": items, "rejected_item_types": rejected,
                     "token_usage_events": usage, "empty_working_directory": str(empty),
                     "host_tools_disabled": True, "apps_disabled": True,
                     "note": "Configuration plus event audit; no claim of raw-API equivalence"}
            (out / "transport_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2))
    return result["text"], {"backend": "codex-relay", "requested_model": model,
                            "reported_model": starts[-1]["model"], "reasoning_effort": effort,
                            "reported_provider": starts[-1]["modelProvider"],
                            "thread_id": starts[-1]["thread_id"],
                            "thread_reused": result["thread_reused"], "tool_events": rejected,
                            "transport_audit": str(out / "transport_audit.json"),
                            "latency_ms": result.get("latency_ms"),
                            "usage": usage[-1] if usage else None}
