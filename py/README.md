# claudewire (Python)

Python implementation of the claudewire wire protocol library. See the [root README](../README.md) for project motivation and overview.

Wraps the Claude Code CLI's `--output-format stream-json` protocol into a clean `ProcessConnection` abstraction. Any process backend (local PTY, remote process multiplexer, SSH, etc.) can implement `ProcessConnection` and get a working SDK Transport for free.

Also provides stateless permission policies for restricting tool access, rate limit event parsing from the Claude API stream, and schema validation against the full protocol.

## Architecture

```
Claude Agent SDK
      |
BridgeTransport (SDK Transport impl)
      |
ProcessConnection (abstract protocol)
      |
DirectProcessConnection    -- or --    YourCustomConnection
(local PTY subprocess)                 (remote via Unix socket, SSH, etc.)
```

## Usage

### Direct (local subprocess)

```python
from claudewire import BridgeTransport, DirectProcessConnection

conn = DirectProcessConnection()
transport = BridgeTransport("my-agent", conn)

await transport.connect()
await transport.spawn(cli_args=["--model", "sonnet"], env={}, cwd="/tmp")

await transport.write(json.dumps({"type": "user_message", "content": "hello"}))

async for msg in transport.read_messages():
    print(msg)

await transport.close()
```

### Permission policies

Composable policies for handling `control_request.can_use_tool`. Each policy returns `Allow`, `Deny`, or `None` (no opinion — pass to next). Chain them with `compose()` and pass to `BridgeTransport` — permission requests are handled automatically and never reach your message loop.

```python
from claudewire import (
    BridgeTransport, DirectProcessConnection, compose,
    tool_block_policy, tool_allow_policy, cwd_policy,
    auto_approve_plans, auto_answer,
)

conn = DirectProcessConnection()
transport = BridgeTransport(
    "my-agent", conn,
    can_use_tool=compose(
        tool_block_policy({"Skill", "Task"}, message="Not available"),
        tool_allow_policy({"TodoWrite", "EnterPlanMode"}),
        auto_approve_plans(),                          # auto-approve ExitPlanMode
        auto_answer({"Continue?": "Yes"}),             # auto-answer AskUserQuestion
        cwd_policy(["/home/user/project"]),            # restrict file writes
    ),
)

# read_messages() only yields non-permission messages —
# can_use_tool requests are handled + responded automatically
async for msg in transport.read_messages():
    ...
```

For custom interactive flows, use callback-based policies:

```python
from claudewire import ask_user_policy, plan_approval_policy, Allow, Deny

async def handle_question(tool_input):
    """Route AskUserQuestion to your UI."""
    questions = tool_input["questions"]
    answers = await my_ui.ask(questions)  # your UI logic
    return Allow(updated_input={**tool_input, "answers": answers})

async def handle_plan(tool_input):
    """Route plan approval to your UI."""
    approved = await my_ui.approve_plan(tool_input)
    if approved:
        return Allow()
    return Deny(message="Plan rejected — please revise")

transport = BridgeTransport(
    "my-agent", conn,
    can_use_tool=compose(
        tool_block_policy({"Skill"}),
        ask_user_policy(handle_question),
        plan_approval_policy(handle_plan),
        cwd_policy(["/home/user/project"]),
    ),
)
```

Built-in trivial policies: `allow_all`, `deny_all`.

### Rate limit event parsing

```python
from claudewire import parse_rate_limit_event, RateLimitInfo

# Parse rate_limit_event from the raw stream
info = parse_rate_limit_event(event_data)
if info is not None:
    print(info.rate_limit_type)  # "five_hour"
    print(info.status)           # "allowed", "allowed_warning", "rejected"
    print(info.resets_at)        # datetime
    print(info.utilization)      # 0.0-1.0 or None
```

### Activity tracking

```python
from claudewire import ActivityState, update_activity

activity = ActivityState()
# Feed raw stream events to track what the agent is doing
update_activity(activity, event)
print(activity.phase)  # "thinking", "writing", "tool_use", etc.
```

