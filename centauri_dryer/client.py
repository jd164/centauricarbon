"""Asynchronous WebSocket SDCP client for Elegoo Centauri Carbon."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from typing import Any, Optional

import websockets
from websockets.asyncio.client import ClientConnection

from centauri_dryer.models import Telemetry
from centauri_dryer.protocol import (
    Cmd,
    MessageType,
    ParsedMessage,
    build_fan_payload,
    build_light_payload,
    build_request_envelope,
    build_temp_payload,
    parse_sdcp_message,
)

logger = logging.getLogger("centauri_dryer.client")


class SDCPClientError(Exception):
    """Base exception for SDCP client errors."""


class ConnectionFailedError(SDCPClientError):
    """Raised when unable to connect to the printer."""


class RequestTimeoutError(SDCPClientError):
    """Raised when a request times out without Ack."""


class CentauriClient:
    """Async WebSocket client for Elegoo Centauri Carbon SDCP v3.0.0."""

    def __init__(
        self,
        ip: str = "192.168.1.100",
        port: int = 3030,
        mainboard_id: str = "auto",
        poll_interval: float = 2.0,
    ):
        self.ip = ip
        self.port = port
        self.mainboard_id = mainboard_id
        self.poll_interval = poll_interval
        self.ws_url = f"ws://{ip}:{port}/websocket"

        self._ws: ClientConnection | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._poller_task: asyncio.Task[None] | None = None
        self._reconnect_task: asyncio.Task[None] | None = None

        self._connected = False
        self._closed = False
        self._pending_requests: dict[str, asyncio.Future[ParsedMessage]] = {}
        self._latest_telemetry: Telemetry = Telemetry()
        self._telemetry_callbacks: list[Callable[[Telemetry], None]] = []

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ws is not None

    @property
    def latest_telemetry(self) -> Telemetry:
        return self._latest_telemetry

    def add_telemetry_listener(self, cb: Callable[[Telemetry], None]) -> None:
        """Register a callback invoked whenever new status telemetry arrives."""
        if cb not in self._telemetry_callbacks:
            self._telemetry_callbacks.append(cb)

    def remove_telemetry_listener(self, cb: Callable[[Telemetry], None]) -> None:
        if cb in self._telemetry_callbacks:
            self._telemetry_callbacks.remove(cb)

    async def connect(self, timeout: float = 6.0) -> None:
        """Establish WebSocket connection and launch reader and telemetry poller."""
        if self.is_connected:
            return

        logger.info(f"Connecting to Centauri Carbon at {self.ws_url}...")
        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(self.ws_url, max_size=None, ping_interval=20, ping_timeout=15),
                timeout=timeout,
            )
            self._connected = True
            logger.info("Connected to Centauri Carbon WebSocket.")

            # Launch background reader
            self._reader_task = asyncio.create_task(self._reader_loop(), name="sdcp-reader")

            # Launch periodic status poller (Cmd 0)
            self._poller_task = asyncio.create_task(self._poller_loop(), name="sdcp-poller")

            # Request initial status immediately
            await self.request_status()

        except Exception as exc:
            self._connected = False
            logger.warning(f"Connection failed to {self.ws_url}: {exc}")
            raise ConnectionFailedError(f"Could not connect to {self.ws_url}") from exc

    async def _reader_loop(self) -> None:
        """Listen continuously for incoming messages from the printer."""
        assert self._ws is not None
        try:
            async for raw_message in self._ws:
                try:
                    self._handle_incoming_frame(raw_message)
                except Exception as ex:
                    logger.debug(f"Error handling frame: {ex}")
        except websockets.ConnectionClosed as cc:
            logger.warning(f"Printer WebSocket closed: {cc}")
        except Exception as e:
            logger.error(f"Reader loop exception: {e}")
        finally:
            self._connected = False
            self._latest_telemetry.connected = False
            self._notify_listeners()

            # Fail any pending futures
            for fut in list(self._pending_requests.values()):
                if not fut.done():
                    fut.set_exception(ConnectionFailedError("WebSocket connection terminated"))
            self._pending_requests.clear()

            # Trigger reconnect if not explicitly closed
            if not self._closed:
                asyncio.create_task(self._reconnect_loop())

    def _handle_incoming_frame(self, raw: str | bytes) -> None:
        """Process a received SDCP frame."""
        parsed = parse_sdcp_message(raw)

        # Auto-discover MainboardID if unset or set to auto
        if parsed.mainboard_id and (not self.mainboard_id or self.mainboard_id.lower() == "auto"):
            self.mainboard_id = parsed.mainboard_id
            logger.info(f"Auto-discovered printer MainboardID: {self.mainboard_id}")

        # 1. Dispatch response to waiting caller
        if parsed.request_id and parsed.request_id in self._pending_requests:
            fut = self._pending_requests.pop(parsed.request_id, None)
            if fut and not fut.done():
                fut.set_result(parsed)

        # 2. Update status telemetry if status frame
        if parsed.status:
            self._latest_telemetry = Telemetry.from_status_dict(parsed.status, connected=True)
            self._notify_listeners()

    def _notify_listeners(self) -> None:
        for cb in self._telemetry_callbacks:
            try:
                cb(self._latest_telemetry)
            except Exception as e:
                logger.error(f"Error in telemetry callback: {e}")

    async def _poller_loop(self) -> None:
        """Periodically trigger Cmd 0 (GET_PRINTER_STATUS) to guarantee fresh telemetry."""
        while self.is_connected and not self._closed:
            try:
                await self.request_status()
            except Exception as e:
                logger.debug(f"Poller query failed: {e}")
            await asyncio.sleep(self.poll_interval)

    async def _reconnect_loop(self) -> None:
        """Automatic reconnect with backoff."""
        delay = 2.0
        while not self.is_connected and not self._closed:
            logger.info(f"Reconnecting to Centauri Carbon in {delay:.1f}s...")
            await asyncio.sleep(delay)
            try:
                await self.connect()
                logger.info("Reconnection successful!")
                return
            except Exception:
                delay = min(delay * 1.5, 15.0)

    async def send_command(
        self,
        cmd: int,
        data: dict[str, Any] | None = None,
        timeout: float = 6.0,
    ) -> ParsedMessage:
        """Send an SDCP request and wait for the correlated Ack response."""
        if not self.is_connected or self._ws is None:
            raise ConnectionFailedError("Cannot send command: not connected to printer")

        envelope = build_request_envelope(cmd, data, self.mainboard_id)
        request_id = envelope["Data"]["RequestID"]

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[ParsedMessage] = loop.create_future()
        self._pending_requests[request_id] = fut

        try:
            payload_str = json.dumps(envelope)
            await self._ws.send(payload_str)
            logger.debug(f"Sent Cmd {cmd} (ReqID {request_id})")
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError as err:
            raise RequestTimeoutError(f"Command {cmd} (ReqID {request_id}) timed out after {timeout}s") from err
        finally:
            self._pending_requests.pop(request_id, None)

    async def request_status(self) -> None:
        """Send Cmd 0 to request live status."""
        try:
            mb_id = self.mainboard_id if (self.mainboard_id and self.mainboard_id.lower() != "auto") else ""
            envelope = build_request_envelope(Cmd.GET_PRINTER_STATUS, {}, mb_id)
            if self._ws:
                await self._ws.send(json.dumps(envelope))
        except Exception as e:
            logger.debug(f"Failed to send GET_PRINTER_STATUS: {e}")

    async def set_temperatures(
        self,
        *,
        bed: float | None = None,
        nozzle: float | None = None,
        chamber: float | None = None,
    ) -> ParsedMessage:
        """Set heater targets using Cmd 403."""
        payload = build_temp_payload(bed=bed, nozzle=nozzle, chamber=chamber)
        logger.info(f"Setting temperatures: {payload}")
        return await self.send_command(Cmd.CHANGE_PRINT_PARAMS, payload)

    async def set_fans(
        self,
        *,
        box_fan: int | None = None,
        auxiliary_fan: int | None = None,
        model_fan: int | None = None,
    ) -> ParsedMessage:
        """Set fan speeds (0..100%) using Cmd 403."""
        payload = build_fan_payload(
            box_fan=box_fan,
            auxiliary_fan=auxiliary_fan,
            model_fan=model_fan,
        )
        logger.info(f"Setting fans: {payload}")
        return await self.send_command(Cmd.CHANGE_PRINT_PARAMS, payload)

    async def set_light(self, on: bool) -> ParsedMessage:
        """Toggle chamber lighting using Cmd 403."""
        payload = build_light_payload(on)
        logger.info(f"Setting chamber light: {'ON' if on else 'OFF'}")
        return await self.send_command(Cmd.CHANGE_PRINT_PARAMS, payload)

    async def emergency_cooldown(self) -> None:
        """Immediately command all heaters to 0°C and optionally vent chamber."""
        logger.warning("Triggering EMERGENCY COOLDOWN: setting bed and nozzle to 0°C")
        try:
            await self.set_temperatures(bed=0.0, nozzle=0.0)
        except Exception as e:
            logger.error(f"Error sending cooldown temperatures: {e}")

    async def close(self) -> None:
        """Cleanly terminate connection and tasks."""
        self._closed = True
        self._connected = False

        if self._poller_task and not self._poller_task.done():
            self._poller_task.cancel()
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()

        if self._ws:
            with contextlib.suppress(Exception):
                await self._ws.close()
        logger.info("CentauriClient closed.")
