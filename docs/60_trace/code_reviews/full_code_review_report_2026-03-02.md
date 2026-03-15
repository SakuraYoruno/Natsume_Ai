鍙椾紬锛欳ODEX
鏍囬锛氬叏闈唬鐮佸闃呮姤鍛婏紙绗笁鐗堬紝2026-03-02锛?
# 馃攳 Natsume Ai 鍏ㄩ潰浠ｇ爜瀹￠槄鎶ュ憡

> **鐢熸垚鏃ユ湡**锛?026-03-02锛堢涓夌増锛屽惈 TASK-0000~0083 鍏ㄩ儴浜や粯锛?> **瀹￠槄鑼冨洿**锛歨ost_brain/銆乪dge_client/銆乼ests/銆乧onfigs/銆乨ocs/
> **瀹￠槄鏂规硶**锛氶€愭ā鍧楁簮鐮侀槄璇?+ 濂戠害娴嬭瘯鍏ㄩ噺杩愯 + 鏋舵瀯涓€鑷存€ф鏌?+ 涓嶅彉閲忛潤鎬佹壂鎻?+ 瀹夊叏瀹¤

---

## 馃搳 涓€銆侀」鐩€昏

| 鎸囨爣 | 鏁版嵁 | 杈冧笂娆″彉鍖?|
|------|------|-----------|
| **Host Brain 婧愭枃浠?* | 78 涓?`.py`锛堝惈 `__init__`锛?| +23锛坴s 涓婄増锛?|
| **Edge Client 婧愭枃浠?* | 17 涓?`.py` | +2 |
| **Host Brain 浠ｇ爜琛?* | 22,326 琛?| +1,069 |
| **Edge Client 浠ｇ爜琛?* | 5,370 琛?| +1,425 |
| **娴嬭瘯浠ｇ爜琛?* | 17,749 琛?| +3,653 |
| **鎬讳唬鐮佽** | **45,445 琛?* | +6,147 |
| **濂戠害娴嬭瘯鏂囦欢** | 81 涓?| +13 |
| **E2E 娴嬭瘯鏂囦欢** | 4 涓?| 鈥?|
| **娴嬭瘯鏂囦欢鍚堣** | 89 涓紙鍚?conftest/helper锛?| +17 |
| **娴嬭瘯鏂█鐐?* | 697 閫氳繃 / **7 澶辫触** 鈿狅笍 | +203 閫氳繃 / +7 澶辫触 |
| **娴嬭瘯 Fixture 鏂囦欢** | 208 涓?`.json` | 鈥?|
| **瀹¤鏃ュ織琛?* | 26 寮?| +8 |
| **閿欒鐮侊紙reason_code锛?* | 144 涓父閲?| -9锛堥儴鍒嗛噸鏋勫悎骞讹級 |
| **SQL 杩佺Щ鑴氭湰** | 38 涓?| +25 |
| **Git 鎻愪氦**锛堝叏 TASK锛?| TASK-0000~0083 鍏ㄩ儴瀹屾垚 | 鈥?|

---

## 馃彈锔?浜屻€佹灦鏋勫叏鏅?
```
鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€ Host Brain锛圥ython 3.12+锛?鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?鈹?                                                                       鈹?鈹? 鈹屸攢 app/ 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?  鈹?鈹? 鈹? fastapi_app.py (626L) 鈫?HTTP 鍏ュ彛 + 涓棿浠?+ 闈欐€佹枃浠?         鈹?  鈹?鈹? 鈹? main.py (909L)        鈫?鍚姩鎶ュ憡 + 鍋ュ悍鑱氬悎 + 9 鑺傜偣鎷撴墤      鈹?  鈹?鈹? 鈹? config.py (549L)      鈫?鍒嗗眰閰嶇疆 + 鑴辨晱蹇収 + diff             鈹?  鈹?鈹? 鈹? web_ui.py (231L)      鈫?鍙娴嬮潰鏉?API                        鈹?  鈹?鈹? 鈹? static/panel.html     鈫?閾捐矾鍙鍖栭潰鏉?                        鈹?  鈹?鈹? 鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?  鈹?鈹?                                                                       鈹?鈹? 鈹屸攢 governor/ 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹? 鈹屸攢 audit/ 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?    鈹?鈹? 鈹? decision.py (310L)   鈹? 鈹? sqlite_ledger.py (1570L) 鈫?鏈€澶? 鈹?    鈹?鈹? 鈹? rules.py (200L)      鈹? 鈹? migration_runner.py (464L)       鈹?    鈹?鈹? 鈹? schemas.py (260L)    鈹? 鈹? retention.py (175L)              鈹?    鈹?鈹? 鈹? tokens.py (220L)     鈹? 鈹? drill.py (85L)                   鈹?    鈹?鈹? 鈹? rate_limit_store.py  鈹? 鈹? 38 涓?SQL 杩佺Щ鑴氭湰               鈹?    鈹?鈹? 鈹?   (600L)             鈹? 鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?    鈹?鈹? 鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?                                           鈹?鈹?                                                                       鈹?鈹? 鈹屸攢 integrations/ 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹? 鈹屸攢 core/ 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?    鈹?鈹? 鈹? graph_runtime.py     鈹? 鈹? errors.py (144 涓敊璇爜甯搁噺)    鈹?    鈹?鈹? 鈹? graph_executor.py    鈹? 鈹? services.py (763L)               鈹?    鈹?鈹? 鈹? graph_fallback.py    鈹? 鈹? qa.py (436L)                     鈹?    鈹?鈹? 鈹? graph_contract.py    鈹? 鈹? config_snapshot.py / ids.py      鈹?    鈹?鈹? 鈹? graph_model_resolve  鈹? 鈹? metabolism.py / key_profiles.py  鈹?    鈹?鈹? 鈹? llm_gateway.py       鈹? 鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?    鈹?鈹? 鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?                                           鈹?鈹?                                                                       鈹?鈹? 鈹屸攢 airlock/ 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹? 鈹屸攢 observability/ 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?    鈹?鈹? 鈹? sanitizer.py         鈹? 鈹? cross_timeline.py (500L)         鈹?    鈹?鈹? 鈹? quarantine.py        鈹? 鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?    鈹?鈹? 鈹? sandbox.py (468L)    鈹?                                           鈹?鈹? 鈹? docker_sandbox.py    鈹? 鈹屸攢 vision/ 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?    鈹?鈹? 鈹?   (245L) 鈫?NEW       鈹? 鈹? screen_ocr.py (355L)             鈹?    鈹?鈹? 鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹? 鈹? camera_event.py (525L)           鈹?    鈹?鈹? 鈹屸攢 channels/ 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹? 鈹? detector_registry.py (433L)      鈹?    鈹?鈹? 鈹? wechat.py (187L)     鈹? 鈹?   鈫?NEW                          鈹?    鈹?鈹? 鈹? vts.py (238L)        鈹? 鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?    鈹?鈹? 鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?                                           鈹?鈹?                                                                       鈹?鈹? + bond/ + memory/ + retrieval/ + skills/ + tools/ + lsp/ + release/  鈹?鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?
鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€ Edge Client 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?鈹? rpc/voice.py | rpc/event_reporter.py (474L) 鈫?NEW                    鈹?鈹? rpc/resource_gate.py | rpc/health.py (56L) 鈫?NEW                     鈹?鈹? senses/asr.py (1028L) | senses/fallback_llm.py (63L)                 鈹?鈹? actuators/speech.py | actuators/tts_engine.py                         鈹?鈹? actuators/vts_driver.py (693L) + 閲嶈繛鏀寔                             鈹?鈹? proofs/resource_gate.py (22L) | config.py (472L)                      鈹?鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?```

