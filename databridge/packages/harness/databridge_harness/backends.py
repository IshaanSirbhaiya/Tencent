"""Backend capability table + the LiteLLM-backed real backend.

The capability table is EXPLICIT, not probed (07_PRD §3): the gaps between providers
are exactly where silent quality loss hides, so we encode them as a reviewed fact. The
structured-output ladder in client.py reads these caps to decide L3 vs L2 vs L1 per call.

No package other than this one may import a vendor SDK (P2 / ADR-0002); even here the
import of ``litellm`` is lazy so the package loads (and FakeBackend works) without it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional, Protocol, Union

if TYPE_CHECKING:  # pragma: no cover
    from .embedding import RawEmbedding


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


#: Models that accept NO sampling override — a reviewed fact, like ``CAPABILITIES``: a live call
#: with ``temperature=0`` returned 400 "Only temperature=1 is supported" (2026-10-01) for Claude
#: Opus 5.5 / Sonnet 5.5 and Fable 5.x. Matched anywhere in the model id so a proxied route
#: (e.g. ``bedrock/anthropic.claude-opus-5-5``) is covered too. Adding a model is a line here.
NO_SAMPLING_OVERRIDE = (
    re.compile(r"claude-(opus|sonnet)-5[-.]5"),
    re.compile(r"fable-5"),
)


#: The sampling controls such a model rejects. They are removed AFTER every argument is combined,
#: so no caller keyword can put one back (R-019 round 3, High #7).
REJECTED_SAMPLING_CONTROLS = ("temperature", "top_p", "top_k")


def accepts_sampling_override(model: str) -> bool:
    """False when ``model`` rejects any temperature other than its default."""
    return not any(p.search(model) for p in NO_SAMPLING_OVERRIDE)


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

    # Optional (additive): ``embed(*, model, inputs) -> RawEmbedding``. A backend without it
    # makes ``Harness.embed`` fall back to the labelled offline embedding rather than fail.


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

        # temperature=0 where the model accepts a sampling override (determinism); OMITTED for
        # the models the reviewed table says reject one. Not LiteLLM ``drop_params``: that drops
        # unsupported parameter NAMES, and temperature is a supported name whose allowed VALUE
        # is restricted — and dropping globally would hide an unsupported control elsewhere.
        call: dict[str, Any] = {"model": model, "messages": messages}
        if accepts_sampling_override(model):
            call["temperature"] = 0
        if seed is not None:
            call["seed"] = seed
        if guided_json is not None:
            # vLLM token-level grammar (L3). LiteLLM forwards extra_body to the server.
            call["extra_body"] = {"guided_json": guided_json}
        elif json_mode:
            call["response_format"] = {"type": "json_object"}
        for k, v in kwargs.items():
            call.setdefault(k, v)
        if not accepts_sampling_override(model):
            # applied last: a caller-supplied ``temperature=`` (or top_p/top_k) never reaches a
            # model that answers it with a 400
            for name in REJECTED_SAMPLING_CONTROLS:
                call.pop(name, None)

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

    def embed(self, *, model: str, inputs: list[str], **kwargs: Any) -> "RawEmbedding":
        """One embedding batch through LiteLLM (``task_class: embed``). Lazy vendor import."""
        import litellm  # lazy: same single vendor import site as generate()

        from .embedding import RawEmbedding

        resp = litellm.embedding(model=model, input=list(inputs), **kwargs)
        data = resp.data if hasattr(resp, "data") else resp["data"]
        rows = sorted(data, key=lambda d: (d["index"] if isinstance(d, dict) else d.index))
        vectors = [list(d["embedding"] if isinstance(d, dict) else d.embedding) for d in rows]
        usage = getattr(resp, "usage", None)
        tokens = (getattr(usage, "prompt_tokens", 0) or 0) if usage is not None else 0
        try:
            cost = float(litellm.completion_cost(completion_response=resp) or 0.0)
        except Exception:
            cost = 0.0
        return RawEmbedding(vectors=vectors, tokens=tokens, cost_usd=cost, model_used=model)
