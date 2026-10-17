"""Runtime configuration for the optional LLM-powered chat assistant.

Reads from real environment variables first, then falls back to a local `.env` file
(simple manual parser so the project doesn't need the `python-dotenv` dependency).

Two chat backends are supported, selected with `AI_PROVIDER`:

* ``deepseek`` (default) — any OpenAI-compatible ``/chat/completions`` endpoint with
  function calling. It only needs the standard library, no SDK.
* ``anthropic`` — the Claude Messages API through the optional `anthropic` SDK.

Model ids are provider-scoped: on DeepSeek's own endpoint the V4.1 Flash model is served
as ``deepseek-flash``, so well-known spellings (``deepseek-v4.1-flash``,
``deepseek-v41-flash``, ``deepseek-v4-flash``, …) are canonicalised there; a third-party
gateway receives whatever `AI_MODEL` says, untouched.

Without an API key configured, `POST /api/chat` automatically falls back to the
rule-based parser in agent.py — the app is fully functional either way.
"""
import os
from urllib.parse import urlsplit

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

PROVIDER_DEEPSEEK = "deepseek"
PROVIDER_ANTHROPIC = "anthropic"

# "openai" is accepted as an alias for the OpenAI-compatible protocol DeepSeek speaks, so
# any compatible gateway can be pointed at with AI_BASE_URL without touching the code.
_PROVIDER_ALIASES = {
    "deepseek": PROVIDER_DEEPSEEK,
    "deepseek-api": PROVIDER_DEEPSEEK,
    "openai": PROVIDER_DEEPSEEK,
    "openai-compatible": PROVIDER_DEEPSEEK,
    "anthropic": PROVIDER_ANTHROPIC,
    "claude": PROVIDER_ANTHROPIC,
}

DEFAULT_BASE_URLS = {
    PROVIDER_DEEPSEEK: "https://api.deepseek.com",
    PROVIDER_ANTHROPIC: "https://api.anthropic.com",
}
DEFAULT_MODELS = {
    PROVIDER_DEEPSEEK: "deepseek-flash",
    PROVIDER_ANTHROPIC: "claude-sonnet-5",
}
# Output cap defaults: the Claude backend keeps the 400-token cap it shipped with, so an
# existing Claude deployment behaves the same; DeepSeek gets a little more room for a tool
# round trip. `AI_MAX_TOKENS` overrides both.
DEFAULT_MAX_TOKENS = {
    PROVIDER_DEEPSEEK: 600,
    PROVIDER_ANTHROPIC: 400,
}
# `None` means "let the SDK keep its own timeout" — that is what the Claude path used
# before the DeepSeek backend existed. The DeepSeek HTTP client needs an explicit default.
DEFAULT_TIMEOUTS = {
    PROVIDER_DEEPSEEK: 30.0,
    PROVIDER_ANTHROPIC: None,
}
# Environment variable that holds the key for each provider (the generic `AI_API_KEY`
# still works as a fallback for both).
PROVIDER_KEY_ENV = {
    PROVIDER_DEEPSEEK: "DEEPSEEK_API_KEY",
    PROVIDER_ANTHROPIC: "ANTHROPIC_API_KEY",
}


def _read_dotenv_text(path):
    """Read a `.env` file written by any Windows editor.

    Notepad can save this file as UTF-8 with a BOM, or as ANSI/GBK; both used to break
    loading (a BOM turned the first key into ``\ufeffAI_API_KEY``, GBK raised
    UnicodeDecodeError and took the whole app down at import). UTF-16 BOMs are detected
    explicitly because GBK happily decodes their bytes into garbage.
    """
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", "replace")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("gbk")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _load_dotenv(path):
    if not os.path.exists(path):
        return
    for line in _read_dotenv_text(path).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(os.path.join(BASE_DIR, ".env"))


def _get(name, default="", source=None):
    """Read one setting. `source` is injectable so the resolution rules are testable."""
    env = os.environ if source is None else source
    value = env.get(name)
    if value is None:
        return default
    return value.strip()


def normalize_provider(name):
    """Map a provider spelling to its canonical name, or None when unsupported."""
    return _PROVIDER_ALIASES.get((name or "").strip().lower())


