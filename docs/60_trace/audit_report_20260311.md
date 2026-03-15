# Natsume AI 娣卞害瀹¤鎶ュ憡

> 瀹¤鏃堕棿锛?026-03-11 09:45
> 瀹¤鑼冨洿锛氭剰鍥捐瘑鍒?路 妯″瀷缂栨帓鎺у埗 路 Agent 璺敱 路 SSE 鍙岀瀵归綈 路 鎯呮劅灞傛帴鍏?路 Bond 浼樺寲

---

## 鐩綍

1. [鎰忓浘璇嗗埆锛圛ntent Recognition锛塢(#涓€鎰忓浘璇嗗埆intent-recognition)
2. [妯″瀷缂栨帓鎺у埗锛圡odel Orchestration锛塢(#浜屾ā鍨嬬紪鎺掓帶鍒秏odel-orchestration)
3. [Agent 璺敱](#涓塧gent-璺敱)
4. [SSE 鍙岀瀵归綈](#鍥泂se-鍙岀瀵归綈)
5. [鎵€鏈夎矾鐢辩殑鎯呮劅灞傛帴鍏ユ儏鍐礭(#浜旀墍鏈夎矾鐢辩殑鎯呮劅灞傛帴鍏ユ儏鍐?
6. [鎯呮劅灞傚湪鏁翠釜绯荤粺鐨勮繛閫氭€(#鍏儏鎰熷眰鍦ㄦ暣涓郴缁熺殑杩為€氭€?
7. [Bond 浼樺寲鎯呭喌](#涓僢ond-浼樺寲鎯呭喌)
8. [姹囨€伙細闇€瑕佷慨澶嶇殑鍏抽敭闂](#鍏眹鎬婚渶瑕佷慨澶嶇殑鍏抽敭闂)

---

## 涓€銆佹剰鍥捐瘑鍒紙Intent Recognition锛?
### 鏋舵瀯灞傛

鎰忓浘璇嗗埆鍒嗕负**涓ゅ眰**锛?
#### 绗竴灞傦細杞婚噺瑙勫垯棰勫垎绫诲櫒锛坄pre_intent_classifier`锛?
```python
# graph_config_builder.py
def pre_intent_classifier(user_input: str) -> str:
    # 浼樺厛绾э細VISION_UI > WORK_CODE > WORK_TRANSLATE > EXEC_INTENT > CHAT
```

杩欐槸涓€涓函鍏抽敭璇嶅尮閰嶇殑瑙勫垯寮曟搸锛屽湪 Governor 閴存潈**涔嬪悗**鎵ц锛坄_unified_chat_sse_generator` 涓厛 Governor 鍐嶅垎绫伙級銆傚叧閿瘝闆嗗悎瑕嗙洊涓嫳鏃ヤ笁璇紝骞舵湁濞变箰鍏抽敭璇嶇櫧鍚嶅崟闃叉璇垽銆?
#### 绗簩灞傦細Governor 鎰忓浘鍐崇瓥寮曟搸锛坄IntentDecisionEngine`锛?
```python
# governor/decision.py
def evaluate_intent(self, raw_intent, ...) -> dict:
    # S_INTENT_PARSE 鈫?S_INTENT_VALIDATE 鈫?S_INTENT_REPLAY 鈫?S_INTENT_SEQ
    # 鈫?S_INTENT_EXPIRED 鈫?S_GOV_GATES 鈫?S_GOV_EMOTION_GATE 鈫?S_INTENT_OK
```

杩欐槸鐪熸鐨勬剰鍥鹃壌鏉冨眰锛屾墽琛?8 涓覆琛岄棬鎺э紙R1-R7 + R8 鎯呮劅闂ㄦ帶锛夛紝骞舵敮鎸侊細
- **M42 鍒嗗眰娌荤悊**锛歚registry` 娉ㄥ叆鍚庢寜 Skill 鐨?`risk_level` 閫夋嫨 `CACHE/RULE/AIRLOCK` 涓夐€氶亾
- **P2-A 蹇€熼€氶亾**锛歚narrative_only` + policy_cache 鍛戒腑鏃惰烦杩囧畬鏁?gate 璇勪及锛堣妭鐪?20-30ms锛?- **R8 鎯呮劅闂ㄦ帶**锛歚critical` 鐘舵€佷笅鎷掔粷 `TOOL_CALL/SKILL_EXEC`

### 闂娓呭崟

| # | 闂 | 浣嶇疆 | 涓ラ噸搴?|
|---|------|------|--------|
| 1 | `pre_intent_classifier` 鏄函鍏抽敭璇嶅尮閰嶏紝鏃犺涔夌悊瑙ｈ兘鍔涳紝"甯垜瀹夎涓€涓嬭繖棣栨瓕" 涔熶細鍛戒腑 `EXEC_INTENT`锛坄瀹夎` 鍦?`_PIC_EXEC_INTENT_KEYWORDS`锛?| `graph_config_builder.py` | 馃煛 涓嵄 |
| 2 | `intent_draft.action` 鍦ㄩ壌鏉冨墠鍥哄畾涓?`narrative_only`锛孏overnor 鏃犳硶鎰熺煡鐪熷疄鎰忓浘绫诲瀷锛孍XEC_INTENT 涔熶互 `narrative_only` 閫氳繃閴存潈锛岀粫杩囨儏鎰熼棬鎺?| `chat_handler.py:L47-60` | 馃煚 涓珮鍗?|
| 3 | `scene_type` 瀛楁鍙瀹㈡埛绔樉寮忚鐩栵紝鏃?Governor 鏍￠獙锛屽鎴风鍙己鍒惰矾鐢卞埌浠绘剰鍦烘櫙 | `chat_handler.py:L130-136` | 馃煚 涓珮鍗?|

---

## 浜屻€佹ā鍨嬬紪鎺掓帶鍒讹紙Model Orchestration锛?
### 鏋舵瀯

```
SCENE_CHAT锛堟棩甯稿璇濓級
  鈹斺攢 host_8b锛坬wen3:8b锛屾湰鍦?Ollama锛夆啋 token 绾ф祦寮?SSE
  鈹斺攢 [gaming 妯″紡] edge_3b锛圗dge RPC锛夆啋 鏈€灏忚矾寰?SSE

SCENE_WORK_CODE / WORK_TRANSLATE / EXEC_INTENT锛堝鏉傚満鏅級
  鈹斺攢 LangGraph 澶氳妭鐐癸紙Gemini 2.5 Pro/Flash锛夆啋 鑺傜偣绾ц繘搴?SSE
     core_reasoner_primary 鈫?[scene_router_post_core] 鈫?workbench_*/planner_constraints
     鈫?consistency_verifier 鈫?renderer_ja_c_style
```

**璺敱鐭╅樀锛坄resolve_host_llm_runtime`锛?* 瀹炵幇浜嗗畬鏁寸殑鐏板害鍙戝竷鏈哄埗锛?- `HOST_CHAT_FORCE_CLOUD` 寮哄埗浜戠
- `HOST_CHAT_HOST_8B_ENABLED` 鎺у埗鏈湴 8B 寮€鍏?- `gaming` 妯″紡寮哄埗璧?edge_3b
- SHA-256 绋冲畾妗剁伆搴︼紙0-99 鍒嗘《锛屾敮鎸佺敤鎴风骇鐏板害锛?- 鏈湴 8B 鍋ュ悍鎺㈡祴锛坄/v1/models`銆乣/health`銆乣/api/tags` 涓夎矾鎺㈡祴锛?
**鑺傜偣瓒呮椂鐭╅樀锛坄build_chat_graph_config`锛?*锛?- CHAT 蹇€熻矾寰勶細`chat_direct_ja` 鏈€浣?10s锛宍core_reasoner_primary` 鏈€楂?5s
- 澶嶆潅鍦烘櫙锛歚core_reasoner_primary` 12s锛宍workbench_*` 10s锛屾€婚绠?90-240s

### 闂娓呭崟

| # | 闂 | 浣嶇疆 | 涓ラ噸搴?|
|---|------|------|--------|
| 1 | `_resolve_host_8b_health_status` 鏄?*鍚屾 HTTP 鎺㈡祴**锛坄urllib.request.urlopen`锛夛紝鍦ㄤ簨浠跺惊鐜腑鐩存帴璋冪敤锛屼細闃诲浜嬩欢寰幆鏈€澶?3s | `chat_stream_handler.py:L430-440` | 馃敶 楂樺嵄 |
| 2 | `MainChain`锛坄main_chain.py`锛変腑鎵€鏈夎妭鐐瑰疄鐜板潎涓洪鏋讹紙`logger.debug` + `return True`锛夛紝`MainChain` 鍦?`dependencies.py` 涓凡鍒濆鍖栦絾**浠庢湭琚换浣曡矾鐢辫皟鐢?*锛屾暣涓富閾捐矾缂栨帓鍣ㄦ槸姝讳唬鐮?| `main_chain.py:L155-220` | 馃煚 涓珮鍗?|
| 3 | `RoutingMatrix`銆乣RecallPackBuilder`銆乣MemoryGuard`銆乣StateArbiter` 鍥涗釜鏂版ā鍧楀湪 `dependencies.py` 涓湁宸ュ巶鍑芥暟锛屼絾**娌℃湁浠讳綍璺敱鎴栧鐞嗗櫒璋冪敤瀹冧滑** | `dependencies.py:L340-447` | 馃煛 涓嵄 |

---

## 涓夈€丄gent 璺敱

### 璺敱鍐崇瓥閾?
```
POST /v1/chat
  鈫?handle_chat
    鈫?_unified_chat_sse_generator
      鈫?Governor.evaluate_intent锛堥壌鏉冿級
      鈫?pre_intent_classifier锛堝垎绫伙級
      鈫?SCENE_CHAT锛?          鈹溾攢 gaming + edge_enabled 鈫?_gaming_edge_chat_sse_generator
          鈹?    鈹斺攢 edge 澶辫触 鈫?_complex_scene_sse_generator锛坒allback锛?          鈹斺攢 鏅€?鈫?_sse_event_generator锛坱oken 绾ф祦寮忥級
      鈫?澶嶆潅鍦烘櫙 鈫?_complex_scene_sse_generator
            鈫?GraphRuntimeContract.run_graph锛圠angGraph锛?```

`SceneRouter`锛坄main_chain.py`锛夋槸鍙︿竴濂楄矾鐢辨満鍒讹紝浣嗗涓婃墍杩版槸姝讳唬鐮侊紝瀹為檯璺敱瀹屽叏鐢?`_unified_chat_sse_generator` 涓殑 `if scene_type == SCENE_CHAT` 鍒嗘敮鎺у埗銆?
### 闂娓呭崟

| # | 闂 | 浣嶇疆 | 涓ラ噸搴?|
|---|------|------|--------|
| 1 | 瀛樺湪**涓ゅ骞惰鐨勮矾鐢辨満鍒?*锛歚SceneRouter`锛坄main_chain.py`锛夊拰 `_unified_chat_sse_generator` 涓殑 if-else 鍒嗘敮锛屽墠鑰呮槸姝讳唬鐮侊紝鍚庤€呮槸瀹為檯鎵ц璺緞锛屾灦鏋勬贩涔?| `main_chain.py` vs `chat_handler.py` | 馃煚 涓珮鍗?|
| 2 | `SCENE_VISION_UI` 鍙鍒嗙被鍑烘潵锛屼絾 `_unified_chat_sse_generator` 涓病鏈夊搴斿鐞嗗垎鏀紝`resolve_scene_node_sequence("SCENE_VISION_UI")` 杩斿洖 `[CHAT_DIRECT_NODE_ID]`锛岃瑙夊満鏅疄闄呰蛋鏅€?chat 鑺傜偣 | `graph_config_builder.py:L310-325` | 馃煛 涓嵄 |

---

## 鍥涖€丼SE 鍙岀瀵归綈

### 浜嬩欢鍗忚

鎵€鏈夎矾寰勫潎閬靛惊缁熶竴鐨?SSE 浜嬩欢鍗忚锛?
```
PRESENCE(THINKING)
鈫?PRESENCE(GOV_APPROVED)
鈫?[PRESENCE(SCENE_ROUTED)]          # 澶嶆潅鍦烘櫙棰濆
鈫?[GRAPH_START]                     # 澶嶆潅鍦烘櫙棰濆
鈫?RENDER(chunk)*                    # 浠?token 绾ф祦寮?鈫?SENTENCE(sentence)*               # 浠?token 绾ф祦寮?鈫?[GRAPH_DONE]                      # 澶嶆潅鍦烘櫙棰濆
鈫?COMMIT(narrative, audit_refs)
```

**COMMIT 浜嬩欢鐨?`audit_refs` 缁撴瀯**鍦ㄤ笁鏉¤矾寰勪腑楂樺害涓€鑷达紝鍧囧寘鍚細
- `intent_id`銆乣retrieval`銆乣llm`锛堝惈 `route_decision`銆乣emotion_snapshot`锛?- `episodic_memory`銆乣trace_metrics`

### 闂娓呭崟

| # | 闂 | 浣嶇疆 | 涓ラ噸搴?|
|---|------|------|--------|
| 1 | `_sse_event_generator` 鍦?`_pre_approved_governor_result` 浼犲叆鏃惰烦杩?`GOV_APPROVED` 浜嬩欢锛岃嫢琚洿鎺ヨ皟鐢紙涓嶇粡杩?`_unified_chat_sse_generator`锛夛紝瀹㈡埛绔細鏀朵笉鍒?`GOV_APPROVED` 浜嬩欢 | `chat_stream_handler.py:L530-535` | 馃煛 涓嵄 |
| 2 | Gaming Edge 璺緞鐨?COMMIT 浜嬩欢缂哄皯 `prompt_context_validation` 鍜?`prompt_context_observability` 瀛楁锛堣涓虹┖ `{}`锛夛紝瀹㈡埛绔鏋滀緷璧栬繖浜涘瓧娈典細鍑虹幇 KeyError | `chat_handler_edge.py:L310-315` | 馃煝 浣庡嵄 |
| 3 | `_complex_scene_sse_generator` 鐨?COMMIT 浜嬩欢涓?`audit_refs` 娌℃湁椤跺眰 `vts` 瀛楁锛岃€?`_sse_event_generator` 鏈夛紝涓ゆ潯璺緞鐨?COMMIT 缁撴瀯涓嶅畬鍏ㄥ绉?| `chat_handler_complex.py:L430` vs `chat_stream_handler.py:L700` | 馃煝 浣庡嵄 |

---

## 浜斻€佹墍鏈夎矾鐢辩殑鎯呮劅灞傛帴鍏ユ儏鍐?
| 璺敱 | 鎯呮劅灞傛帴鍏?| 鎺ュ叆鏂瑰紡 | 瀹屾暣搴?|
|------|-----------|---------|--------|
| `POST /v1/chat`锛圫CENE_CHAT锛宼oken 娴佸紡锛?| 鉁?瀹屾暣 | `resolve_chat_emotion_context` 鈫?`build_chat_governance_snapshot` 鈫?Governor 鈫?`adjust_gen_params_for_emotion` 鈫?VTS 椹卞姩 鈫?`emotion_snapshot` 鍐欏叆 COMMIT | 鍏ㄩ摼璺?|
| `POST /v1/chat`锛堝鏉傚満鏅紝LangGraph锛?| 鉁?瀹屾暣 | 鍚屼笂 + `emotion_snapshot` 娉ㄥ叆 `graph_config` payload + Bond 鎯呮劅鍔犳潈 | 鍏ㄩ摼璺?|
| `POST /v1/chat`锛圙aming Edge锛?| 鈿狅笍 閮ㄥ垎 | `_build_edge_emotion_context_payload` 浼犵粰 Edge RPC锛屼絾**鏃?VTS 椹卞姩銆佹棤 Bond 鏇存柊銆佹棤 episodic 鍐欏叆** | 鏈€灏忚矾寰?|
| `POST /bond/event` | 鉂?鏃?| `BondEventRequest` 鏃?`emotion_tag`/`emotion_intensity` 瀛楁锛宍apply_event` 璋冪敤鏃?`emotion_intensity` 鍥哄畾涓洪粯璁ゅ€?0.5 | 缂哄け |
| `GET /bond/stage`銆乣GET /bond/summary` | N/A | 鍙鎺ュ彛 | 鈥?|
| `POST /v1/edge/events` | 鉂?鏃?| `receive_edge_events_response` 鏃犳儏鎰熶笂涓嬫枃澶勭悊 | 缂哄け |
| `GET /health`銆乣GET /ready`銆乣GET /metrics` | N/A | 鍩虹璁炬柦鎺ュ彛 | 鈥?|
| Canon/Scheduler/Tool/Obs 璺敱 | 鉂?鏃?| 绠＄悊绫绘帴鍙ｏ紝鏃犳儏鎰熷眰锛堣璁″姝わ級 | 鈥?|

---

## 鍏€佹儏鎰熷眰鍦ㄦ暣涓郴缁熺殑杩為€氭€?
### 杩為€氭€ф祦绋嬪浘

```mermaid
flowchart TD
    A["ChatRequest\nmood_state / emotion_tag / emotion_context"]

    A --> B["resolve_chat_emotion_context\nemotion_context.py"]

    B --> C["normalize_hard_state\nmetabolism.py"]
    C --> D["build_hard_snapshot\nHardStateSnapshot"]
    D --> D1["allowed_emotion_tags\nemotion_range_hint"]

    B --> E["build_chat_governance_snapshot"]
    E --> F["Governor.evaluate_intent"]

    F -->|"critical + TOOL_CALL"| G["鉂?REJECT\nEMOTION_GATE_BLOCKED"]
    F -->|"APPROVE"| H["emotion_context 浼犻€?]

    H --> I["adjust_gen_params_for_emotion\nllm_helpers.py"]
    I --> I1["temperature / top_p 鍔ㄦ€佽皟鏁?]

    H --> J["build_chat_emotion_snapshot\n娉ㄥ叆 prompt payload"]

    H --> K["_drive_stream_vts_emotion\n鈿狅笍 浠?SCENE_CHAT"]
    K --> K1["resolve_vts_expression\nchannels/vts.py"]
    K1 --> K2["16 绉嶆儏鎰?鈫?琛ㄦ儏 ID 鏄犲皠"]

    H --> L["BondDynamicsWriter.classify_interaction\nemotion_intensity 鍔犳潈"]
    L --> M["BondStageEngine.apply_event\n鈿狅笍 浠呭鏉傚満鏅?]
    M --> M1{"emotion_intensity > 0.7?"}
    M1 -->|"鏄?| M2["1.5x 绉垎鍔犳潈"]
    M1 -->|"鍚?| M3["姝ｅ父绉垎"]
    M2 --> N["BondScoreState"]
    M3 --> N
    N --> O["to_summary\nstyle_hint / repair_hint"]
    O --> P["PromptContext 娉ㄥ叆 LLM prompt"]

    subgraph ISOLATED ["鉂?瀛ょ珛妯″潡锛堝畾涔夊畬鏁翠絾鏃犺皟鐢ㄦ柟锛?]
        direction LR
        Z1["EmotionVector\nVAD 涓夌淮妯″瀷"]
        Z2["EmotionTimeline"]
        Z3["compute_emotion_echo"]
        Z4["evaluate_emotion_triggers"]
        Z5["apply_visual_emotion_hint"]
        Z6["BondEmotionalTexture.update()\napply_event 涓粠鏈皟鐢?]
    end

    style G fill:#ff4d4d,color:#fff
    style ISOLATED fill:#fff0f0,stroke:#ff4d4d,stroke-dasharray:5 5
    style Z1 fill:#ffe0e0,stroke:#ff9999
    style Z2 fill:#ffe0e0,stroke:#ff9999
    style Z3 fill:#ffe0e0,stroke:#ff9999
    style Z4 fill:#ffe0e0,stroke:#ff9999
    style Z5 fill:#ffe0e0,stroke:#ff9999
    style Z6 fill:#ffe0e0,stroke:#ff9999
    style K fill:#fff3cd,stroke:#ffc107
    style M fill:#fff3cd,stroke:#ffc107
```

### 杩為€氭€ц瘎浼拌〃

| 鎯呮劅妯″潡 | 杩為€氱姸鎬?| 璇存槑 |
|---------|---------|------|
| `normalize_hard_state` + `build_hard_snapshot` | 鉁?宸叉帴鍏?| 姣忔璇锋眰閮界粡杩?|
| `build_governance_snapshot` | 鉁?宸叉帴鍏?| Governor 閴存潈蹇呯粡 |
| R8 鎯呮劅闂ㄦ帶锛坄critical` 鐘舵€佹嫤鎴級 | 鉁?宸叉帴鍏?| Governor 鍐呴儴 |
| `adjust_gen_params_for_emotion` | 鉁?宸叉帴鍏?| 涓ゆ潯涓昏矾寰勫潎鏈?|
| VTS 琛ㄦ儏椹卞姩锛?6 绉嶆儏鎰熸槧灏勶級 | 鈿狅笍 閮ㄥ垎鎺ュ叆 | 浠?SCENE_CHAT锛屽鏉傚満鏅?*鏈帴鍏?* |
| Bond 鎯呮劅鍔犳潈锛?.5x锛?| 鈿狅笍 閮ㄥ垎鎺ュ叆 | 浠呭鏉傚満鏅紝SCENE_CHAT 娴佸紡璺緞**鏈帴鍏?* |
| `EmotionVector`锛圴AD 涓夌淮妯″瀷锛?| 鉂?**瀛ょ珛** | 瀹氫箟瀹屾暣浣嗘棤璋冪敤鏂?|
| `EmotionTimeline` | 鉂?**瀛ょ珛** | 瀹氫箟瀹屾暣浣嗘棤璋冪敤鏂?|
| `compute_emotion_echo` | 鉂?**瀛ょ珛** | 瀹氫箟瀹屾暣浣嗘棤璋冪敤鏂?|
| `evaluate_emotion_triggers` | 鉂?**瀛ょ珛** | 瀹氫箟瀹屾暣浣嗘棤璋冪敤鏂?|
| `apply_visual_emotion_hint` | 鉂?**瀛ょ珛** | 瀹氫箟瀹屾暣浣嗘棤璋冪敤鏂?|
| `BondEmotionalTexture.update()` | 鉂?**瀛ょ珛** | 瀹氫箟瀹屾暣锛宍apply_event` 涓?*浠庢湭璋冪敤** |

### 鏈€涓ラ噸鐨勮繛閫氭€ф柇瑁?
1. **`BondEmotionalTexture` 浠庢湭琚洿鏂?*锛歚BondStageEngine.apply_event` 涓绠椾簡绉垎銆佽Е鍙戜簡鏅嬬骇锛屼絾 `state.texture.update(response_gap_ms=..., emotion_valence=...)` 浠庢湭琚皟鐢紝`intimacy_temperature` 姘歌繙鏄垵濮嬪€?0.5锛宍to_summary()` 涓殑 `style_hint` 姘歌繙涓嶄細鍑虹幇锛堝洜涓?`0.5 < 0.7`锛夈€?
2. **`EmotionVector` VAD 妯″瀷瀹屽叏瀛ょ珛**锛歚metabolism.py` 涓畾涔変簡瀹屾暣鐨?VAD 涓夌淮鎯呯华妯″瀷锛堣“鍑忋€佹贩鍚堛€佹槧灏勶級锛屼絾鏁翠釜绯荤粺涓病鏈変换浣曞湴鏂瑰垱寤烘垨浣跨敤 `EmotionVector` 瀹炰緥銆?
3. **VTS 椹卞姩浠呭湪 SCENE_CHAT 娴佸紡璺緞鎺ュ叆**锛屽鏉傚満鏅紙WORK_CODE/WORK_TRANSLATE/EXEC_INTENT锛夋墽琛屽畬 LangGraph 鍚庢病鏈夎Е鍙?VTS 琛ㄦ儏鏇存柊銆?
---

## 涓冦€丅ond 浼樺寲鎯呭喌

### 缁撹

> **宸茶秴瓒婄畝鍗曟父鎴忔暟鍊煎寲锛屼絾鏍稿績鎯呮劅璐ㄦ劅灞傛湭婵€娲汇€?*

### 宸插疄鐜扮殑浼樺寲锛堣秴瓒婃暟鍊煎寲鐨勯儴鍒嗭級

| 浼樺寲椤?| 瀹炵幇浣嶇疆 | 璐ㄩ噺 |
|--------|---------|------|
| **涓夌骇浜掑姩鍒嗙被**锛圡INIMAL/ENGAGED/DEEP锛夊熀浜庢枃鏈暱搴?+ 鎯呮劅寮哄害 + 闂彿鏁伴噺 | `BondDynamicsWriter.classify_interaction` | 鉁?鏈夎涔?|
| **鎯呮劅寮哄害鍔犳潈**锛坄emotion_intensity > 0.7` 鈫?1.5x 绉垎锛?| `BondStageEngine.apply_event` | 鉁?鏈夋儏鎰熸劅鐭?|
| **瑁傜棔-淇鍙岃建鏈哄埗**锛圕ONFLICT/APOLOGY/COMFORT 浜嬩欢瑙﹀彂瑁傜棔/淇锛?| `BondDynamicsWriter._apply_rift_event` | 鉁?鏈夊叧绯诲姩鎬?|
| **`BondEmotionalTexture`**锛堜翰瀵嗘俯搴?= 鍥炲鑺傚 脳 鎯呮劅鍧囧€硷級 | `BondScoreState.texture` | 鈿狅笍 瀹氫箟瀹屾暣浣?*浠庢湭鏇存柊** |
| **`to_summary()` 鍏ㄤ腑鏂囪涔夎緭鍑?*锛堜笉鍚暟鍊硷紝渚?LLM 娑堣垂锛?| `BondScoreState.to_summary` | 鉁?璁捐姝ｇ‘ |
| **`repair_hint` 淇椋庢牸鎻愮ず**锛堟敞鍏?LLM prompt锛?| `BondRiftState.repair_hint` | 鈿狅笍 閫昏緫姝ｇ‘浣?`style_hint` 姘歌繙涓嶈Е鍙?|
| **闃舵鍚嶇О涓枃鏄犲皠**锛堥檶鐢熶汉鈫掔浉璇嗏啋鏈嬪弸鈫掓寶鍙嬧啋鐭ュ繁鈫掔伒榄備即渚ｏ級 | `_STAGE_CN_MAP` | 鉁?|

### 浠嶆槸娓告垙鏁板€煎寲鐨勯儴鍒?
| 闂 | 璇存槑 |
|------|------|
| **绉垎闃堝€兼槸纭紪鐮佸父閲?*锛圫TRANGER=0, ACQUAINTANCE=10, FRIEND=30...SOULMATE=300锛夛紝鏃犲姩鎬佽皟鏁存満鍒?| `DEFAULT_STAGE_THRESHOLDS` |
| **`BondEmotionalTexture.update()` 浠庢湭琚皟鐢?*锛宍intimacy_temperature` 姘歌繙鏄?0.5锛屼翰瀵嗘俯搴﹀姛鑳藉舰鍚岃櫄璁?| `BondStageEngine.apply_event` 涓己灏?`state.texture = state.texture.update(...)` |
| **`POST /bond/event` 鎺ュ彛鏃犳儏鎰熷弬鏁?*锛屽閮ㄦ彁浜?Bond 浜嬩欢鏃舵棤娉曚紶鍏?`emotion_tag`/`emotion_intensity`锛屾儏鎰熷姞鏉冨け鏁?| `bond_routes.py:BondEventRequest` |
| **Bond 鏇存柊浠呭湪澶嶆潅鍦烘櫙璺緞瑙﹀彂**锛孲CENE_CHAT 娴佸紡璺緞涓病鏈?Bond 鏇存柊閫昏緫锛屾棩甯稿璇濅笉绉疮 Bond 绉垎 | `chat_stream_handler.py` |
| **`BondDynamicsWriter` 浣跨敤 `InMemoryNeo4jBondClient`**锛岃繘绋嬮噸鍚悗鎵€鏈?Bond 鐘舵€佷涪澶憋紝鏃犳寔涔呭寲 | `bond_dynamics.py:L30` |

---

## 鍏€佹眹鎬伙細闇€瑕佷慨澶嶇殑鍏抽敭闂

> **淇鐘舵€佹洿鏂帮細2026-03-11 14:10**
> 鎵€鏈?12 涓棶棰樺凡鍏ㄩ儴淇锛?511 涓崟鍏冩祴璇曞叏閮ㄩ€氳繃锛堟柊澧?15 涓祴璇曪級銆?> #4锛圡ainChain 閫傞厤灞傦級鍜?#10锛圔ond SQLite 鎸佷箙鍖栵級宸插畬鎴愭灦鏋勭骇淇銆?
| # | 闂 | 鏂囦欢 | 涓ラ噸搴?| 鐘舵€?|
|---|------|------|--------|------|
| 1 | `resolve_host_llm_runtime` 涓悓姝?HTTP 鍋ュ悍鎺㈡祴鍦ㄤ簨浠跺惊鐜腑鐩存帴璋冪敤锛岄樆濉炴渶澶?3s | `graph_config_builder.py` | 馃敶 楂樺嵄 | 鉁?宸蹭慨澶?|
| 2 | `intent_draft.action` 鍥哄畾涓?`narrative_only`锛孏overnor 鏃犳硶鎰熺煡鐪熷疄鎰忓浘绫诲瀷锛孍XEC_INTENT 缁曡繃鎯呮劅闂ㄦ帶 | `chat_handler.py` | 馃煚 涓珮鍗?| 鉁?宸蹭慨澶?|
| 3 | `scene_type` 鍙瀹㈡埛绔樉寮忚鐩栵紝鏃?Governor 鏍￠獙 | `chat_handler.py` | 馃煚 涓珮鍗?| 鉁?宸蹭慨澶?|
| 4 | `MainChain` 鏄浠ｇ爜锛屼笌瀹為檯璺敱瀹屽叏鑴辫妭 | `main_chain.py` | 馃煚 涓珮鍗?| 鉁?宸蹭慨澶嶏紙杩佺Щ涓?`_unified_chat_sse_generator` 閫傞厤灞傦級 |
| 5 | `BondEmotionalTexture.update()` 浠庢湭琚皟鐢紝浜插瘑娓╁害姘歌繙鏄?0.5 | `bond_stage_engine.py` | 馃煚 涓珮鍗?| 鉁?宸蹭慨澶?|
| 6 | `EmotionVector`/`EmotionTimeline`/`compute_emotion_echo`/`evaluate_emotion_triggers` 瀹屽叏瀛ょ珛锛孷AD 鎯呮劅妯″瀷鏃犺皟鐢ㄦ柟 | `emotion_context.py` | 馃煛 涓嵄 | 鉁?宸蹭慨澶?|
| 7 | VTS 椹卞姩浠呭湪 SCENE_CHAT 鎺ュ叆锛屽鏉傚満鏅棤 VTS 瑙﹀彂 | `chat_handler_complex.py` | 馃煛 涓嵄 | 鉁?宸蹭慨澶?|
| 8 | SCENE_CHAT 娴佸紡璺緞鏃?Bond 鏇存柊锛屾棩甯稿璇濅笉绉疮 Bond 绉垎 | `chat_stream_handler.py` | 馃煛 涓嵄 | 鉁?宸蹭慨澶?|
| 9 | `POST /bond/event` 鏃犳儏鎰熷弬鏁帮紝澶栭儴 Bond 浜嬩欢鎯呮劅鍔犳潈澶辨晥 | `bond_routes.py` | 馃煛 涓嵄 | 鉁?宸蹭慨澶?|
| 10 | `BondDynamicsWriter` 浣跨敤鍐呭瓨 Neo4j 瀹㈡埛绔紝杩涚▼閲嶅惎鍚?Bond 鐘舵€佷涪澶?| `bond_dynamics.py` | 馃煛 涓嵄 | 鉁?宸蹭慨澶嶏紙`SqliteBondClient` + 鐜鍙橀噺鍒囨崲锛?|
| 11 | `SCENE_VISION_UI` 鍒嗙被鍚庤蛋 `chat_direct` 鑺傜偣锛岃瑙夊満鏅棤涓撶敤澶勭悊 | `chat_handler.py` | 馃煛 涓嵄 | 鉁?宸蹭慨澶嶏紙鎷︽埅骞惰繑鍥?SCENE_VISION_UI_NOT_SUPPORTED锛?|
| 12 | `_complex_scene_sse_generator` COMMIT 缂哄皯 `vts` 瀛楁锛孲SE 鍙岀涓嶅绉?| `chat_handler_complex.py` | 馃煝 浣庡嵄 | 鉁?宸蹭慨澶?|

### 淇鎽樿

| 淇缂栧彿 | 淇鍐呭 | 鍏抽敭鏀瑰姩 |
|---------|---------|---------|
| #1 | 鍚屾 HTTP 鍋ュ悍鎺㈡祴鏀逛负寮傛 | 鏂板 `_resolve_host_8b_health_status_async`锛坄asyncio.to_thread` 鍖呰锛夛紝`resolve_host_llm_runtime_async` 浣跨敤寮傛鐗堟湰锛沗chat_stream_handler.py` 鍜?`chat_handler_complex.py` 鍧囨敼鐢ㄥ紓姝ョ増鏈?|
| #2 | `intent_draft.action` 鍔ㄦ€佹槧灏?| `chat_handler.py` 涓?`_action_map` 鎸?`pre_intent_classifier` 缁撴灉璁剧疆 `action`锛坄exec_intent`/`work_code`/`work_translate`/`narrative_only`锛夛紱`governor/schemas.py` 鏂板瀵瑰簲 `ACTION_ALLOWLIST` 鍜?`ACTION_ARGS_RULES` |
| #3 | `scene_type` 瀹㈡埛绔牎楠?| `chat_handler.py` 涓鏄惧紡 `scene_type` 鍋?`ALLOWED_EXPLICIT_SCENE_TYPES` 鐧藉悕鍗曟牎楠岋紝闈炴硶鍊奸檷绾т负 `SCENE_CHAT` |
| #5 | `BondEmotionalTexture.update()` 婵€娲?| `bond_stage_engine.py:apply_event` 涓柊澧?`state.texture = state.texture.update(response_gap_ms=..., emotion_valence=...)` |
| #6 | VAD 鎯呮劅妯″瀷鎺ュ叆 | `emotion_context.py` 涓?`normalize_chat_emotion_context` 鍒涘缓 `EmotionVector`銆佽褰?`EmotionTimeline`銆佽皟鐢?`compute_emotion_echo` 鍜?`evaluate_emotion_triggers`锛岀粨鏋滄敞鍏?`emotion_context` 瀛楀吀 |
| #7 | 澶嶆潅鍦烘櫙 VTS 椹卞姩 | `chat_handler_complex.py` 鍚庡鐞嗛樁娈佃皟鐢?`_drive_stream_vts_emotion`锛宍vts_summary` 鍐欏叆 COMMIT `audit_refs` |
| #8 | SCENE_CHAT 娴佸紡 Bond 鏇存柊 | `chat_stream_handler.py` 鍚庡鐞?`asyncio.gather` 涓柊澧?`_run_bond()` 鍗忕▼锛屼笌 Airlock/Episodic/VTS 骞惰鎵ц |
| #9 | `POST /bond/event` 鎯呮劅鍙傛暟 | `bond_routes.py:BondEventRequest` 鏂板 `emotion_tag`/`emotion_intensity` 瀛楁锛沗submit_bond_event` 灏?`emotion_intensity` 浼犲叆 `engine.apply_event` |
| #11 | SCENE_VISION_UI 鎷︽埅 | `chat_handler.py` 涓?`SCENE_VISION_UI` 鍒嗘敮杩斿洖 `SCENE_VISION_UI_NOT_SUPPORTED` 閿欒锛屼笉鍐嶉潤榛樿蛋 `chat_direct` 鑺傜偣 |
| #12 | COMMIT `vts` 瀛楁琛ュ叏 | `chat_handler_complex.py` COMMIT 浜嬩欢 `audit_refs` 涓柊澧?`"vts": vts_summary` |
| #4 | MainChain 杩佺Щ涓洪€傞厤灞?| `main_chain.py` 鏂板 `run_as_sse_adapter()` 鏂规硶锛屽鎵樼粰 `_unified_chat_sse_generator`锛沗dependencies.py` 鏂板 `run_chat_via_main_chain()` 杈呭姪鍑芥暟锛涙柊澧?`test_main_chain_sse_adapter.py`锛? 涓祴璇曪級 |
| #10 | Bond SQLite 鎸佷箙鍖?| `neo4j_client.py` 宸叉湁 `SqliteBondClient` 瀹炵幇锛沗dependencies.py:get_bond_update_queue()` 鐨?`writer_factory` 鏀圭敤 `get_bond_neo4j_client()`锛堟敮鎸?`HOST_BOND_BACKEND=sqlite` 鐜鍙橀噺鍒囨崲锛夛紱鏂板 `test_bond_sqlite_persistence.py`锛?0 涓祴璇曪級 |

---

*鎶ュ憡鐢熸垚鏃堕棿锛?026-03-11 09:45 | 瀹¤妯″瀷锛歝laude-4.6-sonnet*
*淇瀹屾垚鏃堕棿锛?026-03-11 14:10 | 淇妯″瀷锛歝laude-4.6-sonnet | 娴嬭瘯缁撴灉锛?511 passed锛堟柊澧?15 涓祴璇曪級*

