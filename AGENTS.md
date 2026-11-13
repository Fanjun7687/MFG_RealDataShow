# AGENTS.md

本文件面向在此仓库工作的 AI 代理与开发者,记录项目架构、核心约束、常用命令、协议适配器信息与踩坑记录。修改代码前请先阅读「架构与核心约束」。

## 项目概述

工厂内网设备看板:显示远端设备实时数据、告警、历史趋势与班次报表。

- **技术栈**:Python 3.13(uv 管理)+ Gradio 6 + SQLite(WAL)+ plotly/pandas
- **协议库**:pymodbus(Modbus TCP)、asyncua(OPC UA)、httpx(HTTP/JSON 网关)
- **架构**:采集服务与看板是**两个独立进程**,仅通过 SQLite 文件耦合

## 架构与核心约束

```
┌──────────────────┐         ┌───────────────────────┐         ┌──────────────────────┐
│ 采集服务(进程1)   │  写入 → │ SQLite (WAL 模式)      │ ← 读取  │ 看板 Web(进程2)       │
│ 读设备→写 readings│         │ readings / alerts     │         │ Gradio 4 页面,只读库   │
│ 评规则→写 alerts  │         │ devices / alarm_rules │         │ 唯一写操作:告警确认     │
│ 写心跳 / 清理数据  │         │ collector_heartbeat   │         │ (低频小事务)           │
└──────────────────┘         └───────────────────────┘         └──────────────────────┘
```

**必须遵守的约束:**

1. 采集与看板不得合并为同一进程;采集崩溃不能影响看板,看板重启不能影响采集。
2. 看板只读采集数据;唯一允许的写操作是告警确认(`dashboard/queries.py::ack_alert`,低频小事务)。
3. SQLite 必须开启 WAL 模式(`common/db.py::ensure_db`),保证读写互不阻塞。
4. 单个设备/单个点位读取失败只跳过该设备本轮,不得中断采集主循环或影响其他设备。
5. 表结构与连接管理集中在 `common/`;看板所有 SQL 集中在 `dashboard/queries.py`(隔离层,便于将来迁移 PostgreSQL 或替换 UI)。
6. 时间统一存本地时间字符串 `"%Y-%m-%d %H:%M:%S"`(字典序即时间序,可直接用于 SQL 范围比较)。
7. 每查询使用独立短连接(避免 Gradio 多线程复用连接问题)。

## 常用命令

```powershell
# 需要安装/更新依赖时先设置代理(仅当前终端有效)
. .\set_proxy.ps1            # PowerShell;CMD 用: call set_proxy.cmd

# 安装依赖(会走代理)
uv add <package>

# 终端1:采集服务(常驻)
uv run python main.py collector

# 终端2:看板(默认 http://0.0.0.0:7860)
uv run python main.py dashboard

# 调试:跑 3 轮采集后退出
uv run python main.py collector --cycles 3 --interval 1

# 模块方式单独启动采集
uv run python -m collector

# 协议适配器自检(本地拉起模拟服务,不依赖真实设备)
uv run python scripts/adapter_check.py
uv run python scripts/adapter_check.py modbus    # 只测指定协议
```

## 目录结构

```
├── config.toml            # 静态配置:设备/规则/班次/间隔;末尾含三种协议注释示例
├── main.py                # 统一入口(collector / dashboard 子命令)
├── common/
│   ├── config.py          # 配置加载(dataclass + tomllib),DeviceConfig.options 存协议参数
│   ├── db.py              # SQLite 连接/建库/WAL/时间工具
│   └── schema.sql         # 表结构(幂等 CREATE IF NOT EXISTS)
├── collector/             # 采集服务(独立进程)
│   ├── adapters/
│   │   ├── base.py        # BaseAdapter:read() 抽象 + close() 生命周期
│   │   ├── mock_device.py # 模拟设备(演示,注入小概率异常)
│   │   ├── modbus_device.py  # Modbus TCP 适配器框架
│   │   ├── opcua_device.py   # OPC UA 适配器框架(异步桥接)
│   │   ├── http_device.py    # HTTP/JSON 网关适配器框架
│   │   └── __init__.py    # _FACTORIES 懒加载注册表 + build_adapter()
│   ├── service.py         # 采集主循环:写库 → 告警 → 心跳 → 清理 → close 适配器
│   ├── run.py             # 采集服务独立 CLI 入口
│   └── __main__.py
├── dashboard/             # 看板(独立进程,只读库)
│   ├── app.py             # Gradio Blocks 外壳;THEME 在 launch() 传入
│   ├── queries.py         # 全部 SQL 查询(隔离层)
│   └── pages/             # realtime / trend / shift_report / alerts
├── scripts/
│   └── adapter_check.py   # 协议自检(模拟 Modbus/OPC UA/HTTP 服务)
└── data/                  # 运行时生成:factory.db(WAL 附属文件),git 忽略
```

## 数据库表(`common/schema.sql`)

| 表 | 用途 |
|----|------|
| `devices` | 设备台账(采集服务首次启动从 config.toml 播种) |
| `readings` | 采集数据,窄表(device_id, metric, value, ts) |
| `alerts` | 告警(level: INFO/WARN/CRIT;ack/ack_by/ack_ts 为确认字段) |
| `alarm_rules` | 告警规则(device_id 支持 `*` 通配;UNIQUE 防重复播种) |
| `collector_heartbeat` | 采集心跳(单行,看板据此判断采集是否存活) |

- 索引:`idx_readings_dev_metric_ts`、`idx_readings_ts`、`idx_alerts_ts`、`idx_alerts_open`。
- 离线判定:`stale_threshold_seconds`;心跳异常判定:超过 2 倍阈值无心跳(见 `queries.get_collector_status`)。
- 班次报表按 `[[shifts]]` 时间窗实时聚合(SQL GROUP BY),不做预聚合表。

