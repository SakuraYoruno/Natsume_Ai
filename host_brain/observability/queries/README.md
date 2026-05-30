受众：CODEX
标题：Observability Named Queries（OPT-9）

# Observability Named Queries（OPT-9）

本目录存放 `ObsQueryBuilder` 使用的 named query 定义文件，由
[`query_builder.py`](../query_builder.py) 的 `NamedQueryRegistry` 自动加载。

详细背景见 [docs/35_quality/OPTIMIZATION_PLAN_20260420.md](../../../docs/35_quality/OPTIMIZATION_PLAN_20260420.md) §13 / OPT-9。

---

## 文件命名与结构

- 每个 `*.yaml` 文件定义**一条** named query；
- 文件名 = `name` 字段 = 调用方使用的 query 键；
- 所有文件被 `NamedQueryRegistry._load_all()` 递归加载，启动时若发现
  **重复的 `name`** 或字段缺失会直接 `raise ValueError`，在启动期就能暴露问题。

一个最小合法文件的 schema：

```yaml
name: <snake_case_query_name>        # 必需，与文件名一致；
description: |                        # 必需；可多行，用于 PR review 与运维排障；
  说明该 query 的业务用途、数据源表、字段语义、典型调用端点。
sql: |                                # 必需，以 :param_name 作占位符；
  SELECT ...
  FROM <table>
  WHERE ts_ms BETWEEN :start AND :end
  ...
params:                               # 必需；类型标签列表；
  start: datetime_ms
  end:   datetime_ms
  limit: int
```

合法 param 类型（见 `_ALLOWED_PARAM_TYPES`）：

| 类型 | 说明 |
|------|------|
| `int` | 整数；`NamedQuery.validate_args` 会用 `int(value)` 强转 |
| `float` | 浮点数；用 `float(value)` 强转 |
| `str` | 字符串；用 `str(value)` 强转 |
| `bool` | 布尔值；转为 sqlite 友好的 `0` / `1` |
| `datetime_ms` | 毫秒时间戳整数 |

---

## 当前登记在册的 named query

| 文件 | 用途 | 数据源表 |
|------|------|----------|
| [session_summary.yaml](./session_summary.yaml) | 会话维度事件数与延迟统计（示例） | `chat_events`（演示用） |
| [governor_decisions.yaml](./governor_decisions.yaml) | Governor 裁决按 `decision` × `reason_code` 的计数 | `governor_log` |
| [skill_execution_stats.yaml](./skill_execution_stats.yaml) | 技能执行按 `skill_name` × `result_status` × `result_code` 的计数 | `skill_exec_log` |
| [error_summary.yaml](./error_summary.yaml) | 错误按 `stage` × `reason_code` 聚合（与 OPT-3 `soft_fail_metrics` 对照） | `error_log` |

---

## 写作与评审规范

1. **字段先查 schema**：所有 SQL 中引用的列名，必须来自
   [`host_brain/audit/ledger_schema.py`](../../audit/ledger_schema.py) 的 `CREATE TABLE` 定义，
   禁止根据经验猜测。
2. **禁止字符串拼接**：不得在 `sql` 中用 Python 格式化插值任何运行期值；
   所有动态值通过 `params` + `:param_name` 占位符传入，交由 sqlite3 参数绑定处理，
   避免注入。
3. **时间窗统一用毫秒时间戳**：表内 `ts_ms` 字段单位为毫秒；
   `params` 声明为 `datetime_ms`。
4. **`LIMIT` 必须可控**：所有 group-by 查询都要接受 `:limit` 参数，
   防止返回超大结果集压垮 obs 页面。
5. **低基数标签**：不得把 `session_id`、`request_id` 之类的高基数字段放进
   `GROUP BY` 后再 label 化上报；若要下钻具体会话，请新起 query 并显式
   通过参数过滤。
6. **幂等性**：named query 必须是**只读** SQL，禁止 `INSERT/UPDATE/DELETE`。
   若需要写入，请通过 audit ledger 的显式 API，而非 named query。

---

## 新增一条 named query 的 checklist

- [ ] 新建 `<name>.yaml`，`name` 字段与文件名一致；
- [ ] `description` 写清楚：业务用途 / 数据源表 / 字段语义 / 调用端点；
- [ ] `sql` 字段只用 `:param` 占位符，不做 Python 字符串拼接；
- [ ] 所引用的列名在 [`ledger_schema.py`](../../audit/ledger_schema.py) 能找到定义；
- [ ] `params` 里的类型在 `_ALLOWED_PARAM_TYPES` 集合内；
- [ ] 在 [`tests/unit/test_query_builder.py`](../../../tests/unit/test_query_builder.py)
      增加一条单测：用内存 sqlite 建最小 fixture 表，`run_named(...)` 断言返回结构。
- [ ] 若新 query 将被 `/obs/*` 端点消费，需要同步记录在该端点的文档里，
      并把散落在 route 里的 SQL 拼装代码删除（OPT-9 二期目标）。

---

## 长期目标

OPT-9 最终形态：`host_brain/app/obs_routes_*.py` 中**不再出现** `SELECT ... FROM`
字面量；所有 SQL 全部收敛到本目录，route 只做参数解析 + DTO 映射。

进度跟踪见 [OPTIMIZATION_PLAN_20260420.md §13](../../../docs/35_quality/OPTIMIZATION_PLAN_20260420.md)。
