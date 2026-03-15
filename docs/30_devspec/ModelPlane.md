鍙椾紬锛欳ODEX
鏍囬锛氭ā鍨嬪钩闈紙Model Plane锛夎鏍艰鏄庯紙9 浜戣妭鐐归厤缃€佽矾鐢便€佸璁′笌闄嶇骇锛?
# 妯″瀷骞抽潰锛圡odel Plane锛夎鏍艰鏄?鐗堟湰锛歷1.2
鏃ユ湡锛?026-03-03

瀹氫綅锛氭湰鏂囦欢灞炰簬 DevSpec 瀛愯鏍硷紝鍙啓鈥滄€庝箞鍋?+ 濡備綍楠岃瘉锛堟祴璇曠偣/鏃ュ織鐐?濂戠害鐐癸級鈥濄€?鐩爣锛氭妸鈥? 浜戣妭鐐规€庝箞鍒嗗伐銆佹€庝箞璺敱銆佹€庝箞闄嶇骇銆佹€庝箞鏈€灏忓寲鍑虹綉銆佹€庝箞瀹¤鍥炴斁鈥濆啓鎴愮ǔ瀹氬绾︼紝鍏佽浣犱互鍚庢崲妯″瀷浣嗕笉鎹㈡不鐞嗐€?
---

## 0. 纭害鏉燂紙涓嶅彲杩濆弽锛?
0.1 涓绘潈涓庝簨瀹?- 鎵€鏈?LLM 鑺傜偣姘歌繙涓嶈鍐炽€佷笉鎵ц銆佷笉瀹ｅ憡鎵ц浜嬪疄锛涗换浣曗€滃凡鎵ц/宸蹭繚瀛?宸茬‘璁?宸插畬鎴愨€濆彧鑳界敱 Host 瀹¤閾炬憳瑕佹敮鎾戙€?- 浜戠杈撳嚭涓€寰嬮檷鏉冧负鑽夋锛氫笉寰楃洿閫氫富鏉冨啓鍏ャ€佷笉寰楃洿閫氭墽琛屻€?
0.2 鍑虹綉鏈€灏忓寲锛圓IRLOCK锛?- 浜戠鍙帴鏀舵渶灏忓繀瑕佷俊鎭細绂佹涓婅瀵嗛挜銆佸璁￠摼鍏ㄦ枃銆佺‖鐘舵€佸師鍊笺€佺瀵嗚蹇嗗叏鏂囥€?- 姣忔鍑虹珯蹇呴』鐢熸垚 sanitization_report_ref 涓?payload_digest锛堝啓鍏?airlock_log锛夛紝鍚﹀垯闃绘柇澶栧彂銆?
0.3 宸ュ叿鍙鎬э紙娓愯繘寮忓姞杞斤級
- LLM 姣忚疆鍙彲瑙?tool_meta_pack 鐨?Top-K 宸ュ叿鍏冩暟鎹瓙闆嗐€?- WRITE 绫诲伐鍏烽粯璁や笉鍙锛涘嵆浣垮彲瑙佷篃蹇呴』缁?Host Governor+Token 鎵嶈兘鎵ц銆?- tool_visibility_hash 蹇呴』闅?intent_log 涓€骞惰褰曪紝鐢ㄤ簬杩借矗鈥滃綋鏃舵ā鍨嬬湅瑙佷簡浠€涔堚€濄€?
0.4 浜屾寮忚緭鍑猴紙寮哄埗锛?- 绗竴娈碉細涓枃璇箟鍐呮牳锛堜骇 narrative_zh + intent_draft锛岃竟鐣岄攣姝伙級
- 绗簩娈碉細鏃ユ枃鍖栨覆鏌擄紙灏?narrative_zh 娓叉煋涓烘棩鏂?C 椋庢牸锛岀姝㈠姞鏂?鍗囩骇锛?
---

## 1. 杈撳嚭濂戠害锛圫trict JSON锛?
1.1 CoreDraft.v1锛堣涔?瑙勫垝绫昏妭鐐硅緭鍑猴級
- 鍗曚釜 JSON 瀵硅薄
- 瀛楁鏈€灏忛泦锛?  - narrative_zh: string
  - intent_draft: object|null
  - meta: object锛堝彲閫夛紱涓嶅緱鎵胯浇鎵ц浜嬪疄锛?
