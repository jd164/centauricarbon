"""FastAPI backend server and WebSocket telemetry streamer for Centauri Carbon Dryer."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from centauri_dryer.client import CentauriClient
from centauri_dryer.cycle_manager import DryingCycleError, DryingCycleManager
from centauri_dryer.models import DEFAULT_PRESETS

logger = logging.getLogger("centauri_dryer.server")
STATIC_DIR = Path(__file__).parent / "web"


class StartCycleRequest(BaseModel):
    preset_id: str
    custom_temp: float | None = Field(default=None, ge=30, le=100)
    custom_duration_hours: float | None = Field(default=None, ge=0.1, le=24.0)
    fan_box_speed: int | None = Field(default=None, ge=0, le=100)


class FanControlRequest(BaseModel):
    box_fan: int | None = Field(default=None, ge=0, le=100)
    auxiliary_fan: int | None = Field(default=None, ge=0, le=100)
    model_fan: int | None = Field(default=None, ge=0, le=100)


def create_app(
    ip: str = "192.168.1.100",
    port: int = 3030,
    mainboard_id: str = "auto",
) -> FastAPI:
    """Factory creating the FastAPI application with shared printer client and cycle manager."""

    client = CentauriClient(ip=ip, port=port, mainboard_id=mainboard_id)
    cycle_mgr = DryingCycleManager(client)
    active_websockets: set[WebSocket] = set()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info(f"Initializing Centauri Dryer Backend (Target: ws://{ip}:{port}/websocket)...")
        # Start connection to printer
        with contextlib.suppress(Exception):
            await client.connect()
        # Start cycle monitor loop
        await cycle_mgr.start()

        # Telemetry push loop to all connected WebSockets
        async def _ws_broadcast_loop():
            while True:
                await asyncio.sleep(1.0)
                if not active_websockets:
                    continue
                payload = {
                    "printer": {
                        "ip": client.ip,
                        "port": client.port,
                        "mainboard_id": client.mainboard_id,
                    },
                    "telemetry": client.latest_telemetry.to_dict(),
                    "session": cycle_mgr.current_session.to_dict(),
                    "temp_history": list(cycle_mgr.temp_history),
                }
                dead_ws = set()
                for ws in active_websockets:
                    try:
                        await ws.send_json(payload)
                    except Exception:
                        dead_ws.add(ws)
                active_websockets.difference_update(dead_ws)

        broadcast_task = asyncio.create_task(_ws_broadcast_loop(), name="ws-broadcaster")

        yield

        # Shutdown sequence
        logger.info("Shutting down Centauri Dryer Backend...")
        broadcast_task.cancel()
        await cycle_mgr.stop()
        await client.close()

    app = FastAPI(
        title="Centauri Carbon Dryer API",
        description="SDCP V3.0.0 Filament Drying Controller for Elegoo Centauri Carbon",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Serve static assets
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=FileResponse)
    async def get_index():
        index_file = STATIC_DIR / "index.html"
        if not index_file.exists():
            raise HTTPException(status_code=404, detail="Index file not found")
        return FileResponse(str(index_file))

    @app.get("/api/status")
    async def get_status():
        return {
            "printer": {
                "ip": client.ip,
                "port": client.port,
                "mainboard_id": client.mainboard_id,
            },
            "telemetry": client.latest_telemetry.to_dict(),
            "session": cycle_mgr.current_session.to_dict(),
        }

    @app.get("/api/presets")
    async def get_presets():
        return {k: v.__dict__ for k, v in DEFAULT_PRESETS.items()}

    @app.post("/api/cycle/start")
    async def start_cycle(req: StartCycleRequest):
        try:
            session = await cycle_mgr.start_cycle(
                preset_id=req.preset_id,
                custom_temp=req.custom_temp,
                custom_duration_hours=req.custom_duration_hours,
                fan_box_speed=req.fan_box_speed,
            )
            return {"status": "ok", "session": session.to_dict()}
        except DryingCycleError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Internal error: {e}")

    @app.post("/api/cycle/pause")
    async def pause_cycle():
        try:
            session = await cycle_mgr.pause_cycle()
            return {"status": "ok", "session": session.to_dict()}
        except DryingCycleError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/cycle/resume")
    async def resume_cycle():
        try:
            session = await cycle_mgr.resume_cycle()
            return {"status": "ok", "session": session.to_dict()}
        except DryingCycleError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/cycle/cancel")
    async def cancel_cycle():
        try:
            session = await cycle_mgr.cancel_cycle()
            return {"status": "ok", "session": session.to_dict()}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/light/toggle")
    async def toggle_light():
        current_state = client.latest_telemetry.light_on
        new_state = not current_state
        try:
            await client.set_light(new_state)
            client.latest_telemetry.light_on = new_state
            return {"status": "ok", "light_on": new_state}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to toggle light: {e}")

    @app.post("/api/fans")
    async def set_fans(req: FanControlRequest):
        try:
            await client.set_fans(
                box_fan=req.box_fan,
                auxiliary_fan=req.auxiliary_fan,
                model_fan=req.model_fan,
            )
            return {"status": "ok"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to set fans: {e}")

    @app.get("/api/history")
    async def get_history():
        return cycle_mgr.history_records

    @app.delete("/api/history")
    async def clear_history():
        cycle_mgr.clear_history()
        return {"status": "ok"}

    @app.get("/api/thermal/export")
    async def export_thermal():
        csv_data = cycle_mgr.export_thermal_csv()
        return Response(
            content=csv_data,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=centauri_thermal_evolution.csv"},
        )

    @app.post("/api/heaters/off")
    async def turn_off_heaters():
        await client.emergency_cooldown()
        if cycle_mgr.current_session.state != DryingState.IDLE:
            await cycle_mgr.cancel_cycle()
        return {"status": "ok"}

    @app.websocket("/ws/telemetry")
    async def websocket_telemetry(websocket: WebSocket):
        await websocket.accept()
        active_websockets.add(websocket)
        # Send initial frame immediately
        with contextlib.suppress(Exception):
            await websocket.send_json({
                "printer": {
                    "ip": client.ip,
                    "port": client.port,
                    "mainboard_id": client.mainboard_id,
                },
                "telemetry": client.latest_telemetry.to_dict(),
                "session": cycle_mgr.current_session.to_dict(),
                "temp_history": list(cycle_mgr.temp_history),
            })
        try:
            while True:
                # Keep socket alive / receive any ping
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            active_websockets.discard(websocket)

    return app
