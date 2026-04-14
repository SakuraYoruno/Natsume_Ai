# QGBT240414B 批量回放最终态（限流阈值提升后）

- 原始留痕：`D:/Natsume_Ai/runtime/test_reports/QGBT240414B_gateway_skill_exec_batch_final.json`

- 汇总：{'total': 12, 'submit_failed': 0, 'timeout': 0, 'route_decided_present': 12, 'llm_route_used_true': 10, 'skill_exec_present': 11, 'terminal_status': {'OK': 12}, 'gateway_status': {'failed': 12}, 'route_source_dist': {'override': 3, 'llm': 7, 'rule': 2}, 'route_type_dist': {'delegate': 9, 'skill': 1, 'recent_turn': 1, 'task_progress': 1}, 'skill_dist': {'sub_agent:intent_agent': 4, 'sub_agent:translate_agent': 3, 'memory_store': 1, 'sub_agent:code_agent': 3}, 'latency_ms_avg': 104263, 'latency_ms_max': 154306}

|#|case|route|target|source|llm_used|skills|terminal|gateway|latency_ms|
|---|---|---|---|---|---|---|---|---|---:|
|1|chat|delegate|intent_agent|override|True|sub_agent:intent_agent|OK / -|failed|154306|
|2|translate1|delegate|translate_agent|llm|True|sub_agent:translate_agent|OK / -|failed|149342|
|3|translate2|delegate|translate_agent|llm|True|sub_agent:translate_agent|OK / -|failed|69312|
|4|memory|skill|memory_store|llm|True|memory_store|OK / -|failed|69298|
|5|codefix|delegate|code_agent|llm|True|sub_agent:code_agent|OK / -|failed|149283|
|6|codegen|delegate|code_agent|llm|True|sub_agent:code_agent|OK / -|failed|66974|
|7|recent|recent_turn|-|override|True|sub_agent:intent_agent|OK / -|failed|66960|
|8|progress|task_progress|-|override|True|-|OK / -|failed|8258|
|9|continue|delegate|intent_agent|rule|False|sub_agent:intent_agent|OK / -|failed|144980|
|10|rewrite|delegate|translate_agent|llm|True|sub_agent:translate_agent|OK / -|failed|75167|
|11|deploy|delegate|code_agent|llm|True|sub_agent:code_agent|OK / -|failed|150461|
|12|summary|delegate|intent_agent|rule|False|sub_agent:intent_agent|OK / -|failed|146815|