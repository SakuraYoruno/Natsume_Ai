# QGBT240414S 网关消息意图识别到子Agent执行链路批测

- 日期: 2026-04-14
- 测试目标: 从“自然网关消息输入”开始，验证 `意图识别 -> EXECUTIVE_ROUTE -> subagent/skill 执行 -> 审计落库` 全链路。
- 测试入口: `POST /v1/chat`（不注入手工 intent_draft，使用自然 query）
- 审计验证: `intent_log / governor_log / skill_exec_log` + `/obs/trace/{request_id}`

## 总体结果

- 总用例: 10
- 路由命中可执行链路（skill/delegate/plan）: 7/10
- 实际触发 sub_agent 执行: 4/10
- `NO_LLM gateway` 降级: 0
- `503` 模型错误: 0

## 用例明细（核心字段）

1. `S01_chat_greeting`  
request_id=`req_9f160db9d25d4617833ba407f9121ad0`  
route=`skill:narrative_only` reason=`intent_draft:skill_target`  
skill_exec=`narrative_only/SUCCESS`

2. `S02_code_debug`  
request_id=`req_14fca700f8484f2e85a49427d7fc72a9`  
intent action=`work_code`，但 route=`chat` reason=`default:chat`  
skill_exec=0（未触发 sub_agent）

3. `S03_translate`  
request_id=`req_966ce9940fe1402ea7b5d3a197704128`  
route=`delegate:translate_agent` reason=`capability_gap:runtime_synthesized`  
skill_exec=`sub_agent:translate_agent/OK/SUB_AGENT_OK`

4. `S04_system_check`  
request_id=`req_f3e73249ecdc427cbc57c8753f03cef0`  
route=`skill:narrative_only`  
skill_exec=`narrative_only/SUCCESS`（未执行系统能力）

5. `S05_plan_multi`  
request_id=`req_0137c11b5f12497dac7c63c0ff91d8ea`  
route=`plan:skill_dag` reason=`hard_rule:multi_step`  
skill_exec:
- `sub_agent:intent_agent/OK`
- `sub_agent:intent_agent/OK`

6. `S06_gap_deploy`  
request_id=`req_3980236dae4a4a57ac3ad1d7efeafcc7`  
route=`delegate:code_agent` reason=`capability_gap:runtime_synthesized`  
skill_exec=`sub_agent:code_agent/OK/SUB_AGENT_OK`

7. `S07_task_progress`  
request_id=`req_f9b0e918e6ca4812a303c6d94b034bb1`  
commit 给出进度文本（“任务还在进行”），但未产生 `EXECUTIVE_ROUTE` 事件  
skill_exec=0

8. `S08_code_generate`  
request_id=`req_ad2ad2f26df848d3bbfa38263f114b5c`  
intent action=`work_code`，但 route=`chat` reason=`default:chat`  
skill_exec=0（未触发 sub_agent）

9. `S09_translate_compare`  
request_id=`req_4d8bff1283204a06b861ee46373101c6`  
route=`skill:narrative_only`  
skill_exec=`narrative_only/SUCCESS`（未进入翻译子链）

10. `S10_exec_intent`  
request_id=`req_0d4973b1274c4f739206f06e65674336`  
route=`delegate:intent_agent` reason=`capability_gap:runtime_synthesized`  
skill_exec=`sub_agent:intent_agent/OK/SUB_AGENT_OK`

## 关键结论

1. 子Agent执行链路已恢复可用（`NO_LLM gateway` 问题已消失）。
2. 意图识别与执行路由存在偏差：`work_code` 不稳定落到 `chat`（S02/S08）。
3. 翻译复合请求也可能被吸入 `narrative_only`（S09），导致“会说不会做”。
4. 复杂多步请求可触发 plan（S05），但实际多落到 `intent_agent`，技能分派粒度仍偏粗。

## 原始数据留痕

- `runtime/test_reports/QGBT240414S_gateway_intent_subagent_batch.json`
