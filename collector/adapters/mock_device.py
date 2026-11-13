"""模拟设备:生成温度 / 振动 / 主轴转速 / 产量计数,并按小概率注入异常,便于演示告警链路。"""
from __future__ import annotations

import random

from collector.adapters.base import BaseAdapter


class MockDeviceAdapter(BaseAdapter):
    METRICS = ("temperature", "vibration", "spindle_speed", "output_count")

    def __init__(self, device):
        super().__init__(device)
        seed = sum(ord(ch) for ch in device.id)
        self._rng = random.Random(seed)
        self._temp_base = 68 + (seed % 8)  # 各设备温度基准略有差异
        self._temp = float(self._temp_base)
        self._output = self._rng.randint(1000, 5000)

    def read(self) -> dict[str, float]:
        rng = self._rng

        # 温度:随机游走 + 小概率高温尖峰(触发 WARN / CRIT 告警)
        self._temp += rng.uniform(-1.5, 1.5)
        self._temp = max(self._temp_base - 10, min(self._temp, self._temp_base + 6))
        temperature = self._temp + rng.uniform(0, 2)
        if rng.random() < 0.03:
            temperature += rng.uniform(18, 30)

        # 振动:基值附近波动 + 小概率突增
        vibration = 4.2 + rng.uniform(-0.6, 0.6)
        if rng.random() < 0.02:
            vibration += rng.uniform(3.0, 5.5)

        # 主轴转速:额定附近,小概率骤降(触发 INFO)
        spindle_speed = 8000 + rng.uniform(-250, 250)
        if rng.random() < 0.015:
            spindle_speed = rng.uniform(4300, 4900)

        # 产量计数:单调递增
        self._output += rng.randint(0, 3)

        return {
            "temperature": round(temperature, 2),
            "vibration": round(vibration, 2),
            "spindle_speed": round(spindle_speed, 1),
            "output_count": float(self._output),
        }
