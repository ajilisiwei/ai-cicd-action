# llm.py — model-agnostic LLM client (OpenAI-compatible) + call wrapper.
#
# Provider/model come from env vars (LLM_PROVIDER / LLM_MODEL), which the
# workflows wire from GitHub Variables; ai-cicd.yml supplies the fallback default.
# Every OpenAI-compatible endpoint works by adding a row to ENDPOINTS.

import os
import sys

from openai import OpenAI

import report

# Provider routing table — add any OpenAI-compatible API here.
ENDPOINTS = {
    "openai":     {"base_url": "https://api.openai.com/v1",    "key_env": "OPENAI_API_KEY"},
    "anthropic":  {"base_url": "https://api.anthropic.com/v1", "key_env": "ANTHROPIC_API_KEY"},
    "deepseek":   {"base_url": "https://api.deepseek.com/v1",  "key_env": "DEEPSEEK_API_KEY"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "key_env": "OPENROUTER_API_KEY"},
    "custom":     {"base_url": os.getenv("LLM_BASE_URL", ""),  "key_env": "LLM_API_KEY"},
}

_client = None
_model = None


def init_client(cfg=None):
    """Initialise the LLM client from env (falling back to cfg defaults). Idempotent."""
    global _client, _model
    if _client is not None:
        return _client, _model

    provider = os.getenv("LLM_PROVIDER") or (cfg.default_provider if cfg else "openai")
    _model = os.getenv("LLM_MODEL") or (cfg.default_model if cfg else "gpt-4o")
    # Keep env authoritative for downstream usage reporting.
    os.environ.setdefault("LLM_PROVIDER", provider)
    os.environ.setdefault("LLM_MODEL", _model)

    if provider == "ollama":
        base = os.getenv("OLLAMA_HOST", "http://localhost:11434/v1")
        _client = OpenAI(base_url=base, api_key="ollama")
        _model = os.getenv("LLM_MODEL") or "qwen2.5-coder:14b"
    else:
        endpoint = ENDPOINTS.get(provider) or ENDPOINTS["custom"]
        api_key = os.getenv(endpoint["key_env"])
        if not api_key:
            print(f"::error::Missing API key env var: {endpoint['key_env']} for provider={provider}")
            sys.exit(1)
        _client = OpenAI(base_url=endpoint["base_url"], api_key=api_key)
    return _client, _model


def call_llm(system: str, user: str) -> str:
    """Call the configured LLM with system + user messages; record token usage."""
    client, model = init_client()
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        temperature=0.1,
        max_tokens=4096,
    )
    report.record_usage(getattr(resp, "usage", None))
    return resp.choices[0].message.content
