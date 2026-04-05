"""Tests for claudewire.schema — validation of stream-json protocol messages."""

from claudewire.schema import (
    AssistantMsg,
    ControlRequestMsg,
    ControlResponseMsg,
    RateLimitEventMsg,
    ResultMsg,
    StreamEventMsg,
    SystemMsg,
    UserMsg,
    validate_inbound,
    validate_inbound_or_bare,
    validate_outbound,
)


# ---------------------------------------------------------------------------
# Inbound message validation
# ---------------------------------------------------------------------------


class TestValidateInbound:
    def test_stream_event_message_start(self):
        msg = {
            "type": "stream_event",
            "uuid": "u1",
            "session_id": "s1",
            "event": {
                "type": "message_start",
                "message": {
                    "model": "claude-sonnet-4-20250514",
                    "id": "msg_123",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 100, "output_tokens": 0},
                },
            },
        }
        result = validate_inbound(msg)
        assert result.ok
        assert isinstance(result.model, StreamEventMsg)

    def test_stream_event_content_block_delta_text(self):
        msg = {
            "type": "stream_event",
            "uuid": "u1",
            "session_id": "s1",
            "event": {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Hello"},
            },
        }
        result = validate_inbound(msg)
        assert result.ok
        assert isinstance(result.model, StreamEventMsg)

    def test_stream_event_content_block_delta_thinking(self):
        msg = {
            "type": "stream_event",
            "uuid": "u1",
            "session_id": "s1",
            "event": {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "Let me think..."},
            },
        }
        result = validate_inbound(msg)
        assert result.ok

    def test_stream_event_content_block_delta_input_json(self):
        msg = {
            "type": "stream_event",
            "uuid": "u1",
            "session_id": "s1",
            "event": {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"cmd":'},
            },
        }
        result = validate_inbound(msg)
        assert result.ok

    def test_stream_event_content_block_start_text(self):
        msg = {
            "type": "stream_event",
            "uuid": "u1",
            "session_id": "s1",
            "event": {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        }
        result = validate_inbound(msg)
        assert result.ok

    def test_stream_event_content_block_start_tool_use(self):
        msg = {
            "type": "stream_event",
            "uuid": "u1",
            "session_id": "s1",
            "event": {
                "type": "content_block_start",
                "index": 1,
                "content_block": {
                    "type": "tool_use",
                    "id": "toolu_abc",
                    "name": "Bash",
                    "input": {},
                },
            },
        }
        result = validate_inbound(msg)
        assert result.ok

    def test_stream_event_content_block_start_thinking(self):
        msg = {
            "type": "stream_event",
            "uuid": "u1",
            "session_id": "s1",
            "event": {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "thinking", "thinking": "", "signature": ""},
            },
        }
        result = validate_inbound(msg)
        assert result.ok

    def test_stream_event_message_delta(self):
        msg = {
            "type": "stream_event",
            "uuid": "u1",
            "session_id": "s1",
            "event": {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 50},
            },
        }
        result = validate_inbound(msg)
        assert result.ok

    def test_stream_event_message_stop(self):
        msg = {
            "type": "stream_event",
            "uuid": "u1",
            "session_id": "s1",
            "event": {"type": "message_stop"},
        }
        result = validate_inbound(msg)
        assert result.ok

    def test_stream_event_content_block_stop(self):
        msg = {
            "type": "stream_event",
            "uuid": "u1",
            "session_id": "s1",
            "event": {"type": "content_block_stop", "index": 0},
        }
        result = validate_inbound(msg)
        assert result.ok

    def test_assistant_message(self):
        msg = {
            "type": "assistant",
            "message": {
                "model": "claude-sonnet-4-20250514",
                "id": "msg_123",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "Hello!"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 100, "output_tokens": 10},
            },
            "parent_tool_use_id": None,
            "session_id": "s1",
            "uuid": "u1",
        }
        result = validate_inbound(msg)
        assert result.ok
        assert isinstance(result.model, AssistantMsg)

    def test_user_message(self):
        msg = {
            "type": "user",
            "message": {"role": "user", "content": "Hello"},
            "session_id": "s1",
            "uuid": "u1",
        }
        result = validate_inbound(msg)
        assert result.ok
        assert isinstance(result.model, UserMsg)

    def test_system_message(self):
        msg = {"type": "system", "subtype": "init", "version": "1.0.0"}
        result = validate_inbound(msg)
        assert result.ok
        assert isinstance(result.model, SystemMsg)

    def test_result_message_success(self):
        msg = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "duration_ms": 1234,
            "duration_api_ms": 1000,
            "num_turns": 1,
            "session_id": "s1",
            "uuid": "u1",
            "result": "Done!",
        }
        result = validate_inbound(msg)
        assert result.ok
        assert isinstance(result.model, ResultMsg)

    def test_result_message_error(self):
        msg = {
            "type": "result",
            "subtype": "error",
            "is_error": True,
            "duration_ms": 500,
            "duration_api_ms": 400,
            "num_turns": 0,
            "session_id": "s1",
            "uuid": "u1",
        }
        result = validate_inbound(msg)
        assert result.ok
        assert isinstance(result.model, ResultMsg)

    def test_control_request(self):
        msg = {
            "type": "control_request",
            "request_id": "req-1",
            "request": {"subtype": "can_use_tool", "tool_name": "Bash", "input": {}},
        }
        result = validate_inbound(msg)
        assert result.ok
        assert isinstance(result.model, ControlRequestMsg)

    def test_control_response(self):
        msg = {
            "type": "control_response",
            "response": {
                "subtype": "success",
                "request_id": "req-1",
                "response": {},
            },
        }
        result = validate_inbound(msg)
        assert result.ok
        assert isinstance(result.model, ControlResponseMsg)

    def test_rate_limit_event(self):
        msg = {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": "allowed",
                "resetsAt": 1700000000,
                "rateLimitType": "five_hour",
                "utilization": 0.5,
            },
            "uuid": "u1",
            "session_id": "s1",
        }
        result = validate_inbound(msg)
        assert result.ok
        assert isinstance(result.model, RateLimitEventMsg)

    def test_missing_type_field(self):
        msg = {"subtype": "init", "version": "1.0"}
        result = validate_inbound(msg)
        assert not result.ok
        assert any("type" in e.message for e in result.errors)

    def test_unknown_type_is_error(self):
        msg = {"type": "totally_unknown_type_xyz"}
        result = validate_inbound(msg)
        assert not result.ok

    def test_unknown_fields_are_warnings(self):
        msg = {
            "type": "stream_event",
            "uuid": "u1",
            "session_id": "s1",
            "some_new_field": "surprise",
            "event": {"type": "message_stop"},
        }
        result = validate_inbound(msg)
        # Unknown fields in strict models produce warnings, not hard errors
        warnings = [e for e in result.errors if e.level == "warning"]
        errors = [e for e in result.errors if e.level == "error"]
        assert len(warnings) > 0
        assert len(errors) == 0

    def test_trace_context_stripped(self):
        msg = {
            "type": "system",
            "subtype": "init",
            "_trace_context": {"traceparent": "00-abc-def-01"},
        }
        result = validate_inbound(msg)
        assert result.ok

    def test_usage_extra_fields_allowed(self):
        msg = {
            "type": "stream_event",
            "uuid": "u1",
            "session_id": "s1",
            "event": {
                "type": "message_start",
                "message": {
                    "model": "claude-sonnet-4-20250514",
                    "id": "msg_123",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 0,
                        "some_future_field": 42,
                    },
                },
            },
        }
        result = validate_inbound(msg)
        assert result.ok


