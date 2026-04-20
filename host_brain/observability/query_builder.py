"""ObsQueryBuilder — Named SQL Query Loader（OPT-9 一期）.

背景：docs/35_quality/OPTIMIZATION_PLAN_20260420.md §13 / OPT-9 指出
`host_brain/app/obs_routes_*.py` 群内各个 route 都在重复拼 SQL、做聚合、映射
DTO，缺少统一抽象。本模块提供 OPT-9 的**一期骨架**：

  * 把常用查询从 route 代码里搬到 `host_brain/observability/queries/*.yaml`；
  * 统一的 ``NamedQueryRegistry`` 负责加载 + 参数类型校验；
  * 最薄 `ObsQueryBuilder.run_named(...)` 接入任意 sqlite3 Connection。

一期不追求替换所有 obs_routes（量太大），目标：

  1. 落地一个可演进的骨架；
  2. 提供 1 个迷你示例（session_summary）；
  3. 为二期把 obs_routes 中 SQL 迁入 queries/ 做好单测兜底。

二期（未来 PR）：把 `obs_routes_core.py` 中的典型 SELECT 语句逐步迁到
`queries/` 下，obs_routes 只做参数解析 + DTO 映射。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore[import-untyped]
except ImportError as exc:  # pragma: no cover - optional dep handled at import
    raise ImportError(
        "ObsQueryBuilder 依赖 PyYAML，请 pip install pyyaml；"
        "OPT-9 一期的 named query 文件以 YAML 存储。"
    ) from exc


DEFAULT_QUERIES_DIR = (
    Path(__file__).resolve().parent / "queries"
)

_ALLOWED_PARAM_TYPES: frozenset[str] = frozenset(
    {"int", "float", "str", "bool", "datetime_ms"}
)


# ---------------------------------------------------------------------------
# 内部类型
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NamedQuery:
    """一条 named query 的静态定义（来自 YAML）."""

    name: str
    sql: str
    params: Mapping[str, str]  # {param_name: type_tag}
    description: str = ""
    source_path: Path | None = field(default=None, compare=False)

    def validate_args(self, args: Mapping[str, Any]) -> None:
        missing = [p for p in self.params if p not in args]
        if missing:
            raise ValueError(
                f"named query '{self.name}' missing params: {missing}"
            )
        extra = [k for k in args if k not in self.params]
        if extra:
            raise ValueError(
                f"named query '{self.name}' got unexpected params: {extra}"
            )
        for pname, ptype in self.params.items():
            value = args[pname]
            _coerce_param_value(pname, ptype, value)  # raises on bad type


def _coerce_param_value(name: str, type_tag: str, value: Any) -> Any:
    """把用户传入的参数转成 sqlite3 可接受的类型；类型非法抛 ValueError。"""
    if type_tag not in _ALLOWED_PARAM_TYPES:
        raise ValueError(
            f"param '{name}' has unsupported type '{type_tag}'; "
            f"allowed: {sorted(_ALLOWED_PARAM_TYPES)}"
        )
    if value is None:
        return None
    if type_tag == "int":
        return int(value)
    if type_tag == "float":
        return float(value)
    if type_tag == "str":
        return str(value)
    if type_tag == "bool":
        return 1 if bool(value) else 0
    if type_tag == "datetime_ms":
        # 允许传 int / 可被 int() 的字符串；sqlite 里以毫秒存储
        return int(value)
    raise ValueError(f"unreachable type_tag={type_tag}")


# ---------------------------------------------------------------------------
# Named Query Registry
# ---------------------------------------------------------------------------


class NamedQueryRegistry:
    """从 YAML 目录加载 named queries，按 name 查询。

    示例 YAML 结构::

        name: session_summary
        description: 统计指定时间窗内各 session 的事件总数与平均延迟。
        sql: |
          SELECT session_id, COUNT(*) AS n, AVG(latency_ms) AS avg_latency
          FROM chat_events
          WHERE ts BETWEEN :start AND :end
          GROUP BY session_id
        params:
          start: datetime_ms
          end: datetime_ms
    """

    def __init__(self, *, queries_dir: Path | None = None) -> None:
        self._queries_dir = (
            queries_dir.resolve()
            if queries_dir is not None
            else DEFAULT_QUERIES_DIR
        )
        self._registry: dict[str, NamedQuery] = {}
        if self._queries_dir.is_dir():
            self._load_all()

    @property
    def queries_dir(self) -> Path:
        return self._queries_dir

    def names(self) -> list[str]:
        return sorted(self._registry)

    def get(self, name: str) -> NamedQuery:
        try:
            return self._registry[name]
        except KeyError as exc:
            raise KeyError(
                f"named query '{name}' not found in {self._queries_dir}. "
                f"Known: {sorted(self._registry)}"
            ) from exc

    # -----------------------------------------------------------------
    # 内部
    # -----------------------------------------------------------------

    def _load_all(self) -> None:
        for path in sorted(self._queries_dir.rglob("*.yaml")):
            self._load_one(path)

    def _load_one(self, path: Path) -> None:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ValueError(
                f"named query YAML 解析失败 path={path}: {exc}"
            ) from exc
        if not isinstance(data, Mapping):
            raise ValueError(
                f"named query YAML 顶层必须是 mapping, path={path}"
            )
        name = str(data.get("name") or "").strip()
        if not name:
            raise ValueError(f"named query 缺少 name 字段: {path}")
        sql = str(data.get("sql") or "").strip()
        if not sql:
            raise ValueError(f"named query '{name}' 缺少 sql 字段: {path}")
        raw_params = data.get("params") or {}
        if not isinstance(raw_params, Mapping):
            raise ValueError(
                f"named query '{name}' 的 params 必须是 mapping: {path}"
            )
        params: dict[str, str] = {}
        for pname, ptype in raw_params.items():
            ptype_tag = str(ptype or "").strip()
            if ptype_tag not in _ALLOWED_PARAM_TYPES:
                raise ValueError(
                    f"named query '{name}' 的 param '{pname}' 类型非法 "
                    f"'{ptype_tag}'；允许：{sorted(_ALLOWED_PARAM_TYPES)}"
                )
            params[str(pname)] = ptype_tag

        description = str(data.get("description") or "").strip()
        nq = NamedQuery(
            name=name,
            sql=sql,
            params=params,
            description=description,
            source_path=path,
        )
        if name in self._registry:
            prev = self._registry[name]
            raise ValueError(
                f"named query '{name}' 重复，先出现在 "
                f"{prev.source_path}，冲突于 {path}"
            )
        self._registry[name] = nq


# ---------------------------------------------------------------------------
# ObsQueryBuilder
# ---------------------------------------------------------------------------


class ObsQueryBuilder:
    """统一的 observability 查询入口.

    设计：
      * 不持有数据库，**调用方**传入 ``sqlite3.Connection``（或其兼容对象）；
      * 仅在 :meth:`run_named` 时做参数校验与 ``execute(sql, params)``；
      * 返回 ``list[dict]``，每行 key 为 SQL 选择列名；
      * 若调用方传入的 Connection 未设置 ``row_factory = sqlite3.Row``，
        则自动在执行时 opt-in 行字典化（不污染原 row_factory）。
    """

    def __init__(
        self,
        *,
        registry: NamedQueryRegistry | None = None,
    ) -> None:
        self._registry = registry or NamedQueryRegistry()

    @property
    def registry(self) -> NamedQueryRegistry:
        return self._registry

    def run_named(
        self,
        conn: sqlite3.Connection,
        name: str,
        **params: Any,
    ) -> list[dict[str, Any]]:
        """执行一条 named query 并以 list[dict] 返回结果."""
        nq = self._registry.get(name)
        nq.validate_args(params)
        coerced: dict[str, Any] = {
            pname: _coerce_param_value(pname, ptype_tag, params[pname])
            for pname, ptype_tag in nq.params.items()
        }
        cursor = conn.execute(nq.sql, coerced)
        columns = [c[0] for c in (cursor.description or ())]
        rows: list[dict[str, Any]] = []
        for raw in cursor.fetchall():
            if isinstance(raw, sqlite3.Row):
                rows.append({k: raw[k] for k in raw.keys()})
            elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
                rows.append(dict(zip(columns, raw, strict=False)))
            elif isinstance(raw, Mapping):
                rows.append(dict(raw))
            else:  # pragma: no cover - sqlite 返回形态若扩展再补
                rows.append({columns[0]: raw})
        return rows


__all__ = [
    "NamedQuery",
    "NamedQueryRegistry",
    "ObsQueryBuilder",
    "DEFAULT_QUERIES_DIR",
]
