"""协议适配器自检:本地拉起模拟服务,验证 Modbus / OPC UA / HTTP 适配器是否可用。

用法:
    uv run python scripts/adapter_check.py            # 测试全部协议
    uv run python scripts/adapter_check.py modbus     # 只测指定协议

说明:
- 全部使用 127.0.0.1 临时端口,不读写项目数据库,不影响正在运行的采集/看板;
- 模拟服务均为守护线程,脚本退出即结束。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import socket
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collector.adapters import build_adapter
from common.config import DeviceConfig

MODBUS_PORT = 15020
OPCUA_PORT = 14840
HTTP_PORT = 18080


def wait_port(port: int, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"端口 {port} 未就绪")


def assert_close(label: str, actual: dict, expected: dict, tol: float = 1e-4) -> None:
    problems = []
    for key, want in expected.items():
        got = actual.get(key)
        if got is None:
            problems.append(f"{key}: 缺失")
        elif abs(float(got) - float(want)) > tol:
            problems.append(f"{key}: 期望 {want}, 实际 {got}")
    if problems:
        raise AssertionError(f"{label} 校验失败: " + "; ".join(problems))
    print(f"[PASS] {label}: {actual}")


# ============================================================ Modbus 模拟服务
def start_modbus_server(port: int) -> None:
    from pymodbus.datastore import (
        ModbusDeviceContext,
        ModbusSequentialDataBlock,
        ModbusServerContext,
    )
    from pymodbus.server import StartTcpServer

    registers = [0] * 200
    registers[0] = 755                                   # int16 * 0.1 = 75.5
    registers[2] = 42                                    # uint16 * 0.1 = 4.2
    registers[10], registers[11] = 0x0000, 0x1F40        # uint32 = 8000
    hi, lo = struct.unpack(">HH", struct.pack(">f", 1.25))
    registers[20], registers[21] = hi, lo                # float32 = 1.25

    coils = [False] * 100
    coils[5] = True

    # 注意:pymodbus 3.16 数据块内部按 address-1 映射,数据块地址 1 对应协议寄存器 0
    store = ModbusDeviceContext(
        hr=ModbusSequentialDataBlock(1, registers),
        co=ModbusSequentialDataBlock(1, coils),
    )
    context = ModbusServerContext(devices={1: store}, single=False)

    def serve() -> None:
        StartTcpServer(context=context, address=("127.0.0.1", port))

    threading.Thread(target=serve, daemon=True, name="modbus-sim").start()


def run_modbus() -> None:
    start_modbus_server(MODBUS_PORT)
    wait_port(MODBUS_PORT)

    device = DeviceConfig(
        id="SIM-MODBUS",
        name="Modbus 模拟设备",
        type="modbus",
        options={
            "host": "127.0.0.1",
            "port": MODBUS_PORT,
            "unit_id": 1,
            "timeout": 3,
            "points": {
                "temperature": {"register": 0, "data_type": "int16", "scale": 0.1},
                "vibration": {"register": 2, "data_type": "uint16", "scale": 0.1},
                "spindle_speed": {"register": 10, "data_type": "uint32"},
                "float_sample": {"register": 20, "data_type": "float32"},
                "running": {"register": 5, "type": "coil", "data_type": "bool"},
            },
        },
    )
    adapter = build_adapter(device)
    try:
        values = adapter.read()
    finally:
        adapter.close()

    assert_close(
        "Modbus",
        values,
        {
            "temperature": 75.5,
            "vibration": 4.2,
            "spindle_speed": 8000.0,
            "float_sample": 1.25,
            "running": 1.0,
        },
    )


# ============================================================ OPC UA 模拟服务
def start_opcua_server(port: int) -> dict[str, str]:
    from asyncua import Server

    ready: dict[str, str] = {}
    started = threading.Event()

    async def serve() -> None:
        server = Server()
        await server.init()
        server.set_endpoint(f"opc.tcp://127.0.0.1:{port}")
        idx = await server.register_namespace("http://factory.sim")
        obj = await server.nodes.objects.add_object(idx, "Line1")
        temp = await obj.add_variable(idx, "Temperature", 76.5)
        vib = await obj.add_variable(idx, "Vibration", 4.3)
        speed = await obj.add_variable(idx, "SpindleSpeed", 8100.0)
        ready.update(
            temperature=temp.nodeid.to_string(),
            vibration=vib.nodeid.to_string(),
            spindle_speed=speed.nodeid.to_string(),
        )
        async with server:
            started.set()
            while True:
                await asyncio.sleep(1)

    def run() -> None:
        asyncio.run(serve())

    threading.Thread(target=run, daemon=True, name="opcua-sim").start()
    if not started.wait(20):
        raise TimeoutError("OPC UA 模拟服务未就绪")
    wait_port(port)
    return ready


def run_opcua() -> None:
    nodes = start_opcua_server(OPCUA_PORT)

    device = DeviceConfig(
        id="SIM-OPCUA",
        name="OPC UA 模拟设备",
        type="opcua",
        options={
            "url": f"opc.tcp://127.0.0.1:{OPCUA_PORT}",
            "timeout": 8,
            "points": {
                "temperature": {"node": nodes["temperature"]},
                "vibration": {"node": nodes["vibration"]},
                "spindle_speed": {"node": nodes["spindle_speed"]},
            },
        },
    )
    adapter = build_adapter(device)
    try:
        values = adapter.read()
    finally:
        adapter.close()

    assert_close(
        "OPC UA",
        values,
        {"temperature": 76.5, "vibration": 4.3, "spindle_speed": 8100.0},
    )


# ============================================================ HTTP 模拟服务
class _GatewayHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path == "/status":
            payload = {"temperature": 77.1, "data": {"vibration": 4.4, "speed": 8200}}
            status = 200
        elif path == "/vibration":
            payload = {"value": 4.5}
            status = 200
        else:
            payload = {"error": "not found"}
            status = 404
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # 静默访问日志
        pass


def start_http_server(port: int) -> None:
    server = HTTPServer(("127.0.0.1", port), _GatewayHandler)
    threading.Thread(target=server.serve_forever, daemon=True, name="http-sim").start()


def run_http() -> None:
    start_http_server(HTTP_PORT)
    wait_port(HTTP_PORT)
    base = f"http://127.0.0.1:{HTTP_PORT}"

    # 模式一:一次请求返回全部点位(json_object)
    device = DeviceConfig(
        id="SIM-HTTP",
        name="HTTP 网关模拟设备",
        type="http",
        options={
            "base_url": base,
            "mode": "json_object",
            "url": "/status",
            "timeout": 3,
            "points": {
                "temperature": {"field": "temperature"},
                "vibration": {"field": "data.vibration"},
                "spindle_speed": {"field": "data.speed"},
            },
        },
    )
    adapter = build_adapter(device)
    try:
        values = adapter.read()
    finally:
        adapter.close()
    assert_close(
        "HTTP(json_object)",
        values,
        {"temperature": 77.1, "vibration": 4.4, "spindle_speed": 8200.0},
    )

    # 模式二:每个点位单独请求(per_point)
    device2 = DeviceConfig(
        id="SIM-HTTP2",
        name="HTTP 网关模拟设备2",
        type="http",
        options={
            "base_url": base,
            "mode": "per_point",
            "timeout": 3,
            "points": {"vibration": {"url": "/vibration", "field": "value"}},
        },
    )
    adapter2 = build_adapter(device2)
    try:
        values2 = adapter2.read()
    finally:
        adapter2.close()
    assert_close("HTTP(per_point)", values2, {"vibration": 4.5})


# ============================================================ 入口
def main() -> None:
    logging.getLogger("pymodbus").setLevel(logging.ERROR)
    logging.getLogger("asyncua").setLevel(logging.ERROR)

    parser = argparse.ArgumentParser(description="协议适配器自检(本地模拟服务)")
    parser.add_argument(
        "protocols",
        nargs="*",
        choices=["modbus", "opcua", "http"],
        help="要测试的协议,默认全部",
    )
    args = parser.parse_args()

    runners = {"modbus": run_modbus, "opcua": run_opcua, "http": run_http}
    targets = args.protocols or list(runners)

    failed = []
    for name in targets:
        print(f"---- 测试 {name} ----")
        try:
            runners[name]()
        except Exception as exc:
            failed.append(name)
            print(f"[FAIL] {name}: {exc}")

    print("=" * 50)
    if failed:
        print(f"失败协议: {', '.join(failed)}")
        sys.exit(1)
    print(f"全部通过: {', '.join(targets)}")


if __name__ == "__main__":
    main()
