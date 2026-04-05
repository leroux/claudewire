"""Claude Wire -- Claude CLI stream-json protocol wrapper.

Wraps the Claude Code CLI's ``--output-format stream-json`` protocol.
Has NO dependency on any specific process transport backend.
The host application's wiring layer provides an adapter from a concrete
transport to the ProcessConnection protocol.

- BridgeTransport: SDK Transport implementation over any ProcessConnection
- DirectProcessConnection: local PTY subprocess ProcessConnection
- ProcessConnection: protocol that any process backend must satisfy
- Config: CLI configuration (flags, env vars) — mirrors Rust config.rs
- Event types: StdoutEvent, StderrEvent, ExitEvent
- Event parsing and activity tracking
- Session lifecycle helpers (disconnect, subprocess cleanup)
"""

from claudewire.config import Config, McpServers
from claudewire.permissions import (
    Allow,
    CanUseTool,
    Deny,
    PolicyFn,
    allow_all,
    ask_user_policy,
    auto_answer,
    auto_approve_plans,
    compose,
    cwd_policy,
    deny_all,
    plan_approval_policy,
    to_control_response,
    tool_allow_policy,
    tool_block_policy,
)
from claudewire.direct import DirectProcessConnection, find_claude
from claudewire.events import (
    ActivityState,
    RateLimitInfo,
    as_stream,
    parse_rate_limit_event,
    update_activity,
)
from claudewire.schema import (
    AssistantMessageInner,
    AssistantMsg,
    ContentBlock,
    ContentBlockDeltaEvent,
    ContentBlockStartEvent,
    ContentBlockStopEvent,
    ControlRequestMsg,
    ControlResponseMsg,
    Delta,
    InboundMsg,
    MessageDeltaEvent,
    MessageStartEvent,
    MessageStopEvent,
    OutboundMsg,
    RateLimitEventMsg,
    ResultMsg,
    SchemaValidationError,
    StreamEvent,
    StreamEventMsg,
    SystemMsg,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    Usage,
    UserMsg,
    ValidationError,
    ValidationResult,
    validate_inbound,
    validate_inbound_or_bare,
    validate_outbound,
)
from claudewire.session import disconnect_client, ensure_process_dead, get_subprocess_pid
from claudewire.transport import BridgeTransport
from claudewire.types import (
    CommandResult,
    ExitEvent,
    ProcessConnection,
    ProcessEvent,
    ProcessEventQueue,
    StderrEvent,
    StdoutEvent,
)

__all__ = [
    "ActivityState",
    "Allow",
    "AssistantMessageInner",
    "AssistantMsg",
    "BridgeTransport",
    "CommandResult",
    "Config",
    "ContentBlock",
    "ContentBlockDeltaEvent",
    "ContentBlockStartEvent",
    "ContentBlockStopEvent",
    "ControlRequestMsg",
    "ControlResponseMsg",
    "Delta",
    "DirectProcessConnection",
    "ExitEvent",
    "InboundMsg",
    "McpServers",
    "MessageDeltaEvent",
    "MessageStartEvent",
    "MessageStopEvent",
    "OutboundMsg",
    "ProcessConnection",
    "ProcessEvent",
    "ProcessEventQueue",
    "RateLimitEventMsg",
    "RateLimitInfo",
    "ResultMsg",
    "SchemaValidationError",
    "StderrEvent",
    "StdoutEvent",
    "StreamEvent",
    "StreamEventMsg",
    "SystemMsg",
    "TextBlock",
    "ThinkingBlock",
    "ToolUseBlock",
    "Usage",
    "UserMsg",
    "ValidationError",
    "ValidationResult",
    "allow_all",
    "as_stream",
    "ask_user_policy",
    "auto_answer",
    "auto_approve_plans",
    "compose",
    "cwd_policy",
    "deny_all",
    "disconnect_client",
    "ensure_process_dead",
    "CanUseTool",
    "Deny",
    "PolicyFn",
    "find_claude",
    "parse_rate_limit_event",
    "plan_approval_policy",
    "to_control_response",
    "tool_allow_policy",
    "tool_block_policy",
    "update_activity",
    "validate_inbound",
    "validate_inbound_or_bare",
    "validate_outbound",
]
