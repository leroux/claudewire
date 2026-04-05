"""Permission policies for Claude Code control_request.can_use_tool handling.

Stateless factory functions that return policy callbacks. Compose them with
compose() to build a policy chain — first non-None result wins.

No dependency on claude-agent-sdk. Types map directly to the control_response
wire format documented in PROTOCOL.md.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Permission result types (map to control_response wire format)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Allow:
    """Allow the tool call. Maps to control_response.success with behavior=allow."""

    updated_input: dict[str, Any] | None = None


@dataclass(slots=True)
class Deny:
    """Deny the tool call. Maps to control_response.success with behavior=deny."""

    message: str = "Tool call denied"


PermissionResult = Allow | Deny

# A policy returns Allow, Deny, or None (no opinion — pass to next).
PolicyFn = Callable[
    [str, dict[str, Any]],
    PermissionResult | None | Awaitable[PermissionResult | None],
]

# A composed policy always returns Allow or Deny (never None).
CanUseTool = Callable[
    [str, dict[str, Any]],
    PermissionResult | Awaitable[PermissionResult],
]


# ---------------------------------------------------------------------------
# Stateless policies
# ---------------------------------------------------------------------------

# Tools that perform file writes
_WRITE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})


def cwd_policy(allowed_paths: list[str]) -> PolicyFn:
    """Restrict file writes to allowed base paths.

    Returns Allow for writes inside allowed paths, Deny for writes outside,
    None for non-write tools (no opinion).
    """
    resolved = [os.path.realpath(p) for p in allowed_paths]

    def _check(tool_name: str, tool_input: dict[str, Any]) -> PermissionResult | None:
        if tool_name not in _WRITE_TOOLS:
            return None
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        real = os.path.realpath(path)
        for base in resolved:
            if real == base or real.startswith(base + os.sep):
                return Allow()
        return Deny(message=f"Access denied: {path} is outside allowed paths")

    return _check


def tool_block_policy(
    blocked: set[str],
    message: str = "Tool not available",
) -> PolicyFn:
    """Block specific tools by name."""

    def _check(tool_name: str, tool_input: dict[str, Any]) -> Deny | None:
        if tool_name in blocked:
            return Deny(message=f"{tool_name}: {message}")
        return None

    return _check


def tool_allow_policy(allowed: set[str]) -> PolicyFn:
    """Auto-allow specific tools by name."""

    def _check(tool_name: str, tool_input: dict[str, Any]) -> Allow | None:
        if tool_name in allowed:
            return Allow()
        return None

    return _check


# ---------------------------------------------------------------------------
# Interactive tool policies
# ---------------------------------------------------------------------------


def auto_approve_plans() -> PolicyFn:
    """Auto-allow ExitPlanMode (approve the plan without user interaction)."""

    def _check(tool_name: str, tool_input: dict[str, Any]) -> Allow | None:
        if tool_name == "ExitPlanMode":
            return Allow()
        return None

    return _check


def auto_answer(answers: dict[str, str]) -> PolicyFn:
    """Auto-answer AskUserQuestion with a static answer map.

    Keys are question text (or substrings matched case-insensitively).
    Values are the answer to return for that question.

    If a question has no matching answer, falls through (returns None).
    """

    def _check(tool_name: str, tool_input: dict[str, Any]) -> Allow | None:
        if tool_name != "AskUserQuestion":
            return None
        questions = tool_input.get("questions", [])
        result: dict[str, str] = {}
        for q in questions:
            question_text = q.get("question", "")
            for pattern, answer in answers.items():
                if pattern.lower() in question_text.lower():
                    result[question_text] = answer
                    break
            else:
                return None  # No match for this question — fall through
        return Allow(updated_input={**tool_input, "answers": result})

    return _check


def ask_user_policy(
    handler: Callable[[dict[str, Any]], Awaitable[PermissionResult | None]],
) -> PolicyFn:
    """Route AskUserQuestion to an async handler.

    The handler receives the full tool_input dict (containing questions[])
    and returns Allow (with updated_input.answers), Deny, or None.
    """

    async def _check(tool_name: str, tool_input: dict[str, Any]) -> PermissionResult | None:
        if tool_name != "AskUserQuestion":
            return None
        return await handler(tool_input)

    return _check


def plan_approval_policy(
    handler: Callable[[dict[str, Any]], Awaitable[PermissionResult | None]],
) -> PolicyFn:
    """Route ExitPlanMode to an async handler.

    The handler receives the tool_input (containing the plan) and returns
    Allow (approve), Deny (with feedback message), or None (fall through).
    """

    async def _check(tool_name: str, tool_input: dict[str, Any]) -> PermissionResult | None:
        if tool_name != "ExitPlanMode":
            return None
        return await handler(tool_input)

    return _check


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def compose(*policies: PolicyFn) -> CanUseTool:
    """Chain policies into a single CanUseTool callback.

    Evaluates policies in order. First non-None result wins.
    If all policies return None, allows the tool call.
    """
    import asyncio

    async def _check(tool_name: str, tool_input: dict[str, Any]) -> PermissionResult:
        for policy in policies:
            result = policy(tool_name, tool_input)
            if asyncio.iscoroutine(result):
                result = await result
            if result is not None:
                return result
        return Allow()

    return _check


# ---------------------------------------------------------------------------
# Trivial policies
# ---------------------------------------------------------------------------


def allow_all(tool_name: str, tool_input: dict[str, Any]) -> Allow:
    """Allow everything."""
    return Allow()


def deny_all(tool_name: str, tool_input: dict[str, Any]) -> Deny:
    """Deny everything."""
    return Deny(message="All tool calls denied")


# ---------------------------------------------------------------------------
# Wire format helpers
# ---------------------------------------------------------------------------


def to_control_response(request_id: str, result: PermissionResult) -> dict[str, Any]:
    """Convert a PermissionResult to a control_response dict for the wire."""
    if isinstance(result, Allow):
        response: dict[str, Any] = {"behavior": "allow"}
        if result.updated_input is not None:
            response["updatedInput"] = result.updated_input
        return {
            "type": "control_response",
            "response": {
                "subtype": "success",
                "request_id": request_id,
                "response": response,
            },
        }
    return {
        "type": "control_response",
        "response": {
            "subtype": "success",
            "request_id": request_id,
            "response": {"behavior": "deny", "message": result.message},
        },
    }
