"""FakeBackend — deterministic canned responses for tests (02_TRD Task 2).

Built NOW because every downstream package tests against it; no test ever hits a real
API. It satisfies the ``Backend`` protocol, records every call (so tests can assert the
backend was/ wasn't hit — e.g. cache hits and airgapped refusals), and can be SCRIPTED
with a queue of raw responses to exercise the L2 retry ladder (invalid → valid).
"""
from __future__ import annotations

import json
from typing import Any, Callable, Optional, Sequence, Union

from pydantic import BaseModel

from .backends import RawCompletion
from .embedding import RawEmbedding, hashing_embed


def valid_json(model_or_dict: Union[BaseModel, dict]) -> str:
    """Helper: a JSON string that will validate against its model (for scripted responses)."""
    if isinstance(model_or_dict, BaseModel):
        return model_or_dict.model_dump_json()
    return json.dumps(model_or_dict)


class FakeBackend:
    """A deterministic stand-in for any provider.

    ``responses`` may be:
      * a single ``str`` — returned for every call;
      * a sequence of ``str`` — consumed one per call (the last repeats once exhausted),
        which is how you script "invalid then corrected" to drive L2 retries;
      * a callable ``(model, messages, guided_json) -> str``.

    ``embedder`` (additive, for ``Harness.embed``) is a callable ``(texts) -> list[vector]``.
    Omitted, the fake embeds with the same deterministic hashing vectorizer as the offline
    fallback — but results are NOT labelled offline, because a fake is a test double standing in
    for a real embedding model. Tests that need "paraphrase is close" semantics pass their own.
    Every ``embed()`` call is recorded in ``embed_calls``.
    """

    name = "fake"

    def __init__(
        self,
        responses: Union[str, Sequence[str], Callable[..., str], None] = None,
        *,
        tokens_in: int = 5,
        tokens_out: int = 5,
        cost_usd: float = 0.0,
        embedder: Optional[Callable[[list[str]], list[list[float]]]] = None,
    ) -> None:
        self._responses = responses
        self._embedder = embedder
        self.embed_calls: list[dict] = []  # one record per embed() call
        self._i = 0
        self._tin = tokens_in
        self._tout = tokens_out
        self._cost = cost_usd
        self.calls: list[dict] = []        # one record per generate() call

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def _next_text(self, model: str, messages: list[dict], guided_json: Optional[dict]) -> str:
        r = self._responses
        if r is None:
            return "{}"
        if callable(r):
            return r(model=model, messages=messages, guided_json=guided_json)
        if isinstance(r, str):
            return r
        seq = list(r)
        if not seq:
            return "{}"
        text = seq[min(self._i, len(seq) - 1)]
        self._i += 1
        return text

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
        self.calls.append({
            "model": model, "messages": messages, "json_mode": json_mode,
            "guided_json": guided_json, "seed": seed,
        })
        text = self._next_text(model, messages, guided_json)
        return RawCompletion(text=text, tokens_in=self._tin, tokens_out=self._tout,
                             cost_usd=self._cost, model_used=model)

    def embed(self, *, model: str, inputs: list[str], **kwargs: Any) -> RawEmbedding:
        texts = list(inputs)
        self.embed_calls.append({"model": model, "inputs": texts})
        vectors = (self._embedder(texts) if self._embedder is not None
                   else hashing_embed(texts))
        return RawEmbedding(vectors=[list(map(float, v)) for v in vectors],
                            tokens=sum(len(t.split()) for t in texts),
                            cost_usd=0.0, model_used=model)
