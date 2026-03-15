鍙椾紬锛欳ODEX
鏍囬锛氬叏闈唬鐮佸闃呮姤鍛婏紙绗簲鐗堬紝2026-03-03锛?
# 馃攳 Natsume Ai 鍏ㄩ潰浠ｇ爜瀹￠槄鎶ュ憡

> **鐢熸垚鏃ユ湡**锛?026-03-03锛堢浜旂増锛屽惈 TASK-0000~0129 / M1~M10-C 鍏ㄩ儴浜や粯锛?> **瀹￠槄鑼冨洿**锛歨ost_brain/銆乪dge_client/銆乼ests/銆乧onfigs/銆乨ocs/
> **瀹￠槄鏂规硶**锛氶€愭ā鍧楁簮鐮侀槄璇?+ 濂戠害娴嬭瘯鍏ㄩ噺杩愯 + 鏋舵瀯涓€鑷存€ф鏌?+ 涓嶅彉閲忛潤鎬佹壂鎻?+ 瀹夊叏瀹¤

---

## 馃搳 涓€銆侀」鐩€昏

| 鎸囨爣 | 鏁版嵁 | 杈冧笂娆″彉鍖栵紙vs 绗洓鐗堬級 |
|------|------|-----------|
| **Host Brain 婧愭枃浠?* | 96 涓?`.py`锛堝惈 `__init__`锛?| +2锛圡10-B/C 鏂板锛?|
| **Edge Client 婧愭枃浠?* | 26 涓?`.py` | 鈥?|
| **Host Brain 浠ｇ爜琛?* | ~27,100 琛?| +564锛坉istillation.py + prompt_context 鎵╁睍 + ledger 鎵╁睍锛?|
| **Edge Client 浠ｇ爜琛?* | ~6,900 琛?| +84锛坅sr.py 鐑瘝娉ㄥ叆锛?|
| **娴嬭瘯浠ｇ爜琛?* | ~25,800 琛?| +1,625锛圡10-B/C 鏂板 21 涓祴璇曪級 |
| **鎬讳唬鐮佽** | **~59,800 琛?* | +2,273 |
| **濂戠害娴嬭瘯鏂囦欢** | 122 涓紙鍚?integration/e2e锛?| +2锛圡10-B 鏂板锛?|
| **娴嬭瘯鏂█鐐?* | **1062 閫氳繃 / 0 澶辫触** 鉁?| +21 閫氳繃 / 0 澶辫触 |
| **娴嬭瘯 Fixture 鏂囦欢** | 257 涓?`.json` | 鈥?|
| **瀹¤鏃ュ織琛?* | 27 寮?| +1锛坰emantic_memory锛孧10-C锛?|
| **閿欒鐮侊紙reason_code锛?* | 148 涓父閲?| 鈥?|
| **SQL 杩佺Щ鑴氭湰** | 20 涓紙鍚?bond cypher锛?| +1锛?017_canon_proposal_log_unique.sql锛孧10-A P1锛?|
| **鍙戝竷娓呭崟** | 5 涓紙M8/M9/M10-A/M10-B/M10-C锛?| +2锛圡10-B/C锛?|
| **闇€姹傝拷韪潯鐩?* | R-01~R-97 | +7锛圧-91~R-97锛?|

---

