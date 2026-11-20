# 水质平台 · 报文契约发布

固件升级前确认新报文能被分析程序读取、程序升级后仍能读旧归档的契约发布网页。
按设备型号登记**历代固件写入格式**（只追加）与**现用程序读取格式**（按程序号替换），
各至多 8 份。每次发布前用 **z3** 对「全部历代写入格式 × 全部现用程序」做**全量**兼容判定
（逻辑证明，非抽样试样例）：任一写入格式允许的全部报文必须被每个现用程序接受，
否则拒绝发布，并给出**合法反例报文**与**拒收程序**。

## 快速启动（Docker Compose）

```bash
docker compose up --build
```

- 操作入口（网页）：http://localhost:8000
- 健康检查：http://localhost:8000/api/health
- 数据保存在命名卷 `pgdata`（PostgreSQL），服务重启后已发布格式保留。

## 网页操作

1. 首页创建设备型号（如 `WQ-5000`），进入型号管理页。
2. 在「发布新格式」粘贴受限 JSON Schema：
   - 左栏**追加固件写入格式**（版本号自动递增，只追加）；
   - 右栏**发布/替换程序读取格式**（同一程序号覆盖旧版）。
3. 发布结果即时展示：成功，或拒绝 + 每个不兼容组合的反例报文与拒收程序；
   页面下方「发布日志」保留最近 50 条成功/拒绝/无效记录（含拒绝依据）。
4. 页面内置四个示例，点按钮即可填入编辑器：
   - **温度扩范围**：固件 v2 扩到 -40~100℃，直接发布会被旧程序（-10~50℃）拒收，
     反例如 `{"temp": -40, ...}`；先升级程序再发固件则通过。
   - **质控码缺省**：固件缺省 `qc`（或写 `null`），把 `qc` 列为 `required` 的程序会被拒，
     反例为不含 `qc` 的报文；程序改为可选并允许 `null` 后通过。
   - **顶层 anyOf**：程序同时接受多种报文形态。
   - **选列暴露复核**：安全契约删站号后通过；anyOf 异形契约勾选任一字段即给见证报文与唯一站号。

## 公开选列 · 站号暴露复核

给合作方分享水质明细前，需要确认**删掉站号字段后，剩余字段会不会反推出采样站**。
型号页「公开选列 · 站号暴露复核」卡片的操作：

1. 选一个**已发布的固件写入契约版本**，再选**站号字段**、勾选站号之外**拟公开的字段**（可全不选）；
2. 站号字段须满足：在固件的**每个分支都必填**、为**纯字符串枚举**（不允许 null/整数）、
   各分支枚举值**并集最多 8 个站号**，否则请求判无效（HTTP 422）；
3. 系统按所选字段裁剪报文（缺省字段不出现、显式 `null` 保留为 `null`，两者区别不丢），
   对契约允许的**全部合法报文**做 z3 全量判定，顶层 `anyOf` 按全部合法报文一并判定：
   - **选列通过**：每一种可能的公开报文都能由**至少两个不同站号**的合法原报文裁剪得到；
   - **选列不通过**：存在只能由唯一站号产生的公开报文，页面/API 给出一条这样的公开报文及
     对应站号，工程师据此改选字段后可直接复查；
   - **待核**：求解器超时或返回 unknown，未能完成判定，**不得视为通过**。

判定同样是逻辑证明而非抽样：受限语法下字段合法性只取决于字段自身，
「公开报文 P 能由站号 s 的合法原报文裁剪得到」等价于「存在某分支接受 P 的公开投影
且该分支站号枚举含 s」（其余必填字段总能在不改变投影的前提下补合法值），
因此一次无量词 SAT/UNSAT 查询即覆盖全部公开报文；SAT 见证另经独立纯 Python 判定器复核
（可达站号集合 + 补全出一条完整合法原报文）后才输出。

### 选列复核 API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/models/{name}/disclosure` | `{"version":1,"site_field":"site","public_fields":["temp"]}` |

响应 `200 {"ok":true,"status":"pass|risk|unknown", ...}`：`risk` 含
`witness`（公开报文）与 `witness_site`（唯一站号）；`unknown` 为待核；
请求不合法（站号字段不合规、字段不存在、版本不存在等）返回 `422`。
该接口为只读判定，不写库、不产生发布日志。

