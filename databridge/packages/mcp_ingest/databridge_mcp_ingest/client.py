"""A small synchronous MCP client surface over two routes to a server, with warm session reuse.

* **direct** — the python `mcp` SDK, stdio or streamable HTTP. The SDK is async and its transports
  are anyio task groups that must be entered and exited in ONE task, so the session lives in a
  single long-running task on a private event loop thread; calls are submitted to that loop.
* **mcpjungle** — the aggregator's HTTP API (`jungle.py`), over ONE keep-alive HTTP client per
  base URL. MCPJungle proxies tools AND resources (verified 2026-10-01): a resource's URI is
  rewritten to `mcpj://res/<server>/<b64url(original)>`, and this client maps it back, so an item's
  identity (`sha256(mcp://<source_id>/<uri>)`) is the same whichever route read it.

**Session pooling.** Starting a stdio server is the dominant MCP latency cost (an `npx -y`
package: seconds; a python server: ~1 s). `open_client(conn)` therefore LEASES a warm session from
a process-wide `SessionPool` and returns it on exit instead of closing it:
  * keyed by the connection's resolved route (command/args/env/cwd or URL/headers), so a changed
    declaration never reuses a stale session;
  * a lease is exclusive — two concurrent runs of one source get two sessions;
  * bounded (`WALNUT_MCP_POOL_MAX`, default 8 idle sessions, LRU-evicted) with an idle timeout
    (`WALNUT_MCP_POOL_IDLE_S`, default 300 s) swept by a daemon thread;
  * a session whose server died is discarded before reuse (reconnect = a fresh session); a call
    that timed out marks its session suspect and it is not returned to the pool. A failed call is
    NEVER retried — a retry could repeat a write;
  * `WALNUT_MCP_POOL=0` (or `open_client(..., pooled=False)`) disables it.

Every result is normalised to `ContentPart`s so crawl code never sees transport shapes.
"""
from __future__ import annotations

import asyncio
import atexit
import base64
import concurrent.futures
import contextlib
import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from .connections import HttpTransport, SourceConnection, StdioTransport

DEFAULT_TIMEOUT = 60.0


class McpClientError(RuntimeError):
    """The server could not be reached, or refused a call. Message is plain words."""


class ToolChanged(McpClientError):
    """A plan's tool is no longer the one a person approved: it changed on the server (its
    description, schema or annotations), or the server no longer offers it (FXJ3, Codex f2 #2).
    The runner treats it as a HOLD awaiting a re-proposed, re-approved plan, the way it reports a
    shape drift — not as a failure of the source (INT5, R-022 sources cross-track 5)."""


@dataclass(frozen=True)
class ToolInfo:
    name: str
    description: str = ""
    input_schema: dict = field(default_factory=dict)
    read_only: Optional[bool] = None
    destructive: Optional[bool] = None

    def as_record(self) -> dict:
        return {"name": self.name, "description": self.description,
                "input_schema": self.input_schema, "read_only": self.read_only,
                "destructive": self.destructive}


@dataclass(frozen=True)
class ResourceInfo:
    uri: str
    name: str = ""
    mime: Optional[str] = None
    size: Optional[int] = None


@dataclass(frozen=True)
class ContentPart:
    kind: str                     # "text" | "blob"
    text: str = ""
    data: bytes = b""
    mime: Optional[str] = None
    uri: Optional[str] = None


@dataclass(frozen=True)
class ToolResult:
    parts: tuple[ContentPart, ...]
    is_error: bool = False
    structured: Optional[Any] = None
    """The MCP result's `structuredContent` (typed JSON beside the text blocks), kept end to end
    (R-021 agent-runtime / RTF). None when the server sent none."""

    @property
    def text(self) -> str:
        return "\n".join(p.text for p in self.parts if p.kind == "text")

    def first_blob(self) -> Optional[ContentPart]:
        return next((p for p in self.parts if p.kind == "blob"), None)


def parts_from_dicts(content: list) -> tuple[ContentPart, ...]:
    """MCP content blocks (as dicts — the SDK's `model_dump` or MCPJungle's JSON) → parts."""
    out: list[ContentPart] = []
    for block in content or []:
        if not isinstance(block, dict):
            block = getattr(block, "model_dump", lambda **_: {})(by_alias=True)
        kind = block.get("type")
        if kind == "text":
            out.append(ContentPart(kind="text", text=str(block.get("text") or "")))
        elif kind in ("image", "audio"):
            out.append(ContentPart(kind="blob", data=_b64(block.get("data")),
                                   mime=block.get("mimeType")))
        elif kind == "resource":
            res = block.get("resource") or {}
            if "blob" in res and res.get("blob") is not None:
                data = _b64(res.get("blob"))
                _check_meta(res, data)
                out.append(ContentPart(kind="blob", data=data,
                                       mime=res.get("mimeType"), uri=res.get("uri")))
            else:
                out.append(ContentPart(kind="text", text=str(res.get("text") or ""),
                                       mime=res.get("mimeType"), uri=res.get("uri")))
        # resource_link and unknown block types carry no content to capture; ignored.
    return tuple(out)


_B64_WS = re.compile(r"\s+")


def _b64(value: Any) -> bytes:
    """STRICT base64 (Codex f1 #13): the standard alphabet with correct padding; only line-break /
    space characters are removed first (MIME-wrapped payloads). A character outside the alphabet
    or bad padding FAILS the item — `validate=False` would silently drop it and capture altered
    bytes under a digest that does not describe what the server holds."""
    if not value:
        return b""
    text = value.decode("ascii", "replace") if isinstance(value, (bytes, bytearray)) else str(value)
    try:
        return base64.b64decode(_B64_WS.sub("", text), validate=True)
    except Exception as exc:                                     # noqa: BLE001
        raise McpClientError(f"a content block claimed base64 data that did not decode "
                             f"strictly ({type(exc).__name__}); the item is failed, not captured"
                             ) from None


def _check_meta(res: dict, data: bytes) -> None:
    """A server that states the digest/size of the bytes it sent (`_meta.sha256`, `_meta.size` —
    Walnut's files_blob server does) is held to it: a mismatch means the bytes were altered or
    truncated in transit, and the item FAILS (the runner records it in failed.jsonl) rather than
    being captured under a digest that does not describe the original."""
    meta = res.get("_meta") or res.get("meta") or {}
    if not isinstance(meta, dict):
        return
    want = meta.get("sha256")
    if want:
        got = hashlib.sha256(data).hexdigest()
        if str(want).lower().removeprefix("sha256:") != got:
            raise McpClientError(
                f"integrity check failed for {res.get('uri') or 'an embedded resource'}: the "
                f"server declared sha256 {str(want)[:16]}... but the bytes received hash to "
                f"{got[:16]}...")
    size = meta.get("size")
    if isinstance(size, int) and size != len(data):
        raise McpClientError(
            f"integrity check failed for {res.get('uri') or 'an embedded resource'}: the server "
            f"declared {size} bytes but {len(data)} arrived")


def _resource_parts(contents: list[dict]) -> tuple[ContentPart, ...]:
    parts: list[ContentPart] = []
    for d in contents:
        parts.extend(parts_from_dicts([{"type": "resource", "resource": d}]))
    return tuple(parts)


class McpClient(Protocol):
    def list_tools(self) -> list[ToolInfo]: ...
    def call_tool(self, name: str, arguments: dict) -> ToolResult: ...
    def list_resources(self) -> list[ResourceInfo]: ...
    def read_resource(self, uri: str) -> tuple[ContentPart, ...]: ...
    def close(self) -> None: ...


