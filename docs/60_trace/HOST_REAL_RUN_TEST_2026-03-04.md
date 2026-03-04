受众：PROJECT
标题：Host 端真实运行测试报告（2026-03-04）

## 1. 测试目标
- 拉起 Host 端全链路服务（Neo4j + Chroma + Host API）。
- 执行真实健康与就绪验证。
- 将结果持久化为可追溯工件。

## 2. 执行信息
- 执行时间：2026-03-04 22:10:07 +08:00
- 分支：codex
- 提交：0c42f97
- 当前 .env 是否为 Host-only：是（EDGE_* 数量：0）

## 3. 服务拉起结果
- Host PID：42228
- Neo4j PID：41924
- Chroma PID：396
- 监听端口：18080 / 7687 / 8000（均处于 LISTEN）

## 4. HTTP 验证
- GET /health：200（ok=True）
- GET /ready：200（ok=True）
- GET /obs/metrics：FAIL
- GET /health/mcp：FAIL
- GET /health/canon_sync：FAIL

## 5. 运行时探针结果
- check_external_env.ready：False
- Neo4j 探针：down（ok=False）
- Chroma 探针：up（ok=True）
- env_readiness_report.readiness_status：FAILED
- env_readiness_report.reason_code：INFRA_NODE_MISSING_SERVICE

## 6. 结论
- Host 进程与本地依赖已真实启动并可访问 /health。
- 当前环境未达到 READY，主要受以下因素影响：
  - Neo4j 鉴权失败（Unauthorized）
  - env readiness 缺少节点清单导致 INFRA_NODE_MISSING_SERVICE
  - /obs/metrics、/health/mcp、/health/canon_sync 端点返回 404

## 7. 持久化工件
- 说明：`*.json` 英文字段用于机器解析；已新增中文结构化摘要文件。
- [host_real_run_summary_2026-03-04.zh-CN.json](D:/Nastume_Ai/runtime/reports/host_real_run_summary_2026-03-04.zh-CN.json)
- [host_real_run_summary_2026-03-04.json](D:/Nastume_Ai/runtime/reports/host_real_run_summary_2026-03-04.json)
- [host_http_checks_2026-03-04.json](D:/Nastume_Ai/runtime/reports/host_http_checks_2026-03-04.json)
- [check_external_env_host_real_run_2026-03-04.json](D:/Nastume_Ai/runtime/reports/check_external_env_host_real_run_2026-03-04.json)
- [env_readiness_report_host_real_run_2026-03-04.json](D:/Nastume_Ai/runtime/reports/env_readiness_report_host_real_run_2026-03-04.json)
