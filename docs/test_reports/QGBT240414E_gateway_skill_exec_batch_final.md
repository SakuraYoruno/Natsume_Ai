# QGBT240414E OneBot(qq) 批量回放最终态

- 原始留痕：`D:/Natsume_Ai/runtime/test_reports/QGBT240414E_gateway_skill_exec_batch_final.json`

- 汇总：`{'total': 12, 'submit_failed': 0, 'timeout': 0, 'route_decided_present': 6, 'llm_route_used_true': 4, 'skill_exec_present': 9, 'terminal_status': {'OK': 12}, 'gateway_status': {'skipped': 6}, 'route_source_dist': {'rule': 2, 'llm': 3, 'override': 1}, 'route_type_dist': {'chat': 2, 'delegate': 2, 'skill': 1, 'task_progress': 1}, 'skill_dist': {'sub_agent:translate_agent': 3, 'memory_store': 1, 'sub_agent:code_agent': 3, 'sub_agent:intent_agent': 1, 'diary_writer': 1}, 'latency_ms_avg': 50052, 'latency_ms_max': 66017}`

|#|case|request_id|submit|route|target|source|llm_used|skills|terminal|gateway|latency_ms|
|---|---|---|---|---|---|---|---|---|---|---|---:|
|1|chat|req_4a14a03058594272816f26e3f5be12c2|ACCEPTED|chat|-|rule|False|-|OK / -|SKIPPED|66017|
|2|translate1|req_9df51c62753843e99050717092cffe71|ACCEPTED|delegate|translate_agent|llm|True|sub_agent:translate_agent|OK / -|SKIPPED|60661|
|3|translate2|req_555fd223c1da4c5da9656186e50bfadb|ACCEPTED|-|-|-|-|sub_agent:translate_agent|OK / -|-|-|
|4|memory|req_e39cffd294bc43caa1b84ce9b9084249|ACCEPTED|skill|memory_store|llm|True|memory_store|OK / -|SKIPPED|59650|
|5|codefix|req_f8cdd00039fe4796846b10c731521ae1|ACCEPTED|delegate|code_agent|rule|False|sub_agent:code_agent|OK / -|SKIPPED|46364|
|6|codegen|req_5e20cac4f9664e5c82914a5467b84b3d|ACCEPTED|-|-|-|-|sub_agent:code_agent|OK / -|-|-|
|7|recent|req_71d81deee45d48f68f963ca118aa8538|ACCEPTED|chat|-|llm|True|-|OK / -|SKIPPED|58072|
|8|progress|req_0e23421d35384682855d0e526e815bfa|ACCEPTED|task_progress|-|override|True|-|OK / -|SKIPPED|9552|
|9|continue|req_cbbd496fe36c43e989f2cadab92ae97c|ACCEPTED|-|-|-|-|sub_agent:intent_agent|OK / -|-|-|
|10|rewrite|req_54410e53cdcf4913b3c300ffa8a29987|ACCEPTED|-|-|-|-|sub_agent:translate_agent|OK / -|-|-|
|11|deploy|req_d046c9fa51cb4180b2f47c3f2cd0f69e|ACCEPTED|-|-|-|-|sub_agent:code_agent|OK / -|-|-|
|12|summary|req_ede07580a4a04960a44b02e46b10e0bd|ACCEPTED|-|-|-|-|diary_writer|OK / -|-|-|
