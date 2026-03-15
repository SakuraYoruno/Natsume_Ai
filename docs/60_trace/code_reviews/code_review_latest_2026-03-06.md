# 鏈€鏂颁唬鐮佸垎鏋愭姤鍛?
**鐢熸垚鏃堕棿锛?* 2026-03-06 09:43
**鍒嗘瀽鍩哄噯锛?* HEAD = `e8f41d7`锛堟渶鏂版彁浜わ級
**娴嬭瘯缁撴灉锛?* 17 failed / 2077 passed / 10 skipped锛堟€昏 2104锛?
---

## 涓€銆佹渶鏂版彁浜ゆ瑙堬紙鏈€杩?5 娆★級

| Commit | 绫诲瀷 | 鎻忚堪 | 鍙樻洿瑙勬ā |
|--------|------|------|---------|
| `e8f41d7` | docs | 娣诲姞鐪熷疄 host chain 杩愯鎶ュ憡鍜?artifacts | +4841 琛岋紙绾枃妗ｏ級 |
| `1cbd5fa` | feat | 鏀寔鏃ユ枃钂搁锛屾柊澧?natsume_ai_ja 椋庢牸 canon | +108 琛?|
| `7697898` | feat | 瀵归綈 model plane 鍜屽叚搴撳彫鍥?contracts | +969 琛?|
| `e7f48d5` | fix | 鎸佷箙鍖?chat graph trace 鍜?LLM 鑺傜偣璋冪敤鏃ュ織 | +568 琛?|
| `95161f1` | feat | 绋冲畾杩愯鏃?key 瑙ｆ瀽鍜?Gemini 鍥剧敓鎴愯矾寰?| +1075 琛?|

---

## 浜屻€佹牳蹇冨姛鑳藉彉鏇村垎鏋?
### 2.1 鍏簱鍙洖鏋舵瀯锛坄7697898`锛屾渶閲嶈鍙樻洿锛?
**鏂囦欢锛?* `host_brain/retrieval/recall_fusion.py`锛?387 琛岋級銆乣host_brain/retrieval/pipeline.py`锛?13 琛岋級

**鍙樻洿鍐呭锛?*
- `RecallFusionServiceContract` 鏂板 6 涓瓨鍌ㄥ悗绔敮鎸侊細
  - `episodic_vector`锛圕hroma 鍚戦噺搴擄紝鍘熸湁锛?  - `sqlite_ledger`锛圫QLite 瀹¤鏃ュ織锛?  - `neo4j_bond`锛圢eo4j Bond 鍥炬暟鎹簱锛?  - `style_canon`锛堥鏍?Canon 瀛樺偍锛?  - `world_canon`锛堜笘鐣岃 Canon 瀛樺偍锛?  - `artifact_vault`锛圓rtifact 淇濋櫓搴擄級
- 鏂板 `_search_route()` 璺敱鍒嗗彂鏂规硶锛屾牴鎹?`store_id` 鍒嗗彂鍒板搴斿悗绔?- `RecallRouteConfig` 鏂板 `store_id` 瀛楁锛堝師鏉ュ彧鏈?`collection`锛?- `pipeline.py` 鐨?`build_retrieval_pipeline_from_env()` 娉ㄥ叆 `style_store`/`world_store`/`artifact_vault`

**璇勪及锛?* 鉁?鏋舵瀯鎵╁睍鍚堢悊锛屽叚搴撳彫鍥炴槸 M26 鏍稿績鐩爣锛屽疄鐜板畬鏁淬€?
### 2.2 Model Node Override 鏀寔锛坄7697898`锛?
**鏂囦欢锛?* `host_brain/integrations/graph_model_resolve.py`锛?30 琛岋級

**鍙樻洿鍐呭锛?*
- `resolve_model_nodes()` 鏂板 `model_node_overrides` 鏀寔
- 鏂板 `_apply_node_overrides()` 杈呭姪鍑芥暟
- 鏀寔 inline config 鍜?model plane profile 涓ょ璺緞鐨?override 鍚堝苟

**璇勪及锛?* 鉁?瀹炵幇绠€娲侊紝鏀寔杩愯鏃跺姩鎬佽鐩栨ā鍨嬭妭鐐归厤缃€?
### 2.3 Chat Graph 鎸佷箙鍖栵紙`e7f48d5`锛?
**鏂囦欢锛?* `host_brain/app/fastapi_app.py`锛?564 琛岋級

