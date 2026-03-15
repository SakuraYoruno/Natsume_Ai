鍙椾紬锛欳ODEX
鏍囬锛氬叏闈唬鐮佸闃呮姤鍛婏紙绗洓鐗堬紝2026-03-03锛?
# 馃攳 Natsume Ai 鍏ㄩ潰浠ｇ爜瀹￠槄鎶ュ憡

> **鐢熸垚鏃ユ湡**锛?026-03-03锛堢鍥涚増锛屽惈 TASK-0000~0120 / M1~M10-A 鍏ㄩ儴浜や粯锛?> **瀹￠槄鑼冨洿**锛歨ost_brain/銆乪dge_client/銆乼ests/銆乧onfigs/銆乨ocs/
> **瀹￠槄鏂规硶**锛氶€愭ā鍧楁簮鐮侀槄璇?+ 濂戠害娴嬭瘯鍏ㄩ噺杩愯 + 鏋舵瀯涓€鑷存€ф鏌?+ 涓嶅彉閲忛潤鎬佹壂鎻?+ 瀹夊叏瀹¤

---

## 馃搳 涓€銆侀」鐩€昏

| 鎸囨爣 | 鏁版嵁 | 杈冧笂娆″彉鍖栵紙vs 绗笁鐗堬級 |
|------|------|-----------|
| **Host Brain 婧愭枃浠?* | 94 涓?`.py`锛堝惈 `__init__`锛?| +16锛圡8~M10-A 鏂板锛?|
| **Edge Client 婧愭枃浠?* | 26 涓?`.py` | +9 |
| **Host Brain 浠ｇ爜琛?* | 26,536 琛?| +4,210 |
| **Edge Client 浠ｇ爜琛?* | 6,816 琛?| +1,446 |
| **娴嬭瘯浠ｇ爜琛?* | 24,175 琛?| +6,426 |
| **鎬讳唬鐮佽** | **57,527 琛?* | +12,082 |
| **濂戠害娴嬭瘯鏂囦欢** | 120 涓紙鍚?integration/e2e锛?| +31 |
| **娴嬭瘯鏂█鐐?* | **1041 閫氳繃 / 0 澶辫触** 鉁?| +344 閫氳繃 / **-7 澶辫触**锛堝叏閮ㄤ慨澶嶏級 |
| **娴嬭瘯 Fixture 鏂囦欢** | 257 涓?`.json` | +49 |
| **瀹¤鏃ュ織琛?* | 26 寮?| 鈥?|
| **閿欒鐮侊紙reason_code锛?* | 148 涓父閲?| +4锛圡10-A 鏂板锛?|
| **SQL 杩佺Щ鑴氭湰** | 19 涓紙鍚?bond cypher锛?| +2锛圡9 鏂板 0015/0016锛?|
| **Git 鎻愪氦**锛堝叏 TASK锛?| TASK-0000~0120 鍏ㄩ儴瀹屾垚 | 鈥?|

---

