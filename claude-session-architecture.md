# Claude Session Architecture

This document describes the layered architecture for managing Claude CLI agent sessions: the wire protocol (claudewire), process multiplexing (procmux), high-level session management (claude-session), and how consumers like flowcoder use them.

## Overview

The architecture has three layers, each with a single responsibility:

```
┌─────────────────────────────────────────────┐
│  Consumers (flowcoder, bot, TUI, etc.)      │
├─────────────────────────────────────────────┤
│  claude-session                             │
│  High-level session API: query/clear/finish │
│  Session handoff protocol                   │
├─────────────────────────────────────────────┤
│  claudewire              │  procmux         │
│  Wire protocol types     │  Process mux     │
│  Message serialization   │  Output buffering│
│  Config builder          │  Reconnection    │
└─────────────────────────────────────────────┘
```

---

## Layer 1: claudewire — Wire Protocol

claudewire is the Rust implementation of Claude CLI's `stream-json` protocol. It handles message serialization, CLI configuration, and low-level process I/O. It is intentionally free of application logic.

### What it provides

**Message types** — Strongly typed enums for every message the CLI sends or receives:

Inbound (CLI → host):
- `StreamEvent` — token-level streaming during generation
- `Assistant` — complete assistant message
- `Result` — final result with cost, session ID, usage
- `ControlRequest` — permission checks (`can_use_tool`), MCP relay, `initialize`
- `ControlResponse` — responses to control requests
- `RateLimitEvent`, `ToolProgress`, `KeepAlive`, `AuthStatus`, etc.

Outbound (host → CLI):
- `User` — send a user message / prompt
- `ControlRequest` / `ControlResponse` — bidirectional control protocol
- `UpdateEnvironmentVariables` — runtime env changes

**Config builder** — Constructs CLI arguments and environment variables:
```rust
let config = Config::new()
    .model("sonnet")
    .system_prompt("You are a helpful assistant")
    .permission_mode(PermissionMode::Approved)
    .mcp_servers(vec![...]);

let args = config.to_cli_args();  // ["claude", "--input-format", "stream-json", ...]
let env = config.to_env();        // HashMap of env vars
```

**CliSession** — Low-level process lifecycle. Spawns the CLI subprocess, wires stdin/stdout/stderr through channels:
```rust
// Spawn a new CLI process
let session = CliSession::spawn(&config, "agent-1".into(), None)?;

// Or wrap existing channels (e.g. from procmux)
let session = CliSession::new(name, rx, tx, send_stdin, kill, is_alive, reconnecting, None);

// Raw message I/O
session.write(&serde_json::to_string(&outbound_msg)?).await?;
let msg: Option<serde_json::Value> = session.read_message().await;

// Lifecycle
session.stop().await;
session.close().await;
session.send_signal(Signal::SIGINT);
```

### What it does NOT provide

- No "send a prompt, get a result" semantics — that's the message dance, not the wire format
- No session persistence, reconnection logic, or output buffering
- No application-level concepts (agents, flowcharts, commands)

### Protocol lifecycle (reference)

```
spawn CLI
  → CLI sends: control_request.initialize (MCP handshake)
  ← host sends: control_response (capabilities)
  → CLI sends: system.init (ready)

query loop:
  ← host sends: user message (prompt)
  → CLI sends: stream_event* → assistant → control_request.can_use_tool*
  ← host sends: control_response (allow/deny)
  → CLI sends: stream_event* → result (with cost, usage, session_id)

  repeat...
```

---

## Layer 2: procmux — Process Multiplexer

procmux is a background service that manages named Claude CLI subprocesses over a Unix socket. It provides process persistence, output buffering, and reconnection — the substrate that lets agent sessions survive client restarts.

### What it provides

**Process management** — Spawn, kill, interrupt named subprocesses:
```
Client → Server:  Cmd { name: "agent-1", cmd: "claude", cli_args: [...], env: {...} }
Server → Client:  Result { ok: true, pid: 12345, already_running: false }
```

**Output buffering** — When no client is subscribed to a process, stdout/stderr accumulates in memory. On reconnect, buffered messages replay in order:
```
Client → Server:  Cmd { cmd: "subscribe", name: "agent-1" }
Server → Client:  Result { ok: true, replayed: 47, status: "running", idle: true }
Server → Client:  Stdout { name: "agent-1", data: ... }  // buffered messages replay
Server → Client:  Stdout { name: "agent-1", data: ... }  // then live messages continue
```

**Status tracking** — Process status (running/exited), exit code, idle detection (last stdout timestamp >= last stdin timestamp), agent listing.

**Protocol** — NDJSON over Unix socket. Two message types in each direction:

```rust
// Client → Server
enum ClientMsg {
    Cmd { cmd: String, name: String, cli_args: Vec<String>, env: HashMap, cwd: Option<String> },
    Stdin { name: String, data: String },
}

// Server → Client
enum ServerMsg {
    Result { ok: bool, pid: Option<u32>, already_running: bool, replayed: usize, status: String, exit_code: Option<i32>, idle: bool, agents: Vec<String> },
    Stdout { name: String, data: Value },
    Stderr { name: String, data: String },
    Exit { name: String, code: Option<i32> },
}
```

