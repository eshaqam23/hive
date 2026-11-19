"""Built-in provider templates: empty forms with base_url/model prefilled.

A template creates nothing. The Models page (and GET /api/exec/llm/templates)
shows it as a form; a row exists only after the operator saves it. Model ids
are prefills, not promises: the probe lists what the endpoint really serves.
"""

from __future__ import annotations

from copy import deepcopy

#: Sampling for Qwen3 in non-thinking mode (its published recommendation).
QWEN_SAMPLING = {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "seed": 1234,
                 "max_tokens": 3072, "timeout_s": 60.0, "think": False}
#: Hosted OpenAI-compatible APIs do not all accept top_k, so it is left out.
HOSTED_SAMPLING = {**QWEN_SAMPLING, "top_k": None}

#: Hosted OSS-model providers. key_env is the variable each provider documents,
#: so a key the operator's shell already exports keeps working.
HOSTED_PRESETS = (
    {"id": "groq", "label": "Groq", "base_url": "https://api.groq.com/openai/v1",
     "model": "openai/gpt-oss-120b", "key_env": "GROQ_API_KEY"},
    {"id": "cerebras", "label": "Cerebras", "base_url": "https://api.cerebras.ai/v1",
     "model": "gpt-oss-120b", "key_env": "CEREBRAS_API_KEY"},
    {"id": "openrouter", "label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1",
     "model": "openai/gpt-oss-120b", "key_env": "OPENROUTER_API_KEY"},
    {"id": "together", "label": "Together AI", "base_url": "https://api.together.xyz/v1",
     "model": "openai/gpt-oss-120b", "key_env": "TOGETHER_API_KEY"},
    {"id": "gemini", "label": "Google Gemini (OpenAI endpoint)",
     "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
     "model": "gemini-2.5-flash", "key_env": "GEMINI_API_KEY"},
)

LOCAL_OPENAI_PRESETS = (
    {"id": "lmstudio", "label": "LM Studio", "base_url": "http://127.0.0.1:1234/v1",
     "model": "qwen/qwen3-8b", "structured_mode": "json_schema"},
    {"id": "llamacpp", "label": "llama.cpp server", "base_url": "http://127.0.0.1:8080/v1",
     "model": "qwen3-8b", "structured_mode": "json_schema"},
    {"id": "vllm-local", "label": "vLLM (local)", "base_url": "http://127.0.0.1:8000/v1",
     "model": "Qwen/Qwen3-8B", "structured_mode": "structured_outputs"},
)

ARC_TUNNEL = "ssh -L 8000:<compute-node>:8000 <pid>@tinkercliffs1.arc.vt.edu"

TEMPLATES = (
    {
        "template_id": "ollama_local",
        "label": "Ollama (local)",
        "kind": "ollama",
        "base_url": "http://127.0.0.1:11434",
        "model": "qwen3:8b",
        "structured_mode": "ollama_format",
        "key": "none",
        "sampling": QWEN_SAMPLING,
        "hint": ("Ollama's native API (/api/chat with format = the plan schema). Under WSL mirrored "
                 "networking, Ollama running on Windows is reachable from WSL at 127.0.0.1:11434. "
                 "Pull the model first: ollama pull qwen3:8b."),
    },
    {
        "template_id": "vt_arc_vllm",
        "label": "VT ARC (vLLM)",
        "kind": "openai_compatible",
        "base_url": "http://127.0.0.1:8000/v1",
        "model": "Qwen/Qwen3-8B",
        "structured_mode": "structured_outputs",
        "key": "optional",
        "sampling": QWEN_SAMPLING,
        "hint": ("vLLM runs on an ARC compute node. Open the tunnel yourself in another terminal: "
                 f"{ARC_TUNNEL} and keep it open; base_url stays http://127.0.0.1:8000/v1. "
                 "HIVE never runs ssh. Use guided_json instead of structured_outputs for vLLM "
                 "older than 0.10. A key is needed only if the server was started with --api-key."),
    },
    {
        "template_id": "hosted_oss",
        "label": "Hosted OSS-model provider",
        "kind": "openai_compatible",
        "base_url": "",
        "model": "",
        "structured_mode": "json_schema",
        "key": "required",
        "sampling": HOSTED_SAMPLING,
        "presets": HOSTED_PRESETS,
        "hint": ("OpenAI-compatible hosted API with response_format json_schema. Pick a preset; "
                 "the key is stored only in ~/.hive/providers.yaml (mode 0600) and is never "
                 "shown again, only its last four characters."),
    },
    {
        "template_id": "local_openai",
        "label": "Local OpenAI-compatible server",
        "kind": "openai_compatible",
        "base_url": "http://127.0.0.1:1234/v1",
        "model": "qwen/qwen3-8b",
        "structured_mode": "json_schema",
        "key": "none",
        "sampling": HOSTED_SAMPLING,
        "presets": LOCAL_OPENAI_PRESETS,
        "hint": ("llama.cpp server (:8080/v1), LM Studio (:1234/v1) or a local vLLM (:8000/v1). "
                 "Use json_object if the server rejects response_format json_schema."),
    },
)

_PRESETS_BY_ID = {p["id"]: p for p in HOSTED_PRESETS + LOCAL_OPENAI_PRESETS}


def preset(provider_id: str) -> dict:
    """The built-in preset for a known provider id, or {}."""
    return dict(_PRESETS_BY_ID.get(provider_id, {}))


def templates() -> list:
    """A copy safe to hand to a response body."""
    return deepcopy(list(TEMPLATES))
