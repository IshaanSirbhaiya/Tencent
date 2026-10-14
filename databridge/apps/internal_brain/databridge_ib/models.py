"""The Internal Brain's contracts — every workstream builds against these types (plan §4, C0).

Nothing here does I/O. Changing a field is a coordinator decision: the mock platforms, the
connectors, the pipeline, the ledger and the frontend all read these shapes.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

PLATFORMS = ("slack", "confluence", "jira", "drive")
Platform = Literal["slack", "confluence", "jira", "drive"]

#: The ONE sentence every answer carries, whatever happened — never varies by outcome (plan §4.6).
SCOPE_NOTE = ("Answers draw only on content you can access in Confluence, Jira, Slack and "
              "Google Drive.")
#: The ONE refusal text — identical for "restricted", "does not exist" and "not relevant" (P2).
REFUSAL_TEXT = "I couldn't find an answer to that in the sources you can access."


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --------------------------------------------------------------------------------------------
# Directory (who people are) — native platforms stay authoritative; this is a cache (Haiku M25)
# --------------------------------------------------------------------------------------------
class UserKind(str, Enum):
    MEMBER = "member"            # full workspace member / employee
    GUEST = "guest"              # Slack single/multi-channel guest (is_restricted/ultra_restricted)
    CONNECT = "connect"          # Slack Connect external participant


class DirectoryUser(_Frozen):
    id: str                                         # namespaced IB id, e.g. "jdoe"
    name: str
    email: str
    employment: Literal["employee", "contractor"]
    kind: UserKind = UserKind.MEMBER
    active: bool = True
    roles: tuple[str, ...] = ()                     # IB roles: employee | compliance_officer | admin_it
    groups: tuple[str, ...] = ()                    # EXPANDED group ids (nested groups flattened)
    platform_ids: dict[str, str] = Field(default_factory=dict)   # platform -> native account id

    @property
    def domain(self) -> str:
        return self.email.rsplit("@", 1)[-1].lower()


# --------------------------------------------------------------------------------------------
# ACL expressions — policy, never a flattened identifier set (Codex H12). Evaluated in acl.py.
# --------------------------------------------------------------------------------------------
class AclUsers(_Frozen):
    t: Literal["users"] = "users"
    ids: tuple[str, ...]                            # IB user ids


class AclGroups(_Frozen):
    t: Literal["groups"] = "groups"
    ids: tuple[str, ...]


class AclKind(_Frozen):
    t: Literal["kind"] = "kind"
    kinds: tuple[UserKind, ...]                     # principal.kind must be one of these


class AclDomain(_Frozen):
    t: Literal["domain"] = "domain"
    domain: str                                     # verified company domain of the principal


class AclEveryone(_Frozen):
    t: Literal["everyone"] = "everyone"             # every ACTIVE directory user (combine with kind)


class AclNobody(_Frozen):
    """Fail-closed: unsupported configuration, unknown ACL, compile error (plan §4.3)."""
    t: Literal["nobody"] = "nobody"
    reason: str = "unsupported"


class AclAll(_Frozen):
    t: Literal["all"] = "all"
    of: tuple["AclExpr", ...]


class AclAny(_Frozen):
    t: Literal["any"] = "any"
    of: tuple["AclExpr", ...]


AclExpr = Union[AclUsers, AclGroups, AclKind, AclDomain, AclEveryone, AclNobody, AclAll, AclAny]
AclAll.model_rebuild()
AclAny.model_rebuild()


# --------------------------------------------------------------------------------------------
# Source documents and changes (connector output)
# --------------------------------------------------------------------------------------------
class SourceDoc(_Frozen):
    doc_id: str                 # "<platform>:<native id>", e.g. "confluence:cf-1001", "slack:C01:1712.0001"
    platform: Platform
    kind: Literal["page", "issue", "thread", "file"]
    container: str              # space key / project key / channel id / shared-drive id
    container_name: str
    title: str
    text: str                   # plain text, the ONLY content indexed
    version: str                # platform version token (page version, issue updated, latest ts, revision)
    created_at: datetime
    updated_at: datetime
    deep_link: str
    author: Optional[str] = None                    # IB user id when known
    links: tuple[str, ...] = ()                     # raw identifiers found in text (§4.2 step 7)


class ChangeEvent(_Frozen):
    platform: Platform
    type: Literal["content.changed", "content.deleted", "acl.changed", "membership.changed",
                  "user.changed"]
    object_ids: tuple[str, ...] = ()                # native ids (doc ids for content/acl events)
    container: Optional[str] = None                 # set when a container-wide ACL changed
    user: Optional[str] = None                      # IB user id for membership/user events
    delivery_id: str
    at: datetime


class ChangeBatch(_Frozen):
    docs: tuple[SourceDoc, ...] = ()
    deleted: tuple[str, ...] = ()                   # doc_ids
    acl_changed: tuple[str, ...] = ()               # doc_ids whose ACL must be recompiled
    cursor: Optional[str] = None


class AccessCheck(_Frozen):
    """Gate 2's answer for one (principal, doc) — mapped onto Walnut's verdict lattice."""
    doc_id: str
    verdict: Literal["verified", "drift_material", "vanished", "forbidden", "unverifiable"]
    current_version: Optional[str] = None
    reason_code: str = ""                           # ledger-only, NEVER returned to the asker
    checked_at: datetime

    @property
    def allowed(self) -> bool:
        return self.verdict in ("verified", "drift_material")


# --------------------------------------------------------------------------------------------
# API shapes
# --------------------------------------------------------------------------------------------
class AskRequest(_Frozen):
    question: str = Field(min_length=1, max_length=2000)   # NO identity field, ever (P8)


class Citation(_Frozen):
    tag: str                    # "C1"
    doc_id: str
    platform: Platform
    container_name: str
    title: str
    deep_link: str
    version: str
    updated_at: datetime
    content_checked_at: datetime
    authz_checked_at: datetime
    live_read: bool = False     # content re-fetched at answer time (Confluence drift read-through)
    stale: bool = False         # source lag beyond SLO


class ModelInfo(_Frozen):
    provider: str               # "tencent-tokenhub" | "deterministic"
    model: str                  # "hy3" | "deepseek-v4.1-flash" | "extractive-v1"
    fallback_used: bool = False


class AskResponse(_Frozen):
    request_id: str
    answer: str
    citations: tuple[Citation, ...] = ()
    scope_note: str = SCOPE_NOTE
    model: ModelInfo
    as_of: datetime


# --------------------------------------------------------------------------------------------
# Ledger events (A5). Field names are the audit contract; values never include passage text.
# --------------------------------------------------------------------------------------------
IB_EVENT = "ib_event"
EVENT_KINDS = ("request.accepted", "retrieval.decided", "answer.released", "request.failed",
               "audit.query", "audit.reveal", "acl.changed", "membership.changed",
               "directory.changed", "ingest.sync", "anchor.published", "map.viewed", "auth.login")


class Gate2Decision(_Frozen):
    doc_id: str
    verdict: str
    reason_code: str


class EventBase(_Frozen):
    kind: str
    req_id: Optional[str] = None
    event_time: datetime                            # when it happened (backdated for replay)
    synthetic_replay: bool = False                  # S5 seeded history is labelled (Codex H25)


class RequestAccepted(EventBase):
    kind: Literal["request.accepted"] = "request.accepted"
    sub: str
    channel: Literal["ui", "workbuddy-mcp", "api", "replay"]
    question: str
    question_sha256: str
    policy_version: str
    directory_epoch: int


class RetrievalDecided(EventBase):
    kind: Literal["retrieval.decided"] = "retrieval.decided"
    sub: str
    time_window: Optional[tuple[datetime, datetime]] = None
    gate1_policy_version: str
    considered: tuple[str, ...]                     # post-Gate-1 top-K doc ids (the audited universe)
    considered_meta: tuple[dict[str, Any], ...] = ()   # {doc_id, platform, container, title}
    gate2: tuple[Gate2Decision, ...]
    released: tuple[str, ...]


class AnswerReleased(EventBase):
    kind: Literal["answer.released"] = "answer.released"
    sub: str
    model: ModelInfo
    prompt_version: str
    answer_sha256: str
    answer_ref: str                                 # key in the separate content store
    refused: bool
    citations: tuple[dict[str, Any], ...]          # {doc_id, version, content_checked_at, authz_checked_at}
    dropped_sentences_count: int = 0
    latency_ms: int


class RequestFailed(EventBase):
    kind: Literal["request.failed"] = "request.failed"
    sub: str
    stage: str
    error_code: str


class AuditQueryEvent(EventBase):
    kind: Literal["audit.query"] = "audit.query"
    sub: str
    filter: dict[str, Any]
    result_count: int


class SessionStarted(EventBase):
    """Someone signed in (demo sign-in stands in for SSO). Written before the session is issued, so
    who could act as whom is part of the audited record (Opus review #8)."""
    kind: Literal["auth.login"] = "auth.login"
    sub: str
    method: Literal["demo", "sso"] = "demo"
    roles: tuple[str, ...] = ()


class MapViewed(EventBase):
    """A person opened their knowledge map: the titles of documents THEY may read (write-ahead,
    like every other access)."""
    kind: Literal["map.viewed"] = "map.viewed"
    sub: str
    docs_listed: int                                # items counted (each confirmed live)
    items_returned: int = 0                         # item entries actually sent (capped per space)
    containers_listed: int
    policy_version: str


class AuditReveal(EventBase):
    kind: Literal["audit.reveal"] = "audit.reveal"
    sub: str
    target_req_id: str
    reason: str


class SourceChangeEvent(EventBase):
    kind: Literal["acl.changed", "membership.changed", "directory.changed", "ingest.sync"]
    platform: Optional[str] = None
    detail: dict[str, Any] = Field(default_factory=dict)


class AnchorPublished(EventBase):
    kind: Literal["anchor.published"] = "anchor.published"
    seq: int
    head: str
    sink: str
