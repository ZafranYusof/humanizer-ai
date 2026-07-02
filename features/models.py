"""
HumanizeAI — model management, voting, API keys, stats.

Provides custom model endpoints, fallback chain management,
parallel model voting, temperature/prompt control, API key
rotation, and per-model performance statistics.

Config files (models_config.json, api_keys.json, model_stats.json)
live alongside this module.
"""

import json
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional

import requests

# ─── Constants ─────────────────────────────────────────────────────────
_DIR = os.path.dirname(os.path.abspath(__file__))
_MODELS_CONFIG = os.path.join(_DIR, "models_config.json")
_API_KEYS_FILE = os.path.join(_DIR, "api_keys.json")
_STATS_FILE = os.path.join(_DIR, "model_stats.json")

LLM_BASE_URL = os.environ.get("LLM_BASE", "http://localhost:20128/v1") + "/chat/completions"
LLM_KEY = os.environ.get("LLM_KEY", "123456")

DEFAULT_FALLBACK_CHAIN = [
    "cx/gpt-5.4-mini",
    "ag/claude-sonnet-4-6",
    "gc/gemini-2.5-flash",
]

_lock = threading.Lock()


# ─── Helpers ───────────────────────────────────────────────────────────

def _load_json(path: str, default: Any = None) -> Any:
    """Thread-safe JSON file read."""
    with _lock:
        if not os.path.exists(path):
            return default if default is not None else {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)


def _save_json(path: str, data: Any) -> None:
    """Thread-safe JSON file write."""
    with _lock:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


def _llm_request(
    messages: List[Dict[str, str]],
    model: str,
    temperature: float = 0.9,
    timeout: int = 300,
) -> str:
    """Low-level LLM call via requests. Returns response text."""
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 8192,
        "stream": False,
    }
    resp = requests.post(
        LLM_BASE_URL,
        json=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LLM_KEY}",
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    return (data["choices"][0]["message"].get("content") or "").strip()


def _record_stat(model: str, elapsed: float, success: bool, score: Optional[float] = None) -> None:
    """Append a call result to model_stats.json."""
    with _lock:
        stats: Dict[str, Dict] = {}
        if os.path.exists(_STATS_FILE):
            with open(_STATS_FILE, "r", encoding="utf-8") as f:
                stats = json.load(f)

        s = stats.setdefault(model, {
            "calls": 0, "total_time": 0.0, "successes": 0, "scores": [],
        })
        s["calls"] += 1
        s["total_time"] += elapsed
        if success:
            s["successes"] += 1
        if score is not None:
            s["scores"].append(score)

        with open(_STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)


# ─── 1-3: Custom Models ───────────────────────────────────────────────

def list_custom_models() -> Dict[str, Any]:
    """Return user-added custom model endpoints from models_config.json."""
    data = _load_json(_MODELS_CONFIG, {"models": {}})
    return data.get("models", {})