**鍙樻洿鍐呭锛?*
- 鏂板 `CHAT_GRAPH_NODE_ORDER` 鑺傜偣鎵ц椤哄簭甯搁噺锛? 涓妭鐐癸級
- 鏂板 `_repair_utf8_latin1_mojibake()` UTF-8 涔辩爜淇鍑芥暟
- 鏂板 `_render_node_generation_prompt()` 鑺傜偣鐢熸垚 prompt 鏋勫缓
- 鏂板 `_extract_node_runtime_payload()` / `_extract_node_narrative_text()` 杈呭姪鍑芥暟
- Chat graph trace 鍜?LLM 鑺傜偣璋冪敤鏃ュ織鎸佷箙鍖栧埌 SQLite

**璇勪及锛?* 鉁?鍙娴嬫€у寮猴紝涔辩爜淇鍑芥暟鏈夊疄闄呬环鍊笺€?
### 2.4 鏃ユ枃椋庢牸 Canon锛坄1cbd5fa`锛?
**鏂囦欢锛?* `host_brain/canon/style/natsume_ai_ja.json`锛堟柊澧烇級銆乣scripts/runtime/import_natsume_style_canon.py`锛?108 琛岋級

**鍙樻洿鍐呭锛?*
- 鏂板鏃ユ枃椋庢牸 Canon 鏂囦欢 `natsume_ai_ja.json`
- 瀵煎叆鑴氭湰鏀寔鏃ユ枃钂搁璺緞

**璇勪及锛?* 鉁?澶氳瑷€鏀寔鎵╁睍锛岀鍚?M26 鏃ユ枃娓叉煋鐩爣銆?
---

## 涓夈€佹祴璇曞け璐ユ牴鍥犲垎鏋愶紙17 涓級

### 3.1 Bond Summary 涓枃鍖栧鑷寸殑娴嬭瘯涓嶅吋瀹癸紙5 涓級

**澶辫触娴嬭瘯锛?*
- `test_bond_summary_contract.py::test_t_75_summary_is_injected_to_prompt_context_bond_slot`
- `test_prompt_context_contract.py::test_t_cp_01_valid_prompt_context_passes`
- `test_tool_catalog_contract.py::test_t_tool_01_tool_meta_pack_is_metadata_only_and_context_safe`
- `test_canon_store_contract.py::test_t_wcanon_02_authoritative_injection_is_citation_only`
- `test_world_canon_slot_contract.py::test_t_wcanon_05e_fixture_passes_validate_prompt_context`

**鏍瑰洜锛?* 娴嬭瘯 fixture 涓殑 `bond_summary.stage` 浣跨敤浜嗛潪娉曞€硷紙`'warm'`銆乣'stable'`銆乣'ESTABLISHED'`锛夛紝杩欎簺鍊兼棦涓嶅湪鑻辨枃鏋氫妇闆嗗悎锛坄STRANGER/ACQUAINTANCE/FRIEND/CLOSE_FRIEND/CONFIDANT/SOULMATE`锛変腑锛屼篃涓嶅湪涓枃鏋氫妇闆嗗悎锛坄闄岀敓浜?鐩歌瘑/鏈嬪弸/濂藉弸/鎸氬弸/鐏甸瓊浼翠荆`锛変腑銆?
**淇鏂瑰悜锛?* 鏇存柊娴嬭瘯 fixture 涓殑 `bond_summary.stage` 涓哄悎娉曟灇涓惧€硷紙濡?`'FRIEND'` 鎴?`'鏈嬪弸'`锛夈€?
### 3.2 Bond Summary stage/trend 涓枃鍖栧鑷寸殑闆嗘垚娴嬭瘯澶辫触锛? 涓級

**澶辫触娴嬭瘯锛?*
- `test_bond_routes_api.py::test_summary_stage_is_valid`锛堟湡鏈涜嫳鏂囨灇涓撅紝瀹為檯杩斿洖涓枃 `'鐔熻瘑'`锛?- `test_bond_routes_api.py::test_summary_trend_is_valid`锛堟湡鏈?`RISING/STABLE/DECLINING`锛屽疄闄呰繑鍥?`'绋冲畾'`锛?- `test_bond_routes_api.py::test_summary_stage_updates_after_promotion`锛堟湡鏈?`'ACQUAINTANCE'`锛屽疄闄呰繑鍥?`'鐔熻瘑'`锛?- `test_bond_dynamics_stage_integration.py::test_summary_stage_matches_state`锛堝悓涓婏級

**鏍瑰洜锛?* `BondScoreState.to_summary()` 宸蹭腑鏂囧寲锛圡28-H/M28-I锛夛紝浣嗛泦鎴愭祴璇曚粛鏈熸湜鑻辨枃鏋氫妇鍊笺€?
**淇鏂瑰悜锛?* 鏇存柊闆嗘垚娴嬭瘯鐨勬湡鏈涘€间负涓枃鏋氫妇锛屾垨鍦?`to_summary()` 涓鍔?`lang` 鍙傛暟鏀寔鍙岃杈撳嚭銆?
### 3.3 Governor R8 瑙勫垯鏂板瀵艰嚧 rule_trace 鏁伴噺鍙樺寲锛? 涓級

**澶辫触娴嬭瘯锛?*
- `test_governor_rate_limit_contract.py::test_45_existing_r1_r7_trace_structure`锛堟湡鏈?7 鏉★紝瀹為檯 8 鏉★級
- `test_governor_rules_contract.py::test_t_gov_01_rule_trace_has_threshold_and_snapshot_summary`锛堟湡鏈?7 鏉★紝瀹為檯 8 鏉★級

**鏍瑰洜锛?* M28-D锛圤PT-8锛夋柊澧炰簡 R8 鎯呮劅闂ㄦ帶瑙勫垯锛宍rule_trace` 浠?7 鏉″鍔犲埌 8 鏉★紝浣嗘祴璇曚粛纭紪鐮?`assertEqual(7, len(result["rule_trace"]))`銆?
**淇鏂瑰悜锛?* 灏嗘祴璇曚腑鐨?`7` 鏀逛负 `8`锛屽苟琛ュ厖 R8 鐨?trace 缁撴瀯楠岃瘉銆?
### 3.4 Obs Metrics 鏂板 emotion_metrics 瀛楁锛? 涓級