1.2 RenderDraft.v1锛堟覆鏌撳櫒杈撳嚭锛?- 鍗曚釜 JSON 瀵硅薄
- 瀛楁鏈€灏忛泦锛?  - rendered_ja: string锛堝彧鍋氶鏍兼覆鏌擄級
  - render_meta: object锛堝彲閫夛級

娓叉煋绂佷护锛?- 涓嶅緱鏂板浜嬪疄銆佷笉寰楀崌绾ф壙璇哄己搴︺€佷笉寰楀崌绾ц瘉鎹己搴︺€佷笉寰楀紩鍏ユ柊 intent銆?
1.3 Verifier.v1锛堟牎楠岃妭鐐硅緭鍑猴級
- 鍗曚釜 JSON 瀵硅薄
- 瀛楁鏈€灏忛泦锛?  - verdict: PASS|FAIL|UNKNOWN
  - issues: [{code, severity, span_hint, note}]
  - recommended_action: ALLOW|REWRITE|DOWNGRADE|ASK_CLARIFY

---

## 2. PromptContext锛圚ost鈫扖loud锛夊垎妲戒笌鍑虹綉瀛楁

2.1 鍒嗘Ы瀛楁锛堝繀椤伙級
- hard_snapshot锛氭《鍖栵紙energy_bucket/mood_bucket/sys_mode/safety_bucket锛夛紝涓嶅惈鍘熷€?- bond_summary锛歴tage/trend/capability_hint锛屼笉鍚暟鍊?- authoritative.world_canon锛歟xcerpt 绾у紩鐢紙evidence_level=CANON_REFERENCE_ONLY锛?- retrieval.episodic锛氫粎鎽樿+evidence_refs
- style_pack.style_canon锛歵one_only=true锛寀nsafe_as_fact=true
- tool_meta_pack锛歍op-K 宸ュ叿鍏冩暟鎹瓙闆嗭紙鍙┖锛?- constraints锛歴trict_json_output銆乶o_false_execution_claim銆乶o_canon_as_fact銆乼one_policy銆乧ontract_versions

2.2 AIRLOCK 鍑虹珯瀹¤瀛楁锛堝繀椤诲啓锛?- sanitization_report_ref
- payload_digest
- removed_fields / redactions 鎽樿
- destination锛坧rovider/region锛?
---

## 3. 9 浜戣妭鐐癸紙鍥哄畾鍒嗗伐 + 鍏蜂綋妯″瀷钀界偣锛?
璇存槑锛?- node_id/role 鍥哄畾锛沺rovider/model_id 鍙浛鎹紝浣嗗繀椤讳繚鎸佸绾︾増鏈吋瀹癸紱鑻ヤ笉鍏煎蹇呴』 bump model_profile_id銆?- 涓哄彲鍥炴斁涓庢姉婕傜Щ锛氫紭鍏堜娇鐢ㄢ€滃彲 pin 鐨勫叿浣撴ā鍨嬪瓧绗︿覆/蹇収鈥濓紱浠呭湪鏄庣‘鍏佽鏃朵娇鐢?latest 鍒悕銆?
3.1 鑺傜偣娓呭崟锛坴1.1 鍏蜂綋閰嶇疆锛?1锛塩ore_reasoner_primary锛坮ole=core锛?- 鐢ㄩ€旓細涓昏涔夋帹鐞嗭紙鍙欎簨+鎰忓浘鑽夋锛?- provider/model_id锛歡emini / gemini-2.5-pro
- 杈撳嚭锛欳oreDraft.v1
- 绾︽潫锛氳緭鍑哄敖閲忕煭锛堥伩鍏嶉暱鍙欎簨鍗犻绠楋級

2锛塩ore_reasoner_secondary锛坮ole=core_backup锛?- 鐢ㄩ€旓細涓绘ā鍨嬭秴鏃?闄愭祦鏃剁殑澶囬€夛紙鏇村揩鏇寸渷锛?- provider/model_id锛歡emini / gemini-2.5-flash
- 杈撳嚭锛欳oreDraft.v1

