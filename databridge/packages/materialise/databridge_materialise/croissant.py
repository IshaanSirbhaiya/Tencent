"""Croissant 1.1 emission — the Data Card as a build output, not a markdown file that rots.

Croissant (MLCommons, 1.1 released 12 Feb 2026) is the closest external standard to what a plan
and a receipt already contain, so it is emitted rather than a manifest invented: it adopts W3C
PROV-O for record-level provenance, ODRL/DUO for machine-enforceable usage policy, is JSON-LD over
schema.org, and >700k datasets already carry it (R05 §6.2). Hugging Face auto-generates it for
every dataset it hosts, which makes "Parquet + Croissant JSON-LD" the de facto interchange pair.

The mapping that matters, and it is the whole reason this is cheap:

| Walnut | Croissant / PROV-O |
|---|---|
| `SourcePointer` | `prov:wasDerivedFrom` on the record set |
| `authored_by` | `prov:wasAttributedTo` |
| `policy.egress` / lawful basis | an ODRL policy object |
| `unresolved` cell count | AI Act Art. 10 §2(h) "data gaps and shortcomings" |
| `shaped_by` | `prov:wasGeneratedBy` — the ACTIVITY, agent-shaped or plan-shaped |

**The `shaped_by` field is carried into the JSON-LD deliberately.** A consumer reading only the
Croissant file must be able to see that an agent-shaped dataset is not reproducible; if that fact
lived only in the receipt, the interchange format would launder it.

`validated: false` is stated rather than assumed: this build does not run `mlcroissant`'s
validator, and claiming validation nobody performed is exactly the sort of documentation defect
the standard exists to fix.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .dataset import Dataset
from .plan import MaterialisationPlan
from .receipt import Receipt

CROISSANT_VERSION = "1.1"

_CONTEXT: dict[str, Any] = {
    "@vocab": "https://schema.org/",
    "cr": "http://mlcommons.org/croissant/",
    "prov": "http://www.w3.org/ns/prov#",
    "odrl": "http://www.w3.org/ns/odrl/2/",
    "sc": "https://schema.org/",
    "dct": "http://purl.org/dc/terms/",
}


def _field(name: str, description: str, data_type: str,
           derived_from: Optional[str] = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "@type": "cr:Field",
        "@id": f"records/{name}",
        "name": name,
        "description": description,
        "dataType": data_type,
    }
    if derived_from:
        body["prov:wasDerivedFrom"] = derived_from
    return body


_TYPES = {
    "int32": "sc:Integer", "int64": "sc:Integer", "float64": "sc:Float",
    "bool": "sc:Boolean", "category": "sc:Text", "string": "sc:Text",
    "date": "sc:Date", "timestamp": "sc:DateTime", "text": "sc:Text",
}


def to_croissant(plan: MaterialisationPlan, receipt: Receipt,
                 dataset: Dataset) -> dict[str, Any]:
    """Build the JSON-LD document. Pure: no file access, no clock."""
    fields: list[dict[str, Any]] = []
    for column in plan.shape.columns:
        if column.path not in dataset.columns:
            # Refused at build (receipt.columns_refused) or dropped by shaping: not in the file.
            continue
        s = column.source
        fields.append(_field(
            column.path,
            f"{s.relation} over {s.entity}.{s.field}"
            + (f", cut on system time {s.system_time_field}" if s.system_time_field else ""),
            _TYPES.get(column.type, "sc:Text"),
            derived_from=f"{plan.temporal.source_snapshots[0].source_id}#{s.entity}.{s.field}"
            if plan.temporal.source_snapshots else None))
        if plan.shape.emit_missing_reason:
            fields.append(_field(
                f"{column.path}_missing_reason",
                "Why this cell holds no value: not_in_source | grounding_dropped | "
                "policy_suppressed. Three different facts; never summed.",
                "sc:Text"))
    if plan.shape.label is not None:
        label = plan.shape.label
        fields.append(_field(
            label.path,
            f"LABEL. {label.source.entity}.{label.source.field}, cut on VALID time "
            f"({label.source.valid_time_field}) strictly after the decision instant, read at the "
            f"latest system time, matured by {plan.temporal.label_maturity_lag}.",
            _TYPES.get(label.type, "sc:Text")))

    gaps = (f"{receipt.cells.unresolved} cells not in the source; "
            f"{receipt.cells.policy_suppressed} withheld by policy; "
            f"{receipt.cells.grounding_dropped} dropped by the grounding wall. Reported apart: "
            f"summing them would hide a covert channel. "
            f"{receipt.rows.rejected_by_maturity} rows were rejected because their outcome was "
            f"not yet RECORDED at the maturity cutoff, or whose label window had not closed — "
            f"they are not negative examples. {receipt.rows.rejected_unlabelled} rows were "
            f"excluded because their window closed with no outcome recorded inside it. "
            f"{len(receipt.columns_refused)} feature columns were refused because no value was "
            f"known at any emitted row's decision instant.")

    document: dict[str, Any] = {
        "@context": _CONTEXT,
        "@type": "sc:Dataset",
        "conformsTo": f"http://mlcommons.org/croissant/{CROISSANT_VERSION}",
        "name": plan.label or plan.id,
        "identifier": plan.id,
        "description": (
            f"Materialised from Walnut MaterialisationPlan {plan.plan_hash}. Features are cut on "
            f"SYSTEM time (what the organisation had recorded at each row's decision instant); "
            f"labels are cut on VALID time and read at the latest system time. Every cell carries "
            f"a SourcePointer or is a named non-answer."),
        "version": str(plan.scheme_version),
        "datePublished": receipt.executed_at.isoformat(),
        "cr:planHash": plan.plan_hash,
        "cr:contentHash": receipt.content_hash,
        "cr:shapedBy": receipt.shaped_by,
        "cr:reproducible": receipt.reproducible,
        "prov:wasAttributedTo": {
            "@type": "prov:Agent",
            "name": plan.authored_by.agent,
            "prov:actedOnBehalfOf": plan.authored_by.on_behalf_of,
        },
        "prov:wasGeneratedBy": {
            "@type": "prov:Activity",
            "name": ("agent-shaped materialisation" if receipt.shaped_by == "agent"
                     else "plan-shaped materialisation"),
            "description": receipt.honest_summary,
            "endedAtTime": receipt.executed_at.isoformat(),
        },
        "prov:wasDerivedFrom": [
            {
                "@type": "prov:Entity",
                "@id": s.source_id,
                "cr:pinningTier": s.tier,
                "cr:asOf": s.as_of.isoformat(),
                "cr:contentHash": s.content_hash,
                "cr:captureRef": s.capture_ref,
            }
            for s in plan.temporal.source_snapshots
        ],
        "odrl:hasPolicy": {
            "@type": "odrl:Set",
            "odrl:permission": [{
                "odrl:action": "use",
                "odrl:constraint": [
                    {"odrl:leftOperand": "purpose", "odrl:operator": "eq",
                     "odrl:rightOperand": plan.policy.purpose},
                    {"odrl:leftOperand": "egress", "odrl:operator": "eq",
                     "odrl:rightOperand": plan.policy.egress},
                ],
            }],
            "dct:rightsHolder": plan.policy.lawful_basis,
        },
        "cr:dataGapsAndShortcomings": gaps,
        "recordSet": [{
            "@type": "cr:RecordSet",
            "@id": "records",
            "name": "records",
            "description": f"{receipt.rows.emitted} rows under row_order "
                           f"{list(plan.determinism.row_order)}",
            "field": fields,
        }],
        "cr:validated": False,
        "cr:validationNote": (
            "This document was generated from the plan and the receipt and has NOT been run "
            "through mlcroissant's validator in this build. Claiming a validation nobody "
            "performed is the documentation defect the standard exists to fix."),
    }
    return document


def write_croissant(plan: MaterialisationPlan, receipt: Receipt, dataset: Dataset,
                    path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_croissant(plan, receipt, dataset), indent=2,
                               ensure_ascii=True, sort_keys=False) + "\n", encoding="utf-8")
    return path


__all__ = ["CROISSANT_VERSION", "to_croissant", "write_croissant"]
