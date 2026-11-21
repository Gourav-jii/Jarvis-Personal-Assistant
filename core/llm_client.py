"""
Provider-agnostic LLM client for MARK XLVIII.

Everything is driven by config/api_keys.json:

    {
      "llm_provider": "openai",                       # "openai" | "ollama"
      "llm_url":      "https://api.deepseek.com",     # any OpenAI-compatible base URL
      "llm_model":    "deepseek-chat",                # model name
      "llm_api_key":  "sk-...",                       # empty for local servers
      "vision_model": ""                              # optional multimodal model for images
    }

OpenAI-compatible endpoints all work: DeepSeek, OpenRouter, Groq, Together,
Mistral, LM Studio, Jan, LocalAI, llama.cpp server, vLLM, Ollama's own
OpenAI-compatible layer, …

Provider presets (set "llm_preset" instead of typing URLs):
    deepseek · openrouter · groq · mistral · together · lmstudio · jan · ollama
"""

import json
import base64
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Generator

import requests

def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR    = get_base_dir()
CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"

_DEFAULTS = {
    "llm_url":       "https://api.deepseek.com",
    "llm_model":     "deepseek-chat",
    "llm_provider":  "openai",
    "llm_api_key":   "",
    "llm_max_tokens": 0,          # 0 = provider default
    "vision_model":  "",
}

# ── Failover ───────────────────────────────────────────────────────────────────
# Free/community endpoints go down often. When `llm_fallback_url` is configured
# and the primary endpoint returns a transient error (or is unreachable), the
# next request is routed to the fallback automatically for a cool-off period so
# JARVIS keeps working instead of apologising.
_FAILOVER_COOLDOWN = 120.0          # seconds to stay on the fallback
_failover_until    = 0.0            # monotonic deadline; 0 = using primary


def _using_fallback() -> bool:
    return time.monotonic() < _failover_until


def _trip_failover(reason: str) -> bool:
    """Switch to the fallback endpoint if one is configured. Returns True if switched."""
    global _failover_until
    cfg = _load_config()
    if not (cfg.get("llm_fallback_url") or "").strip():
        return False
    if _using_fallback():
        return True
    _failover_until = time.monotonic() + _FAILOVER_COOLDOWN
    print(f"[LLM] Primary endpoint failing ({reason}) — "
          f"switching to fallback for {_FAILOVER_COOLDOWN:.0f}s")
    return True


# ── Known provider presets ─────────────────────────────────────────────────────
# base URL (no trailing slash) + suggested default model.
PRESETS = {
    "gemini":     {"url": "https://generativelanguage.googleapis.com/v1beta/openai", "model": "gemini-flash-lite-latest"},
    "deepseek":   {"url": "https://api.deepseek.com",             "model": "deepseek-chat"},
    "openrouter": {"url": "https://openrouter.ai/api/v1",         "model": "deepseek/deepseek-chat"},
    "groq":       {"url": "https://api.groq.com/openai/v1",       "model": "openai/gpt-oss-120b"},
    "empero":     {"url": "https://free.empero.org/v1",           "model": "glm-5.3-flash"},
    "together":   {"url": "https://api.together.xyz/v1",          "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo"},
    "mistral":    {"url": "https://api.mistral.ai/v1",            "model": "mistral-large-latest"},
    "lmstudio":   {"url": "http://localhost:1234/v1",             "model": "local-model"},
    "jan":        {"url": "http://localhost:1337/v1",             "model": "local-model"},
    "localai":    {"url": "http://localhost:8080/v1",             "model": "local-model"},
    "ollama":     {"url": "http://localhost:11434",               "model": "llama3.2"},
}