3锛塸lanner_constraints锛坮ole=planner锛?- 鐢ㄩ€旓細澶嶆潅浠诲姟绾︽潫鎷嗚В锛堝亸缁撴瀯锛屼笉鍐欓暱鍙欎簨锛?- provider/model_id锛歡emini / gemini-2.5-pro
- 杈撳嚭锛欳oreDraft.v1锛坣arrative_zh 鍙┖锛?
4锛塼ool_planner锛坮ole=tool_planner锛?- 鐢ㄩ€旓細鍦?tool_meta_pack 瀛愰泦鍐呴€夋嫨宸ュ叿涓庡弬鏁拌崏妗堬紙涓嶅緱鎵ц锛?- provider/model_id锛歡emini / gemini-2.5-flash锛坧in锛?- 杈撳嚭锛欳oreDraft.v1锛坕ntent_draft 蹇呭惈 tool_id + args 鑽夋锛?
5锛塻afety_verifier锛坮ole=verifier_safety锛?- 鐢ㄩ€旓細鎷︽埅涓夌被椋庨櫓锛氫吉鎵ц瀹ｅ憡銆佹壙璇哄崌绾с€佽瘉鎹崌绾с€佽秺鏉冨€惧悜
- provider/model_id锛歡emini / gemini-2.5-flash-lite
- 杈撳嚭锛歏erifier.v1

6锛塩onsistency_verifier锛坮ole=verifier_consistency锛?- 鐢ㄩ€旓細妫€鏌?Canon 寮曠敤鍙ｅ緞銆佹贩妲藉€惧悜銆佹牸寮忔紓绉诲€惧悜
- provider/model_id锛歡emini / gemini-2.5-flash-lite
- 杈撳嚭锛歏erifier.v1

7锛塺enderer_ja_c_style锛坮ole=renderer_ja锛?- 鐢ㄩ€旓細鏃ユ枃 C 椋庢牸娓叉煋锛堢煭鍙ャ€佸厠鍒躲€佸仠椤库€︹€︼級锛屼笉寰楀姞鏂?- provider/model_id锛歡emini / gemini-2.5-flash
- 杈撳嚭锛歊enderDraft.v1

8锛墂orkbench_translate锛坮ole=workbench_translate锛?- 鐢ㄩ€旓細缈昏瘧/鏈涓€鑷存€ц緟鍔╋紙娴佹按绾夸换鍔★級
- provider/model_id锛歡emini / gemini-2.5-pro
- 杈撳嚭锛欳oreDraft.v1锛堟垨鍚庣画 TranslateDraft锛?
9锛墂orkbench_code_assist锛坮ole=workbench_code锛?- 鐢ㄩ€旓細浠ｇ爜鍗忎綔鑽夋锛堜笉寰楀绉板凡鏋勫缓/宸查€氳繃锛?- provider/model_id锛歡emini / gemini-2.5-pro
- 杈撳嚭锛欳oreDraft.v1

3.2 Host 甯搁┗鍏滃簳锛堜笉璁″叆浜戣妭鐐癸紝浣嗗睘浜庢ā鍨嬪钩闈級
- host_fallback_policy锛坮ole=rule_engine_or_tiny_model锛?- 鐩爣锛?鈥? 绉掑紑鍙ｃ€佷簯瓒呮椂閫€鍖栥€佹渶浣庡悎瑙勬ā鏉?- 绾︽潫锛氫笉寰椾緷璧?GPU锛涗笉寰椾骇鐢熸墽琛屽鍛婏紱涓嶅緱鑷姩瑙﹀彂鎵ц

---