## 馃彈锔?浜屻€佹灦鏋勫叏鏅紙M10-C 鏇存柊锛?
```
鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€ Host Brain锛圥ython 3.12+锛?鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?鈹?                                                                       鈹?鈹? 鈹屸攢 app/ 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?  鈹?鈹? 鈹? fastapi_app.py      鈫?HTTP 鍏ュ彛 + 涓棿浠?+ 璺敱娉ㄥ唽            鈹?  鈹?鈹? 鈹? main.py             鈫?鍚姩鎶ュ憡 + 鍋ュ悍鑱氬悎 + 9 鑺傜偣鎷撴墤         鈹?  鈹?鈹? 鈹? config.py           鈫?鍒嗗眰閰嶇疆 + 鑴辨晱蹇収 + diff              鈹?  鈹?鈹? 鈹? web_ui.py           鈫?鍙娴嬮潰鏉?API                          鈹?  鈹?鈹? 鈹? canon_routes.py 鈫?M10-A锛? 绔偣锛? M10-B锛?/canon/hotwords锛? 鈹?  鈹?鈹? 鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?  鈹?鈹?                                                                       鈹?鈹? 鈹屸攢 memory/ 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?    鈹?鈹? 鈹? canon_world_store.py 鈫?M9/M10-A/M10-B锛?extract_hotwords锛?  鈹?    鈹?鈹? 鈹?   + rollback_proposal() 鈫?M10-A P1                           鈹?    鈹?鈹? 鈹? distillation.py 鈫?NEW锛圡10-C锛塎emoryDistillationEngine       鈹?    鈹?鈹? 鈹? episodic.py / chroma_client.py                                鈹?    鈹?鈹? 鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?    鈹?鈹?                                                                       鈹?鈹? 鈹屸攢 audit/ 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?    鈹?鈹? 鈹? sqlite_ledger.py锛垀1,750L锛夆啇 +semantic_memory 琛紙M10-C锛?   鈹?    鈹?鈹? 鈹?   + log_semantic_memory / list_semantic_memories              鈹?    鈹?鈹? 鈹? migration_runner.py / retention.py / drill.py                 鈹?    鈹?鈹? 鈹? migrations/0017_canon_proposal_log_unique.sql 鈫?NEW锛圡10-A P1锛夆攤   鈹?鈹? 鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?    鈹?鈹?                                                                       鈹?鈹? 鈹屸攢 core/ 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?   鈹?鈹? 鈹? prompt_context.py 鈫?+OPTIONAL_SLOTS + semantic_slot锛圡10-C锛?  鈹?   鈹?鈹? 鈹?   + build_semantic_slot() + _validate_semantic_slot()         鈹?   鈹?鈹? 鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?   鈹?鈹?                                                                       鈹?鈹? + governor/ + integrations/ + airlock/ + channels/ + bond/            鈹?鈹? + retrieval/ + skills/ + tools/ + lsp/ + release/ + obs/              鈹?鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?
鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€ Edge Client 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?鈹? rpc/voice.py | rpc/event_reporter.py | rpc/resource_gate.py           鈹?鈹? senses/asr.py锛?inject_canon_hotwords锛孧10-B锛?                       鈹?鈹? senses/fallback_llm.py                                                鈹?鈹? actuators/speech.py | actuators/tts_engine.py                         鈹?鈹? actuators/vts_driver.py锛?鎸囨暟閫€閬块噸杩烇級                               鈹?鈹? proofs/resource_gate.py | config.py                                   鈹?鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?
Canon 娌荤悊 REST API锛圡10-A/B 绱 8 绔偣锛夛細
  GET  /canon/proposals                    鈥?鍒楀嚭鎻愭锛堝垎椤碉級
  POST /canon/proposals                    鈥?鎻愪氦鏂版彁妗?  GET  /canon/proposals/{proposal_id}      鈥?鏌ヨ鎻愭璇︽儏
  POST /canon/proposals/{proposal_id}/approve  鈥?瀹℃牳閫氳繃
  POST /canon/proposals/{proposal_id}/reject   鈥?鎷掔粷鎻愭
  POST /canon/proposals/{proposal_id}/rollback 鈥?鍥炴粴宸查€氳繃鎻愭锛圡10-A P1锛?  GET  /canon/documents                    鈥?鍒楀嚭 SSOT 蹇収
  GET  /canon/documents/{doc_id}           鈥?鏌ヨ鍗曚釜鏂囨。
  GET  /canon/hotwords                     鈥?鑾峰彇 ASR 鐑瘝鍒楄〃锛圡10-B锛?
璁板繂钂搁 API锛圡10-C锛夛細
  MemoryDistillationEngine.distill_top_k() 鈥?鎯呰妭璁板繂 鈫?璇箟璁板繂钂搁
  SQLiteLedger.list_semantic_memories()    鈥?璇箟璁板繂鍒嗛〉鏌ヨ
  prompt_context["semantic_slot"]            鈥?璇箟璁板繂娉ㄥ叆鍒嗘Ы锛堝彲閫夛級
```

---

## 馃敩 涓夈€丮10-B/C 鏂板妯″潡瀹℃煡

