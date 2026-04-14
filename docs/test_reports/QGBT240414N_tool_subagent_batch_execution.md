# QGBT240414N Tool/SubAgent 批测执行明细

- Date: 2026-04-14
- Batch ID: `batch_240414_1776140586`
- Total: 10 | Pass: 10 | Fail: 0

## Case 1: run_skill.file_write

- request_id: `batch_240414_1776140586_01_file_write`
- kind: `tool`
- input:
  - skill_name: `file_write`
  - task_text: `write file a`
  - args: `{'path': 'D:\\Natsume_Ai\\runtime\\tmp\\batch_240414_1776140586_a.txt', 'content': 'alpha', 'encoding': 'utf-8'}`
  - _react_context: `{'request_id': 'batch_240414_1776140586_01_file_write', 'session_id': 'batch_240414_1776140586_01_file_write', 'person_id': 'smoke'}`
- output:
  - status: `SUCCESS`
  - success: `True`
  - error: `None`
  - result: `{'status': 'EXECUTED', 'event_id': 'evt_d83a6ed97a164088adddba728404aa5f', 'manifest_ref': 'file_write::D:\\Natsume_Ai\\runtime\\tmp\\batch_240414_1776140586_a.txt', 'path': 'D:\\Natsume_Ai\\runtime\\...`
- business_check:
  - expected: `file_a exists and content == alpha`
  - pass: `True`
  - actual_file_exists: `True`
  - actual_file_content: `alpha`

## Case 2: run_skill.file_append

- request_id: `batch_240414_1776140586_02_file_append`
- kind: `tool`
- input:
  - skill_name: `file_append`
  - task_text: `append file a`
  - args: `{'path': 'D:\\Natsume_Ai\\runtime\\tmp\\batch_240414_1776140586_a.txt', 'content': 'beta', 'encoding': 'utf-8', 'newline': True}`
  - _react_context: `{'request_id': 'batch_240414_1776140586_02_file_append', 'session_id': 'batch_240414_1776140586_02_file_append', 'person_id': 'smoke'}`
- output:
  - status: `SUCCESS`
  - success: `True`
  - error: `None`
  - result: `{'status': 'EXECUTED', 'event_id': 'evt_d97b8b3ede0843359357ff2d68eca44e', 'manifest_ref': 'file_append::D:\\Natsume_Ai\\runtime\\tmp\\batch_240414_1776140586_a.txt', 'path': 'D:\\Natsume_Ai\\runtime\...`
- business_check:
  - expected: `file_a contains alpha and beta`
  - pass: `True`
  - actual_file_content: `alpha
beta`

## Case 3: run_skill.file_read

- request_id: `batch_240414_1776140586_03_file_read`
- kind: `tool`
- input:
  - skill_name: `file_read`
  - task_text: `read file a`
  - args: `{'path': 'D:\\Natsume_Ai\\runtime\\tmp\\batch_240414_1776140586_a.txt', 'encoding': 'utf-8'}`
  - _react_context: `{'request_id': 'batch_240414_1776140586_03_file_read', 'session_id': 'batch_240414_1776140586_03_file_read', 'person_id': 'smoke'}`
- output:
  - status: `SUCCESS`
  - success: `True`
  - error: `None`
  - result: `{'status': 'EXECUTED', 'event_id': 'evt_dc817e44642f46edb39d75afedf99ebd', 'manifest_ref': 'file_read::D:\\Natsume_Ai\\runtime\\tmp\\batch_240414_1776140586_a.txt', 'path': 'D:\\Natsume_Ai\\runtime\\t...`
- business_check:
  - expected: `['alpha', 'beta']`
  - pass: `True`
  - actual_result: `{'status': 'EXECUTED', 'event_id': 'evt_dc817e44642f46edb39d75afedf99ebd', 'manifest_ref': 'file_read::D:\\Natsume_Ai\\runtime\\tmp\\batch_240414_1776140586_a.txt', 'path': 'D:\\Natsume_Ai\\runtime\\tmp\\batch_240414_1776140586_a.txt', 'content': 'alpha\r\nbeta', 'total_lines': 2, 'returned_lines': `

## Case 4: run_skill.file_list

- request_id: `batch_240414_1776140586_04_file_list`
- kind: `tool`
- input:
  - skill_name: `file_list`
  - task_text: `list runtime tmp`
  - args: `{'path': 'runtime\\tmp', 'pattern': 'batch_240414_1776140586_*.txt', 'recursive': False}`
  - _react_context: `{'request_id': 'batch_240414_1776140586_04_file_list', 'session_id': 'batch_240414_1776140586_04_file_list', 'person_id': 'smoke'}`