---

## 馃敩 涓夈€侀€愭ā鍧楁繁搴﹀鏌?
### 3.1 搴旂敤鍏ュ彛灞傦紙`host_brain/app/`锛夆€?猸愨瓙猸愨瓙猸?
| 鏂囦欢 | 琛屾暟 | 瀹屾垚搴?| 鍙樺寲 |
|------|------|--------|------|
| `main.py` | 909 | 95% | 鈥?|
| `config.py` | 549 | 95% | +34锛圧ate Limit 閰嶇疆椤癸級 |
| `fastapi_app.py` | 626 | 95% | **NEW锛圱ASK-0065锛?* |
| `web_ui.py` | 231 | 90% | **NEW锛圱ASK-0066锛?* |
| `static/panel.html` | 鈥?| 90% | **NEW锛圱ASK-0066锛?* |

**瀹℃煡缁撹锛?*
- 鉁?`fastapi_app.py`锛歅OST `/v1/chat` + GET 鍋ュ悍绔偣 + 涓棿浠跺紓甯稿厹搴?+ 闈欐€佹枃浠舵湇鍔°€傜粨鏋勬竻鏅帮紝涓棿浠跺眰淇濊瘉浜嗕换浣曟湭鎹曡幏寮傚父浠嶈繑鍥炲彲瑙ｆ瀽 JSON銆?- 鉁?`web_ui.py`锛氬欢杩熷鍏?`fastapi_app.get_ledger` 閬垮厤寰幆寮曠敤鈥斺€旀纭ā寮忋€?- 鉁?`config.py`锛氭柊澧?`HOST_RATE_LIMIT_BACKEND` / `HOST_RATE_LIMIT_WINDOW_MS` / `HOST_RATE_LIMIT_MAX_REQUESTS` 涓変釜閰嶇疆椤癸紝鏃犳晥鍊艰嚜鍔ㄩ檷绾у埌 memory 鍚庣锛坵arning 绾ц€岄潪 error 绾э級銆?- 鈿狅笍 **鍙戠幇**锛歚main.py` 绗?908 琛屾湁瑁?`print()` 璋冪敤锛屽簲鏇挎崲涓?`logger.info()`銆?
---

### 3.2 Governor 娌荤悊寮曟搸锛坄host_brain/governor/`锛夆€?猸愨瓙猸愨瓙猸?
| 鏂囦欢 | 琛屾暟 | 瀹屾垚搴?| 鍙樺寲 |
|------|------|--------|------|
| `decision.py` | 310 | 92% | 鈥?|
| `rules.py` | 200 | 95% | +R4 backend 鏍囨敞 |
| `schemas.py` | 260 | 95% | 鈥?|
| `tokens.py` | 220 | 95% | 鈥?|
| `rate_limit_store.py` | 600 | 92% | **NEW锛圱ASK-0073锛?* |

**瀹℃煡缁撹锛?*
- 鉁?`rate_limit_store.py`锛氭娊璞℃帴鍙?`RateLimitStore` + 3 绉嶅悗绔紙InMemory/SQLite/Redis锛? `DegradingRateLimitStore` 闄嶇骇鍖呰鍣?+ 宸ュ巶鍑芥暟銆傜嚎绋嬪畨鍏紙InMemory 鐢?threading.Lock锛孲QLite 鐢?WAL+busy_timeout锛夛紝浠ｇ爜缁撴瀯娓呮櫚銆?- 鉁?`rules.py`锛歊4 Gate 澧炲己浜?`backend`/`degraded`/`degraded_from`/`degraded_reason_code` 鍙€夊瓧娈碉紝鍚戝悗鍏煎銆?- 鉁?`GateSnapshot` dataclass 浣跨敤 `frozen=True` 淇濊瘉涓嶅彲鍙樻€с€?- 鈿狅笍 **鍙戠幇**锛歚RedisRateLimitStore` 渚濊禆 `redis` 鍖咃紝浣?`requirements.txt` 鏈垪鍏ワ紙鎸夎璁′负鍙€変緷璧栵紝杩愯鏃?import 澶辫触浼氳Е鍙戦檷绾э級鈥斺€旈渶鍦ㄩ儴缃叉枃妗ｄ腑璇存槑銆?
---

### 3.3 瀹¤閾剧郴缁燂紙`host_brain/audit/`锛夆€?猸愨瓙猸愨瓙猸?
| 鏂囦欢 | 琛屾暟 | 瀹屾垚搴?| 鍙樺寲 |
|------|------|--------|------|
| `sqlite_ledger.py` | 1,570 | 95% | +8 寮犳柊琛?+ WAL 澧炲己 + fetch_by_time_range |
| `migration_runner.py` | 464 | 92% | 鈥?|
| `retention.py` | 175 | 90% | 鈥?|
| `drill.py` | 85 | 85% | 鈥?|

**瀹℃煡缁撹锛?*
- 鉁?绯荤粺鏈€澶у崟鏂囦欢锛?,570 琛岋級锛屼絾鑱岃矗鏄庣‘鈥斺€旀墍鏈夊璁¤〃鐨?DDL/DML/Property 闆嗕腑绠＄悊銆?- 鉁?WAL 妯″紡澧炲己锛圱ASK-0070锛夛細`_activate_wal()` 鈫?鑷姩闄嶇骇鍒?DELETE 妯″紡 + `AUDIT_WAL_FAILED` 鍙娴?+ busy_timeout=5000ms + 3 娆″啓鍏ラ噸璇曘€?- 鉁?`_log_to_table()` 鏄墍鏈夊璁″啓鍏ョ殑缁熶竴鍏ュ彛锛宍_write_lock` + connection context manager + 閲嶈瘯鏈哄埗淇濊瘉浜嗙嚎绋嬪畨鍏ㄣ€?- 鉁?**26 寮犲璁¤〃**瑕嗙洊鍏ㄩ摼璺紝鍚柊澧炵殑 model_control_log / rpc_event_log / edge_health_snapshot / asr_recovery_log / vts_reconnect_log / cross_timeline_cache / config_deploy_log / release_audit_log銆?- 鉁?**鏂板** `fetch_by_time_range()` / `count_by_time_range()` 鏂规硶鈥斺€旀敮鎸佹椂闂寸獥鍙ｈ繃婊?+ 鍒嗛〉鏌ヨ锛圱ASK-0078锛夈€?- 鈿狅笍 **鍙戠幇**锛歚_fetch_all()` 浣跨敤 `f"SELECT * FROM {table}"` 琛ㄥ悕鎻掑€笺€傝櫧鐒?table 鍚嶆潵鑷唴閮ㄥ父閲忚€岄潪鐢ㄦ埛杈撳叆锛屼絾寤鸿澧炲姞琛ㄥ悕鐧藉悕鍗曟牎楠屼互闃叉娼滃湪鐨?SQL 娉ㄥ叆椋庨櫓銆?- 鈿狅笍 **鍙戠幇**锛歚_table_has_column()` 姣忔璋冪敤閮芥墽琛?`PRAGMA table_info`锛屽湪棰戠箒璋冪敤鍦烘櫙鍙兘鏈夋€ц兘褰卞搷鈥斺€斿缓璁紦瀛樿〃缁撴瀯淇℃伅銆?
---