## 4. 璺敱涓庨摼璺紙LangGraph 瑙嗚锛?
4.0 pre_intent_classifier锛圚ost 灞傝交閲忚鍒欓鍒嗙被鍣紝M34锛?
**瀹氫綅**锛氳繍琛屽湪 `/v1/chat` 绔偣灞傦紝涓嶆槸 LangGraph 鑺傜偣锛屾棤 LLM 璋冪敤锛屽欢杩?< 1ms銆?
**杈撳叆**锛歚user_input: str`锛堢敤鎴峰師濮嬭緭鍏ユ枃鏈級

**杈撳嚭**锛歚scene_type_hint: str`锛圕HAT / EXEC_INTENT / WORK_TRANSLATE / WORK_CODE / VISION_UI锛?
**浼樺厛绾ц鍒?*锛堜粠楂樺埌浣庯級锛?1. VISION_UI锛氭埅鍥?灞忓箷/鐣岄潰绛夊叧閿瘝
2. WORK_CODE锛氫唬鐮?缂栫▼/鍑芥暟/python 绛夊叧閿瘝
3. WORK_TRANSLATE锛氱炕璇?translate/瑷炽仐銇?绛夊叧閿瘝
4. EXEC_INTENT锛氭墽琛?杩愯/璁剧疆/閰嶇疆绛夊叧閿瘝
5. CHAT锛堥粯璁わ級锛氬叾浣欐墍鏈夎緭鍏?
**涓?Governor 鐨勫叧绯?*锛?- `pre_intent_classifier` 杈撳嚭鍙奖鍝嶈妭鐐归摼璺€夋嫨锛堣蛋鍝潯璺級锛屼笉褰卞搷 Governor 瑁佸喅鏉?- 鏄惧紡浼犲叆鐨?`scene_type` 浼樺厛绾ч珮浜庨鍒嗙被缁撴灉锛坄scene_type_source = "explicit"` > `"pre_classifier"`锛?- `graph_meta.scene_type_source` 瀛楁璁板綍鏉ユ簮锛屼緵瀹¤杩借釜

4.1 鍦烘櫙鏋氫妇锛堟渶灏忥級
- CHAT锛氶棽鑱?闄即
- EXEC_INTENT锛氭墽琛屾剰鍥撅紙闇€瑕?Governor锛?- WORK_TRANSLATE锛氱炕璇戞祦姘寸嚎
- WORK_CODE锛氫唬鐮佸崗浣?- VISION_UI锛欳lawdBot/UI锛堥珮闂ㄦ锛?- CHANNEL_IO锛氬井淇?閫氶亾锛堢‘瀹氭€у鏄撅紝妯″瀷鍙啓鑽夋鏂囨湰锛?
4.2 涓ゆ寮忚緭鍑洪摼璺紙寮哄埗锛?- CHAT锛堟渶灏忛摼璺級锛歝ore_reasoner_primary 鈫?consistency_verifier 鈫?renderer_ja_c_style
- EXEC_INTENT锛歝ore_reasoner_primary 鈫?planner_constraints 鈫?tool_planner 鈫?safety_verifier 鈫?consistency_verifier 鈫?renderer_ja_c_style
- WORK_TRANSLATE锛歝ore_reasoner_primary 鈫?workbench_translate 鈫?consistency_verifier 鈫?renderer_ja_c_style
- WORK_CODE锛歝ore_reasoner_primary 鈫?workbench_code_assist 鈫?consistency_verifier 鈫?renderer_ja_c_style
- safety_verifier 鍦?CHAT 涓粯璁ら潪蹇呯粡锛屼粎鍦ㄩ珮椋庨櫓瑙﹀彂锛堟墽琛屾壙璇?瓒婃潈鍊惧悜/楂橀闄╅鍩熷缓璁瓑锛夋椂鎻掑叆鍒?consistency_verifier 鍓嶃€?瑙勫垯锛?- intent_draft 鍙兘鏉ヨ嚜 S1锛汼2 涓嶅緱鏂板/淇敼 intent_draft銆?- consistency_verifier 浣滀负瀵瑰杈撳嚭鍓嶇殑涓€鑷存€ч椄闂紝榛樿涓嶅緱璺宠繃锛堟晠闅滈檷绾ч摼璺櫎澶栵級銆?
4.3 3鈥? 绉掑紑鍙ｏ紙鍦ㄥ満鍥炲簲 + 瓒呮椂閫€鍖栵級
- t0锛欻ost 绔嬪嵆杈撳嚭鈥滃湪鍦哄洖搴旀ā鏉库€濓紙涓嶆壙璇烘墽琛岋級锛屽苟鍚姩浜戣皟鐢?- t1锛歝ore_reasoner_primary 瓒呮椂 鈫?core_reasoner_secondary
- t2锛氫粛澶辫触鎴栨牸寮忔紓绉?鈫?host_fallback_policy锛堟ā鏉?鏋佸皬妯″瀷锛?- 浠讳綍闄嶇骇蹇呴』鍐欏叆 error_log/quality.stage锛屽苟甯﹀彲缁熻 reason_code锛圖EPENDENCY_UNAVAILABLE/LLM_FORMAT_DRIFT 绛夛級

4.4 Gaming 妯″紡锛堟樉瀛樿娓告垙鍗犵敤锛?- Host 寮哄埗 NO_LOCAL_GPU锛氫粎鍏佽妯℃澘/瑙勫垯寮曟搸/CPU 鍏滃簳
- 浜戠浠嶅彲鐢紙鍙?AIRLOCK銆侀绠椾笌缃戠粶娉㈠姩褰卞搷锛?- 娓叉煋榛樿璧颁簯绔?renderer_ja_c_style锛涗簯涓嶅彲鐢ㄥ垯閫€鍖栦负 Host 绠€鐭棩鏂囨ā鏉匡紙鐢?Style Canon 鎻愪緵锛夛紝涓嶅緱鈥滆ˉ鍓ф儏鈥濄€?
---

## 5. 鎴愭湰涓庨暱搴︽不鐞嗭紙涓嶅啓浠锋牸锛屽彧鍐欏彲鎵ц棰勭畻鍙ｅ緞锛?
5.1 榛樿闄愰锛堝缓璁熀绾匡級
- CHAT锛欳ore 杈撳嚭 max_output_tokens_cap锛堢煭锛夛紝Renderer 杈撳嚭 max_output_tokens_cap锛堜腑鐭級
- WORK_*锛氬厑璁告洿闀匡紝浣嗗繀椤诲惎鐢ㄢ€滅粨鏋勫寲杈撳嚭+鍒嗘鈥濈瓥鐣ワ紙鐢?Flow/缂栨帓鎺у埗锛?- EXEC_INTENT锛氬彊浜嬬煭銆乮ntent_draft 缁撴瀯瀹屾暣浼樺厛

5.2 瑙﹀彂闄嶆湰绛栫暐鐨勬潯浠讹紙寤鸿锛?- 杩炵画 N 杞暱杈撳嚭銆佹垨鍗曡疆瓒呰繃 token_cap銆佹垨 unit_time_cost 瓒呰繃闃堝€?鈫?闄嶇骇鍒?core_reasoner_secondary 鎴栫缉鐭緭鍑烘ā鏉?- 娣卞害宸ヤ綔绔欎换鍔★紙缈昏瘧/浠ｇ爜锛変紭鍏堢敤涓撶敤鑺傜偣锛岄伩鍏嶆薄鏌撻棽鑱婇绠?
---

## 6. 瀹¤瀛楁锛堝繀椤诲啓鍏ワ級

6.1 姣忔浜戠妯″瀷璋冪敤锛堝缓璁啓鍏?llm_call_log 鎴?intent_log 鎵╁睍锛?- model_profile_id
- node_id
- provider / model_id锛堟垨蹇収鏍囪瘑锛?- prompt_profile_id锛堜粎瀛樺紩鐢?鍝堝笇锛?- input_digest / output_digest
- tool_visibility_hash锛堣嫢鏆撮湶宸ュ叿鍏冩暟鎹級
- latency_ms锛堝彲閫夊惈 TTFT锛?- token_usage锛堣嫢鍙緱锛?- sanitization_report_ref锛堝嚭绔欏繀椤绘湁锛?- outcome锛歋UCCESS|TIMEOUT|FORMAT_FAIL|POLICY_FAIL|DOWNGRADED

6.2 intent_log锛坧roposal锛夊繀椤诲寘鍚紙Intent responsibility锛?- proposed_intent_draft锛堝師鏂囷級
- proposer_node_id / model_id / prompt_profile_id
- tool_visibility_hash
- lint_result锛坰chema 鏍￠獙缁撴灉锛?
---

## 7. 鍥炲綊娴嬭瘯鍙ｅ緞锛堝缓璁紪鍙凤級

- T-LLM-01锛欳oreDraft/RenderDraft/Verifier 杈撳嚭 Strict JSON锛堝崟瀵硅薄銆佹棤鍓嶅悗缂€銆佸瓧娈垫渶灏忛泦锛?- T-Render-01锛氭覆鏌撲笉寰楀姞鏂?涓嶅緱鍗囩骇鎵胯涓庤瘉鎹?涓嶅緱寮曞叆 intent
- T-Route-CHAT-01锛欳HAT 鏈€灏忛摼璺浐瀹氫负 core_reasoner_primary 鈫?consistency_verifier 鈫?renderer_ja_c_style
- T-Route-CHAT-02锛欳HAT 涓?safety_verifier 榛樿涓嶈Е鍙戯紱鍛戒腑楂橀闄╄Е鍙戞潯浠舵椂蹇呴』鍑虹幇鍦?consistency_verifier 涔嬪墠
- T-CP-01锛歝ontext_pack 鍒嗘Ы闅旂锛坵orld/style/episodic/tool_meta 涓嶆贩妲斤級
- T-Intent-01锛歩ntent_log 瀛楁鏈€灏忛泦锛坧roposer/tool_visibility_hash 绛夛級
- T-Intent-02锛歱roposal/decision/exec 鍒嗙锛坕ntent_log 涓嶅緱鏀拺瀹屾垚鎬ц〃杩帮級
- T-Perf-01锛氳秴鏃堕€€鍖栭摼璺紙鍦ㄥ満鍥炲簲鈫掍富鈫掑鈫掑厹搴曪級杈撳嚭缁撴瀯绋冲畾
- T-Gaming-01锛欸aming 妯″紡涓?Host 涓嶄緷璧栨湰鍦?GPU锛堜粎鍏滃簳绛栫暐鍙敤锛?
---

## 8. 椋庨櫓澶囨敞锛堜笌鏈瀵硅瘽缁撹瀵归綈锛?
- 鍘婚檺鐗?abliterated锛氱姝㈢敤浜庤涔夊唴鏍镐笌瑁佸喅鐩稿叧鑺傜偣锛涘蹇呴』浣跨敤锛屼粎鍏佽鐢ㄤ簬 renderer_ja锛屽苟寮哄埗 RenderGuard 鏍￠獙"涓嶅緱鍔犳枡/鍗囩骇"銆?- 澶氫緵搴斿晢锛氱敤浜庢姉鍗曠偣鏁呴殰涓庢垚鏈不鐞嗭紝浣嗘墍鏈夎緭鍑轰粛蹇呴』杩?Host QA/Governor/瀹¤閾俱€?
---

## 9. Edge 绔厹搴曟ā鍨嬶紙涓嶇撼鍏?Agent 鍥撅紝浠呬綔涓鸿繙绋嬭兘鍔涳級
- Edge 3B 閲忓寲妯″瀷锛圤llama锛変笉浣滀负 LangGraph 鑺傜偣鍙備笌缂栨帓鏍戯紱Host 浠呬互鈥滆繙绋嬪伐鍏?鑳藉姏绔偣鈥濈殑鏂瑰紡璋冪敤銆?- 璋冪敤鐩殑浠呴檺锛氬湪鍦哄洖搴斻€佽緭鍏ユ竻娲椼€佹憳瑕佸帇缂┿€佹绱㈡彁绀猴紙draft-only锛夈€?- 绾︽潫锛氫笉寰椾骇鐢熻鍐宠涔夈€佷笉寰椾骇鐢熸墽琛屼簨瀹炲鍛婏紱澶辫触蹇呴』鍙娴嬪苟蹇€熷洖閫€鍒?Host 鍏滃簳鍙ｅ緞銆?
---

## 10. Governed Runtime Contract锛堟不鐞嗚繍琛屾椂濂戠害锛孧34锛?
> **鏍稿績瀹氫綅**锛歀angGraph 鍦ㄨ绯荤粺涓案杩滄槸 governed runtime锛屼笉鏄?autonomous orchestrator銆?> Natsume 閲囩敤 LangGraph锛屼絾涓嶇户鎵垮叾榛樿鑷不璇箟锛涚郴缁熶粎澶嶇敤鍏跺彈鎺ф墽琛岃兘鍔涳紝
> 鎵€鏈夎鍐炽€佹彁浜ゃ€侀檷绾с€佽竟鐣岄攣涓庡璁￠棴閿佸潎淇濈暀鍦?Host 涓绘潈鍩熷唴銆?
### 10.1 浜旀潯濂戠害鏉℃锛堢‖绾︽潫锛?
**Rule 1锛歊oute Commitment Rule**锛圡34 宸插疄鐜帮級
- 鏉′欢杈瑰彧鑳藉熀浜?`committed_route_token` 璺宠浆锛屼笉寰楃洿鎺ユ秷璐?LLM 鍘熷杈撳嚭
- `committed_route_token` 鍙兘鐢?Host Governor 鍐欏叆锛孡LM 鑺傜偣涓嶅彲鍐?- 浠ょ墝鏋氫妇锛歚GOV_OK_RENDER` / `GOV_FAIL_FALLBACK` / `GOV_AIRLOCK_REVIEW` / `GOV_SECONDARY_REASONER`