## 馃彈锔?浜屻€佹灦鏋勫叏鏅紙M10-A 鏇存柊锛?
```
鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€ Host Brain锛圥ython 3.12+锛?鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?鈹?                                                                       鈹?鈹? 鈹屸攢 app/ 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?  鈹?鈹? 鈹? fastapi_app.py      鈫?HTTP 鍏ュ彛 + 涓棿浠?+ 璺敱娉ㄥ唽            鈹?  鈹?鈹? 鈹? main.py             鈫?鍚姩鎶ュ憡 + 鍋ュ悍鑱氬悎 + 9 鑺傜偣鎷撴墤         鈹?  鈹?鈹? 鈹? config.py           鈫?鍒嗗眰閰嶇疆 + 鑴辨晱蹇収 + diff              鈹?  鈹?鈹? 鈹? web_ui.py           鈫?鍙娴嬮潰鏉?API                          鈹?  鈹?鈹? 鈹? canon_routes.py 鈫?NEW锛圡10-A锛塁anon 娌荤悊 REST API锛? 绔偣锛?  鈹?  鈹?鈹? 鈹? static/panel.html   鈫?閾捐矾鍙鍖栭潰鏉?                         鈹?  鈹?鈹? 鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?  鈹?鈹?                                                                       鈹?鈹? 鈹屸攢 memory/ 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?    鈹?鈹? 鈹? canon_world_store.py 鈫?NEW锛圡9锛塛orld Canon SSOT + 鎻愭娴佺▼  鈹?    鈹?鈹? 鈹?   + reject_proposal() 鈫?NEW锛圡10-A锛?                        鈹?    鈹?鈹? 鈹? canon_style_store.py                                          鈹?    鈹?鈹? 鈹? episodic.py / chroma_client.py                                鈹?    鈹?鈹? 鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?    鈹?鈹?                                                                       鈹?鈹? 鈹屸攢 audit/ 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?    鈹?鈹? 鈹? sqlite_ledger.py锛?,620L锛夆啇 +list_proposals()锛圡10-A锛?      鈹?    鈹?鈹? 鈹? migration_runner.py / retention.py / drill.py                 鈹?    鈹?鈹? 鈹? migrations/0015_world_canon_doc.sql 鈫?NEW锛圡9锛?             鈹?    鈹?鈹? 鈹? migrations/0016_canon_proposal_log.sql 鈫?NEW锛圡9锛?          鈹?    鈹?鈹? 鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?    鈹?鈹?                                                                       鈹?鈹? + governor/ + integrations/ + airlock/ + channels/ + bond/            鈹?鈹? + retrieval/ + skills/ + tools/ + lsp/ + release/ + core/ + obs/      鈹?鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?
鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€ Edge Client 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?鈹? rpc/voice.py | rpc/event_reporter.py | rpc/resource_gate.py           鈹?鈹? senses/asr.py锛?璁惧鎭㈠锛墊 senses/fallback_llm.py                    鈹?鈹? actuators/speech.py | actuators/tts_engine.py                         鈹?鈹? actuators/vts_driver.py锛?鎸囨暟閫€閬块噸杩烇級                               鈹?鈹? proofs/resource_gate.py | config.py                                   鈹?鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?
Canon 娌荤悊 REST API锛圡10-A 鏂板锛夛細
  GET  /canon/proposals                    鈥?鍒楀嚭鎻愭锛堝垎椤碉級
  POST /canon/proposals                    鈥?鎻愪氦鏂版彁妗?  GET  /canon/proposals/{proposal_id}      鈥?鏌ヨ鎻愭璇︽儏
  POST /canon/proposals/{proposal_id}/approve 鈥?瀹℃牳閫氳繃
  POST /canon/proposals/{proposal_id}/reject  鈥?鎷掔粷鎻愭
  GET  /canon/documents                    鈥?鍒楀嚭 SSOT 蹇収
  GET  /canon/documents/{doc_id}           鈥?鏌ヨ鍗曚釜鏂囨。
```

---

## 馃敩 涓夈€丮8~M10-A 鏂板妯″潡瀹℃煡

### 3.1 M8 杩愯鏃堕泦鎴愰棴鐜紙TASK-0107~0110锛?
| 妯″潡 | 鍙樺寲 | 璇勪环 |
|------|------|------|
| `core/services.py` | ServiceRegistry 鍋ュ悍鑱氬悎 + 9 鑺傜偣鎷撴墤 | 鉁?瀹屾暣 |
| `obs/metrics.py` | MetricsCollector + 寤惰繜鍩虹嚎蹇収 | 鉁?瀹屾暣 |
| `release/release_manifest.py` | generate_m8_manifest + generate_m9_manifest + generate_m10a_manifest | 鉁?瀹屾暣 |
| `scripts/runtime/start_host_side.ps1` | M8/M9 鍋ュ悍鎺㈡祴 + canon_sync 閽╁瓙 | 鉁?瀹屾暣 |

