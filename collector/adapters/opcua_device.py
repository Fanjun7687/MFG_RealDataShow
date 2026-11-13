"""OPC UA 设备适配器框架(基于 asyncua)。

config.toml 示例:
    [[devices]]
    id = "MACHINE-01"
    type = "opcua"

    [devices.options]
    url = "opc.tcp://192.168.1.20:4840"
    timeout = 5
    # username = "operator"
    # password = "******"

    [devices.options.points.temperature]
    node = "ns=2;s=Line1.Temperature"

    [devices.options.points.spindle_speed]
    node = "ns=2;s=Line1.SpindleSpeed"

实现说明:
- asyncua 是异步库,而采集循环是同步的:
  适配器用独立后台线程跑事件循环并保持长连接,read() 通过 run_coroutine_threadsafe 同步等待结果;
- 连接失败/读取失败 → 断开并在下一周期自动重连;
- 各点位独立容错,单点位失败不影响其他点位。

如需加密/证书连接,可在 _connect() 中扩展 client.set_security(...)。
"""
from __future__ import annotations

import asyncio
import threading

from collector.adapters.base import BaseAdapter
from common.config import DeviceConfig


class OpcUaAdapter(BaseAdapter):
    def __init__(self, device: DeviceConfig):
        super().__init__(device)
        opts = device.options
        self.url = str(opts.get("url", "opc.tcp://127.0.0.1:4840"))
        self.timeout = float(opts.get("timeout", 5))
        self.username = opts.get("username")
        self.password = opts.get("password")
        self.points: dict[str, dict] = opts.get("points", {})
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._client = None

    # ---------- 内部事件循环 ----------
    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._thread is not None and self._thread.is_alive():
            return self._loop  # type: ignore[return-value]
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop,
            name=f"opcua-{self.device.id}",
            daemon=True,
        )
        self._thread.start()
        return self._loop

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _call(self, coro):
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=self.timeout)

    # ---------- 异步实现 ----------
    async def _connect(self) -> None:
        try:
            from asyncua import Client
        except ImportError as exc:
            raise RuntimeError("未安装 asyncua,请执行: uv add asyncua") from exc

        client = Client(url=self.url, timeout=self.timeout)
        if self.username:
            client.set_user(str(self.username))
        if self.password:
            client.set_password(str(self.password))
        await client.connect()
        self._client = client

    async def _read_nodes(self) -> dict[str, float]:
        values: dict[str, float] = {}
        for metric, point in self.points.items():
            node_id = point.get("node")
            if not node_id:
                print(f"[OPCUA] {self.device.id}/{metric} 未配置 node")
                continue
            try:
                node = self._client.get_node(node_id)
                values[metric] = float(await node.read_value())
            except Exception as exc:
                print(f"[OPCUA] {self.device.id}/{metric} 读取失败: {exc}")
        return values

    # ---------- 同步接口 ----------
    def read(self) -> dict[str, float]:
        if not self.points:
            raise RuntimeError(f"设备 {self.device.id} 未配置任何点位(points)")
        self._ensure_loop()
        try:
            if self._client is None:
                self._call(self._connect())
            values = self._call(self._read_nodes())
            if not values:
                raise RuntimeError("所有点位读取失败")
            return values
        except Exception:
            self._reset()
            raise

    def _reset(self) -> None:
        """断开并丢弃客户端,下次 read() 自动重连。"""
        client = self._client
        self._client = None
        if client is not None and self._loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(client.disconnect(), self._loop)
            except Exception:
                pass

    def close(self) -> None:
        client = self._client
        self._client = None
        if client is not None and self._loop is not None and not self._loop.is_closed():
            try:
                self._call(client.disconnect())
            except Exception:
                pass
        if self._loop is not None:
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=3)
        if self._loop is not None:
            try:
                self._loop.close()
            except Exception:
                pass
        self._loop = None
        self._thread = None
