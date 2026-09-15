"""Data models for telemetry, drying presets, and session tracking."""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional


class DryingState(str, Enum):
    IDLE = "idle"
    PREHEATING = "preheating"
    DRYING = "drying"
    PAUSED = "paused"
    COOLDOWN = "cooldown"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass
class Telemetry:
    """Live printer telemetry status."""
    connected: bool = False
    bed_temp: float = 0.0
    bed_target: float = 0.0
    chamber_temp: float = 0.0
    chamber_target: float = 0.0
    nozzle_temp: float = 0.0
    nozzle_target: float = 0.0
    box_fan: int = 0
    aux_fan: int = 0
    model_fan: int = 0
    light_on: bool = False
    print_status: int = 0  # 0=idle, 13=printing
    print_filename: str = ""
    last_update: float = field(default_factory=time.time)

    @classmethod
    def from_status_dict(cls, data: dict[str, Any], connected: bool = True) -> Telemetry:
        fan_speed = data.get("CurrentFanSpeed", {}) or {}
        light_status = data.get("LightStatus", {}) or {}
        print_info = data.get("PrintInfo", {}) or {}

        # Safely extract floats/ints
        def _get_float(key: str, default: float = 0.0) -> float:
            val = data.get(key)
            if val is None:
                return default
            if isinstance(val, (list, tuple)) and len(val) >= 2:
                return float(val[1])  # pair format [target, actual]
            try:
                return round(float(val), 1)
            except (ValueError, TypeError):
                return default

        return cls(
            connected=connected,
            bed_temp=_get_float("TempOfHotbed"),
            bed_target=_get_float("TempTargetHotbed"),
            chamber_temp=_get_float("TempOfBox"),
            chamber_target=_get_float("TempTargetBox"),
            nozzle_temp=_get_float("TempOfNozzle"),
            nozzle_target=_get_float("TempTargetNozzle"),
            box_fan=int(fan_speed.get("BoxFan", 0)),
            aux_fan=int(fan_speed.get("AuxiliaryFan", 0)),
            model_fan=int(fan_speed.get("ModelFan", 0)),
            light_on=bool(light_status.get("SecondLight", 0)),
            print_status=int(print_info.get("Status", 0)),
            print_filename=str(print_info.get("Filename", "")),
            last_update=time.time(),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DryingPreset:
    """Filament drying preset configuration."""
    id: str
    name: str
    target_bed_temp: float
    duration_hours: float
    fan_box_speed: int = 15  # Recommended BoxFan % for moisture exhaust
    enable_spool_flip_reminder: bool = True
    description: str = ""
    accent_color: str = "#00d2ff"

    @property
    def duration_seconds(self) -> int:
        return int(self.duration_hours * 3600)


DEFAULT_PRESETS: dict[str, DryingPreset] = {
    "petg": DryingPreset(
        id="petg",
        name="PETG",
        target_bed_temp=65.0,
        duration_hours=5.0,
        fan_box_speed=15,
        enable_spool_flip_reminder=True,
        description="PETG: 65 °C | 5h | Spool flip reminder at 2h30",
        accent_color="#00e5ff",
    ),
    "pla": DryingPreset(
        id="pla",
        name="PLA",
        target_bed_temp=50.0,
        duration_hours=5.0,
        fan_box_speed=10,
        enable_spool_flip_reminder=True,
        description="PLA: 50 °C | 5h | Spool flip reminder at 2h30",
        accent_color="#00e676",
    ),
    "tpu": DryingPreset(
        id="tpu",
        name="TPU / Flex",
        target_bed_temp=55.0,
        duration_hours=6.0,
        fan_box_speed=15,
        enable_spool_flip_reminder=True,
        description="TPU / Flex: 55 °C | 6h | Spool flip reminder at 3h00",
        accent_color="#e040fb",
    ),
    "asa": DryingPreset(
        id="asa",
        name="ASA / ABS",
        target_bed_temp=75.0,
        duration_hours=4.0,
        fan_box_speed=20,
        enable_spool_flip_reminder=True,
        description="ASA / ABS: 75 °C | 4h | Spool flip reminder at 2h00",
        accent_color="#ff9100",
    ),
    "nylon": DryingPreset(
        id="nylon",
        name="Nylon (PA)",
        target_bed_temp=80.0,
        duration_hours=8.0,
        fan_box_speed=25,
        enable_spool_flip_reminder=True,
        description="Nylon (PA): 80 °C | 8h | Spool flip reminder at 4h00",
        accent_color="#ff5252",
    ),
}


@dataclass
class DryingSession:
    """Current or historical drying session state."""
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    preset_id: str = "petg"
    preset_name: str = "PETG"
    target_bed_temp: float = 65.0
    duration_seconds: int = 5 * 3600
    state: DryingState = DryingState.IDLE
    start_time: float | None = None
    paused_at: float | None = None
    accumulated_pause_sec: float = 0.0
    end_time: float | None = None
    fan_box_speed: int = 15
    flip_spool_reminded: bool = False
    cooldown_completed: bool = False
    thermal_watchdog_triggers: int = 0
    temp_anomaly_detected: bool = False

    def elapsed_seconds(self) -> int:
        if self.start_time is None:
            return 0
        if self.state == DryingState.PAUSED and self.paused_at:
            current_duration = self.paused_at - self.start_time - self.accumulated_pause_sec
        elif self.end_time:
            current_duration = self.end_time - self.start_time - self.accumulated_pause_sec
        else:
            current_duration = time.time() - self.start_time - self.accumulated_pause_sec
        return max(0, min(int(current_duration), self.duration_seconds))

    def remaining_seconds(self) -> int:
        return max(0, self.duration_seconds - self.elapsed_seconds())

    def progress_percent(self) -> float:
        if self.duration_seconds <= 0:
            return 0.0
        pct = (self.elapsed_seconds() / self.duration_seconds) * 100.0
        return min(100.0, max(0.0, round(pct, 1)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "preset_id": self.preset_id,
            "preset_name": self.preset_name,
            "target_bed_temp": self.target_bed_temp,
            "duration_seconds": self.duration_seconds,
            "elapsed_seconds": self.elapsed_seconds(),
            "remaining_seconds": self.remaining_seconds(),
            "progress_percent": self.progress_percent(),
            "state": self.state.value,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "fan_box_speed": self.fan_box_speed,
            "flip_spool_reminded": self.flip_spool_reminded,
            "cooldown_completed": self.cooldown_completed,
            "thermal_watchdog_triggers": self.thermal_watchdog_triggers,
            "temp_anomaly_detected": self.temp_anomaly_detected,
        }
