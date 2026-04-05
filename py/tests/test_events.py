"""Tests for claudewire.events — activity tracking, rate limit parsing, tool display."""

from datetime import UTC, datetime

from claudewire.events import (
    ActivityState,
    RateLimitInfo,
    parse_rate_limit_event,
    tool_display,
    update_activity,
)


# ---------------------------------------------------------------------------
# Activity state machine
# ---------------------------------------------------------------------------


class TestUpdateActivity:
    def test_thinking_block_start(self):
        activity = ActivityState()
        update_activity(activity, {
            "type": "content_block_start",
            "content_block": {"type": "thinking", "thinking": "", "signature": ""},
        })
        assert activity.phase == "thinking"
        assert activity.tool_name is None
        assert activity.thinking_text == ""

    def test_thinking_delta_accumulates(self):
        activity = ActivityState()
        update_activity(activity, {
            "type": "content_block_start",
            "content_block": {"type": "thinking", "thinking": "", "signature": ""},
        })
        update_activity(activity, {
            "type": "content_block_delta",
            "delta": {"type": "thinking_delta", "thinking": "Let me "},
        })
        update_activity(activity, {
            "type": "content_block_delta",
            "delta": {"type": "thinking_delta", "thinking": "think..."},
        })
        assert activity.thinking_text == "Let me think..."

    def test_text_block_start(self):
        activity = ActivityState()
        update_activity(activity, {
            "type": "content_block_start",
            "content_block": {"type": "text", "text": ""},
        })
        assert activity.phase == "writing"
        assert activity.text_chars == 0

    def test_text_delta_counts_chars(self):
        activity = ActivityState()
        update_activity(activity, {
            "type": "content_block_start",
            "content_block": {"type": "text", "text": ""},
        })
        update_activity(activity, {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "Hello"},
        })
        update_activity(activity, {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": " world"},
        })
        assert activity.text_chars == 11

    def test_tool_use_block_start(self):
        activity = ActivityState()
        update_activity(activity, {
            "type": "content_block_start",
            "content_block": {"type": "tool_use", "name": "Bash", "id": "t1", "input": {}},
        })
        assert activity.phase == "tool_use"
        assert activity.tool_name == "Bash"

    def test_tool_input_preview_truncated(self):
        activity = ActivityState()
        update_activity(activity, {
            "type": "content_block_start",
            "content_block": {"type": "tool_use", "name": "Bash", "id": "t1", "input": {}},
        })
        update_activity(activity, {
            "type": "content_block_delta",
            "delta": {"type": "input_json_delta", "partial_json": "x" * 300},
        })
        assert len(activity.tool_input_preview) == 200

    def test_tool_use_block_stop_transitions_to_waiting(self):
        activity = ActivityState()
        activity.phase = "tool_use"
        update_activity(activity, {"type": "content_block_stop"})
        assert activity.phase == "waiting"

    def test_non_tool_block_stop_doesnt_change_phase(self):
        activity = ActivityState()
        activity.phase = "writing"
        update_activity(activity, {"type": "content_block_stop"})
        assert activity.phase == "writing"

    def test_message_start_increments_turn_count(self):
        activity = ActivityState()
        assert activity.turn_count == 0
        update_activity(activity, {"type": "message_start"})
        assert activity.turn_count == 1
        update_activity(activity, {"type": "message_start"})
        assert activity.turn_count == 2

    def test_message_delta_end_turn_goes_idle(self):
        activity = ActivityState()
        activity.phase = "writing"
        activity.tool_name = "Bash"
        update_activity(activity, {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
        })
        assert activity.phase == "idle"
        assert activity.tool_name is None

    def test_message_delta_tool_use_goes_waiting(self):
        activity = ActivityState()
        activity.phase = "writing"
        update_activity(activity, {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use"},
        })
        assert activity.phase == "waiting"

    def test_last_event_updated(self):
        activity = ActivityState()
        assert activity.last_event is None
        update_activity(activity, {"type": "message_start"})
        assert activity.last_event is not None
        assert isinstance(activity.last_event, datetime)


# ---------------------------------------------------------------------------
# Tool display names
# ---------------------------------------------------------------------------


class TestToolDisplay:
    def test_known_tool(self):
        assert tool_display("Bash") == "running bash command"
        assert tool_display("Read") == "reading file"
        assert tool_display("Grep") == "searching code"

    def test_mcp_tool(self):
        assert tool_display("mcp__slack__send_message") == "slack: send_message"

    def test_mcp_tool_short(self):
        assert tool_display("mcp__x") == "using mcp__x"

    def test_unknown_tool(self):
        assert tool_display("CustomTool") == "using CustomTool"


# ---------------------------------------------------------------------------
# Rate limit parsing
# ---------------------------------------------------------------------------


class TestParseRateLimitEvent:
    def test_valid_event(self):
        data = {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": "allowed",
                "resetsAt": 1700000000,
                "rateLimitType": "five_hour",
                "utilization": 0.42,
            },
            "uuid": "u1",
            "session_id": "s1",
        }
        info = parse_rate_limit_event(data)
        assert info is not None
        assert isinstance(info, RateLimitInfo)
        assert info.rate_limit_type == "five_hour"
        assert info.status == "allowed"
        assert info.utilization == 0.42
        assert info.resets_at.tzinfo is not None

    def test_warning_status(self):
        data = {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": "allowed_warning",
                "resetsAt": 1700000000,
                "rateLimitType": "five_hour",
            },
        }
        info = parse_rate_limit_event(data)
        assert info is not None
        assert info.status == "allowed_warning"
        assert info.utilization is None

    def test_rejected_status(self):
        data = {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": "rejected",
                "resetsAt": 1700003600,
                "rateLimitType": "five_hour",
            },
        }
        info = parse_rate_limit_event(data)
        assert info is not None
        assert info.status == "rejected"

    def test_wrong_type_returns_none(self):
        assert parse_rate_limit_event({"type": "system", "subtype": "init"}) is None

    def test_missing_resets_at_returns_none(self):
        data = {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": "allowed",
                "rateLimitType": "five_hour",
            },
        }
        assert parse_rate_limit_event(data) is None

    def test_missing_rate_limit_info_returns_none(self):
        data = {"type": "rate_limit_event"}
        assert parse_rate_limit_event(data) is None

    def test_resets_at_is_utc_datetime(self):
        data = {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": "allowed",
                "resetsAt": 1700000000,
                "rateLimitType": "five_hour",
            },
        }
        info = parse_rate_limit_event(data)
        assert info is not None
        assert info.resets_at == datetime.fromtimestamp(1700000000, tz=UTC)
