"""Interactive Terminal / CLI Mode for Centauri Carbon Dryer."""

from __future__ import annotations

import asyncio
import os
import sys
import time

from centauri_dryer.client import CentauriClient
from centauri_dryer.cycle_manager import DryingCycleManager
from centauri_dryer.models import DryingState


def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def format_seconds(sec: int) -> str:
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


async def run_cli_dashboard(
    ip: str = "192.168.1.100",
    port: int = 3030,
    mainboard_id: str = "auto",
    auto_preset: str | None = None,
) -> None:
    """Run real-time terminal monitor and interactive menu."""
    client = CentauriClient(ip=ip, port=port, mainboard_id=mainboard_id)
    cycle_mgr = DryingCycleManager(client)
    sound_enabled = True
    last_beep_spool = False

    print(f"\n[Centauri Carbon] Connecting to ws://{ip}:{port}/websocket...")
    try:
        await client.connect(timeout=5.0)
    except Exception as e:
        print(f"[Centauri Carbon] Warning: Could not connect immediately ({e}). Retrying in background...")

    await cycle_mgr.start()

    if auto_preset:
        print(f"Starting automatically with preset: {auto_preset}...")
        try:
            await cycle_mgr.start_cycle(auto_preset)
        except Exception as e:
            print(f"Error starting cycle: {e}")

    # Background refresh task
    stop_event = asyncio.Event()

    async def _render_loop():
        nonlocal last_beep_spool
        while not stop_event.is_set():
            t = client.latest_telemetry
            s = cycle_mgr.current_session
            clear_screen()
            conn_str = "ONLINE" if t.connected else "OFFLINE"

            print("=======================================================================")
            print(f"   ELEGOO CENTAURI CARBON 1 — FILAMENT DRYING MANAGER (SDCP v3) ")
            print(f"   IP: {ip}:{port} [{conn_str}] | MainboardID: {mainboard_id[:16]}... ")
            print("=======================================================================")
            print("")
            print(f"  Heated Bed         : {t.bed_temp:5.1f} °C  (Target: {t.bed_target:5.1f} °C)")
            print(f"  Enclosed Chamber   : {t.chamber_temp:5.1f} °C")
            print(f"  Nozzle (Extruder)  : {t.nozzle_temp:5.1f} °C  (Target: {t.nozzle_target:5.1f} °C)")
            print(f"  Exhaust Fan (Box)  : {t.box_fan:3d} %    | Aux: {t.aux_fan:3d}% | Mod: {t.model_fan:3d}%")
            print(f"  Chamber Light      : {'ON' if t.light_on else 'OFF'}")
            print(f"  Thermal Watchdog   : {'ACTIVE (Anti-Timeout)' if s.state != DryingState.IDLE else 'STANDBY'} | Beep: {'ON' if sound_enabled else 'OFF'}")
            print("")
            print("-----------------------------------------------------------------------")
            print("  CYCLE STATUS:")

            state_label = s.state.value.upper()
            rem = format_seconds(s.remaining_seconds())
            elp = format_seconds(s.elapsed_seconds())
            pct = s.progress_percent()

            # Mini progress bar
            bar_len = 30
            filled = int((pct / 100.0) * bar_len)
            bar = "█" * filled + "░" * (bar_len - filled)

            print(f"  Material / Preset  : {s.preset_name} ({s.target_bed_temp} °C)")
            print(f"  Current Phase      : [{state_label}]")
            print(f"  Progress           : [{bar}] {pct:5.1f}%")
            print(f"  Remaining Time     : {rem}  (Elapsed: {elp})")

            if s.flip_spool_reminded and s.state in (DryingState.DRYING, DryingState.PREHEATING):
                print("\n  ⚠️  [SPOOL FLIP ALERT]: Half duration reached! Please flip the filament spool.")
                if sound_enabled and not last_beep_spool:
                    print("\a", end="", flush=True)
                    last_beep_spool = True

            if s.temp_anomaly_detected and s.state in (DryingState.DRYING, DryingState.PREHEATING):
                print(f"\n  ⚠️  [THERMAL ANOMALY]: Temperature drop detected! Watchdog restoring heat.")
                if sound_enabled:
                    print("\a", end="", flush=True)

            print("-----------------------------------------------------------------------")
            print("  ACTIONS / COMMANDS:")
            print("   [1] Start PETG (65 °C | 5h)        [4] Start ASA/ABS (75 °C | 4h)")
            print("   [2] Start PLA  (50 °C | 5h)        [5] Start Nylon   (80 °C | 8h)")
            print("   [3] Start TPU  (55 °C | 6h)        [m] Custom Mode")
            print("   [b] Toggle Sound (Beep)           [l] Toggle Chamber Light")
            print("   [p] Pause Cycle                   [r] Resume Cycle")
            print("   [c] Cancel / Cool Down            [q] Quit (Safe Shutdown)")
            print("=======================================================================")
            print("  Select an option and press Enter: ", end="", flush=True)

            await asyncio.sleep(1.5)

    render_task = asyncio.create_task(_render_loop())
    loop = asyncio.get_running_loop()

    # Simple console input loop in executor
    while not stop_event.is_set():
        try:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            cmd = line.strip().lower()

            if cmd == "1":
                await cycle_mgr.start_cycle("petg")
            elif cmd == "2":
                await cycle_mgr.start_cycle("pla")
            elif cmd == "3":
                await cycle_mgr.start_cycle("tpu")
            elif cmd == "4":
                await cycle_mgr.start_cycle("asa")
            elif cmd == "5":
                await cycle_mgr.start_cycle("nylon")
            elif cmd == "m":
                print("\nCustom Settings:")
                temp_str = await loop.run_in_executor(None, lambda: input("Bed temperature (30-85 °C): ").strip())
                hours_str = await loop.run_in_executor(None, lambda: input("Duration (0.5-24 hours): ").strip())
                try:
                    await cycle_mgr.start_cycle(
                        "custom",
                        custom_temp=float(temp_str),
                        custom_duration_hours=float(hours_str),
                    )
                except Exception as ex:
                    print(f"Error: {ex}")
                    await asyncio.sleep(2)
            elif cmd == "b":
                sound_enabled = not sound_enabled
            elif cmd == "p":
                await cycle_mgr.pause_cycle()
            elif cmd == "r":
                await cycle_mgr.resume_cycle()
            elif cmd == "c":
                await cycle_mgr.cancel_cycle()
            elif cmd == "l":
                await client.set_light(not client.latest_telemetry.light_on)
            elif cmd == "q":
                break
        except (KeyboardInterrupt, EOFError):
            print("\n[Ctrl+C Detected] Initiating Safe Shutdown...")
            break

    stop_event.set()
    render_task.cancel()

    print("\n[Safe Shutdown] Powering off 3D printer heaters to 0 °C...")
    try:
        await client.emergency_cooldown()
    except Exception as e:
        print(f"Shutdown warning: {e}")

    await cycle_mgr.stop()
    await client.close()
    print("✅ Printer safe (Bed at 0 °C). Application exited cleanly.")