## 受限 JSON Schema 语法（其余一律拒绝）

- 平面对象：`{"type":"object","properties":{...},"required":[...],"additionalProperties":false}`
  （`required` 可省略，默认空；`additionalProperties` 必须显式为 `false`）
- 顶层 `anyOf`：`{"anyOf":[<平面对象>, ...]}`，且只能单独出现在顶层
- 整数上下界：`{"type":"integer","minimum":m,"maximum":M}`（上下界均可省略）
- 字符串/整数/空值枚举：`{"enum":[...]}`，可配 `"type"` 限定；枚举值只允许字符串、整数、`null`
- 空值类型：`{"type":"null"}`

嵌套对象、数组、`pattern`、`oneOf`/`allOf`/`not`、浮点/布尔值、`string` 无枚举等语法均被拒绝。

## 校验原理

对每对（写入格式 W，读取格式 R），把报文编码为 z3 变量（每个字段：是否出现、
值类型 null/int/string、整数值、字符串枚举下标），求解 `W 合法 ∧ R 拒收`：

- **UNSAT** ⇒ 不存在反例，`L(W) ⊆ L(R)` 成立——这是对全部报文的证明，不是抽样；
- **SAT** ⇒ 从模型还原一个反例报文，并用独立的纯 Python 判定器复核
  （确实满足 W、确实被 R 拒收）后随拒绝结果返回。

## 并发与事务

- 每次发布在事务内先对型号行加锁（PostgreSQL `SELECT ... FOR UPDATE`），
  再基于最新已提交状态做**全量交叉检查**后落库——并发升级被串行化，
  不会各自通过却留下不兼容组合；
- 检查失败整体回滚，**已发布格式不变**；拒绝/无效尝试另写发布日志留痕；
- 固件格式只追加（无修改/删除入口），程序格式按程序号替换；各至多 8 份。

## JSON API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/models` | 创建型号 `{"name": "WQ-5000"}` |
| GET | `/api/models/{name}` | 查看当前固件/程序格式 |
| GET | `/api/models/{name}/log` | 发布日志（含拒绝依据） |
| POST | `/api/models/{name}/firmware` | 追加固件格式 `{"schema": {...}}` |
| POST | `/api/models/{name}/programs` | 发布/替换程序格式 `{"program_id": "...", "schema": {...}}` |
| POST | `/api/models/{name}/disclosure` | 选列暴露复核（见上节） |

响应：成功 `200 {"ok": true, ...}`；不兼容 `409 {"ok": false, "problems": [{firmware_version, program_id, counterexample}]}`；语法/上限错误 `422`。

## 本地开发与自测

```bash
pip install -r app/requirements.txt httpx
python3 tests/test_units.py                 # 语法子集 + z3 判定单元自测
python3 tests/test_disclosure.py            # 选列暴露复核单元自测
cd app && DATABASE_URL=sqlite:////tmp/contracts.db uvicorn main:app --port 8000 &
python3 tests/selftest.py                   # 集成自测（示例/上限/并发/选列复核/页面）
```

`DATABASE_URL` 缺省为 SQLite（便于本地自测）；Compose 中注入 PostgreSQL 连接串。

## 目录结构

```
app/
  main.py         FastAPI 入口（HTML 页面 + JSON API）
  service.py      发布事务：加锁 → 全量检查 → 落库/回滚；选列复核只读入口
  solver.py       z3 编码与全量兼容判定（含反例复核）
  disclosure.py   选列站号暴露全量判定（通过/风险见证/待核 + 纯 Python 复核）
  schema_spec.py  受限 JSON Schema 子集解析与纯 Python 报文判定
  db.py           SQLAlchemy 模型（PostgreSQL JSONB / SQLite JSON）
  examples.py     温度扩范围、质控码缺省、anyOf、选列暴露示例
  templates/      页面模板
tests/
  test_units.py       单元自测
  test_disclosure.py  选列暴露复核单元自测
  selftest.py         集成自测
docker-compose.yml
```
