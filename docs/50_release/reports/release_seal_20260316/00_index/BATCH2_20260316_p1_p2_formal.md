受众：MASTER
标题：BATCH2：P1/P2 双端健康与 O/W 抽样（2026-03-16）

# BATCH2：P1/P2 双端健康与 O/W 抽样（2026-03-16）

**执行时间**：2026-03-16  
**环境**：Host 127.0.0.1:18080，Edge 192.168.1.101:17800

## P1 Host 核心与子系统

| ID | 项 | 结果 | 说明 |
|----|-----|------|------|
| P1-01 | Host /health、/ready | ✅ PASS | 均 200 |
| P1-02 | tools/bond/scheduler/host-scheduler | ✅ PASS | 四端点均 200 |

## P2 Edge 核心

| ID | 项 | 结果 | 说明 |
|----|-----|------|------|
| P2-01 | Edge /v1/edge/health | ✅ PASS | 200，success=true，status=ACCEPTED，核心服务 available |
| P2-02 | VTS 可用性 | ✅ PASS | Edge 端确认：vts_status=ok，vts 已监听 8001（pid 4272），vts_autostart_status=already_running |

## O-01 / W-01 / W-02 抽样

| ID | 项 | 结果 | 说明 |
|----|-----|------|------|
| O-01 | /obs/metrics、/metrics | ✅ PASS | 均 200 |
| W-01 | /panel | ✅ PASS | 301 重定向（符合预期） |
| W-02 | /api/ui/timeline、status-distribution | ✅ PASS | timeline 需参数时 422 符合合约；status-distribution 带 start_time/end_time 可 200 |

## WebUI 合约测试

| 项 | 结果 |
|----|------|
| tests/contract/test_web_ui_contract.py | 13 passed |

## 结论

- 阻断项：0。P2-02 已由 Edge 端 start_edge_side.ps1 输出确认通过（VTS ok、8001 已监听）。
- Edge 端 ASR/TTS 为 degraded（STARTUP_ASR_DEGRADED、STARTUP_TTS_DEGRADED），属已知态，不影响 P2-02 结论。
- 下一步：P3 Edge RPC、P4 主链路与场景矩阵、W 全项、人工/浏览器侧 W-04/W-05。