def _load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_llm_config(patch: dict) -> None:
    """Merge `patch` into config/api_keys.json (creates the file if needed)."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    data.update(patch)
    CONFIG_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def resolve_config() -> dict:
    """
    Merge defaults ← preset ← stored config. Returns effective settings.
    A preset fills in url+model only when the user hasn't set them explicitly.
    Legacy configs that only contain 'gemini_api_key' are mapped to Google's
    OpenAI-compatible endpoint so old setups keep working unmodified.
    """
    cfg  = _load_config()
    eff  = {
        "provider":     None,
        "url":          None,
        "model":        None,
        "api_key":      "",
        "max_tokens":   int(cfg.get("llm_max_tokens", _DEFAULTS["llm_max_tokens"]) or 0),
        "vision_model": (cfg.get("vision_model") or "").strip(),
    }

    # ── Legacy config: bare Gemini key → Google's OpenAI-compat endpoint ────
    if "gemini_api_key" in cfg and "llm_url" not in cfg and "llm_preset" not in cfg:
        eff["provider"] = "openai"
        eff["url"]      = "https://generativelanguage.googleapis.com/v1beta/openai"
        eff["model"]    = "gemini-2.0-flash"
        eff["api_key"]  = cfg.get("gemini_api_key", "")
        eff["on_fallback"] = False
        return eff

    preset_name = (cfg.get("llm_preset") or "").strip().lower()
    preset      = PRESETS.get(preset_name)

    raw_provider = (cfg.get("llm_provider") or (
        "ollama" if preset_name == "ollama" else "openai")).strip().lower()
    if raw_provider in ("openai", "openai_compatible", "custom", "http"):
        eff["provider"] = "openai"
    elif raw_provider in ("ollama", "local"):
        eff["provider"] = "ollama"
    else:
        eff["provider"] = "openai"

    if preset:
        eff["url"]   = preset["url"]
        eff["model"] = preset["model"]

    if cfg.get("llm_url"):
        eff["url"]   = str(cfg["llm_url"]).strip().rstrip("/")
    if cfg.get("llm_model"):
        eff["model"] = str(cfg["llm_model"]).strip()
    eff["api_key"] = (cfg.get("llm_api_key") or "").strip()

    if not eff["url"]:
        eff["url"]   = _DEFAULTS["llm_url"]
    if not eff["model"]:
        eff["model"] = _DEFAULTS["llm_model"]

    # ── Active failover: route to the backup endpoint during a cool-off ─────
    if _using_fallback():
        fb_url = (cfg.get("llm_fallback_url") or "").strip().rstrip("/")
        if fb_url:
            eff["url"]     = fb_url
            eff["model"]   = (cfg.get("llm_fallback_model") or eff["model"]).strip()
            eff["api_key"] = (cfg.get("llm_fallback_api_key")
                              or cfg.get("llm_api_key") or "").strip()
            eff["on_fallback"] = True
    eff.setdefault("on_fallback", False)
    return eff


def get_llm_settings() -> tuple[str, str]:
    """Returns (base_url, model_name)."""
    c = resolve_config()
    return c["url"], c["model"]


def get_llm_api_key() -> str:
    return resolve_config()["api_key"]


def get_llm_provider() -> str:
    """Returns 'ollama' or 'openai'."""
    return resolve_config()["provider"]


def get_vision_model() -> str:
    """Model used for image questions; falls back to the main model."""
    c = resolve_config()
    return c["vision_model"] or c["model"]


# ── URL helpers ────────────────────────────────────────────────────────────────

def _api_root(base: str) -> str:
    """
    Normalise a base URL to the root that precedes '/v1/...'.
    'https://api.x.com'            -> 'https://api.x.com'
    'https://api.x.com/v1'         -> 'https://api.x.com'
    'https://api.x.com/v1/'        -> 'https://api.x.com'
    """
    base = base.rstrip("/")
    if re.search(r"/v\d+[a-z]*$", base, re.IGNORECASE):
        base = re.sub(r"/v\d+[a-z]*$", "", base, flags=re.IGNORECASE)
    return base.rstrip("/")


def _chat_url(base: str) -> str:
    base = base.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/openai"):
        return f"{base}/chat/completions"
    root = _api_root(base)
    return f"{root}/v1/chat/completions"


def _models_url(base: str) -> str:
    base = base.rstrip("/")
    if base.endswith("/models"):
        return base
    if base.endswith("/openai"):
        return f"{base}/models"
    return f"{_api_root(base)}/v1/models"


def _headers() -> dict:
    key = get_llm_api_key()
    h = {"Content-Type": "application/json"}
    if key:
        h["Authorization"] = f"Bearer {key}"
    # OpenRouter attribution headers (ignored by everyone else)
    h.setdefault("HTTP-Referer", "https://github.com/jarvis-community/mark-xlviii-anyllm")
    h.setdefault("X-Title", "MARK XLVIII")
    return h


def _friendly_http_error(status: int, body: str, url: str) -> str:
    hint = {
        401: "API key missing or invalid.",
        402: "Account out of credit — top up at your provider's dashboard.",
        403: "Key lacks permission for this model.",
        404: "Model name not found on this endpoint — check 'llm_model'.",
        429: "Rate limited / quota exceeded — wait or upgrade your plan.",
        500: "Provider server error — retry shortly.",
        503: "Provider temporarily overloaded — retry shortly.",
    }.get(status, f"HTTP {status}.")
    # Free community endpoints go down often; say so plainly instead of
    # blaming the user's config.
    low = (body or "").lower()
    if status in (429, 503) and ("upstream_down" in low or "restarting" in low):
        hint = ("This free endpoint's model process is down or rate-limiting. "
                "Not your config — wait and retry, or switch 'llm_model'/provider.")
    snippet = body[:200].replace("\n", " ")
    return f"LLM HTTP {status}: {hint} ({snippet})"


# Transient statuses worth one automatic retry (free endpoints bounce a lot).
_RETRY_STATUSES = (429, 500, 502, 503, 504)


def sanitize_messages_for_llm(messages: list) -> list:
    """
    Ensure messages strictly obey Gemini and OpenAI multi-turn conversation rules:
    1. If an assistant message has tool_calls, content MUST be None (Gemini 400 bug fix).
    2. Every assistant tool_call MUST have matching 'tool' response turns immediately following it.
       If an assistant turn has tool_calls but no following tool responses, strip the tool_calls.
    3. No orphaned 'tool' responses without preceding assistant tool_calls.
    4. First non-system message must be 'user'.
    5. No consecutive messages with the same role (merge consecutive user messages).
    """
    if not messages:
        return []

    system_msg = None
    rest = []
    for m in messages:
        if m.get("role") == "system" and system_msg is None:
            system_msg = dict(m)
        elif m.get("role") == "system":
            rest.append({"role": "user", "content": f"[System Note]: {m.get('content', '')}"})
        else:
            rest.append(dict(m))

    while rest and rest[0].get("role") != "user":
        rest.pop(0)

    if not rest:
        rest = [{"role": "user", "content": "Hello"}]

    cleaned = []
    i = 0
    n = len(rest)
    while i < n:
        m = rest[i]
        role = m.get("role")

        if role == "assistant" and m.get("tool_calls"):
            if not cleaned or cleaned[-1].get("role") not in ("user", "tool"):
                if m.get("content"):
                    cleaned.append({"role": "assistant", "content": m["content"]})
                i += 1
                continue

            tc_list = m["tool_calls"]
            tc_ids = set()
            for tc in tc_list:
                cid = tc.get("id") or ""
                if cid:
                    tc_ids.add(cid)

            j = i + 1
            tool_resps = []
            found_ids = set()
            while j < n and rest[j].get("role") == "tool":
                t_msg = rest[j]
                t_id = t_msg.get("tool_call_id") or ""
                tool_resps.append(t_msg)
                found_ids.add(t_id)
                j += 1

            if not tool_resps:
                if m.get("content"):
                    cleaned.append({"role": "assistant", "content": m["content"]})
                i += 1
                continue

            for tc in tc_list:
                cid = tc.get("id") or ""
                if cid not in found_ids:
                    tool_resps.append({
                        "role": "tool",
                        "tool_call_id": cid,
                        "name": tc.get("function", {}).get("name", "tool"),
                        "content": "Tool executed.",
                    })

            cleaned_asst = dict(m)
            cleaned_asst["content"] = None
            cleaned.append(cleaned_asst)
            cleaned.extend(tool_resps)
            i = j
            continue

        elif role == "tool":
            i += 1
            continue

        elif role == "user":
            if cleaned and cleaned[-1].get("role") == "user":
                prev = cleaned[-1]
                prev["content"] = f"{prev.get('content', '')}\n{m.get('content', '')}".strip()
            else:
                cleaned.append(m)
            i += 1
            continue

        elif role == "assistant":
            if cleaned and cleaned[-1].get("role") == "assistant":
                prev = cleaned[-1]
                prev["content"] = f"{prev.get('content', '')} {m.get('content', '')}".strip()
            else:
                cleaned.append(m)
            i += 1
            continue

        else:
            i += 1

    while cleaned and cleaned[0].get("role") != "user":
        cleaned.pop(0)

    if not cleaned:
        cleaned = [{"role": "user", "content": "Hello"}]

    return ([system_msg] if system_msg else []) + cleaned


def _post_chat(payload: dict, stream: bool, timeout,
               retries: int = 1, backoff: float = 3.0) -> requests.Response:
    """
    POST to the chat endpoint, retrying once on a transient upstream failure and
    failing over to `llm_fallback_url` when the primary keeps refusing.
    Streaming requests are retried too — nothing has been yielded yet at this point.
    """
    to   = (10, timeout) if isinstance(timeout, (int, float)) else timeout
    resp = None

    for attempt in range(retries + 2):        # +1 extra attempt for the fallback
        cfg = resolve_config()
        url = _chat_url(cfg["url"])
        # the fallback endpoint usually needs its own model name
        payload = {**payload, "model": cfg["model"]}
        if "messages" in payload and isinstance(payload["messages"], list):
            payload = {**payload, "messages": sanitize_messages_for_llm(payload["messages"])}
        try:
            resp = requests.post(url, headers=_headers(), json=payload,
                                 timeout=to, stream=stream)
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as e:
            if attempt < retries + 1 and _trip_failover(type(e).__name__):
                continue
            raise

        if resp.status_code not in _RETRY_STATUSES:
            return resp
        if attempt >= retries + 1:
            return resp

        try:
            resp.close()
        except Exception:
            pass

        # transient failure: try the fallback immediately, else back off + retry
        if not _using_fallback() and _trip_failover(f"HTTP {resp.status_code}"):
            continue
        wait = backoff * (attempt + 1)
        print(f"[LLM] HTTP {resp.status_code} from {url} — retrying in {wait:.0f}s")
        time.sleep(wait)

    return resp


# ── Health / warmup ────────────────────────────────────────────────────────────

def ensure_ollama_running(timeout: int = 15) -> bool:
    """
    Ping the configured backend; for local Ollama also auto-launches 'ollama serve'.
    Returns True when the backend answers.
    """
    cfg     = resolve_config()
    provider = cfg["provider"]
    url      = cfg["url"]

    def _ping(u: str) -> bool:
        try:
            return requests.get(u, timeout=3, headers=_headers()).status_code in (200, 401, 403, 404)
        except Exception:
            return False

    if provider == "openai":
        # Remote/local OpenAI-compatible server: just probe.
        ok = _ping(_models_url(url)) or _ping(url)
        if not ok:
            print(f"[LLM] Cannot reach {url} — is the server running / is your network up?")
        return ok

    # ── Ollama native ──────────────────────────────────────────────────────
    health = f"{url}/api/tags"

    def _is_up() -> bool:
        try:
            return requests.get(health, timeout=3).status_code == 200
        except Exception:
            return False

    if _is_up():
        return True

    print("[LLM] Ollama not running — launching 'ollama serve'…")
    try:
        kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        subprocess.Popen(["ollama", "serve"], **kwargs)
    except FileNotFoundError:
        print("[LLM] 'ollama' command not found. Install from https://ollama.com")
        return False
    except Exception as e:
        print(f"[LLM] Could not launch Ollama: {e}")
        return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(1.0)
        if _is_up():
            print("[LLM] Ollama started successfully.")
            return True
    print("[LLM] Ollama did not respond within the timeout.")
    return False


def warmup_model(system_prompt: str | None = None) -> bool:
    """Pre-load the model with a tiny request so the first real reply is fast."""
    cfg    = resolve_config()
    model  = cfg["model"]
    print(f"[LLM] Warming up '{model}' ({cfg['provider']})…")

    messages: list[dict] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": "hi"})

    payload: dict = {"model": model, "messages": messages, "stream": False, "max_tokens": 1}

    if cfg["provider"] == "ollama":
        payload = {
            "model": model, "messages": messages, "stream": False,
            "keep_alive": -1, "options": {"num_predict": 1},
        }
        endpoint = f"{cfg['url']}/api/chat"
        try:
            resp = requests.post(endpoint, json=payload, timeout=180)
            resp.raise_for_status()
            print(f"[LLM] '{model}' ready (Ollama).")
            return True
        except Exception as e:
            print(f"[LLM] Warmup failed (non-fatal): {e}")
            return False

    try:
        resp = _post_chat(payload, stream=False, timeout=180)
        resp.raise_for_status()
        print(f"[LLM] '{model}' ready ({cfg['url']}).")
        return True
    except Exception as e:
        print(f"[LLM] Warmup failed (non-fatal): {e}")
        return False


def check_model_available(log: Callable | None = None) -> bool:
    """Warn (non-blocking) if the model isn't available locally in Ollama."""
    if get_llm_provider() != "ollama":
        return True
    cfg = resolve_config()
    url, model = cfg["url"], cfg["model"]
    try:
        resp = requests.get(f"{url}/api/tags", timeout=5)
        resp.raise_for_status()
        pulled = [m.get("name", "") for m in resp.json().get("models", [])]
        model_base = model.split(":")[0]
        found = any(
            m == model or m == model_base or m.startswith(model_base + ":")
            for m in pulled
        )
        if not found:
            available = ", ".join(pulled) if pulled else "none"
            warn = (
                f"WRN: Model '{model}' is not pulled in Ollama.\n"
                f"     Available: {available}\n"
                f"     Fix: ollama pull {model}"
            )
            print(warn)
            if log:
                log(f"WRN: '{model}' not found — run: ollama pull {model}")
        return found
    except Exception:
        return True


