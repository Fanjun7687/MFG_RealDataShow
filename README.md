# 工厂设备实时看板

基于 **Gradio + SQLite** 的工厂内网看板基础工程:采集服务与看板是两个**独立进程**，
通过 SQLite(WAL 模式)解耦——采集崩了不影响看板，看板重启不影响采集。

## Author : Jason.Fan 13515358798 (V同)

## 架构

```
┌─────────────────────┐            ┌────────────────────────┐            ┌─────────────────────────┐
│  采集服务(进程 1)    │   写入     │   SQLite(工厂.db)      │    读取     │   看板 Web(进程 2)       │
│  collector/         │ ─────────▶ │  WAL 模式              │ ◀───────── │   dashboard/            │
│  · 读设备适配器      │            │  · readings 采集数据    │            │   · Gradio Blocks       │
│  · 写 readings      │            │  · alerts 告警          │            │   · 只读库,唯一写:       │
│  · 按规则写 alerts   │            │  · devices / rules     │            │     告警确认(低频)        │
│  · 写心跳 / 清理     │            │  · collector_heartbeat │            │                         │
└─────────────────────┘            └────────────────────────┘            └─────────────────────────┘
```

- **采集服务**：定时读设备 → 批量写 `readings` → 按 `alarm_rules` 评估产生 `alerts`
  → 写心跳 → 按保留期清理旧数据。
- **看板**：只读数据库渲染页面；用 `collector_heartbeat` 判断采集进程是否存活
  (采集挂了看板仍可看历史数据，并显示"心跳中断")。
- 两个进程可独立启动/停止/重启，互不依赖。

## 快速开始

```powershell
# 首次:设置代理(仅在本终端有效,采集/看板本身不需要联网)
. .\set_proxy.ps1          # PowerShell;CMD 用: call set_proxy.cmd

# 终端 1:采集服务(常驻)
uv run python main.py collector

# 终端 2:看板(默认 http://0.0.0.0:7860)
uv run python main.py dashboard
```

调试采集(跑 3 轮后退出):

```powershell
uv run python main.py collector --cycles 3
```

也可以用模块方式单独启动采集:`uv run python -m collector`。

## 页面说明

| 页面 | 内容 |
|------|------|
| 📡 实时状态 | 采集服务心跳、各设备最新指标值、在线/离线状态，每 5 秒自动刷新 |
| 📈 历史趋势 | 按设备 + 指标 + 时间范围查看曲线，支持 30 分钟 ~ 7 天，可开自动刷新 |
| 📊 班次报表 | 按日期 + 班次聚合 avg/min/max 与产量增量，可导出 CSV |
| 🔔 告警 | 未确认统计、告警列表筛选、点击行确认告警、查看告警规则 |

## 配置(config.toml)

- `[database]`：SQLite 路径。
- `[collector]`：采集间隔、离线阈值、告警抑制窗口、数据保留天数。
- `[dashboard]`：监听地址/端口、刷新间隔、趋势图最大点数。
- `[[shifts]]`：班次时段(默认早/中/夜三班)。
- `[[devices]]`：设备台账(首次启动播种到数据库)。
- `[[rules]]`：告警规则，`device_id = "*"` 表示全部设备。

## 设备协议适配器(已内置框架)

三种工业协议适配器已开发完成,**配置即用、无需改代码**;协议库已加入依赖
(`pymodbus` / `asyncua` / `httpx`)。`config.toml` 末尾附有三种协议的注释示例,
取消注释并改成现场参数即可启用。

适配器共同特性:
- 惰性连接 + 失败自动重连(下一采集周期重新连接);
- 单设备/单点位容错:某台设备读取失败只跳过该设备本轮,不影响其他设备;
- 采集服务退出时自动 `close()` 释放连接。

### Modbus TCP(`type = "modbus"`)

```toml
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
register = 100
type = "holding"      # holding / input / coil / discrete
data_type = "int16"   # uint16 / int16 / uint32 / int32 / float32 / float64 / bool
scale = 0.1           # 最终值 = 原始值 × scale + offset
```

