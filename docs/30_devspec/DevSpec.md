鍙椾紬锛欳ODEX
鏍囬锛氬紑鍙戣鏍?DevSpec锛堜粠闆跺彲閲嶅缓锛氱洰褰曢鏋朵笌濂戠害钀界偣锛?
鏈枃鍙啓鈥滄€庝箞鍋氣€濅笌鈥滃浣曢獙璇佲€濓紝涓嶅啓浜у搧鎰挎櫙銆?
# Natsume Ai 浜у搧寮€鍙戞枃妗ｏ紙Dev Spec锛?鐗堟湰锛歷1.6
鏃ユ湡锛?026-03-04
閫傜敤鏋舵瀯锛欻ost_Brain锛圥ython锛屽惈 LangGraph 缂栨帓锛?+ Natsume_Body锛圗dge锛?+ Cloud Exocortex + ClawdBot

鏈枃浠剁敤浜庢妸鐧界毊涔︿笌 PRD 鐨勫師鍒?闇€姹傝惤鎴愬彲瀹炵幇鐨勫伐绋嬭鏍硷細妯″潡杈圭晫銆佹帴鍙ｃ€丼chema銆佹棩蹇椼€佹祴璇曚笌閲岀▼纰戙€?鐩爣锛氬嵆浣夸粨搴撲负绌猴紝涔熻兘鎸夋湰鏂囨。缁欏嚭鐨勨€滅洰褰曢鏋?+ 濂戠害 + 閲岀▼纰戜氦浠樼墿鈥濓紝浠庨浂閲嶆柊寮€鍙戝埌鏈€灏忓彲杩愯闂幆銆?
---

## 0. 鏈涓庣‖绾︽潫锛堟墽琛屽彛寰勶級

0.1 鍗曞悜閾撅細璇箟鍐呮牳锛圠LM 鑽夋锛?鈫?Host Governor 鈫?Skills/Actuators 鈫?Audit
0.2 浜嬪疄锛氫粎浠ュ璁￠摼锛坕ntent/governor/skill_exec锛変笌纭姸鎬?SSOT 涓哄噯銆?0.3 浠讳綍鎵ц蹇呴』鎼哄甫 approval_token锛涢珮椋庨櫓蹇呴』 confirm_token锛涘彲閫嗗彉鏇撮粯璁よ姹?undo_token+manifest銆?0.4 浠讳竴 LLM 鑺傜偣姘歌繙涓嶆墽琛屻€佷笉瑁佸喅銆佷笉瀹ｇО宸叉墽琛岋紱鎵€鏈夆€滃凡瀹屾垚/宸蹭繚瀛?宸茬‘璁も€濆彧鑳藉紩鐢?Host 瀹¤鎽樿瀛楁銆?
0.5 鏂囨。鍏崇郴涓庤拷婧彛寰勶紙DevSpec 蹇呴』閬靛畧锛?DevSpec 鐨勪笂娓告槸 PRD 鐨?FR-闇€姹傜紪鍙凤紱涓嬫父鏄?DevTasks 鐨?TASK-浠诲姟缂栧彿涓?tests 鐨?T-缂栧彿銆?
寮哄埗杩芥函閾撅細
FR-鈥︼紙PRD锛氬仛浠€涔?楠屾敹锛?鈫?DevSpec 绔犺妭锛堟€庝箞鍋?濡備綍楠岃瘉锛?鈫?T-鈥︼紙娴嬭瘯缂栧彿锛氭柇瑷€鐐?鏃ュ織鐐?濂戠害鐐癸級
鈫?TASK-鈥︼紙浠诲姟鍗★細鏈€灏忎氦浠樺崟鍏冿紝寮曠敤 FR/T/绔犺妭锛?
DevSpec 鍐欎綔瑙勫垯锛堝伐绋嬪绾︾骇锛夛細
1锛夋瘡涓彲瀹炵幇鐨勮鏍兼潯鐩繀椤诲０鏄庘€滆鐩栫殑 FR 缂栧彿锛?+锛夆€濄€?2锛夋瘡涓叧閿绾︾偣蹇呴』鍒嗛厤鈥滄祴璇曠紪鍙?T-鈥︹€濓紝骞跺啓娓呮楠岃瘉鍙ｅ緞锛堣緭鍏?杈撳嚭/瀹¤瀛楁/鎷掔粷鐮侊級銆?3锛変换浣曚細浜х敓鍐欏叆锛堝璁?鍏崇郴/璁板繂/Canon/Vault锛夌殑鍔ㄤ綔锛屽繀椤诲啓鏄?evidence_refs 鐨勬潵婧愪笌璁板綍浣嶇疆锛圠edger 琛級銆?4锛塂evTasks 鐨勬媶鍒嗘潵婧愬彧鑳芥槸 DevSpec锛氫换鍔″崱涓嶅厑璁哥洿鎺ュ紩鐢ㄧ櫧鐨功/PRD 鏉ヨ噯閫犲疄鐜扮粏鑺傘€?
---

## 1. 浠ｇ爜浠撳簱缁撴瀯锛堜粠闆跺彲閲嶅缓瀹屾暣鐗堬級

鏈妭鏄€滃敮涓€鐩綍鐪熺浉鈥濄€備换浣曟柊妯″潡銆佽剼鏈笌璧勪骇蹇呴』钀藉埌杩欓噷鐨勬煇涓洰褰曚笅锛屽苟婊¤冻锛?- 鍗曞啓鑰呭師鍒欙細SSOT 瀛樺偍鐨勫啓鍏ヨ兘鍔涘彧鑳藉嚭鐜板湪鎸囧畾妯″潡锛?- 鍒嗘Ы闅旂锛歠acts / world_canon / style_canon / episodic / artifacts 涓嶆贩鐢紱
- 鍙璁★細鎵€鏈夐噸瑕佸啓鍏ラ兘鑳芥寚鍥?SQLite Ledger 鐨?evidence_refs锛?- 鍙洖鏀撅細鏈€灏忛摼璺兘鎸夊璁℃棩蹇楀鐜板叧閿喅绛栦笌鎵ц缁撴灉銆?
浠撳簱鍗曚竴 mono-repo锛圚ost 渚х敓鎴愪袱绔唬鐮佸苟鐗堟湰鍖栵紱Edge 鎷夊彇鍚屼竴浠撳簱锛夛細

### 1.1 鏍圭洰褰曪紙Repo Root锛?
绾︽潫锛氱洰褰曟爲鏄€滃敮涓€鐪熺浉鈥濄€備换浣曟柊澧炴ā鍧楀繀椤诲厛鍦ㄦ鐧昏锛涗换浣曞绾﹁祫浜э紙Flow/ModelPlane/Contracts锛夊繀椤绘湁绋冲畾钀界偣銆?
```aiignore
/
鈹溾攢 README.md
鈹溾攢 LICENSE
鈹溾攢 .editorconfig
鈹溾攢 .gitattributes
鈹溾攢 .gitignore
鈹溾攢 .pre-commit-config.yaml # 鏂囨。鍙椾紬闂ㄧ/鏍煎紡闂ㄧ锛堜笉鍦ㄦ湰鏂囨弿杩板懡浠わ級
鈹溾攢 configs/
鈹? 鈹斺攢 model_plane/
鈹?    鈹溾攢 node_registry.yaml
鈹?    鈹斺攢 model_profiles.yaml
鈹溾攢 prompts/
鈹? 鈹溾攢 CODEX_PROMPT.md # Codex 鍏ュ彛鎻愮ず璇嶏紙杈撳嚭鏍煎紡/琛屼负绾︽潫锛?鈹? 鈹溾攢 PROJECT_PROMPT.md # Project 鍏ュ彛鎻愮ず璇嶏紙鍙啓鏂囨。銆佷笉鏀逛唬鐮侊級
鈹? 鈹斺攢 llm_contract/
鈹?    鈹溾攢 core_output_contract.json # LLM鈫扝ost Strict JSON 杈撳嚭濂戠害绀轰緥锛堢粨鏋勭ず渚嬶紝涓嶆槸瀹炵幇锛?鈹?    鈹斺攢 refusal_templates.md # 缁熶竴鎷掔粷鍙欎簨鑼冨紡锛堥潪浜嬪疄锛?鈹溾攢 docs/
鈹? 鈹溾攢 00_DOCMAP.md
鈹? 鈹溾攢 10_whitepaper/
鈹? 鈹? 鈹斺攢 whitepaper_constitution_persona.md
鈹? 鈹溾攢 15_theory/
鈹? 鈹? 鈹斺攢 Theory.md
鈹? 鈹溾攢 20_prd/
鈹? 鈹? 鈹斺攢 PRD.md
鈹? 鈹溾攢 30_devspec/
鈹? 鈹? 鈹溾攢 DevSpec.md
鈹? 鈹? 鈹溾攢 DevTasks.md
鈹? 鈹? 鈹斺攢 ModelPlane.md
鈹? 鈹溾攢 40_agents/
鈹? 鈹? 鈹斺攢 AGENTS.md
鈹? 鈹溾攢 50_project/
鈹? 鈹? 鈹溾攢 PROJECT_BRIEF.md
鈹? 鈹? 鈹溾攢 PROJECT_TASKS.md
鈹? 鈹? 鈹斺攢 WORKSHEET_Project_Codex_Git_Flow.md
鈹? 鈹斺攢 60_trace/
鈹?    鈹溾攢 requirements_trace.md
鈹?    鈹斺攢 requirements_trace.csv
鈹溾攢 flows/
鈹? 鈹溾攢 exports/ # Flow 瀵煎嚭鐗╁綊妗ｏ紙鍥?閰嶇疆鍘熶欢锛?鈹? 鈹斺攢 active.pointer # 褰撳墠鐢熸晥鐗堟湰鎸囬拡锛坉ev/staging/prod 鈫?exports 鍐呰矾寰勶級
鈹溾攢 host_brain/
鈹? 鈹溾攢 pyproject.toml # 鎴?requirements*锛涘繀椤诲彲澶嶇幇渚濊禆鐗堟湰
鈹? 鈹溾攢 app/
鈹? 鈹? 鈹溾攢 main.py # HTTP API 鍏ュ彛锛堝仴搴锋鏌?閾捐矾鍏ュ彛锛?鈹? 鈹? 鈹斺攢 config.py # 閰嶇疆鍔犺浇锛堝垎灞傦細defaults/local/secret锛?鈹? 鈹溾攢 core/
鈹? 鈹? 鈹溾攢 prompt_context.py # Host鈫扡LM 杈撳叆鎵撳寘锛堝垎妲介殧绂伙級
鈹? 鈹? 鈹溾攢 qa.py # 杈撳嚭鍚堣锛歋trict JSON/浼墽琛?Canon-as-fact
鈹? 鈹? 鈹溾攢 metabolism.py # energy/mood/sys_mode 婕斿寲锛堢‖鐘舵€侊級
鈹? 鈹? 鈹溾攢 ids.py # intent_id/event_id/request_id 鐢熸垚瑙勫垯
鈹? 鈹? 鈹斺攢 errors.py # reason_code 涓庨敊璇璞★紙绋冲畾鏋氫妇锛?鈹? 鈹溾攢 governor/
鈹? 鈹? 鈹溾攢 schemas.py # Intent/GovernorOutput/Token/Manifest锛圫chema/楠岃瘉鍣級
鈹? 鈹? 鈹溾攢 rules.py # R1鈥揜7 Gate锛坱rust/energy/sys_mode/scope/safety/airlock锛?鈹? 鈹? 鈹溾攢 decision.py # 瑁佸喅鍏ュ彛锛氳緭鍏モ啋鏍￠獙鈫掑喅绛栤啋鍐欏璁?鈹? 鈹? 鈹斺攢 tokens.py # approval/confirm/undo 绁ㄦ嵁缁戝畾涓庢牎楠岃鍒?鈹? 鈹溾攢 skills/
鈹? 鈹? 鈹溾攢 registry.py # SkillMeta 娉ㄥ唽锛坈learance/risk/scope/reversible锛?鈹? 鈹? 鈹溾攢 loader.py # 鍞竴鍚堟硶鎵ц鍏ュ彛锛歵oken 鏍￠獙銆佸箓绛夈€佸璁″啓鍏?鈹? 鈹? 鈹斺攢 impl/ # 鍏蜂綋鎶€鑳藉疄鐜帮紙鍙厑璁?loader import锛?鈹? 鈹溾攢 tools/
鈹? 鈹? 鈹溾攢 catalog.py # ToolMeta SSOT锛堜粎鍏冩暟鎹級
鈹? 鈹? 鈹溾攢 selector.py # Top-K 宸ュ叿鍊欓€夐€夋嫨锛坱ool_meta_pack锛?鈹? 鈹? 鈹溾攢 broker.py # ToolBroker锛氬伐鍏锋墽琛屽崟鍏ュ彛锛坱oken+瀹¤锛?鈹? 鈹? 鈹斺攢 mcp_adapter.py # MCP provider鈫扵oolMeta 鏄犲皠涓庢不鐞嗗叆鍙?鈹? 鈹溾攢 audit/
鈹? 鈹? 鈹溾攢 sqlite_ledger.py # Ledger SSOT锛歩ntent/governor/skill_exec + 绱㈠紩
鈹? 鈹? 鈹溾攢 migrations/ # SQLite 琛ㄧ粨鏋勮縼绉绘弿杩帮紙涓嶅彲鎵嬪啓婕傜Щ锛?鈹? 鈹? 鈹斺攢 retention.py # 淇濈暀/褰掓。/GC 绛栫暐锛堝姩浣滃繀椤诲彲瀹¤锛?鈹? 鈹溾攢 bond/
鈹? 鈹? 鈹溾攢 neo4j_client.py
鈹? 鈹? 鈹溾攢 bond_dynamics.py # 鍞竴鍐?Neo4j锛圔ond SSOT 鍗曞啓鑰咃級
鈹? 鈹? 鈹溾攢 summarizer.py # Bond Summary 娲剧敓锛堜笉寰楀啓鍥?SSOT锛?鈹? 鈹? 鈹斺攢 migrations/ # Neo4j 绾︽潫/绱㈠紩鎻忚堪
鈹? 鈹溾攢 memory/
鈹? 鈹? 鈹溾攢 episodic.py # 鍚戦噺搴撳啓鍏?鍙洖锛堝繀椤?evidence_refs锛?鈹? 鈹? 鈹溾攢 chroma_client.py # 鍚戦噺搴撳鎴风灏佽锛堝啓鍏ヤ粎 episodic.py锛?鈹? 鈹? 鈹溾攢 canon_style_store.py # Style Canon SSOT锛堢増鏈寲銆乼one_only锛?鈹? 鈹? 鈹溾攢 canon_world_store.py # World Canon SSOT锛堟渶灏忔潈濞佸唴瀹归泦鍚堬級
鈹? 鈹? 鈹斺攢 canon_sync.py # Canon 鎵弿/绱㈠紩鍚屾锛堝啓鍏ラ渶鐣欑棔锛?鈹? 鈹溾攢 canon/
鈹? 鈹? 鈹溾攢 style/ # Style Canon 璧勪骇锛堟ā鏉?鑼冨紡锛岄潪浜嬪疄锛?鈹? 鈹? 鈹斺攢 world/ # World Canon 鏉＄洰璧勪骇锛堣瀹?鏈/涓嶅彉閲忥級
鈹? 鈹溾攢 lsp/
鈹? 鈹? 鈹溾攢 vault.py # Artifact Vault锛歮anifest/undo/GC锛堝崟鍐欒€咃級
鈹? 鈹? 鈹斺攢 policies/ # Vault policy profiles锛堜繚鐣?鑴辨晱/鐢熷懡鍛ㄦ湡锛?鈹? 鈹溾攢 airlock/
鈹? 鈹? 鈹溾攢 sanitizer.py # 鍑虹珯鑴辨晱鎶ュ憡鐢熸垚锛堝繀椤诲彲瀹¤锛?鈹? 鈹? 鈹斺攢 quarantine.py # 鍏ョ珯闅旂鍖猴紙涓嶅緱鐩撮€氭墽琛岋級
鈹? 鈹溾攢 channels/
鈹? 鈹? 鈹溾攢 wechat.py # 寰俊 I/O 浜嬩欢鍖栦笌瀹¤涓茶仈
鈹? 鈹? 鈹斺攢 vts.py # emotion_tag鈫抏xpression_id 鍥哄畾鏄犲皠涓?talk gate
鈹? 鈹斺攢 integrations/
鈹?    鈹斺攢 llm_gateway.py # LLM 璋冪敤灏佽锛坆locking/streaming 缁熶竴鍏ュ彛锛?鈹溾攢 edge_client/
鈹? 鈹溾攢 pyproject.toml
鈹? 鈹溾攢 senses/ # ears/eyes锛氶噰闆嗕笌鎽樿锛堜笉瑁佸喅锛?鈹? 鈹溾攢 actuators/ # mouth/speaker/avatar锛氭墽琛屽櫒锛堜笉瑁佸喅锛?鈹? 鈹溾攢 rpc/ # Host鈫擡dge RPC锛堝彧鎺ュ彈甯︾エ鎹殑鎵ц璇锋眰锛?鈹? 鈹溾攢 proofs/ # before/after 璇佹嵁閲囬泦锛堝啓鍏?Vault 鐨勫師濮嬫潗鏂欐潵婧愶級
鈹? 鈹斺攢 config.py
鈹溾攢 tests/
鈹? 鈹溾攢 contract/ # Schema/validator/gates/闈欐€佹壂鎻忥紙蹇€熴€佹棤澶栭儴渚濊禆浼樺厛锛?鈹? 鈹溾攢 e2e/ # 鏈€灏忛摼璺細Host鈫扡LM stub鈫扜overnor鈫扴kill stub鈫扐udit
鈹? 鈹溾攢 fixtures/ # intents銆佸潖杈撳嚭銆佹棩蹇楀洖鏀炬牱渚?鈹? 鈹斺攢 TESTPLAN.md # 鍙垪 T-xx锛氱敤渚嬧啋鏂█鐐光啋杈撳叆杈撳嚭锛堜笉鍐欏懡浠わ級
鈹溾攢 tools/
鈹? 鈹溾攢 static_scan/ # 鍗曞啓鑰?鏃佽矾璋冪敤/鍙椾紬闂ㄧ鎵弿瑙勫垯
鈹? 鈹溾攢 doc_lint/ # 鏂囨。澶撮儴鍙椾紬/鏍囬鏍￠獙瑙勫垯
鈹? 鈹斺攢 replay/ # 瀹¤鍥炴斁涓庢渶灏忓鐜板伐鍏凤紙鍙锛?鈹斺攢 .github/
   鈹斺攢 workflows/ # CI 瀹氫箟锛堝彧鎻忚堪搴旀鏌ヤ粈涔堬紝涓嶅湪鏈枃缁欏懡浠わ級
```

### 1.2 浠庨浂閲嶅缓鐨勬渶灏忓熀绾匡紙M0鈥揗3锛?鐩殑锛氫繚璇佲€滅┖浠撳簱涔熻兘鎸夐樁娈典氦浠樷€濄€傛瘡闃舵閮藉繀椤诲叿澶囧彲瀹¤銆佸彲楠岃瘉鐨勪骇鐗┿€?
M0锛氶鏋跺彲瑙ｆ瀽锛堜笉鎺ュ閮ㄤ緷璧栦篃鑳介棴鐜級
浜や粯鐗╋細
- Intent Schema锛堟渶灏忓瓧娈碉級+ GovernorOutput Schema锛坉ecision/reason_code/rule_trace锛?- SQLite Ledger锛坕ntent_log/governor_log/skill_exec_log 鏈€灏忎笁鑱旇〃锛?- LLM stub锛堟垨鍥哄畾鏍蜂緥杈撳叆杈撳嚭锛夊彲椹卞姩 Host 瀹屾暣閾捐矾

楠岃瘉鐐癸細
- 浠绘剰杈撳叆鏈€缁堣緭鍑轰繚鎸?Strict JSON 鍙В鏋愶紱
- 鎷掔粷蹇呭甫 reason_code锛?- skill_exec 鍙湪 APPROVE 涓?token 鍚堟硶鏃跺嚭鐜般€?
M1锛欸overnor 鍙不鐞嗭紙闂ㄧ鍙洖褰掞級
浜や粯鐗╋細
- R1鈥揜7 Gate 鐨勮鍒欓鏋讹紙鍗充娇闃堝€兼湭璋冧紭涔熷繀椤诲彲瑙傛祴锛?- approval/confirm/undo 鐨勭粦瀹氬瓧娈典笌鏍￠獙閫昏緫锛堝惈骞傜瓑锛?
楠岃瘉鐐癸細
- 鍚屼竴璺緞鍚岀増鏈笅 reason_code 绋冲畾锛?- 缂虹エ鎹?瓒婃潈 scope/绂佺敤 sys_mode 瑙﹀彂鏄庣‘鎷掔粷骞跺啓瀹¤銆?
M2锛氬叚搴撳垎宸ヨ惤鍦帮紙鍐欏叆杈圭晫鍙潤鎬侀獙璇侊級
浜や粯鐗╋細
- bond_dynamics锛圢eo4j 鍗曞啓鑰咃級+ bond_update_log 鐣欑棔
- episodic锛堝悜閲忓簱鍗曞啓鑰咃級+ memory_ops_log 鐢熷懡鍛ㄦ湡鐣欑棔
- canon_style_store / canon_world_store锛堢増鏈寲銆佸彉鏇寸暀鐥曪級
- vault锛坢anifest/undo/GC 绾︽潫涓庡璁★級

楠岃瘉鐐癸細
- 浠绘剰闈炲崟鍐欒€呮ā鍧楁棤娉曞啓鍏ュ搴?SSOT锛堝彲鐢遍潤鎬佹壂鎻忔姤鍛婅瘉鏄庯級锛?- episodic/world/style 鐨勬敞鍏ュ垎妲介殧绂讳笖涓嶄細琚?QA 璇綋浜嬪疄銆?
M3锛欵dge 鑱旇皟锛堣瘉鎹潗鏂欏彲鍥炴斁锛?浜や粯鐗╋細
- Host鈫擡dge RPC 濂戠害锛氬彧鎺ュ彈甯︾エ鎹殑鎵ц璇锋眰
- proofs鈫扸ault锛歜efore/after 璇佹嵁鑳藉舰鎴?manifest 涓茶仈鍒?intent_id

楠岃瘉鐐癸細
- UI/瑙嗚鐩稿叧鑳藉姏鍦ㄨ瘉鎹己澶辨椂蹇呴』闄嶇骇/鎷掔粷锛?- Vault GC 涓嶇牬鍧?undo 绐楀彛锛堢瓥鐣ュ彲瀹¤锛夈€?
### 1.2.1 鍐峰惎鍔ㄧ瓥鐣ワ紙NoKey/Service Unavailable锛氭湇鍔″繀椤诲瓨鍦紝鍏佽鏆備笉鍙敤锛?鐩爣锛氬湪鈥滄棤浜戠 API Key / 浠讳竴鏈嶅姟鏆備笉鍙敤鈥濈殑鏉′欢涓嬶紝浠嶈兘浜や粯鏈€灏忛棴鐜細杈撳嚭鍙В鏋愩€佸璁″彲鍥炴斁銆佹棤浼墽琛屽鍛娿€?
A锛変氦浠樿竟鐣岋紙椤圭洰鑼冨洿鍙ｅ緞锛?- 鎵€鏈夋湇鍔￠」涓衡€滃繀椤诲疄鐜般€佺己涓€涓嶅彲鈥濓紙瑙?PRD 2.3锛夈€?- 鍏佽瀛樺湪鈥滆繍琛屾椂鏆備笉鍙敤/鏈厤缃?闄嶇骇鈥濈殑鎯呭喌锛屼絾蹇呴』锛?  - 鏈夌ǔ瀹氭帴鍙ｄ笌鍋ュ悍鐘舵€侊紱
  - 闄嶇骇鏃惰緭鍑虹粨鏋勭ǔ瀹氫笖 reason_code/stage 鍙粺璁★紱
  - 涓嶅緱鎶娾€滀笉鍙敤/鏈Е鍙戔€濅吉瑁呬负鈥滀簨瀹炰负绌衡€濄€?
B锛夊喎鍚姩鏈€浣庡舰鎬侊紙浠庨浂鍙噸寤猴級
- SQLite Ledger锛氬繀椤诲彲鍐欏叆鏈€灏忎笁鑱旂敾锛涚己澶卞垯鏃犳硶绉颁负鍙洖鏀剧郴缁熴€?- Neo4j Bond SSOT / Episodic 鍚戦噺搴擄細蹇呴』瀛樺湪瀵瑰簲鐨勮繛鎺ュ眰涓庡仴搴风姸鎬侊紱涓嶅彲鐢ㄦ椂瀵瑰鍙緭鍑洪檷绾у彛寰勶紙绌?榛樿锛夛紝骞惰褰?stage/reason_code銆?- Style/World Canon / Vault锛氬繀椤讳互鐗堟湰鍖栬祫浜у舰鎬佸瓨鍦紱鍙负绌轰絾涓嶅緱瀵艰嚧绯荤粺浼€犺瀹氭垨浼€犱簨瀹炪€?- Cloud Exocortex锛氬繀椤诲瓨鍦?gateway + airlock锛涙棤 key 鏃跺繀椤昏繘鍏モ€滃鑴戜笉鍙敤鈥濋檷绾ц矾寰勶紝涓斾笉褰卞搷缁撴瀯绋冲畾銆?- Edge锛堣闊?澶栨樉锛夛細蹇呴』瀛樺湪 RPC 涓庤兘鍔涚鐐癸紱涓嶅彲鐢ㄦ椂 Host 杩涘叆鈥滄棤韬綋妯″紡鈥濋檷绾ц矾寰勶紝浠嶄繚鎸佺粨鏋勭ǔ瀹氫笌瀹¤鍙洖鏀俱€?
C锛夊彲瑙傛祴锛堝繀椤伙級
- 浠讳竴鏈嶅姟涓嶅彲鐢ㄤ笉寰楀鑷村穿婧冿紱蹇呴』杈撳嚭鍙В鏋愮粨鏋勶紝骞跺湪 quality.stage 鏍囨敞澶辫触鍙戠敓鐨勯樁娈点€?- 鍚岀被缂哄け鍦ㄥ悓鐗堟湰涓?reason_code 绋冲畾鍙粺璁°€?- 浠讳綍 LLM锛堝惈 Edge 鍏滃簳妯″瀷锛夎緭鍑轰粛蹇呴』婊¤冻 Strict JSON 濂戠害锛屽苟鍙?QA锛堜吉鎵ц銆丆anon-as-fact銆佹牸寮忔紓绉伙級绾︽潫銆?
### 1.3 蹇呴』涓嶈繘浠撳簱鐨勫唴瀹癸紙浠庨浂閲嶅缓鐨勫彲澶嶇幇鍓嶆彁锛?- 浠讳綍 secret锛圓PI key銆乼oken銆佷釜浜鸿处鍙峰嚟鎹級锛氬彧鑳戒互鈥滄ā鏉挎枃浠?+ 鏈湴瑕嗙洊鈥濈殑鏂瑰紡瀛樺湪銆?- artifacts锛堜簩杩涘埗鎴浘/闊抽绛夛級锛氶粯璁や笉杩?Git锛屽繀椤婚€氳繃 Vault manifest 鍙拷婧叾鏉ユ簮涓庣敓鍛藉懆鏈熴€?- local overrides锛堟湰鍦拌矾寰勩€佽澶?ID锛夛細蹇呴』鐙珛浜庡彲澶嶇幇閰嶇疆灞傦紝涓嶅緱鍐欐鍦ㄤ唬鐮佷笌鏂囨。姝ｆ枃涓€?
### 1.4 鐩綍鑱岃矗绾㈢嚎锛堜粠闆堕噸寤烘椂鏈€鏄撹俯鍧戯級
- flows/ 鍙瓨 Flow 瀵煎嚭鐗╀笌鎸囬拡锛涗笉瀛樷€滀簨瀹炴簮鈥濅笌鈥滄墽琛岃剼鏈€濄€?- host_brain/audit/ 鏄€滃彂鐢熻繃浠€涔堚€濈殑绱㈠紩鏋㈢航锛涗换浣曞啓鍏?SSOT 鐨勫姩浣滃繀椤昏兘鎸囧洖瀹冦€?- host_brain/canon/world/ 鐨勬潯鐩彲琚紩鐢ㄧ敤浜庝竴鑷存€э紝浣嗕笉寰楃敤浜庡鍛婂凡鎵ц锛涙绫诲鍛婂彧璁ゅ璁￠摼銆?- tests/contract 璐熻矗濂戠害绋冲畾鎬э紱tests/e2e 璐熻矗鏈€灏忛棴鐜紱涓よ€呴兘涓嶅簲渚濊禆鐪熷疄澶栭儴鐜鎵嶈兘璺戦€氬叾鏂█閫昏緫锛堥€氳繃 stub/fixtures 杈炬垚锛夈€?
### 1.5 閰嶇疆璧勪骇锛欶low 璧勪骇锛圠angGraph 鍥?閰嶇疆锛変笌鎸囬拡濂戠害锛堝彲閮ㄧ讲 SSOT锛?瀹氫綅锛欶low 璧勪骇灞炰簬鈥滃彲閮ㄧ讲閰嶇疆璧勪骇鈥濓紝鍏惰涓哄彉鍖栧繀椤诲彲鐗堟湰鍖栥€佸彲鍥炴粴銆佸彲杩芥函锛涗絾瀹冧笉鎵胯浇鎵ц浜嬪疄锛屾墽琛屼簨瀹炰粛浠ュ璁￠摼涓哄噯銆?
鐩綍涓庢枃浠讹紙璇箟鍥哄畾锛夛細
- 瀵煎嚭鐗╁綊妗ｇ洰褰曪細flows/exports/锛堜粎淇濆瓨 Flow 瀵煎嚭鐗╁師浠讹級
- 褰撳墠鐢熸晥鎸囬拡锛歠lows/active.pointer锛堝繀椤讳负鍗曚釜鍚堟硶 JSON 瀵硅薄锛?- 瀹¤绱㈠紩锛堟枃妗ｅ眰锛夛細docs/60_trace/flows/index.md锛堟寜鐜缁欏嚭鈥滃綋鍓嶇敓鏁堢増鏈€濅笌鍙樻洿鎽樿锛?
active.pointer 鏍煎紡锛堝繀椤伙級锛?- 缁撴瀯锛氭寜鐜鏄犲皠鍒?exports 鍐呯殑鐩稿璺緞锛堟垨鏂囦欢鍚嶏級锛岄敭闆嗗悎鍥哄畾涓?dev/staging/prod銆?- 绾︽潫锛?  - 鎸囧悜鐨勬枃浠跺繀椤诲瓨鍦ㄤ簬 flows/exports/ 涓嬶紱
  - 浠讳竴鐜缂哄け閿垨鎸囧悜瓒婄晫锛堜笉鍦?exports 涓嬶級瑙嗕负閰嶇疆涓嶅悎瑙勶紱
  - docs/60_trace/flows/index.md 鐨勨€滃綋鍓嶇敓鏁堢増鏈€濆繀椤讳笌 active.pointer 涓€鑷达紙鍏佽 index 棰濆璁板綍瀵煎嚭鏃堕棿/鎽樿锛屼絾涓嶅厑璁告寚鍚戜笉鍚屾枃浠讹級銆?
鏈€灏忓洖褰掓祴璇曠紪鍙凤紙閰嶇疆璧勪骇锛夛細
- T-Flow-01锛歛ctive.pointer 鍚堟硶鎬т笌鎸囬拡瀛樺湪鎬э紙缁撴瀯/閿泦鍚?璺緞鑼冨洿/鐩爣鏂囦欢瀛樺湪锛?- T-Flow-02锛歍race 绱㈠紩涓€鑷存€э紙index.md 鐨勬寜鐜鐢熸晥鐗堟湰涓?active.pointer 瀹屽叏涓€鑷达級

---

## 2. 杩愯鏃惰繘绋嬫嫇鎵戜笌閫氫俊

## 2. 杩愯鏃惰繘绋嬫嫇鎵戜笌閫氫俊

2.1 Host 杩涚▼锛堜富鏉冭鍐充笌缁熶竴缂栨帓锛?- 瀵瑰鎻愪緵锛歚POST /v1/chat`锛堢敤鎴疯緭鍏?鈫?杈撳嚭 narrative锛坮endered_ja锛?+ intent_draft + quality锛涘彲閫?debug.core_narrative_zh锛?- 缁熶竴缂栨帓锛氭墍鏈?Agent 涓庨摼璺紪鎺掍粎鍦?Host 鍙戠敓锛汦dge 绔粍浠朵粎浣滀负鈥滆繙绋嬭兘鍔涙彁渚涜€呪€濊璋冪敤锛坉raft-only锛屼笉鍏风粓瀹℃潈锛夈€?- 蹇呭鍐呴儴鏈嶅姟锛堝繀椤诲疄鐜帮級锛欸overnor/Token/Audit銆丼QLite Ledger銆丯eo4j Bond SSOT銆丒pisodic 鍚戦噺搴撱€丒mbedding銆丷eranker銆丷ecall/Fusion銆丼creen Capture銆丱CR/Vision銆丆lawdBot銆丆loud Gateway + AIRLOCK銆丱bservability銆?- 杩愯鏃朵笉鍙敤锛氫换涓€鏈嶅姟涓嶅彲鐢ㄥ繀椤诲彲瑙傛祴闄嶇骇骞朵繚鎸佽緭鍑虹粨鏋勭ǔ瀹氾紙涓嶅緱浼墽琛岋級銆?
2.2 Edge 杩涚▼锛堣韩浣撹兘鍔涳細璇煶閾句笌澶栨樉锛?- 瀵瑰鎻愪緵锛堢粰 Host锛夛細RPC 鑳藉姏绔偣锛堢ず渚嬪懡鍚嶏紝鍏蜂綋璺緞浠?edge_client/rpc 缁熶竴娉ㄥ唽琛ㄤ负鍑嗭級锛?  - asr锛氫笂閫佽浆鍐欎簨浠讹紙asr_chunk锛歵ext/timestamps/confidence/alternates 鍙€夛級
  - speak锛氭帴鏀?Host 鐨勬渶缁堟枃鏈苟瀹屾垚 TTS+鎾斁锛堟敮鎸?barge-in 鎵撴柇锛?  - vts锛歵alk gate 涓?emotion_tag鈫抏xpression_id 鍥哄畾鏄犲皠瑙﹀彂
  - fallback_llm锛氬厹搴曡崏妗堣兘鍔涳紙榛樿鍗歌浇銆佷綆棰戣Е鍙戙€乨raft-only锛?  - health锛氳繑鍥?Edge 鑳藉姏鐭╅樀涓庡綋鍓嶈祫婧愮姸鎬侊紙鍚?GPU 鏄惧瓨涓庨槦鍒楃姸鎬侊級
- 璧勬簮闂搁棬锛堝繀椤伙級锛欵dge 渚у繀椤诲疄鏂戒换鍔′紭鍏堢骇涓庝簰鏂ョ瓥鐣ワ紝闃叉 ASR/TTS/鍏滃簳LLM 楂樺嘲鍙犲姞瀵艰嚧涓嶇ǔ瀹氾紱浼樺厛绾т负 ASR>鎾斁>TTS鍚堟垚>鍏滃簳LLM銆?
2.3 Cloud Exocortex
- Host 璋冪敤澶栭儴妯″瀷锛氱粺涓€缁?`integrations/llm_gateway.py` 鍑虹珯锛堢粡 AIRLOCK 鑴辨晱锛?- 澶栭儴杈撳嚭锛氫弗鏍?JSON锛坣arrative + intent_draft 鎴栨覆鏌撴枃鏈級锛屼竴寰嬮檷鏉冧负鑽夋锛屼笉寰楃洿閫氭墽琛?
2.4 閫氫俊瀹夊叏锛堟渶灏忓疄鐜帮級
- Host鈫扙dge锛氭瘡娆¤姹傚甫 `request_id` + `HMAC(signature)`锛堝叡浜瘑閽ワ級+ `ts_ms` + `nonce`
- Edge 缁存姢 nonce 缂撳瓨绐楀彛锛屾嫆缁濋噸鏀撅紙REPLAY_DETECTED锛夈€?
2.5 Edge 鍏滃簳 LLM 鐨勬帴鍏ユ柟寮忥紙宸ュ叿鍖栵紝涓嶄綔涓?Agent锛?- 瀹氫綅锛欵dge 鍏滃簳 LLM 涓嶆槸 Agent锛屼笉鍙備笌涓婚摼璺鍐筹紱浠呮彁渚?draft-only 鑳藉姏锛?  - presence锛堝湪鍦哄洖搴旇崏妗堬級
  - asr_compile锛堣浆鍐欐竻娲椾笌鍊欓€夛級
  - retrieval_hints锛堟绱㈡彁绀猴細rewrite/keywords/entities/constraints锛?  - context_compress锛堟粴鍔ㄦ憳瑕侊級
- 杈撳嚭绾︽潫锛氭墍鏈夎緭鍑哄繀椤讳负鍗曚釜鍚堟硶 JSON锛涘繀椤诲甫 draft_only=true锛涗笉寰楀寘鍚喅绛栧瓧娈碉紙allow/deny/confirm_required 绛夛級锛涗笉寰楀寘鍚换浣曟墽琛屼簨瀹炲鍛娿€?- 澶辫触绛栫暐锛欵dge 鍏滃簳 LLM 涓嶅彲鐢?鍔犺浇澶辫触蹇呴』蹇€熷け璐ュ苟杩斿洖绋冲畾閿欒鐮侊紱Host 渚у繀椤诲洖閫€鍒拌鍒?妯℃澘鍏滃簳锛屼笉闃诲璇煶涓婚摼銆?
---

## 3. Schema锛欼ntent锛堝繀椤诲己绫诲瀷锛?
瀹氫綅锛欼ntent 鏄€滆瑷€鈫掍唬鐮佲€濈殑鍞竴杈圭晫瀵硅薄锛屾槸 Host Governor 鍙鍐崇殑璇锋眰瀵硅薄銆侺LM 鍙兘鎻愬嚭 intent_draft锛堣崏妗堬級锛汬ost 鍙帴鍙楀己绫诲瀷 Intent锛堝厑璁稿鏉捐В鏋愪慨璇硶锛屼絾绂佹琛ヨ涔夛級銆?
瑕嗙洊闇€姹傦細FR-INTENT-001~003
鍥炲綊鍙ｅ緞锛歍-18~T-22

### 3.1 Intent 鏈€灏忓瓧娈碉紙鍥哄畾 JSON 瀵硅薄锛?Host 鍙帴鍙椻€滃崟涓?JSON 瀵硅薄鈥濓紝涓嶆帴鍙楁暟缁?澶氭鏂囨湰銆傛渶灏忓瓧娈靛涓嬶紙瀛楁鍚嶈涔夊浐瀹氾級锛?- schema_version锛氬浐瀹氬瓧绗︿覆锛堝 "Natsume.intent.v1"锛?- id锛氬叏灞€鍞竴锛圲LID/UUID 鍧囧彲锛涘繀椤诲彲瀹¤鍥炴斁锛?- ts_ms锛氭绉掓椂闂存埑锛堢敤浜庤繃鏈熺獥锛?- session_id锛氫細璇濇爣璇?- seq锛氫細璇濆唴鍗曡皟閫掑锛堥槻涔卞簭/鍥炴粴锛?- type锛氭灇涓撅紙鎰忓浘绫诲埆锛?- action锛氭灇涓撅紙Host allowlist锛?- target锛氭灇涓撅紙Host allowlist锛?- args锛歰bject锛堟寜 action 寮虹被鍨嬫牎楠岋級
- nonce锛氶殢鏈轰覆锛堥槻閲嶆斁锛涢渶缂撳瓨绐楀彛锛?
纭害鏉燂細
- type/action/target 蹇呴』 allowlist锛涗换浣曟湭鐭ュ€间竴寰?REJECT锛圛NTENT_SCHEMA_INVALID锛夈€?- args 蹇呴』鎸?action 鐨?ArgsModel 鏍￠獙锛涘け璐ヤ竴寰?REJECT锛圛NTENT_ARGS_INVALID锛夈€?- seq 蹇呴』鍗曡皟閫掑锛涗贡搴?鍥炴粴涓€寰?REJECT锛圫EQ_INVALID锛夈€?- id/nonce 蹇呴』鍋氶噸鏀句繚鎶わ紱閲嶅涓€寰?REJECT锛圧EPLAY_DETECTED锛夈€?- ts_ms 瓒呭嚭杩囨湡绐楀彛涓€寰?REJECT锛圗XPIRED_INTENT锛夈€?
### 3.2 涓ら樁娈垫牎楠岋紙蹇呴』锛?闃舵 A锛歭enient parse锛堝彧淇娉曪紝涓嶈ˉ璇箟锛?- 鐩爣锛氭妸鈥滃彲鐤戜絾鍙兘鏄?JSON鈥濆彉鎴愨€滃彲瑙ｆ瀽 JSON 瀵硅薄鈥濓紝鍙仛璇硶灞備慨澶嶃€?- 鍏佽锛氬幓鎺夊墠鍚庣紑鍣０銆佷慨澶嶅浣欓€楀彿/寮曞彿绛夌函璇硶闂锛屼娇鍏舵垚涓哄崟涓?JSON 瀵硅薄銆?- 绂佹锛氳噯閫?action/target/type/args锛涚姝⑩€滅寽娴嬬敤鎴锋剰鍥锯€濇潵琛ュ瓧娈点€?澶辫触鏄犲皠锛欼NTENT_PARSE_FAILED

闃舵 B锛歴trict validate锛堝己绫诲瀷/鏋氫妇/鍙傛暟濂戠害/閲嶆斁/鏃舵晥锛?- 瀛楁/绫诲瀷/鏋氫妇锛氬け璐モ啋INTENT_SCHEMA_INVALID
- args锛氬け璐モ啋INTENT_ARGS_INVALID
- 閲嶆斁锛氬け璐モ啋REPLAY_DETECTED
- seq锛氬け璐モ啋SEQ_INVALID
- 杩囨湡锛氬け璐モ啋EXPIRED_INTENT

### 3.3 args 鍙傛暟濂戠害锛堟瘡涓?action 涓€浠斤級
姣忎釜 action 閮藉繀椤诲湪 schemas.py锛堟垨绛変环妯″潡锛変腑缁戝畾 ArgsModel锛屽苟澹版槑锛?- required_keys / optional_keys
- 绫诲瀷銆佽寖鍥淬€侀暱搴︺€佹鍒?- 璺緞瀹夊叏锛堝 base_dir 绾︽潫锛涚姝?`..`锛?- 鍩熷悕 allowlist / data_classification锛堥厤鍚?AIRLOCK锛?
### 3.4 瀹¤瀛楁锛圛ntent 灞傚繀椤诲啓鍏ワ級
Intent 杩涘叆 Governor 鍓嶏紝鑷冲皯瑕佸湪 intent_log锛堟垨绛変环瀹¤琛級璁板綍锛?- raw_text锛堝師濮嬭崏妗?鍘熷杈撳叆鐗囨鎽樿锛?- normalized_json锛堝鏉捐В鏋愬悗鐨?JSON锛?- session_id / seq / ts_ms / nonce
- parse_stage锛歀ENIENT_OK / LENIENT_FAIL
- lint_result锛氬瓧娈电己澶?鏋氫妇瓒婄晫/args 澶辫触鐨勭粨鏋勫寲鎽樿锛堜笉寰楀啓鎴愯嚜鐒惰瑷€缁撹锛?
---

## 4. Schema锛欸overnorOutput锛圚ost鈫掍笂灞傦級

瀹氫綅锛欸overnorOutput 鏄?Host 瀵光€滄槸鍚﹀厑璁告墽琛屸€濈殑瀵瑰绋冲畾濂戠害銆傚畠涓嶆槸鎻愮ず宸ョ▼浜х墿锛屽繀椤诲彲鍥炴斁銆佸彲缁熻銆佸彲鍥炲綊銆?
瑕嗙洊闇€姹傦細FR-GOV-001~003銆丗R-CORE-002
鍥炲綊鍙ｅ緞锛歍-14~T-17锛堝惈 T-14/T-15锛?
### 4.1 杈撳嚭瀛楁锛堢粨鏋勫浐瀹氾級
GovernorOutput 蹇呴』涓哄崟涓?JSON 瀵硅薄锛屽瓧娈佃涔夊浐瀹氾細
- decision锛欰PPROVE | REJECT
- reason_code锛氫粎褰?decision=REJECT 蹇呴』瀛樺湪锛堢ǔ瀹氭灇涓撅級
- rule_trace锛氬懡涓殑 Gate 鍒楄〃锛圧1鈥揜7锛? 闃堝€兼憳瑕?+ 蹇収鎽樿锛堝彲瀹氫綅锛?- tokens锛?  - APPROVE锛氬繀椤诲惈 approval_token_ref锛堝彲閫夊惈 confirm_token_ref/undo_token_ref锛?  - REJECT锛氫笉寰椾笅鍙?approval_token_ref
- snapshot_refs锛歨ard_state/bond_snapshot锛堢増鏈彿鎴?hash锛?- quality锛堝缓璁級锛歴tage锛圫0..S8锛? degraded锛坆ool锛? notes锛堢煭鏋氫妇锛?
### 4.2 杩愯鏃舵柇瑷€锛堝繀椤绘嫤鎴級
- decision=REJECT 涓旂己 reason_code 鈫?瑙嗕负 GOVERNOR_OUTPUT_INVALID锛堜笉寰楀澶栭€忓嚭鈥滄棤 reason_code 鐨勬嫆缁濃€濓級
- decision=REJECT 涓旂己 rule_trace 鈫?鍚屼笂锛堝繀椤诲彲瀹氫綅鍛戒腑闂ㄧ锛?- decision=APPROVE 涓旂己 approval_token_ref 鈫?Skills/鎵ц鍏ュ彛蹇呴』鎷掔粷骞惰褰?SKILL_TOKEN_MISSING

### 4.3 rule_trace 鏈€灏忕粨鏋勶紙寤鸿鍥哄畾瀛楁锛?姣忔潯 Gate 璁板綍鑷冲皯鍖呭惈锛?- gate_id锛歊1..R7
- hit锛歵rue/false
- threshold_summary锛氶槇鍊兼憳瑕侊紙涓嶆硠闇叉晱鎰熷師鍊硷級
- snapshot_summary锛氳緭鍏ュ揩鐓ф憳瑕侊紙妗跺寲锛?- reason_hint锛氱煭鏋氫妇锛堢敤浜庣粺璁★紝涓嶇敤鑷劧璇█锛?
---

## 5. Token锛歛pproval/confirm/undo锛堢エ鎹寲鎵ц锛?
瀹氫綅锛氭妸鈥滄巿鏉冣€濅粠璇█鍙樻垚鍙獙璇佹暟鎹粨鏋勩€係kills/ToolBroker 鍙俊 token锛屼笉淇″彊浜嬨€?
瑕嗙洊闇€姹傦細FR-TOKEN-001~003
鍥炲綊鍙ｅ緞锛歍-34~T-38

### 5.1 approval_token锛堢煭 TTL锛屽彲鎵ц璁稿彲锛?寤鸿瀛楁锛堣涔夊浐瀹氾級锛?- token_id
- intent_id
- session_id
- seq
- skill_name锛堟垨 tool_id锛?- issued_ts_ms / expiry_ts_ms
- nonce
- sig锛堢鍚嶏級

缁戝畾绾︽潫锛堝繀椤伙級锛?- token 蹇呴』缁戝畾 intent_id + skill_name + session_id + seq
- 杩囨湡蹇呴』鎷掔粷锛圱OKEN_INVALID锛?- 涓茬敤锛堟崲 skill/鎹?intent/鎹細璇濓級蹇呴』鎷掔粷锛圱OKEN_INVALID锛?
### 5.2 confirm_token锛堥珮椋庨櫓浜屾鎻愪氦锛?瑙﹀彂鏉′欢锛堣嚦灏戣鐩栵級锛?- risk_level=4 鎴?irreversible=true 鎴?scope 娑夊強 account/payment/system-settings/delete 绛夌孩绾?瑙勫垯锛?- confirm_token 鍙兘鐢?Host 纭娴佺▼鐢熸垚锛涗换浣曗€滃彛澶寸‘璁?妯″瀷鑷堪纭鈥濇棤鏁堛€?- 缂?confirm_token 鏃跺繀椤?REJECT锛欳ONFIRM_REQUIRED锛堜笉寰楄繘鍏ユ墽琛岃矾寰勶級

### 5.3 undo_token + manifest锛堝彲鎾ら攢濂戠害锛?閫傜敤锛?- reversible=true 鐨勬墽琛岄粯璁ゅ繀椤讳骇鍑?undo_token_ref 涓?manifest_ref
manifest 鏈€灏忓瓧娈碉紙璇箟鍥哄畾锛夛細
- manifest_id / intent_id / integrity_digest锛坰ha256锛?- items[]锛歜efore/after/path/hash/artifact_refs锛堝彲鎸夎兘鍔涜鍓級
- policy_profile_id / ts_ms

鎷掔粷鍙ｅ緞锛?- reversible=true 浣嗙己 manifest_ref 鈫?瑙嗕负涓嶅悎瑙勶細UNDO_REQUIRED锛堟垨鏇寸簿纭殑鍐呴儴閿欒鐮侊紱瀵瑰蹇呴』鍙粺璁★級
- manifest digest 涓嶅尮閰?鈫?鎾ら攢蹇呴』鎷掔粷锛圱OKEN_INVALID 鎴?MANIFEST_INVALID 鐨勭ǔ瀹氬瓙绫伙紝浜岄€変竴浣嗗繀椤诲浐瀹氾級

---

## 6. Skills锛氭敞鍐屻€佹墽琛屻€佸箓绛変笌瀹¤

瀹氫綅锛歋kills 鏄兘鍔涜浇浣擄紝鍙仛鎵ц涓嶅仛瑁佸喅锛涘敮涓€鍚堟硶鍏ュ彛蹇呴』鏄?loader锛堟垨绛変环鎵ц闂級锛屽己鍒?token 鏍￠獙銆佸箓绛変笌瀹¤銆?
瑕嗙洊闇€姹傦細FR-SKILL-001~003銆丗R-AUDIT-001
鍥炲綊鍙ｅ緞锛歍-15銆乀-34~T-38锛堜互鍙婂悗缁?T-Skill-xx锛?
### 6.1 SkillMeta锛堝己鍒跺瓧娈碉紝娉ㄥ唽鍗虫牎楠岋級
姣忎釜 skill 蹇呴』澹版槑骞跺彲闈欐€佹壂鎻忥細
- name
- clearance_level锛圠0鈥揕4锛?- risk_level锛?鈥?锛?- reversible锛坱rue/false锛?- scopes锛坧rocess/filesystem/network/ui/camera/mic/account/payment鈥︼級
- requires_visual_audit锛坱rue/false锛?- requires_confirm_token锛坱rue/false锛?- data_classification锛圢ONE/INTERNAL/EXTERNAL_KNOWLEDGE锛?寤鸿瀛楁锛?- rate_limit銆乤llowlist銆乨enylist銆乪nergy_cost銆乵in_trust

娉ㄥ唽鏈熸柇瑷€锛堝繀椤伙級锛?- 缂哄己鍒跺瓧娈碉細娉ㄥ唽澶辫触锛堜笉寰楄繍琛屾椂鈥滃甫鐥呬笂绾库€濓級
- risk_level 涓?requires_confirm_token 鍙ｅ緞涓嶄竴鑷达細瑙嗕负閰嶇疆涓嶅悎瑙勶紙蹇呴』鍙璁★級

### 6.2 鍞竴鎵ц鍏ュ彛锛堝己鍒讹級
鎵ц鍏ュ彛锛堣涔夊浐瀹氾級锛?- 杈撳叆锛欼ntent + approval_token锛堝彲閫?confirm_token/undo_token锛屾寜 SkillMeta 瑕佹眰锛?- 杈撳嚭锛歋killResult锛堢粨鏋勫寲瀵硅薄锛屽繀椤诲彲鍐欏叆 skill_exec_log锛?
鍏ュ彛蹇呴』鍋氱殑浜嬶紙涓嶅彲鐪佺暐锛夛細
1锛塼oken 鏍￠獙锛氱鍚?杩囨湡/缁戝畾涓€鑷?2锛夐棬绂佸鏍革細鑷冲皯澶嶆牳 scope/confirm_token 瑕佹眰锛堜笌 Governor 鍐崇瓥涓€鑷达級
3锛夊箓绛夛細鍚屼竴 intent_id 涓嶅緱浜х敓绗簩娆＄湡瀹炲壇浣滅敤锛堢瓥鐣ュ彲鈥滃鐢ㄧ粨鏋溾€濇垨鈥滄嫆缁濋噸澶嶁€濅絾蹇呴』鍥哄畾锛?4锛夊璁″啓鍏ワ細skill_exec_log 蹇呴』鍖呭惈 intent_id銆乻kill_name銆乤pproval_token_ref銆乺esult_digest銆乤rtifacts/manifest_ref/undo_ref锛堝閫傜敤锛?
### 6.3 骞傜瓑绛栫暐锛堝繀椤诲彲璇佹槑锛?骞傜瓑璇佹槑鍙ｅ緞锛堝繀椤昏兘琚洖鏀鹃獙璇侊級锛?- 閲嶅 intent_id锛氫笉浜х敓绗簩娆℃墽琛屼簨瀹?- 鏃ュ織鑳芥樉绀衡€滃懡涓箓绛夆€濈殑鍘熷洜锛堜緥濡?reused_result / already_executed锛?
### 6.4 瀹¤涓夎仈鐢讳覆鑱旈敭锛堝繀椤讳竴鑷达級
- intent_log锛氭彁璁紙proposal锛?- governor_log锛氳鍐筹紙decision锛?- skill_exec_log锛氭墽琛岋紙fact锛?
涓茶仈閿嚦灏戝寘鍚細
- request_id锛堟垨 turn_id锛?- intent_id
- approval_token_ref锛堣嫢鎵ц鍙戠敓锛?
---

## 7. Context Pack锛欻ost鈫扡LM 鐨勮緭鍏ュ绾?Host 鎵撳寘 prompt_context锛圝SON锛夌粰 LLM銆傛牳蹇冪洰鏍囷細鎶娾€滀簨瀹炶瘉鎹€佹潈濞佸唴瀹广€侀鏍艰祫浜с€佸彫鍥炵嚎绱⑩€濆垎妲介殧绂伙紝闃叉娣风敤瀵艰嚧瓒婃潈鍙欎簨銆?
瀛楁寤鸿锛堝垎妲藉己闅旂锛夛細
- hard_snapshot锛歟nergy_bucket/mood_bucket/sys_mode/safety_bucket锛堜笉鍚晱鎰熷師鍊硷級
- bond_summary锛歴tage/trend/capability_hint锛堜笉鍚叿浣?trust 鏁板€硷級
- authoritative锛?  - world_canon锛歔{doc_id,title,version,tags,excerpt,evidence_level}]
    - evidence_level 鍥哄畾鏋氫妇锛欳ANON_REFERENCE_ONLY锛堟槑纭潪鎵ц浜嬪疄锛?- retrieval锛?  - episodic锛歔{summary_cn,tags,ts,evidence_refs}]
- style_pack锛?  - style_canon锛歔{canon_id,version,kind,triggers,template,constraints}]锛坱one_only=true, unsafe_as_fact=true锛?- recent_events锛歔{kind,reason_code,ts,summary,evidence_refs}]
- constraints锛?  - strict_json_output=true
  - no_false_execution_claim=true
  - no_canon_as_fact=true
  - tone_policy锛堢煭鍙?鈥︹€?绉板懠锛?  - intent_output_schema锛堝己鍒惰緭鍑?JSON 缁撴瀯锛?
纭害鏉燂細
- world_canon銆乻tyle_canon銆乪pisodic銆乺ecent_events 蹇呴』鍒嗗瓧娈甸殧绂伙紱浠讳綍娣锋Ы瑙嗕负鍚堣缂洪櫡銆?- LLM 涓嶅緱鑷妫€绱紝涓嶅緱鎵╁睍鍙洖锛涘彧娑堣垂 Host 缁欏畾 pack銆?- 鍙欎簨寮曠敤 world_canon 蹇呴』浣跨敤鈥滃紩鐢?璁惧畾鍙ｅ緞鈥濊€岄潪鈥滄墽琛屼簨瀹炲彛寰勨€濄€?
---

## 8. Data Stores锛歋QLite Ledger / Neo4j Bond SSOT / Episodic 鍚戦噺搴?/ Style Canon / World Canon / Artifact Vault锛堝叿浣撳疄鐜板绾︼級

瀹氫綅锛氭湰绔犳妸鈥滃叚搴撳垎宸モ€濆啓鎴愬彲楠岃瘉鐨勫伐绋嬪绾︼細璋佸啓璋佽銆丼SOT 杈圭晫銆丼chema 鍙ｅ緞銆佽法搴撳紩鐢ㄤ笌鍚堣鏂█銆備换浣曞亸绂诲繀椤昏兘琚潤鎬佹壂鎻忔垨鍥炲綊娴嬭瘯鍙戠幇銆?
瑕嗙洊闇€姹傦細FR-AUDIT-001~003銆丗R-BOND-001~003銆丗R-MEM-001~002銆丗R-CANON-001~002銆丗R-WCANON-001~003銆丗R-LSP-001~002銆丗R-AIRLOCK-001~002
鍥炲綊鍙ｅ緞锛歍-14/T-15銆乀-72~T-76銆乀-Canon-01~02銆乀-WCanon-01~03銆乀-Vault-01~02銆乀-39/T-40

### 8.0 璐ｄ换鍒嗛厤锛堟潯鐩紡锛屽繀椤诲榻愶級

鏈妭鏄€滃叚搴撳垎宸モ€濈殑宸ョ▼濂戠害鍙ｅ緞锛氬畾涔変簨瀹炴晥鍔涖€丼SOT 鍗曞啓鑰呫€佸厑璁歌鍙栬€呫€佷互鍙婅法搴撴渶灏忓紩鐢ㄩ敭銆?浠讳綍鍋忕蹇呴』鑳借闈欐€佹壂鎻忔垨鍥炲綊娴嬭瘯鎹曡幏锛堣 8.8 涓庡搴?T-xx锛夈€?
1锛塖QLite Ledger锛堝璁¤处鏈笌绱㈠紩鏋㈢航锛?- 浜嬪疄鏁堝姏锛氭渶楂橈紙瀹¤浜嬪疄涓庡洖鏀剧储寮曪級
- SSOT 鍗曞啓鑰咃細host_brain/audit/sqlite_ledger.py
- 鍏佽璇诲彇鑰咃細Host 鍏ㄥ煙锛堝彧璇绘煡璇笌鍥炴斁锛?- 鏈€灏忚法搴撳紩鐢ㄩ敭锛歳equest_id銆乮ntent_id銆乪vent_id銆乪vidence_refs

2锛塏eo4j锛圔ond 鍏崇郴鍩?SSOT锛?- 浜嬪疄鏁堝姏锛氬叧绯荤粨鏋勫煙 SSOT锛堥潪鎵ц浜嬪疄锛?- SSOT 鍗曞啓鑰咃細host_brain/bond/bond_dynamics.py
- 鍏佽璇诲彇鑰咃細Host锛堝浘鏌ヨ/summary 娲剧敓锛泂ummarizer 涓嶅緱鍐欏洖锛?- 鏈€灏忚法搴撳紩鐢ㄩ敭锛歟vent_id + evidence_refs

3锛塃pisodic 鍚戦噺搴擄紙缁忓巻鍙洖绾跨储锛?- 浜嬪疄鏁堝姏锛氭棤瑁佸喅鏉冿紙鐩镐技鎬х嚎绱級
- SSOT 鍗曞啓鑰咃細host_brain/memory/episodic.py
- 鍏佽璇诲彇鑰咃細Host锛堝彫鍥烇紱涓嶅緱浠ユ鍋氳鍐充緷鎹級
- 鏈€灏忚法搴撳紩鐢ㄩ敭锛歮emory_id + evidence_refs

4锛塖tyle Canon Store锛堥鏍艰祫浜э細tone_only锛?- 浜嬪疄鏁堝姏锛氫粎鍙ｅ惢锛堜笉寰楁彁渚涗簨瀹烇級
- SSOT 鍗曞啓鑰咃細host_brain/memory/canon_style_store.py
- 鍏佽璇诲彇鑰咃細Host锛堟敞鍏?style_pack锛涗笉寰楁贩妲斤級
- 鏈€灏忚法搴撳紩鐢ㄩ敭锛歝anon_id + version + hash

5锛塛orld Canon Store锛堟渶灏忔潈濞佸唴瀹归泦鍚堬細鍙紩鐢ㄣ€侀潪鎵ц浜嬪疄锛?- 浜嬪疄鏁堝姏锛氭潈濞佸唴瀹瑰紩鐢紙鐢ㄤ簬涓€鑷存€т笌鍐茬獊瑁佸喅锛涢潪鎵ц浜嬪疄锛?- SSOT 鍗曞啓鑰咃細host_brain/memory/canon_world_store.py
- 鍏佽璇诲彇鑰咃細Host锛堟敞鍏?authoritative.world_canon锛涗笉寰楁贩妲斤級
- 鏈€灏忚法搴撳紩鐢ㄩ敭锛歞oc_id + version + hash

6锛堿rtifact Vault锛堣瘉鎹?鍥炴粴鏉愭枡锛?- 浜嬪疄鏁堝姏锛氳瘉鎹潗鏂欎粨锛堟敮鎾戝彲鍥炴斁涓庡彲鎾ら攢锛?- SSOT 鍗曞啓鑰咃細host_brain/lsp/vault.py
- 鍏佽璇诲彇鑰咃細Host锛堝紩鐢?灞曠ず/鍥炴粴锛汫C 鍙楃瓥鐣ョ害鏉燂級
- 鏈€灏忚法搴撳紩鐢ㄩ敭锛歛rtifact_id + hash + manifest_id

### 8.1 SQLite Ledger锛堝璁¤处鏈笌绱㈠紩鏋㈢航锛?鏈€浣庝笁鑱旂敾琛紙蹇呴』瀛樺湪锛岃涔夊浐瀹氾級锛?- intent_log锛坧roposal锛夛細璁板綍鈥滄ā鍨?瑙勫垯寮曟搸鎻愬嚭浜嗕粈涔?intent_draft鈥?- governor_log锛坉ecision锛夛細璁板綍鈥淗ost 濡備綍瑁佸喅锛圓PPROVE/REJECT + reason_code + rule_trace锛夆€?- skill_exec_log锛坒act锛夛細璁板綍鈥滄墽琛屽疄闄呭彂鐢熶簡浠€涔堬紙鎴栨湭鍙戠敓锛夆€?
寤鸿鐨勬渶灏忓瓧娈靛彛寰勶紙蹇呴』璇箟绛変环锛夛細

A锛塱ntent_log
- request_id銆乮ntent_id銆乻ession_id銆乻eq銆乼s_ms
- proposer_node_id锛堝 core_reasoner_primary / tool_planner / host_fallback_policy锛?- model_snapshot锛坧rovider + model_id 鎴栫瓑浠峰揩鐓ф爣璇嗭級
- prompt_profile_id锛堝紩鐢?鍝堝笇锛?- tool_visibility_hash锛堟湰杞毚闇茬殑宸ュ叿鍏冩暟鎹瓙闆嗗搱甯岋紱鏃犲垯涓虹┖锛?- proposed_intent_draft_json锛堝師鏂囷級
- lint_result_json锛坰chema/args 鏍￠獙鎽樿锛涚姝㈣嚜鐒惰瑷€鈥滅粨璁哄彞鈥濇浛浠ｏ級

B锛塯overnor_log
- intent_id銆乨ecision銆乺eason_code锛圧EJECT 蹇呭～锛夈€乺ule_trace_json锛堝繀濉級
- token_refs_json锛圓PPROVE 蹇呭惈 approval_token_ref锛?- snapshot_refs_json锛坔ard/bond snapshot 鐗堟湰鍙锋垨 hash锛?- ts_ms銆乻tage锛圫0..S8锛?
C锛塻kill_exec_log
- intent_id銆乻kill_name銆乤pproval_token_ref
- result_status/result_code銆乺esult_digest
- artifacts_json锛堝彲涓虹┖锛夈€乵anifest_ref锛堝彲涓虹┖锛夈€乽ndo_ref锛堝彲涓虹┖锛?- ts_ms

杩愯鏃舵柇瑷€锛堝繀椤伙級锛?- decision=REJECT 鈫?reason_code 闈炵┖涓旀潵鑷ǔ瀹氭灇涓撅紱缂哄け瑙嗕负 GOVERNOR_OUTPUT_INVALID锛堜笉寰楀澶栭€忓嚭锛夈€?- 浠绘剰 skill_exec_log 琛屽繀椤昏兘鍏宠仈 governor_log锛堝悓 intent_id锛変笖 approval_token_ref 鍙牳楠屻€?- 浠绘剰鍐欏叆绫讳簨浠讹紙Bond/Memory/Canon/Vault/Airlock/Clawd锛夐兘蹇呴』鍏峰 evidence_refs锛堣嚦灏?1 涓?intent_id 鎴?event_id 寮曠敤锛夈€?
### 8.2 Neo4j Bond SSOT锛堝叧绯诲煙鍞竴鐪熺浉锛?瀹氫綅锛歂eo4j 鍙壙杞解€滃叧绯荤粨鏋勪笌鍥犳灉璺緞鈥濓紝涓嶅緱鎵胯浇鎵ц浜嬪疄锛涙墽琛屼簨瀹炰粛浠?Ledger 涓哄噯銆?
鍐欏叆杈圭晫锛?- 鍞竴鍐欏叆鑰咃細bond/bond_dynamics.py
- summarizer.py 鍙兘娲剧敓 summary锛屼笉寰楀啓鍥?SSOT

鏈€灏忓浘妯″瀷锛堣涔夌ǔ瀹氾紝鍙墿灞曪級锛?- 鑺傜偣锛歎ser銆丼ession銆丒vent銆乀opic銆丆apabilityHint
- 鍏崇郴锛欻AS_EVENT / ABOUT / AFFECTS 绛夛紙鍏蜂綋鍛藉悕鍙彉锛屼絾蹇呴』鍙洖鏀炬槧灏勶級
- Event 蹇呴』鎼哄甫 event_id 涓?evidence_refs锛堟垨鍙瓑浠疯拷婧埌 SQLite 鐨?bond_update_log锛?
鍥炲綊鍙ｅ緞锛?- 闈?bond_dynamics 灏濊瘯鍐?Neo4j 蹇呴』琚潤鎬佹壂鎻?杩愯鏃舵柇瑷€鎷︽埅锛圱-SSOT-01锛夈€?- Bond Summary 涓嶅緱鍖呭惈鏁忔劅鏁板€硷紙渚嬪 trust=xx锛夛紝鍙兘 stage/trend/capability_hint锛圱-Bond-01锛夈€?
### 8.3 Episodic 鍚戦噺搴擄紙缁忓巻鍙洖绾跨储锛屼笉鍏峰瑁佸喅鏉冿級
瀹氫綅锛氬彧鐢ㄤ簬鐩镐技鎬у彫鍥烇紱涓嶅緱褰撲簨瀹炴簮銆?
鍐欏叆杈圭晫锛?- 鍞竴鍐欏叆鑰咃細memory/episodic.py
- 蹇呴』 evidence_refs锛氭棤璇佹嵁涓嶅緱鍐欏叆锛堥槻骞昏鍥哄寲锛?
鏉＄洰鍏冩暟鎹渶灏忓瓧娈碉紙蹇呴』璇箟绛変环锛夛細
- memory_id銆乼s_ms銆乼ext_hash銆乨ecay_profile_id
- evidence_refs锛堝繀椤伙級
- merged_into锛堝彲绌猴級銆乨eleted_at锛堝彲绌猴級
- visibility锛坕nternal/user_visible锛?- embedding_model_id

鐢熷懡鍛ㄦ湡锛堝繀椤诲璁★級锛?- write/merge/decay/delete 蹇呴』鍐欏叆 memory_ops_log锛堟垨绛変环琛級锛屽苟甯?evidence_refs
- merge 蹇呴』淇濈暀鈥滄潵婧愭潯鐩啋鐩爣鏉＄洰鈥濈殑鍙拷婧叧绯?
鍥炲綊鍙ｅ緞锛?- T-Mem-01锛氭棤 evidence_refs 鍐欏叆蹇呭け璐ヤ笖鍙В閲?- T-Mem-02锛歮erge/decay/delete 鍧囧彲鍦?memory_ops_log 鍥炴斁

8.3.1 RETRIEVAL锛圗mbedding / Rerank / Evidence Pack锛?瑕嗙洊闇€姹傦細FR-MEM-003~005銆丗R-AUDIT-001銆丗R-CORE-002銆?
濂戠害鐐癸紙蹇呴』璇箟绛変环锛夛細
- Embedding 鎺ュ彛蹇呴』瀛樺湪锛氳緭鍏?text锛岃緭鍑?{embedding_vector, embedding_model_id, latency_ms, status}
- Reranker 鎺ュ彛蹇呴』瀛樺湪锛氳緭鍏?{query, candidates[]}锛岃緭鍑?{scores[], rerank_model_id, latency_ms, status}
- Evidence Pack锛堣瀺鍚堣緭鍑猴級瀛楁鍥哄畾锛歿query, topk[], degraded?, stage, reason_code?}
  - topk[] 鏈€灏忓瓧娈碉細{doc_id, source_ref, snippet, score, confidence, metadata?}

鍥炲綊娴嬭瘯缂栧彿锛堟柊澧烇紝蹇呴』锛夛細
- T-Ret-01锛歟mbedding 涓嶅彲鐢ㄦ椂鐨勯檷绾у彛寰勶紙涓嶅緱浼涓虹┖浜嬪疄锛? reason_code 绋冲畾
- T-Ret-02锛歳eranker 涓嶅彲鐢ㄦ椂鐨勯檷绾у彛寰?+ evidence pack 缁撴瀯浠嶇ǔ瀹?- T-Ret-03锛歟vidence pack 瀛楁绋冲畾 + 鍙洖鏀惧紩鐢紙鍙敤浜庡洖绛斾笌瀹¤锛?
### 8.4 Style Canon Store锛堥鏍艰祫浜э細tone_only锛?瀹氫綅锛氬彧鍐冲畾鈥滄€庝箞璇粹€濓紝涓嶅緱褰撲簨瀹炴敞鍏ャ€?
鍐欏叆杈圭晫锛?- 鍞竴鍐欏叆鑰咃細memory/canon_style_store.py锛堝繀椤荤粡鐧藉悕鍗?Evolution 鎴栫瓑浠锋祦绋嬶級
- 浠绘剰鍙樻洿蹇呴』鍐?canon_change_log锛堟垨绛変环琛級

鏉＄洰瀛楁锛堟渶灏忥級锛?- canon_id銆乿ersion銆乭ash
- tone_only=true銆乽nsafe_as_fact=true
- content锛堟ā鏉?鑼冨紡锛夈€乼ags銆乽pdated_by锛坧roposal_id锛夈€乼s_ms

娉ㄥ叆闅旂锛?- 鍙兘杩涘叆 prompt_context.style_pack锛涗笉寰椾笌 facts/world_canon/episodic 娣锋Ы锛圱-CP-01锛夈€?
### 8.5 World Canon Store锛堟渶灏忔潈濞佸唴瀹归泦鍚堬細鍙紩鐢ㄣ€侀潪鎵ц浜嬪疄锛?瀹氫綅锛氱ǔ瀹氣€滃唴瀹逛竴鑷存€р€濓紝鐢ㄤ簬璁惧畾/鏈/涓嶅彉閲忓紩鐢ㄤ笌鍐茬獊瑁佸喅锛涗笉鏋勬垚鎵ц浜嬪疄銆?
鍐欏叆杈圭晫锛?- 鍞竴鍐欏叆鑰咃細memory/canon_world_store.py锛堝繀椤荤粡鐧藉悕鍗?Evolution 鎴栫瓑浠锋祦绋嬶級
- 浠绘剰鍙樻洿蹇呴』鍐?canon_change_log 涓?canon_proposal_log锛堟垨璇箟绛変环锛?
鎺ㄨ崘鍙屽眰褰㈡€侊紙鏈湴浼樺厛銆佸彲瀹¤锛夛細
- 鍐呭璧勪骇灞傦紙鏂囦欢 SSOT锛夛細host_brain/canon/world/ 涓嬫潯鐩枃浠讹紝鑷冲皯鍖呭惈 doc_id銆乿ersion銆乼itle銆乼ags銆乥ody
- 绱㈠紩灞傦紙SQLite锛夛細world_canon_doc锛坉oc_id/version/hash/status/source_path/updated_ts锛?
鍚屾濂戠害锛?- canon_sync 璐熻矗鎵弿鏂囦欢鈫掕绠?hash鈫掓洿鏂扮储寮曪紱浠讳綍鍙樻洿蹇呴』鍐?canon_change_log锛坈anon_kind=world锛?
寮曠敤鍙ｅ緞锛堝繀椤伙級锛?- 杈撳嚭涓紩鐢?world_canon 蹇呴』浣跨敤鈥滆瀹?寮曠敤鍙ｅ緞鈥濓紝涓嶅緱鍗囩骇涓衡€滃凡鎵ц/宸蹭繚瀛?宸茬‘璁も€?- 浠讳綍瀹屾垚鎬ц〃杩板繀椤诲紩鐢ㄥ璁￠摼鎽樿瀛楁锛屽惁鍒?QA 鎷︽埅锛堜笌 9 绔犺仈鍔級

### 8.6 Artifact Vault锛堣瘉鎹笌鍙€嗘潗鏂欎粨锛?瀹氫綅锛氫繚瀛樻埅鍥?鍏抽敭甯?鑴辨晱鎶ュ憡/鍥炴粴鏉愭枡绛夎祫浜э紝鏀拺鎾ら攢涓?UI 琛屼负鍙洖鏀俱€?
鍐欏叆杈圭晫锛?- 鍞竴鍐欏叆鑰咃細lsp/vault.py锛堢粡 Skill 鎵ц浜у嚭锛?- manifest 蹇呴』鍙储寮曞埌 intent_id锛屽苟鑳藉叧鑱?undo_token_ref锛堣嫢 reversible=true锛?
manifest 鏈€灏忓瓧娈碉紙蹇呴』璇箟绛変环锛夛細
- manifest_id銆乮ntent_id銆乮ntegrity_digest锛坰ha256锛?- items[]锛歿artifact_id, kind, hash, created_ts, evidence_refs}
- policy_profile_id銆乼s_ms
- undo_ref锛堝彲绌猴紝鑻?reversible=true 鍒欏繀濉級

GC 绾︽潫锛?- 涓嶅緱鍒犻櫎鏈夋晥 undo 绐楀彛鏉愭枡
- 姣忔 GC 蹇呴』鍐?vault_gc_log锛堝惈 policy_profile_id銆乨eleted_artifact_ids銆乪vidence_refs锛?
### 8.7 Airlock锛堝嚭绔欏噣鍖?鍏ョ珯闅旂锛変笌鏃ュ織鍙ｅ緞
鍑虹珯蹇呴』浜х敓锛?- sanitization_report_json锛坮emoved_fields/redactions/payload_digest锛?- airlock_log.direction=EGRESS锛屼笖鍚?request_id

鍏ョ珯蹇呴』闅旂涓庡垎绫伙細
- 鍒嗙被鏋氫妇锛欶ACT/DRAFT_CODE/DRAFT_INSTRUCTION/UNKNOWN
- 浠讳綍鍏ョ珯鍐呭涓嶅緱鐩撮€氭墽琛岋紱蹇呴』杞垚 Intent 骞惰繃 Governor锛屽惁鍒?EXTERNAL_DATA_GATE

### 8.8 闈欐€佹壂鎻忔柇瑷€锛堝崟鍐欒€?鏃佽矾绂佹锛屽繀椤诲彲鍥炲綊锛?蹇呴』瑕嗙洊鐨勭姝㈤」锛堝彛寰勭骇锛夛細
- 闈?skills/loader 涓嶅緱鐩存帴璋冪敤 skills/impl
- 闈?bond/bond_dynamics 涓嶅緱鎸佹湁/璋冪敤 Neo4j 鍐欏叆鑳藉姏
- 闈?memory/episodic 涓嶅緱鎸佹湁/璋冪敤鍚戦噺搴撳啓鍏ヨ兘鍔?- 闈?memory/canon_style_store 涓嶅緱鎸佹湁/璋冪敤 Style Canon 鍐欏叆鑳藉姏
- 闈?memory/canon_world_store 涓嶅緱鎸佹湁/璋冪敤 World Canon 鍐欏叆鑳藉姏
- 闈?lsp/vault 涓嶅緱鎸佹湁/璋冪敤 Vault 鍐欏叆鑳藉姏

鍥炲綊鍙ｅ緞锛?- T-SSOT-01锛氶潤鎬佹壂鎻忚兘鍙戠幇骞堕樆鏂笂杩版梺璺矾寰勶紙娴嬭瘯鍙柇瑷€鈥滃懡涓鍒欌€濓紝涓嶄緷璧栫湡瀹炲閮ㄦ湇鍔★級銆?
### 8.9 Tools锛圱ool Catalog/Selector/Broker锛氭笎杩涘紡鍙鎬т笌鍗曞叆鍙ｆ墽琛岋級

瀹氫綅锛氭妸鈥滃伐鍏烽€夋嫨鈥濅笌鈥滃伐鍏锋墽琛屸€濇媶鎴愬彲瀹¤銆佸彲鍥炲綊鐨勫伐绋嬪绾︺€傛ā鍨嬪彧鐪嬪埌鍊欓€夊伐鍏风殑鏈€灏忓厓鏁版嵁瀛愰泦锛坱ool_meta_pack锛夛紝浠讳綍鍓綔鐢ㄥ伐鍏锋墽琛屽繀椤荤粡 ToolBroker 鍗曞叆鍙ｅ苟鍙?Governor/Token 娌荤悊銆?
瑕嗙洊闇€姹傦細FR-TOOL-001~003銆丗R-SKILL-002銆丗R-AUDIT-001銆丯FR-SEC-001銆?
8.9.1 ToolMeta锛堜粎鍏冩暟鎹級鏈€灏忓瓧娈碉紙蹇呴』璇箟绛変环锛?- tool_id锛堢ǔ瀹?ID锛夈€乶ame銆乨esc
- args_schema_hash锛堝弬鏁?Schema 鍝堝笇锛涚姝笂琛屽畬鏁村疄鐜扮粏鑺傦級
- side_effect_level锛歊EAD|WRITE|EXEC
- auth_level锛歂ONE|APPROVAL|CONFIRM
- domain_tags锛堢敤浜?Top-K 閫夋嫨锛?- version锛堝伐鍏峰厓鏁版嵁鐗堟湰锛?
8.9.2 tool_meta_pack锛圱op-K 鍙宸ュ叿瀛愰泦锛?- 璇箟锛氭瘡杞粎鏆撮湶 Top-K 涓?ToolMeta锛涢粯璁や笉鍖呭惈 WRITE锛涘嵆浣垮寘鍚篃涓嶇瓑浠蜂簬鍙墽琛屻€?- 蹇呴』鍙拷婧細tool_visibility_hash 蹇呴』涓?intent_log锛坧roposal锛?llm_call_log 涓茶仈銆?
8.9.3 tool_visibility_hash锛堝彲杩借矗鍙ｅ緞锛?- 璁＄畻鏉愭枡锛氭寜绋冲畾鎺掑簭鐨勶紙tool_id + args_schema_hash + side_effect_level + version锛夊簭鍒楄绠楁憳瑕併€?- 璁板綍浣嶇疆锛歩ntent_log.tool_visibility_hash + llm_call_log.tool_visibility_hash锛堜簩鑰呰嚦灏戝叾涓€锛屼笖鍙叧鑱斿埌鍚屼竴 request_id/intent_id锛夈€?
8.9.4 ToolBroker锛堝伐鍏锋墽琛屽崟鍏ュ彛锛?- 杈撳叆濂戠害锛堟蹇电骇锛夛細{intent_id, tool_id, args, approval_token_ref?, confirm_token_ref?}
- 杈撳嚭濂戠害锛堟蹇电骇锛夛細{status, evidence_ref, tool_result_digest, ts_ms}
- 绾︽潫锛?  - WRITE/EXEC锛氬繀椤?Governor=APPROVE 涓?token 鍚堟硶锛涘惁鍒欐嫆缁濆苟鍐欏叆鍙粺璁?reason_code銆?  - 浠讳竴鎴愬姛鎵ц蹇呴』浜у嚭 evidence_ref锛屼笖鍙湪瀹¤閾句腑鍥炴斁鍒?skill_exec_log锛堟垨绛変环浜嬪疄鏃ュ織锛夈€?
8.9.5 鍥炲綊娴嬭瘯缂栧彿锛堝繀椤伙級
- T-Tool-01锛歍oolMeta 瀛楁瀹屾暣鎬т笌鈥滀粎鍏冩暟鎹€濈害鏉燂紙涓嶅緱娣峰叆鎵ц瀹炵幇缁嗚妭锛?- T-Tool-02锛歍op-K 瀛愰泦涓?WRITE 榛樿涓嶅彲瑙侊紙绛栫暐绋冲畾銆佸彲缁熻锛?- T-Tool-03锛歍oolBroker 鍗曞叆鍙ｄ笌 WRITE Token gating锛堟棤 token 蹇呮嫆缁濓紝鎴愬姛蹇呮湁 evidence_ref 涓茶仈锛?
### 8.10 MCP锛坧rovider鈫扵oolMeta 鏄犲皠 + 鍙楁不鐞嗚皟鐢級

瀹氫綅锛氭妸 MCP provider 绾冲叆宸ュ叿鍏冩暟鎹綋绯讳笌瀹¤閾撅紱瀵?MCP 鐨?READ/WRITE 璋冪敤鏂藉姞鍚岀瓑娌荤悊锛屼笉鍏佽鈥滃閮ㄥ伐鍏风洿閫氭墽琛屸€濄€?
瑕嗙洊闇€姹傦細FR-MCP-001銆丗R-TOOL-001~003銆丗R-AIRLOCK-001銆丗R-AUDIT-001銆?
濂戠害鐐癸紙蹇呴』璇箟绛変环锛夛細
- provider_id銆乸rovider_version 鈫?ToolMeta.version
- provider_tool_id 鈫?ToolMeta.tool_id锛堥渶鍛藉悕绌洪棿闅旂锛岄伩鍏嶄笌鏈湴宸ュ叿鍐茬獊锛?- side_effect_level锛歊EAD/WRITE 鍒嗙骇锛沇RITE 蹇呴』璧?Token gating
- 瀹¤瀛楁锛歱rovider_id/tool_id/request_id/evidence_ref锛堝彲鍥炴斁閾捐矾锛?
鍥炲綊娴嬭瘯缂栧彿锛堝繀椤伙級锛?- T-MCP-01锛歱rovider鈫扵oolMeta 鍚屾绋冲畾鎬?+ WRITE 鏃?token 蹇呮嫆缁?+ 瀹¤涓茶仈瀹屾暣

### 8.11 Channels锛堝井淇★級涓?Avatar锛圴TS 澶栨樉锛夋不鐞嗗绾?
瀹氫綅锛氭妸鈥滈€氶亾 I/O鈥濅笌鈥滃鏄鹃┍鍔ㄢ€濈撼鍏ョ‘瀹氭€ф槧灏勪笌瀹¤閾撅紝鎷掔粷妯″瀷鑷敱鎸囧畾闅愬紡鍙傛暟锛堟敹浠朵汉/鑷敱鍔ㄤ綔/浠绘剰 expression_id锛夈€?
瑕嗙洊闇€姹傦細FR-CHANNEL-001銆丗R-AVATAR-001銆丗R-AUDIT-001銆丯FR-SEC-001銆?
8.11.1 寰俊閫氶亾锛圛/O 浜嬩欢鍖栵級
- 鍏ョ珯锛歩nbound event 蹇呴』鐢熸垚绋冲畾 thread_key/message_id/request_id锛屽苟鍐欏叆 channel_io_log锛堟垨绛変环鏃ュ織锛夈€?- 鍑虹珯锛歰utbound event 蹇呴』缁戝畾 intent_id锛涘彂閫佸け璐ュ繀椤讳互 reason_code + stage 鍙В閲娿€?- 绂佹锛歀LM 涓嶅緱鐩存帴鍐冲畾鈥滃彂缁欒皝/鍙戜粈涔堚€濓紝鍙兘鎻愬嚭 intent_draft锛岀敱 Governor 瑁佸喅涓庣エ鎹寲鎵ц銆?
鍥炲綊娴嬭瘯缂栧彿锛?- T-Channel-01锛氬叆绔欏幓閲嶏紙閲嶅娑堟伅涓嶉噸澶嶈Е鍙戞墽琛岋級銆佸嚭绔欏彲杩芥函锛坱hread_key/request_id/intent_id 涓茶仈锛?
8.11.2 VTS 澶栨樉锛堢‘瀹氭€ф槧灏勶級
- 鍥哄畾鏄犲皠锛歟motion_tag鈫抏xpression_id锛堝悓鐗堟湰鍥哄畾锛夛紝涓斿繀椤诲彲瀹¤鍥炴斁銆?- talk gate锛歴tart/stop_talking 鍙厑璁哥敱绯荤粺浜嬩欢瑙﹀彂锛堜緥濡傛挱鏀鹃槦鍒楃姸鎬侊級锛屼笉寰楃敱妯″瀷浠绘剰瑙﹀彂銆?- 鎷掔粷锛氭湭鐭?emotion_tag 鎴栬嚜鐢卞姩浣滃弬鏁板繀椤绘嫆缁濆苟鐣欑棔銆?
鍥炲綊娴嬭瘯缂栧彿锛?- T-Avatar-01锛氬浐瀹氭槧灏勪竴鑷存€?+ 闈炴硶 tag 鎷掔粷 + 鍙洖鏀惧璁″瓧娈?
8.11.3 VOICE锛堣闊抽摼璺細ASR/TTS/鎵撴柇/鍏滃簳妯″瀷锛?瑕嗙洊闇€姹傦細FR-VOICE-001~005銆丗R-AUDIT-001銆丯FR-SEC-001銆?
濂戠害鐐癸紙蹇呴』璇箟绛変环锛夛細
- ASR 涓婇€佷簨浠讹紙asr_chunk锛夛細{request_id, ts_ms, text, t_start_ms, t_end_ms, confidence, alternates?}
- 鎾姤璇锋眰锛坰peak锛夛細{request_id, text, emotion_tag, voice_profile_id?, priority?}
- 鎵撴柇浜嬩欢锛坆arge_in锛夛細{request_id, reason, ts_ms}
- 鍏滃簳妯″瀷绔偣杈撳嚭锛氬繀椤诲崟涓悎娉?JSON锛屼笖 draft_only=true锛涗笉寰楀嚭鐜?decision/allow/deny/confirm_required 绛夎鍐冲瓧娈碉紱涓嶅緱鍑虹幇浠讳綍鎵ц浜嬪疄瀹ｅ憡銆?
鍥炲綊娴嬭瘯缂栧彿锛堟柊澧烇紝蹇呴』锛夛細
- T-Voice-01锛歛sr_chunk 瀛楁缁撴瀯绋冲畾 + 浣庣疆淇″害鏄惧紡鏍囨敞 + 鍙洖鏀?- T-Voice-02锛歴peak鈫掑悎鎴愨啋鎾姤浜嬩欢涓茶仈 request_id + 澶辫触 reason_code/stage
- T-Voice-03锛欱arge-in 鐢熸晥锛堟挱鎶ュ仠姝?闄嶇骇锛? ASR 涓嶄腑鏂?+ 瀹¤鏈夎褰?- T-Voice-04锛欵dge 鍏滃簳 LLM draft-only 杈撳嚭绾︽潫 + 涓嶅彲鐢ㄥ揩閫熷け璐ュ洖閫€
- T-Voice-05锛氳祫婧愰椄闂ㄤ紭鍏堢骇锛圓SR 浼樺厛锛? 宄板€间簰鏂ヨ涓哄彲瑙傛祴

## 8.12 VISION锛堝睆骞曠洃鎺?OCR/鎽勫儚澶达細Gaming 闂ㄦ帶 + 浜嬩欢鍖?+ 璇佹嵁寮曠敤锛?
瑕嗙洊闇€姹傦細FR-VISION-003~004銆丗R-MODE-001~002銆丗R-AUDIT-001~002銆?鍥炲綊鍙ｅ緞锛氾紙鏂板锛塗-VisionGate-01銆乀-Vision-01銆乀-Vision-02銆?
### 8.12.1 灞忓箷鐩戞帶涓?OCR锛堟寜闇€瑙﹀彂锛?瀹氫綅锛氬睆骞曠洃鎺т笌 OCR/璇嗗浘灞炰簬鈥滄寜闇€鎺ㄧ悊鑳藉姏鈥濓紝蹇呴』鍙?sys_mode/Gaming 闂ㄦ帶锛屼笖缁撴灉蹇呴』缁撴瀯鍖栥€佸彲鍥炴斁銆佸彲寮曠敤璇佹嵁锛涗笉寰椾互鑷劧璇█鏇夸唬璇佹嵁寮曠敤銆?
濂戠害鐐癸紙蹇呴』璇箟绛変环锛夛細
- 瑙﹀彂闂ㄦ帶锛?  - sys_mode=GAMING锛氱姝㈣Е鍙戜换浣曞睆骞曠洃鎺?OCR锛堝惈瀹氭椂杞涓庤鍔ㄨЕ鍙戯級锛涘繀椤绘嫆缁濇垨绋冲畾闄嶇骇锛屽苟杩斿洖鍙粺璁?reason_code銆?  - 闈?GAMING锛氬厑璁告寜闇€瑙﹀彂锛屼絾蹇呴』浜у嚭缁撴瀯鍖栬緭鍑猴紝骞跺彲杩芥函 source_ref銆?- OCR 杈撳嚭缁撴瀯锛堟渶灏忓瓧娈甸泦锛夛細
  - {request_id, ts_ms, source_ref, engine_id, text_blocks[]}
  - text_blocks[]锛歿text, bbox, conf}
  - 绾︽潫锛歜box 鍧愭爣绯诲繀椤诲浐瀹氾紱conf 蹇呴』鏄惧紡缁欏嚭锛泂ource_ref 蹇呴』鍙洖鏀惧埌璇佹嵁鏉愭枡锛圴ault artifact/manifest/evidence_ref 涔嬩竴锛屽彇鍏朵竴浣嗗彛寰勫繀椤诲浐瀹氾級銆?- 澶辫触/闄嶇骇鍙ｅ緞锛?  - Engine 涓嶅彲鐢?瓒呮椂锛氫笉寰椾吉瑁呬负鈥滄病鏈夋枃瀛?鐪嬩笉鍒板唴瀹光€濓紱蹇呴』浠?reason_code + quality.stage 鏄庣‘鏍囨敞闄嶇骇闃舵銆?
鍥炲綊娴嬭瘯缂栧彿锛堟柊澧烇紝蹇呴』锛夛細
- T-VisionGate-01锛欸aming 妯″紡涓嬩换鎰忓睆骞曠洃鎺?OCR 璇锋眰鍧囪鎷掔粷鎴栭檷绾э紙reason_code 绋冲畾鍙粺璁★級锛涗笉寰楀嚭鐜扳€滄垜鐪嬪埌浜嗗睆骞曞唴瀹光€濈殑鏃犺瘉鎹鍛娿€?- T-Vision-01锛氶潪 Gaming 涓?OCR 杈撳嚭缁撴瀯绋冲畾锛坱ext_blocks/bbox/conf/source_ref锛夛紱source_ref 鍙洖鏀惧紩鐢ㄣ€?
### 8.12.2 鎽勫儚澶磋瘑鍒紙浜嬩欢鍖栬緭鍑?+ evidence_ref锛?瀹氫綅锛氭憚鍍忓ご璇嗗埆蹇呴』杈撳嚭浜嬩欢鍖栫粨鏋勶紝骞朵笌瀹¤閾句覆鑱旓紱浣庣疆淇″害蹇呴』鏄惧紡鏍囨敞锛涜瘉鎹己澶辨椂蹇呴』绋冲畾鎷掔粷/闄嶇骇銆?
濂戠害鐐癸紙蹇呴』璇箟绛変环锛夛細
- camera_event 杈撳嚭缁撴瀯锛堟渶灏忓瓧娈甸泦锛夛細
  - {request_id, ts_ms, evidence_ref, objects[]}
  - objects[]锛歿label, conf, bbox?}
  - 绾︽潫锛歟vidence_ref 蹇呴』鑳藉洖鏀惧埌璇佹嵁鏉愭枡锛圴ault artifact/manifest/evidence_ref 涔嬩竴锛屽彇鍏朵竴浣嗗彛寰勫繀椤诲浐瀹氾級銆?- 浣庣疆淇″害绛栫暐锛?  - conf 浣庝簬闃堝€兼椂蹇呴』鏄惧紡鏍囨敞锛堜緥濡?low_confidence=true 鎴栦互 reason_code/notes 鏋氫妇琛ㄧず锛夛紝涓嶅緱鈥滃己鏂█鈥濄€?
鍥炲綊娴嬭瘯缂栧彿锛堟柊澧烇紝蹇呴』锛夛細
- T-Vision-02锛氭憚鍍忓ご璇嗗埆浜嬩欢鍙洖鏀惧苟鎸囧悜 evidence_ref锛涜瘉鎹己澶辨椂绋冲畾闄嶇骇/鎷掔粷涓?reason_code 鍙粺璁°€?
### 8.12.3 reason_code 涓庡璁′覆鑱旓紙VISION 涓撶敤锛?- VISION 鐩稿叧鎷掔粷/闄嶇骇蹇呴』鍏峰绋冲畾 reason_code锛堟灇涓惧寲锛夛紝骞跺湪 quality.stage 鏍囨敞澶辫触闃舵銆?- 鎵€鏈?VISION 缁撴灉蹇呴』鍙覆鑱斿埌 request_id/intent_id锛堣嚦灏戝叾涓€锛夛紝骞跺彲鐢卞璁″洖鏀惧畾浣嶅埌璇佹嵁寮曠敤锛坰ource_ref/evidence_ref锛夈€?
---

## 9. QA锛氳緭鍑哄悎瑙勬ā鍧楋紙Host 蹇呴』锛?`core/qa.py` 璐熻矗瀵?LLM 杈撳嚭鍋氣€滃悎瑙勬鏌ヤ笌閲嶅啓涓€娆♀€濓細
- 浼墽琛屽鍛婏紙FALSE_EXECUTION_CLAIM锛?- Canon 褰撲簨瀹烇紙CANON_AS_FACT_VIOLATION锛?- 闈炴硶璇█/鍙ｅ惢锛圠ANGUAGE_POLICY_VIOLATION / TONE_POLICY_VIOLATION锛?- JSON 缁撴瀯婕傜Щ锛圠LM_FORMAT_DRIFT锛?
绛栫暐锛?- 鍏佽閲嶅啓 1 娆★紱浠嶅け璐?鈫?Host 鏈€灏忔ā鏉胯緭鍑猴紙骞惰褰?reason_code锛?
---

## 10. 鏈€灏忓彲浜や粯閲岀▼纰戯紙MVP鈫扡SP锛?
### M0锛堝彲璺戦€氾級
- Host `/v1/chat`锛氳緭鍏モ啋璋冪敤 LLM stub/fixtures鈫扱A鈫掕緭鍑?- Intent 瀹芥澗瑙ｆ瀽 + 涓ユ牸鏍￠獙
- Governor REJECT/APPROVE + reason_code + token_ref
- Skills 妗嗘灦锛氳嚦灏戝疄鐜?`narrative_only`锛堜笉鎵ц锛変笌 `memory_recall`锛堝彧璇伙級
- SQLite audit 鏈€灏忎笁琛細intent/governor/skill_exec

### M1锛堝畨鍏ㄩ棴鐜級
- approval_token 楠岃瘉
- 閲嶆斁淇濇姢锛歩d/seq/ts/nonce
- reason_code 鏋氫妇涓庢祴璇曪紙T-16/T-17锛?- 闈欐€佹壂鎻忕姝㈡梺璺?
### M2锛圗dge 鎺ュ叆锛?- Host鈫擡dge RPC锛歴creenshot/play_audio
- 澶辫触闄嶇骇锛圖EPENDENCY_UNAVAILABLE 绛夛級

### M3锛圠SP锛?- undo_token+manifest
- Artifact Vault + vault_log + GC
- DesktopIntervention锛堜粎 Natsume_Space锛屽彲鎾ら攢锛?
---

## 11. 鍚堣娴嬭瘯锛堜粠鐧界毊涔︽槧灏勶級

Dev Spec 蹇呴』鎻愪緵鍙墽琛岀殑娴嬭瘯娓呭崟鏂囦欢锛堣 `tests/e2e/`锛夛紝寤鸿鎸夌紪鍙风洿鎺ヨ惤鍦帮細
- T-14/T-15 Governor 杈撳嚭濂戠害
- T-18~T-22 Intent 鏍￠獙涓庨噸鏀?- T-34~T-38 Token 涓?confirm/undo
- T-41~T-43 瑙嗚鐔旀柇
- T-67~T-71 闄嶇骇涓?QA
- T-72~T-76 鐢熷懡鍛ㄦ湡涓?SSOT
- T-Canon-01~T-Canon-02 Style Canon 鐗堟湰鍖栦笌 Canon-as-fact 鎷︽埅
- T-WCanon-01~T-WCanon-03 World Canon 绱㈠紩鍚屾/寮曠敤娉ㄥ叆/闈炴墽琛屼簨瀹炲彛寰勫洖褰?- T-Flow-01~T-Flow-02 閰嶇疆璧勪骇锛團low锛夛細鎸囬拡鍚堟硶鎬?+ Trace 绱㈠紩涓€鑷存€?
---

## 12. Codex 鎵ц璇存槑

浣犲湪姣忔璁?Codex 鏀逛唬鐮佸墠锛屽繀椤绘彁渚涳細
- 鐩爣閲岀▼纰戯紙M0/M1/M2/M3锛?- 瑕佹敼鐨勬枃浠跺垪琛紙鍏ㄦ枃浠舵浛鎹紭鍏堬級
- 闇€瑕侀€氳繃鐨勬祴璇曠紪鍙凤紙渚嬪 T-14銆乀-18銆乀-34锛?- 涓嶅彉閲忥紙渚嬪 I1/I8/I9锛変笉鍙牬鍧?- 杈撳嚭鏍煎紡锛氬繀椤荤粰鍑哄彉鏇存憳瑕?+ 鍙樻洿鏂囦欢鍒楄〃 + 涓嬩竴姝ュ缓璁?
锛堟洿瀹屾暣鐨?Codex 琛屼负瑙勮寖鍦?`docs/40_agents/AGENTS.md`銆傦級

---

## 13. 鐜涓庨厤缃紙dev/test/prod锛?
瀹氫綅锛氭湰绔犲畾涔夆€滅幆澧冩灇涓俱€侀厤缃潵婧愪紭鍏堢骇銆佹晱鎰熶俊鎭竟鐣屻€佸閮ㄤ緷璧栭殧绂诲彛寰勨€濄€備笉鍖呭惈浠讳綍鍛戒护琛屾垨鎵ц姝ラ銆?
瑕嗙洊闇€姹傦細NFR-SEC-001銆丯FR-REL-001銆丯FR-MAINT-001锛堜互鍙?ModelPlane/AIRLOCK 鐩稿叧钀界偣锛?鍥炲綊鍙ｅ緞锛歍-Perf-01銆乀-Gaming-01銆乀-39/T-40銆乀-Flow-01/02锛堥厤缃竴鑷存€э級

### 13.1 鐜鏋氫妇锛堣涔夊浐瀹氾級
- Natsume_ENV=dev锛氭湰鍦板紑鍙戯紙鍏佽 stub/fixtures锛涙洿璇︾粏鏃ュ織锛涘厑璁稿叧闂儴鍒嗗閮ㄤ緷璧栦絾蹇呴』鍙檷绾э級
- Natsume_ENV=test锛氳嚜鍔ㄥ寲娴嬭瘯锛堝己鍒堕殧绂诲閮ㄤ緷璧栵紱浣跨敤涓存椂搴?涓存椂鐩綍锛涗笉寰楄闂湡瀹炲瘑閽ワ級
- Natsume_ENV=prod锛氱湡瀹炶繍琛岋紙鏈€灏忔毚闇诧紱涓ユ牸闂ㄧ榛樿锛涘嵄闄╄兘鍔涢粯璁ゅ叧闂垨闇€鏄惧紡鎺堟潈锛?
### 13.2 閰嶇疆鏉ユ簮浼樺厛绾э紙浠庨珮鍒颁綆锛?1) 鐜鍙橀噺锛堣繍琛屾椂娉ㄥ叆锛?2) 鏈湴瑕嗙洊閰嶇疆锛堜粎 dev/test锛屼笖涓嶅緱鍏ュ簱锛?3) 浠撳簱鍐呴粯璁ら厤缃紙瀹夊叏榛樿鍊硷紱蹇呴』鑳借窇鏈€灏忛棴鐜級

纭害鏉燂細
- 浠讳綍 secret锛圓PI key銆乼oken銆佽处鍙峰嚟鎹級涓嶅緱鍑虹幇鍦ㄤ粨搴撴枃鏈枃浠朵腑锛涘彧鑳戒互閿悕瀛樺湪锛堜緥濡?OPENAI_API_KEY锛夈€?- 浠讳綍鍑虹綉 endpoint 鍙兘鍦?secret/鐜灞傛彁渚涳紱浠撳簱鍐呬粎鍏佽 provider 鍚嶇О涓庢ā鍨嬫爣璇嗭紙瑙?ModelPlane 閰嶇疆璧勪骇锛夈€?
### 13.3 閰嶇疆璧勪骇鍏ュ彛锛堜笌鏂囨。/Trace 鐨勫叧绯伙級
- Flow 閰嶇疆璧勪骇锛歠lows/exports + flows/active.pointer锛涘叾涓€鑷存€х敱 docs/60_trace/flows/index.md 鎸囧悜骞跺彲鍥炲綊锛圱-Flow-01/02锛夈€?- ModelPlane 閰嶇疆璧勪骇锛歝onfigs/model_plane/node_registry.yaml + model_profiles.yaml锛涘叾鍙樻洿蹇呴』 bump model_profile_id锛屽苟鍐欏叆 llm_call_log锛堟垨绛変环瀹¤瀛楁锛夈€?- LLM 杈撳嚭濂戠害璧勪骇锛歱rompts/llm_contract/*锛坈ontract 绀轰緥涓庢嫆缁濊寖寮忥級锛涗笉寰楀綋闇€姹傛潵婧愶紝鍙綋濂戠害/鍥炲綊杈撳叆婧愩€?
### 13.4 澶栭儴渚濊禆闅旂瑕佹眰锛坱est 鐜寮哄埗锛?- test 鐜涓嶅緱杩炴帴鐪熷疄 Neo4j銆佺湡瀹炲悜閲忓簱銆佺湡瀹炰簯绔ā鍨嬫湇鍔★紱蹇呴』浣跨敤 stub/fixtures 鎴栨浛韬疄鐜般€?- 鍑虹綉鐩稿叧妯″潡蹇呴』鏀寔鈥渁irlock stub鈥濓細浠嶄骇鐢?airlock_log 缁撴瀯鍖栬褰曪紝浣嗕笉浼氱湡瀹炲彂閫併€?- ClawdBot/UI 绫昏兘鍔涘湪 test 鐜蹇呴』榛樿绂佺敤鎴栨ā鎷燂紙鍙獙璇佹棩蹇?濂戠害涓庣孩绾跨啍鏂彛寰勶級銆?
### 13.5 Gaming 妯″紡璧勬簮绛栫暐锛堟樉瀛樿娓告垙鍗犵敤锛?- Host 涓嶅緱渚濊禆鏈湴 GPU 妯″瀷锛涘彧鍏佽瑙勫垯寮曟搸/妯℃澘/CPU tiny model锛堝瀛樺湪锛夈€?- 浜戠璋冪敤鍏佽缁х画锛屼絾蹇呴』鍙楄秴鏃堕€€鍖栫瓥鐣ョ害鏉燂紝閬垮厤娌夐粯锛圱-Perf-01銆乀-Gaming-01锛夈€?- 娓叉煋浼樺厛璧颁簯绔?renderer锛涗簯涓嶅彲鐢ㄦ椂閫€鍖栦负 Host 鏈€灏忔棩鏂囨ā鏉匡紙涓嶅緱鍔犳枡锛夈€?
---

## 14. 瀹炴柦妫€鏌ユ竻鍗曪紙Codex 鎵ц鐢級

### 14.1 鎬昏锛堜綋绯荤粨鏋勪笌鏁版嵁娴侊級
蹇呴』娓呮櫚琛ㄨ揪骞跺湪瀹炵幇涓繚鎸佷竴鑷寸殑浜嬪疄锛?- 鍗曞悜閾撅細杈撳叆 鈫?LLM锛坣arrative + intent_draft 鑽夋锛夆啋 Host锛圙overnor 瑁佸喅锛夆啋 Skills/Actuators锛堟墽琛岋級鈫?Audit锛堜簨瀹烇級
- 鈥滀簨瀹炴潵婧愨€濓細浠呭璁￠摼锛坕ntent/governor/skill_exec锛? Hard State/SSOT锛汱LM 鍙欎簨涓嶆瀯鎴愪簨瀹?
### 14.2 妯″潡杈圭晫锛堝彲闈欐€佹壂鎻忛獙璇侊級
杈圭晫瀹氫箟锛堝繀椤绘弧瓒筹級锛?- Host锛欻ard State / Governor / RAG 涓绘潈 / Audit / Bond Dynamics / Evolution
- LLM锛氬彧璐熻矗鍙欎簨鐢熸垚 + Intent 鑽夋锛堢姝㈡墽琛屻€佺姝㈣鍐炽€佺姝㈠绉颁簨瀹烇級
- Edge锛氭劅鐭?鎵ц绔紙RPC + 绛惧悕 + 閲嶆斁淇濇姢锛夛紝涓嶅緱鏈夎鍐抽€昏緫

### 14.3 鍗忚涓?Schema锛堝己绫诲瀷 + 鍙洖鏀撅級

鐩爣锛氭妸鈥滆瑷€鈫掕涓衡€濈殑鍞竴鍏ュ彛闄愬畾涓哄己绫诲瀷瀵硅薄锛屽苟鑳界ǔ瀹氭嫆缁濄€佺ǔ瀹氬璁°€佺ǔ瀹氬洖鏀俱€?
蹇呴』瀛樺湪涓斾繚鎸佷竴鑷寸殑濂戠害锛堟渶灏忛泦锛夛細
1锛塈ntent锛堣绗?3 绔狅級
- lenient parse锛氬彧淇娉曚笉琛ヨ涔夛紱澶辫触 INTENT_PARSE_FAILED
- strict validate锛氬瓧娈?绫诲瀷/鏋氫妇/args/閲嶆斁/鏃舵晥锛涘け璐ユ槧灏?INTENT_SCHEMA_INVALID / INTENT_ARGS_INVALID / REPLAY_DETECTED / SEQ_INVALID / EXPIRED_INTENT
- allowlist锛歵ype/action/target

2锛塆overnorOutput锛堣绗?4 绔狅級
- decision/reason_code/rule_trace/tokens/snapshot_refs 缁撴瀯鍥哄畾
- REJECT 蹇呭甫 reason_code + rule_trace
- APPROVE 蹇呭甫 approval_token_ref锛涚己澶卞繀椤昏Е鍙戞墽琛屽叆鍙ｆ嫆缁濓紙SKILL_TOKEN_MISSING锛?
3锛塗oken/Manifest锛堣绗?5 绔狅級
- approval_token锛氱粦瀹?intent_id + skill_name + session_id + seq + TTL
- confirm_token锛氶珮椋庨櫓浜屾鎻愪氦璇佹槑锛堜笉寰楃敤璇█纭鏇夸唬锛?- undo_token + manifest锛氬彲閫嗛粯璁わ紙digest 蹇呴』鍙牳楠岋級

楠屾敹鐐癸紙鍙ｅ緞锛夛細
- T-18~T-22锛圛ntent锛?- T-14~T-17锛圙overnorOutput锛?- T-34~T-38锛圱oken/Undo锛?
### 14.4 鍏抽敭瑙勫垯涓庣畻娉曪紙鍙増鏈寲銆佸彲鍥炲綊锛?
鐩爣锛氭妸瑁佸喅涓庢巿鏉冪殑鍏抽敭閫昏緫鍋氭垚鈥滅増鏈寲瑙勫垯鈥濓紝鑰屼笉鏄暎钀藉湪涓氬姟浠ｇ爜閲屻€?
蹇呴』瀹炵幇涓斿彲鐗堟湰鍖栫殑瑙勫垯鏃忥細
1锛塆overnor Gates锛圧1鈥揜7锛?- trust / energy / sys_mode / rate_limit / scope_policy / safety / airlock
- 姣忔瑁佸喅蹇呴』浜у嚭 rule_trace锛堝寘鍚懡涓棬銆侀槇鍊兼憳瑕併€佸揩鐓ф憳瑕侊級

2锛夊伐鍏蜂笌鎵ц闂ㄧ
- WRITE 绫诲壇浣滅敤鑳藉姏蹇呴』缁?Token锛堝繀瑕佹椂 confirm_token锛?- 宸ュ叿鍙鎬у繀椤绘笎杩涘紡鍔犺浇锛圱op-K 鍏冩暟鎹瓙闆嗭級骞惰褰?tool_visibility_hash

3锛夊箓绛変笌閲嶆斁
- intent_id 骞傜瓑锛氶噸澶?intent 涓嶅緱浜х敓绗簩娆″壇浣滅敤
- seq/nonce/ts_ms锛氶噸鏀剧獥銆佷贡搴忋€佽繃鏈熷潎鍙В閲婃嫆缁?
楠屾敹鐐癸紙鍙ｅ緞锛夛細
- T-Gov-01锛坮ule_trace 鍙畾浣嶏級
- T-SSOT-01锛堟梺璺姝?鍗曞啓鑰咃級
- T-Tool-01~03锛堣嫢 Tool 骞抽潰宸茬撼鍏?DevTasks锛?- T-18~T-22锛堥噸鏀?椤哄簭/杩囨湡锛?
### 14.5 澶辫触涓庨檷绾э紙鐘舵€佹満 + 涓€娆￠噸鍐欙級

鐩爣锛氶摼璺换浣曢樁娈靛け璐ワ紝绯荤粺涔熷繀椤昏緭鍑衡€滅ǔ瀹氱粨鏋?+ 鏄庣‘ reason_code鈥濓紝涓旂粷涓嶇紪閫犱簨瀹炪€?
蹇呴』鏈夌殑鏈哄埗锛?1锛夊垎闃舵鐘舵€佹満锛圫0..S8锛屾垨璇箟绛変环锛?- 姣忛樁娈靛畾涔夛細鎴愬姛鏉′欢/澶辫触鏉′欢/闄嶇骇绛栫暐/瀹¤璁板綍浣嶇疆/瀵瑰 reason_code

2锛塓A 妯″潡锛堣绗?9 绔狅級
- 鎷︽埅锛氫吉鎵ц瀹ｅ憡銆丆anon-as-fact銆佸彛鍚?璇█杩濊銆丣SON 婕傜Щ
- 鍏佽閲嶅啓 1 娆★紱浠嶅け璐ュ繀椤婚檷绾у埌 Host 鏈€灏忔ā鏉胯緭鍑猴紙涓嶅緱鎵胯鎵ц锛?
3锛変緷璧栦笉鍙敤锛圖B/Neo4j/Vector/Cloud锛?- 涓嶅穿婧冿細浠嶈緭鍑哄彲瑙ｆ瀽缁撴瀯
- 鍙娴嬶細error_log/quality.stage 鍙畾浣嶅け璐ョ偣
- 鏇翠繚瀹堬細鎵ц绫昏兘鍔涢粯璁ゆ嫆缁濇垨闄嶇骇涓鸿崏妗?
楠屾敹鐐癸紙鍙ｅ緞锛夛細
- T-67~T-71锛堥檷绾т笌 QA锛?- T-Perf-01锛堣秴鏃堕€€鍖栦笌鍦ㄥ満鍥炲簲锛?- T-39/T-40锛坅irlock 澶辫触璺緞锛?
### 14.6 楠屾敹娴嬭瘯钀藉湴鏂瑰紡锛圱-xx 鈫?tests/contract + tests/e2e锛?
鐩爣锛氭妸 DevSpec 鐨勨€滃绾︿笌鏂█鈥濊惤鎴愪袱绫诲洖褰掞細濂戠害娴嬭瘯锛堝揩閫燂級+ 鏈€灏忛棴鐜紙鍙洖鏀撅級銆?
1锛塩ontract tests锛堝揩閫熴€佹棤澶栭儴渚濊禆浼樺厛锛?蹇呴』瑕嗙洊锛?- Schema/Enum锛欼ntent銆丟overnorOutput銆乀oken銆丮anifest銆乺eason_code 鏋氫妇绋冲畾
- 鎸囬拡涓€鑷存€э細Flow active.pointer 涓?Trace 绱㈠紩涓€鑷存€э紙T-Flow-01/02锛?- 鍗曞啓鑰呬笌鏃佽矾绂佹锛氶潤鎬佹壂鎻忔柇瑷€锛圱-SSOT-01锛?- LLM 杈撳嚭濂戠害 lint锛歋trict JSON锛圱-LLM-01锛?
2锛塭2e tests锛堟渶灏忛棴鐜級
鏈€灏忛摼璺彛寰勶細
- Host 杈撳叆 鈫?LLM stub/fixtures 鈫?QA 鈫掞紙鍙€夛級Governor 鈫?Skill stub 鈫?Audit
蹇呴』鏂█锛?- 涓夎仈鐢诲彲涓茶仈锛歩ntent_log 鈫?governor_log 鈫掞紙鍙€夛級skill_exec_log
- REJECT 蹇呭甫 reason_code
- APPROVE 蹇呭甫 approval_token_ref锛涙棤 token 涓嶅緱鎵ц

寤鸿鏈€灏忚鐩栭泦锛堥殢閲岀▼纰戞墿灞曪級锛?- T-14/T-15
- T-18~T-22
- T-34~T-38
- T-67~T-71
- T-Flow-01~02

### 14.7 鏂板楠屾敹娴嬭瘯缂栧彿锛圴OICE/VISION/RETRIEVAL/EDGE锛?浠ヤ笅娴嬭瘯缂栧彿鐢ㄤ簬瑕嗙洊鏈瀵硅瘽鏂板鐨勬湇鍔￠」娓呭崟涓庤闊?瑙嗚/妫€绱㈤摼璺绾︼紙涓嶅啓鍛戒护锛屼粎瀹氫箟鏂█鐐癸級锛?
- T-80锛圴OICE-ASR锛夛細ASR 涓婇€佷簨浠剁粨鏋勭ǔ瀹氾紙timestamps/confidence/alternates锛夛紱浣庣疆淇″害鏄惧紡鏍囨敞锛涘彲鍥炴斁銆?- T-81锛圴OICE-TTS锛夛細Host 涓嬪彂鏂囨湰鈫扙dge 鍚堟垚鎾姤锛涘け璐ヨ繑鍥?reason_code/stage锛涙挱鎶ヤ簨浠跺彲涓?request_id 涓茶仈銆?- T-82锛圴OICE-BARGEIN锛夛細鎾姤涓敤鎴峰紑鍙ｈЕ鍙戞墦鏂紱鎾斁鍋滄/闄嶇骇锛汚SR 缁х画涓婇€侊紱瀹¤璁板綍鎵撴柇浜嬩欢銆?- T-83锛圗DGE-FALLBACK-LLM锛夛細鍏滃簳 LLM 杈撳嚭涓?draft-only JSON锛涗笉寰楀嚭鐜版墽琛屼簨瀹炲鍛婏紱涓嶅彲鐢ㄦ椂蹇€熷け璐ュ苟瑙﹀彂 Host 鍏滃簳鍙ｅ緞銆?- T-84锛圴ISION-SCREEN-GATE锛夛細Gaming 妯″紡绂佹灞忓箷鐩戞帶/OCR/Clawd UI锛涢潪 Gaming 妯″紡鎸夐渶瑙﹀彂骞朵簨浠跺寲瀹¤銆?- T-85锛圧ETRIEVAL-PIPE锛夛細embedding+rerrank+recall/fusion 杈撳嚭 TopK evidence pack 瀛楁绋冲畾锛涚己浠讳竴鏈嶅姟鏃堕檷绾у彛寰勪笌 reason_code 绋冲畾銆?- T-86锛圔OND-NEO4J锛夛細Neo4j 鍗曞啓鑰呰竟鐣岀敓鏁堬紱bond_summary 娲剧敓涓嶅惈鎵ц浜嬪疄锛涗笉鍙敤鏃堕檷绾у彛寰勭ǔ瀹氥€?- T-87锛圤BS-RESOURCE锛夛細Edge 璇煶閾句笌 GPU 鏄惧瓨鎸囨爣鍙娴嬶紱寮傚父宄板€煎彲瀹氫綅 stage 涓庣粍浠躲€?
### 14.8 鏂板楠屾敹娴嬭瘯缂栧彿锛圡4 浣撻獙鍖咃細SCHEDULER/DESKTOP/DREAM/DIARY/PROACTIVE/FOCUS锛?
浠ヤ笅娴嬭瘯缂栧彿鐢ㄤ簬瑕嗙洊 M4 浣撻獙鍖呮柊澧炲姛鑳界殑濂戠害涓庤涓烘柇瑷€锛?
- T-Sched-01锛圫CHEDULER 娉ㄥ唽涓庤Е鍙戯級锛氳皟搴﹀櫒娉ㄥ唽/娉ㄩ攢/瑙﹀彂鍙洖鏀撅紱system_initiated_intent 甯?proposer_node_id=scheduler锛涜Е鍙戦鐜囩鍚堥厤缃€?- T-Sched-02锛圫CHEDULER 妯″紡闂ㄦ帶锛夛細Gaming 妯″紡鎷︽埅浣庝紭鍏堢骇浠诲姟锛圫YSMODE_FORBIDDEN锛夛紱Sleep 妯″紡浠呭厑璁哥櫧鍚嶅崟浠诲姟锛汧ocus 瀛愭ā寮忔殏鍋滈潪绱ф€ヤ换鍔°€?- T-Sched-03锛圫CHEDULER 鍋ュ悍涓庨檷绾э級锛氳繛缁け璐ヨ嚜鍔ㄦ殏鍋滐紙SCHED_TASK_SUSPENDED锛夛紱鍋ュ悍蹇収鍙鍙栥€?- T-Desktop-01锛堟闈㈢暀瑷€鍒涘缓涓庢挙閿€锛夛細鏂囦欢鍒涘缓鎴愬姛 + manifest 瀹屾暣锛坆efore/after/path/hash锛夛紱undo 鎭㈠鍘熺姸鎬侊紱璺緞绌胯秺鎷掔粷锛圫COPE_VIOLATION锛夈€?- T-Desktop-02锛堟闈㈢暀瑷€ QA锛夛細鍐呭鍚吉鎵ц鍙ユ椂 QA 鎷︽埅骞堕噸鍐欙紱鍐欏叆瀹¤閾惧畬鏁淬€?- T-Dream-01锛堟ⅵ澧冪敓鎴愪笌鏍囩锛夛細Sleep/绌洪棽瑙﹀彂鎴愬姛锛涘啓鍏?Episodic 甯?dream 鏍囩 + NARRATIVE_ONLY锛汵ormal 闈炵┖闂叉嫆缁濄€?- T-Dream-02锛堟ⅵ澧冮潪浜嬪疄绾︽潫锛夛細鍙洖姊﹀鏃?evidence_level=NARRATIVE_ONLY 瀛樺湪锛決A 涓嶅崌绾т负浜嬪疄銆?- T-Diary-01锛堟棩璁扮敓鎴愰棴鐜級锛氬畾鏃惰Е鍙戞垚鍔燂紱鏂囦欢鍒涘缓 + Episodic 鍐欏叆锛涙棤褰撴棩瀵硅瘽鏃朵笉浼€犮€?- T-Diary-02锛堟棩璁?manifest 涓庢挙閿€锛夛細鏃ヨ鏂囦欢 manifest 瀹屾暣锛泆ndo 鍙洖婊氥€?- T-Proactive-01锛堜富鍔ㄥ璇濊Е鍙戜笌闂ㄦ帶锛夛細绌洪棽瑙﹀彂鎴愬姛 + 瀹¤閾惧畬鏁达紱Gaming 鎷掔粷锛涢鐜囪秴闄愭嫆缁濓紙RATE_LIMIT_EXCEEDED锛夈€?- T-Proactive-02锛堜富鍔ㄥ璇濆唴瀹圭害鏉燂級锛氬唴瀹逛笉鍚墽琛?intent锛涙帹閫佸け璐ラ潤榛橀檷绾т笉閲嶈瘯銆?- T-Focus-01锛團ocus Mode 鍒囨崲锛夛細杩涘叆/閫€鍑哄彲瀹¤锛涗富鍔ㄥ璇濊鎷︽埅锛涚敤鎴蜂富鍔ㄦ彁闂甯稿搷搴旓紱閫€鍑哄悗璋冨害鍣ㄦ仮澶嶃€?
---

## 15. M4 浣撻獙鍖呭伐绋嬪绾︼紙SCHEDULER/DESKTOP/DREAM/DIARY/PROACTIVE/FOCUS锛?
瀹氫綅锛氭湰绔犲畾涔?M4 浣撻獙鍖呯殑宸ョ▼瀹炵幇濂戠害鈥斺€旇皟搴﹀櫒鍩虹缁勪欢 + 5 涓綋楠屽姛鑳界殑 Skill 瀹氫箟銆佽Е鍙戠瓥鐣ャ€乧ontent_pack 娉ㄥ叆銆丵A 绾︽潫涓庡璁″彛寰勩€?
瑕嗙洊闇€姹傦細FR-SCHED-001~002銆丗R-DESKTOP-001~002銆丗R-DREAM-001~002銆丗R-DIARY-001~002銆丗R-PROACTIVE-001~002銆丗R-FOCUS-001銆?鍥炲綊鍙ｅ緞锛歍-Sched-01~03銆乀-Desktop-01~02銆乀-Dream-01~02銆乀-Diary-01~02銆乀-Proactive-01~02銆乀-Focus-01銆?
### 15.1 Scheduler锛堝畾鏃惰皟搴﹀櫒锛?
钀界偣锛歚host_brain/scheduler/` 鐩綍锛堟柊澧烇級銆?
15.1.1 鏍稿績缁勪欢
- `scheduler.py`锛氳皟搴﹀櫒涓讳綋锛堝熀浜?APScheduler 鎴栫瓑浠疯交閲忓簱锛夛紝鏀寔 cron/interval/once 涓夌瑙﹀彂妯″紡銆?- `task_registry.py`锛氳皟搴︿换鍔℃敞鍐岃〃锛坱ask_id/trigger_config/intent_template/priority/mode_allowlist/enabled/max_consecutive_failures锛夈€?- `health.py`锛氫换鍔″仴搴峰揩鐓э紙task_id/last_run_ts/next_run_ts/status/error_count/suspended_reason锛夈€?
15.1.2 Intent 鐢熸垚绾﹀畾
- 姣忔瑙﹀彂鐢熸垚 `system_initiated_intent`锛屽瓧娈典笌鐢ㄦ埛 Intent 瀹屽叏涓€鑷达紝浣?`proposer_node_id=scheduler`銆?- Intent 杩涘叆涓庣敤鎴疯姹傜浉鍚岀殑缂栨帓鍏ュ彛锛坄/v1/chat` 鍐呴儴璺緞鎴栫瓑浠风紪鎺掕皟搴﹀嚱鏁帮級锛岃蛋 Governor鈫扵oken鈫扴kill鈫扐udit銆?
15.1.3 sys_mode 闂ㄦ帶鐭╅樀

| 浠诲姟浼樺厛绾?| Normal | Sleep | Gaming | Focus锛圢ormal瀛愭ā寮忥級 |
|-----------|--------|-------|--------|---------------------|
| HIGH锛堝鏃ヨ锛?| 鉁?| 鉁?| 鉂?| 鉁?|
| MEDIUM锛堝姊﹀锛?| 鉁?| 鉁?| 鉂?| 鉂?|
| LOW锛堝涓诲姩瀵硅瘽锛?| 鉁?| 浠呴棶鍊?| 鉂?| 鉂?|

15.1.4 瀹¤瀛楁
- `scheduler_event_log`锛堟柊澧炲璁¤〃锛夛細task_id/trigger_ts/intent_id/status/reason_code/mode_at_trigger/error_detail銆?
### 15.2 Desktop Note Skill锛堟闈㈢暀瑷€锛?
钀界偣锛歚host_brain/skills/impl/desktop_note.py`锛堟柊澧烇級銆?
15.2.1 SkillMeta
```
name: desktop_note
clearance_level: L1
risk_level: 2
reversible: true
scopes: [filesystem]
requires_visual_audit: false
requires_confirm_token: false
data_classification: INTERNAL
rate_limit: 10/hour
energy_cost: low
```

15.2.2 鎵ц濂戠害
- 杈撳叆 args锛歿action: create|update|delete, filename: str, content: str, base_dir_key: str}
- base_dir_key 鏄犲皠鍒伴厤缃腑鐨勫畨鍏ㄥ熀鐩綍锛堝 `NATSUME_DESKTOP_DIR`锛夛紱璺緞绌胯秺妫€鏌ワ紙`..`/symlink 鎷掔粷锛夈€?- create/update锛氬啓鍏ユ枃浠?鈫?浜у嚭 manifest锛坆efore_hash/after_hash/path锛? undo_token銆?- delete锛歮ove-to-quarantine锛圴ault quarantine/锛夛紝涓嶇湡鍒狅紱鐪熷垹闇€ confirm_token銆?- 鍐呭缁?QA 鏍￠獙锛堜笉寰楀惈浼墽琛屽彞锛夈€?
15.2.3 reason_code 鏄犲皠
- 璺緞绌胯秺锛歋COPE_VIOLATION
- 鍩虹洰褰曚笉鍙啓锛歋KILL_EXEC_FAILED锛堝瓙绫?FS_WRITE_FAILED锛?- QA 鎷︽埅锛欶ALSE_EXECUTION_CLAIM锛堥噸鍐欏悗閲嶈瘯 1 娆★級

### 15.3 Dream Skill锛堟ⅵ澧冪敓鎴愶級

钀界偣锛歚host_brain/skills/impl/dream_generator.py`锛堟柊澧烇級銆?
15.3.1 SkillMeta
```
name: dream_generator
clearance_level: L0
risk_level: 1
reversible: true
scopes: [memory]
requires_visual_audit: false
requires_confirm_token: false
data_classification: INTERNAL
rate_limit: 2/day
energy_cost: medium
```

15.3.2 鎵ц濂戠害
- 瑙﹀彂鏉′欢鐢?Scheduler 鍒ゅ畾锛坰ys_mode=SLEEP 鎴?idle_duration 鈮?闃堝€硷級銆?- 璇诲彇褰撴棩 Episodic Memory锛堟寜 ts_ms 杩囨护褰撴棩鏉＄洰锛孴opK=20锛夈€?- 鎵撳寘 prompt_context锛堝惈 bond_summary + 褰撴棩 episodic 鎽樿锛夆啋 LLM 鐢熸垚姊﹀鍙欎簨銆?- 鍐欏叆 Episodic锛歝ategory=dream, evidence_level=NARRATIVE_ONLY, evidence_refs=[intent_id]銆?- manifest锛歮emory_before_count/memory_after_count/dream_id/hash銆?
15.3.3 鍐呭鏍囩涓庡彛寰?- 鍐欏叆鏃跺繀椤绘爣娉?`evidence_level=NARRATIVE_ONLY`銆?- 鍙洖鏃?prompt_context 鐨?retrieval.episodic 妲戒綅闇€淇濈暀姝ゆ爣娉紝QA 鎹鍒ゆ柇"涓嶅彲鍗囩骇涓轰簨瀹?銆?
### 15.4 Diary Skill锛堟棩璁扮敓鎴愶級

钀界偣锛歚host_brain/skills/impl/diary_writer.py`锛堟柊澧烇級銆?
15.4.1 SkillMeta
```
name: diary_writer
clearance_level: L0
risk_level: 1
reversible: true
scopes: [memory, filesystem]
requires_visual_audit: false
requires_confirm_token: false
data_classification: INTERNAL
rate_limit: 1/day
energy_cost: medium
```

15.4.2 鎵ц濂戠害
- 姣忔棩瀹氭椂瑙﹀彂锛堝 23:00锛夛紝鐢?Scheduler 鍙戣捣銆?- 姹囨€诲綋鏃ュ璇濓細璇诲彇 intent_log + governor_log锛堝綋鏃?ts_ms 鑼冨洿锛? Episodic 褰撴棩鏉＄洰銆?- 妫€鏌ユ槸鍚︽湁瀹炶川瀵硅瘽鍐呭锛堥潪绌哄垽瀹氾級锛氭棤瀵硅瘽 鈫?璺宠繃骞惰褰?SCHED_NO_CONTENT锛堜笉浼€犳棩璁帮級銆?- 鏈夊璇?鈫?鎵撳寘 prompt_context 鈫?LLM 鐢熸垚鏃ヨ鍙欎簨 鈫?鍙屽啓锛?  - 鏈湴鏂囦欢锛歚{base_dir}/diary/{YYYY-MM-DD}.md`锛屼骇鍑?file manifest銆?  - Episodic Memory锛歝ategory=diary, evidence_refs=[intent_id, file_path]銆?- QA 鏍￠獙锛氭棩璁板唴瀹逛笉寰楀惈浼墽琛屽彞銆?
15.4.3 Evolution 鎻愭锛堝彲閫夋墿灞曪級
- 鏃ヨ LLM 杈撳嚭鍙€夊寘鍚?`drift_suggestion` 瀛楁銆?- 鑻ュ瓨鍦細鐢熸垚 canon_proposal 鈫?鍐欏叆 canon_proposal_log 鈫?寰?Host 鐧藉悕鍗?Evolution 瀹℃牳銆?- 涓嶅瓨鍦細璺宠繃锛堥粯璁よ矾寰勶級銆?
### 15.5 Proactive Conversation Skill锛堜富鍔ㄥ璇濓級

钀界偣锛歚host_brain/skills/impl/proactive_talk.py`锛堟柊澧烇級銆?
15.5.1 SkillMeta
```
name: proactive_talk
clearance_level: L0
risk_level: 1
reversible: false
scopes: [dialogue]
requires_visual_audit: false
requires_confirm_token: false
data_classification: NONE
rate_limit: 3/hour
energy_cost: low
```

15.5.2 瑙﹀彂绛栫暐锛圫cheduler 浠诲姟閰嶇疆锛?- 鏉′欢缁勫悎锛圓ND 閫昏緫锛夛細
  - idle_duration 鈮?闃堝€硷紙榛樿 30 鍒嗛挓锛屽彲閰嶇疆锛?  - bond.stage 鈮?threshold锛堥粯璁?familiar 浠ヤ笂锛?  - sys_mode 鈭?[NORMAL]锛圙aming/Sleep/Focus 鍧囩姝㈡垨浠呮瀬浣庨闂€欙級
  - 褰撳墠灏忔椂 鈭?active_hours锛堥粯璁?8:00~23:00锛?- 棰戠巼闄愬埗锛氭瘡灏忔椂鏈€澶?3 娆★紙 Rate Limit R4 Gate 鐢熸晥锛夈€?
15.5.3 鎵ц濂戠害
- 鎵撳寘 prompt_context锛堝惈 bond_summary + 鏈€杩?episodic + 褰撳墠鏃堕棿娈典俊鎭級鈫?LLM 鐢熸垚涓诲姩瀵硅瘽鑽夋銆?- QA 鏍￠獙锛氬唴瀹逛粎闄愰棶鍊?鍏冲績/鍥炲繂寮曠敤/鎻愰啋鍙ｅ緞锛涘惈鎵ц intent 鈫?鎷︽埅閲嶅啓銆?- 鎺ㄩ€佽矾寰勶細
  - Edge 鍙揪 鈫?RPC speak锛堣闊筹級鎴?text push锛?  - 寰俊閫氶亾鍙敤 鈫?閫氳繃 wechat outbound 鎺ㄩ€侊紱
  - 鍧囦笉鍙揪 鈫?闈欓粯闄嶇骇锛堣褰?DEPENDENCY_UNAVAILABLE锛夛紝涓嶉噸璇曟帹閫侊紙闃查獨鎵帮級銆?- 瀹¤锛歱roactive_event_log锛堟柊澧炴垨澶嶇敤 skill_exec_log锛夎褰曡Е鍙戞潯浠跺揩鐓с€佹帹閫佺粨鏋溿€乺eason_code銆?
### 15.6 Focus Mode锛堜笓娉ㄦā寮忕姸鎬佹墿灞曪級

钀界偣锛歚host_brain/core/metabolism.py`锛堟墿灞曠幇鏈?sys_mode 鐘舵€佹満锛夈€?
15.6.1 鐘舵€佸畾涔?- Focus 涓?Normal 鐨勫瓙妯″紡锛歿sys_mode: NORMAL, sub_mode: FOCUS}銆?- 杩涘叆鏉′欢锛氱敤鎴锋寚浠?/ 瀹氭椂绛栫暐 / 鏃ュ巻浜嬩欢锛堝潎闇€ evidence锛夈€?- 閫€鍑烘潯浠讹細鐢ㄦ埛鎸囦护 / 瀹氭椂绛栫暐鍒版湡 / 瓒呮椂鑷姩閫€鍑恒€?
15.6.2 鑳藉姏鐭╅樀褰卞搷
- 涓诲姩瀵硅瘽锛氣潓 鏆傚仠锛圫YSMODE_FORBIDDEN锛?- 姊﹀/鏃ヨ绛夐潪绱ф€ヨ皟搴︿换鍔★細鉂?鏆傚仠
- 妗岄潰鐣欒█锛堢敤鎴疯姹傝Е鍙戯級锛氣渽 鍏佽
- 鐢ㄦ埛涓诲姩鎻愰棶锛氣渽 姝ｅ父鍝嶅簲
- 閫氱煡鎺ㄩ€侊細鉂?闈欓粯

15.6.3 瀹¤
- 杩涘叆/閫€鍑哄啓 hard_state_log锛堝師鍥犮€佽瘉鎹憳瑕併€佹寔缁椂闀匡級銆?- 琚嫤鎴殑璋冨害浠诲姟鍐?scheduler_event_log锛坮eason_code=SYSMODE_FORBIDDEN_FOCUS锛夈€?
---

## 16. 鎬ц兘棰勭畻涓庢椂寤跺彲瑙傛祴锛圢FR-PERF-001锛?
瀹氫綅锛氭湰绔犲畾涔?鍝嶅簲鏃跺欢棰勭畻"鐨勫伐绋嬪绾︹€斺€旈噰闆嗘柟寮忋€侀槇鍊煎彛寰勩€佽秴鏃堕€€鍖栫瓥鐣ヤ笌鍙娴嬫姤鍛婄粨鏋勶紝浣?NFR-PERF-001 浠?PRD 鐨勫畾鎬ф弿杩板彉涓哄彲楠岃瘉鐨勫伐绋嬪熀绾裤€?
瑕嗙洊闇€姹傦細NFR-PERF-001銆?鍥炲綊鍙ｅ緞锛歍-Perf-01锛堟椂寤堕绠楀熀鍑嗕笌瓒呮椂閫€鍖栵級銆?
### 16.1 鏃跺欢棰勭畻瀹氫箟锛堝熀鍑嗘満鍣ㄥ彛寰勶級

鍩哄噯鏈哄櫒锛欻ost 渚?i7-12700 / 32GB RAM / SSD锛汦dge 渚?RTX 3060 12GB VRAM锛堟垨绛変环锛夈€?
| 閾捐矾闃舵 | 鎸囨爣 | p50 鐩爣 | p95 鐩爣 | 瓒呮椂纭檺 |
|----------|------|---------|---------|---------|
| 鏅€氬璇濓紙鏃犳墽琛岋級 | 鐢ㄦ埛杈撳叆鈫扝ost 杈撳嚭 narrative | 鈮?2000ms | 鈮?5000ms | 10000ms |
| 鎵ц閾捐矾锛堝惈 Skill锛?| 鐢ㄦ埛杈撳叆鈫抯kill_exec_log 鍐欏叆 | 鈮?3000ms | 鈮?8000ms | 15000ms |
| 妫€绱㈤摼璺紙Embedding+Rerank+Fusion锛?| query鈫抏vidence_pack 杈撳嚭 | 鈮?500ms | 鈮?1500ms | 3000ms |
| LLM 澶栭儴璋冪敤锛堝崟娆★級 | request鈫抮esponse | 鈮?1500ms | 鈮?4000ms | 8000ms |
| Ledger 鍐欏叆锛堝崟鏉★級 | write鈫抍ommit | 鈮?5ms | 鈮?20ms | 100ms |
| Edge ASR锛堝崟娈佃浆鍐欙級 | audio_chunk鈫抋sr_chunk | 鈮?300ms | 鈮?800ms | 2000ms |
| Edge TTS锛堝悎鎴?鎾斁鍚姩锛?| text鈫抋udio_start | 鈮?500ms | 鈮?1500ms | 3000ms |

绾︽潫锛?- 闃堝€间负鎸囧鎬у熀绾匡紙闈炵‖闂ㄧ锛夛紝瓒呭嚭 p95 鏃跺繀椤诲湪 quality.stage 涓爣娉?PERF_DEGRADED 骞惰褰?reason_code=PERF_TIMEOUT 鎴栨洿绮剧‘瀛愮被銆?- 瓒呮椂纭檺鍒拌揪鏃跺繀椤昏Е鍙戣秴鏃堕€€鍖栫瓥鐣ワ紙瑙?16.2锛夛紝涓嶅緱鏃犻檺闃诲銆?
### 16.2 瓒呮椂閫€鍖栫瓥鐣?
- LLM 璋冪敤瓒呮椂锛氳繘鍏?Host 鏈€灏忔ā鏉胯緭鍑猴紙涓嶄吉鎵ц锛夛紝quality.stage 鏍囨敞 LLM_TIMEOUT锛宺eason_code=PERF_LLM_TIMEOUT銆?- 妫€绱㈤摼璺秴鏃讹細杩斿洖绌?evidence_pack锛堝瓧娈电ǔ瀹氾級+ quality.stage=RETRIEVAL_TIMEOUT锛宺eason_code=PERF_RETRIEVAL_TIMEOUT銆?- Skill 鎵ц瓒呮椂锛欸overnor 鎵瑰噯鍚庢墽琛岃秴鏃讹紝璁板綍 skill_exec_log锛坮esult_status=TIMEOUT锛? reason_code=PERF_SKILL_TIMEOUT锛岃繑鍥為檷绾ц緭鍑恒€?- Edge 瓒呮椂锛圓SR/TTS锛夛細Host 渚ф爣娉?DEPENDENCY_TIMEOUT锛屼笉闃诲涓昏緭鍑恒€?
### 16.3 鏃跺欢閲囬泦涓庢姤鍛婄粨鏋?
閲囬泦鐐癸紙蹇呴』锛夛細
- Host 涓婚摼璺細request_start_ms / llm_call_start_ms / llm_call_end_ms / governor_start_ms / governor_end_ms / skill_start_ms / skill_end_ms / response_end_ms銆?- 妫€绱㈤摼璺細embed_start_ms / embed_end_ms / rerank_start_ms / rerank_end_ms / fusion_end_ms銆?- Edge 閾捐矾锛歛sr_start_ms / asr_end_ms / tts_start_ms / tts_audio_start_ms銆?
閲囬泦鍐欏叆浣嶇疆锛?- 姣忔璇锋眰鍦ㄥ璁￠摼鎵╁睍瀛楁涓褰?`latency_breakdown`锛圝SON 瀵硅薄锛屽惈涓婅堪鏃堕棿鎴冲锛夈€?- 瀹氭湡鑱氬悎鎶ュ憡锛堢敱 bench 鑴氭湰鎴栬娴嬮潰鏉挎寜鏃堕棿绐楀彛杈撳嚭 p50/p95/p99 缁熻锛夈€?
鎶ュ憡缁撴瀯锛堟渶灏忓瓧娈碉級锛?```
{
  "report_ts": "ISO8601",
  "window_ms": 3600000,
  "sample_count": 100,
  "stages": {
    "e2e_chat": {"p50_ms": ..., "p95_ms": ..., "p99_ms": ..., "timeout_count": ...},
    "llm_call": {"p50_ms": ..., "p95_ms": ..., "p99_ms": ..., "timeout_count": ...},
    "retrieval": {"p50_ms": ..., "p95_ms": ..., "p99_ms": ..., "timeout_count": ...},
    "ledger_write": {"p50_ms": ..., "p95_ms": ..., "p99_ms": ..., "timeout_count": ...}
  }
}
```

### 16.4 鏂板楠屾敹娴嬭瘯缂栧彿

- T-Perf-01锛堟€ц兘棰勭畻鍩哄噯涓庤秴鏃堕€€鍖栵級锛?  - 鍦ㄥ熀鍑嗘満鍣ㄤ笂杩愯 bench 鑴氭湰锛岄獙璇佸悇闃舵 p50/p95 鏄惁鍦ㄧ洰鏍囪寖鍥村唴锛?  - 妯℃嫙 LLM/妫€绱?Skill 瓒呮椂鍦烘櫙锛岄獙璇侀€€鍖栫瓥鐣ョ敓鏁堬紙杈撳嚭缁撴瀯绋冲畾 + quality.stage 鏍囨敞瓒呮椂闃舵 + reason_code 鍙粺璁★級锛?  - Ledger 鍐欏叆鎬ц兘鍩哄噯锛堝鐢?ledger_bench.py锛夋弧瓒?p95 鈮?20ms銆?
### 16.5 鏂板閿欒鐮?
- PERF_TIMEOUT锛氶€氱敤鏃跺欢瓒呴檺
- PERF_LLM_TIMEOUT锛歀LM 璋冪敤瓒呮椂
- PERF_RETRIEVAL_TIMEOUT锛氭绱㈤摼璺秴鏃?- PERF_SKILL_TIMEOUT锛歋kill 鎵ц瓒呮椂

---

## 17. M7 鍙娴?+ MCP + 璇煶娣卞寲锛堝伐绋嬪绾︼級

> 瀵瑰簲 PRD 搂4.16锛團R-OBS锛夈€伮?.18锛團R-MCP锛夈€伮?.19锛團R-VOICE锛夈€?> 褰撳墠鐘舵€侊細PRD_READY 鈫?鏈珷鑺傚畬鎴愬悗杩涘叆 SPEC_READY銆?
---

### 17.1 鍙娴嬩笌杩愮淮锛團R-OBS-001/002锛?
#### 17.1.1 鍏抽敭鎸囨爣闈㈡澘锛團R-OBS-001锛?
**鎸囨爣閲囬泦鐐癸紙蹇呴』锛?*锛?- `governor_decision_total`锛氭寜 decision锛圓PPROVE/REJECT/CONFIRM_REQUIRED锛? reason_code 鍒嗙粍璁℃暟銆?- `pseudo_exec_violation_total`锛歈A 鎷︽埅鐨勪吉鎵ц瀹ｅ憡娆℃暟锛坮eason_code=PSEUDO_EXEC_VIOLATION锛夈€?- `retrieval_evidence_count`锛氭瘡娆″彫鍥炶瀺鍚堢殑 evidence 鍊欓€夋暟锛坧50/p95锛夈€?- `hard_snapshot_age_ms`锛欻ard Snapshot 璺濅笂娆℃洿鏂扮殑鏃堕棿宸紙鍙娴?staleness锛夈€?- `chain_summary_count`锛氭渶杩?N 鏉￠摼璺憳瑕侊紙request_id/intent_id/decision/stage/ts_ms锛夈€?
**闈㈡澘鎺ュ彛**锛?```
GET /obs/metrics
鈫?{
    "governor_decisions": {"APPROVE": int, "REJECT": int, "CONFIRM_REQUIRED": int},
    "top_reject_codes": [{"reason_code": str, "count": int}],
    "pseudo_exec_violations": int,
    "retrieval_p50": float,
    "hard_snapshot_age_ms": int,
    "recent_chains": [{"request_id": str, "decision": str, "stage": str, "ts_ms": int}]
  }
```

**鏃ュ織瀛楁瀵归綈**锛氭墍鏈夋寚鏍囧繀椤诲彲閫氳繃 `request_id` / `intent_id` 涓茶仈鍒?SQLite Ledger銆?
**楠屾敹娴嬭瘯缂栧彿**锛歍-OBS-01锛堟寚鏍囬潰鏉跨粨鏋勭ǔ瀹?+ request_id 涓茶仈鍙洖鏀撅級

#### 17.1.2 寮€鍙?鐢熶骇妯″紡宸紓锛團R-OBS-002锛?
**妯″紡鍒囨崲**锛氶€氳繃 `OBS_MODE=dev|prod` 鐜鍙橀噺鎺у埗锛堟垨 config 瀛楁锛夈€?
**琛屼负宸紓**锛?- `dev` 妯″紡锛歚/obs/metrics` 棰濆杩斿洖 `debug_trace`锛堝惈 LLM 鍘熷 intent_draft銆丟overnor 瑙勫垯鍛戒腑璺緞銆丼kill 鎵ц鍙傛暟锛夈€?- `prod` 妯″紡锛歚debug_trace` 瀛楁涓嶈繑鍥烇紙鎴栬繑鍥?null锛夛紱浠呬繚鐣?`user_facing_hint`銆?
**閿欒鐮?*锛?- `OBS_MODE_INVALID`锛歰bs_mode 鍊间笉鍚堟硶銆?
**楠屾敹娴嬭瘯缂栧彿**锛歍-OBS-02锛堝垏鎹㈡ā寮忓悗 debug_trace 瀛楁鎸夐鏈熷嚭鐜?娑堝け锛涗笉褰卞搷瑁佸喅涓庡璁′簨瀹烇級

---

### 17.2 MCP Provider 娌荤悊锛團R-MCP-001锛?
#### 17.2.1 ToolMeta 鍚屾涓庡垎绾?
**ToolMeta Schema**锛?```json
{
  "provider_id": "string",
  "tool_id": "string",
  "name": "string",
  "description": "string",
  "access_level": "READ | WRITE",
  "risk_level": 1,
  "reversible": true,
  "requires_token": false,
  "schema_version": "string",
  "synced_at_ms": 0
}
```

**鍚屾瑙勫垯**锛?- MCP provider 鏆撮湶鐨勫伐鍏峰繀椤诲厛鍚屾涓?ToolMeta锛堜粎鍏冩暟鎹級锛屼笉寰楃洿鎺ヨ皟鐢ㄣ€?- ToolMeta 鍐欏叆 `mcp_tool_registry`锛圫QLite 琛級锛屽彉鏇村啓 `mcp_sync_log`锛堝彲鍥炴斁锛夈€?
#### 17.2.2 ToolBroker 鍗曞叆鍙?
**璋冪敤閾?*锛歚intent_draft.mcp_call 鈫?ToolBroker 鈫?Governor gate 鈫?Token锛圵RITE 蹇呴』锛夆啋 MCP SDK 鈫?Audit`

**瀹¤瀛楁**锛堝啓鍏?`mcp_exec_log`锛夛細
```json
{
  "request_id": "string",
  "intent_id": "string",
  "provider_id": "string",
  "tool_id": "string",
  "access_level": "READ | WRITE",
  "decision": "APPROVE | REJECT",
  "reason_code": "string | null",
  "evidence_ref": "string",
  "ts_ms": 0
}
```

**闂ㄧ瑙勫垯**锛?- `access_level=WRITE` 涓旀棤 approval_token 鈫?`reason_code=TOKEN_REQUIRED`锛屾嫆缁濄€?- `access_level=WRITE` 涓?token 鏈?APPROVE 鈫?`reason_code=TOKEN_NOT_APPROVED`锛屾嫆缁濄€?- AIRLOCK 鍑虹珯鏈€灏忓寲锛歁CP 璋冪敤鍙傛暟蹇呴』缁?AIRLOCK 鑴辨晱锛堟晱鎰熷瓧娈典笉鍑虹珯锛夈€?
**閿欒鐮?*锛?- `MCP_TOOL_NOT_REGISTERED`锛氬伐鍏锋湭鍦?ToolMeta 娉ㄥ唽銆?- `MCP_WRITE_TOKEN_REQUIRED`锛歐RITE 鎿嶄綔缂哄皯 token銆?- `MCP_PROVIDER_UNAVAILABLE`锛歱rovider 涓嶅彲杈撅紝闄嶇骇銆?
**楠屾敹娴嬭瘯缂栧彿**锛?- T-MCP-01锛圵RITE 鏃?token 鈫?TOKEN_REQUIRED 鎷掔粷 + 瀹¤鍙洖鏀撅級
- T-MCP-02锛圧EAD 鏃?token 鈫?姝ｅ父鎵ц + 瀹¤鍙洖鏀撅級
- T-MCP-03锛坧rovider 涓嶅彲杈?鈫?MCP_PROVIDER_UNAVAILABLE 闄嶇骇 + 缁撴瀯绋冲畾锛?
---

### 17.3 璇煶閾捐矾娣卞寲锛團R-VOICE-001~005锛?
#### 17.3.1 ASR 浜嬩欢鍖栦笂閫侊紙FR-VOICE-001锛?
**浜嬩欢 Schema**锛圗dge鈫扝ost RPC锛夛細
```json
{
  "event_type": "asr_transcript",
  "request_id": "string",
  "session_id": "string",
  "text": "string",
  "confidence": 0.95,
  "is_final": true,
  "ts_start_ms": 0,
  "ts_end_ms": 0,
  "alternates": []
}
```

**绾︽潫**锛?- `confidence < 0.6` 鏃跺繀椤绘樉寮忔爣娉?`low_confidence=true`锛孒ost 鍙€夋嫨闄嶇骇澶勭悊銆?- 浜嬩欢鍐欏叆 `voice_event_log`锛堝惈 request_id 涓茶仈锛夈€?
**閿欒鐮?*锛歚ASR_LOW_CONFIDENCE`銆乣ASR_UNAVAILABLE`

**楠屾敹娴嬭瘯缂栧彿**锛歍-VOICE-01锛圓SR 浜嬩欢缁撴瀯绋冲畾 + 浣庣疆淇″害鏍囨敞 + 瀹¤鍙洖鏀撅級

#### 17.3.2 TTS 鍚堟垚涓庢挱鎶ラ槦鍒楋紙FR-VOICE-002锛?
**涓嬪彂 Schema**锛圚ost鈫扙dge RPC锛夛細
```json
{
  "request_id": "string",
  "intent_id": "string",
  "text": "string",
  "emotion_tag": "NEUTRAL | HAPPY | SAD | ANGRY | SURPRISED | THINKING",
  "priority": 1,
  "interruptible": true
}
```

**鎾姤闃熷垪瑙勫垯**锛?- 闃熷垪鎸?priority 鎺掑簭锛涘悓浼樺厛绾?FIFO銆?- `interruptible=true` 鐨勪换鍔″彲琚?Barge-in 鎵撴柇銆?- 鎾姤瀹屾垚/鎵撴柇鍚庡啓 `voice_event_log`锛堝惈 reason_code 鑻ュけ璐ワ級銆?
**閿欒鐮?*锛歚TTS_MODEL_UNAVAILABLE`銆乣TTS_QUEUE_FULL`銆乣TTS_INTERRUPTED`

**楠屾敹娴嬭瘯缂栧彿**锛歍-VOICE-02锛圱TS 涓嬪彂缁撴瀯绋冲畾 + 闃熷垪浼樺厛绾?+ 澶辫触鍙В閲婏級

#### 17.3.3 寮轰氦浜掑彲鎵撴柇 Barge-in锛團R-VOICE-003锛?
**瑙﹀彂鏉′欢**锛欰SR 妫€娴嬪埌鐢ㄦ埛寮€鍙ｏ紙`is_speaking=true`锛夋椂锛孍dge 绔嬪嵆鍙戦€?`barge_in` 浜嬩欢銆?
**Host 澶勭悊**锛?- 鏀跺埌 `barge_in` 鈫?鍚?Edge 鍙戦€?`tts_interrupt` 鎸囦护 鈫?鍐?`voice_event_log`锛坋vent_type=barge_in锛夈€?- 鎵撴柇涓嶆瀯鎴愪换浣曟墽琛屼簨瀹炴垨鍐欏叆浜嬪疄锛坮eason_code=BARGE_IN_INTERRUPTED锛夈€?
**楠屾敹娴嬭瘯缂栧彿**锛歍-VOICE-03锛堟墦鏂珛鍗崇敓鏁?+ 瀹¤璁板綍 + ASR 涓嶈闃诲锛?
#### 17.3.4 Edge 鍏滃簳灏忔ā鍨嬶紙FR-VOICE-004锛?
**瑙﹀彂鏉′欢**锛欻ost 涓婚摼璺笉鍙敤锛圕loud LLM 瓒呮椂/涓嶅彲杈撅級涓?Edge 鍏滃簳妯″瀷宸插姞杞姐€?
**绾︽潫**锛?- 鍏滃簳杈撳嚭蹇呴』鏍囪 `draft_only=true`锛屼笉寰楀寘鍚鍐虫垨鎵ц浜嬪疄瀹ｅ憡銆?- 榛樿鍗歌浇锛坄ollama_loaded=false`锛夛紝鎸夐渶鍔犺浇锛堣Е鍙戝悗 鈮?0s 鍐呭彲鐢級銆?- 涓嶅彲鐢ㄦ椂 `reason_code=FALLBACK_MODEL_UNAVAILABLE`锛屽揩閫熷け璐ュ洖閫€鍒?Host 鍏滃簳鍙ｅ緞銆?
**閿欒鐮?*锛歚FALLBACK_MODEL_UNAVAILABLE`銆乣FALLBACK_OUTPUT_DRAFT_ONLY`

**楠屾敹娴嬭瘯缂栧彿**锛歍-VOICE-04锛堝厹搴曡緭鍑烘爣璁?draft_only + 涓嶅彲鐢ㄦ椂蹇€熷け璐?+ 缁撴瀯绋冲畾锛?
#### 17.3.5 Edge 璧勬簮闂搁棬锛團R-VOICE-005锛?
**浼樺厛绾х煩闃?*锛?
| 浼樺厛绾?| 浠诲姟 | 鎶㈠崰瑙勫垯 |
|--------|------|---------|
| P1锛堟渶楂橈級| ASR 閲囬泦 | 涓嶅彲琚姠鍗?|
| P2 | 闊抽鎾斁 | 鍙 P1 鎵撴柇 |
| P3 | TTS 鍚堟垚 | 鍙 P1/P2 鎵撴柇 |
| P4锛堟渶浣庯級| 鍏滃簳 LLM 鎺ㄧ悊 | 鍙浠绘剰楂樹紭鍏堢骇鎶㈠崰 |

**璧勬簮闂搁棬 Schema**锛堝仴搴峰揩鐓э級锛?```json
{
  "asr_active": true,
  "playback_active": false,
  "tts_synthesizing": false,
  "fallback_llm_loaded": false,
  "gpu_utilization_pct": 45,
  "current_priority": "P1"
}
```

**閿欒鐮?*锛歚RESOURCE_GATE_PREEMPTED`锛堜綆浼樺厛绾т换鍔¤鎶㈠崰锛?
**楠屾敹娴嬭瘯缂栧彿**锛歍-VOICE-05锛堝帇鍔涗笅 ASR 浼樺厛淇濊瘉 + 浣庝紭鍏堢骇闄嶇骇鍙娴嬶級

---

### 17.4 M7 鏂板閿欒鐮佹眹鎬?
- `OBS_MODE_INVALID`锛歰bs_mode 鍊间笉鍚堟硶
- `MCP_TOOL_NOT_REGISTERED`锛歁CP 宸ュ叿鏈敞鍐?- `MCP_WRITE_TOKEN_REQUIRED`锛歁CP WRITE 缂哄皯 token
- `MCP_PROVIDER_UNAVAILABLE`锛歁CP provider 涓嶅彲杈?- `ASR_LOW_CONFIDENCE`锛欰SR 缃俊搴︿綆浜庨槇鍊?- `ASR_UNAVAILABLE`锛欰SR 鏈嶅姟涓嶅彲鐢?- `TTS_QUEUE_FULL`锛歍TS 鎾姤闃熷垪宸叉弧
- `TTS_INTERRUPTED`锛歍TS 琚?Barge-in 鎵撴柇
- `FALLBACK_MODEL_UNAVAILABLE`锛欵dge 鍏滃簳妯″瀷涓嶅彲鐢?- `FALLBACK_OUTPUT_DRAFT_ONLY`锛氬厹搴曡緭鍑轰粎涓鸿崏妗?- `RESOURCE_GATE_PREEMPTED`锛氳祫婧愰椄闂ㄦ姠鍗?
---

鏈枃浠朵笉鍐嶇淮鎶?PRD鈫扗evSpec鈫扗evTasks鈫扵ests"鐨勮鐩栫煩闃碉紝浠ラ伩鍏嶉噸澶嶇淮鎶ゅ鑷寸殑鐘舵€佽繃鏈熶笌鍐茬獊銆?瑕嗙洊鍏崇郴鐨勫敮涓€鐪熺浉婧愭槸 Trace锛歞ocs/60_trace/requirements_trace.md銆?
鏈枃浠讹紙DevSpec锛夊彧鎵挎媴涓や欢浜嬶細
1) 鎶?FR 闇€姹傝惤鎴愬彲瀹￠槄鐨勫伐绋嬪绾︼紙鎺ュ彛/Schema/閿欒鐮?鏃ュ織鐐?娴嬭瘯鐐圭紪鍙?T-xx锛夈€?2) 缁欏嚭"濡備綍楠岃瘉"鐨勬柇瑷€鍙ｅ緞锛圱-xx 鐨勮緭鍏?杈撳嚭/鎷掔粷鐮?瀹¤瀛楁锛夈€?
鐘舵€佷娇鐢ㄥ缓璁紙Trace 渚ф灇涓句负鍑嗭級锛?- PRD_READY / SPEC_READY / TASK_READY / TEST_READY / DONE锛氱敤浜庤〃杈鹃摼璺垚鐔熷害銆?- SPEC_GAP / TASK_GAP / TEST_GAP锛氱敤浜庢槑纭?缂哄彛鍦ㄥ摢閲?锛屼究浜庝笅涓€杞ˉ榻愩€?
---

## 搂18 M8 杩愯鏃堕泦鎴愰棴鐜?
> 瀵瑰簲 PRD M8锛涜鐩?NFR-PERF-001銆丯FR-REL-001銆丗R-CORE-004銆?> 鐩爣锛氳 M1~M7 鎵€鏈夊凡瀹炵幇妯″潡鐪熸鑳借涓€閿惎鍔ㄣ€佺鍒扮鑱旈€氾紝骞跺缓绔嬪彲鍥炲綊鐨勬€ц兘鍩虹嚎涓庢晠闅滄敞鍏ョ煩闃点€?
### 18.1 杩愯鏃惰剼鏈泦鎴愶紙TASK-0107锛?
#### 18.1.1 start_host_side.ps1 闆嗘垚 OBS 璺敱涓?MCP Broker锛圢FR-REL-001锛?
**褰撳墠缂哄彛**锛歚start_host_side.ps1` 鍚姩 `host_brain` 杩涚▼鍚庯紝`/obs/metrics` 璺敱涓?`MCPToolBroker` 宸插湪浠ｇ爜涓疄鐜帮紝浣嗗惎鍔ㄨ剼鏈殑鍋ュ悍妫€鏌ヤ粎鎺㈡祴 `/health`锛屾湭楠岃瘉 OBS 涓?MCP 瀛愮郴缁熸槸鍚︽甯稿垵濮嬪寲銆?
**宸ョ▼濂戠害**锛?- `start_host_side.ps1` 鍦?`/health` 閫氳繃鍚庯紝棰濆鎺㈡祴 `/obs/metrics`锛堟湡鏈?HTTP 200锛夛紱
- 鑻?`/obs/metrics` 杩斿洖闈?200锛屽惎鍔ㄨ剼鏈緭鍑?`obs_status=degraded`锛堜笉闃绘柇鍚姩锛屼絾鍐欏叆 `startup_warnings`锛夛紱
- 鍚姩缁撴灉 JSON 鏂板瀛楁锛歚obs_status`锛坥k/degraded锛夈€乣mcp_registry_status`锛坥k/degraded锛夛紱
- `mcp_registry_status` 閫氳繃璋冪敤 `MCPToolRegistry.health_check()` 鍒ゆ柇锛圫QLite 鍙啓鍗?ok锛夈€?
**杈撳嚭缁撴瀯鎵╁睍**锛?```json
{
  "side": "host",
  "status": "started",
  "pid": 12345,
  "obs_status": "ok",
  "mcp_registry_status": "ok",
  "startup_warnings": []
}
```

**閿欒鐮?*锛歚STARTUP_OBS_DEGRADED`銆乣STARTUP_MCP_DEGRADED`

**楠屾敹娴嬭瘯缂栧彿**锛歍-RT-01锛堝惎鍔ㄨ剼鏈泦鎴愶細OBS/MCP 瀛愮郴缁熷仴搴风姸鎬佸彲瑙傛祴锛?
---

### 18.2 绔埌绔?Smoke Test锛圱ASK-0108锛?
#### 18.2.1 鍐峰惎鍔ㄧ鍒扮鏈€灏忛棴鐜紙FR-CORE-004锛?
**鐩爣**锛氬湪鏃犲閮ㄤ緷璧栵紙鏃?API Key銆佹棤 Neo4j銆佹棤 Chroma锛夌殑鎯呭喌涓嬶紝楠岃瘉绯荤粺鑳藉畬鎴?杈撳叆鈫掗檷绾у彊浜嬧啋瀹¤璁板綍鈫掔ǔ瀹氳緭鍑?鐨勬渶灏忛棴鐜€?
**娴嬭瘯鍦烘櫙**锛圱-E2E-01锛夛細
1. 鍚姩 `host_brain`锛堜粎 SQLite锛屾棤澶栭儴鏈嶅姟锛夛紱
2. 鍙戦€?`POST /chat`锛堟櫘閫氬璇濓紝鏃犳墽琛?intent锛夛紱
3. 鏂█锛?   - 鍝嶅簲涓哄悎娉?JSON锛宍quality.stage` 鍖呭惈闄嶇骇鏍囪锛?   - `reason_code` 涓嶄负绌猴紙鑻ユ湁鎷掔粷锛夛紱
   - SQLite 涓瓨鍦ㄥ搴?`request_id` 鐨?`intent_log` 涓?`governor_log` 璁板綍锛?   - 鍝嶅簲涓嶅惈浼墽琛屽鍛婏紙`FALSE_EXECUTION_CLAIM` 妫€鏌ワ級銆?
**娴嬭瘯鍦烘櫙**锛圱-E2E-02锛夛細鎵ц閾捐矾 Smoke锛堟湁 approval_token锛夛細
1. 鍙戦€佹墽琛?intent锛坄action=desktop_note`锛宍risk_level=2`锛夛紱
2. 鏂█锛欸overnor APPROVE 鈫?approval_token 绛惧彂 鈫?skill_exec_log 鍐欏叆 鈫?manifest_ref 瀛樺湪锛?3. 鍙戦€?undo锛堟惡甯?undo_token锛夆啋 鏂█鏂囦欢鎭㈠ + vault_gc_log 涓嶅垹鏈夋晥 undo 绐楀彛鏉愭枡銆?
**娴嬭瘯鍦烘櫙**锛圱-E2E-03锛夛細鏁呴殰娉ㄥ叆鐭╅樀锛圢FR-REL-001锛夛細
- 娉ㄥ叆 Neo4j 涓嶅彲鐢?鈫?杈撳嚭缁撴瀯绋冲畾锛宍reason_code=DEPENDENCY_UNAVAILABLE`锛?- 娉ㄥ叆 Chroma 涓嶅彲鐢?鈫?杈撳嚭缁撴瀯绋冲畾锛宔vidence pack 闄嶇骇浣嗗瓧娈靛瓨鍦紱
- 娉ㄥ叆 Cloud LLM 瓒呮椂 鈫?杈撳嚭鏈€灏忔ā鏉匡紝涓嶄骇鐢熸墽琛?intent锛?- 娉ㄥ叆 Edge RPC 涓嶅彲杈?鈫?璇煶閾捐矾闄嶇骇锛宍ASR_UNAVAILABLE` 鍙粺璁°€?
**楠屾敹娴嬭瘯缂栧彿**锛歍-E2E-01~03

---

### 18.3 鎬ц兘鍩虹嚎閲囬泦锛圱ASK-0109锛?
#### 18.3.1 p50/p95 鍝嶅簲鏃跺欢鍩虹嚎锛圢FR-PERF-001锛?
**閲囬泦鏂瑰紡**锛氬湪 `host_brain/obs/metrics.py` 鐨?`MetricsCollector` 涓紝浠?`retrieval_event_log` 鑱氬悎妫€绱㈤摼璺椂寤讹紱浠?`governor_log` 鑱氬悎瑁佸喅鏃跺欢锛涗粠 `skill_exec_log` 鑱氬悎鎵ц鏃跺欢銆?
**鍩虹嚎鎸囨爣**锛堟湰鍦板熀鍑嗘満鍣紝鏃犲閮ㄤ緷璧栵級锛?
| 閾捐矾娈?| p50 鐩爣 | p95 鐩爣 |
|--------|---------|---------|
| 妫€绱㈤摼璺紙embedding + rerank + fusion锛?| 鈮?200ms | 鈮?500ms |
| Governor 瑁佸喅锛堝惈 rule_trace锛?| 鈮?20ms | 鈮?50ms |
| Skill 鎵ц锛坉esktop_note锛屾湰鍦版枃浠讹級 | 鈮?100ms | 鈮?300ms |
| 瀹屾暣瀵硅瘽閾捐矾锛堟棤 LLM锛岄檷绾фā寮忥級 | 鈮?500ms | 鈮?1500ms |

**杈撳嚭**锛歚/obs/metrics` 鍝嶅簲涓柊澧?`latency_baseline` 瀛楁锛?```json
{
  "latency_baseline": {
    "retrieval_p50_ms": 180,
    "retrieval_p95_ms": 420,
    "governor_p50_ms": 12,
    "governor_p95_ms": 38,
    "skill_exec_p50_ms": 85,
    "skill_exec_p95_ms": 210,
    "sampled_at": "2026-03-03T12:00:00Z",
    "sample_count": 100
  }
}
```

**楠屾敹娴嬭瘯缂栧彿**锛歍-PERF-01锛堝熀绾垮瓧娈电粨鏋勭ǔ瀹?+ 閲囨牱璁℃暟 > 0 + 鏃跺欢鍊煎悎鐞嗚寖鍥达級

---

### 18.4 鍙戝竷娓呭崟涓庣増鏈攣瀹氾紙TASK-0110锛?
#### 18.4.1 M8 鍙戝竷娓呭崟锛坮elease manifest锛?
**鐩爣**锛氬湪 `host_brain/release/release_manifest.py` 鍩虹涓婏紝鐢熸垚 M8 鍙戝竷娓呭崟锛岄攣瀹氾細
- 褰撳墠 git commit hash锛?- 鎵€鏈?migration 鏂囦欢鐨?hash锛圫QLite + Neo4j锛夛紱
- 娴嬭瘯閫氳繃鏁帮紙914 + M8 鏂板锛夛紱
- 鎬ц兘鍩虹嚎蹇収锛堟潵鑷?T-PERF-01锛夛紱
- 鏈嶅姟娉ㄥ唽琛ㄥ揩鐓э紙鎵€鏈?required=true 鏈嶅姟鐨?status锛夈€?
**杈撳嚭鏂囦欢**锛歚release/manifests/m8_release.json`

**楠屾敹娴嬭瘯缂栧彿**锛歍-REL-01锛堝彂甯冩竻鍗曞瓧娈靛畬鏁存€?+ hash 鍙獙璇侊級

---

### 18.5 M8 鏂板閿欒鐮佹眹鎬?
- `STARTUP_OBS_DEGRADED`锛氬惎鍔ㄦ椂 OBS 瀛愮郴缁熶笉鍙敤
- `STARTUP_MCP_DEGRADED`锛氬惎鍔ㄦ椂 MCP 娉ㄥ唽琛ㄤ笉鍙敤
- `E2E_SMOKE_FAILED`锛氱鍒扮 Smoke 娴嬭瘯澶辫触锛堜粎鐢ㄤ簬 CI 鎶ュ憡锛?- `PERF_BASELINE_STALE`锛氭€ц兘鍩虹嚎閲囨牱鏁版嵁杩囨湡锛堣秴杩?24h 鏈洿鏂帮級


---

## 搂19 M9 World Canon 闂幆 + 鏃ヨ Evolution 鍏宠仈

> 瀵瑰簲 PRD 搂4.12锛團R-WCANON-001~003锛夈€伮?.23锛團R-DIARY-002锛夈€?> 鐩爣锛氳ˉ鍏?PRD 涓敮涓€"宸插畾涔変絾瀹屽叏鏈疄鐜?鐨勫姛鑳藉煙锛圵orld Canon 鐙珛妲戒綅娉ㄥ叆闂幆锛夛紝骞舵墦閫?鏃ヨ鈫掕瀹氭紨鍖?鎻愭閾捐矾銆?
---

### 19.1 World Canon 鐙珛妲戒綅娉ㄥ叆闂幆锛團R-WCANON-001~003锛?
#### 19.1.1 World Canon 鏉＄洰缁撴瀯锛堟枃浠?SSOT锛?
钀界偣锛歚host_brain/canon/world/`锛堟瘡涓潯鐩竴涓?JSON 鏂囦欢锛夈€?
**鏉＄洰鏈€灏忓瓧娈?*锛堣涔夊浐瀹氾級锛?- doc_id锛氬叏灞€鍞竴 ID锛堝 wc-001锛?- version锛氳涔夌増鏈紙濡?1.0.0锛?- title锛氭潯鐩爣棰?- tags锛氭爣绛惧垪琛紙濡?architecture / invariant锛?- body锛氭潯鐩鏂囷紙璁惧畾/鏈/涓嶅彉閲忔弿杩帮級
- evidence_level锛氬浐瀹氫负 CANON_REFERENCE_ONLY
- status锛歛ctive | deprecated | draft
- created_at / updated_at锛欼SO8601

**纭害鏉?*锛?- evidence_level 鍥哄畾涓?CANON_REFERENCE_ONLY锛屼笉寰椾慨鏀癸紱
- body 涓嶅緱鍖呭惈"宸叉墽琛?宸蹭繚瀛?宸茬‘璁?绛夊畬鎴愭€ц〃杩帮紱
- 浠讳綍鏉＄洰鍙樻洿蹇呴』 bump version 骞跺啓 canon_change_log銆?
#### 19.1.2 World Canon 绱㈠紩灞傦紙SQLite锛?
琛細world_canon_doc锛堣惤鐐癸細host_brain/audit/sqlite_ledger.py 鎴栫嫭绔嬭縼绉绘枃浠讹級銆?
鏈€灏忓瓧娈碉細
- doc_id TEXT PRIMARY KEY
- version TEXT NOT NULL
- hash TEXT NOT NULL锛坰ha256(body + version)锛?- status TEXT NOT NULL锛坅ctive | deprecated | draft锛?- source_path TEXT NOT NULL锛堢浉瀵逛簬 host_brain/canon/world/ 鐨勮矾寰勶級
- updated_ts INTEGER NOT NULL锛圲nix ms锛?
**鍚屾濂戠害**锛坈anon_sync.py锛夛細
- 鎵弿 host_brain/canon/world/ 鈫?璁＄畻 hash 鈫?涓庣储寮曞姣?鈫?宸紓鍐?canon_change_log锛坈anon_kind=world锛夛紱
- 鍚屾蹇呴』骞傜瓑锛堢浉鍚?hash 涓嶉噸澶嶅啓 change_log锛夛紱
- 鍚屾澶辫触涓嶅緱瀵艰嚧绯荤粺宕╂簝锛屽繀椤婚檷绾у苟璁板綍 CANON_SYNC_FAILED銆?
#### 19.1.3 娉ㄥ叆鍒嗘Ы闅旂锛坈ontext_pack.authoritative.world_canon锛?
**娉ㄥ叆鍙ｅ緞**锛坈ontext_pack.py锛夛細
- World Canon 鏉＄洰鍙兘杩涘叆 prompt_context.authoritative.world_canon锛?- 涓嶅緱涓?style_pack銆乺etrieval.episodic銆乺ecent_events 娣锋Ы锛?- 姣忎釜娉ㄥ叆鏉＄洰蹇呴』鎼哄甫 evidence_level=CANON_REFERENCE_ONLY锛?- 娉ㄥ叆鏁伴噺涓婇檺锛歍op-K锛堥粯璁?5锛屽彲閰嶇疆锛夈€?
**QA 鑱斿姩**锛坬a.py锛夛細
- 鍙欎簨杈撳嚭涓嫢鍑虹幇"World Canon 鍐呭 + 瀹屾垚鎬ц〃杩?缁勫悎锛屽繀椤绘嫤鎴苟璁板綍 CANON_AS_FACT_VIOLATION锛?- 鎷︽埅鍚庡厑璁搁噸鍐?1 娆★紱浠嶅け璐ラ檷绾т负 Host 鏈€灏忔ā鏉裤€?
#### 19.1.4 World Canon 鏇存柊娴佺▼锛圗volution 鎻愭锛?
**鎻愭鍐欏叆**锛坈anon_proposal_log锛夋渶灏忓瓧娈碉細
- proposal_id TEXT PRIMARY KEY
- canon_kind TEXT NOT NULL锛坵orld | style锛?- doc_id TEXT NOT NULL
- proposed_version TEXT NOT NULL
- proposed_body TEXT NOT NULL
- proposer TEXT NOT NULL锛坰cheduler | user | diary_writer锛?- source_intent_id TEXT锛堟潵婧?intent_id锛屽彲绌猴級
- status TEXT NOT NULL锛坧ending | approved | rejected锛?- created_at INTEGER NOT NULL
- reviewed_at INTEGER锛堝彲绌猴級

**瀹℃牳娴佺▼**锛?- 鎻愭鍐欏叆鍚庣姸鎬佷负 pending锛?- 鍙湁 Host 鐧藉悕鍗曟祦绋嬶紙鎴?Master 鏄惧紡纭锛夊彲灏嗙姸鎬佹敼涓?approved锛?- approved 鍚?canon_sync.py 灏嗘柊鐗堟湰鍐欏叆鏂囦欢 SSOT 骞舵洿鏂扮储寮曪紱
- 浠讳綍鐘舵€佸彉鏇村啓 canon_change_log锛堝惈鍙樻洿鍓嶅悗 hash锛夈€?
#### 19.1.5 鍥炲綊娴嬭瘯缂栧彿锛堟柊澧烇級

- T-WCanon-04锛堢储寮曞悓姝ュ箓绛夋€э級锛氱浉鍚?hash 鐨勬潯鐩噸澶嶅悓姝ヤ笉浜х敓閲嶅 canon_change_log 鏉＄洰锛涘悓姝ュけ璐ユ椂 CANON_SYNC_FAILED 鍙粺璁°€?- T-WCanon-05锛堟敞鍏ュ垎妲介殧绂伙級锛歐orld Canon 鏉＄洰鍙嚭鐜板湪 authoritative.world_canon 妲戒綅锛涙贩妲芥敞鍏ヨ contract 娴嬭瘯鎷︽埅銆?- T-WCanon-06锛圦A 鎷︽埅 Canon-as-fact锛夛細鍙欎簨鍚?World Canon 鍐呭 + 瀹屾垚鎬ц〃杩?鏃?QA 鎷︽埅骞惰褰?CANON_AS_FACT_VIOLATION锛涢噸鍐欏悗浠嶅け璐ュ垯闄嶇骇銆?- T-WCanon-07锛圗volution 鎻愭鍐欏叆涓庡鏍告祦绋嬶級锛氭彁妗堝啓鍏?canon_proposal_log锛坰tatus=pending锛夛紱瀹℃牳鍚庣姸鎬佸彉鏇村彲鍥炴斁锛沜anon_change_log 鍚彉鏇村墠鍚?hash銆?
#### 19.1.6 鏂板閿欒鐮?
- CANON_SYNC_FAILED锛歐orld Canon 鏂囦欢鎵弿/鍚屾澶辫触
- WCANON_SLOT_VIOLATION锛歐orld Canon 娣锋Ы娉ㄥ叆锛堟敞鍏ュ埌闈?authoritative.world_canon 妲戒綅锛?- WCANON_PROPOSAL_INVALID锛氭彁妗堝瓧娈典笉鍚堣锛堢己 doc_id/body/proposer 绛夛級

---

### 19.2 鏃ヨ Evolution 鎻愭鍏宠仈锛團R-DIARY-002锛?
#### 19.2.1 瑙﹀彂鏉′欢

- 鏃ヨ鐢熸垚锛坉iary_writer.py锛夊畬鎴愬悗锛孡LM 杈撳嚭鍙€夊寘鍚?drift_suggestion 瀛楁锛?- drift_suggestion 缁撴瀯锛歿doc_id, suggested_body, reason锛堜笉瓒呰繃 200 瀛楋級}锛?- 鑻?drift_suggestion 瀛樺湪涓斿瓧娈靛悎瑙勶細鐢熸垚 canon_proposal锛坧roposer=diary_writer锛宻ource_intent_id=鏃ヨ intent_id锛夊啓鍏?canon_proposal_log锛?- 鑻?drift_suggestion 涓嶅瓨鍦ㄦ垨瀛楁涓嶅悎瑙勶細璺宠繃锛堥粯璁よ矾寰勶級锛屼笉闃诲鏃ヨ鍐欏叆銆?
#### 19.2.2 QA 绾︽潫

- drift_suggestion.suggested_body 蹇呴』缁?QA 鏍￠獙锛堜笉寰楀惈浼墽琛屽彞銆佷笉寰楀惈瀹屾垚鎬ц〃杩帮級锛?- 鏍￠獙澶辫触锛氫涪寮?drift_suggestion锛屾棩璁版甯稿啓鍏ワ紝璁板綍 WCANON_PROPOSAL_INVALID銆?
#### 19.2.3 瀹¤涓茶仈

- canon_proposal_log.source_intent_id 鎸囧悜鏃ヨ intent_id锛?- 鍙粠鏃ヨ intent_id 杩芥函鍒?canon_proposal_log锛堟鍚戯級锛?- 鍙粠 canon_proposal_log.proposal_id 杩芥函鍒版棩璁?intent_id锛堝弽鍚戯級銆?
#### 19.2.4 鍥炲綊娴嬭瘯缂栧彿锛堟柊澧烇級

- T-Diary-03锛圗volution 鎻愭鍐欏叆锛夛細鏃ヨ鍚?drift_suggestion 鏃讹紝canon_proposal_log 鍐欏叆鎴愬姛锛宻ource_intent_id 鍙拷婧埌鏃ヨ intent_id銆?- T-Diary-04锛圗volution 鎻愭 QA 鎷︽埅锛夛細drift_suggestion.suggested_body 鍚吉鎵ц鍙ユ椂锛屾彁妗堣涓㈠純锛屾棩璁版甯稿啓鍏ワ紝WCANON_PROPOSAL_INVALID 鍙粺璁°€?
#### 19.2.5 鏂板閿欒鐮?
- DIARY_EVOLUTION_PROPOSAL_SKIPPED锛氭棩璁版棤 drift_suggestion 鎴栧瓧娈典笉鍚堣锛屾彁妗堣烦杩囷紙闈為敊璇紝浠呭彲瑙傛祴锛?
---

### 19.3 M9 鏂板閿欒鐮佹眹鎬?
| 閿欒鐮?| 瑙﹀彂鍦烘櫙 |
|--------|---------|
| CANON_SYNC_FAILED | World Canon 鏂囦欢鎵弿/鍚屾澶辫触 |
| WCANON_SLOT_VIOLATION | World Canon 娣锋Ы娉ㄥ叆 |
| WCANON_PROPOSAL_INVALID | 鎻愭瀛楁涓嶅悎瑙勶紙鍚?QA 鎷︽埅锛?|
| DIARY_EVOLUTION_PROPOSAL_SKIPPED | 鏃ヨ鏃犳湁鏁?drift_suggestion锛堝彲瑙傛祴锛岄潪閿欒锛?|

---

### 19.4 M9 鍥炲綊娴嬭瘯缂栧彿姹囨€?
| 缂栧彿 | 瑕嗙洊闇€姹?| 鏂█鍙ｅ緞 |
|------|---------|---------|
| T-WCanon-04 | FR-WCANON-002 | 绱㈠紩鍚屾骞傜瓑鎬?+ CANON_SYNC_FAILED 鍙粺璁?|
| T-WCanon-05 | FR-WCANON-003 | 娉ㄥ叆鍒嗘Ы闅旂锛坈ontract 娴嬭瘯锛?|
| T-WCanon-06 | FR-WCANON-001 + FR-CANON-001 | QA 鎷︽埅 Canon-as-fact + 閲嶅啓/闄嶇骇 |
| T-WCanon-07 | FR-WCANON-002 | Evolution 鎻愭鍐欏叆/瀹℃牳/鍙樻洿 hash 鍙洖鏀?|
| T-Diary-03 | FR-DIARY-002 | 鎻愭鍐欏叆 + source_intent_id 涓茶仈 |
| T-Diary-04 | FR-DIARY-002 | 鎻愭 QA 鎷︽埅 + 鏃ヨ姝ｅ父鍐欏叆 |


---

## 搂19 M9 World Canon 闂幆 + 鏃ヨ Evolution 鍏宠仈

> 瀵瑰簲 PRD 搂4.12锛團R-WCANON-001~003锛夈€伮?.23锛團R-DIARY-002锛夈€?> 鐩爣锛氳ˉ鍏?PRD 涓敮涓€"宸插畾涔変絾瀹屽叏鏈疄鐜?鐨勫姛鑳藉煙锛圵orld Canon 鐙珛妲戒綅娉ㄥ叆闂幆锛夛紝骞舵墦閫?鏃ヨ鈫掕瀹氭紨鍖?鎻愭閾捐矾銆?
---

### 19.1 World Canon 鐙珛妲戒綅娉ㄥ叆闂幆锛團R-WCANON-001~003锛?
#### 19.1.1 World Canon 鏉＄洰缁撴瀯锛堟枃浠?SSOT锛?
钀界偣锛歚host_brain/canon/world/`锛堟瘡涓潯鐩竴涓?JSON 鏂囦欢锛夈€?
**鏉＄洰鏈€灏忓瓧娈?*锛堣涔夊浐瀹氾級锛?- doc_id锛氬叏灞€鍞竴 ID锛堝 wc-001锛?- version锛氳涔夌増鏈紙濡?1.0.0锛?- title锛氭潯鐩爣棰?- tags锛氭爣绛惧垪琛紙濡?architecture / invariant锛?- body锛氭潯鐩鏂囷紙璁惧畾/鏈/涓嶅彉閲忔弿杩帮級
- evidence_level锛氬浐瀹氫负 CANON_REFERENCE_ONLY
- status锛歛ctive | deprecated | draft
- created_at / updated_at锛欼SO8601

**纭害鏉?*锛?- evidence_level 鍥哄畾涓?CANON_REFERENCE_ONLY锛屼笉寰椾慨鏀癸紱
- body 涓嶅緱鍖呭惈"宸叉墽琛?宸蹭繚瀛?宸茬‘璁?绛夊畬鎴愭€ц〃杩帮紱
- 浠讳綍鏉＄洰鍙樻洿蹇呴』 bump version 骞跺啓 canon_change_log銆?
#### 19.1.2 World Canon 绱㈠紩灞傦紙SQLite锛?
琛細world_canon_doc锛堣惤鐐癸細host_brain/audit/sqlite_ledger.py 鎴栫嫭绔嬭縼绉绘枃浠讹級銆?
鏈€灏忓瓧娈碉細
- doc_id TEXT PRIMARY KEY
- version TEXT NOT NULL
- hash TEXT NOT NULL锛坰ha256(body + version)锛?- status TEXT NOT NULL锛坅ctive | deprecated | draft锛?- source_path TEXT NOT NULL锛堢浉瀵逛簬 host_brain/canon/world/ 鐨勮矾寰勶級
- updated_ts INTEGER NOT NULL锛圲nix ms锛?
**鍚屾濂戠害**锛坈anon_sync.py锛夛細
- 鎵弿 host_brain/canon/world/ 鈫?璁＄畻 hash 鈫?涓庣储寮曞姣?鈫?宸紓鍐?canon_change_log锛坈anon_kind=world锛夛紱
- 鍚屾蹇呴』骞傜瓑锛堢浉鍚?hash 涓嶉噸澶嶅啓 change_log锛夛紱
- 鍚屾澶辫触涓嶅緱瀵艰嚧绯荤粺宕╂簝锛屽繀椤婚檷绾у苟璁板綍 CANON_SYNC_FAILED銆?
#### 19.1.3 娉ㄥ叆鍒嗘Ы闅旂锛坈ontext_pack.authoritative.world_canon锛?
**娉ㄥ叆鍙ｅ緞**锛坈ontext_pack.py锛夛細
- World Canon 鏉＄洰鍙兘杩涘叆 prompt_context.authoritative.world_canon锛?- 涓嶅緱涓?style_pack銆乺etrieval.episodic銆乺ecent_events 娣锋Ы锛?- 姣忎釜娉ㄥ叆鏉＄洰蹇呴』鎼哄甫 evidence_level=CANON_REFERENCE_ONLY锛?- 娉ㄥ叆鏁伴噺涓婇檺锛歍op-K锛堥粯璁?5锛屽彲閰嶇疆锛夈€?
**QA 鑱斿姩**锛坬a.py锛夛細
- 鍙欎簨杈撳嚭涓嫢鍑虹幇"World Canon 鍐呭 + 瀹屾垚鎬ц〃杩?缁勫悎锛屽繀椤绘嫤鎴苟璁板綍 CANON_AS_FACT_VIOLATION锛?- 鎷︽埅鍚庡厑璁搁噸鍐?1 娆★紱浠嶅け璐ラ檷绾т负 Host 鏈€灏忔ā鏉裤€?
#### 19.1.4 World Canon 鏇存柊娴佺▼锛圗volution 鎻愭锛?
**鎻愭鍐欏叆**锛坈anon_proposal_log锛夋渶灏忓瓧娈碉細
- proposal_id TEXT PRIMARY KEY
- canon_kind TEXT NOT NULL锛坵orld | style锛?- doc_id TEXT NOT NULL
- proposed_version TEXT NOT NULL
- proposed_body TEXT NOT NULL
- proposer TEXT NOT NULL锛坰cheduler | user | diary_writer锛?- source_intent_id TEXT锛堟潵婧?intent_id锛屽彲绌猴級
- status TEXT NOT NULL锛坧ending | approved | rejected锛?- created_at INTEGER NOT NULL
- reviewed_at INTEGER锛堝彲绌猴級

**瀹℃牳娴佺▼**锛?- 鎻愭鍐欏叆鍚庣姸鎬佷负 pending锛?- 鍙湁 Host 鐧藉悕鍗曟祦绋嬶紙鎴?Master 鏄惧紡纭锛夊彲灏嗙姸鎬佹敼涓?approved锛?- approved 鍚?canon_sync.py 灏嗘柊鐗堟湰鍐欏叆鏂囦欢 SSOT 骞舵洿鏂扮储寮曪紱
- 浠讳綍鐘舵€佸彉鏇村啓 canon_change_log锛堝惈鍙樻洿鍓嶅悗 hash锛夈€?
#### 19.1.5 鍥炲綊娴嬭瘯缂栧彿锛堟柊澧烇級

- T-WCanon-04锛堢储寮曞悓姝ュ箓绛夋€э級锛氱浉鍚?hash 鐨勬潯鐩噸澶嶅悓姝ヤ笉浜х敓閲嶅 canon_change_log 鏉＄洰锛涘悓姝ュけ璐ユ椂 CANON_SYNC_FAILED 鍙粺璁°€?- T-WCanon-05锛堟敞鍏ュ垎妲介殧绂伙級锛歐orld Canon 鏉＄洰鍙嚭鐜板湪 authoritative.world_canon 妲戒綅锛涙贩妲芥敞鍏ヨ contract 娴嬭瘯鎷︽埅銆?- T-WCanon-06锛圦A 鎷︽埅 Canon-as-fact锛夛細鍙欎簨鍚?World Canon 鍐呭 + 瀹屾垚鎬ц〃杩?鏃?QA 鎷︽埅骞惰褰?CANON_AS_FACT_VIOLATION锛涢噸鍐欏悗浠嶅け璐ュ垯闄嶇骇銆?- T-WCanon-07锛圗volution 鎻愭鍐欏叆涓庡鏍告祦绋嬶級锛氭彁妗堝啓鍏?canon_proposal_log锛坰tatus=pending锛夛紱瀹℃牳鍚庣姸鎬佸彉鏇村彲鍥炴斁锛沜anon_change_log 鍚彉鏇村墠鍚?hash銆?
#### 19.1.6 鏂板閿欒鐮?
- CANON_SYNC_FAILED锛歐orld Canon 鏂囦欢鎵弿/鍚屾澶辫触
- WCANON_SLOT_VIOLATION锛歐orld Canon 娣锋Ы娉ㄥ叆锛堟敞鍏ュ埌闈?authoritative.world_canon 妲戒綅锛?- WCANON_PROPOSAL_INVALID锛氭彁妗堝瓧娈典笉鍚堣锛堢己 doc_id/body/proposer 绛夛級

---

### 19.2 鏃ヨ Evolution 鎻愭鍏宠仈锛團R-DIARY-002锛?
#### 19.2.1 瑙﹀彂鏉′欢

- 鏃ヨ鐢熸垚锛坉iary_writer.py锛夊畬鎴愬悗锛孡LM 杈撳嚭鍙€夊寘鍚?drift_suggestion 瀛楁锛?- drift_suggestion 缁撴瀯锛歿doc_id, suggested_body, reason锛堜笉瓒呰繃 200 瀛楋級}锛?- 鑻?drift_suggestion 瀛樺湪涓斿瓧娈靛悎瑙勶細鐢熸垚 canon_proposal锛坧roposer=diary_writer锛宻ource_intent_id=鏃ヨ intent_id锛夊啓鍏?canon_proposal_log锛?- 鑻?drift_suggestion 涓嶅瓨鍦ㄦ垨瀛楁涓嶅悎瑙勶細璺宠繃锛堥粯璁よ矾寰勶級锛屼笉闃诲鏃ヨ鍐欏叆銆?
#### 19.2.2 QA 绾︽潫

- drift_suggestion.suggested_body 蹇呴』缁?QA 鏍￠獙锛堜笉寰楀惈浼墽琛屽彞銆佷笉寰楀惈瀹屾垚鎬ц〃杩帮級锛?- 鏍￠獙澶辫触锛氫涪寮?drift_suggestion锛屾棩璁版甯稿啓鍏ワ紝璁板綍 WCANON_PROPOSAL_INVALID銆?
#### 19.2.3 瀹¤涓茶仈

- canon_proposal_log.source_intent_id 鎸囧悜鏃ヨ intent_id锛?- 鍙粠鏃ヨ intent_id 杩芥函鍒?canon_proposal_log锛堟鍚戯級锛?- 鍙粠 canon_proposal_log.proposal_id 杩芥函鍒版棩璁?intent_id锛堝弽鍚戯級銆?
#### 19.2.4 鍥炲綊娴嬭瘯缂栧彿锛堟柊澧烇級

- T-Diary-03锛圗volution 鎻愭鍐欏叆锛夛細鏃ヨ鍚?drift_suggestion 鏃讹紝canon_proposal_log 鍐欏叆鎴愬姛锛宻ource_intent_id 鍙拷婧埌鏃ヨ intent_id銆?- T-Diary-04锛圗volution 鎻愭 QA 鎷︽埅锛夛細drift_suggestion.suggested_body 鍚吉鎵ц鍙ユ椂锛屾彁妗堣涓㈠純锛屾棩璁版甯稿啓鍏ワ紝WCANON_PROPOSAL_INVALID 鍙粺璁°€?
#### 19.2.5 鏂板閿欒鐮?
- DIARY_EVOLUTION_PROPOSAL_SKIPPED锛氭棩璁版棤 drift_suggestion 鎴栧瓧娈典笉鍚堣锛屾彁妗堣烦杩囷紙闈為敊璇紝浠呭彲瑙傛祴锛?
---

### 19.3 M9 鏂板閿欒鐮佹眹鎬?
| 閿欒鐮?| 瑙﹀彂鍦烘櫙 |
|--------|---------|
| CANON_SYNC_FAILED | World Canon 鏂囦欢鎵弿/鍚屾澶辫触 |
| WCANON_SLOT_VIOLATION | World Canon 娣锋Ы娉ㄥ叆 |
| WCANON_PROPOSAL_INVALID | 鎻愭瀛楁涓嶅悎瑙勶紙鍚?QA 鎷︽埅锛?|
| DIARY_EVOLUTION_PROPOSAL_SKIPPED | 鏃ヨ鏃犳湁鏁?drift_suggestion锛堝彲瑙傛祴锛岄潪閿欒锛?|

---

### 19.4 M9 鍥炲綊娴嬭瘯缂栧彿姹囨€?
| 缂栧彿 | 瑕嗙洊闇€姹?| 鏂█鍙ｅ緞 |
|------|---------|---------|
| T-WCanon-04 | FR-WCANON-002 | 绱㈠紩鍚屾骞傜瓑鎬?+ CANON_SYNC_FAILED 鍙粺璁?|
| T-WCanon-05 | FR-WCANON-003 | 娉ㄥ叆鍒嗘Ы闅旂锛坈ontract 娴嬭瘯锛?|
| T-WCanon-06 | FR-WCANON-001 + FR-CANON-001 | QA 鎷︽埅 Canon-as-fact + 閲嶅啓/闄嶇骇 |
| T-WCanon-07 | FR-WCANON-002 | Evolution 鎻愭鍐欏叆/瀹℃牳/鍙樻洿 hash 鍙洖鏀?|
| T-Diary-03 | FR-DIARY-002 | 鎻愭鍐欏叆 + source_intent_id 涓茶仈 |
| T-Diary-04 | FR-DIARY-002 | 鎻愭 QA 鎷︽埅 + 鏃ヨ姝ｅ父鍐欏叆 |

---

## 搂20 M10-A Canon 娌荤悊 API锛團R-WCANON-002 鎵╁睍锛?
> 瀵瑰簲 PRD M10-A锛圥0锛夛細Canon 鎻愭瀹℃牳 REST API銆?> 灏?M9 宸茶惤鍦扮殑 `submit_proposal()` / `approve_proposal_with_content()` 鏆撮湶涓哄彲鎿嶄綔鐨?REST 绔偣锛?> 鏀寔 Master 閫氳繃 HTTP 鎺ュ彛鏌ヨ銆佹彁浜ゃ€佸鏍搞€佹嫆缁?World Canon Evolution 鎻愭銆?
---

### 20.1 璁捐鍘熷垯

1. **Host 涓绘潈涓嶅彉**锛氭墍鏈夊啓鎿嶄綔浠嶇粡 API-level 鏉冮檺闂ㄦ帶锛坄proposer_node_id` 寮哄埗鏍囨敞鏉ユ簮锛夛紝涓嶅紩鍏ユ梺璺€?2. **瀹¤涓茶仈**锛氭瘡涓?API 璋冪敤浜х敓 `request_id`锛屽啓鎿嶄綔蹇呴』灏?`request_id` 鍐欏叆 `canon_proposal_log.proposer_node_id`锛堟牸寮忥細`api.canon_proposals::{request_id}`锛夈€?3. **骞傜瓑鎬?*锛歚POST /canon/proposals`锛堟彁浜ゆ彁妗堬級瀵圭浉鍚?`proposal_id` 骞傜瓑锛堥噸澶嶆彁浜よ繑鍥?409 + WCANON_PROPOSAL_DUPLICATE锛屼笉閲嶅鍐欏叆锛夈€?4. **缁撴瀯绋冲畾**锛氭垚鍔?鎷掔粷/闄嶇骇鍧囪繑鍥炵粺涓€ JSON 缁撴瀯锛坄CanonProposalResponse`锛夛紝鍚?`reason_code`銆?5. **鍙绔偣鏃犲壇浣滅敤**锛歚GET /canon/proposals` / `GET /canon/proposals/{proposal_id}` / `GET /canon/documents` 涓嶄慨鏀逛换浣曠姸鎬併€?
---

### 20.2 REST 绔偣瑙勬牸

#### 20.2.1 绔偣娓呭崟

| 鏂规硶 | 璺緞 | 鎻忚堪 | 鍓綔鐢?|
|------|------|------|--------|
| GET | `/canon/proposals` | 鍒楀嚭鎻愭锛堝垎椤碉紝鍙寜 status 杩囨护锛?| 鏃?|
| POST | `/canon/proposals` | 鎻愪氦鏂版彁妗堬紙status=pending锛?| 鍐?canon_proposal_log |
| GET | `/canon/proposals/{proposal_id}` | 鏌ヨ鍗曚釜鎻愭璇︽儏 | 鏃?|
| POST | `/canon/proposals/{proposal_id}/approve` | 瀹℃牳閫氳繃鎻愭锛堣Е鍙?SSOT 鍐欏叆锛?| 鍐?SSOT + canon_change_log |
| POST | `/canon/proposals/{proposal_id}/reject` | 鎷掔粷鎻愭锛坰tatus=rejected锛屼笉鍐?SSOT锛?| 鍐?canon_proposal_log锛坰tatus 鏇存柊锛?|
| GET | `/canon/documents` | 鍒楀嚭褰撳墠 World Canon 鏂囨。锛圫SOT 蹇収锛?| 鏃?|
| GET | `/canon/documents/{doc_id}` | 鏌ヨ鍗曚釜 World Canon 鏂囨。 | 鏃?|

#### 20.2.2 璇锋眰/鍝嶅簲 Schema

**CanonProposalSubmitRequest**锛圥OST /canon/proposals锛夛細
```json
{
  "proposal_id": "string锛堣皟鐢ㄦ柟鐢熸垚锛屽敮涓€锛?,
  "target_doc_id": "string锛堢洰鏍?World Canon 鏂囨。 ID锛?,
  "proposed_title": "string",
  "proposed_tags": ["string"],
  "proposed_body": "string锛堜笉寰楀惈瀹屾垚鎬ц〃杩帮級",
  "proposer_node_id": "string锛堝彲閫夛紝榛樿 api.canon_proposals锛?,
  "summary": "string锛堝彲閫夛紝涓嶈秴杩?200 瀛楋級"
}
```

**CanonProposalApproveRequest**锛圥OST /canon/proposals/{id}/approve锛夛細
```json
{
  "proposed_title": "string",
  "proposed_tags": ["string"],
  "proposed_body": "string",
  "approver_node_id": "string锛堝彲閫夛紝榛樿 api.canon_proposals.approve锛?
}
```

**CanonProposalRejectRequest**锛圥OST /canon/proposals/{id}/reject锛夛細
```json
{
  "reason": "string锛堟嫆缁濆師鍥狅紝涓嶈秴杩?200 瀛楋級",
  "rejector_node_id": "string锛堝彲閫夛級"
}
```

**CanonProposalResponse**锛堢粺涓€鍝嶅簲缁撴瀯锛夛細
```json
{
  "proposal_id": "string",
  "target_doc_id": "string",
  "status": "pending | approved | rejected | APPLIED",
  "proposer_node_id": "string",
  "summary": "string",
  "proposed_hash": "string",
  "base_version": "int",
  "proposed_version": "int",
  "ts_ms": "int",
  "request_id": "string",
  "reason_code": "string | null",
  "timestamp": "int"
}
```

**CanonDocumentResponse**锛圵orld Canon 鏂囨。鍝嶅簲锛夛細
```json
{
  "doc_id": "string",
  "version": "int",
  "hash": "string",
  "status": "string",
  "title": "string",
  "tags": ["string"],
  "body": "string",
  "updated_by": "string",
  "ts_ms": "int",
  "source_path": "string",
  "request_id": "string",
  "timestamp": "int"
}
```

**CanonProposalListResponse**锛堝垪琛ㄥ搷搴旓級锛?```json
{
  "items": ["CanonProposalResponse"],
  "total": "int",
  "page": "int",
  "page_size": "int",
  "request_id": "string",
  "timestamp": "int"
}
```

#### 20.2.3 閿欒鐮佹槧灏?
| HTTP 鐘舵€佺爜 | reason_code | 瑙﹀彂鍦烘櫙 |
|------------|-------------|---------|
| 400 | WCANON_PROPOSAL_INVALID | 璇锋眰瀛楁涓嶅悎瑙勶紙缂哄繀濉瓧娈碉紝鎴?body 鍚畬鎴愭€ц〃杩帮級 |
| 404 | WCANON_PROPOSAL_NOT_FOUND | 鎻愭 ID 涓嶅瓨鍦?|
| 404 | WCANON_DOC_NOT_FOUND | World Canon 鏂囨。 ID 涓嶅瓨鍦?|
| 409 | WCANON_PROPOSAL_DUPLICATE | 鐩稿悓 proposal_id 宸插瓨鍦紙骞傜瓑杩斿洖宸叉湁璁板綍锛?|
| 422 | WCANON_PROPOSAL_STATUS_INVALID | 鎻愭 status 涓嶆槸 pending锛堜笉鍙鏍?鎷掔粷锛?|
| 500 | CANON_SYNC_FAILED | SSOT 鏂囦欢鍐欏叆澶辫触 |

---

### 20.3 璺敱娉ㄥ唽鏂瑰紡

- 鏂板缓 `host_brain/app/canon_routes.py`锛屼娇鐢?`fastapi.APIRouter(prefix="/canon", tags=["canon"])`锛?- 鍦?`fastapi_app.py` 涓?`app.include_router(canon_router)` 娉ㄥ唽锛堣矾鐢辫嚜甯?`/canon` 鍓嶇紑锛夛紱
- 璺敱渚濊禆 `WorldCanonStore`锛堥€氳繃 `get_canon_store()` 渚濊禆娉ㄥ叆锛屽欢杩熷垵濮嬪寲锛宍world_root` 浠庣幆澧冨彉閲?`Natsume_WORLD_CANON_ROOT` 璇诲彇锛岄粯璁?`host_brain/canon/world`锛夛紱
- 璺敱渚濊禆 `SQLiteLedger`锛堝鐢?`get_ledger()`锛夈€?
---

### 20.4 瀹¤涓茶仈瑕佹眰

- 姣忎釜鍐欐搷浣滐紙POST /canon/proposals銆乤pprove銆乺eject锛夊繀椤伙細
  - 鐢熸垚 `request_id`锛坄new_request_id()`锛夛紱
  - 灏?`request_id` 鍐欏叆 `canon_proposal_log.proposer_node_id`锛堟牸寮忥細`api.canon_proposals::{request_id}`锛夛紱
  - approve 鏃跺啓 `canon_change_log`锛堥€氳繃 `upsert_document()` 鍐呴儴瀹屾垚锛夛紱
  - reject 鏃跺啓 `system_event_log`锛坋vent_type=WCANON_PROPOSAL_REJECTED锛屽惈 proposal_id + reason锛夈€?- 鍙鎿嶄綔锛圙ET锛変笉鍐欏璁★紝浣嗗繀椤诲湪鍝嶅簲涓惡甯?`request_id`銆?
---

### 20.5 `reject_proposal()` 鏂规硶鎵╁睍锛坈anon_world_store.py锛?
M9 鐨?`WorldCanonStore` 缂哄皯 `reject_proposal()` 鏂规硶锛孧10-A 闇€瑕佽ˉ鍏咃細

```python
def reject_proposal(
    self,
    proposal_id: str,
    *,
    reason: str = "",
    rejector_node_id: str = "api.canon_proposals.reject",
    ts_ms: int | None = None,
) -> dict[str, Any]:
    reject_ts = int(ts_ms if ts_ms is not None else now_ms())
    proposal = self.ledger.get_proposal(proposal_id)
    if proposal is None:
        raise KeyError(f"proposal not found: {proposal_id}")
    current_status = str(proposal.get("status", ""))
    if current_status != "pending":
        raise PermissionError(
            f"proposal {proposal_id!r} cannot be rejected: "
            f"current status is {current_status!r}, expected 'pending'"
        )
    self.ledger.update_proposal_status(proposal_id, new_status="rejected", ts_ms=reject_ts)
    return {**proposal, "status": "rejected", "ts_ms": reject_ts, "reject_reason": reason}
```

---

### 20.6 鍥炲綊娴嬭瘯缂栧彿锛圡10-A 鏂板锛?
| 缂栧彿 | 瑕嗙洊闇€姹?| 鏂█鍙ｅ緞 |
|------|---------|---------|
| T-Canon-API-01 | FR-WCANON-002 | POST /canon/proposals 鎻愪氦鎴愬姛锛宻tatus=pending锛宲roposal_id 鍙煡璇?|
| T-Canon-API-02 | FR-WCANON-002 | POST /canon/proposals 閲嶅 proposal_id 杩斿洖 409 + WCANON_PROPOSAL_DUPLICATE |
| T-Canon-API-03 | FR-WCANON-002 | POST /canon/proposals body 鍚畬鎴愭€ц〃杩拌繑鍥?400 + WCANON_PROPOSAL_INVALID |
| T-Canon-API-04 | FR-WCANON-002 | POST /canon/proposals/{id}/approve 瀹℃牳閫氳繃锛孲SOT 鏂囦欢鏇存柊锛宑anon_change_log 鍙洖鏀?|
| T-Canon-API-05 | FR-WCANON-002 | POST /canon/proposals/{id}/approve 瀵归潪 pending 鎻愭杩斿洖 422 + WCANON_PROPOSAL_STATUS_INVALID |
| T-Canon-API-06 | FR-WCANON-002 | POST /canon/proposals/{id}/reject 鎷掔粷鎴愬姛锛宻tatus=rejected锛孲SOT 涓嶅彉 |
| T-Canon-API-07 | FR-WCANON-002 | GET /canon/proposals 鍒嗛〉鍒楄〃锛宻tatus 杩囨护姝ｇ‘ |
| T-Canon-API-08 | FR-WCANON-001 | GET /canon/documents 杩斿洖褰撳墠 SSOT 蹇収锛? 涓瀛愭潯鐩級 |

---

### 20.7 M10-A 鏂板閿欒鐮佹眹鎬?
| 閿欒鐮?| 瑙﹀彂鍦烘櫙 |
|--------|---------|
| WCANON_PROPOSAL_NOT_FOUND | 鎻愭 ID 涓嶅瓨鍦?|
| WCANON_DOC_NOT_FOUND | World Canon 鏂囨。 ID 涓嶅瓨鍦?|
| WCANON_PROPOSAL_DUPLICATE | 鐩稿悓 proposal_id 宸插瓨鍦?|
| WCANON_PROPOSAL_STATUS_INVALID | 鎻愭 status 涓嶅厑璁稿綋鍓嶆搷浣滐紙闈?pending锛?|

---

## 搂21 瀹氭椂璋冨害鍣紙Scheduler锛夊伐绋嬪绾?
### 21.1 妯″潡杈圭晫

```
host_brain/scheduler/
鈹溾攢鈹€ __init__.py
鈹溾攢鈹€ scheduler_engine.py      # SchedulerEngine锛氫换鍔℃敞鍐?娉ㄩ攢/瑙﹀彂/鍋ュ悍鐘舵€?鈹溾攢鈹€ scheduler_models.py      # ScheduledTask銆乀askHealth銆丼chedulerConfig Schema
鈹溾攢鈹€ scheduler_store.py       # SQLite 鎸佷箙鍖栵紙scheduler_tasks 琛級
鈹斺攢鈹€ scheduler_routes.py      # FastAPI 璺敱锛?scheduler/tasks CRUD + /scheduler/health
```

### 21.2 鏍稿績 Schema

#### ScheduledTask
```python
@dataclass
class ScheduledTask:
    task_id: str                    # UUID锛屽敮涓€鏍囪瘑
    name: str                       # 浠诲姟鍚嶇О锛堝彲璇伙級
    cron_expr: str | None           # cron 琛ㄨ揪寮忥紙濡?"0 23 * * *"锛?    interval_seconds: int | None    # 鍛ㄦ湡瑙﹀彂锛堢锛夛紝涓?cron_expr 浜岄€変竴
    intent_type: str                # 瑙﹀彂鐨?intent type锛坅llowlist 鏍￠獙锛?    intent_action: str              # 瑙﹀彂鐨?intent action
    intent_args: dict               # 瑙﹀彂鏃堕檮甯︾殑 args
    sys_mode_allowlist: list[str]   # 鍏佽瑙﹀彂鐨?sys_mode锛堝 ["NORMAL", "SLEEP"]锛?    priority: str                   # "HIGH" / "NORMAL" / "LOW"
    enabled: bool                   # 鏄惁鍚敤
    created_at: str                 # ISO8601
    updated_at: str                 # ISO8601
    config_version: int             # 閰嶇疆鐗堟湰鍙凤紙姣忔鏇存柊閫掑锛?```

#### TaskHealth
```python
@dataclass
class TaskHealth:
    task_id: str
    status: str                     # "RUNNING" / "PAUSED" / "SUSPENDED" / "DISABLED"
    last_run_ts: str | None         # 涓婃瑙﹀彂鏃堕棿
    next_run_ts: str | None         # 涓嬫棰勮瑙﹀彂鏃堕棿
    last_result: str | None         # "SUCCESS" / "FAILED" / "SKIPPED"
    error_count: int                # 杩炵画澶辫触娆℃暟
    suspended_reason: str | None    # 鏆傚仠鍘熷洜锛堝 "SCHED_TASK_SUSPENDED"锛?```

### 21.3 SQLite 琛細scheduler_tasks

```sql
CREATE TABLE IF NOT EXISTS scheduler_tasks (
    task_id         TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    cron_expr       TEXT,
    interval_seconds INTEGER,
    intent_type     TEXT NOT NULL,
    intent_action   TEXT NOT NULL,
    intent_args     TEXT NOT NULL DEFAULT '{}',   -- JSON
    sys_mode_allowlist TEXT NOT NULL DEFAULT '["NORMAL"]',  -- JSON array
    priority        TEXT NOT NULL DEFAULT 'NORMAL',
    enabled         INTEGER NOT NULL DEFAULT 1,
    status          TEXT NOT NULL DEFAULT 'RUNNING',
    last_run_ts     TEXT,
    next_run_ts     TEXT,
    last_result     TEXT,
    error_count     INTEGER NOT NULL DEFAULT 0,
    suspended_reason TEXT,
    config_version  INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
```

### 21.4 SchedulerEngine 鎺ュ彛濂戠害

```python
class SchedulerEngine:
    def register_task(self, task: ScheduledTask) -> None:
        """娉ㄥ唽浠诲姟锛泃ask_id 閲嶅鍒?raise SCHED_TASK_DUPLICATE"""

    def unregister_task(self, task_id: str) -> None:
        """娉ㄩ攢浠诲姟锛涗笉瀛樺湪鍒?raise SCHED_TASK_NOT_FOUND"""

    def enable_task(self, task_id: str) -> None: ...
    def disable_task(self, task_id: str) -> None: ...

    def get_health(self, task_id: str) -> TaskHealth: ...
    def list_health(self) -> list[TaskHealth]: ...

    def tick(self, current_ts: float, current_sys_mode: str) -> list[str]:
        """
        璋冨害蹇冭烦锛氭鏌ユ墍鏈?enabled 浠诲姟锛岃繑鍥炴湰娆¤Е鍙戠殑 task_id 鍒楄〃銆?        - sys_mode 涓嶅湪 allowlist 鈫?SKIPPED锛堜笉瑙﹀彂锛屼笉璁″叆 error_count锛?        - 瑙﹀彂鍚庣敓鎴?system_initiated_intent 骞舵帹鍏?intent_queue
        - 杩炵画澶辫触 MAX_ERROR_COUNT 娆?鈫?status=SUSPENDED + 鍐?scheduler_config_log
        """

    def _build_system_intent(self, task: ScheduledTask) -> dict:
        """
        鏋勯€?system_initiated_intent锛?        - proposer_node_id = "scheduler"
        - intent_id = uuid4()
        - type = task.intent_type
        - action = task.intent_action
        - args = task.intent_args
        - source_task_id = task.task_id
        """
```

### 21.5 sys_mode 闂ㄦ帶瑙勫垯

| sys_mode | HIGH 浼樺厛绾т换鍔?| NORMAL 浼樺厛绾т换鍔?| LOW 浼樺厛绾т换鍔?|
|---------|--------------|----------------|--------------|
| NORMAL  | 鉁?瑙﹀彂 | 鉁?瑙﹀彂 | 鉁?瑙﹀彂 |
| GAMING  | 鉁?瑙﹀彂 | 鉂?SYSMODE_FORBIDDEN | 鉂?SYSMODE_FORBIDDEN |
| SLEEP   | 鉁?瑙﹀彂锛堢櫧鍚嶅崟锛?| 鉂?SYSMODE_FORBIDDEN | 鉂?SYSMODE_FORBIDDEN |
| FOCUS   | 鉁?瑙﹀彂 | 鉂?SYSMODE_FORBIDDEN锛坧roactive/dream 绫伙級 | 鉂?SYSMODE_FORBIDDEN |

> SLEEP 妯″紡涓?HIGH 浼樺厛绾х櫧鍚嶅崟锛歚["diary_generate", "dream_generate"]`

### 21.6 REST API 绔偣

| 鏂规硶 | 璺緞 | 鎻忚堪 |
|------|------|------|
| POST | /scheduler/tasks | 娉ㄥ唽鏂颁换鍔?|
| GET  | /scheduler/tasks | 鍒楀嚭鎵€鏈変换鍔★紙鍚仴搴风姸鎬侊級 |
| GET  | /scheduler/tasks/{task_id} | 鑾峰彇鍗曚釜浠诲姟璇︽儏 |
| PATCH | /scheduler/tasks/{task_id} | 鏇存柊浠诲姟閰嶇疆锛坈onfig_version 閫掑锛?|
| DELETE | /scheduler/tasks/{task_id} | 娉ㄩ攢浠诲姟 |
| POST | /scheduler/tasks/{task_id}/enable | 鍚敤浠诲姟 |
| POST | /scheduler/tasks/{task_id}/disable | 绂佺敤浠诲姟 |
| GET  | /scheduler/health | 鎵€鏈変换鍔″仴搴峰揩鐓?|

### 21.7 瀹¤瀛楁

- `scheduler_config_log`锛氫换鍔℃敞鍐?娉ㄩ攢/閰嶇疆鍙樻洿/鏆傚仠浜嬩欢锛屽瓧娈碉細`task_id / event_type / config_version / reason / ts`
- `system_initiated_intent`锛氳皟搴﹁Е鍙戠殑 intent锛屽瓧娈碉細`proposer_node_id="scheduler" / source_task_id / intent_id / ts`

### 21.8 閿欒鐮?
| 閿欒鐮?| 瑙﹀彂鍦烘櫙 |
|--------|---------|
| SCHED_TASK_NOT_FOUND | task_id 涓嶅瓨鍦?|
| SCHED_TASK_DUPLICATE | task_id 宸插瓨鍦?|
| SCHED_TASK_SUSPENDED | 杩炵画澶辫触瓒呴檺锛屼换鍔¤嚜鍔ㄦ殏鍋?|
| SCHED_CONDITION_UNMET | 瑙﹀彂鏉′欢涓嶆弧瓒筹紙闈?sys_mode 鍘熷洜锛?|
| SCHED_CONFIG_INVALID | cron_expr/interval_seconds 鏍煎紡閿欒鎴栦袱鑰呭潎涓虹┖ |

### 21.9 娴嬭瘯缂栧彿

| 娴嬭瘯缂栧彿 | 鍏宠仈闇€姹?| 鏂█鎻忚堪 |
|---------|---------|---------|
| T-SCHED-01 | FR-SCHED-001 | 娉ㄥ唽浠诲姟鍚?tick() 鍦ㄦ纭椂闂磋Е鍙戯紝鐢熸垚 system_initiated_intent |
| T-SCHED-02 | FR-SCHED-001 | sys_mode=GAMING 鏃?NORMAL 浼樺厛绾т换鍔¤璺宠繃锛圫YSMODE_FORBIDDEN锛?|
| T-SCHED-03 | FR-SCHED-001 | sys_mode=SLEEP 鏃?HIGH 浼樺厛绾х櫧鍚嶅崟浠诲姟姝ｅ父瑙﹀彂 |
| T-SCHED-04 | FR-SCHED-002 | 杩炵画澶辫触 MAX_ERROR_COUNT 娆″悗 status=SUSPENDED锛屽啓 scheduler_config_log |
| T-SCHED-05 | FR-SCHED-002 | /scheduler/health 杩斿洖鎵€鏈変换鍔″仴搴峰揩鐓э紝瀛楁瀹屾暣 |
| T-SCHED-06 | FR-SCHED-001 | 娉ㄩ攢浠诲姟鍚?tick() 涓嶅啀瑙﹀彂 |
| T-SCHED-07 | FR-SCHED-001 | system_initiated_intent 涓?proposer_node_id="scheduler" 涓?source_task_id 姝ｇ‘ |

---

## 搂22 妗岄潰鐣欒█锛圖esktop Message锛夊伐绋嬪绾?
### 22.1 妯″潡杈圭晫

```
host_brain/skills/desktop_message/
鈹溾攢鈹€ __init__.py
鈹溾攢鈹€ desktop_message_skill.py    # DesktopMessageSkill锛歝reate/update/delete/undo
鈹溾攢鈹€ desktop_message_models.py   # DesktopMessageArgs銆丏esktopMessageManifest Schema
鈹斺攢鈹€ path_validator.py           # 瀹夊叏璺緞鏍￠獙锛坆ase_dir allowlist + 璺緞绌胯秺妫€娴嬶級
```

### 22.2 SkillMeta 澹版槑

```python
DESKTOP_MESSAGE_SKILL_META = SkillMeta(
    skill_name="desktop_message",
    clearance_level="L1",
    risk_level=2,
    scopes=["filesystem"],
    reversible=True,
    requires_visual_audit=False,
    requires_confirm_token=False,   # 浣庨闄╋紝涓嶉渶瑕佷簩娆＄‘璁?    allow_list=["create", "update", "delete", "undo"],
    deny_list=[],
)
```

### 22.3 鏍稿績 Schema

#### DesktopMessageArgs
```python
@dataclass
class DesktopMessageArgs:
    action: str          # "create" / "update" / "delete" / "undo"
    filename: str        # 鏂囦欢鍚嶏紙涓嶅惈璺緞锛屼粎鏂囦欢鍚嶉儴鍒嗭級
    content: str | None  # 鐣欒█鍐呭锛坈reate/update 鏃跺繀濉級
    undo_token: str | None  # undo 鏃跺繀濉?```

#### DesktopMessageManifest
```python
@dataclass
class DesktopMessageManifest:
    manifest_id: str
    skill_name: str = "desktop_message"
    action: str
    file_path: str          # 缁濆璺緞锛堝凡鏍￠獙锛?    before_hash: str | None # 鎿嶄綔鍓嶆枃浠?SHA256锛堜笉瀛樺湪鏃朵负 None锛?    after_hash: str | None  # 鎿嶄綔鍚庢枃浠?SHA256锛堝垹闄ゆ椂涓?None锛?    before_content: str | None
    after_content: str | None
    undo_token: str
    reversible: bool = True
    ts: str                 # ISO8601
    request_id: str
    intent_id: str
```

### 22.4 瀹夊叏璺緞鏍￠獙瑙勫垯

```python
BASE_DIR_ALLOWLIST = [
    Path.home() / "Desktop",
    Path.home() / "Documents" / "Natsume_Messages",
]

def validate_path(filename: str, base_dir: Path) -> Path:
    """
    1. 鎷掔粷鍖呭惈 '..' 鐨?filename 鈫?SCOPE_VIOLATION
    2. 鎷掔粷缁濆璺緞 filename 鈫?SCOPE_VIOLATION
    3. 鎷掔粷绗﹀彿閾炬帴 鈫?SCOPE_VIOLATION
    4. 瑙ｆ瀽鍚庤矾寰勫繀椤讳互 base_dir 寮€澶?鈫?SCOPE_VIOLATION
    5. 鏂囦欢鍚嶉暱搴?鈮?255 瀛楄妭 鈫?SCOPE_VIOLATION
    """
```

### 22.5 鎵ц娴佺▼

```
create/update:
  1. validate_path(filename, base_dir)
  2. 璇诲彇 before_content锛堟枃浠朵笉瀛樺湪鏃?before_hash=None锛?  3. QA 妫€鏌?content锛堜笉寰楀惈浼墽琛屽彞锛?  4. 鍐欏叆鏂囦欢
  5. 璁＄畻 after_hash
  6. 鐢熸垚 manifest + undo_token
  7. 鍐欏叆 skill_exec_log锛堝惈 manifest_ref锛?
delete:
  1. validate_path(filename, base_dir)
  2. 璇诲彇 before_content + before_hash
  3. move-to-quarantine锛堜笉鐪熷垹锛?  4. 鐢熸垚 manifest锛坅fter_hash=None锛? undo_token
  5. 鍐欏叆 skill_exec_log

undo:
  1. 鏍￠獙 undo_token锛圱TL + manifest_ref 鍖归厤锛?  2. 璇诲彇 manifest锛坆efore_content/before_hash锛?  3. 鎭㈠鏂囦欢锛坈reate鈫掑垹闄わ紱update鈫掑啓鍥?before_content锛沝elete鈫掍粠 quarantine 鎭㈠锛?  4. 鍐欏叆 skill_exec_log锛坲ndo 浜嬩欢锛?```

### 22.6 閿欒鐮?
| 閿欒鐮?| 瑙﹀彂鍦烘櫙 |
|--------|---------|
| SCOPE_VIOLATION | 璺緞瓒婄晫/璺緞绌胯秺/绗﹀彿閾炬帴 |
| SYSMODE_FORBIDDEN | Gaming 妯″紡涓嬭Е鍙?|
| SKILL_EXEC_FAILED | 鏂囦欢绯荤粺涓嶅彲鍐?IO 閿欒 |
| DESKTOP_MSG_QA_REJECTED | 鍐呭鍚吉鎵ц鍙ワ紝QA 鎷︽埅 |
| DESKTOP_MSG_UNDO_INVALID | undo_token 鏃犳晥/杩囨湡/manifest 涓嶅尮閰?|
| DESKTOP_MSG_NOT_FOUND | delete/update 鏃舵枃浠朵笉瀛樺湪 |

### 22.7 娴嬭瘯缂栧彿

| 娴嬭瘯缂栧彿 | 鍏宠仈闇€姹?| 鏂█鎻忚堪 |
|---------|---------|---------|
| T-DESK-01 | FR-DESKTOP-001 | create 鎴愬姛锛宮anifest 鍚?before(None)/after/path/hash |
| T-DESK-02 | FR-DESKTOP-001 | undo_token 璋冪敤鍚庢枃浠舵仮澶嶅師鐘舵€?|
| T-DESK-03 | FR-DESKTOP-001 | 璺緞瓒婄晫锛堝惈 ..锛夆啋 SCOPE_VIOLATION |
| T-DESK-04 | FR-DESKTOP-001 | Gaming 妯″紡 鈫?SYSMODE_FORBIDDEN |
| T-DESK-05 | FR-DESKTOP-001 | 鏂囦欢绯荤粺涓嶅彲鍐?鈫?SKILL_EXEC_FAILED + reason_code |
| T-DESK-06 | FR-DESKTOP-002 | 鍐呭鍚吉鎵ц鍙?鈫?DESKTOP_MSG_QA_REJECTED |
| T-DESK-07 | FR-DESKTOP-001 | delete 榛樿 move-to-quarantine锛寀ndo 鍙仮澶?|
| T-DESK-08 | FR-DESKTOP-002 | 鍐欏叆瀹¤鍙拷婧師濮嬭崏妗堜笌鏈€缁堝唴瀹?|

---

## 搂23 姊﹀/鏃ヨ/涓诲姩瀵硅瘽/Focus Mode 宸ョ▼濂戠害

### 23.1 姊﹀锛圖ream锛夋ā鍧楄竟鐣?
```
host_brain/skills/dream/
鈹溾攢鈹€ __init__.py
鈹溾攢鈹€ dream_skill.py          # DreamSkill锛歡enerate锛堣 Episodic 鈫?LLM 鈫?鍐?Episodic锛?鈹斺攢鈹€ dream_models.py         # DreamArgs銆丏reamEntry Schema
```

#### DreamEntry Schema
```python
@dataclass
class DreamEntry:
    entry_id: str
    category: str = "dream"
    evidence_level: str = "NARRATIVE_ONLY"
    content: str                    # LLM 鐢熸垚鐨勬ⅵ澧冨彊浜?    source_episodic_ids: list[str]  # 鏉ユ簮 Episodic 鏉＄洰 ID
    ts: str                         # ISO8601
    request_id: str
    intent_id: str
    reversible: bool = True
    undo_token: str | None = None
```

#### 瑙﹀彂鏉′欢鏍￠獙
```python
def check_dream_precondition(sys_mode: str, idle_duration_s: float, config: dict) -> bool:
    """
    sys_mode in ["SLEEP"] OR idle_duration_s >= config["idle_threshold_s"]
    AND sys_mode NOT IN ["GAMING"]
    """
```

#### 娴嬭瘯缂栧彿

| 娴嬭瘯缂栧彿 | 鍏宠仈闇€姹?| 鏂█鎻忚堪 |
|---------|---------|---------|
| T-DREAM-01 | FR-DREAM-001 | Sleep 妯″紡瑙﹀彂锛屾ⅵ澧冨啓鍏?Episodic锛宑ategory=dream |
| T-DREAM-02 | FR-DREAM-001 | Normal 妯″紡鏃犵┖闂?鈫?SCHED_CONDITION_UNMET |
| T-DREAM-03 | FR-DREAM-001 | Episodic 涓嶅彲鐢?鈫?DEPENDENCY_UNAVAILABLE锛屼笉浼€?|
| T-DREAM-04 | FR-DREAM-002 | 鍙洖姊﹀鏃?evidence_level=NARRATIVE_ONLY 鏍囨敞瀛樺湪 |
| T-DREAM-05 | FR-DREAM-001 | Gaming 妯″紡 鈫?SYSMODE_FORBIDDEN |

---

### 23.2 鏃ヨ锛圖iary锛夋ā鍧楄竟鐣?
```
host_brain/skills/diary/
鈹溾攢鈹€ __init__.py
鈹溾攢鈹€ diary_skill.py          # DiarySkill锛歡enerate锛堣褰撴棩瀵硅瘽 鈫?LLM 鈫?鍐欐枃浠?+ Episodic锛?鈹斺攢鈹€ diary_models.py         # DiaryArgs銆丏iaryEntry銆丏iaryManifest Schema
```

#### DiaryManifest Schema
```python
@dataclass
class DiaryManifest:
    manifest_id: str
    skill_name: str = "diary"
    date: str                       # YYYY-MM-DD
    file_path: str                  # 缁濆璺緞锛堝畨鍏ㄥ熀鐩綍鍐咃級
    before_hash: str | None
    after_hash: str
    episodic_entry_ids: list[str]   # 鍐欏叆鐨?Episodic 鏉＄洰 ID
    canon_proposal_ids: list[str]   # 鍙€夛細鍏宠仈鐨?canon_proposal锛團R-DIARY-002锛?    undo_token: str
    reversible: bool = True
    ts: str
    request_id: str
    intent_id: str
```

#### 娴嬭瘯缂栧彿

| 娴嬭瘯缂栧彿 | 鍏宠仈闇€姹?| 鏂█鎻忚堪 |
|---------|---------|---------|
| T-DIARY-01 | FR-DIARY-001 | 姣忔棩 23:00 瑙﹀彂锛屾棩璁版枃浠跺垱寤烘垚鍔燂紝Episodic 鍐欏叆鎴愬姛 |
| T-DIARY-02 | FR-DIARY-001 | 鏃犲綋鏃ュ璇?鈫?杈撳嚭"鏃犲唴瀹?锛屼笉浼€犳棩璁?|
| T-DIARY-03 | FR-DIARY-001 | 鏂囦欢绯荤粺涓嶅彲鐢?鈫?SKILL_EXEC_FAILED锛屼笉浼€?|
| T-DIARY-04 | FR-DIARY-001 | Episodic 涓嶅彲鐢?鈫?DEPENDENCY_UNAVAILABLE锛屾枃浠朵粛鍙啓鍏ワ紙闄嶇骇锛?|
| T-DIARY-05 | FR-DIARY-002 | 鏃ヨ鍚瀹氭紓绉诲缓璁椂锛岀敓鎴?canon_proposal 骞跺啓鍏?canon_proposal_log |

---

### 23.3 涓诲姩瀵硅瘽锛圥roactive锛夋ā鍧楄竟鐣?
```
host_brain/skills/proactive/
鈹溾攢鈹€ __init__.py
鈹溾攢鈹€ proactive_skill.py      # ProactiveSkill锛歵rigger锛堟潯浠舵牎楠?鈫?LLM 鐢熸垚 鈫?鎺ㄩ€侊級
鈹斺攢鈹€ proactive_models.py     # ProactiveArgs銆丳roactiveEvent Schema
```

#### 瑙﹀彂鏉′欢鏍￠獙
```python
def check_proactive_precondition(
    idle_duration_s: float,
    bond_stage: str,
    sys_mode: str,
    hourly_count: int,
    config: dict,
) -> tuple[bool, str | None]:
    """
    Returns (can_trigger, reason_code_if_blocked)
    - sys_mode == "GAMING" 鈫?(False, "SYSMODE_FORBIDDEN")
    - hourly_count >= config["max_per_hour"] 鈫?(False, "RATE_LIMIT_EXCEEDED")
    - idle_duration_s < config["idle_threshold_s"] 鈫?(False, "SCHED_CONDITION_UNMET")
    - bond_stage not in config["bond_stage_allowlist"] 鈫?(False, "SCHED_CONDITION_UNMET")
    """
```

#### 娴嬭瘯缂栧彿

| 娴嬭瘯缂栧彿 | 鍏宠仈闇€姹?| 鏂█鎻忚堪 |
|---------|---------|---------|
| T-PROACT-01 | FR-PROACTIVE-001 | 绌洪棽瑙﹀彂锛岀敤鎴风鏀跺埌涓诲姩娑堟伅锛屽璁￠摼瀹屾暣 |
| T-PROACT-02 | FR-PROACTIVE-001 | Gaming 鈫?SYSMODE_FORBIDDEN |
| T-PROACT-03 | FR-PROACTIVE-001 | 棰戠巼瓒呴檺 鈫?RATE_LIMIT_EXCEEDED |
| T-PROACT-04 | FR-PROACTIVE-002 | 鍐呭鍚墽琛?intent 鈫?QA 鎷︽埅 |
| T-PROACT-05 | FR-PROACTIVE-002 | Edge 涓嶅彲杈?鈫?闈欓粯闄嶇骇锛屼笉閲嶈瘯锛圖EPENDENCY_UNAVAILABLE锛?|
| T-PROACT-06 | FR-PROACTIVE-001 | Sleep 妯″紡 鈫?浠呭厑璁镐綆棰戦棶鍊欙紙bond_stage 鐧藉悕鍗曪級 |

---

### 23.4 Focus Mode 宸ョ▼濂戠害

#### sys_mode 鎵╁睍

Focus Mode 浣滀负 `sys_mode` 鐨勫瓙鐘舵€侊紝鍦?`SysModeEnum` 涓柊澧?`FOCUS`锛?
```python
class SysModeEnum(str, Enum):
    NORMAL = "NORMAL"
    GAMING = "GAMING"
    SLEEP = "SLEEP"
    FOCUS = "FOCUS"   # 鏂板
```

#### 琛屼负鐭╅樀

| 鑳藉姏 | FOCUS 妯″紡琛屼负 |
|------|--------------|
| 鐢ㄦ埛涓诲姩瀵硅瘽 | 鉁?姝ｅ父鍝嶅簲 |
| 涓诲姩瀵硅瘽锛圥roactive锛?| 鉂?SYSMODE_FORBIDDEN |
| 姊﹀鐢熸垚锛圖ream锛?| 鉂?SYSMODE_FORBIDDEN |
| 鏃ヨ鐢熸垚锛圖iary锛?| 鉂?SYSMODE_FORBIDDEN锛堟帹杩熷埌閫€鍑?Focus 鍚庯級 |
| 閫氱煡鎺ㄩ€?| 鉂?闈欓粯 |
| Scheduler 闈炵揣鎬ヤ换鍔?| 鉂?SYSMODE_FORBIDDEN |

#### 鍒囨崲瀹¤

```python
# hard_state_log 瀛楁
{
    "event_type": "SYS_MODE_CHANGE",
    "from_mode": "NORMAL",
    "to_mode": "FOCUS",
    "source": "user_command" | "scheduled_policy" | "calendar_event",
    "evidence_summary": str,
    "ts": str,
    "request_id": str,
}
```

#### 娴嬭瘯缂栧彿

| 娴嬭瘯缂栧彿 | 鍏宠仈闇€姹?| 鏂█鎻忚堪 |
|---------|---------|---------|
| T-FOCUS-01 | FR-FOCUS-001 | 杩涘叆 Focus 鍚庝富鍔ㄥ璇濊鎷︽埅锛圫YSMODE_FORBIDDEN锛?|
| T-FOCUS-02 | FR-FOCUS-001 | 鐢ㄦ埛涓诲姩鎻愰棶姝ｅ父鍝嶅簲 |
| T-FOCUS-03 | FR-FOCUS-001 | 閫€鍑?Focus 鍚庤皟搴﹀櫒鎭㈠ |
| T-FOCUS-04 | FR-FOCUS-001 | 鍒囨崲浜嬩欢鍐欏叆 hard_state_log锛屽瓧娈靛畬鏁?|

### 23.5 搂21~搂23 鏂板閿欒鐮佹眹鎬?
| 閿欒鐮?| 瑙﹀彂鍦烘櫙 |
|--------|---------|
| SCHED_TASK_NOT_FOUND | 璋冨害浠诲姟 ID 涓嶅瓨鍦?|
| SCHED_TASK_DUPLICATE | 璋冨害浠诲姟 ID 宸插瓨鍦?|
| SCHED_TASK_SUSPENDED | 杩炵画澶辫触瓒呴檺锛屼换鍔¤嚜鍔ㄦ殏鍋?|
| SCHED_CONDITION_UNMET | 瑙﹀彂鏉′欢涓嶆弧瓒筹紙闈?sys_mode 鍘熷洜锛?|
| SCHED_CONFIG_INVALID | cron_expr/interval_seconds 鏍煎紡閿欒 |
| SCOPE_VIOLATION | 璺緞瓒婄晫/璺緞绌胯秺/绗﹀彿閾炬帴 |
| DESKTOP_MSG_QA_REJECTED | 妗岄潰鐣欒█鍐呭鍚吉鎵ц鍙?|
| DESKTOP_MSG_UNDO_INVALID | undo_token 鏃犳晥/杩囨湡/manifest 涓嶅尮閰?|
| DESKTOP_MSG_NOT_FOUND | 鐩爣鏂囦欢涓嶅瓨鍦?|
| RATE_LIMIT_EXCEEDED | 涓诲姩瀵硅瘽棰戠巼瓒呴檺 |

---

## 搂24 M12 宸ョ▼濂戠害锛圥LANNED锛?
### 搂24.1 M12-A ToolBroker 鍗曞叆鍙?+ tool_visibility_hash锛團R-TOOL-001/002/003锛?
#### 24.1.1 妯″潡杈圭晫

```
host_brain/tools/
  tool_broker.py          鈫?ToolBroker 鍗曞叆鍙ｏ紙NEW锛?  tool_meta_registry.py   鈫?ToolMeta 娉ㄥ唽琛紙NEW锛?  tool_visibility.py      鈫?tool_visibility_hash 璁＄畻涓庡璁★紙NEW锛?```

#### 24.1.2 ToolMeta Schema

```python
@dataclass(frozen=True)
class ToolMeta:
    tool_id: str                    # 鍞竴鏍囪瘑
    provider_id: str                # MCP provider 鎴?internal
    name: str                       # 灞曠ず鍚?    description: str                # 鍔熻兘鎻忚堪锛堟毚闇茬粰 LLM锛?    access_level: Literal["READ", "WRITE"]
    requires_token: bool            # WRITE 蹇呴』涓?True
    clearance_level: int            # 0~4
    risk_level: int                 # 0~4
    scopes: list[str]
    reversible: bool
    version: str
```

#### 24.1.3 ToolBroker 鎺ュ彛

```python
class ToolBroker:
    def get_tool_meta_pack(
        self,
        context: dict,
        top_k: int = 5,
    ) -> ToolMetaPack:
        """杩斿洖褰撳墠杞鍙鐨?Top-K 宸ュ叿鍏冩暟鎹紙涓嶅惈 WRITE 榛樿锛夈€?""

    def execute_tool(
        self,
        tool_id: str,
        args: dict,
        context: dict,
    ) -> ToolExecResult:
        """鎵ц宸ュ叿锛圵RITE 蹇呴』鏈?approval_token锛夈€?""

    def compute_visibility_hash(
        self,
        tool_meta_pack: ToolMetaPack,
    ) -> str:
        """璁＄畻 tool_visibility_hash锛圫HA-256 of sorted tool_ids锛夈€?""
```

#### 24.1.4 tool_visibility_hash 瀹¤瀛楁

| 瀛楁 | 绫诲瀷 | 璇存槑 |
|------|------|------|
| `tool_visibility_hash` | str | SHA-256(sorted tool_ids) |
| `tool_count` | int | 鏈疆鍙宸ュ叿鏁伴噺 |
| `write_tools_visible` | bool | 鏄惁鍖呭惈 WRITE 宸ュ叿 |
| `request_id` | str | 鍏宠仈璇锋眰 |
| `intent_id` | str | 鍏宠仈 intent |
| `ts_ms` | int | 鏃堕棿鎴?|

#### 24.1.5 閿欒鐮?
| 閿欒鐮?| 瑙﹀彂鏉′欢 |
|--------|---------|
| `TOOL_VISIBILITY_POLICY_VIOLATION` | WRITE 宸ュ叿鍑虹幇鍦ㄦ湭鎺堟潈涓婁笅鏂?|
| `TOOL_TOKEN_MISSING` | WRITE 宸ュ叿鏃?approval_token |
| `TOOL_NOT_FOUND` | tool_id 涓嶅湪娉ㄥ唽琛?|
| `TOOL_EXEC_FAILED` | 宸ュ叿鎵ц寮傚父 |
| `TOOL_EVIDENCE_REF_MISSING` | WRITE 鎵ц鏃?evidence_ref |

#### 24.1.6 娴嬭瘯缂栧彿

| 缂栧彿 | 鎻忚堪 |
|------|------|
| T-TOOL-01 | get_tool_meta_pack 榛樿涓嶅惈 WRITE 宸ュ叿 |
| T-TOOL-02 | WRITE 宸ュ叿鏃?token 鈫?TOOL_TOKEN_MISSING |
| T-TOOL-03 | tool_visibility_hash 鍙噸鐜帮紙鍚岃緭鍏ュ悓杈撳嚭锛?|
| T-TOOL-04 | tool_visibility_hash 鍐欏叆瀹¤閾?|
| T-TOOL-05 | MCP WRITE 鏃?APPROVE 鈫?鎷掔粷涓斿彲缁熻 |
| T-TOOL-06 | 缁曡繃 ToolBroker 鐩存帴鎵ц 鈫?闃绘柇 |

---

### 搂24.2 M12-B 澶氳疆瀵硅瘽涓婁笅鏂囧帇缂╋紙FR-MEM-005 鎵╁睍锛?
#### 24.2.1 妯″潡杈圭晫

```
host_brain/memory/
  context_compressor.py   鈫?ContextCompressor锛圢EW锛?```

#### 24.2.2 ContextCompressor 鎺ュ彛

```python
class ContextCompressor:
    def compress(
        self,
        turns: list[dict],
        *,
        max_tokens: int = 2048,
        strategy: Literal["sliding_window", "summary", "hybrid"] = "hybrid",
    ) -> CompressedContext:
        """鍘嬬缉澶氳疆瀵硅瘽涓婁笅鏂囷紝杩斿洖缁撴瀯鍖?evidence pack銆?""

    def build_evidence_pack(
        self,
        compressed: CompressedContext,
        episodic_hits: list[dict],
        canon_slots: dict,
    ) -> EvidencePack:
        """鏋勫缓缁撴瀯鍖?evidence pack锛堝€欓€?鍒嗘暟/鎽樿/鏉ユ簮鎸囬拡/缃俊搴︼級銆?""
```

#### 24.2.3 EvidencePack Schema

```python
@dataclass(frozen=True)
class EvidencePack:
    pack_id: str
    request_id: str
    intent_id: str
    candidates: list[EvidenceCandidate]   # TopK 鍊欓€?    total_tokens: int
    strategy: str
    ts_ms: int
    hash: str                              # SHA-256(candidates)
```

#### 24.2.4 娴嬭瘯缂栧彿

| 缂栧彿 | 鎻忚堪 |
|------|------|
| T-CTX-01 | sliding_window 鎴柇瓒呭嚭 max_tokens 鐨勮疆娆?|
| T-CTX-02 | summary 绛栫暐鐢熸垚鎽樿涓斾笉浼€犱簨瀹?|
| T-CTX-03 | hybrid 绛栫暐浼樺厛淇濈暀鏈€杩?N 杞?+ 鎽樿鍘嗗彶 |
| T-CTX-04 | evidence_pack 瀛楁缁撴瀯绋冲畾锛堝悓鐗堟湰鍚岃緭鍏ワ級 |
| T-CTX-05 | LLM 涓嶅彲鐢ㄦ椂闄嶇骇涓?sliding_window |

---

### 搂24.3 M12-C Reranker 鏈嶅姟寮哄寲锛團R-MEM-004锛?
#### 24.3.1 妯″潡杈圭晫

```
host_brain/retrieval/
  reranker.py             鈫?RerankerService锛堝凡鏈夛紝闇€寮哄寲锛?```

#### 24.3.2 RerankerService 鎺ュ彛寮哄寲

```python
class RerankerService:
    def rerank(
        self,
        query: str,
        candidates: list[dict],
        *,
        top_k: int = 5,
        fallback: Literal["recall_order", "lightweight"] = "recall_order",
    ) -> RerankResult:
        """閲嶆帓鍊欓€夛紝涓嶅彲鐢ㄦ椂闄嶇骇骞舵樉寮忔爣娉?stage銆?""

    def health(self) -> dict:
        """杩斿洖鍋ュ悍鐘舵€侊紙status/latency_ms/last_error锛夈€?""
```

#### 24.3.3 娴嬭瘯缂栧彿

| 缂栧彿 | 鎻忚堪 |
|------|------|
| T-RERANK-01 | 姝ｅ父閲嶆帓杩斿洖 TopK锛屽瓧娈电ǔ瀹?|
| T-RERANK-02 | Reranker 涓嶅彲鐢?鈫?闄嶇骇涓?recall_order锛宻tage 鏍囨敞 |
| T-RERANK-03 | health() 杩斿洖缁撴瀯绋冲畾 |
| T-RERANK-04 | evidence_pack 鍚?rerank_stage 瀛楁 |

---

### 搂24.4 M12-D 鎶€鑳界儹鍔犺浇锛團R-SKILL-001/002 鎵╁睍锛?
#### 24.4.1 妯″潡杈圭晫

```
host_brain/skills/
  hot_loader.py           鈫?SkillHotLoader锛圢EW锛?  version_registry.py     鈫?SkillVersionRegistry锛圢EW锛?```

#### 24.4.2 SkillHotLoader 鎺ュ彛

```python
class SkillHotLoader:
    def reload_skill(
        self,
        skill_name: str,
        *,
        version: str | None = None,
    ) -> SkillReloadResult:
        """鐑姞杞芥寚瀹?Skill锛堜笉閲嶅惎鏈嶅姟锛夈€?""

    def list_versions(self, skill_name: str) -> list[SkillVersion]:
        """鍒楀嚭宸叉敞鍐岀殑 Skill 鐗堟湰銆?""

    def rollback_skill(
        self,
        skill_name: str,
        target_version: str,
    ) -> SkillReloadResult:
        """鍥炴粴鍒版寚瀹氱増鏈€?""
```

#### 24.4.3 娴嬭瘯缂栧彿

| 缂栧彿 | 鎻忚堪 |
|------|------|
| T-HOTLOAD-01 | reload_skill 鎴愬姛锛屾柊鐗堟湰鐢熸晥 |
| T-HOTLOAD-02 | reload_skill 澶辫触锛屾棫鐗堟湰淇濇寔涓嶅彉 |
| T-HOTLOAD-03 | rollback_skill 鍥炴粴鍒版寚瀹氱増鏈?|
| T-HOTLOAD-04 | 鐑姞杞藉啓 skill_reload_log锛堝璁★級 |
| T-HOTLOAD-05 | 杩愯鏃朵竴鑷存€ф鏌ワ細SkillMeta 瀛楁缂哄け 鈫?鎷掔粷鍔犺浇 |

---

## 搂25 M13 宸ョ▼濂戠害锛圔ond 闃舵鑷姩鏅嬬骇 + 闀挎湡璁板繂钂搁寮哄寲锛?
### 搂25.1 M13-A Bond 闃舵绉垎 + 鑷姩鏅嬬骇锛團R-BOND-001/002/003锛?
#### 25.1.1 妯″潡杈圭晫

```
host_brain/bond/
  bond_stage_engine.py    鈫?BondStageEngine锛圢EW锛?host_brain/app/
  bond_routes.py          鈫?Bond REST API 5 绔偣锛圢EW锛?```

#### 25.1.2 BondStageEngine 鎺ュ彛

```python
@dataclass(frozen=True)
class BondScoreState:
    stage: str                          # "stranger" | "acquaintance" | "friend" | "close_friend" | "soulmate"
    score: float                        # 鍐呴儴绉垎锛堜笉鏆撮湶缁欒涔夊唴鏍革級
    trend: Literal["rising", "stable", "falling"]
    last_event_ts: int
    audit_ref: str                      # 鏈€杩戜竴娆″璁￠摼寮曠敤

@dataclass(frozen=True)
class StageTransitionResult:
    previous_stage: str
    new_stage: str
    delta: float
    transition_type: Literal["upgrade", "downgrade", "stable"]
    audit_ref: str
    ts_ms: int

class BondStageEngine:
    def apply_event(
        self,
        event: dict,                    # etype / intensity / evidence_refs
        current_state: BondScoreState,
    ) -> tuple[BondScoreState, StageTransitionResult]:
        """搴旂敤 Bond 浜嬩欢锛岃繑鍥炴柊鐘舵€佷笌鏅嬬骇/闄嶇骇缁撴灉锛涙檵绾?闄嶇骇鍐欏叆瀹¤閾俱€?""

    def get_summary(
        self,
        state: BondScoreState,
    ) -> dict:
        """杩斿洖缁欒涔夊唴鏍哥殑 Bond Summary锛堜粎 stage/trend/capability_hint锛屼笉鍚暟鍊硷級銆?""

    def get_trend(self, history: list[BondScoreState]) -> str:
        """璁＄畻瓒嬪娍锛坮ising/stable/falling锛夈€?""
```

#### 25.1.3 Bond REST API 绔偣

| 绔偣 | 鏂规硶 | 鎻忚堪 |
|------|------|------|
| `/bond/stage` | GET | 杩斿洖褰撳墠 Bond 闃舵鐘舵€侊紙涓嶅惈鏁板€硷級 |
| `/bond/summary` | GET | 杩斿洖缁欒涔夊唴鏍哥殑 Bond Summary |
| `/bond/event` | POST | 鎻愪氦 Bond 浜嬩欢锛堥渶 evidence_refs锛?|
| `/bond/users` | GET | 鍒楀嚭鎵€鏈夌敤鎴风殑 Bond 鐘舵€?|
| `/bond/health` | GET | 鍋ュ悍妫€鏌?|

#### 25.1.4 瀹¤瀛楁

| 瀛楁 | 绫诲瀷 | 璇存槑 |
|------|------|------|
| `stage_transition` | dict | 鏅嬬骇/闄嶇骇缁撴灉锛坧revious/new/delta/type锛?|
| `audit_ref` | str | 瀹¤閾惧紩鐢?|
| `evidence_refs` | list[str] | 瑙﹀彂浜嬩欢鐨勮瘉鎹紩鐢?|
| `ts_ms` | int | 鏃堕棿鎴?|

#### 25.1.5 閿欒鐮?
| 閿欒鐮?| 瑙﹀彂鏉′欢 |
|--------|---------|
| `BOND_UPDATE_INVALID` | 缂?evidence_refs 鐨?Bond 鏇存柊 |
| `BOND_STAGE_UNKNOWN` | 鏈煡 stage 鍊?|
| `BOND_EVENT_INVALID` | 浜嬩欢鏍煎紡涓嶅悎瑙?|

#### 25.1.6 娴嬭瘯缂栧彿

| 缂栧彿 | 鎻忚堪 |
|------|------|
| T-BOND-STAGE-01 | apply_event 绉垎绱Н锛宻tage 涓嶅彉 |
| T-BOND-STAGE-02 | 绉垎瓒呴槇鍊?鈫?鑷姩鏅嬬骇锛屽啓瀹¤閾?|
| T-BOND-STAGE-03 | 绉垎浣庝簬闃堝€?鈫?鑷姩闄嶇骇锛屽啓瀹¤閾?|
| T-BOND-STAGE-04 | get_summary 涓嶅惈鏁板€硷紙FR-BOND-003锛?|
| T-BOND-STAGE-05 | 缂?evidence_refs 鈫?BOND_UPDATE_INVALID |
| T-BOND-STAGE-06 | get_trend 杩斿洖 rising/stable/falling |
| T-BOND-STAGE-07 | 杈圭晫锛歴core=0 涓嶉檷绾у埌 stranger 浠ヤ笅 |
| T-BOND-STAGE-08 | 杈圭晫锛歴core=max 涓嶆檵绾у埌 soulmate 浠ヤ笂 |
| T-BOND-STAGE-09 | 瀹¤閾惧彲杩芥函 stage_transition 瀛楁 |
| T-BOND-API-01 | GET /bond/stage 杩斿洖缁撴瀯绋冲畾 |
| T-BOND-API-02 | GET /bond/summary 涓嶅惈鏁板€?|
| T-BOND-API-03 | POST /bond/event 鎴愬姛锛岃繑鍥?stage_transition |
| T-BOND-API-04 | POST /bond/event 缂?evidence_refs 鈫?422 |
| T-BOND-API-05 | GET /bond/users 杩斿洖鍒楄〃缁撴瀯绋冲畾 |
| T-BOND-API-06 | GET /bond/health 杩斿洖 status/latency_ms |
| T-BOND-API-07 | Bond Summary 涓嶅惈鏁板€硷紙FR-BOND-003 API 灞傞獙鏀讹級 |
| T-BOND-API-08 | 闈炴硶 event 鏍煎紡 鈫?422 + reason_code |

---

### 搂25.2 M13-B 闀挎湡璁板繂钂搁寮哄寲锛團R-MEM-DISTILL-001/002锛?
#### 25.2.1 妯″潡杈圭晫

```
host_brain/memory/
  long_term_distiller.py  鈫?LongTermMemoryDistiller锛圢EW锛?```

#### 25.2.2 LongTermMemoryDistiller 鎺ュ彛

```python
@dataclass(frozen=True)
class DistillationProposal:
    proposal_id: str
    source_episodic_ids: list[str]      # 鏉ユ簮 EpisodicMemory ID
    semantic_summary: str               # 璇箟鎽樿
    confidence: float
    evidence_refs: list[str]
    status: Literal["pending", "approved", "rejected"]
    ts_ms: int

class LongTermMemoryDistiller:
    def distill(
        self,
        episodic_memories: list[dict],
        *,
        max_proposals: int = 5,         # 姣忔鏈€澶?5 涓彁妗?    ) -> list[DistillationProposal]:
        """灏?EpisodicMemory 钂搁涓鸿涔夎蹇嗘彁妗堬紙pending 鐘舵€侊紝涓嶇洿鎺ュ啓 Canon SSOT锛夈€?""

    def is_duplicate(
        self,
        proposal: DistillationProposal,
        existing: list[DistillationProposal],
    ) -> bool:
        """骞傜瓑淇濇姢锛氭鏌ユ槸鍚﹀凡瀛樺湪鐩稿悓鎻愭銆?""

def make_distillation_task_config(
    *,
    cron: str = "0 3 * * *",           # 姣忔棩 03:00
    max_proposals: int = 5,
    enabled: bool = True,
) -> dict:
    """鐢熸垚璋冨害鍣?CRON 浠诲姟閰嶇疆锛堝伐鍘傚嚱鏁帮級銆?""
```

#### 25.2.3 鍏抽敭绾︽潫

- 钂搁缁撴灉鍙敓鎴?`pending` 鎻愭锛?*涓嶇洿鎺ュ啓 Canon SSOT**锛團R-MEM-DISTILL-001 绾㈢嚎锛?- 姣忔寰幆鏈€澶?5 涓彁妗堬紙闃叉鎵归噺姹℃煋锛?- 骞傜瓑淇濇姢锛氬悓涓€ source_episodic_ids 缁勫悎涓嶉噸澶嶇敓鎴愭彁妗?- 鎻愭蹇呴』鎼哄甫 `evidence_refs`锛堟潵婧?EpisodicMemory ID锛?
#### 25.2.4 娴嬭瘯缂栧彿

| 缂栧彿 | 鎻忚堪 |
|------|------|
| T-DISTILL-B-01 | distill 杩斿洖 pending 鎻愭鍒楄〃锛屼笉鍐?Canon |
| T-DISTILL-B-02 | 鎻愭 status=pending锛堜笉鐩存帴鍐?SSOT锛?|
| T-DISTILL-B-03 | 姣忔鏈€澶?5 涓彁妗堬紙max_proposals 杈圭晫锛?|
| T-DISTILL-B-04 | 骞傜瓑锛氱浉鍚?source_episodic_ids 涓嶉噸澶嶇敓鎴?|
| T-DISTILL-B-05 | 鎻愭鍚?evidence_refs锛堟潵婧?EpisodicMemory ID锛?|
| T-DISTILL-B-06 | episodic_memories 涓虹┖ 鈫?杩斿洖绌哄垪琛?|
| T-DISTILL-B-07 | 璇箟鎽樿涓嶄吉閫犱簨瀹烇紙confidence 瀛楁鍙娴嬶級 |
| T-DISTILL-B-08 | is_duplicate 姝ｇ‘璇嗗埆閲嶅鎻愭 |
| T-DISTILL-B-09 | make_distillation_task_config 杩斿洖鍚堟硶 CRON 閰嶇疆 |
| T-DISTILL-B-10 | make_distillation_task_config 闈炴硶鍙傛暟 鈫?鎶涘嚭 ValueError |

---

### 搂25.3 M13-C Bond 浜嬩欢绔埌绔泦鎴愶紙FR-BOND-001/002锛?
#### 25.3.1 妯″潡杈圭晫

```
host_brain/bond/
  bond_dynamics.py        鈫?BondDynamicsWriter锛堝凡鏈夛紝闇€寮哄寲锛?```

#### 25.3.2 BondDynamicsWriter 寮哄寲鎺ュ彛

```python
class BondDynamicsWriter:
    def __init__(
        self,
        *,
        stage_engine: BondStageEngine | None = None,  # 鍙€夋敞鍏ワ紝鍚戝悗鍏煎
    ): ...

    def apply_update(
        self,
        event: dict,
        *,
        user_id: str,
    ) -> dict:
        """
        搴旂敤 Bond 鏇存柊锛?        1. 鍐?Neo4j SSOT锛堝師鏈夐€昏緫锛?        2. 鑻?stage_engine 宸叉敞鍏ワ紝璋冪敤 apply_event 骞跺皢 stage_transition 鍐欏叆 event 缁撴灉
        3. stage_engine 寮傚父鏃堕潪鑷村懡锛堥檷绾э紝璁板綍 warn锛屼笉涓柇涓绘祦绋嬶級
        """
```

#### 25.3.3 event 缁撴灉鏂板瀛楁

| 瀛楁 | 绫诲瀷 | 璇存槑 |
|------|------|------|
| `stage_transition` | dict \| None | 鏅嬬骇/闄嶇骇缁撴灉锛泂tage_engine=None 鏃朵负 None |
| `stage_engine_error` | str \| None | stage_engine 寮傚父鏃剁殑閿欒鎽樿锛堥潪鑷村懡锛?|

#### 25.3.4 娴嬭瘯缂栧彿

| 缂栧彿 | 鎻忚堪 |
|------|------|
| T-BOND-INTEG-01 | apply_update 鎴愬姛锛宔vent 鍚?stage_transition |
| T-BOND-INTEG-02 | stage_engine=None 鈫?stage_transition=None锛堝悜鍚庡吋瀹癸級 |
| T-BOND-INTEG-03 | stage_engine 寮傚父 鈫?闈炶嚧鍛斤紝涓绘祦绋嬬户缁?|
| T-BOND-INTEG-04 | stage_transition 鍐欏叆瀹¤閾?|
| T-BOND-INTEG-05 | 缂?evidence_refs 鈫?BOND_UPDATE_INVALID锛團R-BOND-002锛?|
| T-BOND-INTEG-06 | 绔埌绔細Bond 浜嬩欢 鈫?鏅嬬骇 鈫?瀹¤閾惧彲杩芥函 |
| T-BOND-INTEG-07 | 绔埌绔細Bond 浜嬩欢 鈫?闄嶇骇 鈫?瀹¤閾惧彲杩芥函 |
| T-BOND-INTEG-08 | 鏃ц皟鐢ㄨ矾寰勶紙鏃?stage_engine锛夐浂褰卞搷 |

---

## 搂26 M13 鍙戝竷娓呭崟宸ョ▼濂戠害

### 搂26.1 鍙戝竷娓呭崟 Schema

```json
{
  "milestone": "M13",
  "release_date": "2026-03-04",
  "release_ready": true,
  "milestones": {
    "M13-A": "DONE",
    "M13-B": "DONE",
    "M13-C": "DONE",
    "M13-D": "DONE"
  },
  "core_files": {
    "host_brain/bond/bond_stage_engine.py": "<sha256>",
    "host_brain/app/bond_routes.py": "<sha256>",
    "host_brain/memory/long_term_distiller.py": "<sha256>",
    "host_brain/bond/bond_dynamics.py": "<sha256>"
  },
  "test_summary": {
    "m13_tests_passed": 102,
    "total_tests_passed": 1342,
    "skipped": 2,
    "failed": 0
  },
  "quality_gates": {
    "all_core_files_present": true,
    "zero_regressions": true,
    "audit_chain_verified": true
  }
}
```

### 搂26.2 璐ㄩ噺闂ㄧ

| 闂ㄧ椤?| 瑕佹眰 |
|--------|------|
| `release_ready` | 蹇呴』涓?true |
| `all_core_files_present` | 鎵€鏈夋牳蹇冩枃浠?hash 宸插～鍐?|
| `zero_regressions` | failed=0 |
| `audit_chain_verified` | 瀹¤閾惧瓧娈靛彲杩芥函 |

---

## 搂27 M14 宸ョ▼濂戠害锛堥€氶亾 + 澶栨樉 + MCP 娌荤悊鏀跺彛锛?
### 搂27.1 M14-A 寰俊閫氶亾 I/O 浜嬩欢鍖栵紙FR-CHANNEL-001锛?
#### 27.1.1 妯″潡杈圭晫

```
host_brain/channels/
  wechat_channel.py       鈫?WeChatChannelEvent锛圢EW锛?host_brain/core/
  errors.py               鈫?鏂板 CHANNEL_* 閿欒鐮?tests/unit/
  test_wechat_channel.py  鈫?T-CHAN-01~05
tests/integration/
  test_wechat_channel_audit.py  鈫?T-CHAN-AUDIT-01~03
```

#### 27.1.2 WeChatChannelEvent Schema

```python
from enum import Enum
from dataclasses import dataclass

class ChannelEventType(str, Enum):
    INBOUND  = "inbound"    # 鐢ㄦ埛鈫扝ost
    OUTBOUND = "outbound"   # Host鈫掔敤鎴?
@dataclass(frozen=True)
class WeChatChannelEvent:
    event_id: str                           # UUID
    event_type: ChannelEventType
    thread_key: str                         # 浼氳瘽绾跨▼鏍囪瘑锛堝彲杩芥函锛?    request_id: str                         # 鍏宠仈 Host 璇锋眰 ID
    intent_id: str | None                   # 鍏宠仈 intent锛坥utbound 蹇呭～锛?    payload: dict                           # 娑堟伅鍐呭锛堝噣鍖栧悗锛?    audit_ref: str                          # 瀹¤閾惧紩鐢?    ts_ms: int
    reason_code: str | None = None          # 澶辫触鏃跺繀濉?    stage: str | None = None                # 澶辫触闃舵
```

#### 27.1.3 閫氶亾浜嬩欢澶勭悊鎺ュ彛

```python
class WeChatChannelHandler:
    def receive(
        self,
        raw_payload: dict,
        *,
        thread_key: str,
        request_id: str,
    ) -> WeChatChannelEvent:
        """
        鎺ユ敹寰俊 inbound 娑堟伅锛屽缓妯′负 WeChatChannelEvent銆?        - 鍐欏叆瀹¤閾撅紙channel_event_log锛?        - 杩斿洖缁撴瀯鍖栦簨浠讹紙涓嶅惈鍘熷鏁忔劅瀛楁锛?        """

    def send(
        self,
        message: dict,
        *,
        thread_key: str,
        request_id: str,
        intent_id: str,
        approval_token: str,
    ) -> WeChatChannelEvent:
        """
        鍙戦€佸井淇?outbound 娑堟伅銆?        - LLM 涓嶅緱鑷鍐冲畾"鏄惁鍙戦€?鍙戠粰璋?鍙戜粈涔?
        - 蹇呴』鏈?approval_token锛圵RITE 鎿嶄綔锛?        - 鍐欏叆瀹¤閾撅紙channel_event_log锛?        - 澶辫触蹇呴』杩斿洖 reason_code + stage
        """
```

#### 27.1.4 瀹¤瀛楁锛坈hannel_event_log锛?
| 瀛楁 | 绫诲瀷 | 璇存槑 |
|------|------|------|
| `event_id` | str | 閫氶亾浜嬩欢鍞竴 ID |
| `event_type` | str | inbound / outbound |
| `thread_key` | str | 浼氳瘽绾跨▼鏍囪瘑 |
| `request_id` | str | 鍏宠仈 Host 璇锋眰 ID |
| `intent_id` | str \| None | 鍏宠仈 intent锛坥utbound 蹇呭～锛?|
| `audit_ref` | str | 瀹¤閾惧紩鐢?|
| `reason_code` | str \| None | 澶辫触鏃跺繀濉?|
| `stage` | str \| None | 澶辫触闃舵 |
| `ts_ms` | int | 鏃堕棿鎴?|

#### 27.1.5 閿欒鐮?
| 閿欒鐮?| 瑙﹀彂鏉′欢 |
|--------|---------|
| `CHANNEL_SEND_UNAUTHORIZED` | outbound 鏃?approval_token |
| `CHANNEL_INTENT_MISSING` | outbound 鏃?intent_id |
| `CHANNEL_PAYLOAD_INVALID` | 娑堟伅鍐呭鏍煎紡涓嶅悎瑙?|
| `CHANNEL_THREAD_KEY_MISSING` | 缂?thread_key |
| `CHANNEL_AUDIT_WRITE_FAILED` | 瀹¤閾惧啓鍏ュけ璐ワ紙闈炶嚧鍛斤紝闄嶇骇锛?|

#### 27.1.6 娴嬭瘯缂栧彿

| 缂栧彿 | 鎻忚堪 |
|------|------|
| T-CHAN-01 | receive 鎴愬姛锛岃繑鍥?WeChatChannelEvent锛坕nbound锛夛紝鍐欏璁￠摼 |
| T-CHAN-02 | send 鎴愬姛锛岃繑鍥?WeChatChannelEvent锛坥utbound锛夛紝鍐欏璁￠摼 |
| T-CHAN-03 | send 鏃?approval_token 鈫?CHANNEL_SEND_UNAUTHORIZED |
| T-CHAN-04 | send 鏃?intent_id 鈫?CHANNEL_INTENT_MISSING |
| T-CHAN-05 | 缂?thread_key 鈫?CHANNEL_THREAD_KEY_MISSING |
| T-CHAN-AUDIT-01 | outbound 浜嬩欢鍙湪瀹¤閾捐拷婧埌 thread_key + request_id |
| T-CHAN-AUDIT-02 | inbound 浜嬩欢鍙湪瀹¤閾捐拷婧埌 thread_key + request_id |
| T-CHAN-AUDIT-03 | 澶辫触浜嬩欢鍚?reason_code + stage锛屽彲鍥炴斁鍒?intent_id |

---

### 搂27.2 M14-B VTS 澶栨樉纭畾鎬ф槧灏勶紙FR-AVATAR-001锛?
#### 27.2.1 妯″潡杈圭晫

```
edge_client/actuators/
  vts_expression_map.py   鈫?VTSExpressionMap锛圢EW锛?  vts_driver.py           鈫?寮哄寲锛氭帴鍏?VTSExpressionMap锛堝凡鏈夛級
host_brain/core/
  errors.py               鈫?鏂板 VTS_* 閿欒鐮?tests/unit/
  test_vts_expression_map.py  鈫?T-VTS-01~05
```

#### 27.2.2 VTSExpressionMap Schema

```python
from enum import Enum
from dataclasses import dataclass

class EmotionTag(str, Enum):
    """鍥哄畾鏋氫妇 emotion_tag锛堜笉鍏佽鑷敱鎵╁睍锛?""
    NEUTRAL   = "neutral"
    HAPPY     = "happy"
    SAD       = "sad"
    ANGRY     = "angry"
    SURPRISED = "surprised"
    FEARFUL   = "fearful"
    DISGUSTED = "disgusted"
    EXCITED   = "excited"
    CALM      = "calm"

# 鍥哄畾鏄犲皠琛紙鍚岀増鏈悓杈撳叆杈撳嚭涓€鑷达級
EXPRESSION_MAP: dict[EmotionTag, str] = {
    EmotionTag.NEUTRAL:   "exp_neutral_01",
    EmotionTag.HAPPY:     "exp_happy_01",
    EmotionTag.SAD:       "exp_sad_01",
    EmotionTag.ANGRY:     "exp_angry_01",
    EmotionTag.SURPRISED: "exp_surprised_01",
    EmotionTag.FEARFUL:   "exp_fearful_01",
    EmotionTag.DISGUSTED: "exp_disgusted_01",
    EmotionTag.EXCITED:   "exp_excited_01",
    EmotionTag.CALM:      "exp_calm_01",
}

@dataclass(frozen=True)
class ExpressionTrigger:
    emotion_tag: EmotionTag
    expression_id: str                  # 鏉ヨ嚜 EXPRESSION_MAP
    intensity: float                    # 0.0~1.0
    duration_ms: int
    request_id: str
    ts_ms: int

class VTSExpressionMap:
    def resolve(
        self,
        emotion_tag: str,
        *,
        intensity: float = 1.0,
        duration_ms: int = 2000,
        request_id: str,
    ) -> ExpressionTrigger:
        """
        灏?emotion_tag 鏄犲皠涓?ExpressionTrigger銆?        - 闈炴硶 tag 鈫?VTS_TAG_UNKNOWN
        - 鑷敱鍔ㄤ綔鍙傛暟锛堥潪鏋氫妇鍊硷級鈫?VTS_EXPRESSION_INVALID
        - 鍚岀増鏈悓杈撳叆杈撳嚭涓€鑷达紙鍙洖鏀撅級
        """

    def list_tags(self) -> list[str]:
        """杩斿洖鎵€鏈夊悎娉?emotion_tag 鏋氫妇鍊笺€?""
```

#### 27.2.3 VTSDriver 寮哄寲瑕佺偣

- 鎺ュ叆 `VTSExpressionMap.resolve()`锛屼笉鍐嶆帴鍙楀師濮嬪瓧绗︿覆 tag
- 闈炴硶 tag/鑷敱鍔ㄤ綔鍙傛暟鍦?`resolve()` 闃舵鎷掔粷锛屼笉杩涘叆 VTS 璋冪敤
- 瑙﹀彂璁板綍鍐欏叆 `vts_trigger_log`锛堝惈 expression_id/intensity/request_id锛?
#### 27.2.4 閿欒鐮?
| 閿欒鐮?| 瑙﹀彂鏉′欢 |
|--------|---------|
| `VTS_TAG_UNKNOWN` | emotion_tag 涓嶅湪鏋氫妇涓?|
| `VTS_EXPRESSION_INVALID` | 鑷敱鍔ㄤ綔鍙傛暟鎴栭潪娉?expression_id |
| `VTS_INTENSITY_OUT_OF_RANGE` | intensity 涓嶅湪 0.0~1.0 |
| `VTS_DURATION_INVALID` | duration_ms 鈮?0 |

#### 27.2.5 娴嬭瘯缂栧彿

| 缂栧彿 | 鎻忚堪 |
|------|------|
| T-VTS-01 | resolve 鍚堟硶 tag 鈫?杩斿洖 ExpressionTrigger锛宔xpression_id 鍥哄畾 |
| T-VTS-02 | 闈炴硶 emotion_tag 鈫?VTS_TAG_UNKNOWN |
| T-VTS-03 | 鑷敱鍔ㄤ綔鍙傛暟锛堥潪鏋氫妇鍊硷級鈫?VTS_EXPRESSION_INVALID |
| T-VTS-04 | 鍚岀増鏈悓杈撳叆杈撳嚭涓€鑷达紙鍙洖鏀炬槧灏勮〃锛?|
| T-VTS-05 | list_tags 杩斿洖鎵€鏈夊悎娉曟灇涓惧€?|

---

### 搂27.3 M14-C MCP Provider 娌荤悊瀹屾暣楠屾敹锛團R-MCP-001锛?
#### 27.3.1 妯″潡杈圭晫

```
host_brain/tools/
  mcp_adapter.py          鈫?寮哄寲锛氬璁′覆鑱?+ AIRLOCK 鍑虹珯鏈€灏忓寲锛堝凡鏈夛級
tests/unit/
  test_mcp_governance.py  鈫?T-MCP-GOV-01~05
tests/integration/
  test_mcp_audit_chain.py 鈫?T-MCP-AUDIT-01~03
```

#### 27.3.2 MCP 璋冪敤瀹¤瀛楁

| 瀛楁 | 绫诲瀷 | 璇存槑 |
|------|------|------|
| `provider_id` | str | MCP provider 鍞竴鏍囪瘑 |
| `tool_id` | str | 宸ュ叿鍞竴鏍囪瘑 |
| `request_id` | str | 鍏宠仈 Host 璇锋眰 ID |
| `intent_id` | str | 鍏宠仈 intent |
| `evidence_ref` | str | 鎵ц璇佹嵁寮曠敤 |
| `access_level` | str | READ / WRITE |
| `approval_token_ref` | str \| None | WRITE 蹇呭～ |
| `airlock_sanitized` | bool | 鏄惁缁忚繃 AIRLOCK 鍑€鍖?|
| `ts_ms` | int | 鏃堕棿鎴?|

#### 27.3.3 AIRLOCK 鍑虹珯鏈€灏忓寲瑕佹眰

- 鍑虹珯 payload 蹇呴』缁?AIRLOCK 鍑€鍖栵紙绉婚櫎纭姸鎬佸師濮嬪€?瀹硶鍏ㄦ枃/瑙勫垯缁嗚妭锛?- 姣忔鍑虹珯鐢熸垚 `sanitization_report`锛坮emoved_fields/redactions/payload_digest锛?- 缂哄け `sanitization_report` 鈫?鎷掔粷鍑虹珯锛坄AIRLOCK_BLOCKED_EGRESS`锛?
#### 27.3.4 MCP ToolMeta 鍚屾鎺ュ彛

```python
class MCPAdapter:
    def sync_provider_tools(
        self,
        provider_id: str,
        *,
        provider_url: str,
    ) -> list[ToolMeta]:
        """
        鍚屾 MCP provider 鏆撮湶鐨勫伐鍏蜂负 ToolMeta 鍒楄〃銆?        - 浠呭悓姝ュ厓鏁版嵁锛屼笉鎵ц
        - 鎸?READ/WRITE 鍒嗙骇
        - 鍐欏叆 tool_sync_log锛堝璁★級
        """

    def execute_mcp_tool(
        self,
        tool_id: str,
        args: dict,
        *,
        request_id: str,
        intent_id: str,
        approval_token: str | None = None,
    ) -> dict:
        """
        鎵ц MCP 宸ュ叿锛堝繀椤荤粡 ToolBroker 鍗曞叆鍙ｏ級銆?        - WRITE 蹇呴』鏈?approval_token
        - 鍑虹珯缁?AIRLOCK 鍑€鍖?        - 鍐欏叆 mcp_call_log锛堝璁★級
        """
```

#### 27.3.5 閿欒鐮?
| 閿欒鐮?| 瑙﹀彂鏉′欢 |
|--------|---------|
| `MCP_WRITE_UNAUTHORIZED` | WRITE 宸ュ叿鏃?approval_token 鎴栨湭 APPROVE |
| `MCP_PROVIDER_SYNC_FAILED` | provider 鍏冩暟鎹悓姝ュけ璐?|
| `MCP_TOOL_NOT_FOUND` | tool_id 涓嶅湪宸插悓姝ュ垪琛?|
| `MCP_AUDIT_REF_MISSING` | 鎵ц鏃?evidence_ref |
| `AIRLOCK_BLOCKED_EGRESS` | 鍑虹珯缂?sanitization_report |

#### 27.3.6 娴嬭瘯缂栧彿

| 缂栧彿 | 鎻忚堪 |
|------|------|
| T-MCP-GOV-01 | sync_provider_tools 鎴愬姛锛岃繑鍥?ToolMeta 鍒楄〃锛堝惈 READ/WRITE 鍒嗙骇锛?|
| T-MCP-GOV-02 | WRITE 宸ュ叿鏃?approval_token 鈫?MCP_WRITE_UNAUTHORIZED |
| T-MCP-GOV-03 | WRITE 宸ュ叿鏈?APPROVE 鈫?MCP_WRITE_UNAUTHORIZED |
| T-MCP-GOV-04 | 鍑虹珯缂?sanitization_report 鈫?AIRLOCK_BLOCKED_EGRESS |
| T-MCP-GOV-05 | 鎵ц鏃?evidence_ref 鈫?MCP_AUDIT_REF_MISSING |
| T-MCP-AUDIT-01 | MCP 璋冪敤鍙拷婧埌 intent/governor/skill_exec 璁板綍 |
| T-MCP-AUDIT-02 | MCP WRITE 璋冪敤鍚?approval_token_ref + evidence_ref |
| T-MCP-AUDIT-03 | AIRLOCK 鍑€鍖栨姤鍛婂彲鍥炴斁锛坮emoved_fields/payload_digest锛?|

---

### 搂27.4 M14-D 鍙戝竷娓呭崟宸ョ▼濂戠害

#### 27.4.1 鍙戝竷娓呭崟 Schema

```json
{
  "milestone": "M14",
  "release_date": "TBD",
  "release_ready": false,
  "milestones": {
    "M14-A": "PLANNED",
    "M14-B": "PLANNED",
    "M14-C": "PLANNED",
    "M14-D": "PLANNED"
  },
  "core_files": {
    "host_brain/channels/wechat_channel.py": "",
    "edge_client/actuators/vts_expression_map.py": "",
    "edge_client/actuators/vts_driver.py": "",
    "host_brain/tools/mcp_adapter.py": ""
  },
  "test_summary": {
    "m14_tests_passed": 0,
    "total_tests_passed": 0,
    "skipped": 0,
    "failed": 0
  },
  "quality_gates": {
    "all_core_files_present": false,
    "zero_regressions": false,
    "audit_chain_verified": false
  }
}
```

#### 27.4.2 璐ㄩ噺闂ㄧ锛圡14 楠屾敹鏍囧噯锛?
| 闂ㄧ椤?| 瑕佹眰 |
|--------|------|
| `release_ready` | 蹇呴』涓?true |
| `all_core_files_present` | 4 涓牳蹇冩枃浠?hash 宸插～鍐?|
| `zero_regressions` | failed=0 |
| `total_tests_passed` | 鈮?1370 |
| `audit_chain_verified` | 閫氶亾/澶栨樉/MCP 瀹¤閾惧瓧娈靛彲杩芥函 |

---

## 搂28 M15 宸ョ▼濂戠害锛堣闊抽摼璺繁鍖栧畬鏁撮獙鏀讹級

### 搂28.1 M15-A ASR 浜嬩欢鍖栦笂閫佸己鍖栵紙FR-VOICE-001锛?
#### 28.1.1 妯″潡杈圭晫

```
edge_client/senses/
  asr.py                  鈫?ASREventEmitter 寮哄寲锛堝凡鏈夛紝闇€琛?alternates + evidence_ref锛?tests/unit/
  test_asr_event_emitter.py  鈫?T-ASR-EVT-01~05锛圢EW锛?```

#### 28.1.2 ASRTranscriptEvent Schema锛堝己鍖栵級

```python
@dataclass(frozen=True)
class ASRTranscriptEvent:
    event_id: str
    session_id: str
    segment_id: str
    text: str
    confidence: float               # 0.0~1.0
    ts_start_ms: int
    ts_end_ms: int
    is_final: bool
    alternates: list[str]           # N-best锛堝彲閫夛紝鏈€澶?3 涓級
    evidence_ref: str               # 瀹¤閾惧紩鐢紙蹇呭～锛?    low_confidence: bool            # confidence < 0.6 鏃朵负 True
    reason_code: str | None         # 浣庣疆淇″害鏃跺～ ASR_LOW_CONFIDENCE
```

#### 28.1.3 鍏抽敭绾︽潫

- 浣庣疆淇″害锛坈onfidence < 0.6锛夊繀椤绘樉寮忔爣娉?`low_confidence=True` + `reason_code=ASR_LOW_CONFIDENCE`
- 姣忎釜浜嬩欢蹇呴』鎼哄甫 `evidence_ref`锛堝彲杩芥函锛?- `alternates` 鏈€澶?3 涓紙闃叉杩囧害鏆撮湶锛?- 娴佸紡/鍒嗘鍧囬€傜敤鍚屼竴 Schema

#### 28.1.4 娴嬭瘯缂栧彿

| 缂栧彿 | 鎻忚堪 |
|------|------|
| T-ASR-EVT-01 | 姝ｅ父杞啓浜嬩欢锛宑onfidence 鈮?0.6锛宭ow_confidence=False |
| T-ASR-EVT-02 | 浣庣疆淇″害锛宑onfidence < 0.6锛宭ow_confidence=True锛宺eason_code=ASR_LOW_CONFIDENCE |
| T-ASR-EVT-03 | alternates 鏈€澶?3 涓紙杈圭晫锛? 涓椂鎴柇锛?|
| T-ASR-EVT-04 | evidence_ref 蹇呭～锛堢己澶辨椂鎷掔粷鏋勫缓锛?|
| T-ASR-EVT-05 | is_final=True 鏃朵簨浠跺彲鍥炴斁锛堝惈 ts_start_ms/ts_end_ms锛?|

---

### 搂28.2 M15-B TTS 鎾姤闃熷垪寮哄寲锛團R-VOICE-002锛?
#### 28.2.1 妯″潡杈圭晫

```
edge_client/actuators/
  tts_engine.py           鈫?TTSPlaybackQueue 寮哄寲锛堝凡鏈夛紝闇€琛?request_id 鍏宠仈 + 澶辫触 reason_code锛?tests/unit/
  test_tts_playback_queue.py  鈫?T-TTS-QUEUE-01~05锛圢EW锛?```

#### 28.2.2 TTSPlaybackEvent Schema锛堝己鍖栵級

```python
@dataclass(frozen=True)
class TTSPlaybackEvent:
    event_id: str
    request_id: str                 # 鍏宠仈 Host 璇锋眰 ID锛堝繀濉級
    intent_id: str | None           # 鍏宠仈 intent锛堝彲閫夛級
    text: str                       # 鏈€缁堝彛寰勬枃鏈紙Host QA 鍚庯級
    emotion_tag: str                # 鎯呯华鏍囩锛堟潵鑷?EmotionTag 鏋氫妇锛?    status: str                     # "queued" | "playing" | "done" | "interrupted" | "failed"
    reason_code: str | None         # 澶辫触鏃跺繀濉?    stage: str | None               # 澶辫触闃舵
    ts_ms: int
    evidence_ref: str               # 瀹¤閾惧紩鐢?```

#### 28.2.3 鍏抽敭绾︽潫

- Host 鍙笅鍙戞渶缁堝彛寰勬枃鏈笌鎯呯华鏍囩锛堜笉鍚墽琛屼簨瀹炲鍛婏級
- 鎾姤浜嬩欢蹇呴』鍏宠仈 `request_id`锛堝彲杩芥函锛?- 澶辫触蹇呴』杩斿洖 `reason_code` + `stage`锛圱TS_QUEUE_FULL / TTS_SYNTH_FAILED / TTS_PLAYBACK_FAILED锛?- 涓嶅緱浜х敓浼墽琛屽鍛?
#### 28.2.4 娴嬭瘯缂栧彿

| 缂栧彿 | 鎻忚堪 |
|------|------|
| T-TTS-QUEUE-01 | 姝ｅ父鍏ラ槦锛宻tatus=queued锛宔vidence_ref 瀛樺湪 |
| T-TTS-QUEUE-02 | 鎾姤瀹屾垚锛宻tatus=done锛宺equest_id 鍙拷婧?|
| T-TTS-QUEUE-03 | 闃熷垪婊★紝status=failed锛宺eason_code=TTS_QUEUE_FULL |
| T-TTS-QUEUE-04 | 鍚堟垚澶辫触锛宻tatus=failed锛宺eason_code=TTS_SYNTH_FAILED |
| T-TTS-QUEUE-05 | 缂?request_id 鈫?鎷掔粷鍏ラ槦锛圴OICE_SCHEMA_INVALID锛?|

---

### 搂28.3 M15-C Barge-in 鍙墦鏂己鍖栵紙FR-VOICE-003锛?
#### 28.3.1 妯″潡杈圭晫

```
edge_client/actuators/
  tts_engine.py           鈫?interrupt() 鏂规硶寮哄寲锛堝凡鏈夛紝闇€琛ュ璁¤褰曪級
tests/unit/
  test_barge_in.py        鈫?T-BARGE-01~04锛圢EW锛?```

#### 28.3.2 BargeInEvent Schema

```python
@dataclass(frozen=True)
class BargeInEvent:
    event_id: str
    session_id: str
    interrupted_request_id: str     # 琚墦鏂殑鎾姤 request_id
    trigger_ts_ms: int              # 鎵撴柇瑙﹀彂鏃堕棿
    asr_continued: bool             # ASR 鏄惁缁х画涓婇€?    evidence_ref: str               # 瀹¤閾惧紩鐢?```

#### 28.3.3 鍏抽敭绾︽潫

- 鎵撴柇绔嬪嵆鐢熸晥锛堜笉绛夊緟褰撳墠鍙ュ瓙鎾畬锛?- 鎵撴柇涓嶆瀯鎴愪换浣曟墽琛屼簨瀹炴垨鍐欏叆浜嬪疄
- ASR 涓嶈闃诲锛堟墦鏂悗 ASR 缁х画涓婇€侊級
- 鎵撴柇浜嬩欢鍐欏叆瀹¤閾撅紙barge_in_log锛?
#### 28.3.4 娴嬭瘯缂栧彿

| 缂栧彿 | 鎻忚堪 |
|------|------|
| T-BARGE-01 | 鎵撴柇绔嬪嵆鐢熸晥锛宨nterrupted_request_id 鍙拷婧?|
| T-BARGE-02 | 鎵撴柇鍚?ASR 缁х画涓婇€侊紙asr_continued=True锛?|
| T-BARGE-03 | 鎵撴柇涓嶄骇鐢熸墽琛屼簨瀹烇紙鏃?intent_draft锛?|
| T-BARGE-04 | 鎵撴柇浜嬩欢鍐欏叆瀹¤閾撅紙evidence_ref 瀛樺湪锛?|

---

### 搂28.4 M15-D Edge 鍏滃簳灏忔ā鍨嬮棬鎺э紙FR-VOICE-004锛?
#### 28.4.1 妯″潡杈圭晫

```
edge_client/
  fallback_model.py       鈫?FallbackModelGate锛圢EW锛?tests/unit/
  test_fallback_model_gate.py  鈫?T-FALLBACK-01~05锛圢EW锛?```

#### 28.4.2 FallbackModelGate 鎺ュ彛

```python
@dataclass(frozen=True)
class FallbackOutput:
    output_id: str
    text: str
    draft_only: bool = True         # 姘歌繙涓?True锛堜笉寰楀寘鍚鍐虫垨鎵ц浜嬪疄锛?    reason_code: str | None = None  # 涓嶅彲鐢ㄦ椂濉?FALLBACK_MODEL_UNAVAILABLE
    stage: str | None = None
    evidence_ref: str | None = None

class FallbackModelGate:
    def generate(
        self,
        prompt: str,
        *,
        task_type: str,             # "scene_response" | "input_clean" | "summary" | "retrieval_hint"
        request_id: str,
        ts_ms: int | None = None,
    ) -> FallbackOutput:
        """
        璋冪敤鍏滃簳灏忔ā鍨嬶紙Ollama 3B 閲忓寲锛夈€?        - 杈撳嚭姘歌繙鏍囪涓?draft_only=True
        - 涓嶅彲鐢ㄦ椂蹇€熷け璐ワ紙FALLBACK_MODEL_UNAVAILABLE锛?        - 涓嶅緱鍖呭惈瑁佸喅鎴栨墽琛屼簨瀹炲鍛?        """

    def is_available(self) -> bool:
        """妫€鏌ュ厹搴曟ā鍨嬫槸鍚﹀彲鐢紙宸插姞杞斤級銆?""

    def load(self) -> bool:
        """鎸夐渶鍔犺浇鍏滃簳妯″瀷锛堥粯璁ゅ嵏杞斤級銆?""

    def unload(self) -> None:
        """鍗歌浇鍏滃簳妯″瀷锛堥噴鏀捐祫婧愶級銆?""
```

#### 28.4.3 鍏抽敭绾︽潫

- 榛樿鍗歌浇锛屾寜闇€鍔犺浇锛堜笉甯搁┗ GPU锛?- 杈撳嚭姘歌繙鏍囪涓?`draft_only=True`
- 涓嶅彲鐢ㄦ椂蹇€熷け璐ワ紙涓嶉樆濉炰富娴佺▼锛?- 涓嶅緱鍖呭惈瑁佸喅鎴栨墽琛屼簨瀹炲鍛?
#### 28.4.4 娴嬭瘯缂栧彿

| 缂栧彿 | 鎻忚堪 |
|------|------|
| T-FALLBACK-01 | generate 鎴愬姛锛宒raft_only=True |
| T-FALLBACK-02 | 涓嶅彲鐢ㄦ椂蹇€熷け璐ワ紝reason_code=FALLBACK_MODEL_UNAVAILABLE |
| T-FALLBACK-03 | 杈撳嚭涓嶅惈瑁佸喅/鎵ц浜嬪疄瀹ｅ憡锛圦A 鏂█锛?|
| T-FALLBACK-04 | load/unload 鐢熷懡鍛ㄦ湡姝ｇ‘ |
| T-FALLBACK-05 | task_type 闈炴硶 鈫?VOICE_SCHEMA_INVALID |

---

### 搂28.5 M15-E Edge 璧勬簮闂搁棬寮哄寲锛團R-VOICE-005锛?
#### 28.5.1 妯″潡杈圭晫

```
edge_client/
  resource_gate.py        鈫?EdgeResourceGate 寮哄寲锛堝凡鏈夛紝闇€琛ヤ紭鍏堢骇鎶㈠崰瀹¤锛?tests/unit/
  test_edge_resource_gate.py  鈫?T-RGATE-01~05锛圢EW锛?```

#### 28.5.2 ResourcePreemptionEvent Schema

```python
@dataclass(frozen=True)
class ResourcePreemptionEvent:
    event_id: str
    preemptor: str                  # 鎶㈠崰鑰咃紙"ASR" | "PLAYBACK" | "TTS" | "FALLBACK_LLM"锛?    preempted: str                  # 琚姠鍗犺€?    reason_code: str                # RESOURCE_GATE_PREEMPTED
    ts_ms: int
    evidence_ref: str
```

#### 28.5.3 浼樺厛绾ц鍒欙紙鍥哄畾锛?
```
ASR > PLAYBACK > TTS_SYNTH > FALLBACK_LLM
```

#### 28.5.4 鍏抽敭绾︽潫

- 鍘嬪姏涓嬩粛淇濊瘉 ASR锛堟渶楂樹紭鍏堢骇锛?- 璧勬簮涓嶈冻鏃堕檷绾ц涓哄彲瑙傛祴锛圧esourcePreemptionEvent 鍐欏叆瀹¤閾撅級
- 鎶㈠崰浜嬩欢鍚?preemptor/preempted/reason_code

#### 28.5.5 娴嬭瘯缂栧彿

| 缂栧彿 | 鎻忚堪 |
|------|------|
| T-RGATE-01 | ASR 鎶㈠崰 TTS_SYNTH锛孯esourcePreemptionEvent 鍐欏叆瀹¤閾?|
| T-RGATE-02 | ASR 鎶㈠崰 FALLBACK_LLM锛宺eason_code=RESOURCE_GATE_PREEMPTED |
| T-RGATE-03 | PLAYBACK 鎶㈠崰 TTS_SYNTH锛圓SR 涓嶅湪杩愯鏃讹級 |
| T-RGATE-04 | 鍘嬪姏涓?ASR 濮嬬粓淇濊瘉锛堜笉琚换浣曚换鍔℃姠鍗狅級 |
| T-RGATE-05 | 鎶㈠崰浜嬩欢鍚?preemptor/preempted/evidence_ref |

---

### 搂28.6 M15 鍙戝竷娓呭崟宸ョ▼濂戠害

#### 28.6.1 鍙戝竷娓呭崟 Schema

```json
{
  "milestone": "M15",
  "release_date": "TBD",
  "release_ready": false,
  "milestones": {
    "M15-A": "PLANNED",
    "M15-B": "PLANNED",
    "M15-C": "PLANNED",
    "M15-D": "PLANNED",
    "M15-E": "PLANNED",
    "M15-F": "PLANNED"
  },
  "core_files": {
    "edge_client/senses/asr.py": "",
    "edge_client/actuators/tts_engine.py": "",
    "edge_client/fallback_model.py": "",
    "edge_client/resource_gate.py": ""
  },
  "test_summary": {
    "m15_tests_passed": 0,
    "total_tests_passed": 0,
    "skipped": 0,
    "failed": 0
  },
  "quality_gates": {
    "all_core_files_present": false,
    "zero_regressions": false,
    "audit_chain_verified": false
  }
}
```

#### 28.6.2 璐ㄩ噺闂ㄧ锛圡15 楠屾敹鏍囧噯锛?
| 闂ㄧ椤?| 瑕佹眰 |
|--------|------|
| `release_ready` | 蹇呴』涓?true |
| `all_core_files_present` | 4 涓牳蹇冩枃浠?hash 宸插～鍐?|
| `zero_regressions` | failed=0 |
| `total_tests_passed` | 鈮?1445 |
| `audit_chain_verified` | ASR/TTS/Barge-in/鍏滃簳/璧勬簮闂搁棬瀹¤閾惧瓧娈靛彲杩芥函 |

---

## 29. M17/M18 鐩戞帶闈㈡澘 v3 宸ョ▼濂戠害锛堝墠绔?SPA + 鍚庣 API 鎵╁睍锛?
> 瀵瑰簲 PRD 搂4.20锛團R-OBS-003~011锛夈€?> 褰撳墠鐘舵€侊細DONE锛?026-03-04锛歊eact+Vite SPA 鍥涢〉闈㈠叏閲忓彲鐢紱26 涓柊澧炲悗绔祴璇曞叏閮ㄩ€氳繃锛?577 鍏ㄩ噺閫氳繃锛? skipped锛? failed锛夈€?
---

### 29.1 鎬讳綋瀹氫綅涓庣害鏉?
**鐩爣**锛氬皢鐜版湁 `panel.html` 鍗曢〉閲嶅缓涓轰笓涓氳繍缁寸洃鎺?SPA锛屽弬鑰?Dify 绠€绾﹂鏍硷紝瀹炵幇鍙岀鍏ㄦ湇鍔＄洃鎺?+ 鑺傜偣閾捐矾鍙鍖栧睍绀恒€?
**瑕嗙洊闇€姹?*锛欶R-OBS-003~011銆?**鍥炲綊鍙ｅ緞**锛歍-OBS-V3-ARCH-01~03銆乀-OBS-V3-OVW-01~05銆乀-OBS-V3-SESS-01~05銆乀-OBS-V3-TRACE-01~08銆乀-OBS-V3-METRICS-01~05銆乀-OBS-HEALTH-01~06銆乀-OBS-SESS-01~06銆乀-OBS-TRACE-01~08銆乀-OBS-TS-01~06銆?
**纭害鏉?*锛?- 鍓嶇浜у嚭鐗╋紙`host_brain/app/static/monitor/`锛夌敱 FastAPI StaticFiles 鎵樼锛屾棤闇€ Node.js 杩愯鏃躲€?- 鐢熶骇鏋勫缓锛歚npm run build` 浜у嚭鐗╂彁浜ゅ埌浠撳簱锛涘紑鍙戞ā寮忥細`npm run dev`锛圴ite dev server 浠ｇ悊 `/obs/*` 鍒?FastAPI锛夈€?- 璁捐椋庢牸锛欴ify 绠€绾︾櫧鑹查鏍硷紙鑳屾櫙 `#F9FAFB`锛屼富鑹?`#2970FF`锛屽崱鐗囧渾瑙?`8px`锛屽瓧浣?Inter锛夛紱**涓ユ牸绂佺敤澶ч潰绉珮楗卞拰搴︽笎鍙樹笌寤変环鍙戝厜闃村奖**锛?*绂佹浣跨敤 Emoji 浣滀负鍥炬爣**銆?
---

### 29.2 鎶€鏈爤绾﹀畾锛圡17 寮哄埗鎵ц锛?
| 鎶€鏈?| 鐗堟湰 | 鐢ㄩ€?|
|------|------|------|
| React | 18.x | 缁勪欢鍖栧紑鍙戯紝鎵€鏈夐〉闈㈠潎涓?React 缁勪欢 |
| Vite | 5.x | 蹇€熸瀯寤猴紱`npm run build` 浜у嚭闈欐€佹枃浠剁敱 FastAPI StaticFiles 鎵樼 |
| Tailwind CSS | 3.x | 鍘熷瓙鍖栨牱寮忥紱瀹炵幇 Dify 绠€绾︾櫧鑹查鏍?|
| React Router | 6.x | 4 涓牳蹇冮〉闈㈢殑 History/Hash 璺敱鍒囨崲 |
| React Flow | latest | Trace 鑺傜偣娴佸浘锛圖ify Workflow 鍚屾搴曞眰搴擄紝鍘熺敓鏀寔鑺傜偣鍗＄墖銆佹湁鍚戣繛绾裤€佸垎鏀?骞惰甯冨眬锛?|
| Apache ECharts | 5.x | Metrics 鏃跺簭鎶樼嚎鍥俱€佹煴鐘跺浘銆佹潯褰㈠浘 |
| Zustand | 4.x | 鍏ㄥ眬鐘舵€佺鐞嗭紙30s 杞鐘舵€?+ 鍏ㄥ眬鏃堕棿鑼冨洿杩囨护锛?|
| Lucide React | latest | 缁熶竴涓撲笟鐨勭函鑹?SVG 鍥炬爣 |

**椤圭洰缁撴瀯绾﹀畾锛堣涔夊浐瀹氾級**锛?- 婧愮爜鐩綍锛歚host_brain/app/monitor_ui/`锛圧eact 椤圭洰鏍癸紝鍚?`package.json` / `vite.config.js`锛?- 鏋勫缓浜у嚭锛歚host_brain/app/static/monitor/`锛團astAPI StaticFiles 鎵樼锛屽叆鍙ｄ负 `index.html`锛?
---

### 29.3 鍓嶇鏋舵瀯濂戠害锛圡17-A锛毬BS-V3-001锛?
瑕嗙洊闇€姹傦細FR-OBS-003銆?鍥炲綊鍙ｅ緞锛歍-OBS-V3-ARCH-01~03銆?
#### 29.3.1 鏍稿績鏂囦欢钀界偣锛堣涔夊浐瀹氾級

```
host_brain/app/monitor_ui/
  package.json                          鈫?渚濊禆澹版槑锛坮eact/vite/tailwind/react-router/reactflow/echarts/zustand/lucide-react锛?  vite.config.js                        鈫?鏋勫缓閰嶇疆锛堜骇鍑哄埌 ../static/monitor/锛?  tailwind.config.js                    鈫?Dify 璁捐 token
  src/
    main.jsx                            鈫?React 鍏ュ彛
    App.jsx                             鈫?鏍圭粍浠?+ React Router 璺敱閰嶇疆
    store/index.js                      鈫?Zustand store锛堣疆璇㈢姸鎬?+ 鏃堕棿鑼冨洿杩囨护锛?    components/
      Layout.jsx                        鈫?宸︿晶鍥哄畾瀵艰埅鏍?+ 椤堕儴闈㈠寘灞?+ 涓诲唴瀹瑰尯甯冨眬妗嗘灦
      trace/
        CustomNode.jsx                  鈫?鑷畾涔夎妭鐐瑰崱鐗囩粍浠讹紙React Flow锛?        NodeDetailPanel.jsx             鈫?鑺傜偣璇︽儏渚ц竟闈㈡澘
      charts/
        LineChart.jsx                   鈫?ECharts 鎶樼嚎鍥惧皝瑁咃紙鍙鐢級
        BarChart.jsx                    鈫?ECharts 鏌辩姸/鏉″舰鍥惧皝瑁咃紙鍙鐢級
    pages/
      Overview.jsx                      鈫?鍏ㄦ湇鍔″仴搴锋€昏椤?      Sessions.jsx                      鈫?浼氳瘽鍒楄〃椤?      Trace.jsx                         鈫?Dify 椋庢牸閾捐矾鍙鍖栭〉锛圧eact Flow锛?      Metrics.jsx                       鈫?鎸囨爣澶х洏椤碉紙ECharts锛?    utils/
      traceLayout.js                    鈫?Dagre 鑷姩甯冨眬宸ュ叿鍑芥暟
host_brain/app/static/monitor/          鈫?npm run build 浜у嚭锛堟彁浜ゅ埌浠撳簱锛?```

#### 29.3.2 璺敱濂戠害锛? 涓牳蹇冮〉闈級

| 璺敱璺緞 | 椤甸潰缁勪欢 | 鍔熻兘 |
|---------|---------|------|
| `/` 鎴?`/overview` | `Overview.jsx` | 鍏ㄦ湇鍔″仴搴锋€昏 |
| `/sessions` | `Sessions.jsx` | 浼氳瘽鍒楄〃 + 杩囨护 + 鍒嗛〉 |
| `/trace` | `Trace.jsx` | 閾捐矾鑺傜偣鍙鍖栵紙React Flow锛?|
| `/metrics` | `Metrics.jsx` | 鎸囨爣澶х洏锛圗Charts锛?|

#### 29.3.3 Zustand Store 濂戠害锛堟渶灏忓瓧娈碉級

```javascript
{
  // 杞鐘舵€?  pollingEnabled: boolean,          // 鏄惁寮€鍚?30s 鑷姩杞
  lastPolledAt: number | null,      // 涓婃杞鏃堕棿鎴筹紙ms锛?  pollingError: string | null,      // 杞閿欒淇℃伅

  // 鍏ㄥ眬鏃堕棿鑼冨洿杩囨护
  timeRange: '1h' | '6h' | '24h' | '7d',

  // 褰撳墠閫変腑鐨?request_id锛圫essions 鈫?Trace 璺宠浆浼犻€掞級
  selectedRequestId: string | null,
}
```

#### 29.3.4 楠屾敹娴嬭瘯缂栧彿

| 缂栧彿 | 鎻忚堪 |
|------|------|
| T-OBS-V3-ARCH-01 | `npm run dev` 鍚姩鏃犳姤閿欙紝鍥涗釜椤甸潰鍧囧彲閫氳繃璺敱鍒囨崲 |
| T-OBS-V3-ARCH-02 | `npm run build` 鎴愬姛浜у嚭锛孎astAPI 鍙甯告墭绠?|
| T-OBS-V3-ARCH-03 | 宸︿晶瀵艰埅楂樹寒褰撳墠椤碉紱1280脳800 鍒嗚鲸鐜囦笅甯冨眬姝ｅ父锛屾棤妯悜婊氬姩鏉?|

---

### 29.4 鍏ㄦ湇鍔″仴搴锋€昏椤靛绾︼紙M17-B锛毬BS-V3-002锛?
瑕嗙洊闇€姹傦細FR-OBS-004銆?鍥炲綊鍙ｅ緞锛歍-OBS-V3-OVW-01~05銆?
#### 29.4.1 椤甸潰鍔熻兘濂戠害

- 椤堕儴姹囨€荤粺璁¤锛氭€绘湇鍔℃暟 / 鍋ュ悍鏁?/ 闄嶇骇鏁?/ 鏁呴殰鏁帮紙涓庡崱鐗囨暟閲忎弗鏍间竴鑷达級銆?- 鏈嶅姟鍗＄墖缃戞牸锛氭瘡鍗＄墖鍚湇鍔″悕銆佷晶锛圚ost/Edge锛夈€佺姸鎬佸窘绔狅紙UP/DOWN/DEGRADED锛夈€佸搷搴斿欢杩熴€佹渶鍚庢鏌ユ椂闂淬€侀敊璇憳瑕侊紙DOWN 鏃跺睍绀猴級銆?- 30 绉掕嚜鍔ㄨ疆璇?`GET /obs/services/health`锛涜疆璇㈠け璐ユ椂鏄剧ず"涓婃宸茬煡鐘舵€?HH:MM:SS"锛屼笉宕╂簝銆?- DOWN 鐘舵€佸崱鐗囨樉绀虹孩鑹茶竟妗?+ 閿欒鎽樿锛汥EGRADED 鏄剧ず姗欒壊杈规銆?
#### 29.4.2 楠屾敹娴嬭瘯缂栧彿

| 缂栧彿 | 鎻忚堪 |
|------|------|
| T-OBS-V3-OVW-01 | 鎵€鏈夊凡娉ㄥ唽鏈嶅姟锛堚墺12 涓級鍧囧嚭鐜板湪鎬昏椤?|
| T-OBS-V3-OVW-02 | DOWN 鐘舵€佸崱鐗囨樉绀虹孩鑹茶竟妗?+ 閿欒鎽樿 |
| T-OBS-V3-OVW-03 | 杞澶辫触鏃舵樉绀?涓婃宸茬煡鐘舵€?HH:MM:SS"锛屼笉宕╂簝 |
| T-OBS-V3-OVW-04 | 椤堕儴姹囨€绘暟瀛椾笌鍗＄墖鏁伴噺涓€鑷?|
| T-OBS-V3-OVW-05 | `?side=host|edge` 杩囨护鍙傛暟姝ｇ‘鐢熸晥 |

---

### 29.5 浼氳瘽鍒楄〃椤靛绾︼紙M17-C锛毬BS-V3-003锛?
瑕嗙洊闇€姹傦細FR-OBS-005銆?鍥炲綊鍙ｅ緞锛歍-OBS-V3-SESS-01~05銆?
#### 29.5.1 椤甸潰鍔熻兘濂戠害

- 椤堕儴杩囨护鏍忥細鏃堕棿鑼冨洿閫夋嫨鍣?+ 鐘舵€佷笅鎷夛紙OK/REJECT/ERROR锛? 鍏抽敭璇嶆悳绱€?- 琛ㄦ牸鍒楋細request_id / 鏃堕棿 / 鐘舵€?/ 鑰楁椂 / 閫氶亾 / reason_code銆?- 搴曢儴鍒嗛〉鎺т欢锛堟瘡椤?20 鏉★級锛岀炕椤典笉涓㈠け杩囨护鏉′欢銆?- 鐐瑰嚮琛岃烦杞埌 Trace 椤靛苟浼犻€?request_id锛圲RL hash 鍙樹负 `#trace?rid=xxx`锛夈€?- 绌虹粨鏋滄椂鏄剧ず鍙嬪ソ绌虹姸鎬侊紙闈炴姤閿欙級銆?
#### 29.5.2 楠屾敹娴嬭瘯缂栧彿

| 缂栧彿 | 鎻忚堪 |
|------|------|
| T-OBS-V3-SESS-01 | 鍒楄〃姝ｅ父鍒嗛〉锛堟瘡椤?20 鏉★級锛岀炕椤典笉涓㈠け杩囨护鏉′欢 |
| T-OBS-V3-SESS-02 | 鐐瑰嚮琛屽悗 URL hash 鍙樹负 `#trace?rid=xxx`锛孴race 椤佃嚜鍔ㄥ姞杞?|
| T-OBS-V3-SESS-03 | 鐘舵€佽繃婊わ紙OK/REJECT/ERROR锛夋纭敓鏁?|
| T-OBS-V3-SESS-04 | 绌虹粨鏋滄椂鏄剧ず鍙嬪ソ绌虹姸鎬侊紙闈炴姤閿欙級 |
| T-OBS-V3-SESS-05 | total_count 涓庡疄闄呮暟鎹竴鑷?|

---

### 29.6 閾捐矾鍙鍖栭〉濂戠害锛圡17-D锛毬BS-V3-004锛?
瑕嗙洊闇€姹傦細FR-OBS-006銆?鍥炲綊鍙ｅ緞锛歍-OBS-V3-TRACE-01~08銆?
#### 29.6.1 椤甸潰鍔熻兘濂戠害

- React Flow 鐢诲竷锛氳嚜鍔ㄥ竷灞€锛堜粠宸﹀埌鍙筹紝Dagre 甯冨眬绠楁硶锛夛紝鏀寔鍒嗘敮/骞惰锛屾敮鎸佺缉鏀?骞崇Щ銆?- 鑷畾涔夎妭鐐瑰崱鐗囷紙CustomNode锛夛細鍦嗚鐭╁舰锛屽惈 Lucide React 鑺傜偣绫诲瀷鍥炬爣銆佽妭鐐瑰悕绉般€佺姸鎬侀鑹诧紙缁?绾?姗?钃濆姩鐢伙級銆佽€楁椂鏍囩銆丳rovider 寰界珷锛圠LM 鑺傜偣涓撳睘锛夈€?- 鏈夊悜杩炵嚎锛歊eact Flow 鍐呯疆 Edge锛屾敮鎸佸垎鏀悎骞讹紝绠ご鏍峰紡銆?- 鑺傜偣鐐瑰嚮渚ц竟闈㈡澘锛圢odeDetailPanel锛夛細灞曠ず input/output锛圝SON 鏍煎紡鍖?+ 鎶樺彔/灞曞紑锛夈€乻tatus銆乺eason_code銆佹椂闂存埑銆乻ide锛圚ost/Edge锛夈€乵odel_id锛圠LM 鑺傜偣锛夈€?- 椤堕儴宸ュ叿鏍忥細request_id 杈撳叆妗?+ 鏌ヨ鎸夐挳 + 鍏ㄥ睆鍒囨崲銆?- 瀵规帴 `GET /obs/trace/{request_id}` API銆?
#### 29.6.2 楠屾敹娴嬭瘯缂栧彿

| 缂栧彿 | 鎻忚堪 |
|------|------|
| T-OBS-V3-TRACE-01 | 缁欏畾 request_id 鍚庤妭鐐瑰浘姝ｇ‘娓叉煋锛堚墹50 鑺傜偣娴佺晠锛屾棤鍗￠】锛?|
| T-OBS-V3-TRACE-02 | 鐐瑰嚮鑺傜偣渚ц竟闈㈡澘姝ｇ‘鏄剧ず input/output |
| T-OBS-V3-TRACE-03 | 骞惰鑺傜偣浠ュ垎鏀舰寮忓睍绀猴紙涓嶉噸鍙狅紝Dagre 甯冨眬姝ｇ‘锛?|
| T-OBS-V3-TRACE-04 | JSON 鎶樺彔/灞曞紑鍔熻兘姝ｅ父 |
| T-OBS-V3-TRACE-05 | 鍏ㄥ睆妯″紡鍙敤 |
| T-OBS-V3-TRACE-06 | 绌洪摼璺椂鏄剧ず鍙嬪ソ绌虹姸鎬?|
| T-OBS-V3-TRACE-07 | LLM 鑺傜偣鍗＄墖鏄剧ず Provider 寰界珷锛圤penAI/Gemini/DeepSeek 棰滆壊鍖哄垎锛?|
| T-OBS-V3-TRACE-08 | input/output 瓒?64KB 鏃?truncated=true 鏍囨敞瀛樺湪 |

---

### 29.7 鎸囨爣澶х洏椤靛绾︼紙M17-E锛毬BS-V3-005锛?
瑕嗙洊闇€姹傦細FR-OBS-007銆?鍥炲綊鍙ｅ緞锛歍-OBS-V3-METRICS-01~05銆?
#### 29.7.1 椤甸潰鍔熻兘濂戠害

- 鏃堕棿鑼冨洿閫夋嫨鍣紙1h / 6h / 24h / 7d锛孼ustand 鐘舵€侀┍鍔級銆?- 璇锋眰閲忚秼鍔挎姌绾垮浘锛圗Charts Canvas锛夈€?- 鎷掔粷鐜囪秼鍔挎姌绾垮浘锛圗Charts Canvas锛夈€?- reason_code 鍒嗗竷妯悜鏉″舰鍥撅紙ECharts Canvas锛夈€?- 鍚勬湇鍔″搷搴斿欢杩?P50/P95 鏌辩姸鍥撅紙ECharts Canvas锛夈€?- 瀵规帴 `GET /obs/metrics/timeseries` API銆?
#### 29.7.2 楠屾敹娴嬭瘯缂栧彿

| 缂栧彿 | 鎻忚堪 |
|------|------|
| T-OBS-V3-METRICS-01 | 鍒囨崲鏃堕棿鑼冨洿鍚庡浘琛ㄦ暟鎹纭洿鏂帮紙Zustand 鐘舵€侀┍鍔級 |
| T-OBS-V3-METRICS-02 | 鍥捐〃鍦?HiDPI 灞忓箷涓嬫竻鏅帮紙ECharts 鍐呯疆 devicePixelRatio 鏀寔锛?|
| T-OBS-V3-METRICS-03 | 鏃犳暟鎹椂鏄剧ず"鏁版嵁閲囬泦涓?鍗犱綅锛屼笉宕╂簝 |
| T-OBS-V3-METRICS-04 | 鍥涚鍥捐〃绫诲瀷鍧囨纭覆鏌?|
| T-OBS-V3-METRICS-05 | 闄嶉噰鏍峰悗鏁版嵁鐐规暟閲?鈮?000 |

---

### 29.8 鍚庣 API 鎵╁睍濂戠害锛圡18锛毬BS-V3-API-001~004锛?
瑕嗙洊闇€姹傦細FR-OBS-008~011銆?鍥炲綊鍙ｅ緞锛歍-OBS-HEALTH-01~06銆乀-OBS-SESS-01~06銆乀-OBS-TRACE-01~08銆乀-OBS-TS-01~06銆?
#### 29.8.1 鍏ㄦ湇鍔″仴搴疯仛鍚?API锛埪BS-V3-API-001锛?
**绔偣**锛歚GET /obs/services/health`

**鍔熻兘**锛氬苟鍙戞帰娴嬪弻绔墍鏈夊凡娉ㄥ唽鏈嶅姟鍋ュ悍鐘舵€侊紱鍗曟湇鍔¤秴鏃讹紙鈮?00ms锛変笉闃诲鍏朵粬鏈嶅姟锛涙敮鎸?`?side=host|edge` 杩囨护銆?
**宸叉敞鍐屾湇鍔★紙鏈€灏忛泦锛?*锛?- Host 渚э紙8 涓級锛歀LM Gateway銆丯eo4j銆丼QLite銆丆hroma銆丼cheduler銆丅ond Engine銆丮emory Distiller銆丮CP Provider
- Edge 渚э紙5 涓級锛欰SR銆乀TS銆乂TS銆丄udio Playback銆丒dge RPC

**鍝嶅簲缁撴瀯锛堟渶灏忓瓧娈碉紝璇箟鍥哄畾锛?*锛?```json
{
  "ts_ms": 1709500000000,
  "total": 13,
  "healthy": 12,
  "degraded": 1,
  "down": 0,
  "services": [
    {
      "service_id": "neo4j",
      "service_name": "Neo4j Bond SSOT",
      "side": "host",
      "status": "UP",
      "latency_ms": 12,
      "last_checked_ts_ms": 1709500000000,
      "error_summary": null
    }
  ]
}
```

**绾︽潫**锛?- 鍝嶅簲鏃堕棿 鈮?00ms锛堝苟鍙戞帰娴嬶級銆?- 鍝嶅簲缁撴瀯绋冲畾锛堝瓧娈典笉闅忔湇鍔＄姸鎬佸彉鍖栬€岀己澶憋級銆?- 鍗曟湇鍔¤秴鏃舵椂璇ユ湇鍔?`status=DOWN`锛屽叾浣欐甯歌繑鍥炪€?
**钀界偣**锛?- `host_brain/app/obs_routes.py`锛堟柊澧炵鐐癸級
- `host_brain/obs/service_health_aggregator.py`锛圫erviceHealthAggregator锛?- `tests/unit/test_service_health_aggregator.py`锛圱-OBS-HEALTH-01~06锛?
#### 29.8.2 浼氳瘽鍒楄〃 API锛埪BS-V3-API-002锛?
**绔偣**锛歚GET /obs/sessions`

**鏌ヨ鍙傛暟**锛歚start_time`銆乣end_time`銆乣status`銆乣channel`銆乣keyword`銆乣page`銆乣page_size`

**鍝嶅簲缁撴瀯锛堟渶灏忓瓧娈碉級**锛?```json
{
  "total_count": 100,
  "page": 1,
  "page_size": 20,
  "results": [
    {
      "request_id": "01HXXX",
      "ts_ms": 1709500000000,
      "status": "APPROVE",
      "duration_ms": 1234,
      "channel": "wechat",
      "reason_code": null
    }
  ]
}
```

**绾︽潫**锛?- 绌虹粨鏋滄椂杩斿洖 `results=[]` 鑰岄潪 404銆?- `total_count` 涓庡疄闄呮暟鎹竴鑷淬€?- 杩囨护鍙傛暟缁勫悎姝ｇ‘鐢熸晥銆?
**钀界偣**锛?- `host_brain/app/obs_routes.py`锛堟柊澧炵鐐癸級
- `host_brain/obs/session_query.py`锛圫essionQueryService锛?- `host_brain/audit/migrations/0019_obs_sessions_index.sql`锛堟煡璇㈢储寮曪級
- `tests/unit/test_session_query.py`锛圱-OBS-SESS-01~06锛?
#### 29.8.3 閾捐矾鑺傜偣璇︽儏 API锛埪BS-V3-API-003锛?
**绔偣**锛歚GET /obs/trace/{request_id}`

**鏌ヨ鍙傛暟**锛歚include_io`锛堥粯璁?true锛沠alse 鏃朵笉杩斿洖 input/output 瀛楁锛?
**鑺傜偣缁撴瀯锛堟渶灏忓瓧娈碉紝璇箟鍥哄畾锛?*锛?```json
{
  "request_id": "01HXXX",
  "nodes": [
    {
      "node_id": "node_001",
      "node_type": "LLM",
      "node_name": "core_reasoner_primary",
      "status": "SUCCESS",
      "start_ts_ms": 1709500000000,
      "end_ts_ms": 1709500001500,
      "duration_ms": 1500,
      "input": {},
      "output": {},
      "truncated": false,
      "reason_code": null,
      "parent_node_id": null,
      "side": "host",
      "model_id": "gpt-4o"
    }
  ]
}
```

**绾︽潫**锛?- 涓嶅瓨鍦ㄧ殑 `request_id` 杩斿洖 404 + 绋冲畾閿欒缁撴瀯銆?- `input`/`output` 瓒?64KB 鎴柇骞舵爣娉?`truncated=true`銆?- `parent_node_id` 姝ｇ‘鍙嶆槧鎵ц鏍戠粨鏋勩€?- 鏁版嵁鏉ユ簮锛氫粠瀹¤閾撅紙intent_log / governor_log / skill_exec_log / asr_log / tts_log / vts_log 绛夛級鑱氬悎鏋勫缓銆?
**钀界偣**锛?- `host_brain/app/obs_routes.py`锛堟柊澧炵鐐癸級
- `host_brain/obs/trace_node_builder.py`锛圱raceNodeBuilder锛?- `tests/unit/test_trace_node_builder.py`锛圱-OBS-TRACE-01~08锛?
#### 29.8.4 鎸囨爣鏃跺簭 API锛埪BS-V3-API-004锛?
**绔偣**锛歚GET /obs/metrics/timeseries`

**鏌ヨ鍙傛暟**锛歚metrics`锛堥€楀彿鍒嗛殧锛屾敮鎸佹壒閲忥級銆乣interval`锛?m/5m/1h锛夈€乣start_time`銆乣end_time`

**鏀寔鐨勬寚鏍囩淮搴?*锛歚request_count`銆乣reject_rate`銆乣latency_p50`銆乣latency_p95`銆乣reason_code_dist`

**鍝嶅簲缁撴瀯锛堟渶灏忓瓧娈碉級**锛?```json
{
  "interval": "5m",
  "start_time": "2026-03-04T00:00:00Z",
  "end_time": "2026-03-04T06:00:00Z",
  "metrics": {
    "request_count": {
      "data_points": [
        {"ts_ms": 1709500000000, "value": 42}
      ]
    },
    "reject_rate": {
      "data_points": [
        {"ts_ms": 1709500000000, "value": 0.05}
      ]
    }
  }
}
```

**绾︽潫**锛?- 鏁版嵁鐐逛笂闄?1000锛堣秴鍑鸿嚜鍔ㄩ檷閲囨牱锛夈€?- 鏃犳暟鎹尯闂磋繑鍥?`null` 鍊肩偣淇濊瘉鏃跺簭杩炵画鎬с€?- 鎵归噺鏌ヨ杩斿洖 `{metrics: {metric_name: {data_points: [...]}}}` 缁撴瀯銆?
**钀界偣**锛?- `host_brain/app/obs_routes.py`锛堟柊澧炵鐐癸級
- `host_brain/obs/metrics_timeseries.py`锛圡etricsTimeseriesService锛?- `tests/unit/test_metrics_timeseries.py`锛圱-OBS-TS-01~06锛?
---

### 29.9 M17/M18 鏂板閿欒鐮佹眹鎬?
| 閿欒鐮?| 瑙﹀彂鍦烘櫙 |
|--------|---------|
| `OBS_SERVICE_HEALTH_TIMEOUT` | 鍗曟湇鍔″仴搴锋帰娴嬭秴鏃讹紙鈮?00ms 纭檺锛?|
| `OBS_SESSION_QUERY_INVALID` | 浼氳瘽鍒楄〃鏌ヨ鍙傛暟闈炴硶锛堟椂闂磋寖鍥?鍒嗛〉瓒婄晫锛?|
| `OBS_TRACE_NOT_FOUND` | 鎸囧畾 request_id 涓嶅瓨鍦ㄤ簬瀹¤閾?|
| `OBS_TRACE_IO_TRUNCATED` | 鑺傜偣 input/output 瓒?64KB 琚埅鏂紙闈為敊璇紝鏍囨敞瀛楁锛?|
| `OBS_METRICS_INTERVAL_INVALID` | 鏃跺簭鏌ヨ interval 鍙傛暟闈炴硶锛堥潪 1m/5m/1h锛?|
| `OBS_METRICS_DOWNSAMPLE_APPLIED` | 鏁版嵁鐐硅秴 1000 瑙﹀彂闄嶉噰鏍凤紙闈為敊璇紝鏍囨敞瀛楁锛?|

---

### 29.10 M17/M18 鍙戝竷娓呭崟璐ㄩ噺闂ㄧ

**M17 璐ㄩ噺闂ㄧ**锛?
| 闂ㄧ椤?| 瑕佹眰 |
|--------|------|
| `release_ready` | 蹇呴』涓?true |
| `all_pages_functional` | 鍥涢〉闈㈠叏閲忓彲鐢紙Overview/Sessions/Trace/Metrics锛?|
| `zero_js_errors` | 娴忚鍣ㄦ帶鍒跺彴鏃?JS 閿欒 |
| `design_review_passed` | 绗﹀悎 Dify 绠€绾︾櫧鑹查鏍?|

**M18 璐ㄩ噺闂ㄧ**锛?
| 闂ㄧ椤?| 瑕佹眰 |
|--------|------|
| `release_ready` | 蹇呴』涓?true |
| `all_api_endpoints_tested` | 4 涓柊澧炵鐐规祴璇曞叏閮ㄩ€氳繃 |
| `frontend_api_integration_verified` | 鍓嶇鍒囨崲鐪熷疄 API 楠岃瘉閫氳繃 |
| `zero_regressions` | failed=0 |
| `total_tests_passed` | 鈮?1575 |