# ── Tool-schema conversion (Gemini style → OpenAI style) ──────────────────────

_TYPE_MAP = {
    "OBJECT": "object", "STRING": "string", "NUMBER": "number",
    "INTEGER": "integer", "BOOLEAN": "boolean", "ARRAY": "array",
}


def _convert_schema(schema: dict | None) -> dict:
    """Recursively convert a Gemini parameter schema to JSON-Schema/OpenAI form."""
    if not schema:
        return {"type": "object", "properties": {}}
    out: dict = {}
    t = schema.get("type", "")
    if t:
        out["type"] = _TYPE_MAP.get(str(t).upper(), str(t).lower())
    if "description" in schema:
        out["description"] = schema["description"]
    if "enum" in schema:
        out["enum"] = schema["enum"]
    props = schema.get("properties")
    if props:
        out["properties"] = {k: _convert_schema(v) for k, v in props.items()}
    if "required" in schema:
        out["required"] = schema["required"]
    if "items" in schema:
        out["items"] = _convert_schema(schema["items"])
    return out


def gemini_declarations_to_openai(tools: list) -> list[dict]:
    """
    Accepts either
        [{"function_declarations": [ {...}, ... ]}]   (Gemini Live format)
    or a flat list of declarations, and returns OpenAI `tools=[{"type":"function",
    "function": {...}}]` declarations. Already-OpenAI-style input passes through.
    """
    decls: list[dict] = []
    for group in tools or []:
        if isinstance(group, dict) and "function_declarations" in group:
            decls.extend(group["function_declarations"])
        elif isinstance(group, dict):
            decls.append(group)

    out: list[dict] = []
    for d in decls:
        if "type" in d and d.get("type") == "function":
            out.append(d)                     # already OpenAI format
            continue
        fn: dict = {"name": d.get("name", "")}
        if d.get("description"):
            fn["description"] = d["description"]
        fn["parameters"] = _convert_schema(d.get("parameters"))
        out.append({"type": "function", "function": fn})
    return out