# Values shipped in .env.example (or obvious dummies) must not look like a configured key:
# otherwise every chat makes a doomed API call before falling back to the rule parser.
PLACEHOLDER_KEYS = frozenset(
    {
        "",
        "enter ai api",
        "enter deepseek api key",
        "enter deepseek api",
        "enter anthropic api key",
        "your api key",
        "your api key here",
        "your-deepseek-api-key",
        "your-api-key",
        "changeme",
        "change-me",
        "api-key",
        "sk-...",
    }
)


def looks_like_key(key):
    """True when `key` is plausibly a real API key rather than a shipped placeholder."""
    key = (key or "").strip()
    if not key:
        return False
    lowered = key.lower()
    if lowered in PLACEHOLDER_KEYS:
        return False
    if lowered.startswith("<") and lowered.endswith(">"):
        return False
    if "your" in lowered and ("api" in lowered or "key" in lowered):
        return False
    # "xxxxx", "sk-xxx", "......" and friends are dummies too.
    if all(character in "x-_.* " for character in lowered):
        return False
    return True


def resolve_provider(source=None):
    """`AI_PROVIDER` wins; otherwise infer from whichever real key is present.

    A lone generic `AI_API_KEY` still means Claude: that was the only backend before
    DeepSeek support existed, so an existing Claude deployment keeps working exactly as
    before. Placeholders (`Enter DeepSeek API Key` and friends) are ignored, so a copied
    `.env.example` never overrides a real Claude key. DeepSeek uses `DEEPSEEK_API_KEY`
    (what `.env.example` shows) or an explicit `AI_PROVIDER=deepseek`; with nothing
    configured the app reports DeepSeek, the documented default.
    """
    explicit = normalize_provider(_get("AI_PROVIDER", "", source))
    if explicit is not None:
        return explicit
    if looks_like_key(_get(PROVIDER_KEY_ENV[PROVIDER_DEEPSEEK], "", source)):
        return PROVIDER_DEEPSEEK
    if looks_like_key(_get(PROVIDER_KEY_ENV[PROVIDER_ANTHROPIC], "", source)):
        return PROVIDER_ANTHROPIC
    if looks_like_key(_get("AI_API_KEY", "", source)):
        return PROVIDER_ANTHROPIC
    return PROVIDER_DEEPSEEK


def resolve_api_key(provider, source=None):
    """Pick the key for `provider`.

    DeepSeek prefers its own `DEEPSEEK_API_KEY` and falls back to the generic
    `AI_API_KEY`. Claude keeps the historical precedence (`AI_API_KEY` before
    `ANTHROPIC_API_KEY`), so a long-standing deployment resolves the same key as before.
    """
    generic = _get("AI_API_KEY", "", source)
    if provider == PROVIDER_ANTHROPIC:
        if looks_like_key(generic):
            return generic
        return _get(PROVIDER_KEY_ENV[PROVIDER_ANTHROPIC], "", source)
    key_env = PROVIDER_KEY_ENV.get(provider)
    if key_env:
        key = _get(key_env, "", source)
        if looks_like_key(key):
            return key
    return generic


# ---------------------------------------------------------------------------
# Endpoint and model resolution
# ---------------------------------------------------------------------------
# DeepSeek serves V4.1 Flash under the stable id `deepseek-flash`; the spelling used by
# other catalogs (for example Alibaba Cloud's `deepseek-v4.1-flash`) is not what
# api.deepseek.com expects. These aliases are only applied on DeepSeek's own host, because
# a gateway defines its own model ids.
OFFICIAL_DEEPSEEK_HOSTS = frozenset({"api.deepseek.com"})

_DEEPSEEK_MODEL_ALIASES = {
    "deepseekflash": "deepseek-flash",
    "deepseekv41flash": "deepseek-flash",
    "deepseekv41flash1017": "deepseek-flash",
    "deepseekv4flash": "deepseek-flash",
    "deepseekv4flashvisionexp": "deepseek-flash",
    "deepseekv4pro": "deepseek-v4-pro",
    "deepseekv4pro0813": "deepseek-v4-pro",
}


