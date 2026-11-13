"""设备适配器抽象:接入真实设备时,按协议实现一个 Adapter 即可。

生命周期:
- 采集服务启动时创建适配器实例;
- 每次采集周期调用一次 read();
- 采集服务退出时调用 close() 释放连接。

已内置协议框架(配置即用,无需改代码):
- mock   :模拟数据(演示用)
- modbus :Modbus TCP(PLC / 变频器 / 仪表,基于 pymodbus)
- opcua  :OPC UA(基于 asyncua,同步 read 内部维护独立事件循环线程)
- http   :HTTP/JSON 设备网关(基于 httpx)

自定义协议只需继承 BaseAdapter、实现 read(),并在 adapters/__init__.py 注册。
"""
from __future__ import annotations

import abc

from common.config import DeviceConfig


class BaseAdapter(abc.ABC):
    """一次 read() 返回该设备当前的 {指标名: 数值}。"""

    def __init__(self, device: DeviceConfig):
        self.device = device

    @abc.abstractmethod
    def read(self) -> dict[str, float]:
        """读取设备当前各指标值;失败时抛异常,由采集循环捕获并跳过本轮。"""

    def close(self) -> None:
        """释放连接等资源(默认无操作;持有连接的适配器请覆盖)。"""
