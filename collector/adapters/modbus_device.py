"""Modbus TCP 设备适配器框架(基于 pymodbus)。

config.toml 示例:
    [[devices]]
    id = "PLC-01"
    name = "1号PLC产线"
    type = "modbus"

    [devices.options]
    host = "192.168.1.10"
    port = 502
    unit_id = 1
    timeout = 3

    [devices.options.points.temperature]
    register = 100          # PDU 起始地址(0 基)
    type = "holding"        # holding / input / coil / discrete
    data_type = "int16"     # uint16 / int16 / uint32 / int32 / float32 / float64 / bool
    scale = 0.1             # 最终值 = 原始值 * scale + offset
    offset = 0
    word_order = "big"      # 32/64 位数据的字序:big(高字在前, ABCD)/ little(字交换, CDAB)

连接策略:惰性连接;读取失败自动断开,下一周期重新连接(采集循环会记录并跳过本轮)。
"""
from __future__ import annotations

import struct

from collector.adapters.base import BaseAdapter
from common.config import DeviceConfig

# data_type -> 占用寄存器数
REGISTER_WIDTH = {
    "bool": 1,
    "uint16": 1,
    "int16": 1,
    "uint32": 2,
    "int32": 2,
    "float32": 2,
    "float64": 4,
}
# data_type -> struct 格式(大端;字序调整在解包前完成)
STRUCT_FMT = {
    "uint16": ">H",
    "int16": ">h",
    "uint32": ">I",
    "int32": ">i",
    "float32": ">f",
    "float64": ">d",
}
REGISTER_FN = {
    "holding": "read_holding_registers",
    "input": "read_input_registers",
}
BIT_FN = {
    "coil": "read_coils",
    "discrete": "read_discrete_inputs",
}


class ModbusAdapter(BaseAdapter):
    def __init__(self, device: DeviceConfig):
        super().__init__(device)
        opts = device.options
        self.host = str(opts.get("host", "127.0.0.1"))
        self.port = int(opts.get("port", 502))
        self.unit_id = int(opts.get("unit_id", 1))
        self.timeout = float(opts.get("timeout", 3))
        self.points: dict[str, dict] = opts.get("points", {})
        self._client = None

    # ---------- 连接管理 ----------
    def _ensure_client(self):
        if self._client is not None:
            return self._client
        try:
            from pymodbus.client import ModbusTcpClient
        except ImportError as exc:
            raise RuntimeError("未安装 pymodbus,请执行: uv add pymodbus") from exc

        client = ModbusTcpClient(self.host, port=self.port, timeout=self.timeout)
        if not client.connect():
            client.close()
            raise ConnectionError(f"Modbus 连接失败: {self.host}:{self.port}")
        self._client = client
        return client

    def _reset(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    # ---------- 读取 ----------
    def read(self) -> dict[str, float]:
        if not self.points:
            raise RuntimeError(f"设备 {self.device.id} 未配置任何点位(points)")
        client = self._ensure_client()
        values: dict[str, float] = {}
        try:
            for metric, point in self.points.items():
                try:
                    values[metric] = self._read_point(client, point)
                except Exception as exc:
                    print(f"[Modbus] {self.device.id}/{metric} 读取失败: {exc}")
            if not values:
                raise RuntimeError("所有点位读取失败")
            return values
        except Exception:
            self._reset()
            raise

    def _call(self, fn, address: int, count: int):
        """兼容 pymodbus 新旧版本的 unit_id 参数名(device_id / slave)。"""
        try:
            return fn(address, count=count, device_id=self.unit_id)
        except TypeError:
            return fn(address, count=count, slave=self.unit_id)

    def _read_point(self, client, point: dict) -> float:
        reg_type = str(point.get("type", "holding")).lower()
        data_type = str(point.get("data_type", "uint16")).lower()
        address = int(point["register"])
        scale = float(point.get("scale", 1))
        offset = float(point.get("offset", 0))

        # 位类型:线圈 / 离散输入
        if reg_type in BIT_FN:
            result = self._call(getattr(client, BIT_FN[reg_type]), address, 1)
            if result.isError():
                raise RuntimeError(str(result))
            raw = 1.0 if result.bits[0] else 0.0
            return raw * scale + offset

        # 寄存器类型
        width = REGISTER_WIDTH.get(data_type)
        if width is None:
            raise ValueError(f"不支持的 data_type: {data_type}")
        fn = getattr(client, REGISTER_FN.get(reg_type, REGISTER_FN["holding"]))
        result = self._call(fn, address, width)
        if result.isError():
            raise RuntimeError(str(result))

        registers = list(result.registers[:width])
        if data_type == "bool":
            return (1.0 if registers[0] else 0.0) * scale + offset

        # 字交换:AB CD -> CD AB(每两个寄存器交换一次)
        if str(point.get("word_order", "big")).lower() == "little" and width >= 2:
            registers = [r for i in range(0, width, 2) for r in (registers[i + 1], registers[i])]

        raw = struct.unpack(
            STRUCT_FMT[data_type], struct.pack(">" + "H" * width, *registers)
        )[0]
        return float(raw) * scale + offset

    # ---------- 清理 ----------
    def close(self) -> None:
        self._reset()
