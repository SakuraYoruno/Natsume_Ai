# QGBT240414D OneBot(qq) 批量回放最终态

- 原始留痕：`D:/Natsume_Ai/runtime/test_reports/QGBT240414D_gateway_skill_exec_batch_final.json`

- 汇总：`{'total': 12, 'submit_failed': 2, 'timeout': 0, 'route_decided_present': 5, 'llm_route_used_true': 5, 'skill_exec_present': 8, 'terminal_status': {'OK': 10}, 'gateway_status': {'degraded': 3}, 'route_source_dist': {'llm': 4, 'override': 1}, 'route_type_dist': {'chat': 2, 'skill': 2, 'delegate': 1}, 'skill_dist': {'sub_agent:translate_agent': 3, 'memory_store': 1, 'sub_agent:code_agent': 2, 'codex_task': 1, 'diary_writer': 1}, 'latency_ms_avg': 104213, 'latency_ms_max': 104909}`

|#|case|route|target|source|llm_used|skills|terminal|gateway|latency_ms|
|---|---|---|---|---|---|---|---|---|---:|
|1|chat|chat|-|llm|True|-|OK / -|DEGRADED|104909|
|2|translate1|-|-|-|-|sub_agent:translate_agent|OK / -|-|-|
|3|translate2|-|-|-|-|sub_agent:translate_agent|OK / -|-|-|
|4|memory|skill|memory_store|llm|True|memory_store|OK / -|DEGRADED|104201|
|5|codefix|-|-|-|-|sub_agent:code_agent|OK / -|-|-|
|6|codegen|-|-|-|-|codex_task|OK / -|-|-|
|7|recent|chat|-|llm|True|-|OK / -|DEGRADED|103529|
|8|progress|-|-|-|-|-|SUBMIT_FAILED / -|-|-|
|9|continue|-|-|-|-|-|SUBMIT_FAILED / -|-|-|
|10|rewrite|-|-|-|-|sub_agent:translate_agent|OK / -|-|-|
|11|deploy|delegate|code_agent|override|True|sub_agent:code_agent|OK / -|-|-|
|12|summary|skill|diary_writer|llm|True|diary_writer|OK / -|-|-|