- output:
  - status: `SUCCESS`
  - success: `True`
  - error: `None`
  - result: `{'status': 'EXECUTED', 'event_id': 'evt_374771253aa140a3b35801895cafb5c9', 'manifest_ref': 'file_list::runtime\\tmp', 'path': 'runtime\\tmp', 'items': [{'name': 'batch_240414_1776140586_a.txt', 'path'...`
- business_check:
  - expected: `result contains batch_240414_1776140586_a.txt`
  - pass: `True`
  - actual_result: `{'status': 'EXECUTED', 'event_id': 'evt_374771253aa140a3b35801895cafb5c9', 'manifest_ref': 'file_list::runtime\\tmp', 'path': 'runtime\\tmp', 'items': [{'name': 'batch_240414_1776140586_a.txt', 'path': 'runtime\\tmp\\batch_240414_1776140586_a.txt', 'type': 'file', 'size': 11, 'modified_ms': 17761405`

## Case 5: run_skill.system_info

- request_id: `batch_240414_1776140586_05_system_info`
- kind: `tool`
- input:
  - skill_name: `system_info`
  - task_text: `system info`
  - args: `{'include_cpu': True, 'include_memory': True}`
  - _react_context: `{'request_id': 'batch_240414_1776140586_05_system_info', 'session_id': 'batch_240414_1776140586_05_system_info', 'person_id': 'smoke'}`
- output:
  - status: `SUCCESS`
  - success: `True`
  - error: `None`
  - result: `{'platform': {'system': 'Windows', 'release': '10', 'version': '10.0.26220', 'machine': 'AMD64', 'python_version': '3.11.0'}, 'cpu': {'count_logical': 16, 'count_physical': 8, 'percent': 33.3, 'freq_m...`
- business_check:
  - expected: `status SUCCESS and result has platform/system fields`
  - pass: `True`
  - actual_result: `{'platform': {'system': 'Windows', 'release': '10', 'version': '10.0.26220', 'machine': 'AMD64', 'python_version': '3.11.0'}, 'cpu': {'count_logical': 16, 'count_physical': 8, 'percent': 33.3, 'freq_mhz': 4700.0}, 'memory': {'total_gb': 31.15, 'available_gb': 11.57, 'percent': 62.9}, 'disk': {'total`

## Case 6: dispatch_sub_agent.code_agent

- request_id: `batch_240414_1776140586_06_dispatch_code`
- kind: `tool`
- input:
  - agent_id: `code_agent`
  - task: `return concise code diagnosis`
  - _react_context: `{'request_id': 'batch_240414_1776140586_06_dispatch_code', 'session_id': 'batch_240414_1776140586_06_dispatch_code', 'gateway': '<FixedFinalGateway>'}`
- output:
  - status: `SUCCESS`
  - success: `True`
  - error: `None`
  - result: `code-diagnosis-ok`
- business_check:
  - expected: `result == code-diagnosis-ok`
  - pass: `True`
  - actual: `code-diagnosis-ok`

## Case 7: dispatch_sub_agent.translate_agent

- request_id: `batch_240414_1776140586_07_dispatch_translate`
- kind: `tool`
- input:
  - agent_id: `translate_agent`
  - task: `translate hello to chinese`
  - _react_context: `{'request_id': 'batch_240414_1776140586_07_dispatch_translate', 'session_id': 'batch_240414_1776140586_07_dispatch_translate', 'gateway': '<FixedFinalGateway>'}`
- output:
  - status: `SUCCESS`
  - success: `True`
  - error: `None`
  - result: `你好`
- business_check:
  - expected: `result == 你好`
  - pass: `True`
  - actual: `你好`

## Case 8: intent.run_skill.file_write

- request_id: `batch_240414_1776140586_08_intent_write`
- kind: `intent_handler`
- process:
  - steps_count: `2`
  - step1: type=tool_call, tool=run_skill, obs_status=SUCCESS, final=None
  - step2: type=final_answer, tool=None, obs_status=None, final=B文件写入完成
  - final_answer: `B文件写入完成`
- business_check:
  - expected: `file_b exists, content intent-b, final answer correct`
  - pass: `True`
  - actual_file_exists: `True`
  - actual_file_content: `intent-b`
  - actual_final_answer: `B文件写入完成`

## Case 9: intent.run_skill.file_read

- request_id: `batch_240414_1776140586_09_intent_read`
- kind: `intent_handler`
- process:
  - steps_count: `2`
  - step1: type=tool_call, tool=run_skill, obs_status=SUCCESS, final=None
  - step2: type=final_answer, tool=None, obs_status=None, final=B文件读取完成
  - final_answer: `B文件读取完成`
