"""Tests for claudewire.config — CLI argument and environment variable construction."""

import json

from claudewire.config import Config, McpServers, SDK_VERSION


class TestToCliArgs:
    def test_default_config_has_stream_json(self):
        args = Config().to_cli_args()
        assert "--output-format" in args
        assert "stream-json" in args
        assert "--input-format" in args

    def test_default_config_has_permission_prompt_tool(self):
        args = Config().to_cli_args()
        idx = args.index("--permission-prompt-tool")
        assert args[idx + 1] == "stdio"

    def test_default_config_starts_with_claude(self):
        args = Config().to_cli_args()
        assert args[0] == "claude"

    def test_default_config_no_print(self):
        args = Config().to_cli_args()
        assert "--print" not in args

    def test_model_flag(self):
        args = Config(model="sonnet").to_cli_args()
        idx = args.index("--model")
        assert args[idx + 1] == "sonnet"

    def test_permission_mode_flag(self):
        args = Config(permission_mode="plan").to_cli_args()
        idx = args.index("--permission-mode")
        assert args[idx + 1] == "plan"

    def test_append_system_prompt(self):
        args = Config(append_system_prompt="Be concise.").to_cli_args()
        idx = args.index("--append-system-prompt")
        assert args[idx + 1] == "Be concise."

    def test_setting_sources(self):
        args = Config(setting_sources=["local", "project"]).to_cli_args()
        idx = args.index("--setting-sources")
        assert args[idx + 1] == "local,project"

    def test_disallowed_tools(self):
        args = Config(disallowed_tools=["Task", "Skill"]).to_cli_args()
        idx = args.index("--disallowed-tools")
        assert args[idx + 1] == "Task,Skill"

    def test_allowed_tools(self):
        args = Config(allowed_tools=["Read", "Grep"]).to_cli_args()
        idx = args.index("--allowedTools")
        assert args[idx + 1] == "Read,Grep"

    def test_max_thinking_tokens(self):
        args = Config(max_thinking_tokens=128_000).to_cli_args()
        idx = args.index("--max-thinking-tokens")
        assert args[idx + 1] == "128000"

    def test_effort(self):
        args = Config(effort="high").to_cli_args()
        idx = args.index("--effort")
        assert args[idx + 1] == "high"

    def test_verbose(self):
        args = Config(verbose=True).to_cli_args()
        assert "--verbose" in args

    def test_include_partial_messages(self):
        args = Config(include_partial_messages=True).to_cli_args()
        assert "--include-partial-messages" in args

    def test_debug_to_stderr(self):
        args = Config(debug_to_stderr=True).to_cli_args()
        assert "--debug-to-stderr" in args

    def test_replay_user_messages(self):
        args = Config(replay_user_messages=True).to_cli_args()
        assert "--replay-user-messages" in args

    def test_print_mode(self):
        args = Config(print_mode=True).to_cli_args()
        assert "--print" in args

    def test_resume(self):
        args = Config(resume="sess-123").to_cli_args()
        idx = args.index("--resume")
        assert args[idx + 1] == "sess-123"

    def test_sandbox_enabled(self):
        args = Config(sandbox_enabled=True).to_cli_args()
        idx = args.index("--settings")
        settings = json.loads(args[idx + 1])
        assert settings["sandbox"]["enabled"] is True
        assert settings["sandbox"]["autoAllowBashIfSandboxed"] is False

    def test_sandbox_with_auto_allow_bash(self):
        args = Config(sandbox_enabled=True, auto_allow_bash_if_sandboxed=True).to_cli_args()
        idx = args.index("--settings")
        settings = json.loads(args[idx + 1])
        assert settings["sandbox"]["autoAllowBashIfSandboxed"] is True

    def test_no_sandbox_no_settings(self):
        args = Config().to_cli_args()
        assert "--settings" not in args

    def test_empty_fields_omitted(self):
        args = Config().to_cli_args()
        assert "--model" not in args
        assert "--permission-mode" not in args
        assert "--append-system-prompt" not in args
        assert "--disallowed-tools" not in args
        assert "--allowedTools" not in args
        assert "--resume" not in args