**Rule 2锛歅arallel Branch Pre-Issuance Rule**锛堝悗缁噷绋嬬瀹炵幇锛?- 骞惰鍒嗘敮蹇呴』鍏堣幏寰?Host 绛惧彂鐨?`branch_manifest`锛堝惈 `branch_id`銆乣audit_ticket_id`銆乣allowed_capabilities`锛?- fan-in 鏃跺繀椤婚€氳繃 `barrier_join()` 鏍￠獙鎵€鏈夊垎鏀殑 `audit_commit`
- 鏆備笉寮€鏀惧甫鍓綔鐢ㄧ殑骞惰鍒嗘敮

**Rule 3锛歍ransparent Subgraph Rule**锛堝悗缁噷绋嬬瀹炵幇锛?- 瀛愬浘鍐呴儴鑺傜偣蹇呴』瀵?Evidence Plane 閫忔槑锛堥€氳繃 `compile_governed_subgraph()` 宸ュ巶缂栬瘧锛?- 瀛愬浘缁撴潫鏃惰緭鍑?`subgraph_commit_summary`锛孍vidence Plane 淇濆瓨灞曞钩鍚庣殑 `graph_trace_flat`
- 涓嶅厑璁镐笉閫忔槑瀛愬浘瀛樺湪

**Rule 4锛欴ual-Clock Rule**锛堝悗缁噷绋嬬瀹炵幇锛?- 鐘舵€佹仮澶嶇敱 Execution Clock锛圠angGraph checkpoint/interrupt/resume锛夌鐞?- 瓒呮椂闄嶇骇鐢?Policy Clock锛圚ost Supervisor锛歚budget_ms_remaining`銆乣deadline_at_mono`锛夌鐞?- 涓よ€呰亴璐ｅ垎绂伙細LangGraph 璐熻矗"鑳戒笉鑳芥仮澶?锛孒ost 璐熻矗"鍏佷笉鍏佽鎭㈠"