# ── Core chat transports ───────────────────────────────────────────────────────

def _extract_openai_message(data: dict) -> dict:
    choice = (data.get("choices") or [{}])[0]
    msg    = choice.get("message", {}) or {}

    tc_list = []
    for t in (msg.get("tool_calls") or []):
        fn = t.get("function", {}) or {}
        args = fn.get("arguments", "{}")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                pass                              # leave as string; executor tolerates
        tc_item = {
            "id":       t.get("id", f"call_{int(time.time()*1000)}"),
            "function": {"name": fn.get("name", ""), "arguments": args},
        }
        if "extra_content" in t:
            tc_item["extra_content"] = t["extra_content"]
        tc_list.append(tc_item)

    reasoning = msg.get("reasoning_content") or ""
    return {
        "content":    (msg.get("content") or "").strip(),
        "tool_calls": tc_list,
        "reasoning":  reasoning.strip(),
    }


def call_llm(
    messages: list,
    tools:    list | None = None,          # Gemini-style or OpenAI-style declarations
    timeout:  int | float = 120,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> dict:
    """
    Non-streaming chat request routed to the configured backend.

    Returns:
        {"content": str, "tool_calls": [{"id","function":{"name","arguments"}}]}
    Raises RuntimeError with an actionable message on failure.
    """
    cfg = resolve_config()
    eff_max_tokens = max_tokens or (cfg["max_tokens"] or None)

    if cfg["provider"] == "openai":
        payload: dict = {
            "model":    cfg["model"],
            "messages": messages,
            "stream":   False,
        }
        if eff_max_tokens:
            payload["max_tokens"] = int(eff_max_tokens)
        if temperature is not None:
            payload["temperature"] = temperature
        if tools:
            payload["tools"]       = gemini_declarations_to_openai(tools)
            payload["tool_choice"] = "auto"
        try:
            resp = _post_chat(payload, stream=False, timeout=timeout)
            if resp.status_code != 200:
                raise RuntimeError(_friendly_http_error(resp.status_code, resp.text, cfg["url"]))
            return _extract_openai_message(resp.json())
        except RuntimeError:
            raise
        except requests.exceptions.ConnectionError as e:
            raise RuntimeError(
                f"Cannot reach {cfg['url']} — check your internet connection "
                f"(or start the local server). Details: {e}"
            )
        except requests.exceptions.Timeout:
            raise RuntimeError("LLM request timed out.")
        except Exception as e:
            raise RuntimeError(f"LLM call failed: {e}")

    # ── Ollama native ──────────────────────────────────────────────────────
    payload = {
        "model":      cfg["model"],
        "messages":   messages,
        "stream":     False,
        "keep_alive": -1,
        "options":    {"num_predict": int(eff_max_tokens or 600)},
    }
    if temperature is not None:
        payload["options"]["temperature"] = temperature
    if tools:
        payload["tools"] = gemini_declarations_to_openai(tools)

    try:
        resp = requests.post(f"{cfg['url']}/api/chat", json=payload,
                             timeout=(10, timeout))
        if resp.status_code != 200:
            raise RuntimeError(_friendly_http_error(resp.status_code, resp.text, cfg["url"]))
        data = resp.json()
        msg  = data.get("message", {})
        return {
            "content":    (msg.get("content") or "").strip(),
            "tool_calls": msg.get("tool_calls") or [],
            "reasoning":  "",
        }
    except RuntimeError:
        raise
    except requests.exceptions.ConnectionError:
        if ensure_ollama_running():
            try:
                resp = requests.post(f"{cfg['url']}/api/chat", json=payload,
                                     timeout=(10, timeout))
                resp.raise_for_status()
                msg = resp.json().get("message", {})
                return {
                    "content":    (msg.get("content") or "").strip(),
                    "tool_calls": msg.get("tool_calls") or [],
                    "reasoning":  "",
                }
            except Exception:
                pass
        raise RuntimeError(
            f"Cannot connect to Ollama at {cfg['url']}. Make sure Ollama is installed."
        )
    except requests.exceptions.Timeout:
        raise RuntimeError("Ollama request timed out.")
    except Exception as e:
        raise RuntimeError(f"Ollama call failed: {e}")


def call_llm_text(
    prompt:  str,
    system:  str | None = None,
    model:   str | None = None,
    timeout: int | float = 120,
    max_tokens: int | None = None,
    json_mode: bool = False,
    images: list[tuple[bytes, str]] | None = None,   # [(png_bytes, mime_type), …]
) -> str:
    """
    One-shot text generation (no conversation state, no tools).
    Used by every internal helper (planner, summarizers, parsers, vision…).
    `images` enables vision when the target model supports it.
    """
    cfg = resolve_config()
    m   = model or cfg["model"]

    content: str | list = prompt
    if images:
        parts: list[dict] = []
        for img_bytes, mime in images:
            b64 = base64.b64encode(img_bytes).decode("ascii")
            parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            })
        parts.append({"type": "text", "text": prompt})
        content = parts

    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": content})

    eff_max_tokens = max_tokens or (cfg["max_tokens"] or None)

    if cfg["provider"] == "openai":
        payload: dict = {"model": m, "messages": messages, "stream": False}
        if eff_max_tokens:
            payload["max_tokens"] = int(eff_max_tokens)
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            resp = _post_chat(payload, stream=False, timeout=timeout)
            if resp.status_code != 200:
                raise RuntimeError(_friendly_http_error(resp.status_code, resp.text, cfg["url"]))
            return _extract_openai_message(resp.json())["content"]
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"LLM text call failed: {e}")

    # Ollama native
    payload = {
        "model": m, "messages": messages, "stream": False,
        "keep_alive": -1, "options": {"num_predict": int(eff_max_tokens or 800)},
    }
    if json_mode:
        payload["format"] = "json"
    try:
        resp = requests.post(f"{cfg['url']}/api/chat", json=payload, timeout=(10, timeout))
        resp.raise_for_status()
        return (resp.json().get("message", {}).get("content") or "").strip()
    except Exception as e:
        raise RuntimeError(f"Ollama text call failed: {e}")