# ================================================================================================
# list_changed: a server says its tools / resources changed (real-time drift, BUILD_PLAN §5.6)
# ================================================================================================
LIST_CHANGED_METHODS = {"notifications/tools/list_changed": "tools",
                        "notifications/resources/list_changed": "resources"}
_LIST_CHANGED: list = []
_LIST_CHANGED_LOCK = threading.Lock()
_LAST_FIRED: dict[tuple[str, str], float] = {}
_HOOK_INSTALLED = False


def _debounce_s() -> float:
    try:
        return max(0.0, float(os.environ.get("WALNUT_LIST_CHANGED_DEBOUNCE_S", "2")))
    except ValueError:
        return 2.0


def add_list_changed_listener(fn) -> Any:
    """Register `fn(connection_id, kind)`, called when a server this process holds a session with
    (pooled ones included — an idle pooled session keeps listening) sends
    `notifications/tools/list_changed` (kind `tools`) or `notifications/resources/list_changed`
    (kind `resources`). `fn` runs on the session's event-loop thread and MUST NOT block (hand the
    work to a thread). Registering the same function twice is a no-op. The same
    (connection, kind) arriving again within `WALNUT_LIST_CHANGED_DEBOUNCE_S` (default 2 s) — two
    open sessions of one source, or a chatty server — is delivered once. Returns a callable that
    removes the listener."""
    with _LIST_CHANGED_LOCK:
        if fn not in _LIST_CHANGED:
            _LIST_CHANGED.append(fn)

    def remove() -> None:
        with _LIST_CHANGED_LOCK:
            if fn in _LIST_CHANGED:
                _LIST_CHANGED.remove(fn)
    return remove


def list_changed_listeners() -> list:
    with _LIST_CHANGED_LOCK:
        return list(_LIST_CHANGED)


def clear_list_changed_listeners() -> None:
    """Remove every listener and forget the debounce state (tests; a gateway shutting down)."""
    global _HOOK_INSTALLED
    with _LIST_CHANGED_LOCK:
        _LIST_CHANGED.clear()
        _LAST_FIRED.clear()
        _IN_FLIGHT.clear()
        _UNDURABLE.clear()
        _GENERATION[0] += 1          # retries scheduled before this point stop at their next tick
        _HOOK_INSTALLED = False


# --- durable delivery (FXI3, Codex f1 #14) --------------------------------------------------------
#: (connection, kind) -> the event id being delivered (incl. its retries). A repeat that arrives
#: meanwhile is covered by it (coalesced), so a failing listener never multiplies events.
_IN_FLIGHT: dict[tuple[str, str], str] = {}
_QUEUE_LOCK = threading.Lock()
#: Bumped by `clear_list_changed_listeners`: a retry from an earlier generation does nothing (it
#: must not mark an event delivered, or start a debounce window, for listeners that are gone).
_GENERATION = [0]


def list_changed_queue_path():
    """The append-only list_changed journal: `WALNUT_LIST_CHANGED_QUEUE`, else
    `<sources>/_events/list_changed.jsonl`. Records: `received`, `listener_failed`, `delivered`,
    `gave_up`, `replayed`. An event with `received` and no `delivered` is PENDING, and
    `replay_list_changed()` delivers it again (the gateway's boot hook calls it)."""
    from pathlib import Path

    from . import paths

    override = os.environ.get("WALNUT_LIST_CHANGED_QUEUE")
    return Path(override) if override else paths.sources_dir() / "_events" / "list_changed.jsonl"