**Rule 5锛歅hase-Locked Streaming Rule**锛堝悗缁噷绋嬬瀹炵幇锛?- Presence Stream锛堝湪鍦烘祦锛夛細闈炴潈濞侊紝鍙粰鍓嶇锛屼笉鍏佽浣滀负涓嬫父鑺傜偣杈撳叆
- Commit Stream锛堟彁浜ゆ祦锛夛細鏉冨▉锛屽彧鍦?S1 瀹屾垚骞剁粡杩?Governor 鍚庝骇鐢?- Render Stream锛堟覆鏌撴祦锛夛細鍙兘璇诲彇 Commit Stream 瀵瑰簲鐨勫揩鐓э紝涓嶅厑璁稿洖璇?Presence Stream

### 10.2 鐘舵€佸瓧娈典笁鍒嗘硶锛圡34 宸插疄鐜帮級

| 鍓嶇紑 | 璇箟 | 鍐欏叆鏂?|
|------|------|--------|
| `observed_*` | 瑙傛祴鍊硷紝鎻忚堪浜嬪疄 | Host 瑙傛祴灞?|
| `proposed_*` | LLM 鎻愭锛屽€欓€夊姩浣?| LLM 鑺傜偣 |
| `committed_*` | Host 宸叉彁浜ゅ喅绛?| Host Governor锛堝敮涓€锛?|

