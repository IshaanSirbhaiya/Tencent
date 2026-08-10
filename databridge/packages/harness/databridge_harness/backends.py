"""Backend capability table + the LiteLLM-backed real backend.

The capability table is EXPLICIT, not probed (07_PRD §3): the gaps between providers
are exactly where silent quality loss hides, so we encode them as a reviewed fact. The
structured-output ladder in client.py reads these caps to decide L3 vs L2 vs L1 per call.

No package other than this one may import a vendor SDK (P2 / ADR-0002); even here the
import of ``litellm`` is lazy so the package loads (and FakeBackend works) without it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol, Union


@dataclass(frozen=True)
class Caps:
    """What a backend family can guarantee about structured output (07_PRD §3)."""

    json_mode: bool                       # can force syntactically-valid JSON
    strict_schema: bool                   # near-guaranteed schema compliance
    tools: Union[bool, str]               # True | False | "varies"
    guided_decoding: Union[bool, str]     # False | "format_json" | "guided_json"
    seed: bool                            # honours a seed for reproducibility

    @property
    def has_guided_decoding(self) -> bool:
        """True when token-level grammar constraint (L3) is available."""
        return bool(self.guided_decoding)


# The table, EXACTLY as specified in 07_PRD §3. Keyed by "<provider>/*".
CAPABILITIES: dict[str, Caps] = {
    # Anthropic tool-use does NOT guarantee schema compliance → L1/L2 carry weight.
    "anthropic/*": Caps(json_mode=False, strict_schema=False, tools=True,
                        guided_decoding=False, seed=False),
    # OpenAI strict mode ⇒ near-guaranteed schema compliance (<0.1% failure).
    "openai/*": Caps(json_mode=True, strict_schema=True, tools=True,
                     guided_decoding=False, seed=True),
    # Ollama format=json guarantees JSON syntax, NOT your schema → L2 still required.
    "ollama_chat/*": Caps(json_mode=True, strict_schema=False, tools="varies",
                          guided_decoding="format_json", seed=True),
    # vLLM guided_json = token-level grammar ⇒ schema guaranteed; strongest local option,
    # the recommended airgapped serving stack.
    "hosted_vllm/*": Caps(json_mode=True, strict_schema=True, tools=True,
                          guided_decoding="guided_json", seed=True),
}

# Conservative fallback for an unrecognised provider: force nothing, lean on L1/L2.
_UNKNOWN_CAPS = Caps(json_mode=False, strict_schema=False, tools=False,
                     guided_decoding=False, seed=False)

# Provider prefixes whose models leave the customer perimeter (P1 airgapped refusal).
HOSTED_PROVIDERS = frozenset({"anthropic", "openai"})


def provider_of(model: str) -> str:
    """The provider segment of a LiteLLM model id, e.g. 'anthropic/claude-...' → 'anthropic'."""
    return model.split("/", 1)[0]


def caps_for(model: str) -> Caps:
    """Capabilities for a model id; conservative defaults for unknown providers."""
    return CAPABILITIES.get(f"{provider_of(model)}/*", _UNKNOWN_CAPS)


def is_hosted(model: str) -> bool:
    """True if the model would call out of the customer perimeter (hosted API)."""
    return provider_of(model) in HOSTED_PROVIDERS


@dataclass(frozen=True)
class RawCompletion:
    """What a backend returns: the raw text plus usage telemetry."""

    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    model_used: str = ""

    @property
    def tokens(self) -> int:
        return self.tokens_in + self.tokens_out


class Backend(Protocol):
    """The single method client.py calls. FakeBackend and LiteLLMBackend both satisfy it."""

    name: str

    def generate(
        self,
        *,
        model: str,
        messages: list[dict],
        json_mode: bool = False,
        guided_json: Optional[dict] = None,
        seed: Optional[int] = None,
        **kwargs: Any,
    ) -> RawCompletion: ...


class LiteLLMBackend:
    """Real backend: every provider through one LiteLLM call (P2). Lazy vendor import."""

    name = "litellm"

    def generate(
        self,
        *,
        model: str,
        messages: list[dict],
        json_mode: bool = False,
        guided_json: Optional[dict] = None,
        seed: Optional[int] = None,
        **kwargs: Any,
    ) -> RawCompletion:
        import litellm  # lazy: the only vendor import in the codebase lives behind this

        call: dict[str, Any] = {"model": model, "messages": messages, "temperature": 0}
        if seed is not None:
            call["seed"] = seed
        if guided_json is not None:
            # vLLM token-level grammar (L3). LiteLLM forwards extra_body to the server.
            call["extra_body"] = {"guided_json": guided_json}
        elif json_mode:
            call["response_format"] = {"type": "json_object"}
        for k, v in kwargs.items():
            call.setdefault(k, v)

        resp = litellm.completion(**call)
        text = resp.choices[0].message.content or ""
        usage = getattr(resp, "usage", None)
        tin = getattr(usage, "prompt_tokens", 0) or 0
        tout = getattr(usage, "completion_tokens", 0) or 0
        try:
            cost = float(litellm.completion_cost(completion_response=resp) or 0.0)
        except Exception:
            cost = 0.0
        return RawCompletion(text=text, tokens_in=tin, tokens_out=tout,
                             cost_usd=cost, model_used=model)
