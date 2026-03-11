import ast, glob, re

# 1. 全项目语法检查
errors = []
for f in glob.glob('host_brain/**/*.py', recursive=True):
    try:
        with open(f, encoding='utf-8-sig') as fh:
            ast.parse(fh.read())
    except SyntaxError as e:
        errors.append(f'{f}: {e}')
if errors:
    print('SYNTAX ERRORS:')
    for e in errors: print(' ', e)
else:
    print('全项目语法检查 OK')

# 2. EvidencePlane 单例线程安全
with open('host_brain/integrations/graph_evidence.py', encoding='utf-8') as f:
    src = f.read()
has_lock = 'threading.Lock' in src or '_singleton_lock' in src
print('EvidencePlane 单例线程安全:', 'OK' if has_lock else 'WARNING - 无锁保护')

# 3. NatsumeScheduler._trigger_task 调用 _build_system_intent 时是否传入情感状态
with open('host_brain/scheduler/scheduler.py', encoding='utf-8') as f:
    sched_src = f.read()
calls = re.findall(r'self\._build_system_intent\(task.*?\)', sched_src, re.DOTALL)
has_emotion = any('emotion' in c for c in calls)
print('_trigger_task 传入情感状态:', 'OK' if has_emotion else 'WARNING - emotion_tag/mood_state 未从 _get_emotion_state() 传入')

# 4. EpisodicStore._records 内存无上限
with open('host_brain/memory/episodic.py', encoding='utf-8') as f:
    ep_src = f.read()
has_limit = 'maxlen' in ep_src or 'MAX_RECORDS' in ep_src or 'evict' in ep_src
print('EpisodicStore._records 内存上限:', 'OK' if has_limit else 'WARNING - 无上限，长期运行会 OOM')

# 5. SkillRegistry 重复注册
with open('host_brain/skills/registry.py', encoding='utf-8') as f:
    reg_src = f.read()
has_force = 'force' in reg_src or 'overwrite' in reg_src
print('SkillRegistry 重复注册保护:', '有 force 参数' if has_force else 'WARNING - 无 force，热重载时会抛异常')

# 6. EvidenceWriteQueue.flush 异常静默降级（不重试）
with open('host_brain/integrations/evidence_write_queue.py', encoding='utf-8') as f:
    eq_src = f.read()
has_retry = 'retry' in eq_src or 'requeue' in eq_src
print('EvidenceWriteQueue 失败重试:', 'OK' if has_retry else 'WARNING - 失败静默丢弃，无重试机制')

# 7. SQLiteLedger 缺少 log_llm_route 方法（dependencies.py 中调用了它）
with open('host_brain/audit/sqlite_ledger.py', encoding='utf-8') as f:
    ledger_src = f.read()
has_llm_route = 'log_llm_route' in ledger_src
print('SQLiteLedger.log_llm_route:', 'OK' if has_llm_route else 'WARNING - 方法不存在，dependencies.py 调用会 AttributeError')

# 8. 检查 dependencies.py 中调用的 ledger 方法是否都存在
with open('host_brain/app/dependencies.py', encoding='utf-8') as f:
    dep_src = f.read()
ledger_calls = re.findall(r'ledger\.(log_\w+|persist_\w+|load_\w+|delete_\w+)\(', dep_src)
missing = []
for call in set(ledger_calls):
    if call not in ledger_src:
        missing.append(call)
if missing:
    print('dependencies.py 调用了不存在的 ledger 方法:', missing)
else:
    print('dependencies.py ledger 方法调用 OK')

print('DONE')
