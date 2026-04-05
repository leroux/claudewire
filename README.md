# claudewire

Typed protocol library for the Claude Code CLI. Reverse-engineered spec, implementations in Python and Rust.

claudewire wraps the Claude Code CLI's `--output-format stream-json` protocol into typed models, a pluggable transport layer, and session management primitives. It's an alternative to the official `claude-agent-sdk` for applications that need reconnection, schema validation, or custom process backends.

## What you get

- **Pluggable process backends** — `ProcessConnection` protocol decouples the CLI process lifecycle from your host. Swap between local PTY, remote process multiplexer, SSH, or anything you implement.
- **Re-attach after host restart** — If the CLI is managed by an external backend, `BridgeTransport(reconnecting=True)` reconnects to the running process by intercepting the SDK's initialize handshake.
- **Strict schema validation** — Every message validated against typed models (pydantic in Python, serde in Rust). Unknown fields produce warnings, not errors — forward-compatible but you'll know when the protocol changes.
- **Stderr as transport events** — stderr lines are first-class `StderrEvent`s in the message stream, not callbacks. Captures the `autocompact:` debug line for context token tracking.
- **Composable permission policies** — `cwd_policy`, `tool_block_policy`, `tool_allow_policy` with `compose()` chaining. No per-tool callback boilerplate.
- **Rate limit parsing** — Typed `RateLimitInfo` with limit type, status, reset time, and utilization.

## Architecture

```
Claude Agent SDK (optional)
      │
BridgeTransport (SDK Transport impl)
      │
ProcessConnection (protocol)
      │
DirectProcessConnection    ── or ──    YourCustomConnection
(local PTY subprocess)                 (remote, SSH, etc.)
```

## Quick start (Python)

```
pip install claudewire
```

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

See [`py/README.md`](py/README.md) for the full Python API reference.

<details>
<summary><b>Quick start (Rust)</b></summary>

```toml
[dependencies]
claudewire = "0.1"
```

```rust
use claudewire::config::Config;
use claudewire::session::CliSession;

let config = Config {
    model: "sonnet".into(),
    permission_mode: "plan".into(),
    ..Default::default()
};

let mut session = CliSession::spawn(&config, "my-agent".into(), None)?;

let init = session.read_message().await.expect("system.init");

let user_msg = serde_json::json!({
    "type": "user",
    "session_id": "",
    "message": {"role": "user", "content": "Say hello"},
    "parent_tool_use_id": null,
});
session.write(&user_msg.to_string()).await?;

while let Some(msg) = session.read_message().await {
    if msg["type"] == "result" { break; }
}

session.stop().await;
```

</details>

## vs claude-agent-sdk

| Feature | claude-agent-sdk | claudewire |
|---------|-----------------|------------|
| Transport backends | `SubprocessCLITransport` only (unstable `Transport` ABC for custom) | Pluggable `ProcessConnection` protocol |
| Re-attach to running CLI | No — child process dies with host | Yes, via external process backend |
| Wire-level validation | Dict key access, no schema enforcement | Strict typed models (pydantic / serde) |
| Stderr in transport | Callback on options, not in `Transport` ABC | First-class `StderrEvent` in message stream |
| Permission policies | Raw `canUseTool` callback | Composable factories: `cwd_policy`, `tool_block_policy`, `tool_allow_policy` |
| Protocol documentation | None (undocumented wire format) | [`PROTOCOL.md`](PROTOCOL.md) — 900-line spec |

## Protocol spec

[`PROTOCOL.md`](PROTOCOL.md) is the full wire protocol specification, reverse-engineered from the Claude Code CLI binary. Covers all message types, session lifecycle, MCP handshake, dual emission, control requests/responses, and rate limit events. Both implementations are derived from this spec.

See [`RE.md`](RE.md) for the reverse engineering methodology.

## Implementations

| Language | Path | Description |
|----------|------|-------------|
| Python | [`py/`](py/) | SDK Transport, schema validation (pydantic), permission policies, activity tracking |
| Rust | [`rs/`](rs/) | Config, schema types (serde), session management (tokio) |

## License

[MIT](LICENSE)