### 3.2 M9 World Canon 闂幆锛圱ASK-0111~0115锛?
| 妯″潡 | 鍙樺寲 | 璇勪环 |
|------|------|------|
| `memory/canon_world_store.py` | WorldCanonStore锛圫SOT + 鐗堟湰鍖?+ 鎻愭娴佺▼锛?| 鉁?瀹屾暣 |
| `audit/migrations/0015_world_canon_doc.sql` | world_canon_doc 琛?| 鉁?瀹屾暣 |
| `audit/migrations/0016_canon_proposal_log.sql` | canon_proposal_log 琛?| 鉁?瀹屾暣 |
| `canon/world/*.json` | 3 涓瀛愭潯鐩紙wc-001/002/003锛?| 鉁?瀹屾暣 |
| `core/qa.py` | QA 鎷︽埅 Canon-as-fact + 閲嶅啓/闄嶇骇 | 鉁?瀹屾暣 |
| `memory/canon_sync.py` | 绱㈠紩鍚屾 + 骞傜瓑鎬?| 鉁?瀹屾暣 |

### 3.3 M10-A Canon 娌荤悊 API锛圱ASK-0117~0120锛夆€?猸愨瓙猸愨瓙猸?
| 鏂囦欢 | 琛屾暟 | 瀹屾垚搴?| 鍙樺寲 |
|------|------|--------|------|
| `app/canon_routes.py` | ~350 | 95% | **NEW锛圡10-A锛?* |
| `memory/canon_world_store.py` | ~380 | 97% | +reject_proposal() |
| `audit/sqlite_ledger.py` | ~1,620 | 96% | +list_proposals() |
| `core/errors.py` | ~155 | 97% | +4 涓?M10-A 閿欒鐮?|
| `app/fastapi_app.py` | ~640 | 96% | +canon_router 娉ㄥ唽 |