绀轰緥锛?- `proposed_intent_draft_json`锛歀LM 鎻愭瀛楁锛坧roposed_* 绫伙級
- `committed_route_token`锛欸overnor 瑁佸喅浠ょ墝锛坈ommitted_* 绫伙級

### 10.3 鑳藉姏浼樺厛绾т笁妗ｅ垎绫?
**绗竴妗ｏ細浼樺厛鍋氾紙鏀剁泭澶с€佷富鏉冮闄╀綆锛?*

| 鑳藉姏 | 鐘舵€?|
|------|------|
| `committed_route_token` + Route Commitment Rule | 鉁?M34 宸插疄鐜帮紙缂栬瘧鍐欏叆锛墊
| `committed_route_token` 鏉′欢杈硅鍙栵紙瀹屾暣钀藉湴锛?| 鉁?M35 宸插疄鐜?|
| `pre_intent_classifier` 杞婚噺棰勫垎绫?| 鉁?M34 宸插疄鐜?|
| `transparent_subgraph` 瀹¤閫忔槑瀛愬浘 | 鉁?M35 宸插疄鐜帮紙绗竴闃舵锛墊
| `dual-clock supervision` 鍙屾椂閽熺洃鐫?| 鉁?M36 宸插疄鐜帮紙ExecutionClock + PolicyClock锛墊
| Evidence Plane SQLite 鍐欏叆 | 鉁?M36 宸插疄鐜帮紙graph_evidence.py锛墊
| Checkpoint / Interrupt / Resume | 鉁?M39 宸插疄鐜帮紙鍙屾椂閽熸ā鍨嬩笅鏂偣缁窇锛孒ostPolicyGuard 鎭㈠鍓嶇疆妫€鏌ワ級|
| HostPolicyGuard锛堟仮澶嶅墠缃鏌ワ級 | 鉁?M39 宸插疄鐜帮紙瓒呮椂鏃跺啓鍏?SyntheticTimeoutEvent锛屼笉鐩存帴鎭㈠锛墊
| SyntheticTimeoutEvent | 鉁?M39 宸插疄鐜帮紙TIMEOUT_SYNTHETIC + DEADLINE_EXCEEDED + GOV_FALLBACK_SECONDARY锛墊