def add_custom_model(name: str, base_url: str, api_key: str, model_name: str) -> None:
    """Save a custom model endpoint to models_config.json."""
    with _lock:
        data = _load_json(_MODELS_CONFIG, {"models": {}})
        models = data.setdefault("models", {})
        models[name] = {
            "base_url": base_url,
            "api_key": api_key,
            "model_name": model_name,
        }
        with open(_MODELS_CONFIG, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


def remove_custom_model(name: str) -> bool:
    """Delete model from config. Returns True if found & removed."""
    with _lock:
        data = _load_json(_MODELS_CONFIG, {"models": {}})
        models = data.get("models", {})
        if name not in models:
            return False
        del models[name]
        with open(_MODELS_CONFIG, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True


# ─── 4: Model Voting ──────────────────────────────────────────────────

def model_vote(
    text: str,
    models: List[str],
    humanize_fn: Callable[[str, str], tuple],
) -> tuple:
    """
    Run humanize on up to 3 models in parallel, pick lowest detection score.

    humanize_fn(text, model) -> (humanized_text, detection_score)
    Returns (best_text, best_model, best_score).
    """
    candidates = models[:3]
    results = []

    def _run(m: str):
        t0 = time.time()
        try:
            humanized, score = humanize_fn(text, m)
            elapsed = time.time() - t0
            _record_stat(m, elapsed, True, score)
            return (humanized, m, score)
        except Exception as e:
            elapsed = time.time() - t0
            _record_stat(m, elapsed, False)
            return None

    with ThreadPoolExecutor(max_workers=min(len(candidates), 3)) as pool:
        futures = {pool.submit(_run, m): m for m in candidates}
        for fut in as_completed(futures):
            result = fut.result()
            if result is not None:
                results.append(result)

    if not results:
        raise RuntimeError("All models failed in model_vote")

    results.sort(key=lambda r: r[2])  # lowest score = best
    return results[0]


# ─── 5-6: LLM Calls ──────────────────────────────────────────────────

def temperature_call(
    text: str,
    model: str,
    temperature: float = 0.9,
    system_prompt: str = "",
) -> str:
    """LLM call with custom temperature. Returns response text."""
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": text})

    t0 = time.time()
    try:
        result = _llm_request(messages, model, temperature=temperature)
        _record_stat(model, time.time() - t0, True)
        return result
    except Exception:
        _record_stat(model, time.time() - t0, False)
        raise


def custom_system_prompt_call(
    text: str,
    model: str,
    system_prompt: str,
) -> str:
    """LLM call with user-provided system prompt. Returns response text."""
    return temperature_call(text, model, temperature=0.9, system_prompt=system_prompt)


# ─── 7-8: Fallback Chain ──────────────────────────────────────────────

_FALLBACK_CHAIN: List[str] = list(DEFAULT_FALLBACK_CHAIN)


def get_fallback_chain() -> List[str]:
    """Return current model fallback order."""
    with _lock:
        return list(_FALLBACK_CHAIN)


def update_fallback_chain(chain: List[str]) -> None:
    """Reorder fallback chain."""
    global _FALLBACK_CHAIN
    with _lock:
        _FALLBACK_CHAIN = list(chain)


# ─── 9-11: API Keys ──────────────────────────────────────────────────

def get_api_keys() -> Dict[str, Any]:
    """Return masked API keys per provider."""
    data = _load_json(_API_KEYS_FILE, {"providers": {}})
    providers = data.get("providers", {})
    masked = {}
    for provider, info in providers.items():
        if isinstance(info, dict):
            keys = info.get("keys", [])
            active = info.get("active_index", 0)
            masked[provider] = {
                "keys": [
                    k[:4] + "..." + k[-4:] if len(k) > 8 else "****"
                    for k in keys
                ],
                "active_index": active,
                "count": len(keys),
            }
        elif isinstance(info, str):
            masked[provider] = {
                "keys": [info[:4] + "..." + info[-4:] if len(info) > 8 else "****"],
                "active_index": 0,
                "count": 1,
            }
    return masked


def set_api_key(provider: str, key: str) -> None:
    """Save/update API key for provider in api_keys.json."""
    with _lock:
        data = _load_json(_API_KEYS_FILE, {"providers": {}})
        providers = data.setdefault("providers", {})
        existing = providers.get(provider)

        if isinstance(existing, dict):
            existing["keys"] = existing.get("keys", [])
            existing["keys"].append(key)
            existing["active_index"] = len(existing["keys"]) - 1
        elif isinstance(existing, str):
            providers[provider] = {
                "keys": [existing, key],
                "active_index": 1,
            }
        else:
            providers[provider] = {
                "keys": [key],
                "active_index": 0,
            }

        with open(_API_KEYS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


def rotate_api_key(provider: str) -> Optional[str]:
    """Switch to next key if multiple exist. Returns new active key (masked) or None."""
    with _lock:
        data = _load_json(_API_KEYS_FILE, {"providers": {}})
        providers = data.get("providers", {})
        info = providers.get(provider)

        if not isinstance(info, dict) or len(info.get("keys", [])) < 2:
            return None

        keys = info["keys"]
        current = info.get("active_index", 0)
        new_index = (current + 1) % len(keys)
        info["active_index"] = new_index

        with open(_API_KEYS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        k = keys[new_index]
        return k[:4] + "..." + k[-4:] if len(k) > 8 else "****"


# ─── 12: Model Stats ─────────────────────────────────────────────────

def get_model_stats() -> Dict[str, Dict[str, Any]]:
    """Return {model: {calls, avg_time, success_rate, avg_score}}."""
    raw = _load_json(_STATS_FILE, {})
    result = {}
    for model, s in raw.items():
        calls = s.get("calls", 0)
        total_time = s.get("total_time", 0.0)
        successes = s.get("successes", 0)
        scores = s.get("scores", [])
        result[model] = {
            "calls": calls,
            "avg_time": round(total_time / calls, 3) if calls else 0.0,
            "success_rate": round(successes / calls, 4) if calls else 0.0,
            "avg_score": round(sum(scores) / len(scores), 2) if scores else None,
        }
    return result


# ─── Test Block ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    print("=== HumanizeAI models.py test ===\n")

    # Fallback chain
    print("1. get_fallback_chain():", get_fallback_chain())
    update_fallback_chain(["a", "b", "c"])
    print("   after update:", get_fallback_chain())
    update_fallback_chain(DEFAULT_FALLBACK_CHAIN)
    print("   restored:", get_fallback_chain())

    # Custom models
    add_custom_model("test_model", "http://localhost:9999/v1", "sk-test", "test-7b")
    print("\n2. list_custom_models():", list_custom_models())
    removed = remove_custom_model("test_model")
    print("   removed:", removed, "| after:", list_custom_models())

    # API keys
    set_api_key("openai", "sk-1234567890abcdef")
    set_api_key("openai", "sk-abcdef1234567890")
    print("\n3. get_api_keys():", get_api_keys())
    rotated = rotate_api_key("openai")
    print("   rotated to:", rotated)
    print("   get_api_keys():", get_api_keys())

    # Stats
    print("\n4. get_model_stats():", get_model_stats())

    # LLM calls (skip if server not running)
    print("\n5. Testing LLM calls...")
    try:
        resp = temperature_call("Say hello", model="gc/gemini-2.5-flash", temperature=0.1)
        print("   temperature_call response:", resp[:80])
    except Exception as e:
        print(f"   temperature_call skipped (server offline): {type(e).__name__}")

    try:
        resp = custom_system_prompt_call("Say hi", model="gc/gemini-2.5-flash", system_prompt="You are a pirate.")
        print("   custom_system_prompt_call response:", resp[:80])
    except Exception as e:
        print(f"   custom_system_prompt_call skipped (server offline): {type(e).__name__}")

    # model_vote (skip if server not running)
    try:
        def _dummy_humanize(text, model):
            t0 = time.time()
            result = temperature_call(text, model=model, temperature=0.5, system_prompt="Rewrite casually.")
            elapsed = time.time() - t0
            return result, 50.0  # dummy score

        best = model_vote("AI writing is detectable.", ["gc/gemini-2.5-flash"], _dummy_humanize)
        print(f"\n6. model_vote result: model={best[1]}, score={best[2]}")
    except Exception as e:
        print(f"\n6. model_vote skipped (server offline): {type(e).__name__}")

    print("\n=== Done ===")
