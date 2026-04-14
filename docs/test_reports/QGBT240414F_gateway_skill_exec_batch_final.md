# QGBT240414F OneBot(qq) 批量回放最终态（基于 QGBT240414E request_id 复核）

- 来源批次：`D:/Natsume_Ai/runtime/test_reports/QGBT240414E_gateway_skill_exec_batch_final.json`
- 原始留痕：`D:/Natsume_Ai/runtime/test_reports/QGBT240414F_gateway_skill_exec_batch_final.json`

- 汇总：`{'total': 12, 'submit_failed': 0, 'timeout': 0, 'route_decided_present': 12, 'llm_route_used_true': 9, 'skill_exec_present': 9, 'terminal_status': {'OK': 12}, 'gateway_status': {'skipped': 12}, 'route_source_dist': {'rule': 3, 'llm': 8, 'override': 1}, 'route_type_dist': {'chat': 2, 'delegate': 7, 'skill': 2, 'task_progress': 1}, 'skill_dist': {'sub_agent:translate_agent': 3, 'memory_store': 1, 'sub_agent:code_agent': 3, 'sub_agent:intent_agent': 1, 'diary_writer': 1}, 'latency_ms_avg': 79281, 'latency_ms_max': 111434}`

|#|case|route|target|source|llm_used|skills|terminal|gateway|latency_ms|
|---|---|---|---|---|---|---|---|---|---:|
|1|chat|chat|-|rule|False|-|OK / -|SKIPPED|66017|
|2|translate1|delegate|translate_agent|llm|True|sub_agent:translate_agent|OK / -|SKIPPED|60661|
|3|translate2|delegate|translate_agent|llm|True|sub_agent:translate_agent|OK / -|SKIPPED|111434|
|4|memory|skill|memory_store|llm|True|memory_store|OK / -|SKIPPED|59650|
|5|codefix|delegate|code_agent|rule|False|sub_agent:code_agent|OK / -|SKIPPED|46364|
|6|codegen|delegate|code_agent|llm|True|sub_agent:code_agent|OK / -|SKIPPED|109896|
|7|recent|chat|-|llm|True|-|OK / -|SKIPPED|58072|
|8|progress|task_progress|-|override|True|-|OK / -|SKIPPED|9552|
|9|continue|delegate|intent_agent|llm|True|sub_agent:intent_agent|OK / -|SKIPPED|108230|
|10|rewrite|delegate|intent_agent|rule|False|sub_agent:translate_agent|OK / -|SKIPPED|107699|
|11|deploy|delegate|code_agent|llm|True|sub_agent:code_agent|OK / -|SKIPPED|107186|
|12|summary|skill|diary_writer|llm|True|diary_writer|OK / -|SKIPPED|106613|
