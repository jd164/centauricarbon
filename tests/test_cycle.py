"""Unit tests for cycle manager state and presets."""

import pytest
from centauri_dryer.client import CentauriClient
from centauri_dryer.cycle_manager import DryingCycleError, DryingCycleManager
from centauri_dryer.models import DryingState, Telemetry


@pytest.mark.asyncio
async def test_cycle_presets_exist():
    client = CentauriClient()
    mgr = DryingCycleManager(client)
    assert "petg" in mgr.presets
    assert "pla" in mgr.presets
    assert "asa" in mgr.presets
    assert mgr.presets["petg"].target_bed_temp == 65.0
    assert mgr.presets["petg"].duration_hours == 5.0


@pytest.mark.asyncio
async def test_printer_busy_safety():
    client = CentauriClient()
    # Simulate printer currently printing a 3D part
    client._latest_telemetry = Telemetry(connected=True, print_status=13)
    mgr = DryingCycleManager(client)

    with pytest.raises(DryingCycleError, match="The printer is currently printing"):
        await mgr.start_cycle("petg")


@pytest.mark.asyncio
async def test_session_math():
    client = CentauriClient()
    client._latest_telemetry = Telemetry(connected=True, print_status=0)
    mgr = DryingCycleManager(client)

    session = mgr.current_session
    assert session.state == DryingState.IDLE
    assert session.progress_percent() == 0.0


@pytest.mark.asyncio
async def test_thermal_watchdog_detects_target_drop():
    client = CentauriClient()
    client._latest_telemetry = Telemetry(connected=True, print_status=0, bed_temp=65.0, bed_target=65.0)
    # Mock set_temperatures and set_fans so it doesn't need real WS
    sent_commands = []
    async def mock_set_temps(**kwargs):
        sent_commands.append(kwargs)
    async def mock_set_fans(**kwargs):
        pass
    client.set_temperatures = mock_set_temps
    client.set_fans = mock_set_fans

    mgr = DryingCycleManager(client)
    await mgr.start_cycle("petg")
    assert mgr.current_session.target_bed_temp == 65.0

    # Simulate printer idle timeout dropping bed target to 0°C
    client._latest_telemetry.bed_target = 0.0
    client._latest_telemetry.bed_temp = 58.0

    # Trigger one tick of monitor
    import asyncio
    task = asyncio.create_task(mgr._monitor_loop())
    await asyncio.sleep(1.2)
    task.cancel()

    # The watchdog should have detected the drop, incremented counter and re-sent 65.0
    assert mgr.current_session.thermal_watchdog_triggers >= 1
    assert any(cmd.get("bed") == 65.0 for cmd in sent_commands)