class TestMcpConfig:
    def test_no_mcp_no_flag(self):
        args = Config().to_cli_args()
        assert "--mcp-config" not in args

    def test_external_only(self):
        cfg = Config(mcp_servers=McpServers(external={"myserver": {"command": "node"}}))
        args = cfg.to_cli_args()
        idx = args.index("--mcp-config")
        mcp = json.loads(args[idx + 1])
        assert "myserver" in mcp["mcpServers"]

    def test_sdk_only(self):
        cfg = Config(mcp_servers=McpServers(sdk={"utils": {"type": "sdk"}}))
        args = cfg.to_cli_args()
        idx = args.index("--mcp-config")
        mcp = json.loads(args[idx + 1])
        assert mcp["mcpServers"]["utils"]["type"] == "sdk"

    def test_merged(self):
        cfg = Config(
            mcp_servers=McpServers(
                external={"ext": {"command": "node"}},
                sdk={"utils": {"type": "sdk"}},
            )
        )
        args = cfg.to_cli_args()
        idx = args.index("--mcp-config")
        mcp = json.loads(args[idx + 1])
        assert "ext" in mcp["mcpServers"]
        assert "utils" in mcp["mcpServers"]

    def test_external_overrides_sdk_same_name(self):
        cfg = Config(
            mcp_servers=McpServers(
                sdk={"shared": {"type": "sdk"}},
                external={"shared": {"command": "custom"}},
            )
        )
        args = cfg.to_cli_args()
        idx = args.index("--mcp-config")
        mcp = json.loads(args[idx + 1])
        assert mcp["mcpServers"]["shared"]["command"] == "custom"
        assert "type" not in mcp["mcpServers"]["shared"]


class TestToEnv:
    def test_sdk_vars_present(self):
        env = Config().to_env()
        assert env["CLAUDE_CODE_ENTRYPOINT"] == "sdk-py"
        assert env["CLAUDE_AGENT_SDK_VERSION"] == SDK_VERSION
        assert env["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"] == "100"

    def test_system_vars_passed(self):
        env = Config().to_env()
        assert "HOME" in env
        assert "PATH" in env

    def test_no_extra_claude_vars(self):
        env = Config().to_env()
        # Should not leak arbitrary CLAUDE_* vars from the host env
        for key in env:
            if key.startswith("CLAUDE") and key not in (
                "CLAUDE_CODE_ENTRYPOINT",
                "CLAUDE_AGENT_SDK_VERSION",
                "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE",
            ):
                raise AssertionError(f"Unexpected CLAUDE var: {key}")


class TestFullConfigRoundTrip:
    def test_all_fields_set(self):
        cfg = Config(
            model="sonnet",
            append_system_prompt="You are helpful.",
            permission_mode="default",
            setting_sources=["local"],
            disallowed_tools=["Task"],
            allowed_tools=["Read"],
            max_thinking_tokens=128_000,
            effort="high",
            sandbox_enabled=True,
            auto_allow_bash_if_sandboxed=True,
            verbose=True,
            include_partial_messages=True,
            debug_to_stderr=True,
            resume="sess-123",
        )
        args = cfg.to_cli_args()

        assert "--output-format" in args
        assert "--input-format" in args
        assert "--verbose" in args
        assert "--include-partial-messages" in args
        assert "--debug-to-stderr" in args
        assert "--model" in args
        assert "--setting-sources" in args
        assert "--permission-mode" in args
        assert "--append-system-prompt" in args
        assert "--disallowed-tools" in args
        assert "--allowedTools" in args
        assert "--max-thinking-tokens" in args
        assert "--effort" in args
        assert "--settings" in args
        assert "--resume" in args
        assert "--print" not in args