def _host(base_url):
    candidate = (base_url or "").strip().lower()
    if not candidate:
        return ""
    if "://" not in candidate:
        candidate = "https://" + candidate
    try:
        return urlsplit(candidate).hostname or ""
    except ValueError:
        return ""


def is_official_deepseek(base_url):
    """True when `base_url` points at DeepSeek's own API host."""
    return _host(base_url) in OFFICIAL_DEEPSEEK_HOSTS


def _compact_model(name):
    return "".join(character for character in (name or "").lower() if character.isalnum())


def resolve_model(model, base_url=None):
    """Return the model id the configured endpoint actually accepts.

    `deepseek-v4.1-flash`, `deepseek-v41-flash`, `DeepSeek-V4.1-Flash`, `deepseek-v4-flash`
    and `deepseek-v4.1-flash-1017` all name the same V4.1 Flash model, which DeepSeek's API
    serves as `deepseek-flash`. Any other endpoint keeps the configured string verbatim.
    """
    model = (model or "").strip()
    if not model:
        return DEFAULT_MODELS[PROVIDER_DEEPSEEK]
    if not is_official_deepseek(base_url):
        return model
    return _DEEPSEEK_MODEL_ALIASES.get(_compact_model(model), model)


def resolve_thinking(setting, base_url=None):
    """Effective thinking behaviour for a request: ``enabled``, ``disabled`` or ``omitted``.

    ``auto`` (the default) disables thinking on DeepSeek's own host — the model would
    otherwise reason by default, which costs latency and output tokens — and omits the
    DeepSeek-specific field on third-party gateways that may not understand it.
    """
    if setting == "enabled":
        return "enabled"
    if setting == "disabled":
        return "disabled"
    return "disabled" if is_official_deepseek(base_url) else "omitted"


def _number(name, default, cast, minimum, maximum, source=None):
    raw = _get(name, "", source)
    if not raw:
        return default
    try:
        value = cast(raw)
    except (TypeError, ValueError):
        return default
    if value != value:  # NaN
        return default
    return max(minimum, min(value, maximum))


def _choice(name, choices, default, source=None):
    value = _get(name, "", source).lower()
    return value if value in choices else default


def _flag(name, default, source=None):
    raw = _get(name, "", source).lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no", "off", "disabled")


AI_PROVIDER = resolve_provider()
AI_API_KEY = resolve_api_key(AI_PROVIDER)
AI_MODEL = _get("AI_MODEL") or DEFAULT_MODELS[AI_PROVIDER]
AI_BASE_URL = (_get("AI_BASE_URL") or DEFAULT_BASE_URLS[AI_PROVIDER]).rstrip("/")

# Per-request budget. Thinking tokens are billed as output and count against
# `AI_MAX_TOKENS`, so keep thinking mode disabled for a chat this short by default.
AI_MAX_TOKENS = _number(
    "AI_MAX_TOKENS", DEFAULT_MAX_TOKENS[AI_PROVIDER], int, 1, 393216
)
AI_TEMPERATURE = _number("AI_TEMPERATURE", 0.7, float, 0.0, 2.0)
AI_TIMEOUT = _number("AI_TIMEOUT", DEFAULT_TIMEOUTS[AI_PROVIDER], float, 1.0, 600.0)
AI_MAX_RETRIES = _number("AI_MAX_RETRIES", 1, int, 0, 3)

# DeepSeek-specific switches. `auto` disables thinking on api.deepseek.com and omits the
# field elsewhere (see resolve_thinking). Thinking mode is supported, but a request that
# carries `tools` must echo every `reasoning_content` back, which DeepSeekChat implements.
AI_THINKING = _choice("AI_THINKING", ("auto", "enabled", "disabled"), "auto")
AI_REASONING_EFFORT = _choice("AI_REASONING_EFFORT", ("low", "high", "max"), "")

# Inject a compact, read-only snapshot of the student's budget into the system prompt so
# the model can answer without an extra tool round trip. The tools stay authoritative.
AI_CONTEXT = _flag("AI_CONTEXT", True)
