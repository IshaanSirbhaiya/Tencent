"""The closed JSON shape a model may return for a view (when one is connected).

Strict like ``deliverables/spec.py``: ``extra="forbid"``, a cap on every string and list, tags by
pattern. The model picks blocks and their order, and proposes sentences, table cells and chart
points with tags; ``blocks.check_model_block`` checks every one of them against the sources. For
``conversation`` and ``sections`` the model only picks the block: our code fills it from the
sources. The model never writes A2UI: our code compiles the checked blocks (``a2ui.py``).
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import Field

from ..deliverables.spec import MChart, MSentence, MTable, Tag, _Strict

Heading = Annotated[str, Field(max_length=60)]


class MFacts(_Strict):
    type: Literal["facts"]
    heading: Heading = ""
    items: list[MSentence] = Field(min_length=1, max_length=6)


class MSteps(_Strict):
    type: Literal["steps"]
    heading: Heading = ""
    items: list[MSentence] = Field(min_length=2, max_length=12)


class MWhen(_Strict):
    when: str = Field(min_length=1, max_length=40)
    text: str = Field(min_length=1, max_length=300)
    tags: list[Tag] = Field(min_length=1, max_length=3)


class MTimeline(_Strict):
    type: Literal["timeline"]
    heading: Heading = ""
    items: list[MWhen] = Field(min_length=2, max_length=10)


class MTableBlock(_Strict):
    type: Literal["table"]
    heading: Heading = ""
    table: MTable


class MChartBlock(_Strict):
    type: Literal["chart"]
    heading: Heading = ""
    chart: MChart


class MPick(_Strict):
    type: Literal["conversation", "sections"]
    heading: Heading = ""


MBlock = Annotated[Union[MFacts, MSteps, MTimeline, MTableBlock, MChartBlock, MPick],
                   Field(discriminator="type")]


class MView(_Strict):
    title: str = Field(min_length=1, max_length=120)
    blocks: list[MBlock] = Field(min_length=1, max_length=6)


__all__ = ["MView", "MBlock", "MFacts", "MSteps", "MTimeline", "MTableBlock", "MChartBlock",
           "MPick"]
