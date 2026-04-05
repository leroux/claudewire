"""Integration tests — require the real Claude CLI binary on PATH.

Run with: pytest -m integration
Skipped by default (see conftest.py).
"""

import asyncio
import json
import shutil

import pytest

from claudewire.config import Config
from claudewire.direct import DirectProcessConnection
from claudewire.types import ExitEvent, StdoutEvent


def skip_without_claude():
    if shutil.which("claude") is None:
        pytest.skip("claude not on PATH")


# ---------------------------------------------------------------------------
# DirectProcessConnection tests (no Claude CLI needed)
# ---------------------------------------------------------------------------


class TestDirectProcessConnection:
    @pytest.mark.asyncio
    async def test_spawn_and_read_ndjson(self):
        """Spawn a bash process that emits NDJSON, read it back."""
        conn = DirectProcessConnection()
        queue = conn.register("test-ndjson")

        result = await conn.spawn(
            "test-ndjson",
            cli_args=[
                "bash", "-c",
                'echo \'{"type":"system","subtype":"init"}\'; '
                'echo \'{"type":"result","subtype":"success","is_error":false,'
                '"duration_ms":0,"duration_api_ms":0,"num_turns":0,'
                '"session_id":"s","uuid":"u"}\'',
            ],
            env={},
            cwd="/tmp",
        )
        assert result.ok

        msg1 = await asyncio.wait_for(queue.get(), timeout=5.0)
        assert isinstance(msg1, StdoutEvent)
        assert msg1.data["type"] == "system"

        msg2 = await asyncio.wait_for(queue.get(), timeout=5.0)
        assert isinstance(msg2, StdoutEvent)
        assert msg2.data["type"] == "result"

        msg3 = await asyncio.wait_for(queue.get(), timeout=5.0)
        assert isinstance(msg3, ExitEvent)
        # Exit code may be None due to PTY close racing process exit
        assert msg3.code is None or msg3.code == 0

    @pytest.mark.asyncio
    async def test_spawn_detects_exit(self):
        """Process exit produces an ExitEvent."""
        conn = DirectProcessConnection()
        queue = conn.register("test-exit")

        await conn.spawn(
            "test-exit",
            cli_args=["bash", "-c", "exit 42"],
            env={},
            cwd="/tmp",
        )

        msg = await asyncio.wait_for(queue.get(), timeout=5.0)
        assert isinstance(msg, ExitEvent)
        # PTY stdout reader pushes ExitEvent when the PTY closes;
        # returncode may not be populated yet (race with process.wait())
        assert msg.code is None or msg.code == 42

    @pytest.mark.asyncio
    async def test_send_stdin_and_read_back(self):
        """Use cat to echo stdin back as stdout."""
        conn = DirectProcessConnection()
        queue = conn.register("test-stdin")

        await conn.spawn(
            "test-stdin",
            cli_args=["bash", "-c", "cat"],
            env={},
            cwd="/tmp",
        )

        await conn.send_stdin("test-stdin", {"type": "user", "content": "hello"})

        msg = await asyncio.wait_for(queue.get(), timeout=5.0)
        assert isinstance(msg, StdoutEvent)
        assert msg.data["type"] == "user"
        assert msg.data["content"] == "hello"

        await conn.kill("test-stdin")

    @pytest.mark.asyncio
    async def test_kill_terminates_process(self):
        conn = DirectProcessConnection()
        queue = conn.register("test-kill")

        await conn.spawn(
            "test-kill",
            cli_args=["bash", "-c", "sleep 300"],
            env={},
            cwd="/tmp",
        )

        # Give the process a moment to start
        await asyncio.sleep(0.1)

        result = await conn.kill("test-kill")
        assert result.ok


# ---------------------------------------------------------------------------
# Real Claude CLI tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestRealCLI:
    @pytest.mark.asyncio
    async def test_spawn_gets_system_init(self):
        skip_without_claude()

        conn = DirectProcessConnection()
        queue = conn.register("test-init")

        config = Config(model="haiku", permission_mode="plan", verbose=True)
        args = config.to_cli_args()
        env = config.to_env()

        await conn.spawn("test-init", cli_args=args, env=env, cwd="/tmp")

        msg = await asyncio.wait_for(queue.get(), timeout=30.0)
        assert isinstance(msg, StdoutEvent)
        assert msg.data["type"] == "system"
        assert msg.data["subtype"] == "init"

        await conn.kill("test-init")

    @pytest.mark.asyncio
    async def test_full_query(self):
        skip_without_claude()

        conn = DirectProcessConnection()
        queue = conn.register("test-query")

        config = Config(model="haiku", permission_mode="plan", verbose=True)
        args = config.to_cli_args()
        env = config.to_env()

        await conn.spawn("test-query", cli_args=args, env=env, cwd="/tmp")

        # Wait for system.init
        msg = await asyncio.wait_for(queue.get(), timeout=30.0)
        assert isinstance(msg, StdoutEvent)
        assert msg.data["type"] == "system"

        # Send a simple query
        user_msg = {
            "type": "user",
            "session_id": "",
            "message": {"role": "user", "content": "Reply with exactly: PONG"},
            "parent_tool_use_id": None,
        }
        await conn.send_stdin("test-query", user_msg)

        # Collect messages until result
        got_result = False
        result_text = ""
        deadline = asyncio.get_event_loop().time() + 60

        while asyncio.get_event_loop().time() < deadline:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=30.0)
            except TimeoutError:
                break

            if isinstance(msg, ExitEvent):
                break
            if isinstance(msg, StdoutEvent):
                if msg.data.get("type") == "result":
                    got_result = True
                    result_text = msg.data.get("result", "")
                    break

        assert got_result, "expected a result message"
        assert "PONG" in result_text, f"expected PONG in result, got: {result_text}"

        await conn.kill("test-query")