### CLI argument construction (requires claude-agent-sdk)

```python
from claudewire import build_cli_spawn_args

cli_args, env, cwd = build_cli_spawn_args(agent_options)
```

## API

### Transport & Connection

| Export | Description |
|---|---|
| `BridgeTransport` | SDK `Transport` impl over any `ProcessConnection` |
| `ProcessConnection` | Protocol that process backends must satisfy |
| `DirectProcessConnection` | Local PTY subprocess backend |
| `CommandResult` | Result of spawn/subscribe/kill commands |

### Event Types

| Export | Description |
|---|---|
| `StdoutEvent` | JSON data from process stdout |
| `StderrEvent` | Text line from stderr |
| `ExitEvent` | Process exit with code |
| `ProcessEvent` | Union of above |
| `ProcessEventQueue` | Async queue protocol (get/put) |

### Activity Tracking

| Export | Description |
|---|---|
| `ActivityState` | Tracks phase, tool, thinking text, turn count, etc. |
| `update_activity()` | Parse stream events into `ActivityState` |
| `as_stream()` | Wrap a prompt as `AsyncIterable` for SDK streaming |

### Rate Limit Events

| Export | Description |
|---|---|
| `RateLimitInfo` | Parsed rate limit event (type, status, resets_at, utilization) |
| `parse_rate_limit_event()` | Parse a `rate_limit_event` dict into `RateLimitInfo` |

### Permission Policies

| Export | Description |
|---|---|
| `Allow` | Permission result: allow the tool call (optionally with `updated_input`) |
| `Deny` | Permission result: deny the tool call (with message) |
| `compose()` | Chain policies — first non-None result wins, all None defaults to allow |
| `cwd_policy()` | Restrict file writes to allowed base paths |
| `tool_block_policy()` | Block specific tools by name |
| `tool_allow_policy()` | Auto-allow specific tools by name |
| `auto_approve_plans()` | Auto-allow `ExitPlanMode` |
| `auto_answer()` | Auto-answer `AskUserQuestion` from a static map |
| `ask_user_policy()` | Route `AskUserQuestion` to an async handler |
| `plan_approval_policy()` | Route `ExitPlanMode` to an async handler |
| `allow_all` | Trivial policy: allow everything |
| `deny_all` | Trivial policy: deny everything |
| `to_control_response()` | Convert `Allow`/`Deny` to wire-format `control_response` dict |

### Session Lifecycle

| Export | Description |
|---|---|
| `disconnect_client()` | Graceful async client teardown |
| `ensure_process_dead()` | SIGTERM cleanup for leaked processes |
| `get_subprocess_pid()` | Extract PID from SDK client |
| `find_claude()` | Locate `claude` binary on PATH |
| `build_cli_spawn_args()` | Build CLI args from `ClaudeAgentOptions` (lazy import) |

### Schema Validation

| Export | Description |
|---|---|
| `validate_inbound()` | Validate an inbound (CLI → host) message against pydantic models |
| `validate_outbound()` | Validate an outbound (host → CLI) message |
| `validate_inbound_or_bare()` | Validate inbound message, handling both `stream_event`-wrapped and bare forms |
| `ValidationResult` | Result with `.ok`, `.errors`, and typed `.model` |

All stream-json messages are validated against strict pydantic models with discriminated unions. Unknown fields produce warnings (not hard errors) so new upstream fields don't break us — but they do get logged, which is how we detect CLI protocol changes.

**When the Claude CLI adds new message types, content block types, or fields**: validation warnings will appear in logs. Update the models in `schema.py` and add real samples to `tests/unit/test_claudewire_schema_real.py`. See `PROTOCOL.md` for the full protocol reference.

## Dependencies

`pydantic>=2.0` for schema validation. `claude-agent-sdk` is optional (only needed for `build_cli_spawn_args`).

Requires Python 3.12+.
