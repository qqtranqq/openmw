"""Async TCP client for the OpenMW bridge socket."""

import asyncio
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class BridgeConnection:
    """Async TCP client that connects to OpenMW's bridge socket.

    Messages are JSON Lines (newline-delimited JSON) over TCP.
    """

    def __init__(self):
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._message_queue: asyncio.Queue[dict] = asyncio.Queue()
        self._read_task: Optional[asyncio.Task] = None
        self._connected = False
        self._cmd_counter = 0

    async def connect(self, host: str = "127.0.0.1", port: int = 21003, retries: int = 5, delay: float = 2.0):
        """Connect to the OpenMW bridge socket with retry logic."""
        for attempt in range(retries):
            try:
                self._reader, self._writer = await asyncio.open_connection(host, port)
                self._connected = True
                self._read_task = asyncio.create_task(self._read_loop())
                logger.info(f"Connected to {host}:{port}")
                return
            except (ConnectionRefusedError, OSError) as e:
                if attempt < retries - 1:
                    logger.warning(f"Connection attempt {attempt + 1} failed: {e}. Retrying in {delay}s...")
                    await asyncio.sleep(delay)
                    delay *= 1.5
                else:
                    raise ConnectionError(f"Failed to connect to {host}:{port} after {retries} attempts") from e

    async def disconnect(self):
        """Close the connection."""
        self._connected = False
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
            self._read_task = None
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None
        logger.info("Disconnected")

    async def send(self, msg: dict):
        """Send a JSON message over the bridge."""
        if not self._connected or not self._writer:
            raise ConnectionError("Not connected")
        line = json.dumps(msg, separators=(",", ":")) + "\n"
        self._writer.write(line.encode("utf-8"))
        await self._writer.drain()
        logger.debug(f"Sent: {msg.get('type', '?')} id={msg.get('id', '?')}")

    async def send_action(self, action: str, params: Optional[dict] = None, cmd_id: Optional[str] = None) -> str:
        """Send an action command. Returns the command ID."""
        if cmd_id is None:
            cmd_id = self._next_id()
        msg = {"type": "action", "id": cmd_id, "action": action}
        if params:
            msg["params"] = params
        await self.send(msg)
        return cmd_id

    async def recv(self, timeout: Optional[float] = None) -> Optional[dict]:
        """Receive the next message from the queue."""
        try:
            if timeout is not None:
                return await asyncio.wait_for(self._message_queue.get(), timeout=timeout)
            else:
                return await self._message_queue.get()
        except asyncio.TimeoutError:
            return None

    async def recv_type(self, msg_type: str, timeout: float = 5.0) -> Optional[dict]:
        """Wait for a message of a specific type, re-queuing others."""
        deadline = asyncio.get_event_loop().time() + timeout
        stashed = []
        try:
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    return None
                msg = await self.recv(timeout=remaining)
                if msg is None:
                    return None
                if msg.get("type") == msg_type:
                    return msg
                stashed.append(msg)
        finally:
            # Put stashed messages back
            for m in stashed:
                await self._message_queue.put(m)

    async def recv_by_id(self, cmd_id: str, msg_types: Optional[list[str]] = None, timeout: float = 10.0) -> Optional[dict]:
        """Wait for a message with a specific ID, optionally filtering by type."""
        if msg_types is None:
            msg_types = ["action_result", "action_complete", "pong", "world_info"]
        deadline = asyncio.get_event_loop().time() + timeout
        stashed = []
        try:
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    return None
                msg = await self.recv(timeout=remaining)
                if msg is None:
                    return None
                if msg.get("id") == cmd_id and msg.get("type") in msg_types:
                    return msg
                stashed.append(msg)
        finally:
            for m in stashed:
                await self._message_queue.put(m)

    async def send_and_wait(self, action: str, params: Optional[dict] = None, timeout: float = 10.0) -> Optional[dict]:
        """Send an action and wait for its result."""
        cmd_id = await self.send_action(action, params)
        return await self.recv_by_id(cmd_id, timeout=timeout)

    async def ping(self, timeout: float = 3.0) -> bool:
        """Send a ping and wait for pong."""
        cmd_id = self._next_id()
        await self.send({"type": "ping", "id": cmd_id})
        result = await self.recv_by_id(cmd_id, msg_types=["pong"], timeout=timeout)
        return result is not None

    def is_connected(self) -> bool:
        return self._connected

    def _next_id(self) -> str:
        self._cmd_counter += 1
        return f"cmd_{self._cmd_counter:04d}"

    async def _read_loop(self):
        """Background task that reads from the socket and enqueues messages."""
        buffer = ""
        try:
            while self._connected and self._reader:
                data = await self._reader.read(65536)
                if not data:
                    logger.warning("Connection closed by server")
                    self._connected = False
                    break
                buffer += data.decode("utf-8")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                        await self._message_queue.put(msg)
                        logger.debug(f"Recv: {msg.get('type', '?')} id={msg.get('id', '?')}")
                    except json.JSONDecodeError as e:
                        logger.warning(f"Invalid JSON: {e}: {line[:100]}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Read loop error: {e}")
            self._connected = False

    async def drain_observations(self) -> Optional[dict]:
        """Drain all queued observations and return only the latest one."""
        latest = None
        drained = []
        while not self._message_queue.empty():
            try:
                msg = self._message_queue.get_nowait()
                if msg.get("type") == "observation":
                    latest = msg
                else:
                    drained.append(msg)
            except asyncio.QueueEmpty:
                break
        # Re-queue non-observation messages
        for m in drained:
            await self._message_queue.put(m)
        return latest