### 3.1 M10-B ASR 鐑瘝娉ㄥ叆锛圱ASK-0122~0125锛?
| 妯″潡 | 鍙樺寲 | 璇勪环 |
|------|------|------|
| `memory/canon_world_store.py` | +`extract_hotwords(max_words=200)` | 鉁?鍘婚噸鎺掑簭锛岀┖鐩綍杩斿洖 [] |
| `app/canon_routes.py` | +`GET /canon/hotwords` 绔偣 | 鉁?鍚?max_words 鍙傛暟锛屽搷搴旂粨鏋勭粺涓€ |
| `edge_client/senses/asr.py` | +`inject_canon_hotwords()` + `get_canon_hotwords()` + `_CANON_HOTWORDS_CACHE` | 鉁?Host 涓嶅彲杈炬椂闄嶇骇杩斿洖 0锛屼笉鎶涘紓甯?|
| `release/manifests/m10b_release.json` | 鏂板 M10-B 鍙戝竷娓呭崟 | 鉁?閿佸畾 canon_routes_hash + asr_hash |

**瀹℃煡缁撹锛?*
- 鉁?`extract_hotwords()`锛氫粠鎵€鏈?active 鏂囨。鐨?title/tags 鎻愬彇锛屽幓閲嶆帓搴忥紝max_words 鎴柇姝ｇ‘銆?- 鉁?`GET /canon/hotwords`锛氬搷搴旂粨鏋勫惈 `hotwords/total/request_id/timestamp`锛屼笌鍏朵粬 Canon API 涓€鑷淬€?- 鉁?`inject_canon_hotwords()`锛歶rllib 瀹炵幇锛屾棤棰濆渚濊禆锛沀RLError 闄嶇骇澶勭悊瀹屾暣锛沗_CANON_HOTWORDS_CACHE` 妯″潡绾х紦瀛樻敮鎸?`get_canon_hotwords()` 璇诲彇銆?- 鉁?娴嬭瘯瑕嗙洊锛歍-Canon-HW-01~03锛堝崟鍏冿級+ T-Canon-HW-API-01~02锛堝绾︼級+ T-ASR-HW-01~03锛坢ock HTTP锛? 8 涓祴璇曘€?
### 3.2 M10-C 璁板繂钂搁绯荤粺锛圱ASK-0126~0129锛?
| 妯″潡 | 鍙樺寲 | 璇勪环 |
|------|------|------|
| `memory/distillation.py` | 鏂板 `MemoryDistillationEngine` | 鉁?瀹屾暣 |
| `audit/sqlite_ledger.py` | +`semantic_memory` 琛?+ 4 涓?CRUD 鏂规硶 | 鉁?瀹屾暣 |
| `core/prompt_context.py` | +`OPTIONAL_SLOTS` + `semantic_slot` 鍒嗘Ы | 鉁?瀹屾暣 |
| `release/manifests/m10c_release.json` | 鏂板 M10-C 鍙戝竷娓呭崟 | 鉁?閿佸畾 distillation/prompt_context/ledger hash |

**瀹℃煡缁撹锛?*
- 鉁?`MemoryDistillationEngine.distill_top_k()`锛氬彫鍥?Top-K 鎯呰妭璁板繂 鈫?鍚堝苟鎽樿 鈫?骞傜瓑鍐欏叆锛坰ource_hash UNIQUE锛夆啋 瀹¤ system_event_log銆?- 鉁?骞傜瓑鎬э細`source_hash = SHA-256(sorted source_memory_ids)`锛岀浉鍚屾潵婧愰噸澶嶈捀棣忚繑鍥?`skipped=True`锛屼笉閲嶅鍐欏叆銆?- 鉁?`semantic_memory` 琛細鍚?`semantic_id UNIQUE` + `source_hash UNIQUE` + `ts_ms INDEX`锛宍deleted_at` 杞垹闄ゆ敮鎸併€?- 鉁?`prompt_context.semantic_slot`锛氳璁′负**鍙€夋Ы浣?*锛坄OPTIONAL_SLOTS`锛夛紝涓嶇牬鍧忕幇鏈?prompt_context 楠岃瘉閫昏緫锛屽瓨鍦ㄦ椂鏍￠獙 `semantic_id` + `summary` 蹇呭～瀛楁銆?- 鉁?`build_semantic_slot()`锛氫粠 ledger 鍙?Top-K 鏈€鏂拌涔夎蹇嗭紝鏋勫缓鏍囧噯鍖?slot 鍒楄〃銆?- 鉁?娴嬭瘯瑕嗙洊锛歍-Distill-01~06 = 8 涓祴璇曪紙姝ｅ父钂搁/骞傜瓑/绌哄瓨鍌?鍒嗛〉/prompt_context 楠岃瘉/build_semantic_slot锛夈€?- 鈿狅笍 **鍙戠幇**锛歚_merge_texts()` 褰撳墠涓虹畝鍗曟枃鏈嫾鎺ワ紝鏈帴鍏?LLM 鎽樿锛圧-MC-01锛孭2锛屽凡鐭ヨ竟鐣岋級銆?- 鈿狅笍 **鍙戠幇**锛歚distill_top_k()` 鐨?`query_text` 璇箟鍙洖渚濊禆 `EpisodicStore.recall()`锛岃嫢 Chroma 鍚戦噺搴撲负绌哄垯闄嶇骇杩斿洖 success=False锛堝凡鏈?T-Distill-03 瑕嗙洊锛夈€?
---

## 馃И 鍥涖€佹祴璇曚綋绯昏瘎浼帮紙M10-C 鏇存柊锛?
### 4.1 娴嬭瘯鍏ㄩ噺杩愯缁撴灉

```
1062 passed, 0 failed 鉁?```

| 娴嬭瘯鍒嗙被 | 鏂囦欢鏁?| 閫氳繃 | 澶辫触 |
|----------|--------|------|------|
| **濂戠害娴嬭瘯** | ~102 | ~1008 | 0 |
| **闆嗘垚娴嬭瘯** | ~10 | ~30 | 0 |
| **E2E 娴嬭瘯** | ~10 | ~24 | 0 |
| **鍚堣** | 122 | **1062** | **0** |

### 4.2 M10-B/C 鏂板娴嬭瘯瑕嗙洊

| 娴嬭瘯缂栧彿 | 娴嬭瘯鏂囦欢 | 鏂█鐐?| 瑕嗙洊鍩?|
|---------|---------|--------|-------|
| T-Canon-HW-01~03 | `tests/unit/test_canon_hotwords.py` | 8 | extract_hotwords 姝ｅ父/鎴柇/绌虹洰褰?|
| T-Canon-HW-API-01~02 | `tests/contract/test_canon_hotwords_api.py` | 6 | GET /canon/hotwords 濂戠害 |
| T-ASR-HW-01~03 | `tests/unit/test_asr_hotword_injection.py` | 7 | inject_canon_hotwords mock HTTP |
| T-Distill-01~06 | `tests/unit/test_memory_distillation.py` | 18 | 钂搁寮曟搸鍏ㄩ摼璺?|

---

## 馃敀 浜斻€佸畨鍏ㄥ璁★紙M10-C 鏇存柊锛?
### 5.1 涓嶅彉閲忓悎瑙勬壂鎻?
| 涓嶅彉閲?| 妫€鏌ラ」 | 缁撴灉 |
|--------|--------|------|
| **I1 鍗曞悜閾?* | 鎼滅储 `from host_brain.skills.impl` 鏃佽矾瀵煎叆 | 鉁?浠?`impl/__init__.py` 鍐呴儴娉ㄥ唽锛屾棤鏃佽矾 |
| **I6 Token 蹇呮** | 鎼滅储 Skill 鎵ц璺緞 bypass | 鉁?鎵€鏈夋墽琛岀粡 `loader.py` Token 鏍￠獙 |
| **I8 瀹¤寮哄埗** | 鎼滅储鎵ц鏃犲璁″啓鍏ヨ矾寰?| 鉁?鎵€鏈?`_log_to_table` 閫氳矾鍙揪 |
| **I9 鎷掔粷蹇呭甫鐮?* | 鎼滅储 REJECT 鏃?reason_code | 鉁?`validate_governor_output` 鎷︽埅 |
| **I11 涓嶄吉鎵ц** | 鎼滅储 `print("宸叉墽琛?)` 绛?| 鉁?QA 鎷︽埅鍣?+ 姝ｅ垯鍖归厤瑕嗙洊 |
| **Canon 涓绘潈** | 鎼滅储 SSOT 鏃佽矾鍐欏叆 | 鉁?鎵€鏈夊啓鍏ョ粡 `upsert_document()` + `_write_lock` |
| **璇箟璁板繂骞傜瓑** | source_hash UNIQUE 绾︽潫 | 鉁?INSERT OR IGNORE 淇濊瘉涓嶉噸澶嶅啓鍏?|

### 5.2 M10-B/C 鏂板瀹夊叏妫€鏌?
| 妫€鏌ラ」 | 缁撴灉 |
|--------|------|
| 鐑瘝 API 鏃犲啓鎿嶄綔锛屾棤闇€瀹¤涓茶仈 | 鉁?GET /canon/hotwords 鍙 |
| inject_canon_hotwords 闄嶇骇澶勭悊 | 鉁?URLError 鎹曡幏锛岃繑鍥?0锛屼笉鎶涘紓甯?|
| semantic_memory 鍐欏叆骞傜瓑 | 鉁?source_hash UNIQUE + INSERT OR IGNORE |
| 钂搁瀹¤ | 鉁?distill_top_k 鍐?system_event_log锛圡EMORY_DISTILLED 浜嬩欢锛?|
| prompt_context 鍙€夋Ы浣嶄笉鐮村潖鐜版湁楠岃瘉 | 鉁?OPTIONAL_SLOTS 鐙珛浜?EXPECTED_SLOTS |

---

## 鈿狅笍 鍏€佸彂鐜伴」姹囨€伙紙M10-C 鏇存柊锛?
### 馃敶 P0 鈥?闃绘柇鎬ч棶棰橈紙0 椤癸級

**鏃犻樆鏂€ч棶棰樸€?*

### 馃煛 P1 鈥?闇€璺熻繘锛?4 椤癸級

> 娉細F-28~F-31锛圧118-1/R118-2/R118-3/R114-1锛夊凡鍦?M10-A P1 淇涓В鍐筹紝浠?P1 鍒楄〃绉婚櫎銆?
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
| F-32 | `scripts/runtime/start_host_side.ps1` | canon_sync DEGRADED 闈為樆鏂彲鑳芥帺鐩栧悓姝ュけ璐ワ紙R112-1锛?| 鐩戞帶鍛婅瑕嗙洊 STARTUP_CANON_SYNC_DEGRADED |
| F-33 | `core/qa.py` | prompt_context 鍒嗘Ы娉ㄥ叆澶辫触闄嶇骇涓虹┖妲斤紙R113-1锛?| 鐩戞帶鍛婅瑕嗙洊 WCANON_SLOT_VIOLATION |
| F-34 | `memory/canon_world_store.py` | SSOT 鏂囦欢骞跺彂鍐欏叆鏃犳枃浠堕攣锛圧111-1锛?| 澶氳繘绋嬮儴缃叉椂寮曞叆鏂囦欢閿?|
| **F-40** | `memory/distillation.py` | `_merge_texts()` 涓虹畝鍗曟枃鏈嫾鎺ワ紝鏈帴鍏?LLM 鎽樿锛圧-MC-01锛?| 鍚庣画鎺ュ叆 LLM 鎽樿 API |
| **F-41** | `memory/distillation.py` | `distill_top_k()` 鏃犲苟鍙戜繚鎶わ紝澶氱嚎绋嬪悓鏃惰捀棣忓彲鑳戒骇鐢熼噸澶?source_hash 绔炴€侊紙R-MC-02锛?| 澶氳繘绋嬮儴缃叉椂鍔?distillation 绾у埆閿?|

### 馃煝 P2 鈥?宸茬煡杈圭晫/寤鸿鏀硅繘锛?6 椤癸級

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
| F-35 | `app/canon_routes.py` | approve 绔偣涓ゆ DB 鎿嶄綔闈炰簨鍔★紙R119-1锛?| 澶氳繘绋嬮儴缃叉椂浣跨敤浜嬪姟鍖呰９ |
| F-36 | `app/canon_routes.py` | reject 瀹¤鍐欏叆澶辫触鍙褰?warning锛圧119-2锛?| 鍚庣画绾冲叆浜嬪姟纭繚鍘熷瓙鎬?|
| F-37 | `release/manifests/m10a_release.json` | 鏈攣瀹?canon_world_store.py 鍜?sqlite_ledger.py hash锛圧120-1锛?| 鎵╁睍 manifest 閿佸畾鏇村鏍稿績鏂囦欢 |
| F-38 | `memory/canon_world_store.py` | `list_proposals()` 鍒嗛〉浣跨敤 LIMIT/OFFSET锛屾繁椤电爜鎬ц兘閫€鍖栵紙R112-2锛?| 鍚庣画鏀逛负 cursor-based 鍒嗛〉 |
| F-39 | `memory/episodic.py` | 鏃ヨ source_intent_id 璺ㄨ〃鍏宠仈鍙兘鏂摼锛圧115-1锛?| 澧炲姞 intent_id 瀹屾暣鎬ф牎楠?|
| **F-42** | `memory/distillation.py` | `_CANON_HOTWORDS_CACHE` 妯″潡绾х紦瀛樻棤 TTL锛岀儹璇嶆洿鏂板悗涓嶈嚜鍔ㄥけ鏁?| 澧炲姞 TTL 鎴栦富鍔ㄥ埛鏂版満鍒?|

---

## 馃搳 涓冦€佺患鍚堣瘎鍒嗭紙M10-C 鏇存柊锛?
| 缁村害 | 璇勫垎 | 杈冧笂娆?| 璇存槑 |
|------|------|--------|------|
| **鏋舵瀯璁捐** | 猸愨瓙猸愨瓙猸?| 鈥?| Host-Edge 鍒嗕綋 + 鍗曞悜閾?+ 绁ㄦ嵁鍖栨墽琛?+ Canon 娌荤悊 + 璁板繂钂搁 |
| **浠ｇ爜璐ㄩ噺** | 猸愨瓙猸愨瓙猸?| 鈥?| 0 瑁?except / 0 TODO / 0 纭紪鐮佸瘑閽?/ frozen dataclass |
| **瀹夊叏鎬?* | 猸愨瓙猸愨瓙猸?| 鈥?| Docker 瀹瑰櫒闅旂 + Canon 涓绘潈淇濇姢 + 瀹¤涓茶仈 + 钂搁骞傜瓑 |
| **鍙璁℃€?* | 猸愨瓙猸愨瓙猸?| 猬嗭笍 | 27 寮犲璁¤〃 + 148 涓敊璇爜 + 钂搁 MEMORY_DISTILLED 浜嬩欢 |
| **鍙祴璇曟€?* | 猸愨瓙猸愨瓙猸?| 猬嗭笍 | **1062 閫氳繃 / 0 澶辫触**锛?21 tests vs 绗洓鐗堬級 |
| **鍔熻兘瀹屾垚搴?* | 猸愨瓙猸愨瓙猸?| 猬嗭笍 | TASK-0000~0129 / M1~M10-C 鍏ㄩ儴瀹屾垚 |
| **鍙繍琛屾€?* | 猸愨瓙猸愨瓙猸?| 鈥?| FastAPI + Canon REST API + Docker sandbox + Edge 閲嶈繛/鎭㈠ |
| **鍙娴嬫€?* | 猸愨瓙猸愨瓙猸?| 鈥?| 闈㈡澘 + 璺ㄧ鏃堕棿绾?+ Canon 娌荤悊 API + 钂搁瀹¤ |

### 鎬讳綋璇勫垎锛?*猸愨瓙猸愨瓙猸?/ 5锛堝伐绋嬫垚鐔熷害鏋侀珮锛?062 tests 鍏ㄩ儴閫氳繃锛?*

---

## 馃搵 鍏€佷笌绗洓鐗堝姣旓紙鏀硅繘鎬荤粨锛?
| 涓婃鍙戠幇 | 浼樺厛绾?| 鐘舵€?| 瑙ｅ喅鏂瑰紡 |
|---------|--------|------|---------|
| F-28 `_get_canon_store()` 鍏ㄥ眬鍗曚緥绔炴€侊紙R118-1锛?| P1 | 鉁?**宸茶В鍐?* | M10-A P1锛氭敼涓轰緷璧栨敞鍏?`set_ledger_provider()` |
| F-29 `_get_ledger_from_app()` 寰幆寮曠敤椋庨櫓锛圧118-2锛?| P1 | 鉁?**宸茶В鍐?* | M10-A P1锛氶噸鏋勪负鍙傛暟浼犻€掓ā寮?|
| F-30 `proposal_id` 鏃?UNIQUE 绾︽潫锛圧118-3锛?| P1 | 鉁?**宸茶В鍐?* | M10-A P1锛?017 migration + INSERT OR IGNORE |
| F-31 Evolution approve 鏃犲洖婊氭満鍒讹紙R114-1锛?| P1 | 鉁?**宸茶В鍐?* | M10-A P1锛歚rollback_proposal()` + `/rollback` 绔偣 |

M10-B/C 鏂板鑳藉姏锛?
| 鏂板鑳藉姏 | 閲岀▼纰?| 璇存槑 |
|---------|--------|------|
| ASR 鐑瘝鎻愬彇锛坋xtract_hotwords锛?| M10-B | TASK-0122 |
| GET /canon/hotwords 绔偣 | M10-B | TASK-0123 |
| Edge ASR 鐑瘝娉ㄥ叆锛坕nject_canon_hotwords锛?| M10-B | TASK-0124 |
| M10-B 鍙戝竷娓呭崟锛坢10b_release.json锛?| M10-B | TASK-0125 |
| 璁板繂钂搁寮曟搸锛圡emoryDistillationEngine锛?| M10-C | TASK-0126 |
| semantic_memory 琛?+ CRUD | M10-C | TASK-0127 |
| prompt_context.semantic_slot 鍙€夊垎妲?| M10-C | TASK-0128 |
| M10-C 鍙戝竷娓呭崟锛坢10c_release.json锛?| M10-C | TASK-0129 |

---

## 馃弳 涔濄€佺粨璁?
**Natsume Ai 鍦?TASK-0000~0129 / M1~M10-C 鍏ㄩ儴瀹屾垚鍚庯紝宸茶揪鍒?璁板繂钂搁闂幆"鐨勫伐绋嬫垚鐔熷害銆?*

- 鉁?**~59,800 琛屼唬鐮?*锛垀34,000 涓氬姟 + ~25,800 娴嬭瘯锛夊叏閮ㄦ湁鏈虹粍缁?- 鉁?**1062 椤规祴璇曞叏閮ㄩ€氳繃**锛? 澶辫触锛夛紝杈冪鍥涚増鏂板 21 椤?- 鉁?**27 寮犲璁¤〃 + 148 涓敊璇爜甯搁噺 + 20 涓?SQL 杩佺Щ鑴氭湰** 瑕嗙洊鍏ㄩ摼璺?- 鉁?**Canon 娌荤悊 REST API**锛? 绔偣锛屽惈 /canon/hotwords锛夊畬鏁村疄鐜?- 鉁?**璁板繂钂搁绯荤粺**锛氭儏鑺傝蹇?鈫?璇箟璁板繂钂搁锛屽箓绛夊啓鍏ワ紝prompt_context 娉ㄥ叆
- 鉁?**ASR 鐑瘝娉ㄥ叆**锛歐orld Canon 鈫?Edge ASR 鐑瘝闂幆
- 鉁?鏍稿績涓嶅彉閲忥紙鍗曞悜閾?Token 蹇呮/瀹¤寮哄埗/鎷掔粷蹇呭甫鐮?涓嶄吉鎵ц/Canon 涓绘潈锛夊叏閮ㄩ€氳繃
- 鉁?Docker 瀹瑰櫒绾ф矙绠遍殧绂诲凡灏变綅
- 鉁?Edge 璁惧鎭㈠闊ф€э細VTS 鑷姩閲嶈繛 + ASR 璁惧鎭㈠ + 浜嬩欢琛ュ彂
- 鈿狅笍 鍓╀綑 14 涓?P1 鍙戠幇椤癸紙F-28~F-31 宸茶В鍐筹紝鏂板 F-40/F-41锛?- 鈿狅笍 鍓╀綑 16 涓?P2 寤鸿椤癸紙鏂板 F-42锛?
**椋庨櫓璇︽儏宸茶褰曞湪** [risk_registry.md](../60_trace/risk_registry.md) **涓€?*

---

> 鏈姤鍛婂熀浜庡叏閲忔簮鐮侀€愭ā鍧楀闃?+ 1062 椤规祴璇曞叏閲忚繍琛?+ 涓嶅彉閲忛潤鎬佹壂鎻?+ 瀹夊叏瀹¤鐢熸垚銆?
