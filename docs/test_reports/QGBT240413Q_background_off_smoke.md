# QGBT240413Q 背景任务关闭后短批次对照

- 原始留痕：`D:/Natsume_Ai/runtime/test_reports/QGBT240413Q_background_off_smoke.json`

- 汇总：{'total': 5, 'submit_failed': 0, 'timeout': 0, 'route_decided': 5, 'llm_used_true': 4, 'terminal_status': {'OK': 5}, 'gateway_status': {'failed': 4, 'DEGRADED': 1}, 'latency_avg_ms': 79280, 'latency_max_ms': 158764}

|#|case|route|target|source|llm_used|skills|terminal|gateway|latency_ms|
|---|---|---|---|---|---|---|---|---|---:|
|1|translate|delegate|translate_agent|rule|False|sub_agent:translate_agent|OK / -|failed|92450|
|2|code_fix|delegate|code_agent|llm|True|sub_agent:code_agent|OK / -|DEGRADED|158764|
|3|recent|recent_turn|-|override|True|sub_agent:intent_agent|OK / -|failed|67337|
|4|progress|chat|-|llm|True|-|OK / -|failed|12212|
|5|chat|skill|read_only_status|llm|True|read_only_status|OK / -|failed|65641|