# Natsume_Ai 鏋舵瀯浼樺寲璇︾粏 TodoList锛堝熀浜?ARCH_REVIEW_AND_ROADMAP_2026-03-09锛?
**鐗堟湰**锛歷1.0  
**鍒涘缓鏃ユ湡**锛?026-03-09  
**鏉ユ簮鏂囨。**锛歚docs/60_trace/ARCH_REVIEW_AND_ROADMAP_2026-03-09.md`  
**鐩爣**锛氭妸璺嚎鍥捐浆涓哄彲鎵ц浠诲姟娓呭崟锛屾敮鎸佺洿鎺ュ紑宸ヤ笌璺熻釜銆?
---

## 瀹炴柦杩涘害锛?026-03-09锛?
- [x] P0-A锛氭祦寮忚矾寰勫湪 LLM 璋冪敤鍓嶇瓑寰?Retrieval 缁撴灉锛屽苟娉ㄥ叆 prompt/PromptContext
- [x] P0-A锛氭柊澧炴祦寮?Retrieval 瓒呮椂闂ㄦ帶 `HOST_STREAM_RETRIEVAL_TIMEOUT_MS`锛堣秴鏃堕檷绾т笉闃诲锛?- [x] P0-A锛氭祦寮?Retrieval summary 瀛楁涓庨潪娴佸紡瀵归綈锛坄route_rows` / `route_errors` / `reason_chain`锛?- [x] P0-A锛氭柊澧?2 鏉″崟鍏冩祴璇曡鐩栤€滃墠缃敞鍏モ€濆拰鈥滆秴鏃堕檷绾р€?- [x] P0-B锛氭祦寮忛摼璺帴鍏?VTS 鎯呮劅椹卞姩锛堥檺娴?+ 瓒呮椂 + 寮€鍏筹級锛屽苟鍐欏叆 `COMMIT.audit_refs.vts`
- [x] P0-B锛氭柊澧?1 鏉″崟鍏冩祴璇曡鐩?`vts` 瀹¤瀛楁鍐欏叆
- [x] P0-A锛氭柊澧炲熀绾块噰鏍疯剼鏈?`tools/diag_stream_baseline.py`锛堥浜嬩欢/棣栨覆鏌?鎻愪氦鏃跺欢锛?- [x] P1-C锛氭柊澧?Graph 鍏ュ彛 SceneRouter handler锛坋ntry/post-core 鍙岃妭鐐硅矾鐢憋級
- [x] P1-C锛歚/v1/chat` 绉婚櫎 I/O 灞?pre-classifier 璺敱锛屾敼涓?`scene_type=AUTO` 浜ょ敱 Graph 鍏ュ彛鍐崇瓥
- [x] P1-C锛歚get_graph_runtime()` 娉ㄥ唽 SceneRouter handlers锛屽畬鎴愯繍琛屾椂鎺ュ叆
- [x] P1-B锛欸aming CHAT 璺敱鎺ュ叆 Edge fallback 3B锛坄/v1/chat`锛夛紝澶辫触鏃惰嚜鍔ㄥ洖閫€ Host Graph
- [x] P1-B锛氭渶灏忚兘鍔涚害鏉熻惤鍦帮紙鏋佺畝鍥炲銆佽烦杩?retrieval/episodic锛屼繚鐣欏璁★級
- [x] P1-B锛氭柊澧?3 鏉″崟鍏冩祴璇曡鐩?Edge 鎴愬姛鍒嗘祦銆佸け璐ュ厹搴曚笌寮€鍏冲叧闂?- [x] P1-A锛氶潪 Gaming 榛樿璺敱鎺ュ叆 Host 8B锛坄chat_direct_ja`锛夛紝澶嶆潅鑺傜偣淇濇寔 Cloud
- [x] P1-A锛氭柊澧?Host 8B 鍋ュ悍妫€鏌?+ 涓嶅彲鐢ㄨ嚜鍔ㄩ檷绾?Cloud + `HOST_CHAT_FORCE_CLOUD` 鍥炴粴寮€鍏?- [x] P1-A锛氭柊澧?`sys_mode=gaming` 鑷姩閫€鍑?Host 8B 鏄惧瓨闂ㄦ帶
- [x] P1-A锛氭柊澧?5 鏉″崟鍏冩祴璇曡鐩栭粯璁ゅ垎娴併€丟aming 闂ㄦ帶銆佸己鍒跺洖婊氥€佸仴搴烽檷绾т笌鑺傜偣鎻愪緵鏂瑰垎绂?- [x] P1-A锛氭柊澧?A/B 璇勪及宸ュ叿 `tools/diag_chat_ab.py`锛坔ost8b/edge3b/cloud 棰勮銆丣SON+Markdown 鎶ュ憡锛?- [x] P1-A锛氳ˉ榻?Ollama OpenAI 鍏煎鍩哄潃閫忎紶锛圚ost 8B 鐙珛 `base_url`锛屼笉褰卞搷 Cloud provider锛?- [x] P1-A锛欰/B 宸ュ叿淇锛坢etrics 鍏煎 + 浼氳瘽搴忓垪鍘诲啿绐侊級锛屽畬鎴?`qwen3:8b` 瀹炴満鍐掔儫鎶ュ憡
- [x] P1-C锛氱粺涓€璺敱鍐崇瓥瀵硅薄 `route_decision`锛坔ost/edge/cloud/skills锛夎疮閫?SceneRouter銆乣/v1/chat` 涓?`/v1/chat/stream` 瀹¤杈撳嚭
- [x] P0-A锛氬畬鎴愭祦寮忛摼璺熀绾块噰鏍锋枃妗?`baseline_stream_metrics_2026-03-09.md`锛?0 鏍锋湰銆佸彲澶嶇幇姝ラ锛?- [x] P0-A锛氭柊澧?`RetrievalOrchestrator`锛堢粺涓€ `query/session_state/trace_id -> retrieval_bundle` 鍗忚锛?- [x] P0-A锛歚/v1/chat` 涓?`/v1/chat/stream` 鎺ュ叆缂栨帓鍣紝瑕嗙洊 success/partial_failure/all_failure 璺緞
- [x] P0-A锛氬叚璺彫鍥炲绾﹀洖褰掗€氳繃锛坄test_recall_fusion_six_store_routes` + `test_retrieval_pipeline_contract`锛?- [x] P0-A/A4锛氳ˉ榻?`prompt_context_observability` 瀛楁锛堟潵婧愯鏁?鏉＄洰缁熻/涓婁笅鏂囧ぇ灏?鍙洖璺緞锛?- [x] P0-A/A5锛氭祦寮忎笌闈炴祦寮忚矾寰勫鍔犻€€鍖栭『搴忔棩蹇楋紙`six_route -> core_three -> minimal_context -> bare_llm`锛?- [x] P0-A/A5锛氱煭浼氳瘽妫€绱㈢紦瀛橈紙`RetrievalOrchestrator` TTL/LRU锛夎惤鍦帮紝鍛戒腑淇℃伅鍐欏叆 `retrieval_summary`
- [x] P0-B/B1锛氭柊澧炵粺涓€ `emotion_snapshot`锛坱ag/intensity/source/timestamp/age锛夊苟璐€?`chat`/`chat/stream` 瀹¤杈撳嚭
- [x] P0-B/B1锛歅rompt 缁戝畾琛ラ綈鎯呮劅寮哄害涓庡揩鐓у瓧娈碉紙`emotion_intensity` + `snapshot_*`锛夛紝鏂板瀵瑰簲鍗曟祴
- [x] P0-B/B3锛氭柊澧為棴鐜牎楠屽伐鍏?`tools/diag_emotion_closed_loop.py`锛?2 鏉¤鏂欙紝杈撳嚭 JSON+Markdown 鎶ュ憡锛?- [x] P0-B/B3锛氬畬鎴愰杞棴鐜姤鍛?`EMOTION_CLOSED_LOOP_REPORT_20260309_235917.*`锛屼竴鑷存€ч€氳繃鐜?`100%`锛?2/12锛?- [x] P0-A/A6锛氭柊澧炴祦寮?SSE 濂戠害娴嬭瘯 `test_stream_chat_contract.py`锛圙ET/POST 浜嬩欢椤哄簭銆佺粓姝㈡潯浠躲€丆OMMIT 瀹¤瀛楁锛?- [x] P0-A/A6锛氬畬鎴愭祦寮忛泦鎴愬洖褰掞紙`/v1/chat/stream` 绔偣灞傦級骞堕獙璇?intent_draft 閫忎紶
- [x] P0-A/A6锛氬畬鎴愭墿灞曞洖褰?`45 passed`锛堝惈 stream/retrieval/chat 璺緞锛?- [x] 5.2锛氭柊澧炶矾鐢卞櫒鍦烘櫙鍒嗙被娴嬭瘯闆?`test_scene_router_classification_dataset.py`锛堝叆鍙?鍚庣疆璺敱銆佷紭鍏堢骇涓庢樉寮忚鐩栵級
- [x] 5.2锛氬畬鎴愬満鏅垎绫诲洖褰掓墿灞?`79 passed`锛坰cene_router + stream + retrieval + chat锛?- [x] 5.2锛氭柊澧炴儏鎰熷眰鍥炲綊鏍锋湰闆?`tests/fixtures/emotion/closed_loop_cases.json`锛?6 鏍锋湰锛岃鐩?mood/tag 绾︽潫涓庡己搴?clamp锛?- [x] 5.2锛氶棴鐜伐鍏锋敮鎸?fixture 璇枡鍔犺浇锛坄--case-file`锛夊苟鍦ㄦ姤鍛婁腑璁板綍 `case_source`
- [x] 5.2锛氬畬鎴愭儏鎰熷眰鍥炲綊鎵╁睍 `82 passed`锛坰cene_router + stream + retrieval + chat + emotion锛?- [x] P2-C锛氭柊澧炵粺涓€璇婃柇鍏ュ彛 `tools/diag.py`锛堝瓙鍛戒护锛歚retrieval` / `stream` / `emotion` / `graph`锛?- [x] P2-C锛氱粺涓€璇婃柇鎶ュ憡鏍煎紡钀藉湴锛坄DIAG_UNIFIED_<DOMAIN>_*.json/.md`锛?- [x] P2-C锛氭柊澧炲伐鍏峰崟娴?`test_diag_unified_tool.py`锛坉ry-run/鍙傛暟鎷艰/涓嬫父鎶ュ憡瑙ｆ瀽锛?- [x] P2-C锛氬畬鎴愭墿灞曞洖褰?`86 passed`锛堝惈 unified diag + scene_router + stream + retrieval + chat + emotion锛?- [x] P2-B锛氬畬鎴?`host_brain/obs` 涓?`host_brain/observability` 鑳藉姏娓呯偣骞跺缓绔嬬粺涓€涓昏矾寰勶紙`observability`锛?- [x] P2-B锛氬皢 metrics/session/trace/health/routes 涓诲疄鐜拌縼绉诲埌 `host_brain/observability/*`锛宍host_brain/obs/*` 淇濈暀鍏煎杞彂灞?- [x] P2-B锛氬簲鐢ㄥ眰璋冪敤鏂瑰垏鎹㈠埌 `host_brain/observability/*`锛坉ependencies/health_routes/system_handler/obs_routes锛?- [x] P2-B锛氭柊澧炲吋瀹瑰鍏ュ崟娴?`test_observability_module_compat.py`锛屽畬鎴愭墿灞曞洖褰?`149 passed`
- [x] 5.1锛氳ˉ榻愬叧閿摼璺?trace 瀛楁锛坄retrieval_latency_ms` / `first_token_latency_ms` / `background_tasks_latency_ms`锛?- [x] 5.1锛歚/v1/chat` 涓?`/v1/chat/stream` 瀹¤杈撳嚭澧炲姞 `audit_refs.trace_metrics`锛堝惈 retrieval銆侀 token銆佸悗澶勭悊鑰楁椂锛?- [x] 5.1锛氬畬鎴愭墿灞曞洖褰?`149 passed`锛坥bs + scene_router + stream + retrieval + chat + emotion锛?- [x] 5.1锛歚/obs/metrics` 澧炲姞瓒呮椂/闄嶇骇/澶辫触鐜囬槇鍊煎憡璀︼紙鐜鍙橀噺闃堝€?+ 绋冲畾鍛婅鐮侊級
- [x] 5.1锛氭柊澧?鏇存柊 observability 濂戠害鍥炲綊 `26 passed`锛坄test_obs_metrics_contract` + `test_perf_baseline_contract`锛?- [x] 5.1锛氭柊澧炲懆绋冲畾鎬ф姤鍛婂伐鍏?`tools/diag_stability_weekly.py` 骞舵帴鍏?`tools/diag.py stability` 瀛愬懡浠?- [x] 5.1锛氱敓鎴愰浠藉懆鎶?`WEEKLY_STABILITY_REPORT_20260310_005912.{json,md}`锛? 澶╃獥鍙ｏ紝鐘舵€?`WARN`锛? 鏉″憡璀︼級
- [x] 5.3锛氭柊澧炵伆搴﹂棬鎺э紙鎸変細璇?鐢ㄦ埛鍝堝笇鐧惧垎姣?+ 鍦烘櫙鐧藉悕鍗?+ allowlist锛夛紝`/v1/chat` 涓?`/v1/chat/stream` 宸叉帴鍏?- [x] 5.3锛氱伆搴﹀洖褰掗€氳繃 `15 passed`锛坔ost8b runtime route + stream/chat 璺緞锛?- [x] 5.3锛氭柊澧炲彂甯冮棬绂佽剼鏈?`tools/release_rollback_gate.py`锛堢粦瀹氬洖婊氭潯浠?+ 鍥炴粴鍛戒护杈撳嚭锛?- [x] 5.3锛氭柊澧炲洖婊氳繍琛屾墜鍐?`ROLLBACK_RUNBOOK_2026-03-10.md`锛屽苟鐢熸垚闂ㄧ鎶ュ憡 `RELEASE_ROLLBACK_GATE_20260310_010648.{json,md}`
- [x] 5.3锛氭柊澧?24h 澶嶇洏鑴氭湰 `tools/post_release_review.py`锛堣仛鍚堢ǔ瀹氭€?+ 鍥炴粴闂ㄧ缁撹锛?- [x] 5.3锛氱敓鎴愰浠藉鐩?`POST_RELEASE_REVIEW_20260310_010918.{json,md}`锛坄release_id=codex-20260309`锛岀姸鎬?`COMPLETE`锛?- [x] P2-A锛歚skills/registry.py` 鏂板缁熶竴 `@skill` 瑁呴グ鍣?+ `register_decorated()` 鎵弿娉ㄥ唽鍏ュ彛
- [x] P2-A锛氳ˉ榻?`SkillResult` 涓?`permission_model` 鎶借薄锛屾柊澧炶楗板櫒鍥炲綊 `60 passed`锛堝惈 skills/governance锛?- [x] P2-A锛氭柊澧?IDE Skills 灏佽 `ide_registry.py`锛坄open_cursor` / `run_pycharm_action` / `codex_task`锛?- [x] P2-A锛氭柊澧?MCP鈫扴kills 妗ユ帴 `mcp_skill_bridge.py`锛堢粺涓€ `mcp::<provider>::<tool>` 鍛藉悕锛屾敮鎸?broker 鍗曞叆鍙ｈ皟鐢級
- [x] P2-A锛氭柊澧?skills/mcp 鐩稿叧鍥炲綊 `26 passed`锛坉ecorator + ide + mcp bridge + hot_loader锛?- [x] P3锛氭柊澧?Bond 寮傛闃熷垪 `bond/async_queue.py`锛宍/v1/chat` 鏀逛负鍏ラ槦鍐欏叆锛堥槦鍒楅€€鍖栨椂 direct fallback锛?- [x] P3锛氭柊澧為槦鍒楀崟娴?`test_bond_async_queue.py`锛屽苟瀹屾垚 chat/stream/bond 鍥炲綊 `21 passed`
- [x] P3锛氫緷璧栧眰鎺ュ叆 MCP/Skills 鑷姩鍚堝苟锛歚get_skill_registry()` 澧為噺鍚屾 MCP 娉ㄥ唽琛ㄤ负 `mcp::<provider>::<tool>` 鎶€鑳藉叆鍙?- [x] P3锛氭柊澧炰緷璧栧眰妗ユ帴鍥炲綊 `23 passed`锛坄test_dependencies_mcp_skill_bridge` + `test_mcp_skill_bridge` + `test_tiered_governance_runtime_m44`锛?- [x] P3锛氳ˉ榻?Edge 3B 璺緞鎯呮劅灞傞€忎紶锛歚chat_handler` 灏?`emotion_context` 涓嬪彂鍒?edge fallback RPC锛坧resence/asr_compile锛夊苟鍐欏叆瀹¤
- [x] P3锛氭柊澧炴儏鎰熷叏璺緞鍥炲綊 `12 passed`锛坄test_chat_gaming_edge_route` + `test_chat_emotion_integration_m45` + graph/stream 濂戠害锛?- [x] P3锛氭柊澧為暱鏈熸寚鏍囩湅鏉垮伐鍏?`tools/diag_longterm_dashboard.py`锛岀粺涓€璐ㄩ噺/寤惰繜/鎴愭湰/鍙敤鎬у洓缁磋緭鍑猴紙JSON+Markdown锛?- [x] P3锛氭帴鍏ョ粺涓€璇婃柇鍏ュ彛 `tools/diag.py dashboard`锛屾柊澧炲洖褰?`10 passed`锛坉ashboard + unified + stability锛?- [x] P3锛氱敓鎴愰浠介暱鏈熸寚鏍囩湅鏉?`LONGTERM_METRIC_DASHBOARD_20260310_014526.{json,md}`锛?0 澶╃獥鍙ｏ紝鐘舵€?`WARN`锛?
---

## 0. 鎵ц瑙勫垯锛堝厛缁熶竴锛?
- [ ] 缁熶竴鍒嗘敮绛栫暐锛歚feat/p0-stream-retrieval`銆乣feat/p0-stream-emotion`銆乣feat/p1-scenerouter` 绛?- [ ] 姣忎釜浠诲姟蹇呴』鏈夛細浠ｇ爜鏀瑰姩 + 娴嬭瘯 + 瑙傛祴鎸囨爣 + 鍥炴粴鏂规
- [ ] 姣忔棩鍚屾鍥涢」锛氳繘搴︺€侀樆濉炪€侀闄┿€佹鏃ヨ鍒?- [ ] 涓ユ牸鍖哄垎鈥滈瀛楄妭璺緞鈥濆拰鈥滃悗鍙板紓姝ヨ矾寰勨€濓紝绂佹鍚庡彴鍐欏叆闃诲 SSE 杈撳嚭

---

## 1. P0锛堢珛鍗虫墽琛岋級鏈€澶у姛鑳界己鍙ｄ慨澶?
### P0-A 娴佸紡 Chat 鎺ュ叆鍏矾鍙洖锛堥浼?3-5 澶╋級

#### A1. 鐜扮姸姊崇悊涓庡熀绾挎祴閲忥紙0.5 澶╋級
- [x] 姊崇悊褰撳墠閾捐矾锛歚/v1/chat/stream -> chat_stream_handler -> astream_llm_chunks`
- [x] 璁板綍鍩虹嚎鎸囨爣锛氶瀛楄妭寤惰繜銆佸畬鏁村搷搴旇€楁椂銆佸彫鍥炲懡涓巼锛堝綋鍓嶅簲涓虹┖锛?- [x] 杈撳嚭閾捐矾鍥句笌鐡堕鐐癸紙鏂囨。鍖栵級

**浜у嚭**
- `docs/60_trace/baseline_stream_metrics_YYYY-MM-DD.md`

**楠屾敹鏍囧噯**
- 鏈夊彲澶嶇幇瀹為獙姝ラ鍜岃嚦灏?20 鏉℃牱鏈粺璁?
#### A2. 鍙洖缂栨帓鍣ㄨ璁★紙0.5 澶╋級
- [x] 璁捐 `RetrievalOrchestrator` 鎺ュ彛锛堝苟鍙戝叚璺彫鍥?+ 瓒呮椂鎺у埗 + 澶辫触闄嶇骇锛?- [x] 缁熶竴杈撳叆杈撳嚭鍗忚锛歲uery銆乻ession_state銆乺etrieval_bundle銆乼race_id
- [x] 纭畾瓒呮椂棰勭畻锛堝缓璁€婚绠?200ms锛屽崟璺秴鏃?120-180ms锛?
**浜у嚭**
- 璁捐鏂囨。 + 鎺ュ彛瀹氫箟锛堢被鍨嬫敞瑙ｏ級

**楠屾敹鏍囧噯**
- 鑳芥竻鏅版弿杩扳€滄垚鍔?閮ㄥ垎澶辫触/鍏ㄥけ璐モ€濅笁绉嶈矾寰?
#### A3. 鍏矾鍙洖鎺ュ叆瀹炵幇锛?-1.5 澶╋級
- [x] 鎺ュ叆 Episodic 鍚戦噺鍙洖
- [x] 鎺ュ叆 StyleCanon 妫€绱?- [x] 鎺ュ叆 WorldCanon 妫€绱?- [x] 鎺ュ叆 Bond 鎽樿璇诲彇
- [x] 鎺ュ叆鍏ㄦ枃妫€绱?- [x] 鎺ュ叆閲嶆帓铻嶅悎锛圧eranker/Fusion锛?
**褰卞搷妯″潡锛堥璁★級**
- `host_brain/app/chat_stream_handler.py`
- `host_brain/app/chat_context_builder.py`
- `host_brain/retrieval/*`
- `host_brain/memory/*`
- `host_brain/bond/*`

**楠屾敹鏍囧噯**
- 鍏矾缁撴灉鍙湪鍚屼竴 `retrieval_bundle` 涓娴嬪埌
- 鍗曡矾澶辫触涓嶅鑷存暣浣撴祦寮忓け璐ワ紙闄嶇骇鍙敤锛?
#### A4. PromptContext 鍓嶇疆鏋勫缓锛?.5-1 澶╋級
- [x] 鍦ㄨ繘鍏?LLM 娴佸紡璋冪敤鍓嶆瀯寤?PromptContext
- [x] 娉ㄥ叆 prompt payload锛屼繚璇佸彫鍥炵粨鏋滃弬涓庣敓鎴?- [x] 闄愬埗涓婁笅鏂囧ぇ灏忥紝閬垮厤 token 鐖嗙偢

**楠屾敹鏍囧噯**
- 鏃ュ織鍙 PromptContext 鏉ユ簮涓庢潯鐩粺璁?- 涓庘€滆８ LLM鈥濈浉姣旓紝鍥炵瓟鍙紩鐢ㄥ彫鍥炲唴瀹?
#### A5. 鎬ц兘涓庨€€鍖栫瓥鐣ワ紙0.5 澶╋級
- [x] 澧炲姞骞跺彂鎵ц涓庤秴鏃跺彇娑堢瓥鐣?- [x] 澧炲姞缂撳瓨锛堝彲閫夛級锛氱煭浼氳瘽绐楀彛鍐呴噸澶?query 澶嶇敤鍙洖
- [x] 瀹氫箟閫€鍖栭『搴忥細`鍏矾 -> 鏍稿績涓夎矾 -> 鏈€灏忎笂涓嬫枃 -> 瑁?LLM`

**楠屾敹鏍囧噯**
- 棣栧瓧鑺?P95 鎺у埗鍦ㄧ洰鏍囪寖鍥达紙寤鸿 <= 500ms锛?- 閫€鍖栬矾寰勬湁鏄庣‘鏃ュ織鏍囪瘑

#### A6. 娴嬭瘯涓庡洖褰掞紙0.5 澶╋級
- [x] 鍗曞厓娴嬭瘯锛氱紪鎺掑櫒銆佽秴鏃躲€佽瀺鍚堥€昏緫
- [x] 闆嗘垚娴嬭瘯锛氭祦寮忚矾寰勮兘甯﹀彫鍥炰笂涓嬫枃杈撳嚭
- [x] 鍥炲綊娴嬭瘯锛氶潪娴佸紡璺緞涓嶈鐮村潖

**楠屾敹鏍囧噯**
- 鏂板娴嬭瘯閫氳繃锛涘叧閿洖褰掗」閫氳繃

---

### P0-B 娴佸紡 Chat 鎺ュ叆鎯呮劅灞傦紙棰勪及 1-2 澶╋級

#### B1. 鎯呮劅蹇収鎺ュ叆锛?.5 澶╋級
- [x] 鍦ㄥ彫鍥炲悗銆丩LM 鍓嶈皟鐢?`emotion_context` 鏋勫缓蹇収
- [x] 灏嗘儏鎰熺姸鎬佹敞鍏?prompt锛堝惈寮哄害/鏍囩锛?
**楠屾敹鏍囧噯**
- trace 涓彲瑙傛祴 `emotion_snapshot`
- prompt 鏋勫缓鍖呭惈鎯呮劅瀛楁

#### B2. VTS 椹卞姩浜嬩欢鎵撻€氾紙0.5 澶╋級
- [x] 灏嗘儏鎰熸爣绛炬槧灏勪负 VTS 浜嬩欢
- [x] 浜嬩欢寮傛鎶曢€掞紝涓嶉樆濉?SSE

**楠屾敹鏍囧噯**
- SSE 杈撳嚭涓嶄腑鏂紱VTS 浜嬩欢鍗曠嫭鍙拷韪?
#### B3. 闂幆鏍￠獙锛?.5 澶╋級
- [x] 瀵?10+ 鏉℃儏鎰熸祴璇曡鏂欏仛楠岃瘉
- [x] 鏍￠獙鈥滄儏鎰熻瘑鍒?-> 杈撳嚭璇皵 -> VTS 鍔ㄤ綔鈥濅竴鑷存€?
**楠屾敹鏍囧噯**
- 涓€鑷存€ч€氳繃鐜囪揪鍒伴璁鹃槇鍊硷紙寤鸿 >= 80%锛?
---

## 2. P1锛堣繎鏈燂級浣撻獙鎻愬崌浠诲姟

### P1-A Host 閮ㄧ讲 Qwen3 8B锛堥浼?2-3 澶╋級
- [x] 瀹屾垚妯″瀷瑙勬牸閫夊瀷锛堜紭鍏?Q4_K_M锛?- [x] 瀹屾垚鎺ㄧ悊鏈嶅姟鍚姩/鍋ュ悍妫€鏌?闄嶇骇绛栫暐
- [x] 寤虹珛鏄惧瓨闂ㄦ帶锛歚sys_mode=GAMING` 鑷姩閫€鍑?Host 8B
- [x] 瀵规瘮 3B 涓?8B 璐ㄩ噺鍜屽欢杩燂紙A/B 鏍锋湰闆嗭級

**楠屾敹鏍囧噯**
- 闈?Gaming 鍦烘櫙榛樿璧?Host 8B
- 鏈夊彲鍥炴粴鍒?Cloud 鐨勫紑鍏?
### P1-B Gaming 鍒囨崲 Edge 3B锛堥浼?1-2 澶╋級
- [x] 灏?Gaming CHAT 璺敱鏀逛负 Edge 3B锛堟浛浠ｆā鏉块€€鍖栵級
- [x] 闄愬埗鑳藉姏锛氭瀬绠€鍥炲銆佸熀纭€ TTS銆佷笉瑙﹀彂澶嶆潅鍔ㄤ綔
- [x] 澧炲姞鍒囨崲鏃ュ織涓庡け璐ュ厹搴?
**楠屾敹鏍囧噯**
- Gaming 涓嬬ǔ瀹氳蛋 Edge 3B
- 鏄惧瓨鍐茬獊鏄庢樉涓嬮檷

### P1-C SceneRouter 涓嬫矇鍒?Graph 鍏ュ彛锛堥浼?2 澶╋級
- [x] 浠?FastAPI handler 绉婚櫎 `pre_intent_classifier`
- [x] 鏂板 Graph 绗竴鑺傜偣锛歚SceneRouterNode`
- [x] 杈撳嚭缁熶竴璺敱鍐崇瓥瀵硅薄锛坔ost/edge/cloud/skills锛?
**楠屾敹鏍囧噯**
- FastAPI 浠呭仛 I/O锛屼笉鍐嶆寔鏈夎矾鐢卞喅绛?- 璺敱绛栫暐鍙崟娴?
---

## 3. P2锛堜腑鏈燂級鏋舵瀯娌荤悊浼樺寲

### P2-A Skills 灞?OpenClaw 椋庢牸灏佽锛堥浼?5-7 澶╋級
- [x] 璁捐缁熶竴 `@skill` 娉ㄥ唽鍣?- [x] 鎶借薄 `SkillRegistry`銆乣SkillResult`銆佹潈闄愭ā鍨?- [x] 灏佽 Cursor / PyCharm / Codex 涓夌被鑳藉姏
- [x] 鎵撻€?MCP 宸ュ叿娉ㄥ唽涓?Skills锛堝崟鍏ュ彛锛?
**楠屾敹鏍囧噯**
- 鏂板鎶€鑳藉彲閫氳繃缁熶竴鍗忚鍙戠幇涓庤皟鐢?- MCP/Skills 涓嶅啀鍙岃建缁存姢

### P2-B observability 鍚堝苟锛堥浼?1 澶╋級
- [x] 娓呯偣 `obs/` 涓?`observability/` 閲嶅鑳藉姏
- [x] 鍚堝苟鐩綍鍜屽鍑鸿矾寰?- [x] 杩佺Щ璋冪敤鏂癸紝淇濈暀鍏煎灞傦紙鐭湡锛?
**楠屾敹鏍囧噯**
- 鍔熻兘涓嶅洖閫€锛涚洰褰曡亴璐ｆ竻鏅?
### P2-C 璇婃柇宸ュ叿鏁村悎锛堥浼?1-2 澶╋級
- [x] 鍚堝苟 `tools/_diag_*.py` 涓虹粺涓€鍏ュ彛鍛戒护
- [x] 鎻愪緵瀛愬懡浠わ細`retrieval`銆乣stream`銆乣emotion`銆乣graph`
- [x] 杈撳嚭缁熶竴鎶ュ憡鏍煎紡锛圝SON + 浜虹被鍙锛?
**楠屾敹鏍囧噯**
- 甯歌璇婃柇鍦烘櫙鏃犻渶鏌ユ壘澶氫釜鑴氭湰

---

## 4. P3锛堥暱鏈燂級闂幆寤鸿

- [x] Bond 鏇存柊缁熶竴寮傛闃熷垪鍖?- [x] 鎯呮劅灞傛帴鍏?Host 8B / Edge 3B / Cloud Graph 鍏ㄨ矾寰?- [x] MCP 涓?Skills 瀹屽叏鍚堝苟锛屽舰鎴愬敮涓€宸ュ叿鍏ュ彛
- [x] 寤虹珛闀挎湡鎸囨爣鐪嬫澘锛堣川閲忋€佸欢杩熴€佹垚鏈€佸彲鐢ㄦ€э級

---

## 5. 璺ㄩ樁娈靛叕鍏变换鍔★紙蹇呴』鎸佺画鎵ц锛?
### 5.1 瑙傛祴涓庡憡璀?- [x] 澧炲姞鍏抽敭閾捐矾 trace锛氬彫鍥炶€楁椂銆侀 token銆佸悗鍙颁换鍔¤€楁椂
- [x] 涓鸿秴鏃躲€侀檷绾с€佸け璐ョ巼璁剧疆闃堝€煎憡璀?- [x] 姣忓懆杈撳嚭绋冲畾鎬ф姤鍛?
### 5.2 娴嬭瘯浣撶郴
- [x] 涓烘祦寮忛摼璺鍔犲绾︽祴璇曪紙SSE 浜嬩欢椤哄簭銆佺粓姝㈡潯浠讹級
- [x] 涓鸿矾鐢卞櫒澧炲姞鍦烘櫙鍒嗙被娴嬭瘯闆?- [x] 涓烘儏鎰熷眰澧炲姞鍥炲綊鏍锋湰闆?
### 5.3 鍙戝竷涓庡洖婊?- [x] 姣忎釜闃舵鍑嗗鐏板害寮€鍏筹紙鎸夌敤鎴?鎸夊満鏅級
- [x] 姣忔涓婄嚎缁戝畾鍥炴粴鏉′欢涓庡洖婊氬懡浠?- [x] 鍙戝竷鍚?24h 鍋氫竴娆″鐩?
---

## 6. 寤鸿鎺掓湡锛堝彲鐩存帴鎵ц锛?
### 鍛?1锛圥0锛?- [ ] 瀹屾垚 P0-A锛堝叚璺彫鍥?+ PromptContext + 娴嬭瘯锛?- [ ] 瀹屾垚 P0-B锛堟儏鎰熷眰 + VTS 寮傛锛?
### 鍛?2锛圥1锛?- [x] 瀹屾垚 Host 8B 閮ㄧ讲涓庤瘎浼?- [x] 瀹屾垚 Gaming -> Edge 3B 鍒囨崲
- [ ] 瀹屾垚 SceneRouter 涓嬫矇鍒?Graph 鍏ュ彛

### 鍛?3-4锛圥2锛?- [ ] Skills 缁熶竴灏佽
- [ ] 鍙娴嬫ā鍧楀悎骞?- [ ] 璇婃柇宸ュ叿缁熶竴鍏ュ彛

### 鍛?5+锛圥3锛?- [ ] 寮傛闃熷垪涓庡叏璺緞闂幆
- [ ] 闀挎湡鎸囨爣娌荤悊

---

## 7. 浠诲姟瀹屾垚瀹氫箟锛圖oD锛?
- [ ] 浠ｇ爜鍚堝叆涓诲垎鏀苟閫氳繃 CI
- [ ] 瀵瑰簲娴嬭瘯琛ラ綈骞堕€氳繃
- [ ] 鏈夌洃鎺ф寚鏍囧拰鏃ュ織瀛楁
- [ ] 鏂囨。鏇存柊锛堟灦鏋勫浘銆佽繍琛屾墜鍐屻€佸洖婊氳鏄庯級
- [ ] 涓氬姟楠屾敹閫氳繃锛堣川閲?寤惰繜/绋冲畾鎬ц揪鏍囷級

---

## 8. 褰撳墠寤鸿绔嬪嵆寮€宸ラ」锛堜粖澶╋級

- [ ] 寮€鍒嗘敮锛歚feat/p0-stream-retrieval`
- [ ] 瀹屾垚 P0-A 鐨?A1/A2锛堢幇鐘舵⒊鐞?+ 缂栨帓鍣ㄨ璁★級
- [ ] 杈撳嚭绗竴鐗堟帴鍙ｈ崏鍥惧苟璇勫
- [ ] 鏄庣‘ P0 楠屾敹鎸囨爣闃堝€煎苟鍥哄寲鍒版枃妗?
