"""Unit tests for SDCP protocol and packet builders."""

import pytest
from centauri_dryer.protocol import (
    Cmd,
    MessageType,
    build_fan_payload,
    build_light_payload,
    build_request_envelope,
    build_temp_payload,
    parse_sdcp_message,
)

MAINBOARD_ID = "0123456789abcdef0123456789abcdef"


def test_build_request_envelope():
    envelope = build_request_envelope(Cmd.GET_PRINTER_STATUS, {}, MAINBOARD_ID, request_id="test1234")
    assert envelope["Id"] == MAINBOARD_ID
    assert envelope["Topic"] == f"sdcp/request/{MAINBOARD_ID}"
    assert envelope["Data"]["Cmd"] == 0
    assert envelope["Data"]["RequestID"] == "test1234"
    assert envelope["Data"]["From"] == 1


def test_build_temp_payload_valid():
    payload = build_temp_payload(bed=65.0, nozzle=0.0)
    assert payload == {"TempTargetHotbed": 65.0, "TempTargetNozzle": 0.0}


def test_build_temp_payload_out_of_range():
    with pytest.raises(ValueError):
        build_temp_payload(bed=150.0)  # Max 110


def test_build_fan_payload():
    payload = build_fan_payload(box_fan=20)
    assert payload == {"TargetFanSpeed": {"BoxFan": 20}}


def test_build_light_payload():
    payload_on = build_light_payload(True)
    assert payload_on == {"LightStatus": {"SecondLight": 1}}
    payload_off = build_light_payload(False)
    assert payload_off == {"LightStatus": {"SecondLight": 0}}


def test_parse_unwrapped_status():
    raw_json = (
        '{"Status": {"TempOfHotbed": 64.5, "TempTargetHotbed": 65.0, "TempOfBox": 32.0, '
        '"PrintInfo": {"Status": 0}}}'
    )
    msg = parse_sdcp_message(raw_json)
    assert msg.msg_type == MessageType.STATUS
    assert msg.status["TempOfHotbed"] == 64.5
    assert msg.status["TempTargetHotbed"] == 65.0


def test_parse_response_ack():
    raw_json = (
        f'{{"Id": "{MAINBOARD_ID}", "Topic": "sdcp/response/{MAINBOARD_ID}", '
        f'"Data": {{"Cmd": 0, "Data": {{"Ack": 0}}, "RequestID": "req123"}}}}'
    )
    msg = parse_sdcp_message(raw_json)
    assert msg.msg_type == MessageType.RESPONSE
    assert msg.request_id == "req123"
    assert msg.ack == 0