- business_check:
  - expected: `step1 observation contains intent-b`
  - pass: `True`
  - actual_observation_result: `{'status': 'EXECUTED', 'event_id': 'evt_d888b8fcae364d8ba8f2abc21601f6bd', 'manifest_ref': 'file_read::D:\\Natsume_Ai\\runtime\\tmp\\batch_240414_1776140586_b.txt', 'path': 'D:\\Natsume_Ai\\runtime\\tmp\\batch_240414_1776140586_b.txt', 'content': 'intent-b', 'total_lines': 1, 'returned_lines': 1, 't`

## Case 10: intent.dispatch_sub_agent.code_agent

- request_id: `batch_240414_1776140586_10_intent_dispatch`
- kind: `intent_handler`
- process:
  - steps_count: `2`
  - step1: type=tool_call, tool=dispatch_sub_agent, obs_status=SUCCESS, final=None
  - step2: type=final_answer, tool=None, obs_status=None, final=代码子智能体执行完成
  - final_answer: `代码子智能体执行完成`
- business_check:
  - expected: `step1 tool is dispatch_sub_agent and observation status SUCCESS`
  - pass: `True`
  - actual_step1_tool: `dispatch_sub_agent`
  - actual_step1_obs_status: `SUCCESS`
  - actual_final_answer: `代码子智能体执行完成`

## 审计留痕

- `batch_240414_1776140586_01_file_write`
  - skill_name=file_write, result_status=SUCCESS, result_code=None, result_digest={'status': 'EXECUTED', 'event_id': 'evt_d83a6ed97a164088adddba728404aa5f', 'manifest_ref': 'file_write::D:\\Natsume_Ai\\runtime\\tmp\\batch_240414_1776140586_a.txt', 'path': 'D:\\N...
- `batch_240414_1776140586_02_file_append`
  - skill_name=file_append, result_status=SUCCESS, result_code=None, result_digest={'status': 'EXECUTED', 'event_id': 'evt_d97b8b3ede0843359357ff2d68eca44e', 'manifest_ref': 'file_append::D:\\Natsume_Ai\\runtime\\tmp\\batch_240414_1776140586_a.txt', 'path': 'D:\\...
- `batch_240414_1776140586_03_file_read`
  - skill_name=file_read, result_status=SUCCESS, result_code=None, result_digest={'status': 'EXECUTED', 'event_id': 'evt_dc817e44642f46edb39d75afedf99ebd', 'manifest_ref': 'file_read::D:\\Natsume_Ai\\runtime\\tmp\\batch_240414_1776140586_a.txt', 'path': 'D:\\Na...
- `batch_240414_1776140586_04_file_list`
  - skill_name=file_list, result_status=SUCCESS, result_code=None, result_digest={'status': 'EXECUTED', 'event_id': 'evt_374771253aa140a3b35801895cafb5c9', 'manifest_ref': 'file_list::runtime\\tmp', 'path': 'runtime\\tmp', 'items': [{'name': 'batch_240414_17761...
- `batch_240414_1776140586_05_system_info`
  - skill_name=system_info, result_status=SUCCESS, result_code=None, result_digest={'platform': {'system': 'Windows', 'release': '10', 'version': '10.0.26220', 'machine': 'AMD64', 'python_version': '3.11.0'}, 'cpu': {'count_logical': 16, 'count_physical': 8, 'per...
- `batch_240414_1776140586_06_dispatch_code`
  - skill_name=sub_agent:code_agent, result_status=OK, result_code=SUB_AGENT_OK, result_digest=code-diagnosis-ok
- `batch_240414_1776140586_07_dispatch_translate`
  - skill_name=sub_agent:translate_agent, result_status=OK, result_code=SUB_AGENT_OK, result_digest=你好
- `batch_240414_1776140586_08_intent_write`
  - skill_name=file_write, result_status=SUCCESS, result_code=None, result_digest={'status': 'EXECUTED', 'event_id': 'evt_c8400cb897ce45e28b90f4932f61e0e7', 'manifest_ref': 'file_write::D:\\Natsume_Ai\\runtime\\tmp\\batch_240414_1776140586_b.txt', 'path': 'D:\\N...
- `batch_240414_1776140586_09_intent_read`
  - skill_name=file_read, result_status=SUCCESS, result_code=None, result_digest={'status': 'EXECUTED', 'event_id': 'evt_d888b8fcae364d8ba8f2abc21601f6bd', 'manifest_ref': 'file_read::D:\\Natsume_Ai\\runtime\\tmp\\batch_240414_1776140586_b.txt', 'path': 'D:\\Na...
- `batch_240414_1776140586_10_intent_dispatch`
  - skill_name=sub_agent:code_agent, result_status=OK, result_code=SUB_AGENT_OK, result_digest=代码子智能体执行完成