def ask_llm(prompt: str, system: str | None = None, **kw) -> str:
    """Alias kept for readability at call sites."""
    return call_llm_text(prompt, system=system, **kw)


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

def ask_llm_json(prompt: str, system: str | None = None, **kw) -> dict | list:
    """
    ask_llm + tolerant JSON parsing. Strips markdown fences and, on failure,
    retries once asking the model to output raw JSON only.
    """
    kw.pop("json_mode", None)
    raw = call_llm_text(prompt, system=system, json_mode=True, **kw)
    for attempt in range(2):
        text = raw.strip()
        fence = _JSON_FENCE_RE.search(text)
        if fence:
            text = fence.group(1).strip()
        try:
            return json.loads(text)
        except Exception:
            if attempt == 0:
                raw = call_llm_text(
                    f"{prompt}\n\nCRITICAL: your previous answer was not valid JSON. "
                    "Respond with RAW JSON only — no prose, no markdown fences.",
                    system=system, json_mode=True, **{
                        k: v for k, v in kw.items() if k not in ("images",)
                    },
                )
                continue
        break
    # Last resort: first {...} or [...] block
    text = raw.strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = text.find(opener), text.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(text[i:j + 1])
            except Exception:
                pass
    raise RuntimeError(f"Model did not return valid JSON: {raw[:160]}")