# ---------------------------------------------------------------------------
# Bare stream event validation
# ---------------------------------------------------------------------------


class TestValidateInboundOrBare:
    def test_bare_content_block_delta(self):
        msg = {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "hi"},
        }
        result = validate_inbound_or_bare(msg)
        assert result.ok

    def test_bare_message_start(self):
        msg = {
            "type": "message_start",
            "message": {
                "model": "claude-sonnet-4-20250514",
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [],
            },
        }
        result = validate_inbound_or_bare(msg)
        assert result.ok

    def test_bare_message_stop(self):
        result = validate_inbound_or_bare({"type": "message_stop"})
        assert result.ok

    def test_bare_content_block_stop(self):
        result = validate_inbound_or_bare({"type": "content_block_stop", "index": 0})
        assert result.ok

    def test_non_bare_falls_through_to_inbound(self):
        msg = {"type": "system", "subtype": "init"}
        result = validate_inbound_or_bare(msg)
        assert result.ok
        assert isinstance(result.model, SystemMsg)


# ---------------------------------------------------------------------------
# Outbound message validation
# ---------------------------------------------------------------------------


class TestValidateOutbound:
    def test_user_message(self):
        msg = {
            "type": "user",
            "content": "Hello",
            "session_id": "s1",
        }
        result = validate_outbound(msg)
        assert result.ok

    def test_user_message_with_message_inner(self):
        msg = {
            "type": "user",
            "message": {"role": "user", "content": "Hello"},
        }
        result = validate_outbound(msg)
        assert result.ok

    def test_control_response_outbound(self):
        msg = {
            "type": "control_response",
            "response": {
                "subtype": "success",
                "request_id": "req-1",
                "response": {"allowed": True},
            },
        }
        result = validate_outbound(msg)
        assert result.ok

    def test_control_request_outbound(self):
        msg = {
            "type": "control_request",
            "request_id": "req-2",
            "request": {"subtype": "initialize"},
        }
        result = validate_outbound(msg)
        assert result.ok

    def test_missing_type(self):
        result = validate_outbound({"content": "hi"})
        assert not result.ok


# ---------------------------------------------------------------------------
# ValidationResult properties
# ---------------------------------------------------------------------------


class TestValidationResult:
    def test_ok_with_no_errors(self):
        result = validate_inbound({"type": "system", "subtype": "init"})
        assert result.ok
        assert result.errors == []

    def test_ok_false_with_errors(self):
        result = validate_inbound({})
        assert not result.ok

    def test_ok_true_with_only_warnings(self):
        msg = {
            "type": "stream_event",
            "uuid": "u1",
            "session_id": "s1",
            "new_unknown_field": True,
            "event": {"type": "message_stop"},
        }
        result = validate_inbound(msg)
        assert result.ok  # warnings don't make ok=False
