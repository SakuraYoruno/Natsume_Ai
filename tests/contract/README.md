受众：CODEX
标题：Contract Tests 说明

分组约定：
1. `flow/`：指针合法性与索引一致性。
2. `schema/`：输出 Schema 与必填字段。
3. `enums/`：拒绝码、状态枚举稳定性。
4. `audit/`：审计字段与串联键完整性。

说明：当前目录已包含可执行契约回归（按 `T-xx` 编号落地到 `tests/contract/test_*.py`），并与 `tests/fixtures/` 配对维护。