def describe_image(
    image_bytes: bytes,
    mime_type:   str,
    question:    str,
    system:      str | None = None,
    timeout:     int | float = 90,
) -> str:
    """
    Ask the (vision-capable) configured model about an image.
    Uses 'vision_model' from config when set, else the main model.
    """
    model = get_vision_model()
    return call_llm_text(
        question,
        system=system,
        model=model if model != resolve_config()["model"] else None,
        images=[(image_bytes, mime_type)],
        timeout=timeout,
        max_tokens=700,
    )


# ── Streaming ──────────────────────────────────────────────────────────────────

def _stream_openai(
    messages: list,
    tools:    list | None,
    timeout,
    max_tokens: int | None,
    temperature: float | None,
    model: str | None = None,
) -> Generator[dict, None, None]:
    """
    SSE streaming for OpenAI-compatible servers.
    Yields:
        {"type": "delta", "text": str}          — incremental text
        {"type": "done", "content": str,
         "tool_calls": list, "reasoning": str}  — final aggregate
    """
    cfg = resolve_config()
    payload: dict = {
        "model":    model or cfg["model"],
        "messages": messages,
        "stream":   True,
    }
    if max_tokens or cfg["max_tokens"]:
        payload["max_tokens"] = int(max_tokens or cfg["max_tokens"])
    if temperature is not None:
        payload["temperature"] = temperature
    if tools:
        payload["tools"]       = gemini_declarations_to_openai(tools)
        payload["tool_choice"] = "auto"

    try:
        resp = _post_chat(payload, stream=True, timeout=timeout)
        if resp.status_code != 200:
            raise RuntimeError(_friendly_http_error(resp.status_code, resp.text, cfg["url"]))

        full_content = ""
        reasoning_acc = ""
        tc_fragments: dict[int, dict] = {}

        for raw in resp.iter_lines():
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue

            choices = chunk.get("choices") or [{}]
            choice  = choices[0] or {}
            delta   = choice.get("delta", {}) or {}
            text    = delta.get("content") or ""
            r_txt   = delta.get("reasoning_content") or ""
            if r_txt:
                reasoning_acc += r_txt
            if text:
                full_content += text
                yield {"type": "delta", "text": text}

            for tc in (delta.get("tool_calls") or []):
                idx = tc.get("index", 0)
                if idx not in tc_fragments:
                    tc_fragments[idx] = {"id": "", "function": {"name": "", "arguments": ""}}
                frag = tc_fragments[idx]
                if tc.get("id"):
                    frag["id"] = tc["id"]
                if "extra_content" in tc:
                    frag["extra_content"] = tc["extra_content"]
                elif "extra_content" in delta and "extra_content" not in frag:
                    frag["extra_content"] = delta["extra_content"]
                fn = tc.get("function", {}) or {}
                fn_name = fn.get("name") or ""
                if fn_name and not frag["function"]["name"]:
                    frag["function"]["name"] = fn_name
                frag["function"]["arguments"] += fn.get("arguments") or ""

            finish = choice.get("finish_reason")
            if finish in ("stop", "tool_calls", "length"):
                break

        tool_calls: list = []
        for idx in sorted(tc_fragments):
            frag = tc_fragments[idx]
            args = frag["function"]["arguments"]
            try:
                args = json.loads(args)
            except Exception:
                pass
            tc_item = {
                "id":       frag["id"],
                "function": {"name": frag["function"]["name"], "arguments": args},
            }
            if "extra_content" in frag:
                tc_item["extra_content"] = frag["extra_content"]
            tool_calls.append(tc_item)

        yield {
            "type":       "done",
            "content":    full_content.strip(),
            "tool_calls": tool_calls,
            "reasoning":  reasoning_acc.strip(),
        }

    except requests.exceptions.ConnectionError as e:
        raise RuntimeError(
            f"Cannot reach {resolve_config()['url']} — check connection/server. Details: {e}"
        )
    except requests.exceptions.Timeout:
        raise RuntimeError("LLM stream timed out.")
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"LLM stream failed: {e}")


