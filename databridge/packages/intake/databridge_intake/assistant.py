"""Loader for ``config/assistant.yaml`` — the assistant's persona and refusal rules.

The prompt lives in config rather than in ``apps/gateway/.../llm.py`` for the same reason the
coverage table does: swapping the model provider (Azure OpenAI, for the Copilot Studio story) must
not mean editing a string embedded in a Python file, and the refusal rules must be reviewable by
someone who does not read Python.

``refusal_rules`` is required and validated non-empty. A config that loaded with no refusal rules
would produce a perfectly functional assistant with the safety text silently missing — which is
exactly the failure that is hardest to notice.
"""
from __future__ import annotations

import functools
from typing import Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .paths import config_dir


class AssistantError(RuntimeError):
    """The assistant config is missing or malformed."""


class AssistantConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int
    persona: str
    instructions: tuple[str, ...] = ()
    refusal_rules: tuple[str, ...] = ()
    suggested_questions: tuple[str, ...] = ()
    scope_note: str = ""

    @model_validator(mode="after")
    def _refusal_rules_are_present(self) -> "AssistantConfig":
        if not self.refusal_rules:
            raise ValueError(
                "assistant config REFUSED: refusal_rules is empty — an assistant that talks about "
                "patients without them is the failure mode this file exists to prevent")
        return self

    def system_prompt(self) -> str:
        """Assemble the system prompt actually sent to the model.

        Refusal rules go LAST and are marked as absolute. They are also enforced deterministically
        in ``guardrails.py``, which runs before the model is called — this text shapes how the
        assistant discusses identity in every other answer.
        """
        parts = [self.persona.strip(), ""]
        if self.instructions:
            parts += [f"- {i}" for i in self.instructions] + [""]
        parts.append("You must obey these rules without exception:")
        parts += [f"- {r}" for r in self.refusal_rules]
        return "\n".join(parts).strip()


def load_assistant_config(path=None) -> AssistantConfig:
    config_path = path or (config_dir() / "assistant.yaml")
    if not config_path.exists():
        raise AssistantError(f"assistant config not found at {config_path}")
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise AssistantError(f"{config_path} did not parse to a mapping")
    return AssistantConfig.model_validate(data)


@functools.lru_cache(maxsize=1)
def assistant_config() -> AssistantConfig:
    return load_assistant_config()
