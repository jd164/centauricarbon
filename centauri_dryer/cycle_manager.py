"""Drying Cycle State Machine and Session Management."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

from centauri_dryer.client import CentauriClient
from centauri_dryer.models import (
    DEFAULT_PRESETS,
    DryingPreset,
    DryingSession,
    DryingState,
    Telemetry,
)

logger = logging.getLogger("centauri_dryer.cycle")

HISTORY_FILE = Path("drying_history.json")


class DryingCycleError(Exception):
    """Exception raised for cycle state issues."""


class DryingCycleManager:
    """Orchestrates drying cycles, timers, thermal safety, and session logging."""

    def __init__(self, client: CentauriClient):
        self.client = client
        self.presets = dict(DEFAULT_PRESETS)
        self.current_session: DryingSession = DryingSession()
        self.temp_history: deque[dict[str, Any]] = deque(maxlen=15000)  # >8h continuous logging at 2s
        self._loop_task: asyncio.Task[None] | None = None
        self._listeners: list[Callable[[DryingSession, Telemetry], None]] = []

        # Hook telemetry listener
        self.client.add_telemetry_listener(self._on_telemetry_update)

        # Load historical sessions
        self.history_records: list[dict[str, Any]] = self._load_history()

    def add_listener(self, cb: Callable[[DryingSession, Telemetry], None]) -> None:
        if cb not in self._listeners:
            self._listeners.append(cb)

    def remove_listener(self, cb: Callable[[DryingSession, Telemetry], None]) -> None:
        if cb in self._listeners:
            self._listeners.remove(cb)

    def _notify(self) -> None:
        for cb in self._listeners:
            try:
                cb(self.current_session, self.client.latest_telemetry)
            except Exception as e:
                logger.error(f"Error in cycle listener: {e}")

    def _on_telemetry_update(self, telemetry: Telemetry) -> None:
        # Record temperature history point if connected
        if telemetry.connected:
            self.temp_history.append({
                "timestamp": round(telemetry.last_update, 1),
                "bed": telemetry.bed_temp,
                "bed_target": telemetry.bed_target,
                "chamber": telemetry.chamber_temp,
                "nozzle": telemetry.nozzle_temp,
            })
        self._notify()

    async def start(self) -> None:
        """Start the background monitor task."""
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = asyncio.create_task(self._monitor_loop(), name="cycle-monitor")

    async def stop(self) -> None:
        """Stop background monitor task and ensure heaters are powered off."""
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
        if self.current_session.state in (DryingState.DRYING, DryingState.PREHEATING, DryingState.PAUSED):
            logger.info("Cycle manager stopping: shutting down heaters...")
            await self.client.set_temperatures(bed=0.0, nozzle=0.0)

    async def start_cycle(
        self,
        preset_id: str,
        custom_temp: float | None = None,
        custom_duration_hours: float | None = None,
        fan_box_speed: int | None = None,
    ) -> DryingSession:
        """Initiate a filament drying session."""
        telemetry = self.client.latest_telemetry

        # 1. Safety Check: Verify printer is not printing a 3D model!
        if telemetry.print_status == 13:
            raise DryingCycleError("The printer is currently printing a file! Cannot start drying.")

        # 2. Determine temperature, duration and fan
        if preset_id == "custom":
            if custom_temp is None or not (30 <= custom_temp <= 85):
                raise DryingCycleError("Custom temperature must be between 30 and 85 °C for safety reasons.")
            if custom_duration_hours is None or not (0.1 <= custom_duration_hours <= 24.0):
                raise DryingCycleError("Duration must be between 0.1 and 24 hours.")
            preset_name = "Custom"
            target_temp = float(custom_temp)
            duration_sec = int(custom_duration_hours * 3600)
            box_fan = fan_box_speed if fan_box_speed is not None else 15
        else:
            if preset_id not in self.presets:
                raise DryingCycleError(f"Unknown preset: {preset_id}")
            preset = self.presets[preset_id]
            preset_name = preset.name
            target_temp = preset.target_bed_temp
            duration_sec = preset.duration_seconds
            box_fan = fan_box_speed if fan_box_speed is not None else preset.fan_box_speed

        # 3. Initialize session state
        self.current_session = DryingSession(
            preset_id=preset_id,
            preset_name=preset_name,
            target_bed_temp=target_temp,
            duration_seconds=duration_sec,
            state=DryingState.PREHEATING,
            start_time=time.time(),
            fan_box_speed=box_fan,
        )

        logger.info(
            f"Starting drying cycle: {preset_name} | Target: {target_temp}°C | "
            f"Duration: {duration_sec / 3600:.1f}h | BoxFan: {box_fan}%"
        )

        # 4. Command the printer to heat the bed and activate ventilation
        try:
            await self.client.set_temperatures(bed=target_temp, nozzle=0.0)
            if box_fan > 0:
                await self.client.set_fans(box_fan=box_fan)
        except Exception as e:
            self.current_session.state = DryingState.CANCELLED
            raise DryingCycleError(f"Failed to send command to printer: {e}") from e

        self._notify()
        return self.current_session

    async def pause_cycle(self) -> DryingSession:
        """Pause the drying cycle (turns off bed heat temporarily)."""
        if self.current_session.state not in (DryingState.DRYING, DryingState.PREHEATING):
            raise DryingCycleError("No active cycle to pause.")

        self.current_session.state = DryingState.PAUSED
        self.current_session.paused_at = time.time()

        # Safely reduce bed temp during pause
        with contextlib.suppress(Exception):
            await self.client.set_temperatures(bed=0.0)

        logger.info(f"Drying cycle paused at {self.current_session.elapsed_seconds()}s")
        self._notify()
        return self.current_session

    async def resume_cycle(self) -> DryingSession:
        """Resume a paused drying cycle."""
        if self.current_session.state != DryingState.PAUSED:
            raise DryingCycleError("The cycle is not paused.")

        if self.current_session.paused_at:
            pause_delta = time.time() - self.current_session.paused_at
            self.current_session.accumulated_pause_sec += pause_delta
            self.current_session.paused_at = None

        self.current_session.state = DryingState.DRYING

        # Reheat bed to target
        await self.client.set_temperatures(bed=self.current_session.target_bed_temp, nozzle=0.0)
        if self.current_session.fan_box_speed > 0:
            await self.client.set_fans(box_fan=self.current_session.fan_box_speed)

        logger.info("Drying cycle resumed.")
        self._notify()
        return self.current_session

    async def cancel_cycle(self) -> DryingSession:
        """Cancel the active drying cycle immediately and trigger cooldown."""
        logger.warning("Cancelling drying cycle!")
        self.current_session.state = DryingState.CANCELLED
        self.current_session.end_time = time.time()

        # Emergency cooldown: bed to 0°C
        await self.client.emergency_cooldown()

        self._record_session()
        self._notify()
        return self.current_session

    async def complete_cycle(self) -> None:
        """Called automatically when timer elapses."""
        logger.info("Drying cycle duration reached! Completing cycle...")
        self.current_session.state = DryingState.COMPLETED
        self.current_session.end_time = time.time()

        # Turn off bed heater
        await self.client.set_temperatures(bed=0.0, nozzle=0.0)

        self._record_session()
        self._notify()

    async def _monitor_loop(self) -> None:
        """Tick once per second to manage timer, temperature transition, anti-idle watchdog, and alerts."""
        last_keepalive_time = time.time()

        while True:
            try:
                await asyncio.sleep(1.0)
                session = self.current_session
                telemetry = self.client.latest_telemetry
                now = time.time()

                # --- 1. Thermal Safety & Watchdog ---
                # Safety: Emergency cutoff if temperature reading exceeds 85°C
                if telemetry.connected and telemetry.bed_temp > 85.0:
                    logger.critical(
                        f"🚨 EMERGENCY CUTOFF: Bed temperature exceeded 85°C ({telemetry.bed_temp}°C)! "
                        f"Immediately cutting heaters to 0°C!"
                    )
                    await self.cancel_cycle()
                    session.temp_anomaly_detected = True
                    self._notify()
                    continue

                # 3D printer firmwares often shut heaters off if no G-code moves occur for 15-30 mins.
                # Here we actively check and enforce the target temperature.
                if session.state in (DryingState.PREHEATING, DryingState.DRYING):
                    bed_target_dropped = telemetry.connected and (telemetry.bed_target < (session.target_bed_temp - 1.0))
                    periodic_keepalive_due = (now - last_keepalive_time) >= 60.0

                    if bed_target_dropped or periodic_keepalive_due:
                        if bed_target_dropped:
                            session.thermal_watchdog_triggers += 1
                            logger.warning(
                                f"⚠️ Thermal Watchdog: Firmware shut down/reduced bed target due to idle timeout "
                                f"(Target read: {telemetry.bed_target}°C vs Expected: {session.target_bed_temp}°C). "
                                f"Restoring target temperature immediately! (Trigger #{session.thermal_watchdog_triggers})"
                            )
                        with contextlib.suppress(Exception):
                            await self.client.set_temperatures(bed=session.target_bed_temp, nozzle=0.0)
                            if session.fan_box_speed > 0 and telemetry.box_fan != session.fan_box_speed:
                                await self.client.set_fans(box_fan=session.fan_box_speed)
                        last_keepalive_time = now
                        self._notify()

                    # Temperature drop anomaly check during DRYING phase
                    if session.state == DryingState.DRYING and telemetry.connected:
                        if telemetry.bed_temp < (session.target_bed_temp - 6.0):
                            if not session.temp_anomaly_detected:
                                session.temp_anomaly_detected = True
                                logger.warning(
                                    f"⚠️ Thermal Alert: Bed temperature dropped to {telemetry.bed_temp}°C "
                                    f"(Target: {session.target_bed_temp}°C). Enforcing heat..."
                                )
                                self._notify()
                            # Re-send heating command
                            with contextlib.suppress(Exception):
                                await self.client.set_temperatures(bed=session.target_bed_temp, nozzle=0.0)
                        elif telemetry.bed_temp >= (session.target_bed_temp - 2.5):
                            if session.temp_anomaly_detected:
                                session.temp_anomaly_detected = False
                                logger.info(f"✅ Bed temperature stabilized ({telemetry.bed_temp}°C).")
                                self._notify()

                # --- 2. State Machine Transitions ---
                if session.state == DryingState.PREHEATING:
                    # If bed temp reaches within 2°C of target, switch state to DRYING
                    if telemetry.bed_temp >= (session.target_bed_temp - 2.0):
                        logger.info(f"Target bed temperature reached ({telemetry.bed_temp}°C). Entering DRYING phase.")
                        session.state = DryingState.DRYING
                        self._notify()

                elif session.state == DryingState.DRYING:
                    # Check if timer completed
                    if session.elapsed_seconds() >= session.duration_seconds:
                        await self.complete_cycle()

                    # Check for mid-cycle spool flip reminder (at 50% elapsed)
                    elif not session.flip_spool_reminded:
                        halfway = session.duration_seconds / 2.0
                        if session.elapsed_seconds() >= halfway:
                            session.flip_spool_reminded = True
                            logger.info("Mid-cycle reached! Spool flip reminder triggered.")
                            self._notify()

                elif session.state in (DryingState.COMPLETED, DryingState.CANCELLED):
                    # Cooldown phase: monitor bed temperature until it drops below 42°C
                    if not session.cooldown_completed and telemetry.bed_temp <= 42.0:
                        session.cooldown_completed = True
                        logger.info("Bed cooled down to safe temperature (<42°C).")
                        # Turn off fans once cool
                        with contextlib.suppress(Exception):
                            await self.client.set_fans(box_fan=0, auxiliary_fan=0, model_fan=0)
                        self._notify()

            except asyncio.CancelledError:
                break
            except Exception as ex:
                logger.error(f"Error in cycle monitor tick: {ex}")

    def _record_session(self) -> None:
        """Persist session in history JSON."""
        record = self.current_session.to_dict()
        record["recorded_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.history_records.insert(0, record)
        # Keep latest 50 records
        self.history_records = self.history_records[:50]
        try:
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(self.history_records, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Could not save history to {HISTORY_FILE}: {e}")

    def clear_history(self) -> None:
        """Clear all historical drying session records."""
        self.history_records.clear()
        try:
            if HISTORY_FILE.exists():
                HISTORY_FILE.unlink()
            logger.info("Drying history cleared successfully.")
        except Exception as e:
            logger.warning(f"Could not remove {HISTORY_FILE}: {e}")

    def export_thermal_csv(self) -> str:
        """Export all recorded temperature data points as CSV text."""
        lines = ["Date_Time,Timestamp,Bed_Actual_C,Bed_Target_C,Chamber_C,Nozzle_C"]
        for pt in self.temp_history:
            dt_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(pt.get("timestamp", time.time())))
            ts = pt.get("timestamp", 0)
            bed = pt.get("bed", 0.0)
            bed_tgt = pt.get("bed_target", 0.0)
            box = pt.get("chamber", 0.0)
            noz = pt.get("nozzle", 0.0)
            lines.append(f"{dt_str},{ts},{bed:.1f},{bed_tgt:.1f},{box:.1f},{noz:.1f}")
        return "\n".join(lines)

    def _load_history(self) -> list[dict[str, Any]]:
        """Load past sessions from JSON file."""
        if not HISTORY_FILE.exists():
            return []
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load history: {e}")
            return []
