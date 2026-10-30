"""The closed JSON shapes a model may return for a deliverable (PLAN §3.5, Codex review #10).

Pydantic v2, ``extra="forbid"``, a cap on every string and list, tags by pattern, numbers strict
and finite (no bool, NaN or infinity: Opus review #3). ``Model.model_json_schema()`` is what the
harness puts in the prompt, and ``model_validate_json`` is what accepts the reply. The model never
sends ``numeric``, ``unit``, ``currency`` or anything about checks: our code sets those.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Tag = Annotated[str, StringConstraints(pattern=r"^C\d{1,2}$")]
Number = Annotated[float, Field(strict=True, allow_inf_nan=False, ge=-1e15, le=1e15)]
IntNumber = Annotated[int, Field(strict=True, ge=-10**15, le=10**15)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MSentence(_Strict):
    text: str = Field(min_length=1, max_length=300)
    tags: list[Tag] = Field(min_length=1, max_length=3)


class MSection(_Strict):
    heading: Literal["Key facts", "Trends", "Risks", "What changed"]
    sentences: list[MSentence] = Field(min_length=1, max_length=4)


class MAnalysis(_Strict):
    title: str = Field(min_length=1, max_length=120)
    sections: list[MSection] = Field(min_length=1, max_length=3)


class MPoint(_Strict):
    x: str = Field(min_length=1, max_length=40)
    value: Union[IntNumber, Number]
    raw: str = Field(min_length=1, max_length=60)
    tag: Tag


class MSeries(_Strict):
    name: str = Field(min_length=1, max_length=40)
    points: list[MPoint] = Field(min_length=2, max_length=12)


class MChart(_Strict):
    title: str = Field(min_length=1, max_length=120)
    type: Literal["bar", "line"]
    x_label: str = Field("", max_length=40)
    y_label: str = Field("", max_length=40)
    series: list[MSeries] = Field(min_length=1, max_length=3)


class MCell(_Strict):
    text: str = Field("", max_length=200)
    tags: list[Tag] = Field(default_factory=list, max_length=1)


class MTable(_Strict):
    title: str = Field(min_length=1, max_length=120)
    columns: list[Annotated[str, StringConstraints(min_length=1, max_length=40)]] = Field(
        min_length=2, max_length=6)
    rows: list[list[MCell]] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def _rectangular(self) -> "MTable":
        if any(len(r) != len(self.columns) for r in self.rows):
            raise ValueError("every row must have exactly one cell per column")
        return self


MODEL_FOR = {"analysis": MAnalysis, "chart": MChart, "table": MTable}

__all__ = ["MSentence", "MSection", "MAnalysis", "MPoint", "MSeries", "MChart", "MCell",
           "MTable", "MODEL_FOR"]