### 3.4 Airlock 闅旂灞傦紙`host_brain/airlock/`锛夆€?猸愨瓙猸愨瓙

| 鏂囦欢 | 琛屾暟 | 瀹屾垚搴?| 鍙樺寲 |
|------|------|--------|------|
| `sanitizer.py` | 230 | 90% | 鈥?|
| `quarantine.py` | 110 | 85% | 鈥?|
| `sandbox.py` | 468 | 90% | +Docker 鍚庣濮旀墭 + `isolation_backend` 瀛楁 |
| `docker_sandbox.py` | 245 | 88% | **NEW锛圱ASK-0077锛?* |

**瀹℃煡缁撹锛?*
- 鉁?`sandbox.py`锛歴ubprocess 闅旂 + 涓存椂鐩綍 + 瓒呮椂寮烘潃 + 杈撳嚭鎴柇 + 浠ｇ爜浣撶Н涓婇檺 + 瀹¤鍐欏叆銆俙SandboxExecResult` 缁撴瀯瀹屾暣銆?- 鉁?**鏂板** `docker_sandbox.py`锛圱ASK-0077锛夛細Docker 瀹瑰櫒绾ч殧绂诲悗绔紝`--network=none` + `--read-only` + `--pids-limit=32` + `--memory=128m`銆侱ocker 涓嶅彲鐢ㄦ椂鑷姩闄嶇骇鍒?subprocess 骞舵爣璁?`degraded_from=docker`銆?- 鉁?`SandboxConfig` 鏂板 `isolation_backend` 瀛楁锛坄"subprocess"` / `"docker"`锛夛紝鏀寔鎸夌幆澧冮厤缃殧绂荤骇鍒€?- 鈿狅笍 **P1 瀹夊叏椋庨櫓**锛歴ubprocess 鍚庣闅旂绾у埆杈冧綆锛屾伓鎰忎唬鐮佷粛鍙鍙栦富鏈烘枃浠剁郴缁熴€傜敓浜ч儴缃插簲浣跨敤 Docker 鍚庣銆?- 鈿狅笍 **鍙戠幇**锛歚subprocess.TimeoutExpired` 澶勭悊鍦?Windows/Linux 涓婅涓哄彲鑳戒笉鍚岋紙淇″彿澶勭悊宸紓锛夈€?
---

### 3.5 鍙娴嬪眰锛坄host_brain/observability/`锛夆€?猸愨瓙猸愨瓙

| 鏂囦欢 | 琛屾暟 | 瀹屾垚搴?| 鍙樺寲 |
|------|------|--------|------|
| `cross_timeline.py` | 500 | 92% | +fetch_by_time_range 闆嗘垚 |

**瀹℃煡缁撹锛?*
- 鉁?`EdgeEventReport` + `HostEdgeTimelineAggregator` + `UnifiedTimelineEntry` 涓夊眰缁撴瀯銆?- 鉁?鏃堕棿婕傜Щ瀹瑰繊绛栫暐锛堥粯璁?5 绉掗槇鍊煎彲閰嶇疆锛夈€?- 鉁?Host 瀹¤琛?+ Edge 浜嬩欢缁熶竴鎺掑簭杈撳嚭锛宺equest_id/session_id 鍏宠仈銆?- 鉁?宸查泦鎴?`fetch_by_time_range()` 瀹炵幇鎸夋椂闂寸獥鍙ｉ珮鏁堟煡璇紙TASK-0078 瑙ｅ喅浜嗗師鏉ョ殑鍏ㄩ噺鍐呭瓨鍔犺浇闂锛夈€?
---

### 3.6 瑙嗚绯荤粺锛坄host_brain/vision/`锛夆€?猸愨瓙猸愨瓙

| 鏂囦欢 | 琛屾暟 | 瀹屾垚搴?| 鍙樺寲 |
|------|------|--------|------|
| `screen_ocr.py` | 355 | 85% | 鈥?|
| `camera_event.py` | 525 | 88% | +detect_multi |
| `detector_registry.py` | 433 | 90% | **NEW锛圱ASK-0074锛?* |

**瀹℃煡缁撹锛?*
- 鉁?`detector_registry.py`锛氱嚎绋嬪畨鍏ㄧ殑 `DetectorRegistry` + `MultiDetectorAggregator`銆傛敮鎸佸苟琛屾墽琛屻€侀儴鍒嗘垚鍔燂紙`PARTIAL`锛夈€佸叏閮ㄥけ璐ワ紙`ALL_FAILED`锛変笁绉嶈仛鍚堢粨鏋溿€?- 鉁?`detect_multi()` 鏂板鏂规硶涓嶅奖鍝嶇幇鏈?`detect()` 璋冪敤鏂光€斺€斿悜鍚庡吋瀹广€?- 鉁?Gaming 妯″紡闅旂鍦?screen_ocr 灞傚畬鏁村疄鐜般€?
---

### 3.7 闆嗘垚灞傦紙`host_brain/integrations/`锛夆€?猸愨瓙猸愨瓙