def stream_chat(
    messages: list,
    tools:    list | None = None,
    timeout:  int | float = 120,
    max_tokens: int | None = None,
    temperature: float | None = None,
    model: str | None = None,
) -> Generator[dict, None, None]:
    """
    Streaming chat routed to the configured backend. Same event shapes for both.
    """
    cfg = resolve_config()
    if cfg["provider"] == "openai":
        yield from _stream_openai(messages, tools, timeout, max_tokens, temperature, model)
        return

    # ── Ollama native streaming ────────────────────────────────────────────
    payload: dict = {
        "model":      model or cfg["model"],
        "messages":   messages,
        "stream":     True,
        "keep_alive": -1,
        "options":    {"num_predict": int(max_tokens or cfg["max_tokens"] or 600)},
    }
    if temperature is not None:
        payload["options"]["temperature"] = temperature
    if tools:
        payload["tools"] = gemini_declarations_to_openai(tools)

    try:
        full_content = ""
        tool_calls: list = []
        with requests.post(f"{cfg['url']}/api/chat", json=payload,
                           timeout=(10, timeout), stream=True) as resp:
            resp.raise_for_status()
            for raw in resp.iter_lines():
                if not raw:
                    continue
                try:
                    chunk = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                delta = chunk.get("message", {}).get("content") or ""
                if delta:
                    full_content += delta
                    yield {"type": "delta", "text": delta}
                if chunk.get("message", {}).get("tool_calls"):
                    tool_calls.extend(chunk["message"]["tool_calls"])
                if chunk.get("done"):
                    yield {
                        "type":       "done",
                        "content":    full_content.strip(),
                        "tool_calls": tool_calls,
                        "reasoning":  "",
                    }
                    return
        yield {"type": "done", "content": full_content.strip(), "tool_calls": tool_calls, "reasoning": ""}
    except requests.exceptions.ConnectionError:
        raise RuntimeError(f"Cannot connect to Ollama at {cfg['url']}. Is 'ollama serve' running?")
    except requests.exceptions.Timeout:
        raise RuntimeError("Ollama stream timed out.")
    except Exception as e:
        raise RuntimeError(f"Ollama stream failed: {e}")