def _journal(rec: dict) -> bool:
    """Append one record (append mode, flushed + fsynced). False when it could not be written —
    reported by the caller, never raised into the session's event loop."""
    rec = {**rec, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        path = list_changed_queue_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _QUEUE_LOCK, open(path, "ab") as fh:
            fh.write(json.dumps(rec, sort_keys=True, default=str).encode("utf-8") + b"\n")
            fh.flush()
            os.fsync(fh.fileno())
        return True
    except OSError as exc:
        import logging

        logging.getLogger(__name__).error("list_changed journal could not be written (%s) for "
                                          "%s record of event %s", exc, rec.get("event"),
                                          rec.get("id"))
        return False


def pending_list_changed() -> list[dict]:
    """`received` events with no `delivered` record, oldest first."""
    path = list_changed_queue_path()
    if not path.is_file():
        return []
    received: dict[str, dict] = {}
    done: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue                        # a torn last line: the rest still counts
        if rec.get("event") == "received":
            received[str(rec.get("id"))] = rec
        elif rec.get("event") == "delivered":
            done.add(str(rec.get("id")))
    return [r for i, r in received.items() if i not in done]


def _retry_limits() -> tuple[int, float]:
    try:
        n = max(0, int(os.environ.get("WALNUT_LIST_CHANGED_RETRIES", "5")))
    except ValueError:
        n = 5
    try:
        base = max(0.0, float(os.environ.get("WALNUT_LIST_CHANGED_RETRY_S", "1")))
    except ValueError:
        base = 1.0
    return n, base


def _fn_name(fn) -> str:
    return f"{getattr(fn, '__module__', '?')}.{getattr(fn, '__qualname__', type(fn).__name__)}"


def _deliver(event_id: str, connection_id: str, kind: str, listeners: list,
             attempt: int, generation: Optional[int] = None) -> None:
    """Call each listener; failed ones are journaled and retried with backoff on a daemon timer.
    When every listener has taken it, `delivered` is journaled and the debounce window starts."""
    if generation is None:
        generation = _GENERATION[0]
    elif generation != _GENERATION[0]:
        return                              # the listeners were cleared: stays pending
    failed = []
    for fn in listeners:
        with _LIST_CHANGED_LOCK:
            still = fn in _LIST_CHANGED
        if not still:
            continue                        # removed meanwhile: nothing left to deliver to
        try:
            fn(connection_id, kind)
        except Exception as exc:                                     # noqa: BLE001
            import logging

            logging.getLogger(__name__).warning("list_changed listener %r failed for %s (%s): %s",
                                                fn, connection_id, kind, exc)
            _journal({"event": "listener_failed", "id": event_id, "listener": _fn_name(fn),
                      "attempt": attempt, "error": f"{type(exc).__name__}: {str(exc)[:300]}"})
            failed.append(fn)
    key = (connection_id, kind)
    if not failed:
        if listeners:
            _journal({"event": "delivered", "id": event_id})
            with _LIST_CHANGED_LOCK:
                _LAST_FIRED[key] = time.monotonic()
        with _LIST_CHANGED_LOCK:
            if _IN_FLIGHT.get(key) == event_id:
                del _IN_FLIGHT[key]
        return
    limit, base = _retry_limits()
    if attempt >= limit:
        _journal({"event": "gave_up", "id": event_id, "attempts": attempt + 1,
                  "listeners": [_fn_name(f) for f in failed]})
        with _LIST_CHANGED_LOCK:
            if _IN_FLIGHT.get(key) == event_id:
                del _IN_FLIGHT[key]
        return                              # stays PENDING in the journal: replayed at boot
    timer = threading.Timer(base * (2 ** attempt), _deliver,
                            args=(event_id, connection_id, kind, failed, attempt + 1,
                                  generation))
    timer.daemon = True
    timer.start()


def notify_list_changed(connection_id: str, kind: str) -> int:
    """Deliver one list_changed to every listener. Returns how many listeners were called (0 when
    it was debounced / coalesced, or there are none).

    DURABLE and RETRIED (Codex f1 #14): the event is journaled (`received`) BEFORE any listener
    runs; a listener that raises is journaled and retried with backoff
    (`WALNUT_LIST_CHANGED_RETRIES`, default 5; `WALNUT_LIST_CHANGED_RETRY_S`, default 1 s,
    doubling) without calling the listeners that already took it; an event not taken by every
    listener stays pending and `replay_list_changed()` delivers it again. The debounce window
    (`WALNUT_LIST_CHANGED_DEBOUNCE_S`) starts only once an event was DELIVERED, and a repeat that
    arrives while one is still being delivered is coalesced into it. If the `received` record
    cannot be journaled, NO listener runs (FXJ3, Codex f2 #13): the event is held in memory and the
    journal write retried; it is delivered only once it is durable (returns 0)."""
    import uuid

    key = (connection_id, kind)
    now = time.monotonic()
    with _LIST_CHANGED_LOCK:
        if key in _IN_FLIGHT:
            return 0
        last = _LAST_FIRED.get(key)
        if last is not None and now - last < _debounce_s():
            return 0
        event_id = uuid.uuid4().hex
        listeners = list(_LIST_CHANGED)
        if listeners:
            _IN_FLIGHT[key] = event_id
    received = {"event": "received", "id": event_id, "connection_id": connection_id,
                "kind": kind, "listeners": len(listeners)}
    if not _journal(received):
        # FXJ3 (Codex f2 #13): NOT durable, so NOT delivered. The event is held in memory and the
        # journal write is retried with backoff; only once it is on disk do listeners run. A
        # crash meanwhile loses only an event no listener ever acted on.
        if listeners:
            _hold_undurable(received, 0, _GENERATION[0])
        return 0
    if listeners:
        _deliver(event_id, connection_id, kind, listeners, 0)
    return len(listeners)


#: event id -> the `received` record whose journal write failed (Codex f2 #13). Retried with
#: backoff; `replay_list_changed` retries whatever is still here.
_UNDURABLE: dict[str, dict] = {}


def undurable_list_changed() -> list[dict]:
    """Events received but NOT yet journaled (so not yet delivered), oldest first."""
    with _LIST_CHANGED_LOCK:
        return list(_UNDURABLE.values())


def _hold_undurable(rec: dict, attempt: int, generation: int) -> None:
    key = (str(rec["connection_id"]), str(rec["kind"]))
    with _LIST_CHANGED_LOCK:
        _UNDURABLE[str(rec["id"])] = rec
    limit, base = _retry_limits()
    if attempt >= limit:
        import logging

        logging.getLogger(__name__).error(
            "list_changed %s for %s (%s) could not be journaled after %d attempts; it is held in "
            "memory and NOT delivered until replay_list_changed() can journal it", rec["id"],
            key[0], key[1], attempt + 1)
        with _LIST_CHANGED_LOCK:
            if _IN_FLIGHT.get(key) == rec["id"]:
                del _IN_FLIGHT[key]
        return
    timer = threading.Timer(base * (2 ** attempt), _retry_undurable,
                            args=(str(rec["id"]), attempt + 1, generation))
    timer.daemon = True
    timer.start()


def _retry_undurable(event_id: str, attempt: int, generation: int) -> None:
    if generation != _GENERATION[0]:
        return
    with _LIST_CHANGED_LOCK:
        rec = _UNDURABLE.get(event_id)
    if rec is None:
        return
    if not _journal(rec):
        _hold_undurable(rec, attempt, generation)
        return
    with _LIST_CHANGED_LOCK:
        _UNDURABLE.pop(event_id, None)
    _deliver(event_id, str(rec["connection_id"]), str(rec["kind"]), list_changed_listeners(), 0,
             generation)


def replay_list_changed() -> int:
    """Deliver every PENDING journaled event again (at-least-once) to the listeners registered
    now. Returns how many events were replayed. Called by `install_list_changed_hook`."""
    listeners = list_changed_listeners()
    if not listeners:
        return 0
    n = 0
    for rec in undurable_list_changed():          # Codex f2 #13: journal first, then deliver
        if _journal(rec):
            with _LIST_CHANGED_LOCK:
                _UNDURABLE.pop(str(rec["id"]), None)
    for rec in pending_list_changed():
        cid, kind, eid = str(rec.get("connection_id")), str(rec.get("kind")), str(rec.get("id"))
        with _LIST_CHANGED_LOCK:
            if (cid, kind) in _IN_FLIGHT:
                continue
            _IN_FLIGHT[(cid, kind)] = eid
        _journal({"event": "replayed", "id": eid})
        _deliver(eid, cid, kind, listeners, 0)
        n += 1
    return n


def list_changed_kind(message: Any) -> Optional[str]:
    """`tools` / `resources` when an SDK message is a list_changed notification, else None."""
    root = getattr(message, "root", message)
    return LIST_CHANGED_METHODS.get(getattr(root, "method", None))


def install_list_changed_hook(manager: Any = None) -> bool:
    """Route every server's list_changed to the adapter agents (`AdapterAgentManager.
    on_list_changed(connection_id)`, via `databridge_adapter_agents.install_list_changed_hook`).
    The gateway calls this ONCE at boot; calling it again is a no-op (so a notification is never
    handled twice). False when `databridge_adapter_agents` is not importable here."""
    global _HOOK_INSTALLED
    with _LIST_CHANGED_LOCK:
        if _HOOK_INSTALLED:
            return True
    try:
        from databridge_adapter_agents import install_list_changed_hook as _install
    except ImportError:
        return False
    with _LIST_CHANGED_LOCK:
        if _HOOK_INSTALLED:
            return True
        _HOOK_INSTALLED = True
    try:
        ok = bool(_install(manager))
    except BaseException:
        with _LIST_CHANGED_LOCK:
            _HOOK_INSTALLED = False
        raise
    if not ok:
        with _LIST_CHANGED_LOCK:
            _HOOK_INSTALLED = False
        return ok
    try:
        replay_list_changed()          # events a previous process received but never delivered
    except Exception as exc:                                         # noqa: BLE001
        import logging

        logging.getLogger(__name__).error("list_changed replay failed: %s", exc)
    return ok


def _tool_info(t) -> ToolInfo:
    ann = getattr(t, "annotations", None)
    return ToolInfo(name=t.name, description=t.description or "",
                    input_schema=dict(t.inputSchema or {}),
                    read_only=getattr(ann, "readOnlyHint", None) if ann else None,
                    destructive=getattr(ann, "destructiveHint", None) if ann else None)


# ================================================================================================
# direct: the python MCP SDK on a private loop
# ================================================================================================
class SdkClient:
    supports_resources = True
    route = "direct"

    def __init__(self, conn: SourceConnection, *, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._conn = conn
        self._timeout = timeout
        self._session = None
        self.suspect = False
        """Set when a call timed out: the session may hold a late reply; do not pool it."""
        self._stop: Optional[asyncio.Event] = None
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True,
                                        name=f"mcp-{conn.id}")
        self._thread.start()
        self._ready: concurrent.futures.Future = concurrent.futures.Future()
        t0 = time.perf_counter()
        self._main = asyncio.run_coroutine_threadsafe(self._run(), self._loop)
        try:
            self._ready.result(timeout=timeout)
        except BaseException as exc:
            self._shutdown_loop()
            raise McpClientError(
                f"could not start a session with source {conn.id!r}: {_plain(exc)}") from exc
        self.connect_ms = (time.perf_counter() - t0) * 1000

    @property
    def alive(self) -> bool:
        return self._session is not None and not self._main.done() and self._loop.is_running()

    async def _run(self) -> None:
        from mcp import ClientSession

        self._stop = asyncio.Event()
        conn_id = self._conn.id

        async def on_message(message) -> None:
            # every server→client message passes here; only list_changed is acted on. Listeners
            # must not block (the adapter-agent hook starts a thread), so this stays cheap.
            kind = list_changed_kind(message)
            if kind is not None:
                notify_list_changed(conn_id, kind)

        try:
            async with _sdk_streams(self._conn, self._timeout) as (read, write):
                async with ClientSession(read, write, message_handler=on_message) as session:
                    await session.initialize()
                    self._session = session
                    self._ready.set_result(True)
                    await self._stop.wait()
        except BaseException as exc:                             # noqa: BLE001
            if not self._ready.done():
                self._ready.set_exception(exc)
            else:
                raise
        finally:
            self._session = None

    def _call(self, coro_factory):
        session = self._session
        if session is None or self._main.done():
            raise McpClientError("session is closed (the server exited or was never started)")
        fut = asyncio.run_coroutine_threadsafe(coro_factory(session), self._loop)
        try:
            return fut.result(timeout=self._timeout)
        except concurrent.futures.TimeoutError as exc:
            fut.cancel()
            self.suspect = True
            raise McpClientError(f"the server did not answer within {self._timeout:.0f}s") from exc

    def list_tools(self) -> list[ToolInfo]:
        tools: list[ToolInfo] = []
        cursor = None
        while True:
            page = self._call(lambda s, c=cursor: s.list_tools(cursor=c))
            tools.extend(_tool_info(t) for t in page.tools)
            cursor = getattr(page, "nextCursor", None)
            if not cursor:
                return tools

    def call_tool_raw(self, name: str, arguments: dict) -> dict:
        """`{content: [block dicts], structured, is_error}` — the §5.4 shape."""
        result = self._call(lambda s: s.call_tool(name, arguments))
        return {"content": [c.model_dump(by_alias=True, exclude_none=True)
                            for c in result.content],
                "structured": getattr(result, "structuredContent", None),
                "is_error": bool(result.isError)}

    def call_tool(self, name: str, arguments: dict) -> ToolResult:
        raw = self.call_tool_raw(name, arguments)
        return ToolResult(parts=parts_from_dicts(raw["content"]), is_error=raw["is_error"],
                          structured=raw.get("structured"))

    def list_resources(self) -> list[ResourceInfo]:
        out: list[ResourceInfo] = []
        cursor = None
        caps = None
        with contextlib.suppress(Exception):
            caps = self._session.get_server_capabilities() if self._session else None
        if caps is not None and getattr(caps, "resources", None) is None:
            return []                       # the server declares no resources capability
        while True:
            try:
                page = self._call(lambda s, c=cursor: s.list_resources(cursor=c))
            except McpClientError:
                raise
            except Exception as exc:                                 # noqa: BLE001
                # Codex f1 #10: ONLY a verified absence is "no resources" — JSON-RPC
                # METHOD_NOT_FOUND on the FIRST page. Anything else (auth, transport, a malformed
                # page, a failed second page) RAISES: the listing is unobserved, never empty.
                if cursor is None and getattr(getattr(exc, "error", None), "code",
                                              None) == -32601:
                    return []
                raise McpClientError(f"resources could not be listed"
                                     f"{' (page after ' + str(len(out)) + ' items)' if cursor else ''}"
                                     f": {_plain(exc)}") from exc
            for r in page.resources:
                out.append(ResourceInfo(uri=str(r.uri), name=r.name or "",
                                        mime=getattr(r, "mimeType", None),
                                        size=getattr(r, "size", None)))
            cursor = getattr(page, "nextCursor", None)
            if not cursor:
                return out

    supports_resource_templates = True

    def list_resource_templates(self) -> list[dict]:
        """Every resource template the server declares, paged: `[{uriTemplate, name, mimeType}]`
        (the drift probe's `resource_templates` facet). A server that does not implement
        `resources/templates/list` (JSON-RPC METHOD_NOT_FOUND, or no `resources` capability)
        has none: `[]`. Any other failure RAISES — an empty answer from a failed call would read
        as "every template was removed"."""
        caps = None
        with contextlib.suppress(Exception):
            caps = self._session.get_server_capabilities() if self._session else None
        if caps is not None and getattr(caps, "resources", None) is None:
            return []
        out: list[dict] = []
        cursor = None
        while True:
            try:
                page = self._call(lambda s, c=cursor: s.list_resource_templates(cursor=c))
            except McpClientError:
                raise
            except Exception as exc:                                 # noqa: BLE001
                # FXJ3 (Codex f2 #14): METHOD_NOT_FOUND means "no templates" ONLY on the FIRST
                # page; on a later page it is a failure, never a wholesale removal.
                if cursor is None and getattr(getattr(exc, "error", None), "code",
                                              None) == -32601:
                    return []                   # METHOD_NOT_FOUND: the server has no templates
                raise McpClientError(f"resource templates could not be listed"
                                     f"{' (page after ' + str(len(out)) + ' templates)' if cursor else ''}"
                                     f": {_plain(exc)}") from exc
            for t in page.resourceTemplates:
                out.append({"uriTemplate": str(t.uriTemplate), "name": t.name or "",
                            "mimeType": getattr(t, "mimeType", None)})
            cursor = getattr(page, "nextCursor", None)
            if not cursor:
                return out

    def read_resource_raw(self, uri: str) -> list[dict]:
        from pydantic import AnyUrl

        result = self._call(lambda s: s.read_resource(AnyUrl(uri)))
        out = []
        for c in result.contents:
            d = c.model_dump(by_alias=True, exclude_none=True)
            out.append({**d, "uri": str(d.get("uri") or uri)})
        return out

    def read_resource(self, uri: str) -> tuple[ContentPart, ...]:
        return _resource_parts(self.read_resource_raw(uri))

    def close(self) -> None:
        if self._stop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._stop.set)
            with contextlib.suppress(Exception):
                self._main.result(timeout=10)
        self._session = None
        self._shutdown_loop()

    def _shutdown_loop(self) -> None:
        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)


