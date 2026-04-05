"""Claude CLI configuration — single source of truth for CLI flags and env vars.

Config is a plain data struct. It knows nothing about any specific application —
the caller sets every field explicitly. to_cli_args() and to_env() are the only
places that know about Claude CLI flag names and env var names.

Mirrors the Rust config.rs implementation.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any


# SDK version string — matches claude-code-sdk for protocol compatibility.
SDK_VERSION = "0.1.39"


@dataclass
class McpServers:
    """MCP server configuration for the --mcp-config flag.

    SDK and external servers are kept separate so callers can selectively
    include or exclude SDK servers depending on their capabilities.
    """

    external: dict[str, Any] | None = None
    sdk: dict[str, Any] | None = None


@dataclass
class Config:
    """All the data needed to invoke a Claude CLI process.

    This is a plain data bag — no policy, no smart defaults.
    The caller sets every field it cares about.
    """

    model: str = ""
    append_system_prompt: str | None = None
    permission_mode: str = ""
    setting_sources: list[str] = field(default_factory=list)
    mcp_servers: McpServers = field(default_factory=McpServers)
    disallowed_tools: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    max_thinking_tokens: int | None = None
    effort: str | None = None
    sandbox_enabled: bool = False
    auto_allow_bash_if_sandboxed: bool = False
    resume: str | None = None
    include_partial_messages: bool = False
    verbose: bool = False
    debug_to_stderr: bool = False
    print_mode: bool = False
    replay_user_messages: bool = False

    def to_cli_args(self) -> list[str]:
        """Build CLI argv for spawning the Claude CLI process.

        This is THE place that knows about Claude CLI flag names.
        Returns args starting with "claude" as argv[0].
        """
        args = ["claude"]

        if self.print_mode:
            args.append("--print")

        args.extend(["--output-format", "stream-json", "--input-format", "stream-json"])

        if self.replay_user_messages:
            args.append("--replay-user-messages")

        if self.verbose:
            args.append("--verbose")

        if self.include_partial_messages:
            args.append("--include-partial-messages")

        if self.debug_to_stderr:
            args.append("--debug-to-stderr")

        # Route permission prompts through the control protocol (stdio)
        args.extend(["--permission-prompt-tool", "stdio"])

        if self.model:
            args.extend(["--model", self.model])

        if self.setting_sources:
            args.extend(["--setting-sources", ",".join(self.setting_sources)])

        if self.permission_mode:
            args.extend(["--permission-mode", self.permission_mode])

        if self.append_system_prompt:
            args.extend(["--append-system-prompt", self.append_system_prompt])

        # MCP servers — merge SDK + external into one --mcp-config
        merged: dict[str, Any] = {}
        if self.mcp_servers.sdk:
            merged.update(self.mcp_servers.sdk)
        if self.mcp_servers.external:
            merged.update(self.mcp_servers.external)
        if merged:
            args.extend(["--mcp-config", json.dumps({"mcpServers": merged})])

        if self.disallowed_tools:
            args.extend(["--disallowed-tools", ",".join(self.disallowed_tools)])

        if self.allowed_tools:
            args.extend(["--allowedTools", ",".join(self.allowed_tools)])

        if self.max_thinking_tokens is not None:
            args.extend(["--max-thinking-tokens", str(self.max_thinking_tokens)])

        if self.effort:
            args.extend(["--effort", self.effort])

        # Sandbox config → --settings JSON
        if self.sandbox_enabled:
            settings = {
                "sandbox": {
                    "enabled": True,
                    "autoAllowBashIfSandboxed": self.auto_allow_bash_if_sandboxed,
                }
            }
            args.extend(["--settings", json.dumps(settings)])

        if self.resume:
            args.extend(["--resume", self.resume])

        return args

    def to_env(self) -> dict[str, str]:
        """Build environment variables for the Claude CLI process.

        This is THE place that knows about SDK env var names.
        Sets what claude-code-sdk's SubprocessCLITransport.connect() sets.
        """
        env: dict[str, str] = {}

        for key in (
            "ANTHROPIC_API_KEY",
            "HOME",
            "PATH",
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME",
            "XDG_STATE_HOME",
            "NODE_PATH",
            "TERM",
        ):
            val = os.environ.get(key)
            if val is not None:
                env[key] = val

        # SDK control protocol — without these, Claude CLI auto-denies
        # tool permissions in pipe mode.
        env["CLAUDE_CODE_ENTRYPOINT"] = "sdk-py"
        env["CLAUDE_AGENT_SDK_VERSION"] = SDK_VERSION

        # Disable internal compaction prompts
        env["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"] = "100"

        return env