### Key design decisions

- **One client at a time** — new connections drop the old one, all processes unsubscribe. This is intentional: the primary use case is a single bot process reconnecting after restart.
- **Output buffering is bounded** — prevents memory exhaustion from long-running unsubscribed processes.
- **Process names are the identity** — spawning with an existing name reconnects to the running process.
- **No protocol awareness** — procmux doesn't understand claudewire messages. It passes opaque JSON blobs. The client is responsible for interpreting them via claudewire.

### Integration with claudewire

claudewire's `CliSession::new()` accepts raw channels, enabling procmux-backed sessions:

```rust
// Connect to procmux
let conn = ProcmuxConnection::connect(socket_path).await?;
conn.send_command("spawn", "agent-1", cli_args, env, cwd).await?;
let rx = conn.register_process("agent-1");

// Wrap in claudewire session
let session = CliSession::new("agent-1".into(), rx, tx, send_stdin, kill, is_alive, false, None);
// Now use session.read_message() / session.write() as normal
```

---

## Layer 3: claude-session — High-Level Session API

**Status: Planned — not yet implemented.**

claude-session is the missing middle layer. It provides the "send a prompt, get a result" semantics that every consumer currently reimplements on top of claudewire's raw messages.

### The problem it solves

Every consumer of claudewire reimplements the same message-level choreography:

1. Send a `User` message (the prompt)
2. Read `StreamEvent`s (token streaming)
3. Handle `ControlRequest`s (tool permissions, MCP relay)
4. Collect `Assistant` message content
5. Read `Result` (cost, usage, session ID)
6. Return the assembled response

This dance is ~50-100 lines of async code with edge cases (rate limits, interrupts, errors, reconnection). Currently duplicated in:
- `flowcoder-engine/src/engine_session.rs`
- `flowcoder/src/claude_session.rs` (TUI)
- `axi/src/claude_process.rs` (bot)

### What it provides

A high-level session API on top of claudewire:

```rust
pub struct ClaudeSession { /* wraps CliSession + state */ }

impl ClaudeSession {
    /// Send a prompt, return the complete response.
    /// Handles the full message dance internally:
    /// send User → collect StreamEvents → handle ControlRequests → return Result
    pub async fn query(&mut self, prompt: &str) -> Result<QueryResult, SessionError>;

    /// Clear conversation history. Optionally respawns the CLI process.
    pub async fn clear(&mut self) -> Result<(), SessionError>;

    /// Interrupt the current generation.
    pub async fn interrupt(&mut self) -> Result<(), SessionError>;

    /// Gracefully stop the session.
    pub async fn stop(&mut self);

    /// Accumulated cost across all queries in this session.
    pub fn total_cost(&self) -> f64;
}

pub struct QueryResult {
    pub response_text: String,
    pub cost: f64,
    pub session_id: Option<String>,
    pub duration_ms: u64,
}
```

### Session handoff

The core architectural concept: **session handoff** is the ability for external code to temporarily take control of an existing agent session, then return control when done.

This enables:
- Interactive → programmatic → interactive transitions (e.g., user is chatting with an agent, invokes `/command`, a flowchart drives the session, then control returns)
- Multiple consumers sharing the same session sequentially
- Test harnesses driving a real agent session

Session handoff is what makes this layer distinct from "just import the SDK." The SDK creates new sessions. claude-session lets you drive an existing one.

### Callbacks and extensibility

Consumers need to handle events during a query (streaming tokens, permission decisions, progress updates). Rather than baking in a specific callback interface, claude-session should expose hooks:

```rust
pub trait SessionHandler: Send {
    /// Called for each streaming token. Default: ignore.
    fn on_stream_text(&mut self, _text: &str) {}

    /// Called when the CLI requests tool permission. Must return allow/deny.
    fn on_permission_request(&mut self, request: &ControlRequest) -> ControlResponse;

    /// Called on rate limit events. Default: ignore.
    fn on_rate_limit(&mut self, _event: &RateLimitEvent) {}
}
```

### Future: JSON-RPC bridge

When non-Rust subprocesses need to drive a session (e.g., Python scripts as program flowcharts), a thin JSON-RPC bridge can expose claude-session's API over stdio:

```
Subprocess                  JSON-RPC Bridge              claude-session
   │                             │                            │
   │ {"method":"query",          │                            │
   │  "params":{"prompt":"..."}} │                            │
   │ ──────────────────────────► │  session.query(prompt)     │
   │                             │ ──────────────────────────►│
   │                             │          QueryResult       │
   │                             │ ◄──────────────────────────│
   │ {"result":"..."}            │                            │
   │ ◄────────────────────────── │                            │
```

This is a future addition. The JSON-RPC bridge is a thin adapter (~100 lines) that translates between stdio JSON-RPC and claude-session method calls. It enables program flowcharts and other non-Rust consumers without requiring them to understand the claudewire protocol.

---

## Consumer: flowcoder

flowcoder is an SDK for building agent workflows as JSON flowcharts — directed graphs of blocks (prompt, bash, branch, variable, etc.) that a state machine walks, producing actions for a consumer to dispatch.