def connect_destination_refusal(url: str) -> Optional[str]:
    """The SSRF policy AT CONNECT TIME (R-019 round 2 D4 / FXI3): the host is RE-RESOLVED on
    every connection and every request, and every address must be global unless the host is on
    `WALNUT_SOURCE_ALLOWED_HOSTS` — the same rule and allow-list as registration
    (`connections.destination_refusal`), so DNS rebinding after registration is caught."""
    from .connections import destination_refusal

    return destination_refusal(url)


#: Test seam: an httpx transport for `guarded_http_client` (None = the network).
_HTTP_TRANSPORT = None


def guarded_http_client(headers: Optional[dict] = None, timeout=None, auth=None):
    """The httpx client for streamable-HTTP sessions (`httpx_client_factory`). Like the SDK's
    default, but EVERY request — the first and each redirect hop — is re-checked by
    `connect_destination_refusal` in a request hook before it is sent; a redirect to a private /
    loopback / link-local / unresolvable address is refused, never followed."""
    import httpx

    async def _check(request) -> None:
        why = await asyncio.to_thread(connect_destination_refusal, str(request.url))
        if why:
            raise McpClientError(f"refused to send a request to {request.url.host!r}: {why}")

    kwargs: dict = {"follow_redirects": True, "event_hooks": {"request": [_check]},
                    "timeout": timeout if timeout is not None else httpx.Timeout(30.0,
                                                                                 read=300.0)}
    if headers is not None:
        kwargs["headers"] = headers
    if auth is not None:
        kwargs["auth"] = auth
    if _HTTP_TRANSPORT is not None:
        kwargs["transport"] = _HTTP_TRANSPORT
    return httpx.AsyncClient(**kwargs)