| 点位字段 | 说明 |
|---|---|
| `register` | PDU 起始地址(0 基) |
| `type` | 寄存器区:holding(保持寄存器/F03)、input(输入寄存器/F04)、coil(线圈/F01)、discrete(离散输入/F02) |
| `data_type` | 数据类型,决定寄存器宽度(uint16=1,uint32/float32=2,float64=4) |
| `scale` / `offset` | 工程量换算:`value * scale + offset` |
| `word_order` | 32/64 位字序:`big`(高字在前,ABCD,默认)/ `little`(字交换,CDAB) |

### OPC UA(`type = "opcua"`)

```toml
[[devices]]
id = "MACHINE-01"
name = "加工中心(OPC UA)"
type = "opcua"

[devices.options]
url = "opc.tcp://192.168.1.20:4840"
timeout = 5
# username = "operator"
# password = "******"

[devices.options.points.temperature]
node = "ns=2;s=Line1.Temperature"
```

异步客户端跑在适配器内部的后台事件循环线程中,保持长连接;需要证书/加密连接时,
可在 `collector/adapters/opcua_device.py` 的 `_connect()` 中扩展 `client.set_security(...)`。

### HTTP / JSON 网关(`type = "http"`)

```toml
[[devices]]
id = "GATEWAY-01"
name = "能管网关"
type = "http"

[devices.options]
base_url = "http://192.168.1.30:8080/api"
mode = "json_object"   # json_object:一次请求取全部点位;per_point:每个点位单独请求
url = "/status"
timeout = 3
# headers = { Authorization = "Bearer your-token" }

[devices.options.points.temperature]
field = "temperature"  # JSON 字段路径,支持 a.b.c、数组下标 a.0.b

[devices.options.points.vibration]
field = "data.vibration"
```

### 适配器自检

无需真实设备,脚本会本地拉起 Modbus / OPC UA / HTTP 三个模拟服务来验证适配器:

```powershell
uv run python scripts/adapter_check.py           # 测试全部协议
uv run python scripts/adapter_check.py modbus    # 只测指定协议
```

### 自定义协议

继承 `BaseAdapter` 实现 `read()`(返回 `{指标: 数值}`),在
`collector/adapters/__init__.py` 的 `_FACTORIES` 中注册即可,
采集、告警、看板链路无需任何改动。

## 运维要点

- **数据文件**:`data/factory.db`(含 WAL 附属文件),已被 `.gitignore` 排除;
  备份时拷贝 `factory.db` + `factory.db-wal` + `factory.db-shm`(停机后拷贝最稳妥)。
- **保留期**:默认 90 天，采集服务每小时清理一次;`retention_days <= 0` 关闭清理。
- **数据量增长后**:看板查询全部收敛在 `dashboard/queries.py`，
  将来如需迁移 PostgreSQL，只需改这一层，页面代码不用动。
- **开机自启**:可用 Windows 任务计划/NSSM 把两个命令注册为服务(采集与看板各一个)。
- **内网访问**:`host = "0.0.0.0"` 时车间大屏可访问 `http://<看板主机IP>:7860`;
  Gradio 无鉴权，如需账号保护可在 `demo.launch(auth=...)` 中配置。

## 目录结构

```
├── config.toml            # 静态配置:设备/规则/班次/间隔
├── main.py                # 统一入口(collector / dashboard 两个子命令)
├── common/                # 共享:配置加载、SQLite 连接、表结构
├── collector/             # 采集服务(独立进程)
│   ├── adapters/          # 协议适配器:mock / Modbus / OPC UA / HTTP
│   └── service.py         # 采集主循环:写库 → 告警 → 心跳 → 清理
├── scripts/               # 开发辅助:adapter_check.py(协议自检)
├── dashboard/             # 看板(独立进程,只读库)
│   ├── app.py             # Gradio Blocks 外壳
│   ├── queries.py         # 所有 SQL 查询(隔离层)
│   └── pages/             # 实时状态 / 历史趋势 / 班次报表 / 告警
└── data/                  # 运行时生成:factory.db
```