| 鏂囦欢 | 琛屾暟 | 瀹屾垚搴?| 鍙樺寲 |
|------|------|--------|------|
| `graph_runtime.py` | 296 | 88% | 閲嶆瀯锛圱ASK-0069锛?|
| `graph_executor.py` | 335 | 88% | **NEW锛圱ASK-0069 鎷嗗垎锛?* |
| `graph_fallback.py` | 126 | 90% | **NEW锛圱ASK-0069 鎷嗗垎锛?* |
| `graph_contract.py` | 243 | 90% | **NEW锛圱ASK-0069 鎷嗗垎锛?* |
| `graph_model_resolve.py` | 179 | 88% | **NEW锛圱ASK-0069 鎷嗗垎锛?* |
| `llm_gateway.py` | 300 | 85% | 鈥?|

**瀹℃煡缁撹锛?*
- 鉁?TASK-0069 灏嗗師 942 琛岀殑 `graph_runtime.py` 鎷嗗垎涓?5 涓ā鍧椻€斺€旇亴璐ｆ竻鏅板害澶у箙鎻愬崌銆?- 鉁?瀵煎叆鏂瑰悜鍗曞悜锛歳untime 鈫?executor/fallback/model_resolve 鈫?contract锛屾棤寰幆寮曠敤銆?- 鉁?`graph_contract.py` 浣滀负鍏变韩绫诲瀷灞傦紝鎵€鏈夋ā鍧楅€氳繃瀹冨叡浜暟鎹粨鏋勩€?- 鈿狅笍 **鍙戠幇**锛歚graph_executor.py` 绗?43 琛?`compile_runtime_graph(self)` 鏄敮涓€娌℃湁杩斿洖绫诲瀷鏍囨敞鐨勬柟娉曗€斺€旇交寰被鍨嬫彁绀虹己澶便€?
---

### 3.8 鍏朵粬 Host 妯″潡

| 妯″潡 | 浠ｈ〃鏂囦欢 | 瀹屾垚搴?| 璇勪环 |
|------|---------|--------|------|
| **Bond** | `neo4j_client.py` (522L) / `bond_dynamics.py` (245L) | 90% | 鍗曞啓鍣?+ 瀹¤闂幆瀹屽杽 |
| **Memory** | `episodic.py` (503L) / `chroma_client.py` (444L) | 88% | evidence_refs 寮哄埗 + 鐢熷懡鍛ㄦ湡瀹屾暣 |
| **Canon** | `canon_world_store.py` (304L) / `canon_style_store.py` (228L) | 95% | 鐗堟湰鍖?+ 浜嬪疄闅旂浼樼 |
| **Retrieval** | `reranker.py` (967L) / `embedding.py` (835L) | 85% | 鍏ㄩ摼璺彲瀹¤锛岄檷绾ц矾寰勫畬鏁?|
| **Channels** | `wechat.py` (187L) / `vts.py` (238L) | 95% | 纭畾鎬ф槧灏?+ DEGRADED 闄嶇骇 + 閲嶅鍘婚噸 |
| **Skills** | `loader.py` (270L) / `clawd_ui_action.py` (195L) | 88% | Token 蹇呮 + 绾㈢嚎鐔旀柇 |
| **Tools** | `broker.py` (225L) / `mcp_adapter.py` (170L) | 90% | Broker 鍗曞叆鍙?+ MCP 鎵╁睍 |
| **LSP** | `vault.py` (417L) | 90% | Artifact 鍙鍧€ + GC 鍙璁?|
| **Release** | `release_manifest.py` (405L) | 90% | 鍙戝竷娓呭崟 + 鍥炴粴 + model_plane digest 鏍￠獙 |
| **Core** | `services.py` (763L) / `qa.py` (436L) | 92% | 9 鑺傜偣鎷撴墤 + QA 鎷︽埅瀹屽杽 |

---

### 3.9 Edge Client 鈥?猸愨瓙猸愨瓙猸?
| 鏂囦欢 | 琛屾暟 | 瀹屾垚搴?| 鍙樺寲 |
|------|------|--------|------|
| `senses/asr.py` | 1,028 | 92% | +璁惧鎭㈠寰幆 + 鍊欓€夎澶囨瀯寤猴紙TASK-0081锛?|
| `senses/fallback_llm.py` | 63 | 95% | **NEW** |
| `actuators/speech.py` | 550 | 90% | 鈥?|
| `actuators/tts_engine.py` | 542 | 90% | 鈥?|
| `actuators/vts_driver.py` | 693 | 92% | +鎸囨暟閫€閬块噸杩?+ 鍋ュ悍蹇収锛圱ASK-0080锛?|
| `rpc/voice.py` | 401 | 95% | 鈥?|
| `rpc/event_reporter.py` | 474 | 95% | **NEW锛圱ASK-0079锛?* |
| `rpc/resource_gate.py` | 380 | 95% | 鈥?|
| `rpc/health.py` | 56 | 95% | **NEW** |
| `proofs/resource_gate.py` | 22 | 95% | **NEW** |
| `config.py` | 472 | 95% | 鈥?|

**瀹℃煡缁撹锛?*
- 鉁?ASR锛圱ASK-0067 + TASK-0081锛夛細WhisperModelManager + CUDA鈫扖PU 鑷姩闄嶇骇 + VAD 闈欓煶妫€娴?+ **楹﹀厠椋庤澶囪嚜鍔ㄦ仮澶?*锛坄_device_recovery_loop` + `_build_device_candidates` + `trigger_device_recovery`锛夈€?- 鉁?TTS锛圱ASK-0068锛夛細GPT-SoVITS 寮曟搸 + 鎾斁闃熷垪 + barge-in 鎵撴柇 + 璧勬簮棰勭暀/閲婃斁銆?- 鉁?VTS锛圱ASK-0068 + TASK-0080锛夛細WebSocket 闀胯繛鎺?+ **鎸囨暟閫€閬胯嚜鍔ㄩ噸杩?*锛堟渶澶?10 娆★紝绾?30 绉掔疮璁★級+ 閲嶈繛鍚?`reset_to_neutral` + `VTS_RECONNECT_EXHAUSTED` reason_code銆?- 鉁?**Event Reporter锛圱ASK-0079锛?*锛欵dge鈫扝ost RPC 浜嬩欢鎵归噺涓婃姤锛屽惈缃戠粶涓柇缂撳瓨锛堟渶澶?1000 鏉★級+ 鎭㈠鍚庢寜 `ts_ms` 琛ュ彂銆?- 鉁?Resource Gate锛氫紭鍏堢骇 ASR > PLAYBACK > TTS_SYNTH > FALLBACK_LLM锛屼簰鏂ユ姠鍗犲彲瀹¤銆?- 鉁?Fallback LLM锛氶粯璁ゅ嵏杞?+ draft_only 寮哄埗 + 绂佹瑁佸喅瀛楁 + 浼墽琛屾嫤鎴€?- 鈿狅笍 **鍙戠幇**锛歚vts_driver.py:685` 浣跨敤 `asyncio.get_event_loop()`鈥斺€擯ython 3.10+ 宸插純鐢紝搴旀敼涓?`asyncio.get_running_loop()` 鎴?`asyncio.new_event_loop()`銆?
---

## 馃И 鍥涖€佹祴璇曚綋绯昏瘎浼?
### 4.1 娴嬭瘯鍏ㄩ噺杩愯缁撴灉

```
697 passed, 7 failed, 1 warning in 13.32s 鈿狅笍
```

| 娴嬭瘯鍒嗙被 | 鏂囦欢鏁?| 閫氳繃 | 澶辫触 |
|----------|--------|------|------|
| **濂戠害娴嬭瘯** | 81 | ~650 | 6 |
| **E2E 娴嬭瘯** | 4 | ~47 | 1 |
| **鍚堣** | 89 | **697** | **7** |

### 4.2 澶辫触娴嬭瘯鍒嗘瀽

| # | 娴嬭瘯 | 澶辫触鍘熷洜 | 鏍瑰洜鍒嗙被 | 寤鸿淇 |
|---|------|---------|---------|---------|
| 1 | `test_avatar_contract::test_t_avatar_01_invalid_tag_and_free_action_attempt_are_rejected` | 鏂█ `assertFalse(invalid.success)` 澶辫触鈥斺€旀湭鐭?tag 鐜板湪杩斿洖 `success=True`锛圖EGRADED锛?| **棰勬湡琛屼负鍙樺寲**锛圱ASK-0082锛?| 鏇存柊鏂█锛氭鏌?`status == DEGRADED` 鑰岄潪 `success == False` |
| 2 | `test_wechat_vts_voice_e2e::test_15_vts_invalid_emotion_rejected_in_e2e` | 鍚屼笂鈥斺€擡2E 灞傛柇瑷€ `assertFalse(result.success)` | **棰勬湡琛屼负鍙樺寲**锛圱ASK-0082锛?| 鍚屼笂 |
| 3 | `test_model_plane_release_contract::test_invalid_milestone_rejected` | `build_release_checklist()` 瀵规棤鏁?milestone raise `ValueError` 鑰岄潪杩斿洖 `STATUS_FAILED` | **娴嬭瘯棰勬湡涓嶅尮閰?* | 娴嬭瘯搴斾娇鐢?`assertRaises(ValueError)` |
| 4 | `test_sandbox_docker_contract::test_docker_backend_degrades_to_subprocess` | `mock.patch("host_brain.airlock.sandbox.is_docker_available")` 澶辫触鈥斺€斿嚱鏁板湪寤惰繜瀵煎叆涓粠 `docker_sandbox` 瀵煎叆 | **Mock 璺緞閿欒** | Mock 璺緞鏀逛负 `host_brain.airlock.docker_sandbox.is_docker_available` |
| 5 | `test_sandbox_docker_contract::test_docker_backend_degrades_audit_logged` | 鍚屼笂 | **Mock 璺緞閿欒** | 鍚屼笂 |
| 6 | `test_vts_reconnect_contract::test_reconnect_restores_neutral` | `last_event["neutral_restored"]` 涓?`False`鈥斺€旈噸杩為€昏緫鏈纭缃?neutral 鎭㈠鏍囧織 | **瀹炵幇缁嗚妭缂哄け** | `vts_driver.py` 閲嶈繛鎴愬姛鍚庨渶璁剧疆 `neutral_restored=True` |
| 7 | `test_vts_reconnect_contract::test_trigger_reconnect_idempotent` | 绗簩娆?trigger 杩斿洖 `"reconnect started"` 鑰岄潪 `"reconnect already in progress"` | **骞傜瓑妫€鏌ョ己澶?* | `trigger_reconnect()` 闇€妫€鏌?`_reconnecting` 鏍囧織 |

### 4.3 鏂板娴嬭瘯瑕嗙洊锛圱ASK-0076~0083锛?
| 娴嬭瘯缂栧彿 | 娴嬭瘯鏂囦欢 | 鏂█鐐?| 瑕嗙洊鍩?|
|---------|---------|--------|-------|
| T-VTS-Degraded-01 | `test_vts_degraded_contract.py` | 36 | VTS DEGRADED 闄嶇骇 |
| T-ASR-Recovery-01 | `test_asr_recovery_contract.py` | 25+ | ASR 璁惧鎭㈠ |
| T-VTS-Reconnect-01 | `test_vts_reconnect_contract.py` | 20+ | VTS 鑷姩閲嶈繛 |
| T-RPC-Event-01 | `test_rpc_event_contract.py` | 15+ | Edge鈫扝ost 浜嬩欢涓婃姤 |
| T-Sandbox-Docker-01 | `test_sandbox_docker_contract.py` | 21 | Docker 瀹瑰櫒闅旂 |
| T-Timeline-Perf-01 | `test_timeline_perf_contract.py` | 10+ | 鏃堕棿绾?SQL 浼樺寲 |
| T-Audit-06 | `test_model_control_audit_contract.py` | 10 | model_control 瀹¤ |
| T-Flow-03 | `test_model_plane_release_contract.py` | 5 | 鍙戝竷闂ㄧ |
| T-Drill-02 | `test_orch_drill_contract.py` | 9 | 缂栨帓鏁呴殰娉ㄥ叆 |

### 4.4 Fixture 浣撶郴

```
208 涓?Fixture JSON 鏂囦欢
```

瑕嗙洊鍩燂細airlock / audit / bond / canon / channels / chat / ci / cold_start / config / prompt_context / drill / e2e / governor_output / governor_rate_limit / governor_rules / intent / llm / llm_gateway / mcp / memory / migrations / observability / orchestration / qa / release / replay_logs / retrieval / services / tokens / tools / vault / vision / voice / web_ui

---

## 馃敀 浜斻€佸畨鍏ㄥ璁?
### 5.1 涓嶅彉閲忓悎瑙勬壂鎻?
| 涓嶅彉閲?| 妫€鏌ラ」 | 缁撴灉 |
|--------|--------|------|
| **I1 鍗曞悜閾?* | 鎼滅储 `from host_brain.skills.impl` 鏃佽矾瀵煎叆 | 鉁?浠?`impl/__init__.py` 鍐呴儴娉ㄥ唽锛屾棤鏃佽矾 |
| **I6 Token 蹇呮** | 鎼滅储 Skill 鎵ц璺緞 bypass | 鉁?鎵€鏈夋墽琛岀粡 `loader.py` Token 鏍￠獙 |
| **I8 瀹¤寮哄埗** | 鎼滅储鎵ц鏃犲璁″啓鍏ヨ矾寰?| 鉁?鎵€鏈?`_log_to_table` 閫氳矾鍙揪 |
| **I9 鎷掔粷蹇呭甫鐮?* | 鎼滅储 REJECT 鏃?reason_code | 鉁?`validate_governor_output` 鎷︽埅 |
| **I11 涓嶄吉鎵ц** | 鎼滅储 `print("宸叉墽琛?)` 绛?| 鉁?QA 鎷︽埅鍣?+ 姝ｅ垯鍖归厤瑕嗙洊 |

### 5.2 鏁忔劅淇℃伅绠＄悊

| 妫€鏌ラ」 | 缁撴灉 |
|--------|------|
| 瑁?`except:` 鎹曡幏 | 鉁?0 澶勨€斺€攈ost_brain + edge_client 鍏ㄩ儴浣跨敤鍏蜂綋寮傚父绫诲瀷 |
| `TODO/FIXME/HACK` 閬楃暀 | 鉁?0 澶勨€斺€攈ost_brain + edge_client 鐢熶骇浠ｇ爜骞插噣 |
| 纭紪鐮佸瘑閽?瀵嗙爜 | 鉁?0 澶勨€斺€斿叏閮ㄩ€氳繃 env + key_profiles 绠＄悊 |
| `print()` 璋冭瘯杈撳嚭 | 鈿狅笍 1 澶勶紙`main.py:908`锛夆€斺€斿簲鏇挎崲涓?logger |
| SQL 娉ㄥ叆椋庨櫓 | 鈿狅笍 浣庨闄┾€斺€旇〃鍚?f-string 鎻掑€兼潵鑷唴閮ㄥ父閲忥紙8 澶勶級锛屾棤琛ㄥ悕鐧藉悕鍗曟牎楠?|
| 寰幆寮曠敤 | 鉁?0 澶勨€斺€擿web_ui.py` 浣跨敤寤惰繜瀵煎叆姝ｇ‘瑙勯伩 |
| DeprecationWarning | 鈿狅笍 1 澶勨€斺€擿vts_driver.py:685` `asyncio.get_event_loop()` 鍦?Python 3.10+ 宸插純鐢?|

### 5.3 绫诲瀷瀹夊叏

| 妫€鏌ラ」 | 缁撴灉 |
|--------|------|
| 缂哄皯杩斿洖绫诲瀷鏍囨敞鐨勫叕寮€鍑芥暟 | 3 澶勶紙`fastapi_app.py` 2 涓?async handler + `graph_executor.py` 1 涓柟娉曪級 |
| `from __future__ import annotations` | 鉁?鎵€鏈夋牳蹇冩ā鍧楀惎鐢?|
| `dataclass(frozen=True)` 涓嶅彲鍙?| 鉁?鎵€鏈夋牳蹇冩暟鎹被浣跨敤 frozen |

---

## 馃搱 鍏€佷唬鐮佽川閲忓害閲?
### 6.1 妯″潡澶嶆潅搴︽帓鍚嶏紙鎸夎鏁帮級

| 鎺掑悕 | 鏂囦欢 | 琛屾暟 | 澶嶆潅搴﹁瘎绾?| 寤鸿 |
|------|------|------|-----------|------|
| 1 | `audit/sqlite_ledger.py` | 1,570 | 馃煛 涓?| 26 寮犺〃闆嗕腑绠＄悊锛岃亴璐ｅ崟涓€浣嗕綋閲忓ぇ锛涘彲鎸夎〃鏃忔媶鍒?|
| 2 | `edge_client/senses/asr.py` | 1,028 | 馃煛 涓?| +璁惧鎭㈠閫昏緫鍚庣獊鐮村崈琛?|
| 3 | `retrieval/reranker.py` | 967 | 馃煛 涓?| 澶?provider 璺敱 + 褰掍竴鍖栭€昏緫瀵嗛泦 |
| 4 | `app/main.py` | 909 | 馃煛 涓?| 鎶ュ憡鐢熸垚鍑芥暟瀵嗛泦 |
| 5 | `retrieval/embedding.py` | 835 | 馃煝 浣?| 缁撴瀯娓呮櫚 |
| 6 | `core/services.py` | 763 | 馃煝 浣?| 鈥?|
| 7 | `edge_client/actuators/vts_driver.py` | 693 | 馃煝 浣?| +閲嶈繛鍚庣獊鐮?600 琛?|
| 8 | `app/fastapi_app.py` | 626 | 馃煝 浣?| 鈥?|
| 9 | `governor/rate_limit_store.py` | 600 | 馃煝 浣?| 3 鍚庣 + 闄嶇骇鍣紝缁撴瀯鑹ソ |
| 10 | `observability/cross_timeline.py` | 500 | 馃煝 浣?| 鈥?|

### 6.2 娴嬭瘯瑕嗙洊搴?
| 搴﹂噺 | 鍊?|
|------|---|
| 婧愭枃浠舵暟锛坔ost_brain + edge_client锛?| 95 |
| 娴嬭瘯鏂囦欢鏁帮紙contract + e2e + helpers锛?| 89 |
| 娴嬭瘯/婧愭枃浠舵瘮 | **0.94**锛堜紭绉€锛?|
| 娴嬭瘯浠ｇ爜琛?涓氬姟浠ｇ爜琛?| 17,749 / 27,696 = **0.64**锛堣壇濂斤級 |
| Fixture 鏂囦欢鏁?| 208 |
| 閿欒鐮佸父閲忔暟 | 144 |
| 瀹¤琛ㄦ暟 | 26 |

---

## 鈿狅笍 涓冦€佸彂鐜伴」姹囨€?
### 馃敶 P0 鈥?闃绘柇鎬ч棶棰橈紙0 椤癸級

**鏃犻樆鏂€ч棶棰樸€?* 鏍稿績涓嶅彉閲忔棤杩濊锛? 椤规祴璇曞け璐ュ潎涓哄彲鏄庣‘淇鐨?P1 绾ч棶棰樸€?
### 馃煛 P1 鈥?闇€璺熻繘锛?3 椤癸級

| # | 妯″潡 | 鍙戠幇 | 寤鸿 |
|---|------|------|------|
| F-01 | `airlock/sandbox.py` | subprocess 鍚庣鏃犲鍣ㄩ殧绂?| 鐢熶骇鐜浣跨敤 Docker 鍚庣 |
| F-02 | `airlock/sandbox.py` | Windows/Linux 瓒呮椂琛屼负宸紓 | Linux 閮ㄧ讲鐜鍥炲綊楠岃瘉 |
| F-04 | `governor/rate_limit_store.py` | Redis 鍚庣鏈湪鐪熷疄鐜楠岃瘉 | 鏈?Redis 瀹炰緥鏃堕泦鎴愭祴璇?|
| F-05 | `audit/sqlite_ledger.py` | WAL 妯″紡鍦?NFS 涓婁笉鏀寔 | 閮ㄧ讲鏂囨。鏍囨敞瀛樺偍浠嬭川瑕佹眰 |
| F-06 | `audit/sqlite_ledger.py` | `_table_has_column` 鏃犵紦瀛?| 澧炲姞琛ㄧ粨鏋勭紦瀛?|
| F-07 | Edge ASR | CPU 闄嶇骇寤惰繜鍙兘涓嶅彲鎺ュ彈 | 鐩爣 Edge 璁惧瀹炴祴 |
| F-08 | Edge TTS+ASR | GPU VRAM 鍙犲姞 OOM | Edge 璁惧鍘嬪姏娴嬭瘯 |
| F-11 | E2E 娴嬭瘯 | E2E 妯℃嫙 Edge 浜嬩欢鑰岄潪鐪熷疄 RPC | 鐪熷疄 Edge 鐜闆嗘垚楠岃瘉 |
| F-12 | `requirements.txt` | 缂哄皯 `redis`锛堝彲閫変緷璧栵級璇存槑 | 琛ュ厖鍙€変緷璧栨枃妗?|
| **F-21** | 娴嬭瘯鍥炲綊 | TASK-0082 VTS DEGRADED 瀵艰嚧 2 涓棫娴嬭瘯鏂█澶辫触 | 鏇存柊鏂█锛氭鏌?`status == DEGRADED` |
| **F-22** | 娴嬭瘯鍥炲綊 | `test_sandbox_docker_contract` mock 璺緞閿欒锛? 椤瑰け璐ワ級 | Mock 鏀逛负 `docker_sandbox.is_docker_available` |
| **F-23** | 娴嬭瘯鍥炲綊 | `test_vts_reconnect_contract` 閲嶈繛/骞傜瓑鏂█澶辫触锛? 椤癸級 | `vts_driver.py` 琛ュ厖 `neutral_restored` 鍜屽箓绛夋鏌?|
| **F-24** | 娴嬭瘯鍥炲綊 | `test_model_plane_release_contract` 寮傚父澶勭悊涓嶅尮閰嶏紙1 椤癸級 | 娴嬭瘯鏀逛负 `assertRaises(ValueError)` |

### 馃煝 P2 鈥?宸茬煡杈圭晫/寤鸿鏀硅繘锛?3 椤癸級

| # | 妯″潡 | 鍙戠幇 | 寤鸿 |
|---|------|------|------|
| F-13 | `app/main.py:908` | 瑁?`print()` 璋冪敤 | 鏇挎崲涓?`logger.info()` |
| F-14 | `graph_executor.py:43` | 缂哄皯杩斿洖绫诲瀷鏍囨敞 | 琛ュ厖 `-> None` 鎴栧叿浣撹繑鍥炵被鍨?|
| F-15 | `fastapi_app.py:582,605` | 2 涓?handler 缂鸿繑鍥炵被鍨嬫爣娉?| 琛ュ厖绫诲瀷鏍囨敞 |
| F-16 | `audit/sqlite_ledger.py` | 琛ㄥ悕 f-string 鎻掑€硷紙8 澶勶級 | 澧炲姞琛ㄥ悕鐧藉悕鍗曟牎楠?|
| F-18 | SQLite 鍗曟枃浠?| 楂樺苟鍙戣鍐欏彲鑳界摱棰?| 涓湡鑰冭檻 PostgreSQL 鎴栧彧璇诲壇鏈?|
| F-19 | WAL checkpoint | 闀挎湡杩愯 WAL 鏂囦欢澧為暱 | 鐩戞帶 WAL 澶у皬 + 瀹氭椂 checkpoint |
| F-20 | `sandbox.py` | 浠呮敮鎸?Python 璇█ | 鎸夐渶鎵╁睍 JS/Shell |
| **F-25** | `vts_driver.py:685` | `asyncio.get_event_loop()` DeprecationWarning锛圥ython 3.10+锛?| 鏀逛负 `asyncio.get_running_loop()` |
| **F-26** | Docker 娌欑 | Docker 闀滃儚鏋勫缓渚濊禆缃戠粶锛岀绾跨幆澧冨彲鑳藉け璐?| 鏀寔棰勬瀯寤洪暅鍍忔湰鍦板姞杞?|
| **F-27** | Docker 娌欑 | nsjail 澶囬€夊湪闈?Linux 涓嶅彲鐢?| 閮ㄧ讲鏂囨。鏍囨敞骞冲彴瑕佹眰 |

> **鍙樺寲璇存槑**锛氬師 F-03锛坈ross_timeline 鍏ㄩ噺鍔犺浇锛夊凡鐢?TASK-0078 `fetch_by_time_range` 瑙ｅ喅鈥斺€旂Щ闄ゃ€傚師 F-09锛圴TS 鏃犻噸杩烇級宸茬敱 TASK-0080 瑙ｅ喅鈥斺€旂Щ闄ゃ€傚師 F-10锛圗dge鈫扝ost RPC 鏈疄鐜帮級宸茬敱 TASK-0079 `event_reporter.py` 瑙ｅ喅鈥斺€旂Щ闄ゃ€傚師 F-17锛圴TS 涓嶆敮鎸?DEGRADED锛夊凡鐢?TASK-0082 瑙ｅ喅鈥斺€旂Щ闄ゃ€傛柊澧?F-21~F-27 涓烘湰娆℃壂鎻忔柊鍙戠幇銆?
---

## 馃搳 鍏€佺患鍚堣瘎鍒?
| 缁村害 | 璇勫垎 | 杈冧笂娆?| 璇存槑 |
|------|------|--------|------|
| **鏋舵瀯璁捐** | 猸愨瓙猸愨瓙猸?| 鈥?| Host-Edge 鍒嗕綋 + 鍗曞悜閾?+ 绁ㄦ嵁鍖栨墽琛?|
| **浠ｇ爜璐ㄩ噺** | 猸愨瓙猸愨瓙猸?| 鈥?| 0 瑁?except / 0 TODO / 0 纭紪鐮佸瘑閽?/ frozen dataclass |
| **瀹夊叏鎬?* | 猸愨瓙猸愨瓙猸?| 猬嗭笍 | Docker 瀹瑰櫒闅旂鍚庣宸插氨浣?|
| **鍙璁℃€?* | 猸愨瓙猸愨瓙猸?| 猬嗭笍 | 26 寮犲璁¤〃 + 144 涓敊璇爜 + model_control 瀹¤ |
| **鍙祴璇曟€?* | 猸愨瓙猸愨瓙鈽?| 猬囷笍 | 697 閫氳繃 / 7 澶辫触锛堟祴璇曟柇瑷€闇€鏇存柊閫傞厤琛屼负鍙樺寲锛?|
| **鍔熻兘瀹屾垚搴?* | 猸愨瓙猸愨瓙猸?| 猬嗭笍 | TASK-0000~0083 鍏ㄩ儴瀹屾垚 |
| **鍙繍琛屾€?* | 猸愨瓙猸愨瓙猸?| 猬嗭笍 | FastAPI + Docker sandbox + Edge 閲嶈繛/鎭㈠ |
| **鍙娴嬫€?* | 猸愨瓙猸愨瓙猸?| 猬嗭笍 | 闈㈡澘 + 璺ㄧ鏃堕棿绾?+ 鏃堕棿绐楀彛鏌ヨ |

### 鎬讳綋璇勫垎锛?*猸愨瓙猸愨瓙猸?/ 5锛堝伐绋嬫垚鐔熷害鏋侀珮锛? 椤规祴璇曞洖褰掗渶淇锛?*

---

## 馃搵 涔濄€佷笌涓婃瀹￠槄瀵规瘮锛堟敼杩涙€荤粨锛?
涓婃瀹￠槄锛堢浜岀増锛夎瘑鍒簡 20 涓彂鐜伴」锛團-01~F-20锛夈€傛湰娆?TASK-0076~0083 鐨勬墽琛岃В鍐充簡鍏朵腑 4 椤癸細

| 涓婃鍙戠幇 | 浼樺厛绾?| 鐘舵€?| 瑙ｅ喅 TASK |
|---------|--------|------|----------|
| F-03 Host 瀹¤琛ㄥ叏閲忓唴瀛樺姞杞?| P1 | 鉁?**宸茶В鍐?* | TASK-0078锛坒etch_by_time_range锛?|
| F-09 VTS WebSocket 鏃犺嚜鍔ㄩ噸杩?| P1 | 鉁?**宸茶В鍐?* | TASK-0080锛堟寚鏁伴€€閬块噸杩烇級 |
| F-10 Edge鈫扝ost RPC 涓婃姤鏈疄鐜?| P1 | 鉁?**宸茶В鍐?* | TASK-0079锛坋vent_reporter.py锛?|
| F-17 Host VTS 涓嶆敮鎸?DEGRADED | P2 | 鉁?**宸茶В鍐?* | TASK-0082锛圫TATUS_DEGRADED锛?|

鍚屾椂锛孴ASK-0076~0083 鏂板浜嗕互涓嬭兘鍔涳細

| 鏂板鑳藉姏 | TASK | 璇存槑 |
|---------|------|------|
| Docker 瀹瑰櫒绾ф矙绠遍殧绂?| TASK-0077 | `docker_sandbox.py` + subprocess 鑷姩闄嶇骇 |
| 璺ㄧ鏃堕棿绾?SQL 浼樺寲 | TASK-0078 | `fetch_by_time_range` + 鍒嗛〉鏌ヨ |
| Edge鈫扝ost 浜嬩欢鎵归噺涓婃姤 | TASK-0079 | `event_reporter.py` + 缂撳瓨琛ュ彂 |
| VTS WebSocket 鑷姩閲嶈繛 | TASK-0080 | 鎸囨暟閫€閬?+ neutral 鎭㈠ + 鍋ュ悍蹇収 |
| ASR 楹﹀厠椋庤澶囪嚜鍔ㄦ仮澶?| TASK-0081 | 璁惧鍊欓€夋瀯寤?+ 鎭㈠寰幆 |
| Host VTS DEGRADED 鐘舵€?| TASK-0082 | neutral fallback + 闄嶇骇瀹¤ |
| 閮ㄧ讲鏂囨。鏇存柊 | TASK-0083 | ENV 娓呭崟 v1.1 |
| 缂栨帓涓绘帶鍏ㄩ摼璺祴璇?| TASK-0057~0063 | model_plane/param_hash/node_selector/governance/audit/release/drill |

---

## 馃弳 鍗併€佺粨璁?
**Natsume Ai 鍦?TASK-0000~0083 鍏ㄩ儴瀹屾垚鍚庯紝宸茶揪鍒?鍏ㄥ姛鑳介棴鐜?鐨勫伐绋嬫垚鐔熷害銆?*

- 鉁?**45,445 琛屼唬鐮?*锛?7,696 涓氬姟 + 17,749 娴嬭瘯锛夊叏閮ㄦ湁鏈虹粍缁?- 鉁?**697 椤规祴璇曢€氳繃**锛屽叏閾捐矾瑕嗙洊
- 鈿狅笍 **7 椤规祴璇曞け璐?*鈥斺€斿潎涓?TASK-0082 DEGRADED 琛屼负鍙樺寲寮曡捣鐨勬柇瑷€杩囨椂锛? 椤癸級+ Mock 璺緞閿欒锛? 椤癸級+ 閲嶈繛缁嗚妭缂哄け锛? 椤癸級锛屽睘浜庡彲蹇€熶慨澶嶇殑鍥炲綊闂
- 鉁?**26 寮犲璁¤〃 + 144 涓敊璇爜甯搁噺 + 38 涓?SQL 杩佺Щ鑴氭湰** 瑕嗙洊鍏ㄩ摼璺?- 鉁?鏍稿績涓嶅彉閲忥紙鍗曞悜閾?Token 蹇呮/瀹¤寮哄埗/鎷掔粷蹇呭甫鐮?涓嶄吉鎵ц锛夊叏閮ㄩ€氳繃闈欐€?杩愯鏃堕獙璇?- 鉁?Docker 瀹瑰櫒绾ф矙绠遍殧绂诲凡灏变綅锛圱ASK-0077锛?- 鉁?Edge 璁惧鎭㈠闊ф€у凡澧炲己锛歏TS 鑷姩閲嶈繛 + ASR 璁惧鎭㈠ + 浜嬩欢琛ュ彂
- 鈿狅笍 鍓╀綑 13 涓?P1 鍙戠幇椤癸紙鍚?7 椤规祴璇曞洖褰掍慨澶嶏級
- 鈿狅笍 鍓╀綑 13 涓?P2 寤鸿椤癸紙浠ｇ爜璐ㄩ噺寰皟 + DeprecationWarning + 娌欑鎵╁睍锛?
**椋庨櫓璇︽儏宸茶褰曞湪** [risk_registry.md](../60_trace/risk_registry.md) **涓紙v2.0锛岃鐩?TASK-0065~0083锛夈€?*

---

> 鏈姤鍛婂熀浜庡叏閲忔簮鐮侀€愭ā鍧楀闃?+ 704 椤规祴璇曞叏閲忚繍琛?+ 涓嶅彉閲忛潤鎬佹壂鎻?+ 瀹夊叏瀹¤鐢熸垚銆?
