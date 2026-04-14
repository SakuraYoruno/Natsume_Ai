# QGBT240414G 批测对比（B vs F）

- B: `QGBT240414B`（auto-reply 开启，gateway 回包失败高）
- F: `QGBT240414F`（auto-reply 关闭，主链路回包短路为 SKIPPED）
- 原始对比：`D:/Natsume_Ai/runtime/test_reports/QGBT240414G_compare_B_vs_F.json`

|指标|B|F|Δ(F-B)|
|---|---:|---:|---:|
|total|12|12|0|
|submit_failed|0|0|0|
|timeout|0|0|0|
|route_decided_present|12|12|0|
|llm_route_used_true|10|9|-1|
|skill_exec_present|11|9|-2|
|latency_ms_avg|104263|79281|-24982|
|latency_ms_max|154306|111434|-42872|

|分布|B|F|
|---|---|---|
|terminal_status|`{'OK': 12}`|`{'OK': 12}`|
|gateway_status|`{'failed': 12}`|`{'skipped': 12}`|
|route_source_dist|`{'override': 3, 'llm': 7, 'rule': 2}`|`{'rule': 3, 'llm': 8, 'override': 1}`|
|route_type_dist|`{'delegate': 9, 'skill': 1, 'recent_turn': 1, 'task_progress': 1}`|`{'chat': 2, 'delegate': 7, 'skill': 2, 'task_progress': 1}`|
|skill_dist|`{'sub_agent:intent_agent': 4, 'sub_agent:translate_agent': 3, 'memory_store': 1, 'sub_agent:code_agent': 3}`|`{'sub_agent:translate_agent': 3, 'memory_store': 1, 'sub_agent:code_agent': 3, 'sub_agent:intent_agent': 1, 'diary_writer': 1}`|