### Architecture (two crates)

**flowchart** (pure, no async, no I/O):
- Data model: `Command`, `Flowchart`, `Block`, `Connection`, `Argument`, `SessionConfig`
- `GraphWalker`: pure state machine. Call `start()` → get an `Action` → dispatch it → call `feed(result)` → get next `Action` → repeat
- `Action` enum: the public interface between walker and consumer
  - `Query` — send prompt to agent, capture response
  - `Bash` — run shell command, capture stdout
  - `SubCommand` — invoke another flowchart (recursive)
  - `Clear` — reset agent session
  - `Done` — flowchart complete
  - `Exit` — early termination with exit code
  - `Spawn` / `Wait` — concurrent sub-sessions (stub)
  - `Error` — unrecoverable failure
- Block types: `Start`, `End`, `Prompt`, `Branch`, `Variable`, `Bash`, `Command`, `Refresh`, `Exit`, `Spawn`, `Wait`
- Variable interpolation (`{{var}}`, `$N`), condition evaluation, graph validation, command resolution

**flowchart-runner** (async, dispatches actions):
- `run_flowchart()`: drives the walker loop, dispatching each `Action`
  - `Query` → calls session.query(prompt)
  - `Bash` → spawns subprocess
  - `SubCommand` → recursive flowchart execution
  - `Clear` → calls session.clear()
- Currently defines its own `Session` trait — this would be replaced by claude-session
- `Protocol` trait for event callbacks (block start/complete, streaming, logging)
- Safety: block limits (default 1000), recursion depth (default 10), cancellation tokens, pause mechanism

### How flowcoder uses claude-session

Today, flowchart-runner defines a `Session` trait that each frontend implements:

```rust
// Current: each frontend reimplements this
pub trait Session: Send {
    fn query(&mut self, prompt: &str, block_id: &str, block_name: &str,
             protocol: &mut dyn Protocol) -> impl Future<Output = Result<QueryResult, ExecutionError>> + Send;
    fn clear(&mut self) -> impl Future<Output = Result<(), ExecutionError>> + Send;
    fn stop(&mut self) -> impl Future<Output = ()> + Send;
    fn interrupt(&mut self) -> impl Future<Output = Result<(), ExecutionError>> + Send;
    fn total_cost(&self) -> f64;
}
```

With claude-session, this trait either disappears or becomes a thin wrapper:

```rust
// Future: flowchart-runner uses claude-session directly
use claude_session::ClaudeSession;

async fn run_flowchart(session: &mut ClaudeSession, command: Command) -> FlowchartResult {
    let mut walker = GraphWalker::new(command.flowchart, variables);
    let mut action = walker.start();
    loop {
        match action {
            Action::Query { prompt, .. } => {
                let result = session.query(&prompt).await?;
                action = walker.feed(&result.response_text);
            }
            Action::Clear { .. } => {
                session.clear().await?;
                action = walker.feed("");
            }
            Action::Done { output } => return FlowchartResult::Completed(output),
            // ...
        }
    }
}
```

### flowcoder-engine (current state)

The engine is currently a monolithic binary that fuses three concerns:
1. **Proxy** — transparent message forwarding between client and Claude CLI
2. **Session management** — routing, control responses, 2-channel stdin demux
3. **Flowchart execution** — walker + dispatch loop

With claude-session extracted, the engine thins to:
1. **Proxy mode** — pass messages through (unchanged)
2. **Command detection** — intercept `/command` messages
3. **Runner spawning** — hand off to flowchart-runner with a claude-session instance

The engine's `engine_session.rs` (which currently reimplements the message dance) is replaced by claude-session.

---

## Dependency graph

```
claudewire (wire protocol, message types, CliSession, Config)
    ↑
procmux (process multiplexer, output buffering, reconnection)
    ↑
claude-session (high-level API: query/clear/finish, session handoff)
    ↑
┌───┴────────────────────┐
│                        │
flowcoder            other consumers
(GraphWalker +       (bot, TUI, test
 runner +             harnesses, future
 engine)              JSON-RPC bridge)
```

Note: procmux is optional. claude-session works with a direct `CliSession::spawn()` (no procmux) or with a procmux-backed session. procmux adds persistence and reconnection but isn't required for the session API.

---

## Design principles

These are drawn from the project's code philosophy and the decisions made during the architecture evaluation.

1. **Each layer has one job.** claudewire: wire format. procmux: process lifecycle. claude-session: query semantics. Consumers: application logic.

2. **No layer understands the one above it.** claudewire doesn't know about sessions. procmux doesn't know about claudewire messages. claude-session doesn't know about flowcharts.

3. **Session handoff is the key abstraction.** The ability to pass control of an existing session between consumers (interactive → programmatic → interactive) is the architectural concept that unifies the design.

4. **Protocol, not framework.** These are libraries with clear interfaces, not frameworks that impose structure. Consumers compose them as needed.

5. **Build what has demand.** claudewire and procmux exist and work. claude-session is the next concrete step (eliminates duplication across three consumers). The JSON-RPC bridge and program flowcharts build on claude-session when demand materializes.