@contextlib.asynccontextmanager
async def _sdk_streams(conn: SourceConnection, timeout: float):
    t = conn.transport
    if isinstance(t, StdioTransport):
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client

        command, args, cwd = conn.resolved_command()
        env = conn.resolved_env() or None
        params = StdioServerParameters(command=command, args=args, env=env, cwd=cwd)
        async with stdio_client(params) as (read, write):
            yield read, write
    elif isinstance(t, HttpTransport):
        from mcp.client.streamable_http import streamablehttp_client

        url = conn.resolved_url()
        why = await asyncio.to_thread(connect_destination_refusal, url)
        if why:
            raise McpClientError(f"refused to connect source {conn.id!r}: {why}")
        async with streamablehttp_client(url, headers=conn.resolved_headers() or None,
                                         timeout=timeout,
                                         httpx_client_factory=guarded_http_client
                                         ) as (read, write, _sid):
            yield read, write
    else:                                                        # pragma: no cover
        raise McpClientError(f"unknown transport {t!r}")


def _plain(exc: BaseException) -> str:
    """Unwrap anyio ExceptionGroups into the first real reason, in plain words."""
    seen = exc
    while isinstance(seen, BaseExceptionGroup) and seen.exceptions:
        seen = seen.exceptions[0]
    return f"{type(seen).__name__}: {seen}"


# ================================================================================================
# via MCPJungle
# ================================================================================================
class _MinimalJungle:
    """A tools-only aggregator client (kept for compatibility; `jungle.JungleAPI` is the default
    and also speaks resources and registration)."""

    def __init__(self, base_url: Optional[str] = None, transport=None) -> None:
        self.base_url = (base_url or os.environ.get("MCPJUNGLE_URL")
                         or "http://127.0.0.1:8080").rstrip("/")
        self._transport = transport

    def _req(self, method: str, path: str, body: Optional[dict] = None, timeout: float = 30.0):
        import httpx

        try:
            with httpx.Client(timeout=timeout, transport=self._transport) as client:
                r = client.request(method, f"{self.base_url}{path}", json=body)
        except httpx.HTTPError as exc:
            raise McpClientError(f"MCPJungle at {self.base_url} is not reachable: {exc}") from exc
        if r.status_code >= 400:
            raise McpClientError(f"MCPJungle refused {path}: HTTP {r.status_code} {r.text[:300]}")
        return r.json() if r.content else None

    def tools(self) -> list[dict]:
        return [t for t in (self._req("GET", "/api/v0/tools") or []) if isinstance(t, dict)]

    def invoke_tool(self, name: str, arguments: dict) -> dict:
        payload = {**{k: v for k, v in arguments.items() if k != "name"}, "name": name}
        body = self._req("POST", "/api/v0/tools/invoke", payload)
        return body if isinstance(body, dict) else {"content": [], "isError": False}


def _jungle_backend(base_url: Optional[str] = None):
    """The process-wide keep-alive aggregator client (`jungle.shared_api`)."""
    from .jungle import shared_api

    return shared_api(base_url)


class JungleClient:
    SEPARATOR = "__"
    route = "mcpjungle"
    alive = True
    suspect = False

    def __init__(self, conn: SourceConnection, *, backend=None) -> None:
        self._conn = conn
        self._server = conn.jungle_name
        self._api = backend if backend is not None else _jungle_backend()
        self._res_map: dict[str, str] = {}
        self.failed_resources: list[dict] = []
        """Resources the aggregator listed whose URI did not decode strictly (Codex f1 #11): each
        `{name, error}`. The crawl should ledger them as failed (CROSS-TRACK, crawl.py)."""
        self.connect_ms = 0.0

    @property
    def supports_resources(self) -> bool:
        return hasattr(self._api, "resources") and hasattr(self._api, "read_resource")

    def _wrap(self, what: str, fn, *args):
        try:
            return fn(*args)
        except McpClientError:
            raise
        except Exception as exc:                                     # noqa: BLE001
            raise McpClientError(f"MCPJungle {what} failed: {exc}") from exc

    def list_tools(self) -> list[ToolInfo]:
        prefix = f"{self._server}{self.SEPARATOR}"
        out = []
        for t in self._wrap("tool listing", self._api.tools):
            name = str(t.get("name", ""))
            if not name.startswith(prefix) or t.get("enabled") is False:
                continue
            ann = t.get("annotations") or {}
            out.append(ToolInfo(name=name[len(prefix):], description=t.get("description") or "",
                                input_schema=t.get("input_schema") or t.get("inputSchema") or {},
                                read_only=ann.get("readOnlyHint"),
                                destructive=ann.get("destructiveHint")))
        return out

    def call_tool_raw(self, name: str, arguments: dict) -> dict:
        body = self._wrap(f"invoke of {name!r}", self._api.invoke_tool,
                          f"{self._server}{self.SEPARATOR}{name}", dict(arguments or {}))
        return {"content": list(body.get("content") or []),
                "structured": body.get("structuredContent"),
                "is_error": bool(body.get("isError"))}

    def call_tool(self, name: str, arguments: dict) -> ToolResult:
        raw = self.call_tool_raw(name, arguments)
        return ToolResult(parts=parts_from_dicts(raw["content"]), is_error=raw["is_error"],
                          structured=raw.get("structured"))

    def _no_resources(self) -> McpClientError:
        return McpClientError(
            "this MCPJungle client proxies tools only; resources cannot be listed through it. "
            "Use the default aggregator client, or connect this server directly (via: direct)")

    def list_resources(self) -> list[ResourceInfo]:
        from .jungle import decode_resource_uri

        if not self.supports_resources:
            raise self._no_resources()
        prefix = f"{self._server}{self.SEPARATOR}"
        out = []
        self.failed_resources = []
        for r in self._wrap("resource listing", self._api.resources):
            name = str(r.get("name", ""))
            juri = str(r.get("uri", ""))
            if not name.startswith(prefix) or r.get("enabled") is False:
                continue
            try:
                server, original = decode_resource_uri(juri)
            except Exception as exc:                                 # noqa: BLE001
                # Codex f1 #11: never fall back to the aggregator's URI as if it were the
                # server's — a later session would re-encode it and the pointer would not
                # resolve. The resource is recorded as FAILED (`failed_resources`) and not listed.
                self.failed_resources.append({"name": name[len(prefix):], "error": str(exc)})
                import logging

                logging.getLogger(__name__).warning(
                    "MCPJungle resource %r under %s has an undecodable URI; not listed: %s",
                    name, self._server, exc)
                continue
            if server != self._server:
                continue
            self._res_map[original] = juri
            out.append(ResourceInfo(uri=original, name=name[len(prefix):],
                                    mime=r.get("mime_type") or r.get("mimeType"),
                                    size=r.get("size")))
        return out

    #: MCPJungle's HTTP API (`jungle.py`: /api/v0/tools, /resources, /resources/read) has no
    #: resource-template route, so through the aggregator the `resource_templates` drift facet is
    #: UNOBSERVED (None — never read as "all removed"). Connect the server `via: direct` to watch it.
    supports_resource_templates = False

    def list_resource_templates(self) -> list[dict]:
        raise McpClientError(
            "resource templates are not exposed by MCPJungle's API (no /api/v0 route); connect "
            "this server directly (via: direct) to observe them")

    def read_resource_raw(self, uri: str) -> list[dict]:
        from .jungle import encode_resource_uri

        if not self.supports_resources:
            raise self._no_resources()
        juri = self._res_map.get(uri) or encode_resource_uri(self._server, uri)
        contents = self._wrap(f"read of {uri!r}", self._api.read_resource, juri)
        # report the ORIGINAL uri: identity and pointers must not depend on the route
        return [{**c, "uri": uri} for c in contents]

    def read_resource(self, uri: str) -> tuple[ContentPart, ...]:
        return _resource_parts(self.read_resource_raw(uri))

    def close(self) -> None:
        return None