**绗簩妗ｏ細鍙互鍋氾紝浣嗚涓ユ牸闄愬煙**

| 鑳藉姏 | 闄愬埗鏉′欢 |
|------|---------|
| 鍙骞惰鍒嗘敮 + `branch_manifest` | 鉁?M37 宸插疄鐜帮紙graph_branch.py锛屽彧璇荤害鏉燂紝鏃犲壇浣滅敤锛墊
| Transparent Subgraph Evidence Plane 瀹炴椂鍐欏叆 | 鉁?M37 宸插疄鐜帮紙瀛愬浘鑺傜偣绾у疄鏃跺啓鍏ワ級|
| Presence Stream锛堝湪鍦烘祦锛?| 鉁?M38 宸插疄鐜帮紙鍙悜 UI 鍗曞悜杈撳嚭锛屼笉浣滀负涓嬫父鑺傜偣杈撳叆锛墊
| Commit Stream锛堟彁浜ゆ祦锛?| 鉁?M38 宸插疄鐜帮紙GOV_OK_RENDER 鏃朵骇鐢燂紝鍚?intent_commit + s1_output_hash锛墊
| Render Stream锛堟覆鏌撴祦锛?| 鉁?M38 宸插疄鐜帮紙鍙秷璐?Commit 蹇収锛孭haseLockGuard 楠岃瘉锛墊
| `barrier_join()`锛堝苟琛屽垎鏀?fan-in 鏍￠獙锛?| 鉁?M40 宸插疄鐜帮紙AuditTicket + BarrierJoinResult + barrier_join()锛宖ail closed锛墊
| `RenderGuard`锛堟覆鏌撹緭鍑烘巿鏉冿級 | 鉁?M40 宸插疄鐜帮紙validate_render_output()锛宭ocked_fields 淇濇姢锛墊

**绗笁妗ｏ細涓嶅缓璁己姹?鎺ヨ繎鍘熺敓浣撻獙"**

| 鑳藉姏 | 鍘熷洜 |
|------|------|
| 鍥惧唴鑷富闄嶇骇 | 闄嶇骇鏉冨睘浜?Host锛屼笉鑳戒笅鏀剧粰鍥?|
| 鍩轰簬 LLM state 鐨勮嚜娌昏矾鐢?| 杩濆弽涓绘潈绾︽潫鏍稿績鏉℃ |
| 璺ㄩ樁娈?token 绾ц仈鍔?streaming | 杈圭晫閿佸畾鎴愭湰鏋侀珮锛屾敹鐩婃湁闄?|
| 甯﹀壇浣滅敤鐨勫苟琛屾墽琛?| 瀹¤闂攣闅句互淇濊瘉锛岄闄╀笉鍙帶 |

### 10.4 鏈€缁堝畾浣嶅０鏄?
> LangGraph 鍦ㄨ绯荤粺涓殑瀹氫綅鍥哄畾涓?**governed runtime**锛岃€岄潪 **autonomous orchestrator**銆?> Host 涓绘潈绾︽潫鍐冲畾浜嗗浘鍐呰嚜娌诲彧鑳藉瓨鍦ㄤ簬鎵ц灞傦紝涓嶈兘杩涘叆瑁佸喅灞傘€?> 绯荤粺浼樺寲鐩爣涓嶆槸"閲婃斁 LangGraph 鍏ㄨ兘鍔?锛岃€屾槸"**浠ユ渶灏忔不鐞嗘垚鏈崲鍙栨渶楂樻墽琛屾敹鐩?*"銆?
