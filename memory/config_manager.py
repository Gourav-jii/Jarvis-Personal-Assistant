import json
import sys
from pathlib import Path

def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR    = get_base_dir()
CONFIG_DIR  = BASE_DIR / "config"
CONFIG_FILE = CONFIG_DIR / "api_keys.json"

def ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

def config_exists() -> bool:
    return CONFIG_FILE.exists()

def save_api_keys(gemini_api_key: str) -> None:
    """Save a Gemini API key for backward compatibility."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["gemini_api_key"] = gemini_api_key.strip()
    CONFIG_FILE.write_text(
        json.dumps(data, indent=2),
        encoding="utf-8"
    )

def load_api_keys() -> dict:
    """Load the raw config dict; may contain legacy 'gemini_api_key' or new structure."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ Failed to load api_keys.json: {e}")
        return {}

def get_gemini_key() -> str | None:
    """Return the legacy Gemini key if present; used for backward compatibility."""
    return load_api_keys().get("gemini_api_key")

def is_configured() -> bool:
    """Check if a legacy Gemini key exists and looks valid."""
    key = get_gemini_key()
    return bool(key and len(key) > 15)

def load_llm_config() -> dict:
    """Load a provider-agnostic LLM configuration.

    The config file may contain:
    - A legacy top-level 'gemini_api_key' (Gemini only)
    - A new structure:
      {
        "llm": {
          "provider": "gemini" | "9router",
          "gemini_api_key": "...",
          "gemini_endpoint": "...",
          ...
        },
        "nine_router": {
          "api_key": "...",
          "endpoint": "..."
        }
      }

    Returns a dict with keys: provider, api_key, endpoint.
    Falls back to Gemini if no explicit provider is set.
    """
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Failed to parse config file: {e}")
        return {"provider": "gemini", "api_key": "", "endpoint": ""}

    # Legacy format – only gemini_api_key at top level
    if "gemini_api_key" in raw:
        return {
            "provider": "gemini",
            "api_key": raw["gemini_api_key"],
            "endpoint": "https://generativelanguage.googleapis.com/v1beta3/models/gemini-1.5-pro:generateContent",
        }

    # New multi-provider format
    llm_cfg = raw.get("llm", {})
    provider = llm_cfg.get("provider", "gemini").lower()

    if provider == "gemini":
        return {
            "provider": "gemini",
            "api_key": llm_cfg.get("gemini_api_key", ""),
            "endpoint": llm_cfg.get("gemini_endpoint", "https://generativelanguage.googleapis.com/v1beta3/models/gemini-1.5-pro:generateContent"),
        }
    elif provider == "9router":
        nine = raw.get("nine_router", {})
        return {
            "provider": "9router",
            "api_key": nine.get("api_key", ""),
            "endpoint": nine.get("endpoint", "https://api.9router.com/v1/completions"),
        }
    else:
        # Unknown provider – default to gemini for safety
        return {
            "provider": "gemini",
            "api_key": "",
            "endpoint": "",
        }