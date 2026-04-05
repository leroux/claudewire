"""Tests for claudewire.permissions — permission policies and composition."""

import asyncio

import pytest

from claudewire.permissions import (
    Allow,
    Deny,
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


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


class TestResultTypes:
    def test_allow_default(self):
        a = Allow()
        assert a.updated_input is None

    def test_allow_with_updated_input(self):
        a = Allow(updated_input={"answers": {"q": "a"}})
        assert a.updated_input == {"answers": {"q": "a"}}

    def test_deny_default_message(self):
        d = Deny()
        assert d.message == "Tool call denied"

    def test_deny_custom_message(self):
        d = Deny(message="Not allowed")
        assert d.message == "Not allowed"


# ---------------------------------------------------------------------------
# Stateless policies
# ---------------------------------------------------------------------------


class TestToolBlockPolicy:
    def test_blocks_listed_tool(self):
        policy = tool_block_policy({"Bash", "Skill"})
        result = policy("Bash", {})
        assert isinstance(result, Deny)
        assert "Bash" in result.message

    def test_passes_unlisted_tool(self):
        policy = tool_block_policy({"Bash"})
        assert policy("Read", {}) is None

    def test_custom_message(self):
        policy = tool_block_policy({"Bash"}, message="nope")
        result = policy("Bash", {})
        assert isinstance(result, Deny)
        assert "nope" in result.message


class TestToolAllowPolicy:
    def test_allows_listed_tool(self):
        policy = tool_allow_policy({"Read", "Grep"})
        assert isinstance(policy("Read", {}), Allow)

    def test_passes_unlisted_tool(self):
        policy = tool_allow_policy({"Read"})
        assert policy("Bash", {}) is None


class TestCwdPolicy:
    def test_allows_write_inside_path(self, tmp_path):
        policy = cwd_policy([str(tmp_path)])
        result = policy("Edit", {"file_path": str(tmp_path / "file.py")})
        assert isinstance(result, Allow)

    def test_denies_write_outside_path(self, tmp_path):
        policy = cwd_policy([str(tmp_path / "allowed")])
        result = policy("Edit", {"file_path": "/etc/passwd"})
        assert isinstance(result, Deny)

    def test_passes_non_write_tool(self, tmp_path):
        policy = cwd_policy([str(tmp_path)])
        assert policy("Read", {"file_path": "/etc/passwd"}) is None

    def test_allows_exact_path(self, tmp_path):
        policy = cwd_policy([str(tmp_path)])
        result = policy("Write", {"file_path": str(tmp_path)})
        assert isinstance(result, Allow)


# ---------------------------------------------------------------------------
# Interactive tool policies
# ---------------------------------------------------------------------------


class TestAutoApprovePlans:
    def test_approves_exit_plan_mode(self):
        policy = auto_approve_plans()
        result = policy("ExitPlanMode", {"plan": "do stuff"})
        assert isinstance(result, Allow)

    def test_passes_other_tools(self):
        policy = auto_approve_plans()
        assert policy("Bash", {}) is None


class TestAutoAnswer:
    def test_matches_question(self):
        policy = auto_answer({"Continue?": "Yes"})
        result = policy("AskUserQuestion", {
            "questions": [{"question": "Continue?", "options": []}],
        })
        assert isinstance(result, Allow)
        assert result.updated_input["answers"]["Continue?"] == "Yes"

    def test_substring_match_case_insensitive(self):
        policy = auto_answer({"ready": "go"})
        result = policy("AskUserQuestion", {
            "questions": [{"question": "Are you READY to proceed?", "options": []}],
        })
        assert isinstance(result, Allow)
        assert result.updated_input["answers"]["Are you READY to proceed?"] == "go"

    def test_falls_through_on_no_match(self):
        policy = auto_answer({"Continue?": "Yes"})
        result = policy("AskUserQuestion", {
            "questions": [{"question": "What color?", "options": []}],
        })
        assert result is None

    def test_passes_non_ask_user(self):
        policy = auto_answer({"x": "y"})
        assert policy("Bash", {}) is None

    def test_multiple_questions_all_matched(self):
        policy = auto_answer({"name": "Alice", "color": "blue"})
        result = policy("AskUserQuestion", {
            "questions": [
                {"question": "What is your name?", "options": []},
                {"question": "Favorite color?", "options": []},
            ],
        })
        assert isinstance(result, Allow)
        assert result.updated_input["answers"]["What is your name?"] == "Alice"
        assert result.updated_input["answers"]["Favorite color?"] == "blue"

    def test_multiple_questions_partial_match_falls_through(self):
        policy = auto_answer({"name": "Alice"})
        result = policy("AskUserQuestion", {
            "questions": [
                {"question": "What is your name?", "options": []},
                {"question": "Favorite color?", "options": []},
            ],
        })
        assert result is None


class TestAskUserPolicy:
    @pytest.mark.asyncio
    async def test_calls_handler(self):
        async def handler(tool_input):
            return Allow(updated_input={**tool_input, "answers": {"q": "a"}})

        policy = ask_user_policy(handler)
        result = await policy("AskUserQuestion", {"questions": []})
        assert isinstance(result, Allow)

    @pytest.mark.asyncio
    async def test_passes_non_ask_user(self):
        async def handler(tool_input):
            return Allow()

        policy = ask_user_policy(handler)
        result = await policy("Bash", {})
        assert result is None


class TestPlanApprovalPolicy:
    @pytest.mark.asyncio
    async def test_calls_handler(self):
        async def handler(tool_input):
            return Deny(message="needs revision")

        policy = plan_approval_policy(handler)
        result = await policy("ExitPlanMode", {"plan": "..."})
        assert isinstance(result, Deny)

    @pytest.mark.asyncio
    async def test_passes_non_exit_plan(self):
        async def handler(tool_input):
            return Allow()

        policy = plan_approval_policy(handler)
        result = await policy("Bash", {})
        assert result is None


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


class TestCompose:
    @pytest.mark.asyncio
    async def test_first_non_none_wins(self):
        policy = compose(
            tool_block_policy({"Bash"}),
            tool_allow_policy({"Read"}),
        )
        result = await policy("Bash", {})
        assert isinstance(result, Deny)

    @pytest.mark.asyncio
    async def test_falls_through_to_allow(self):
        policy = compose(
            tool_block_policy({"Bash"}),
        )
        result = await policy("Read", {})
        assert isinstance(result, Allow)

    @pytest.mark.asyncio
    async def test_empty_compose_allows(self):
        policy = compose()
        result = await policy("anything", {})
        assert isinstance(result, Allow)

    @pytest.mark.asyncio
    async def test_async_policy_in_chain(self):
        async def slow_policy(tool_name, tool_input):
            if tool_name == "Slow":
                return Deny(message="too slow")
            return None

        policy = compose(slow_policy)
        result = await policy("Slow", {})
        assert isinstance(result, Deny)

    @pytest.mark.asyncio
    async def test_mixed_sync_async(self):
        policy = compose(
            tool_block_policy({"Bash"}),
            auto_approve_plans(),
            tool_allow_policy({"Read"}),
        )
        assert isinstance(await policy("Bash", {}), Deny)
        assert isinstance(await policy("ExitPlanMode", {}), Allow)
        assert isinstance(await policy("Read", {}), Allow)
        assert isinstance(await policy("Unknown", {}), Allow)


# ---------------------------------------------------------------------------
# Trivial policies
# ---------------------------------------------------------------------------


class TestTrivialPolicies:
    def test_allow_all(self):
        assert isinstance(allow_all("anything", {}), Allow)

    def test_deny_all(self):
        result = deny_all("anything", {})
        assert isinstance(result, Deny)
        assert "denied" in result.message.lower()


# ---------------------------------------------------------------------------
# Wire format
# ---------------------------------------------------------------------------


class TestToControlResponse:
    def test_allow_response(self):
        resp = to_control_response("req-1", Allow())
        assert resp["type"] == "control_response"
        assert resp["response"]["subtype"] == "success"
        assert resp["response"]["request_id"] == "req-1"
        assert resp["response"]["response"]["behavior"] == "allow"
        assert "updatedInput" not in resp["response"]["response"]

    def test_allow_with_updated_input(self):
        resp = to_control_response("req-1", Allow(updated_input={"answers": {"q": "a"}}))
        assert resp["response"]["response"]["updatedInput"] == {"answers": {"q": "a"}}

    def test_deny_response(self):
        resp = to_control_response("req-1", Deny(message="no"))
        assert resp["response"]["response"]["behavior"] == "deny"
        assert resp["response"]["response"]["message"] == "no"
