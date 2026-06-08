# QGBT260520H 通知 / 提醒技能验收

## 结论

状态：PASSED

本批验收覆盖 `notify_schedule_reminder` 的真实执行主链：

```text
intent skill route
  -> _execute_skill()
  -> NotificationSkillProvider
  -> notification executor
  -> 本地 reminder queue
```

在 Edge / APScheduler 不可用时，提醒技能会降级写入本地 JSON 队列，不阻断主链。

## 修复

- Windows Toast PowerShell 文本改为安全单引号转义。
- macOS osascript 文本改为转义后的字符串字面量。
- 新增提醒技能执行主链测试，确保 `notify_schedule_reminder` 能从 `_execute_skill()` 落到本地队列。

## 真实验收

验收产物：

```text
artifacts/notification_skill_acceptance/run-20260520-170740/notification_skill_acceptance.json
```

关键结果：

```text
status=PASSED
outcome_success=true
raw_status=EXECUTED
reminder_id=accept-notify-reminder-001
queue_count=1
queue_path=artifacts/notification_skill_acceptance/run-20260520-170740/reminder_queue.json
```

本次没有触发真实桌面弹窗，也没有调用 Edge TTS；只验证“提醒请求能被主链接收并可靠落盘”。

## 回归测试

命令：

```powershell
python -m pytest -q tests\unit\test_notification_skills.py
python -m pytest -q tests\unit\test_notification_skills.py tests\unit\test_tool_execution_matrix.py tests\unit\test_canon_proposal_content.py tests\unit\test_long_term_distiller.py tests\unit\test_memory_distillation.py
```

结果：

```text
4 passed in 0.62s
60 passed in 18.82s
```