**澶辫触娴嬭瘯锛?*
- `test_obs_metrics_contract.py::test_t_obs_01_structure_complete`锛堝疄闄呰繑鍥炲浜?`emotion_metrics` 瀛楁锛?
**鏍瑰洜锛?* M29-Q锛圤PT-46锛夊湪 `MetricsCollector.collect()` 涓柊澧炰簡 `emotion_metrics` 瀛楁锛屼絾娴嬭瘯鐨?`required_keys` 闆嗗悎鏈寘鍚瀛楁銆?
**淇鏂瑰悜锛?* 鍦ㄦ祴璇曠殑 `required_keys` 涓坊鍔?`'emotion_metrics'`銆?
### 3.5 VTS 琛ㄦ儏鏄犲皠琛?edge/host 涓嶅悓姝ワ紙5 涓級

**澶辫触娴嬭瘯锛?*
- `test_vts_degraded_contract.py::test_unknown_tag_returns_degraded`锛堟湭鐭?tag 杩斿洖 `APPLIED` 鑰岄潪 `DEGRADED`锛?- `test_vts_degraded_contract.py::test_unknown_tag_preserves_original`锛堣繑鍥?`exp_excited_01` 鑰岄潪 `exp_neutral_01`锛?- `test_vts_degraded_contract.py::test_unknown_tag_fixture_driven`锛堝悓涓婏級
- `test_vts_hw_contract.py::test_mapping_table_matches_host_vts`锛坋dge 6 绉?鈮?host 16 绉嶏級
- `test_vts_hw_contract.py::test_emotion_map_complete_coverage`锛坄EXCITED` 鍦?edge 渚х己澶憋級

**鏍瑰洜锛?* M28-L锛圤PT-34锛夊凡灏?`host_brain/channels/vts.py` 鐨?`EMOTION_TO_EXPRESSION_ID` 鎵╁睍鑷?16 绉嶏紝浣?`edge_client/actuators/vts_driver.py` 鐨勬槧灏勮〃浠嶆槸鏃х殑 6 绉嶏紝瀵艰嚧 edge/host 涓嶅悓姝ャ€傚悓鏃讹紝`EXCITED` 绛夋柊澧炴爣绛惧湪 edge 渚ц褰撲綔鏈煡 tag 澶勭悊锛岃Е鍙戜簡 `APPLIED`锛坒allback锛夎€岄潪 `DEGRADED`銆?
**淇鏂瑰悜锛?* 灏?`edge_client/actuators/vts_driver.py` 鐨?`EMOTION_TO_EXPRESSION_ID` 鍚屾鏇存柊涓?16 绉嶏紝涓?`host_brain/channels/vts.py` 淇濇寔涓€鑷淬€?
---

## 鍥涖€佸け璐ュ垎绫绘眹鎬?
| 绫诲埆 | 鏁伴噺 | 鏍瑰洜 | 淇闅惧害 |
|------|------|------|---------|
| Bond fixture 闈炴硶 stage 鍊?| 5 | 娴嬭瘯 fixture 鏈洿鏂?| 馃煝 浣?|
| Bond summary 涓嫳鏂囦笉鍏煎 | 4 | 闆嗘垚娴嬭瘯鏈熸湜鍊兼湭鏇存柊 | 馃煝 浣?|
| Governor R8 rule_trace 鏁伴噺 | 2 | 娴嬭瘯纭紪鐮佹暟閲忔湭鏇存柊 | 馃煝 浣?|
| Obs metrics 鏂板瓧娈?| 1 | 娴嬭瘯 required_keys 鏈洿鏂?| 馃煝 浣?|
| VTS edge/host 鏄犲皠涓嶅悓姝?| 5 | edge_client 鏈悓姝ユ洿鏂?| 馃煛 涓?|
| **鍚堣** | **17** | | |

> **缁撹锛?* 鎵€鏈?17 涓け璐ュ潎涓?*娴嬭瘯/浠ｇ爜鍚屾闂**锛屾棤閫昏緫 Bug銆傚叾涓?12 涓槸娴嬭瘯鏈熸湜鍊兼湭璺熼殢浠ｇ爜鏇存柊锛? 涓槸 edge_client 鏈悓姝?host_brain 鐨?VTS 鏄犲皠琛ㄦ墿灞曘€?
---

## 浜斻€佷唬鐮佽川閲忚瘎浼?
| 缁村害 | 璇勫垎 | 璇存槑 |
|------|------|------|
| 鏋舵瀯瀹屾暣鎬?| 猸愨瓙猸愨瓙猸?| 鍏簱鍙洖鏋舵瀯瀹屾暣锛孧26 鐩爣杈炬垚 |
| 娴嬭瘯瑕嗙洊鐜?| 猸愨瓙猸愨瓙 | 2077 passed锛屾柊鍔熻兘鏈夊搴旀祴璇?|
| 浠ｇ爜鍚屾鎬?| 猸愨瓙猸?| edge/host VTS 鏄犲皠琛ㄦ湭鍚屾 |
| 鏂囨。瀹屾暣鎬?| 猸愨瓙猸愨瓙猸?| 鏈夊畬鏁寸殑 trace 鎶ュ憡鍜?release 鏂囨。 |
| 鍚戝悗鍏煎鎬?| 猸愨瓙猸愨瓙 | 澶ч儴鍒嗗彉鏇存湁榛樿鍊间繚鎶?|

---

## 鍏€佸緟淇娓呭崟锛堜紭鍏堢骇鎺掑簭锛?
### P0锛堥樆濉?CI锛?1. **鍚屾 edge_client VTS 鏄犲皠琛?*锛歚edge_client/actuators/vts_driver.py` 鐨?`EMOTION_TO_EXPRESSION_ID` 琛ュ叏鑷?16 绉嶏紙涓?`host_brain/channels/vts.py` 涓€鑷达級
2. **鏇存柊 Bond 闆嗘垚娴嬭瘯鏈熸湜鍊?*锛歚test_bond_routes_api.py` 鍜?`test_bond_dynamics_stage_integration.py` 涓殑 `stage`/`trend` 鏈熸湜鍊兼敼涓轰腑鏂囨灇涓?
### P1锛堝奖鍝嶅悎瑙勶級
3. **鏇存柊 Bond fixture 闈炴硶 stage 鍊?*锛? 涓?contract 娴嬭瘯鐨?fixture 涓?`bond_summary.stage` 鏀逛负鍚堟硶鏋氫妇鍊?4. **鏇存柊 Governor 娴嬭瘯 rule_trace 鏁伴噺**锛? 涓?contract 娴嬭瘯涓?`assertEqual(7, ...)` 鏀逛负 `assertEqual(8, ...)`锛屽苟琛ュ厖 R8 trace 缁撴瀯楠岃瘉
5. **鏇存柊 Obs metrics 娴嬭瘯 required_keys**锛歚test_obs_metrics_contract.py` 鐨?`required_keys` 娣诲姞 `'emotion_metrics'`