## 配置说明(config.toml)

- `[database]` 路径;`[collector]` 间隔/离线阈值/告警抑制窗口/保留天数;`[dashboard]` 监听地址/端口/刷新间隔/趋势点数
- `[[devices]]` 允许字段:`id/name/location/type/enabled/options`;`[devices.options]` 为协议专属参数,由各适配器自行解析
- `[[rules]]`:设备/指标/操作符(> < >= <=)/阈值/级别/消息
- 设备台账与规则在采集服务启动时幂等播种到数据库;改 config 后重启采集服务生效

## 设备协议适配器

### 注册与接口

- 接口:`BaseAdapter.read() -> dict[str, float]`(指标名 → 数值);`close()` 释放连接
- 注册:`collector/adapters/__init__.py::_FACTORIES`,**懒加载**(用到才导入依赖)
- 新增自定义协议:继承 `BaseAdapter` 实现 `read()`,加一个工厂函数注册即可,采集/告警/看板链路无需改动
- 统一特性:惰性连接 + 失败自动重连(下一周期);单点位容错;采集退出自动 `close()`

### Modbus TCP(`type = "modbus"`)

```toml
[devices.options]
host = "192.168.1.10"
port = 502
unit_id = 1
timeout = 3

[devices.options.points.temperature]
register = 100          # PDU 起始地址(0 基)
type = "holding"        # holding / input / coil / discrete
data_type = "int16"     # uint16 / int16 / uint32 / int32 / float32 / float64 / bool
scale = 0.1             # 最终值 = 原始值 × scale + offset
offset = 0
word_order = "big"      # 32/64 位字序:big(高字在前)/ little(字交换 CDAB)
```

功能码映射:holding→F03、input→F04、coil→F01、discrete→F02;寄存器宽度由 data_type 决定(1/2/4 个寄存器)。

### OPC UA(`type = "opcua"`)

```toml
[devices.options]
url = "opc.tcp://192.168.1.20:4840"
timeout = 5

[devices.options.points.temperature]
node = "ns=2;s=Line1.Temperature"
```

- asyncua 为异步库:适配器用**独立后台线程事件循环**保持长连接,`read()` 通过 `run_coroutine_threadsafe` 同步等待
- 用户名/密码:可选 `username` / `password`;加密/证书连接在 `_connect()` 扩展 `client.set_security(...)`
- `close()` 顺序:先 disconnect 再 stop loop 再 join 线程(顺序颠倒会卡住)

### HTTP / JSON 网关(`type = "http"`)

```toml
[devices.options]
base_url = "http://192.168.1.30:8080/api"
mode = "json_object"    # json_object:一次请求取全部点位;per_point:每点位单独请求
url = "/status"
timeout = 3
headers = { Authorization = "Bearer token" }

[devices.options.points.temperature]
field = "temperature"   # 点分字段路径,支持 a.b.c / 数组下标 a.0.b
```

- 点位读取失败只跳过该点;全部失败才抛异常

### 自检与验证基线

`scripts/adapter_check.py` 会本地拉起模拟 Modbus / OPC UA / HTTP 服务验证适配器(端口 15020/14840/18080,守护线程退出即止)。

最近一次自检结果(三协议全部 PASS):

```
[PASS] Modbus: temperature=75.5(int16×0.1) vibration=4.2(uint16×0.1)
              spindle_speed=8000(uint32) float_sample=1.25(float32) running=1.0(coil)
[PASS] OPC UA: temperature=76.5 vibration=4.3 spindle_speed=8100.0
[PASS] HTTP(json_object): 77.1 / 4.4 / 8200.0
[PASS] HTTP(per_point): 4.5
```

端到端已验证:临时 TOML(含 `[devices.options]`)→ 采集服务 → SQLite 写入 → 规则告警 → 退出清理。

## 开发踩坑记录

- **Gradio 6**:`theme` 必须传给 `demo.launch(theme=...)`,传给 `gr.Blocks(...)` 会告警;当前 `dashboard/app.py` 用 `THEME` 常量在 launch 传入。
- **pymodbus 3.16**:
  - 客户端 `read_*` 参数为 `device_id=`(无 `slave` 参数);适配器 `_call()` 做了新旧版本兼容
  - 写模拟服务器时 `ModbusSequentialDataBlock` 地址必须 **≥1**(内部按 address-1 建 SimData;地址 1 对应协议寄存器 0);旧 API 会打 deprecation 日志,测试中已屏蔽
- **控制台编码**:输出被管道重定向时中文可能乱码,调试命令可加 `$env:PYTHONIOENCODING="utf-8"`;真实终端不受影响。
- **路径**:项目路径含中文与括号,PowerShell 命令传参注意引号。
- **端口占用**:看板默认 7860;自检脚本用 15020/14840/18080,排查失败先查端口占用。

## 测试方式

1. 修改适配器 → `uv run python scripts/adapter_check.py`
2. 修改采集/告警 → `uv run python main.py collector --cycles 3`
3. 修改看板 → 启动后浏览器验收 4 个页面(实时状态自动刷新/趋势查询/班次报表导出/告警确认)
4. 修改查询层 → 用 python 直接调 `dashboard/queries.py` 的函数核对数据

## 演进方向

- `后期方案.txt`:长期计划将 UI 迁移到 FastAPI + Jinja2 + ECharts;现有表结构与 `queries.py` 可直接复用,替换 UI 层不影响采集/数据库
- 数据量增长后可迁移 PostgreSQL:改动范围仅 `common/db.py` 与 `dashboard/queries.py`
- 生产部署:Windows 任务计划/NSSM 分别注册采集与看板两个服务;Gradio 如需鉴权在 `launch(auth=...)` 配置