# ================================================================================================
# the session pool
# ================================================================================================
def route_key(conn: SourceConnection) -> str:
    """What makes two sessions interchangeable: the connection id and its RESOLVED route. Secrets
    enter only the hash, never a log."""
    if conn.via == "mcpjungle":
        payload: dict = {"via": "mcpjungle", "server": conn.jungle_name}
    elif isinstance(conn.transport, StdioTransport):
        command, args, cwd = conn.resolved_command()
        payload = {"via": "direct", "command": command, "args": args, "cwd": cwd,
                   "env": conn.resolved_env()}
    else:
        payload = {"via": "direct", "url": conn.resolved_url(),
                   "headers": conn.resolved_headers()}
    payload["id"] = conn.id
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


class SessionPool:
    """Warm direct sessions, leased exclusively. See the module docstring for the rules."""

    def __init__(self, *, max_idle: Optional[int] = None, idle_timeout: Optional[float] = None,
                 factory=None) -> None:
        self.max_idle = int(max_idle if max_idle is not None
                            else _env_float("WALNUT_MCP_POOL_MAX", 8))
        self.idle_timeout = float(idle_timeout if idle_timeout is not None
                                  else _env_float("WALNUT_MCP_POOL_IDLE_S", 300.0))
        self._factory = factory or (lambda conn, timeout: SdkClient(conn, timeout=timeout))
        self._lock = threading.Lock()
        self._idle: dict[str, list[tuple[float, Any]]] = {}
        self._leased = 0
        self._sweeper: Optional[threading.Thread] = None
        self._closed = False
        self.stats = {"hits": 0, "misses": 0, "discarded_dead": 0, "discarded_suspect": 0,
                      "evicted_idle": 0, "evicted_full": 0}

    # -- bookkeeping ---------------------------------------------------------------------------
    def idle_count(self) -> int:
        with self._lock:
            return sum(len(v) for v in self._idle.values())

    def snapshot(self) -> dict:
        with self._lock:
            return {**self.stats, "idle": sum(len(v) for v in self._idle.values()),
                    "leased": self._leased, "keys": len(self._idle),
                    "max_idle": self.max_idle, "idle_timeout_s": self.idle_timeout}

    def _expired_locked(self, now: float) -> list:
        out = []
        for key in list(self._idle):
            keep = []
            for ts, c in self._idle[key]:
                if now - ts > self.idle_timeout:
                    out.append(c)
                    self.stats["evicted_idle"] += 1
                else:
                    keep.append((ts, c))
            if keep:
                self._idle[key] = keep
            else:
                del self._idle[key]
        return out

    @staticmethod
    def _close_all(clients) -> None:
        for c in clients:
            with contextlib.suppress(Exception):
                c.close()

    def sweep(self) -> int:
        with self._lock:
            gone = self._expired_locked(time.monotonic())
        self._close_all(gone)
        return len(gone)

    def _ensure_sweeper(self) -> None:
        if self._sweeper is not None and self._sweeper.is_alive():
            return

        def loop() -> None:
            while not self._closed:
                time.sleep(max(1.0, min(self.idle_timeout / 2, 30.0)))
                with contextlib.suppress(Exception):
                    self.sweep()

        self._sweeper = threading.Thread(target=loop, daemon=True, name="mcp-pool-sweeper")
        self._sweeper.start()

    # -- lease ---------------------------------------------------------------------------------
    def acquire(self, conn: SourceConnection, *, timeout: float = DEFAULT_TIMEOUT):
        key = route_key(conn)
        client = None
        dead: list = []
        with self._lock:
            dead.extend(self._expired_locked(time.monotonic()))
            bucket = self._idle.get(key) or []
            while bucket:
                _, c = bucket.pop()                                  # most recently used
                if getattr(c, "alive", True):
                    client = c
                    break
                dead.append(c)
                self.stats["discarded_dead"] += 1
            if not bucket:
                self._idle.pop(key, None)
            self.stats["hits" if client is not None else "misses"] += 1
            self._leased += 1
        self._close_all(dead)
        if client is None:
            try:
                client = self._factory(conn, timeout)
            except BaseException:
                with self._lock:
                    self._leased -= 1
                raise
        else:
            client._timeout = timeout                                # noqa: SLF001
        return key, client

    def release(self, key: str, client, *, broken: bool = False) -> None:
        evicted: list = []
        with self._lock:
            self._leased -= 1
            if broken or self._closed or not getattr(client, "alive", True) \
                    or getattr(client, "suspect", False):
                if getattr(client, "suspect", False):
                    self.stats["discarded_suspect"] += 1
                evicted.append(client)
            else:
                self._idle.setdefault(key, []).append((time.monotonic(), client))
                while sum(len(v) for v in self._idle.values()) > self.max_idle:
                    oldest_key = min(self._idle, key=lambda k: self._idle[k][0][0])
                    _, c = self._idle[oldest_key].pop(0)
                    if not self._idle[oldest_key]:
                        del self._idle[oldest_key]
                    evicted.append(c)
                    self.stats["evicted_full"] += 1
        self._close_all(evicted)
        if not self._closed:
            self._ensure_sweeper()

    @contextlib.contextmanager
    def lease(self, conn: SourceConnection, *, timeout: float = DEFAULT_TIMEOUT):
        key, client = self.acquire(conn, timeout=timeout)
        broken = False
        try:
            yield client
        except BaseException:
            broken = not getattr(client, "alive", True)
            raise
        finally:
            self.release(key, client, broken=broken)

    def close(self) -> None:
        with self._lock:
            clients = [c for v in self._idle.values() for _, c in v]
            self._idle.clear()
        self._close_all(clients)

    def shutdown(self) -> None:
        self._closed = True
        self.close()


_pool: Optional[SessionPool] = None
_pool_lock = threading.Lock()


def get_pool() -> SessionPool:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = SessionPool()
        return _pool


def close_pool() -> None:
    """Close every idle pooled session AND retire the pool (FXJ3, Codex f2 #15): the old pool is
    marked closed, so a session leased from it at this moment is CLOSED when its lease ends, not
    returned to an idle list; the next `open_client` gets a fresh pool."""
    global _pool
    with _pool_lock:
        pool, _pool = _pool, None
    if pool is not None:
        pool.shutdown()


atexit.register(lambda: _pool is not None and _pool.shutdown())


def pooling_enabled() -> bool:
    return os.environ.get("WALNUT_MCP_POOL", "1").strip().lower() not in ("0", "false", "no",
                                                                          "off")


@contextlib.contextmanager
def open_client(conn: SourceConnection, *, timeout: float = DEFAULT_TIMEOUT, jungle_backend=None,
                pooled: Optional[bool] = None):
    """Open the right client for a connection. Direct routes lease a warm pooled session (unless
    `pooled=False` / `WALNUT_MCP_POOL=0`, in which case it is closed on exit)."""
    if conn.via == "mcpjungle":
        yield JungleClient(conn, backend=jungle_backend)
        return
    use_pool = pooling_enabled() if pooled is None else pooled
    if use_pool:
        with get_pool().lease(conn, timeout=timeout) as client:
            yield client
        return
    client = SdkClient(conn, timeout=timeout)
    try:
        yield client
    finally:
        client.close()


