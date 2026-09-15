"""SDCP V3.0.0 Protocol Definitions and Packet Utilities for Elegoo Centauri Carbon."""

from __future__ import annotations

import json
import logging
import secrets
import time
from enum import IntEnum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class Cmd(IntEnum):
    """Elegoo SDCP Command Codes."""
    GET_PRINTER_STATUS = 0
    GET_PRINTER_ATTRIBUTES = 1
    START_PRINT = 128
    PAUSE_PRINT = 129
    STOP_PRINT = 130
    RESUME_PRINT = 131
    GET_FILE_LIST = 258
    DELETE_FILE_LIST = 259
    GET_PRINT_HISTORY = 320
    GET_PRINT_HISTORY_DETAIL = 321
    GET_CANVAS_STATUS = 324
    CHANGE_PRINT_PARAMS = 403
    SUBSCRIBE = 512


class MessageType(IntEnum):
    """SDCP Message Categories."""
    UNKNOWN = 0
    RESPONSE = 1
    STATUS = 2
    ATTRIBUTES = 3


def generate_request_id() -> str:
    """Generate a random 16-character hex request ID."""
    return secrets.token_hex(8)


def build_request_envelope(
    cmd: int,
    data: dict[str, Any] | None,
    mainboard_id: str,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Build a compliant SDCP V3.0.0 request envelope."""
    req_id = request_id or generate_request_id()
    now_ms = int(time.time() * 1000)
    return {
        "Id": mainboard_id,
        "Data": {
            "Cmd": int(cmd),
            "Data": data if data is not None else {},
            "RequestID": req_id,
            "MainboardID": mainboard_id,
            "TimeStamp": now_ms,
            "From": 1,
        },
        "Topic": f"sdcp/request/{mainboard_id}",
    }


def build_temp_payload(
    *,
    bed: float | None = None,
    nozzle: float | None = None,
    chamber: float | None = None,
) -> dict[str, float]:
    """Build payload for Cmd 403 to set heater temperatures."""
    targets: dict[str, float] = {}
    if bed is not None:
        if not (0 <= bed <= 110):
            raise ValueError(f"Target bed temperature {bed}°C out of safe range (0..110°C)")
        targets["TempTargetHotbed"] = float(round(bed, 1))

    if nozzle is not None:
        if not (0 <= nozzle <= 300):
            raise ValueError(f"Target nozzle temperature {nozzle}°C out of safe range (0..300°C)")
        targets["TempTargetNozzle"] = float(round(nozzle, 1))

    if chamber is not None:
        if not (0 <= chamber <= 70):
            raise ValueError(f"Target chamber temperature {chamber}°C out of safe range (0..70°C)")
        targets["TempTargetBox"] = float(round(chamber, 1))

    if not targets:
        raise ValueError("At least one temperature target must be provided")

    return targets


def build_fan_payload(
    *,
    box_fan: int | None = None,
    auxiliary_fan: int | None = None,
    model_fan: int | None = None,
) -> dict[str, dict[str, int]]:
    """Build payload for Cmd 403 to set fan speeds (0..100%)."""
    fan_map: dict[str, int] = {}
    if box_fan is not None:
        if not (0 <= box_fan <= 100):
            raise ValueError("Box fan speed must be between 0 and 100%")
        fan_map["BoxFan"] = int(box_fan)

    if auxiliary_fan is not None:
        if not (0 <= auxiliary_fan <= 100):
            raise ValueError("Auxiliary fan speed must be between 0 and 100%")
        fan_map["AuxiliaryFan"] = int(auxiliary_fan)

    if model_fan is not None:
        if not (0 <= model_fan <= 100):
            raise ValueError("Model fan speed must be between 0 and 100%")
        fan_map["ModelFan"] = int(model_fan)

    if not fan_map:
        raise ValueError("At least one fan speed must be provided")

    return {"TargetFanSpeed": fan_map}


def build_light_payload(on: bool) -> dict[str, dict[str, int]]:
    """Build payload for Cmd 403 to toggle chamber lighting."""
    return {"LightStatus": {"SecondLight": 1 if on else 0}}


class ParsedMessage:
    """Normalized container for incoming SDCP frames."""

    def __init__(
        self,
        raw: dict[str, Any],
        msg_type: MessageType,
        mainboard_id: str | None = None,
        request_id: str | None = None,
        cmd: int | None = None,
        status_data: dict[str, Any] | None = None,
        attributes_data: dict[str, Any] | None = None,
        ack: int | None = None,
    ):
        self.raw = raw
        self.msg_type = msg_type
        self.mainboard_id = mainboard_id
        self.request_id = request_id
        self.cmd = cmd
        self.status = status_data
        self.attributes = attributes_data
        self.ack = ack

    def __repr__(self) -> str:
        return f"<ParsedMessage type={self.msg_type.name} req_id={self.request_id} ack={self.ack}>"


def parse_sdcp_message(raw_data: str | bytes) -> ParsedMessage:
    """Parse an incoming WebSocket message from the Centauri Carbon printer.
    
    Handles standard envelope (Topic sdcp/response/..., sdcp/status/...) as well as
    direct unwrapped {"Status": {...}} JSON structures emitted by the firmware.
    """
    if isinstance(raw_data, bytes):
        raw_text = raw_data.decode("utf-8", errors="replace")
    else:
        raw_text = raw_data

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as err:
        logger.warning(f"Invalid JSON received from printer: {raw_text[:120]}")
        return ParsedMessage(raw={}, msg_type=MessageType.UNKNOWN)

    if not isinstance(payload, dict):
        return ParsedMessage(raw={"_non_dict": payload}, msg_type=MessageType.UNKNOWN)

    # 1. Check for unwrapped status object: {"Status": {...}}
    if "Status" in payload and isinstance(payload["Status"], dict):
        return ParsedMessage(
            raw=payload,
            msg_type=MessageType.STATUS,
            status_data=payload["Status"],
        )

    topic = payload.get("Topic", "")
    data = payload.get("Data", {})
    if not isinstance(data, dict):
        data = {}

    mainboard_id = payload.get("Id") or data.get("MainboardID")
    request_id = data.get("RequestID")
    cmd = data.get("Cmd")

    # 2. Check for Response packet: sdcp/response/<mainboard_id>
    if "sdcp/response" in topic or "Ack" in data or ("Data" in data and isinstance(data["Data"], dict) and "Ack" in data["Data"]):
        inner = data.get("Data", {})
        ack = inner.get("Ack") if isinstance(inner, dict) else data.get("Ack")
        return ParsedMessage(
            raw=payload,
            msg_type=MessageType.RESPONSE,
            mainboard_id=mainboard_id,
            request_id=request_id,
            cmd=cmd,
            ack=ack,
        )

    # 3. Check for Status push: sdcp/status/<mainboard_id>
    if "sdcp/status" in topic or "Status" in data:
        status_dict = data.get("Status") if "Status" in data else data
        return ParsedMessage(
            raw=payload,
            msg_type=MessageType.STATUS,
            mainboard_id=mainboard_id,
            request_id=request_id,
            status_data=status_dict,
        )

    # 4. Check for Attributes push: sdcp/attributes/<mainboard_id>
    if "sdcp/attributes" in topic or "Attributes" in data or "MachineName" in data:
        attrs_dict = data.get("Attributes") if "Attributes" in data else data
        return ParsedMessage(
            raw=payload,
            msg_type=MessageType.ATTRIBUTES,
            mainboard_id=mainboard_id,
            request_id=request_id,
            attributes_data=attrs_dict,
        )

    return ParsedMessage(raw=payload, msg_type=MessageType.UNKNOWN, mainboard_id=mainboard_id)
