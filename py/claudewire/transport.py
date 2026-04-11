"""BridgeTransport -- SDK Transport that routes through a ProcessConnection.

Implements the claude_agent_sdk Transport interface (6 abstract methods).
For reconnecting agents, intercepts the initialize control_request and
fakes a success response -- the CLI is already initialized.

This module has NO dependency on any specific process backend. It works with any backend that
satisfies the ProcessConnection protocol defined in claudewire.types.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from opentelemetry import trace
from opentelemetry.propagate import inject

from claudewire.schema import (
    _BARE_STREAM_TYPES,
    SchemaValidationError,
    ValidationError,
    validate_inbound_or_bare,
    validate_outbound,
)
from claudewire.types import CommandResult, ExitEvent, StderrEvent, StdoutEvent

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from claudewire.permissions import CanUseTool
    from claudewire.types import ProcessConnection, ProcessEventQueue

    ValidationCallback = Callable[[dict[str, Any], list[ValidationError]], Awaitable[None] | None]

log = logging.getLogger(__name__)
_tracer = trace.get_tracer(__name__)


class BridgeTransport:
    """SDK Transport that routes through a ProcessConnection for one agent.

    Implements the claude_agent_sdk.Transport interface (6 abstract methods).
    For reconnecting agents, intercepts the initialize control_request and
    fakes a success response -- the CLI is already initialized.
    """

    def __init__(
        self,
        name: str,
        conn: ProcessConnection,
        *,
        reconnecting: bool = False,
        can_use_tool: CanUseTool | None = None,
        stderr_callback: Callable[[str], None] | None = None,
        stdio_logger: logging.Logger | None = None,
        on_validation_error: ValidationCallback | None = None,
    ):
        self._name = name
        self._conn = conn
        self._reconnecting = reconnecting
        self._can_use_tool = can_use_tool
        self._stderr_callback = stderr_callback
        self._stdio_logger = stdio_logger
        self._on_validation_error = on_validation_error
        self._queue: ProcessEventQueue | None = None
        self._ready = False
        self._cli_exited = False
        self._exit_code: int | None = None

    async def connect(self) -> None:
        """Register with the process connection for this agent's output.

        Idempotent — calling connect() multiple times (e.g. from both
        _create_transport and ClaudeSDKClient.__aenter__) reuses the
        existing queue instead of creating a duplicate registration.
        """
        if self._ready and self._queue is not None:
            return
        _tracer.start_span("claudewire.connect", attributes={"agent.name": self._name}).end()
        self._queue = self._conn.register(self._name)
        self._ready = True

    async def spawn(self, cli_args: list[str], env: dict[str, str], cwd: str) -> CommandResult:
        """Tell the process connection to spawn a new CLI process."""
        _tracer.start_span("claudewire.spawn", attributes={"agent.name": self._name, "agent.cwd": cwd}).end()
        result = await self._conn.spawn(self._name, cli_args=cli_args, env=env, cwd=cwd)
        if not result.ok:
            raise RuntimeError(f"Spawn failed for '{self._name}': {result}")
        return result

    async def subscribe(self) -> CommandResult:
        """Subscribe to this agent's output (triggers buffer replay)."""
        result = await self._conn.subscribe(self._name)
        if not result.ok:
            raise RuntimeError(f"Subscribe failed for '{self._name}': {result}")
        return result

    async def _handle_validation_errors(
        self, msg: dict[str, Any], errors: list[ValidationError], direction: str
    ) -> None:
        """Log validation errors and invoke the callback if set."""
        summary = "; ".join(str(e) for e in errors[:5])
        log.warning("[%s][%s] schema validation %s: %s", direction, self._name, "errors" if errors else "ok", summary)
        if SchemaValidationError is not None and any(e.level == "error" for e in errors):
            from claudewire.schema import STRICT_MODE

            if STRICT_MODE:
                raise SchemaValidationError(errors, msg)
        if self._on_validation_error:
            result = self._on_validation_error(msg, errors)
            if asyncio.iscoroutine(result):
                await result

    async def write(self, data: str) -> None:
        """Write data to the CLI's stdin."""
        with _tracer.start_as_current_span(
            "claudewire.write",
            attributes={"agent.name": self._name, "data.bytes": len(data)},
        ):
            if not self._conn.is_alive:
                raise ConnectionError("Process connection is dead")
            msg = json.loads(data)

            # Intercept initialize for reconnecting agents -- fake success
            if (
                self._reconnecting
                and msg.get("type") == "control_request"
                and msg.get("request", {}).get("subtype") == "initialize"
            ):
                request_id = msg.get("request_id")
                fake_response: dict[str, Any] = {
                    "type": "control_response",
                    "response": {
                        "subtype": "success",
                        "request_id": request_id,
                        "response": {},
                    },
                }
                if self._queue:
                    await self._queue.put(StdoutEvent(name=self._name, data=fake_response))
                self._reconnecting = False
                return

            # Validate outbound message
            outbound_result = validate_outbound(msg)
            if outbound_result.errors:
                await self._handle_validation_errors(msg, outbound_result.errors, "outbound")

            # Inject OTel trace context so downstream processes can link spans
            carrier: dict[str, str] = {}
            inject(carrier)
            if carrier:
                msg["_trace_context"] = carrier

            if self._stdio_logger:
                self._stdio_logger.debug(">>> STDIN  %s", json.dumps(msg))
            await self._conn.send_stdin(self._name, msg)

    async def read_messages(self):
        """Async generator yielding parsed JSON dicts from CLI stdout."""
        if not self._queue:
            return
        if not self._conn.is_alive:
            raise ConnectionError("Process connection is dead")
        while True:
            msg = await self._queue.get()
            # stop() was called — discard any buffered messages and return
            if self._cli_exited:
                return
            if msg is None:
                raise ConnectionError("Process connection lost during read")
            if isinstance(msg, StdoutEvent):
                msg_type = msg.data.get("type", "?")
                # The CLI emits every stream event twice: once wrapped in
                # stream_event and once bare.  The bare duplicate carries no
                # extra information and the SDK can't parse it anyway
                # (MessageParseError), so we drop it here.
                if msg_type in _BARE_STREAM_TYPES:
                    log.debug("[read][%s] dropping bare duplicate type=%s", self._name, msg_type)
                    continue
                log.debug("[read][%s] yielding stdout type=%s", self._name, msg_type)
                if self._stdio_logger:
                    self._stdio_logger.debug("<<< STDOUT %s", json.dumps(msg.data))
                # Validate inbound message
                inbound_result = validate_inbound_or_bare(msg.data)
                if inbound_result.errors:
                    await self._handle_validation_errors(msg.data, inbound_result.errors, "inbound")
                # Auto-handle can_use_tool if a policy is set
                if (
                    self._can_use_tool
                    and msg_type == "control_request"
                    and msg.data.get("request", {}).get("subtype") == "can_use_tool"
                ):
                    await self._handle_can_use_tool(msg.data)
                    continue
                yield msg.data
            elif isinstance(msg, StderrEvent):
                log.debug("[read][%s] stderr: %.200s", self._name, msg.text)
                if self._stdio_logger:
                    self._stdio_logger.debug("<<< STDERR %s", msg.text)
                if self._stderr_callback:
                    self._stderr_callback(msg.text)
            elif isinstance(msg, ExitEvent):
                log.debug("[read][%s] exit code=%s", self._name, msg.code)
                _tracer.start_span(
                    "claudewire.cli_exit",
                    attributes={"agent.name": self._name, "exit.code": msg.code or -1},
                ).end()
                if self._stdio_logger:
                    self._stdio_logger.debug("--- EXIT   code=%s", msg.code)
                self._cli_exited = True
                self._exit_code = msg.code
                return

    async def _handle_can_use_tool(self, msg: dict[str, Any]) -> None:
        """Invoke the can_use_tool policy and send the response back to the CLI."""
        from claudewire.permissions import to_control_response

        assert self._can_use_tool is not None
        request = msg.get("request", {})
        request_id = msg.get("request_id", "")
        tool_name = request.get("tool_name", "")
        tool_input = request.get("input", {})

        log.debug("[read][%s] can_use_tool: %s", self._name, tool_name)
        result = self._can_use_tool(tool_name, tool_input)
        if asyncio.iscoroutine(result):
            result = await result

        # CLI requires updatedInput on Allow responses (Zod validation).
        # Match SDK Query behavior: fall back to original tool_input.
        from claudewire.permissions import Allow

        if isinstance(result, Allow) and result.updated_input is None:
            result = Allow(updated_input=tool_input)

        response = to_control_response(request_id, result)
        if self._stdio_logger:
            self._stdio_logger.debug(">>> STDIN  %s (auto)", json.dumps(response))
        await self._conn.send_stdin(self._name, response)

    async def stop(self) -> None:
        """Terminate the read stream and kill the CLI process.

        Injects an ExitEvent into the local queue so that read_messages()
        terminates on the next iteration (discarding any buffered messages),
        then kills the process synchronously to avoid a race where a
        fire-and-forget kill task outlives this transport and kills a
        newly-spawned process registered under the same name.
        """
        if self._cli_exited:
            return
        self._cli_exited = True
        log.debug("[stop][%s] injecting ExitEvent and killing process", self._name)
        _tracer.start_span("claudewire.stop", attributes={"agent.name": self._name}).end()
        # Inject ExitEvent to unblock read_messages() immediately
        if self._queue:
            await self._queue.put(ExitEvent(name=self._name, code=None))
        # Kill synchronously — avoids race with subsequent spawn under same name
        try:
            await self._conn.kill(self._name)
        except Exception:
            log.debug("[stop][%s] kill failed (process may already be dead)", self._name)

    async def close(self) -> None:
        """Kill the CLI process and unregister."""
        _tracer.start_span("claudewire.close", attributes={"agent.name": self._name}).end()
        if self._ready and not self._cli_exited:
            try:
                await self._conn.kill(self._name)
            except Exception:
                pass
        self._conn.unregister(self._name)
        self._ready = False

    def is_ready(self) -> bool:
        return self._ready

    async def end_input(self) -> None:
        """Not needed for bridge mode -- CLI stdin stays open for multi-turn."""

    @property
    def cli_exited(self) -> bool:
        return self._cli_exited
