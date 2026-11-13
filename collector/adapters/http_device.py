"""HTTP / JSON 设备适配器框架(基于 httpx,适配设备网关、SCADA 中间层等)。

config.toml 示例:
    [[devices]]
    id = "GATEWAY-01"
    type = "http"

    [devices.options]
    base_url = "http://192.168.1.30:8080/api"
    mode = "json_object"    # json_object:一次请求返回 {指标:值};per_point:每个点位单独请求
    url = "/status"
    timeout = 3
    # headers = { Authorization = "Bearer your-token" }

    [devices.options.points.temperature]
    field = "temperature"   # JSON 字段路径,支持 a.b.c 与数组下标 a.0.b

    [devices.options.points.vibration]
    field = "data.vibration"

per_point 模式下可为点位单独指定接口:
    [devices.options.points.spindle_speed]
    url = "/speed"
    field = "value"

各点位独立容错:单字段缺失/非数值只跳过该点位;全部失败才抛出异常。
"""
from __future__ import annotations

from collector.adapters.base import BaseAdapter
from common.config import DeviceConfig


def _pick(data, path: str):
    """按点分路径从 JSON 中取值,如 "data.0.value" / "temperature"。"""
    if not path:
        return data
    current = data
    for part in path.split("."):
        if isinstance(current, list):
            current = current[int(part)]
        elif isinstance(current, dict):
            current = current[part]
        else:
            raise KeyError(f"路径 {path} 无法从 {type(current).__name__} 中取值")
    return current


class HttpAdapter(BaseAdapter):
    def __init__(self, device: DeviceConfig):
        super().__init__(device)
        opts = device.options
        self.base_url = str(opts.get("base_url", "")).rstrip("/")
        self.mode = str(opts.get("mode", "json_object")).lower()
        self.default_url = str(opts.get("url", "/status"))
        self.timeout = float(opts.get("timeout", 3))
        self.headers = {str(k): str(v) for k, v in dict(opts.get("headers", {})).items()}
        self.points: dict[str, dict] = opts.get("points", {})
        self._client = None

    # ---------- 连接管理 ----------
    def _ensure_client(self):
        if self._client is None:
            try:
                import httpx
            except ImportError as exc:
                raise RuntimeError("未安装 httpx,请执行: uv add httpx") from exc
            self._client = httpx.Client(timeout=self.timeout, headers=self.headers)
        return self._client

    def _full_url(self, url: str) -> str:
        if url.startswith(("http://", "https://")):
            return url
        if self.base_url:
            return f"{self.base_url}/{url.lstrip('/')}"
        return url

    def _get_json(self, url: str):
        client = self._ensure_client()
        response = client.get(self._full_url(url))
        response.raise_for_status()
        return response.json()

    # ---------- 读取 ----------
    def read(self) -> dict[str, float]:
        if not self.points:
            raise RuntimeError(f"设备 {self.device.id} 未配置任何点位(points)")
        values: dict[str, float] = {}
        try:
            if self.mode == "per_point":
                for metric, point in self.points.items():
                    url = str(point.get("url", self.default_url))
                    field = str(point.get("field", metric))
                    try:
                        values[metric] = float(_pick(self._get_json(url), field))
                    except Exception as exc:
                        print(f"[HTTP] {self.device.id}/{metric} 读取失败: {exc}")
            else:
                payload = self._get_json(self.default_url)
                for metric, point in self.points.items():
                    field = str(point.get("field", metric))
                    try:
                        values[metric] = float(_pick(payload, field))
                    except Exception as exc:
                        print(f"[HTTP] {self.device.id}/{metric} 字段解析失败: {exc}")
            if not values:
                raise RuntimeError("所有点位读取失败")
            return values
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