# ================================================================================================
# health checks — one connection, or many at once
# ================================================================================================
def check_connection(conn: SourceConnection, *, fresh: bool = True,
                     timeout: float = DEFAULT_TIMEOUT, jungle_backend=None) -> dict:
    """Connect, list tools (and resources), and time each step. `fresh=True` measures a COLD
    connect (a new session, closed afterwards); `fresh=False` leases from the pool (warm)."""
    out: dict = {"id": conn.id, "via": conn.via, "ok": False, "tools": 0, "resources": None,
                 "read_only_tools": 0, "connect_ms": None, "list_tools_ms": None,
                 "list_resources_ms": None, "error": None}
    t0 = time.perf_counter()
    try:
        with open_client(conn, timeout=timeout, jungle_backend=jungle_backend,
                         pooled=not fresh) as c:
            out["connect_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            t1 = time.perf_counter()
            tools = c.list_tools()
            out["list_tools_ms"] = round((time.perf_counter() - t1) * 1000, 1)
            out["tools"] = len(tools)
            out["tool_names"] = sorted(t.name for t in tools)
            from .plans import is_write_tool

            out["read_only_tools"] = sum(1 for t in tools if not is_write_tool(t))
            if getattr(c, "supports_resources", False):
                t2 = time.perf_counter()
                try:
                    out["resources"] = len(c.list_resources())
                except McpClientError as exc:
                    out["resources_error"] = str(exc)
                out["list_resources_ms"] = round((time.perf_counter() - t2) * 1000, 1)
            out["ok"] = True
    except Exception as exc:                                         # noqa: BLE001
        out["error"] = _plain(exc) if not isinstance(exc, McpClientError) else str(exc)
    out["total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return out


def check_connections(conns: list[SourceConnection], *, fresh: bool = True,
                      max_workers: int = 4, timeout: float = DEFAULT_TIMEOUT) -> list[dict]:
    """Health-check many connections concurrently (each in its own session). Order preserved."""
    if not conns:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, max_workers)) as ex:
        return list(ex.map(lambda c: check_connection(c, fresh=fresh, timeout=timeout), conns))


# ================================================================================================
# the shared async API (BUILD_PLAN §5.4) — used by other packages
# ================================================================================================
def _tool_dict(t: ToolInfo) -> dict:
    return {"name": t.name, "description": t.description, "input_schema": t.input_schema,
            "annotations": {"readOnlyHint": t.read_only, "destructiveHint": t.destructive}}


class AsyncSession:
    """One open session: identical surface AND identical shapes whether the route is direct
    (stdio/http) or MCPJungle — both are the same sync client underneath.

        await s.list_tools()               -> [{name, description, input_schema, annotations}]
        await s.call_tool(name, args)      -> {content: [blocks], structured: dict|None,
                                               is_error: bool}
        await s.list_resources()           -> [{uri, name, mime, size}]   (uri = the server's own)
        await s.read_resource(uri)         -> [{uri, mimeType, text | blob(base64)}]
    """

    def __init__(self, conn: SourceConnection, client) -> None:
        self.connection = conn
        self._client = client

    @property
    def route(self) -> str:
        return getattr(self._client, "route", "direct")

    async def list_tools(self) -> list[dict]:
        return [_tool_dict(t) for t in await asyncio.to_thread(self._client.list_tools)]

    async def call_tool(self, name: str, args: Optional[dict] = None) -> dict:
        return await asyncio.to_thread(self._client.call_tool_raw, name, dict(args or {}))

    async def list_resources(self) -> list[dict]:
        items = await asyncio.to_thread(self._client.list_resources)
        return [{"uri": r.uri, "name": r.name, "mime": r.mime, "size": r.size} for r in items]

    async def read_resource(self, uri: str) -> list[dict]:
        return await asyncio.to_thread(self._client.read_resource_raw, uri)


def _resolve(connection_or_id, sources_root=None) -> SourceConnection:
    from .connections import load_connection

    if isinstance(connection_or_id, SourceConnection):
        return connection_or_id
    if isinstance(connection_or_id, dict):
        return SourceConnection.model_validate(connection_or_id)
    return load_connection(str(connection_or_id), sources_root)


@contextlib.asynccontextmanager
async def open_session(connection_or_id, *, timeout: float = DEFAULT_TIMEOUT, jungle_backend=None,
                       sources_root=None, pooled: Optional[bool] = None):
    """`async with open_session(conn_or_id) as s:` — BUILD_PLAN §5.4. `connection_or_id` is a
    `SourceConnection`, a dict declaration, or the id of one saved under WALNUT_SOURCES_DIR.
    Direct sessions are leased from the warm pool (the session is started off the event loop)."""
    conn = _resolve(connection_or_id, sources_root)
    cm = open_client(conn, timeout=timeout, jungle_backend=jungle_backend, pooled=pooled)
    client = await asyncio.to_thread(cm.__enter__)
    try:
        yield AsyncSession(conn, client)
    except BaseException as exc:
        await asyncio.to_thread(cm.__exit__, type(exc), exc, exc.__traceback__)
        raise
    else:
        await asyncio.to_thread(cm.__exit__, None, None, None)


def _declared_error_prefixes(plan) -> tuple[str, ...]:
    """`match.error_text_prefix` of the profiles the plan was built from (plan-level
    `error_text_prefixes` too, if a plan carries them)."""
    out = [str(p) for p in (getattr(plan, "error_text_prefixes", None) or ()) if p]
    wanted = set(getattr(plan, "profiles", None) or ())
    if wanted:
        try:
            from .plans import load_profiles

            for prof in load_profiles():
                if prof.id not in wanted:
                    continue
                declared = prof.match.get("error_text_prefix")
                out += [str(d) for d in (declared if isinstance(declared, list) else [declared])
                        if d]
        except Exception:                                            # noqa: BLE001
            pass
    return tuple(dict.fromkeys(out))