---

## 涓冦€佷笌 M27/M28/M29 閲岀▼纰戠殑瀵瑰簲鍏崇郴

| 鍙樻洿 | 瀵瑰簲閲岀▼纰戜换鍔?| 鐘舵€?|
|------|--------------|------|
| GateSnapshot 鎯呮劅瀛楁 | TASK-0232 M27-A | 鉁?宸插疄鐜?|
| R8 鎯呮劅闂ㄦ帶瑙勫垯 | TASK-0247 M28-D | 鉁?宸插疄鐜帮紙浣嗘祴璇曟湭鏇存柊锛?|
| EMOTION_TO_EXPRESSION_ID 鎵╁睍鑷?6绉?| TASK-0255 M28-L | 鈿狅笍 host 宸插疄鐜帮紝edge 鏈悓姝?|
| BondScoreState.to_summary 涓枃鍖?| TASK-0251 M28-H | 鉁?宸插疄鐜帮紙浣嗛泦鎴愭祴璇曟湭鏇存柊锛?|
| MetricsCollector emotion_metrics | TASK-0280 M29-Q | 鉁?宸插疄鐜帮紙浣嗘祴璇曟湭鏇存柊锛?|
| 鍏簱鍙洖鏋舵瀯 | TASK-0238 M27-G | 鉁?宸插疄鐜?|

