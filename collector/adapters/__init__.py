"""设备适配器注册表:config.toml 中 device.type 对应这里的键。

协议适配器按需懒加载:只有真正用到某协议时才导入其依赖,
未安装对应依赖(或配置了不存在的协议)不会影响其他设备采集。
"""
from __future__ import annotations

from typing import Callable

from collector.adapters.base import BaseAdapter
from collector.adapters.mock_device import MockDeviceAdapter
from common.config import DeviceConfig


def _build_modbus(device: DeviceConfig) -> BaseAdapter:
    from collector.adapters.modbus_device import ModbusAdapter

    return ModbusAdapter(device)


def _build_opcua(device: DeviceConfig) -> BaseAdapter:
    from collector.adapters.opcua_device import OpcUaAdapter

    return OpcUaAdapter(device)


def _build_http(device: DeviceConfig) -> BaseAdapter:
    from collector.adapters.http_device import HttpAdapter

    return HttpAdapter(device)


_FACTORIES: dict[str, Callable[[DeviceConfig], BaseAdapter]] = {
    "mock": MockDeviceAdapter,
    "modbus": _build_modbus,
    "opcua": _build_opcua,
    "http": _build_http,
}


def build_adapter(device: DeviceConfig) -> BaseAdapter:
    factory = _FACTORIES.get(device.type)
    if factory is None:
        raise ValueError(f"未知设备类型: {device.type}(设备 {device.id})")
    return factory(device)