**瀹℃煡缁撹锛?*
- 鉁?`canon_routes.py`锛? 涓鐐瑰疄鐜板畬鏁达紝Pydantic 璇锋眰/鍝嶅簲妯″瀷缁撴瀯绋冲畾锛屽惈 `request_id` + `timestamp` 缁熶竴鍝嶅簲缁撴瀯銆?- 鉁?骞傜瓑鎬э細POST /canon/proposals 瀵归噸澶?`proposal_id` 杩斿洖 409 + WCANON_PROPOSAL_DUPLICATE銆?- 鉁?瀹¤涓茶仈锛氭墍鏈夊啓鎿嶄綔 `proposer_node_id` 鍚?`request_id`锛堟牸寮忥細`api.canon_proposals::{request_id}`锛夈€?- 鉁?閿欒鐮佹槧灏勫畬鏁达細400/404/409/422/500 鍧囨湁瀵瑰簲 `reason_code`銆?- 鉁?`reject_proposal()`锛歴tatus 鏀逛负 rejected锛屼笉淇敼 SSOT 鏂囦欢锛孭ermissionError 鑻ラ潪 pending銆?- 鉁?`list_proposals()`锛氬垎椤垫煡璇紝鏀寔 status/canon_kind/target_doc_id 杩囨护锛宭imit 纭檺鍒?100銆?- 鈿狅笍 **鍙戠幇**锛歚_get_canon_store()` 浣跨敤鍏ㄥ眬鍗曚緥锛屽绾跨▼骞跺彂鍒濆鍖栨椂瀛樺湪绔炴€佹潯浠讹紙R118-1锛夈€?- 鈿狅笍 **鍙戠幇**锛歚_get_ledger_from_app()` 寤惰繜瀵煎叆瀛樺湪寰幆寮曠敤椋庨櫓锛圧118-2锛屼笌 web_ui.py 妯″紡涓€鑷达紝褰撳墠鍙帴鍙楋級銆?- 鈿狅笍 **鍙戠幇**锛歚canon_proposal_log.proposal_id` 鏃?UNIQUE 绾︽潫锛岄珮骞跺彂鏃跺箓绛夋鏌ュ彲鑳藉弻鍐欙紙R118-3锛夈€?
---

## 馃И 鍥涖€佹祴璇曚綋绯昏瘎浼帮紙M10-A 鏇存柊锛?
### 4.1 娴嬭瘯鍏ㄩ噺杩愯缁撴灉

```
1041 passed, 0 failed in 15.76s 鉁?```

| 娴嬭瘯鍒嗙被 | 鏂囦欢鏁?| 閫氳繃 | 澶辫触 |
|----------|--------|------|------|
| **濂戠害娴嬭瘯** | ~100 | ~990 | 0 |
| **闆嗘垚娴嬭瘯** | ~10 | ~30 | 0 |
| **E2E 娴嬭瘯** | ~10 | ~21 | 0 |
| **鍚堣** | 120 | **1041** | **0** |

> **瀵规瘮绗笁鐗?*锛? 椤规祴璇曞け璐ワ紙F-21~F-24锛夊凡鍦?M8~M10-A 寮€鍙戣繃绋嬩腑鍏ㄩ儴淇銆?
### 4.2 M9/M10-A 鏂板娴嬭瘯瑕嗙洊

| 娴嬭瘯缂栧彿 | 娴嬭瘯鏂囦欢 | 鏂█鐐?| 瑕嗙洊鍩?|
|---------|---------|--------|-------|
| T-WCanon-04~07 | `test_world_canon_*_contract.py` | 40+ | World Canon SSOT/鍚屾/鍒嗘Ы/Evolution |
| T-Diary-03~04 | `test_diary_*_contract.py` | 15+ | 鏃ヨ drift_suggestion 鍏宠仈 |
| T-Canon-API-01~08 | `test_canon_api_contract.py` | 10 | Canon 娌荤悊 REST API |

---

## 馃敀 浜斻€佸畨鍏ㄥ璁★紙M10-A 鏇存柊锛?
### 5.1 涓嶅彉閲忓悎瑙勬壂鎻?
| 涓嶅彉閲?| 妫€鏌ラ」 | 缁撴灉 |
|--------|--------|------|
| **I1 鍗曞悜閾?* | 鎼滅储 `from host_brain.skills.impl` 鏃佽矾瀵煎叆 | 鉁?浠?`impl/__init__.py` 鍐呴儴娉ㄥ唽锛屾棤鏃佽矾 |
| **I6 Token 蹇呮** | 鎼滅储 Skill 鎵ц璺緞 bypass | 鉁?鎵€鏈夋墽琛岀粡 `loader.py` Token 鏍￠獙 |
| **I8 瀹¤寮哄埗** | 鎼滅储鎵ц鏃犲璁″啓鍏ヨ矾寰?| 鉁?鎵€鏈?`_log_to_table` 閫氳矾鍙揪 |
| **I9 鎷掔粷蹇呭甫鐮?* | 鎼滅储 REJECT 鏃?reason_code | 鉁?`validate_governor_output` 鎷︽埅 |
| **I11 涓嶄吉鎵ц** | 鎼滅储 `print("宸叉墽琛?)` 绛?| 鉁?QA 鎷︽埅鍣?+ 姝ｅ垯鍖归厤瑕嗙洊 |
| **Canon 涓绘潈** | 鎼滅储 SSOT 鏃佽矾鍐欏叆 | 鉁?鎵€鏈夊啓鍏ョ粡 `upsert_document()` + `_write_lock` |

### 5.2 M10-A 鏂板瀹夊叏妫€鏌?
| 妫€鏌ラ」 | 缁撴灉 |
|--------|------|
| Canon API 鍐欐搷浣滃璁′覆鑱?| 鉁?鎵€鏈夊啓鎿嶄綔 proposer_node_id 鍚?request_id |
| Canon API 骞傜瓑鎬?| 鉁?閲嶅 proposal_id 杩斿洖 409锛屼笉閲嶅鍐欏叆 |
| Canon API 瀹屾垚鎬ц〃杩版嫤鎴?| 鉁?submit_proposal 璋冪敤 `_contains_execution_claim()` 鎷︽埅 |
| SSOT 鏂囦欢鍐欏叆涓茶鍖?| 鉁?`_write_lock` 淇濊瘉鍗曡繘绋嬩覆琛屽啓鍏?|
| proposal_id UNIQUE 绾︽潫 | 鈿狅笍 鏁版嵁搴撳眰鏃?UNIQUE 绾︽潫锛圧118-3锛夛紝渚濊禆搴旂敤灞傚箓绛夋鏌?|

---

## 鈿狅笍 鍏€佸彂鐜伴」姹囨€伙紙M10-A 鏇存柊锛?
### 馃敶 P0 鈥?闃绘柇鎬ч棶棰橈紙0 椤癸級

**鏃犻樆鏂€ч棶棰樸€?*

### 馃煛 P1 鈥?闇€璺熻繘锛?6 椤癸級

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
| **F-28** | `app/canon_routes.py` | `_get_canon_store()` 鍏ㄥ眬鍗曚緥绔炴€侊紙R118-1锛?| 澶氳繘绋嬮儴缃叉椂鏀逛负渚濊禆娉ㄥ叆 |
| **F-29** | `app/canon_routes.py` | `_get_ledger_from_app()` 寤惰繜瀵煎叆寰幆寮曠敤椋庨櫓锛圧118-2锛?| 鍚庣画閲嶆瀯涓哄弬鏁颁紶閫?|
| **F-30** | `audit/migrations/0016_canon_proposal_log.sql` | `proposal_id` 鏃?UNIQUE 绾︽潫锛圧118-3锛?| 娣诲姞 UNIQUE 绾︽潫 migration |
| **F-31** | `memory/canon_world_store.py` | Evolution approve 鏃犲洖婊氭満鍒讹紙R114-1锛?| 瀹炵幇 `rollback_proposal()` |
| **F-32** | `scripts/runtime/start_host_side.ps1` | canon_sync DEGRADED 闈為樆鏂彲鑳芥帺鐩栧悓姝ュけ璐ワ紙R112-1锛?| 鐩戞帶鍛婅瑕嗙洊 STARTUP_CANON_SYNC_DEGRADED |
| **F-33** | `core/qa.py` | prompt_context 鍒嗘Ы娉ㄥ叆澶辫触闄嶇骇涓虹┖妲斤紙R113-1锛?| 鐩戞帶鍛婅瑕嗙洊 WCANON_SLOT_VIOLATION |
| **F-34** | `memory/canon_world_store.py` | SSOT 鏂囦欢骞跺彂鍐欏叆鏃犳枃浠堕攣锛圧111-1锛?| 澶氳繘绋嬮儴缃叉椂寮曞叆鏂囦欢閿?|

### 馃煝 P2 鈥?宸茬煡杈圭晫/寤鸿鏀硅繘锛?5 椤癸級

| # | 妯″潡 | 鍙戠幇 | 寤鸿 |
|---|------|------|------|
| F-13 | `app/main.py:908` | 瑁?`print()` 璋冪敤 | 鏇挎崲涓?`logger.info()` |
| F-14 | `graph_executor.py:43` | 缂哄皯杩斿洖绫诲瀷鏍囨敞 | 琛ュ厖 `-> None` |
| F-15 | `fastapi_app.py:582,605` | 2 涓?handler 缂鸿繑鍥炵被鍨嬫爣娉?| 琛ュ厖绫诲瀷鏍囨敞 |
| F-16 | `audit/sqlite_ledger.py` | 琛ㄥ悕 f-string 鎻掑€硷紙8 澶勶級 | 澧炲姞琛ㄥ悕鐧藉悕鍗曟牎楠?|
| F-18 | SQLite 鍗曟枃浠?| 楂樺苟鍙戣鍐欏彲鑳界摱棰?| 涓湡鑰冭檻 PostgreSQL 鎴栧彧璇诲壇鏈?|
| F-19 | WAL checkpoint | 闀挎湡杩愯 WAL 鏂囦欢澧為暱 | 鐩戞帶 WAL 澶у皬 + 瀹氭椂 checkpoint |
| F-20 | `sandbox.py` | 浠呮敮鎸?Python 璇█ | 鎸夐渶鎵╁睍 JS/Shell |
| F-25 | `vts_driver.py:685` | `asyncio.get_event_loop()` DeprecationWarning | 鏀逛负 `asyncio.get_running_loop()` |
| F-26 | Docker 娌欑 | Docker 闀滃儚鏋勫缓渚濊禆缃戠粶 | 鏀寔棰勬瀯寤洪暅鍍忔湰鍦板姞杞?|
| F-27 | Docker 娌欑 | nsjail 澶囬€夊湪闈?Linux 涓嶅彲鐢?| 閮ㄧ讲鏂囨。鏍囨敞骞冲彴瑕佹眰 |
| **F-35** | `app/canon_routes.py` | approve 绔偣涓ゆ DB 鎿嶄綔闈炰簨鍔★紙R119-1锛?| 澶氳繘绋嬮儴缃叉椂浣跨敤浜嬪姟鍖呰９ |
| **F-36** | `app/canon_routes.py` | reject 瀹¤鍐欏叆澶辫触鍙褰?warning锛圧119-2锛?| 鍚庣画绾冲叆浜嬪姟纭繚鍘熷瓙鎬?|
| **F-37** | `release/manifests/m10a_release.json` | 鏈攣瀹?canon_world_store.py 鍜?sqlite_ledger.py hash锛圧120-1锛?| 鎵╁睍 manifest 閿佸畾鏇村鏍稿績鏂囦欢 |
| **F-38** | `memory/canon_world_store.py` | `list_proposals()` 鍒嗛〉浣跨敤 LIMIT/OFFSET锛屾繁椤电爜鎬ц兘閫€鍖栵紙R112-2锛?| 鍚庣画鏀逛负 cursor-based 鍒嗛〉 |
| **F-39** | `memory/episodic.py` | 鏃ヨ source_intent_id 璺ㄨ〃鍏宠仈鍙兘鏂摼锛圧115-1锛?| 澧炲姞 intent_id 瀹屾暣鎬ф牎楠?|

---

## 馃搳 涓冦€佺患鍚堣瘎鍒嗭紙M10-A 鏇存柊锛?
| 缁村害 | 璇勫垎 | 杈冧笂娆?| 璇存槑 |
|------|------|--------|------|
| **鏋舵瀯璁捐** | 猸愨瓙猸愨瓙猸?| 鈥?| Host-Edge 鍒嗕綋 + 鍗曞悜閾?+ 绁ㄦ嵁鍖栨墽琛?+ Canon 娌荤悊 API |
| **浠ｇ爜璐ㄩ噺** | 猸愨瓙猸愨瓙猸?| 鈥?| 0 瑁?except / 0 TODO / 0 纭紪鐮佸瘑閽?/ frozen dataclass |
| **瀹夊叏鎬?* | 猸愨瓙猸愨瓙猸?| 鈥?| Docker 瀹瑰櫒闅旂 + Canon 涓绘潈淇濇姢 + 瀹¤涓茶仈 |
| **鍙璁℃€?* | 猸愨瓙猸愨瓙猸?| 猬嗭笍 | 26 寮犲璁¤〃 + 148 涓敊璇爜 + Canon 娌荤悊 API 瀹¤ |
| **鍙祴璇曟€?* | 猸愨瓙猸愨瓙猸?| 猬嗭笍 | **1041 閫氳繃 / 0 澶辫触**锛堢涓夌増 7 椤瑰け璐ュ叏閮ㄤ慨澶嶏級 |
| **鍔熻兘瀹屾垚搴?* | 猸愨瓙猸愨瓙猸?| 猬嗭笍 | TASK-0000~0120 / M1~M10-A 鍏ㄩ儴瀹屾垚 |
| **鍙繍琛屾€?* | 猸愨瓙猸愨瓙猸?| 鈥?| FastAPI + Canon REST API + Docker sandbox + Edge 閲嶈繛/鎭㈠ |
| **鍙娴嬫€?* | 猸愨瓙猸愨瓙猸?| 鈥?| 闈㈡澘 + 璺ㄧ鏃堕棿绾?+ Canon 娌荤悊 API 鍙娴?|

### 鎬讳綋璇勫垎锛?*猸愨瓙猸愨瓙猸?/ 5锛堝伐绋嬫垚鐔熷害鏋侀珮锛?041 tests 鍏ㄩ儴閫氳繃锛?*

---

## 馃搵 鍏€佷笌绗笁鐗堝姣旓紙鏀硅繘鎬荤粨锛?
| 涓婃鍙戠幇 | 浼樺厛绾?| 鐘舵€?| 瑙ｅ喅鏂瑰紡 |
|---------|--------|------|---------|
| F-21 VTS DEGRADED 鏂█澶辫触锛? 椤癸級 | P1 | 鉁?**宸茶В鍐?* | M8/M9 寮€鍙戣繃绋嬩腑淇 |
| F-22 sandbox docker mock 璺緞閿欒锛? 椤癸級 | P1 | 鉁?**宸茶В鍐?* | M8/M9 寮€鍙戣繃绋嬩腑淇 |
| F-23 VTS 閲嶈繛/骞傜瓑鏂█澶辫触锛? 椤癸級 | P1 | 鉁?**宸茶В鍐?* | M8/M9 寮€鍙戣繃绋嬩腑淇 |
| F-24 release contract 寮傚父澶勭悊涓嶅尮閰嶏紙1 椤癸級 | P1 | 鉁?**宸茶В鍐?* | M8/M9 寮€鍙戣繃绋嬩腑淇 |

M8~M10-A 鏂板鑳藉姏锛?
| 鏂板鑳藉姏 | 閲岀▼纰?| 璇存槑 |
|---------|--------|------|
| 杩愯鏃堕泦鎴愰棴鐜紙ServiceRegistry + MetricsCollector锛?| M8 | TASK-0107~0110 |
| World Canon SSOT + 绱㈠紩鍚屾 + 娉ㄥ叆鍒嗘Ы | M9 | TASK-0111~0113 |
| Evolution 鎻愭瀹℃牳娴佺▼ | M9 | TASK-0114 |
| 鏃ヨ drift_suggestion 鍏宠仈 | M9 | TASK-0115 |
| Canon 娌荤悊 REST API锛? 绔偣锛?| M10-A | TASK-0117~0119 |
| reject_proposal() + list_proposals() | M10-A | TASK-0117 |
| M10-A 鍙戝竷娓呭崟锛坢10a_release.json锛?| M10-A | TASK-0120 |

---

## 馃弳 涔濄€佺粨璁?
**Natsume Ai 鍦?TASK-0000~0120 / M1~M10-A 鍏ㄩ儴瀹屾垚鍚庯紝宸茶揪鍒?Canon 娌荤悊闂幆"鐨勫伐绋嬫垚鐔熷害銆?*

- 鉁?**57,527 琛屼唬鐮?*锛?3,352 涓氬姟 + 24,175 娴嬭瘯锛夊叏閮ㄦ湁鏈虹粍缁?- 鉁?**1041 椤规祴璇曞叏閮ㄩ€氳繃**锛? 澶辫触锛夛紝绗笁鐗?7 椤瑰け璐ュ叏閮ㄤ慨澶?- 鉁?**26 寮犲璁¤〃 + 148 涓敊璇爜甯搁噺 + 19 涓?SQL 杩佺Щ鑴氭湰** 瑕嗙洊鍏ㄩ摼璺?- 鉁?**Canon 娌荤悊 REST API**锛? 绔偣锛夊畬鏁村疄鐜帮紝瀹¤涓茶仈 + 骞傜瓑鎬?+ 缁撴瀯绋冲畾
- 鉁?鏍稿績涓嶅彉閲忥紙鍗曞悜閾?Token 蹇呮/瀹¤寮哄埗/鎷掔粷蹇呭甫鐮?涓嶄吉鎵ц/Canon 涓绘潈锛夊叏閮ㄩ€氳繃
- 鉁?Docker 瀹瑰櫒绾ф矙绠遍殧绂诲凡灏变綅
- 鉁?Edge 璁惧鎭㈠闊ф€э細VTS 鑷姩閲嶈繛 + ASR 璁惧鎭㈠ + 浜嬩欢琛ュ彂
- 鈿狅笍 鍓╀綑 16 涓?P1 鍙戠幇椤癸紙鍚?7 椤?M10-A 鏂板锛?- 鈿狅笍 鍓╀綑 15 涓?P2 寤鸿椤癸紙鍚?5 椤?M10-A 鏂板锛?
**椋庨櫓璇︽儏宸茶褰曞湪** [risk_registry.md](../60_trace/risk_registry.md) **涓紙v3.0锛岃鐩?TASK-0065~0120锛夈€?*

---

> 鏈姤鍛婂熀浜庡叏閲忔簮鐮侀€愭ā鍧楀闃?+ 1041 椤规祴璇曞叏閲忚繍琛?+ 涓嶅彉閲忛潤鎬佹壂鎻?+ 瀹夊叏瀹¤鐢熸垚銆?