class ReadOnlyClient:
    """Wraps a client for crawling: a tool that can change the source is refused before the call,
    whatever plan asked for it —
      * by name pattern (`plans.writes_by_name`: WRITE_VERB + WRITE_VERB_EXTRA);
      * by DECLARED deny list: `deny` + the plan's `deny_tools` (folded in from its profiles'
        `match.deny_tools`) + the connection's `scope.deny_tools`. With no plan given, every
        bundled profile's deny list applies (conservative: a crawl never needs a tool some
        profile names as a writer);
      * by annotations, once the tool list has been read through this wrapper.

    Some servers report a failure as ordinary text (`"Error: …"`) with `isError` false. A profile
    DECLARES that (`match.error_text_prefix`, a string or list); for the plan's own profiles such
    a result is returned with `is_error=True`, so every text-call path treats it as the error it
    is. Only with a plan: another server's document may legitimately begin with that word."""

    def __init__(self, inner, *, deny=None, plan=None, error_prefixes=None) -> None:
        self._inner = inner
        self.supports_resources = getattr(inner, "supports_resources", True)
        self._writes: set[str] = set()
        self.denied: set[str] = set(deny or ())
        conn = getattr(inner, "_conn", None)
        scope = getattr(conn, "scope", None)
        self.denied |= set(getattr(scope, "deny_tools", None) or ())
        self.error_prefixes: tuple[str, ...] = tuple(p for p in (error_prefixes or ()) if p)
        self.allowed: Optional[set[str]] = None
        """With a plan that carries `tool_classes` (every plan proposed since EF3): the ONLY
        tools that may be called — those classified `read:` (FXI3, Codex f1 #2). The deny lists
        and the verb pattern still apply on top. None (no plan / a legacy plan with no classes):
        the deny-list mode below."""
        #: FXJ3 (Codex f2 #2): the LIVE tool list, re-read before the first call (and again after
        #: `WALNUT_TOOL_RECHECK_S`, default 60 s) — never only if a caller happened to list.
        self._live: Optional[dict[str, ToolInfo]] = None
        self._listed_at: Optional[float] = None
        self._step_digests: dict[str, str] = {}
        self._plan_digest: str = ""
        #: FXJ3 (Codex f2 #1): tool -> (statement argument, fixed extra arguments) for the plan's
        #: tabular SQL tools. A generic executor is callable ONLY as one of these, and only with a
        #: statement that passes `plans.read_only_sql_refusal`.
        self._sql_args: dict[str, tuple[str, dict]] = {}
        #: True when the plan carries evidence (classes / digests) the live list must match:
        #: then a client that cannot list its tools is refused outright.
        self._verifies = False
        if plan is not None:
            self.denied |= set(getattr(plan, "deny_tools", None) or ())
            self.error_prefixes += _declared_error_prefixes(plan)
            classes = getattr(plan, "tool_classes", None) or {}
            if classes:
                self.allowed = {t for t, c in classes.items() if str(c).startswith("read:")}
            self._step_digests = dict(getattr(plan, "step_tool_digests", None) or {})
            self._plan_digest = str(getattr(plan, "tools_digest", "") or "")
            self._verifies = bool(classes or self._step_digests or self._plan_digest)
            for step in getattr(plan, "steps", None) or ():
                sql = getattr(step, "sql", None)
                if sql is not None:
                    self._sql_args[sql.tool] = (sql.arg, dict(sql.extra_args or {}))
        else:
            try:
                from .plans import load_profiles

                for prof in load_profiles():
                    self.denied |= set(prof.deny_tools)
            except Exception:                                    # noqa: BLE001
                pass    # a malformed profile must not open the gate: the verb pattern still holds

    def _absorb(self, tools) -> None:
        from .plans import is_write_tool

        self._writes = {t.name for t in tools if is_write_tool(t)}
        self._live = {t.name: t for t in tools}
        self._listed_at = time.monotonic()

    def list_tools(self):
        tools = self._inner.list_tools()
        self._absorb(tools)
        return tools

    def _ensure_live(self) -> None:
        """Re-read the live tool list when it was never read here, or is older than the recheck
        window. A client that cannot list its tools cannot prove a tool is still a read."""
        fresh = self._listed_at is not None and \
            time.monotonic() - self._listed_at < _env_float("WALNUT_TOOL_RECHECK_S", 60.0)
        if fresh:
            return
        lister = getattr(self._inner, "list_tools", None)
        if not self._verifies:
            # no plan evidence to verify against (no plan, or a hand-made one with no classes or
            # digests): the live annotations are still read when the client can list them; the
            # verb pattern and deny lists hold either way
            if callable(lister):
                try:
                    self._absorb(lister())
                except Exception:                                    # noqa: BLE001
                    self._listed_at = time.monotonic()
            return
        if not callable(lister):
            raise McpClientError("refused: this client cannot list its live tools, so no tool "
                                 "can be verified as the read the plan approved")
        self._absorb(lister())

    def _live_refusal(self, name: str) -> Optional[str]:
        """Why the LIVE server no longer offers `name` as the tool the plan approved."""
        from .plans import tool_digest, tools_digest

        if not self._verifies:
            return None
        info = (self._live or {}).get(name)
        if info is None:
            return f"refused: {name!r} is not offered by the server now"
        if self._step_digests:
            want = self._step_digests.get(name)
            if want is not None and tool_digest(info) != want:
                return (f"refused: {name!r} changed on the server since the plan was approved "
                        f"(its description, schema or annotations differ); re-propose and "
                        f"approve the plan")
        elif self._plan_digest and tools_digest(list((self._live or {}).values())) \
                != self._plan_digest:
            return (f"refused: the server's tools changed since the plan was approved; "
                    f"re-propose and approve the plan")
        return None

    def _executor_refusal(self, name: str, arguments: dict) -> Optional[str]:
        """A generic executor runs what its arguments say, so the ARGUMENTS are checked on every
        call: one statement argument holding a closed-grammar read, nothing else free-form."""
        from .plans import EXECUTOR_ARGS, read_only_sql_refusal

        args = dict(arguments or {})
        if self._sql_args:
            if name not in self._sql_args:
                return "it is not the plan's SQL tool"
            arg, extra = self._sql_args[name]
            if arg not in args:
                return f"the statement argument {arg!r} is missing"
            unexpected = sorted(k for k in args if k != arg and
                                (k not in extra or args[k] != extra[k]))
            if unexpected:
                return f"arguments {unexpected} are not the plan's fixed arguments"
            why = read_only_sql_refusal(args[arg])
            return f"the statement is not a read: {why}" if why else None
        stmt_args = [k for k in args if str(k).lower() in EXECUTOR_ARGS]
        if not stmt_args:
            return "no statement argument was recognised, so the call cannot be proved a read"
        for k in stmt_args:
            why = read_only_sql_refusal(args[k])
            if why:
                return f"argument {k!r} is not a read: {why}"
        for k, v in args.items():
            if k not in stmt_args and isinstance(v, str) and re.search(r"[\s;]", v):
                return f"argument {k!r} is free-form text"
        return None

    def call_tool(self, name: str, arguments: dict) -> ToolResult:
        from .plans import is_generic_executor, writes_by_name

        if writes_by_name(name) or name in self.denied:
            raise McpClientError(f"refused: {name!r} can change the source and a crawl only reads")
        self._ensure_live()
        if name in self._writes:
            raise McpClientError(f"refused: {name!r} can change the source and a crawl only reads"
                                 f" (the live server says so)")
        if self.allowed is not None and name not in self.allowed:
            raise McpClientError(f"refused: {name!r} is not a tool the plan classifies as a read "
                                 f"(a crawl calls only its classified read tools)")
        why = self._live_refusal(name)
        if why:
            raise ToolChanged(why)
        live = (self._live or {}).get(name)
        if live is not None:
            # R-027 r2 #1: a tool whose arguments select what it does (a closed mode set holding
            # a write) is held, on EVERY call, to a fixed read value — whatever the plan says.
            from .plans import dual_use_refusal

            dual = dual_use_refusal(live, arguments)
            if dual:
                raise McpClientError(f"refused: {dual}")
        if name in self._sql_args or is_generic_executor(live if live is not None else name):
            why = self._executor_refusal(name, arguments)
            if why:
                raise McpClientError(f"refused: {name!r} runs whatever its arguments say and "
                                     f"{why}")
        result = self._inner.call_tool(name, arguments)
        if self.error_prefixes and not result.is_error:
            head = result.text.lstrip()
            if any(head.startswith(p) for p in self.error_prefixes):
                return ToolResult(parts=result.parts, is_error=True,
                                  structured=getattr(result, "structured", None))
        return result

    def list_resources(self):
        return self._inner.list_resources()

    @property
    def supports_resource_templates(self) -> bool:
        return bool(getattr(self._inner, "supports_resource_templates", False)) and \
            callable(getattr(self._inner, "list_resource_templates", None))

    def list_resource_templates(self):
        """Read-only (a listing), passed through."""
        lister = getattr(self._inner, "list_resource_templates", None)
        if not callable(lister):
            raise McpClientError("this client cannot list resource templates")
        return lister()

    def read_resource(self, uri: str):
        return self._inner.read_resource(uri)

    def close(self) -> None:
        self._inner.close()


__all__ = ["open_session", "AsyncSession", "ReadOnlyClient", "McpClient", "SdkClient",
           "ToolChanged",
           "JungleClient", "ToolInfo", "ToolResult", "ResourceInfo", "ContentPart",
           "McpClientError", "open_client", "parts_from_dicts", "SessionPool", "get_pool",
           "close_pool", "route_key", "pooling_enabled", "check_connection",
           "check_connections", "add_list_changed_listener", "list_changed_listeners",
           "clear_list_changed_listeners", "notify_list_changed", "list_changed_kind",
           "install_list_changed_hook", "LIST_CHANGED_METHODS"]
