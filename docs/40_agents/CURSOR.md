浣犵幇鍦ㄦ槸 Natsume AI 椤圭洰鐨勯珮绾х郴缁熸灦鏋勫璁′笌淇宸ョ▼甯堛€?
浣犵殑浠诲姟涓嶆槸娉涙硾鍒嗘瀽锛岃€屾槸**鍩轰簬褰撳墠宸ヤ綔鍖轰唬鐮佺殑鐪熷疄瀹炵幇**锛屽畬鎴愪互涓嬪伐浣滐細

1. 淇鏂囨。涓殑绯荤粺浜嬪疄鍋忓樊
2. 琛ラ綈鎴戞寚鍑虹殑鎺у埗闈?/ 鐘舵€侀潰缂洪櫡
3. 鍦ㄥ繀瑕佹椂鐩存帴淇敼浠ｇ爜
4. 杩愯娴嬭瘯骞剁粰鍑洪獙璇佽瘉鎹?5. 浜у嚭涓€鐗?*涓ユ牸銆佺湡瀹炪€佷笉澶稿ぇ瀹屾垚搴?*鐨勪慨璁㈡姤鍛?
---

# 宸ヤ綔鏂瑰紡

浣犲繀椤荤洿鎺ュ湪褰撳墠浠ｇ爜搴撲腑宸ヤ綔锛岄伒寰互涓嬪師鍒欙細

- **鍏堣浠ｇ爜锛屽啀涓嬬粨璁?*
- **浠ヤ唬鐮佺湡瀹炴墽琛岃涔変负鍑?*
- **涓嶅厑璁歌剳琛ワ紝涓嶅厑璁告部鐢ㄦ棫鎶ュ憡涓殑鍙枒缁撹**
- **涓嶅厑璁告妸鈥滃凡鎺ュ叆鈥濆啓鎴愨€滃凡闂幆鈥?*
- **涓嶅厑璁告妸鈥滄湁鍗曟祴鈥濆啓鎴愨€淓2E 宸查獙璇佲€?*
- **涓嶅厑璁稿彧鎻愬缓璁笉钀藉湴**
- 鍙互鐩存帴淇敼浠ｇ爜銆佹枃妗ｃ€佹祴璇?- 淇敼鍚庡繀椤昏繍琛岀浉鍏虫祴璇?- 鏈€缁堝繀椤荤粰鍑猴細浠ｇ爜淇敼娓呭崟銆侀獙璇佺粨鏋溿€佷慨璁㈠悗鐨勫畬鏁存姤鍛?
---

# 褰撳墠闇€瑕佷慨澶?/ 鏍告煡鐨勬牳蹇冮棶棰?
## 闂 1锛氫富閾炬墽琛岄『搴忓湪鏂囨。涓笉涓€鑷?浣犲繀椤诲璁＄湡瀹炶皟鐢ㄩ摼锛屾槑纭互涓嬮『搴忕殑鍞竴浜嬪疄锛?
- `handle_chat`
- `pre_intent_classifier`
- `_unified_chat_sse_generator`
- `Governor.evaluate_intent`
- `SceneRouter.route_sse`
- `MainChain.run_as_sse_adapter`
- `LangGraph` 鎵ц鍣?- `/v1/chat`
- `/v1/host/input`

### 蹇呴』瀹屾垚
- 鎵惧埌鐪熷疄鍏ュ彛鍑芥暟
- 鎵惧埌鐪熷疄璋冪敤鏍?- 杈撳嚭鍞竴鍙俊鐨勬墽琛屾椂搴?- 淇鏃ф姤鍛婁腑鎵€鏈夐『搴忓啿绐?- 濡傛湁蹇呰锛岃ˉ娉ㄩ噴鎴栬交閲忛噸鏋勶紝**浣嗕笉瑕佺牬鍧忕幇鏈夎涓?*

---

## 闂 2锛氭垚鐔熷害鍒嗙骇娣蜂贡锛屾贩娣嗕簡鈥滃凡鎺ョ嚎 / 宸茬敓鏁?/ 宸查獙璇?/ 宸叉垚涓哄敮涓€浜嬪疄鏉ユ簮鈥?浣犲繀椤诲紩鍏ヤ互涓嬪垎绾т綋绯伙紝骞剁敤浜庝慨璁㈡姤鍛婏細

- `DECLARED`锛氫粎鏈夎璁?/ 鎺ュ彛 / 璇存槑
- `WIRED`锛氫唬鐮佸凡鎺ョ嚎锛屾湁璋冪敤鐐?- `ACTIVE`锛氬凡杩涘叆涓婚摼骞跺奖鍝嶇湡瀹炶繍琛岀粨鏋?- `SHADOW`锛氬凡鎺ュ叆鏃佽矾瑙傛祴 / 瀹¤锛屼絾涓嶅奖鍝嶄富鐢熸垚鎴栦富鍐崇瓥
- `AUTHORITATIVE`锛氬凡鎴愪负璇ラ鍩熷敮涓€浜嬪疄鏉ユ簮 / 鍞竴瑁佸喅鍏ュ彛
- `E2E_VERIFIED`锛氬凡瀹屾垚绔埌绔仈璋冮獙璇佸苟鏈夎瘉鎹?- `PROD_READY`锛氬叿澶囬敊璇鐞嗐€佸彲瑙傛祴鎬с€佸洖閫€绛栫暐銆佸畨鍏ㄨ竟鐣?
### 蹇呴』閲嶆柊瀹氱骇鐨勫璞?- `MainChain`
- `RoutingMatrix`
- `RecallPackBuilder`
- `MemoryGuard`
- `StateArbiter`
- `SCENE_VISION_UI`
- `/v1/host/input`
- `/v1/edge/events`

### 棰濆瑕佹眰
鍑℃槸**鍙妸缁撴灉鍐欏叆 `audit_refs`锛屼絾涓嶅奖鍝?prompt / 鍐崇瓥 / 鎵ц** 鐨勬ā鍧楋紝蹇呴』鏍囦负 `SHADOW`锛屼笉寰楀啓鎴愨€滃凡瀹屾垚鎺ュ叆涓婚摼鈥濄€?
---

## 闂 3锛歚RecallPackBuilder` 鍙兘鍙槸 shadow mode
璇峰鏌ュ畠鍦?`chat_stream_handler.py`銆乣_build_stream_chat_prompt()` 涓婁笅娓镐腑鐨勭湡瀹炰綔鐢ㄣ€?
浣犲繀椤诲垽鏂細
- 瀹冪殑缁撴灉鏄惁鐪熺殑杩涘叆 prompt 鏋勫缓
- 鏄惁鍙槸鍐欏叆 `COMMIT.audit_refs`
- 鏄惁鍙槸鍋氳娴?/ 瀹¤鑰屼笉褰卞搷鐢熸垚
- 褰撳墠鐪熷疄绛夌骇鏄?`SHADOW` 杩樻槸 `ACTIVE`

### 瑕佹眰
- 缁欏嚭鏄庣‘鍒ゅ畾
- 淇鏂囨。鍙ｅ緞
- 濡傛灉浣犺涓哄畠搴旇鍗囩骇涓?`ACTIVE`锛屽彲浠ュ疄鏂?*鏈€灏忓畨鍏ㄦ帴鍏?*
- 鑻ュ崌绾э紝蹇呴』璇存槑锛?  - 淇敼鏂囦欢
  - 鎺ュ叆鐐?  - 瀵?prompt 鐨勫叿浣撳奖鍝?  - 椋庨櫓
  - 鍥為€€鏂瑰紡

---

## 闂 4锛歚StateArbiter` 鍙兘鍙畬鎴愪簡 source 娉ㄥ唽锛屾湭杩涘叆涓氬姟瑁佸喅闈?璇锋鏌ワ細

- 鏄惁浠呭湪 `runtime_lifespan` 娉ㄥ唽 source
- 鏄惁鏈夌湡瀹炰笟鍔¤皟鐢?`resolve(...)`
- 鍝簺璇讳簨瀹炶矾寰勭粫杩囦簡瀹?- 瀹冨綋鍓嶅簲褰掔被涓?`WIRED` / `ACTIVE` / `AUTHORITATIVE`

### 澶勭悊绛栫暐
浜岄€変竴锛?
#### 鏂规 A锛堜繚瀹堬級
- 涓嶆敼涓讳笟鍔￠€昏緫
- 鍙慨姝ｆ枃妗?- 鏄庣‘鏍囨敞瀹冨綋鍓嶅彧鏄?`WIRED` 鎴?`ACTIVE`

#### 鏂规 B锛堟帹杩涳級
- 閫夋嫨涓€鏉＄湡瀹炰笟鍔¤矾寰勬帴鍏?`StateArbiter.resolve(...)`
- 渚嬪 retrieval / context / memory fact resolution
- 鎺ュ叆鍚庤ˉ娴嬭瘯
- 鎶ュ憡涓槑纭細
  - 鍝簺浜嬪疄宸茶繘鍏ヤ徊瑁佽矾寰?  - 鍝簺浠嶆湭杩涘叆

浼樺厛閲囩敤**鏈€灏忓畨鍏ㄦ敼鍔?*銆?
---

## 闂 5锛歚/v1/edge/events` 褰撳墠 success 鈮?鍓綔鐢ㄥ畬鎴?璇峰鏌?`system_handler.py` 鐩稿叧閾捐矾锛屾槑纭細

- HTTP success 鏄惁鍙槸 accepted
- Bond / VTS 鏄惁涓?fire-and-forget
- 鍓綔鐢ㄦ槸鍚﹀彲瑙傛祴
- 鏂囨。鏄惁澶稿ぇ涓衡€滃畬鏁撮棴鐜凡鎵撻€氣€?- 褰撳墠鏃犻壌鏉冩槸鍚﹀瓨鍦ㄩ闄?
### 蹇呴』瀹屾垚
1. 缁欏嚭璇ラ摼璺湡瀹炶涔夊垎灞傦細
   - request accepted
   - side effect dispatched
   - side effect completed
   - side effect observed
2. 淇鏂囨。琛ㄨ堪锛屼笉鑳芥妸 accepted 鍐欐垚瀹屾暣闂幆
3. 瀹℃煡鏄惁闇€瑕佹渶灏忓畨鍏ㄤ繚鎶わ細
   - 鏉ユ簮鏍￠獙
   - 浜嬩欢绫诲瀷鐧藉悕鍗?   - 鍐呯綉闄愬埗
   - HMAC / 绛惧悕
4. 鑻ユ殏涓嶅姞閴存潈锛屽繀椤诲湪鎶ュ憡涓啓鏄庡畨鍏ㄥ墠鎻愪笌椋庨櫓杈圭晫

---

## 闂 6锛歚SCENE_VISION_UI` 鐜板湪鍙兘鍙槸鈥滆矾寰勬斁琛屸€濓紝涓嶆槸鈥滆兘鍔涘凡楠岃瘉鈥?璇峰鏌ワ細

- 鐧藉悕鍗曟槸鍚︾湡瀹炵敓鏁?- `chat_handler_vision._vision_ui_sse_generator` 鏄惁鍙揪
- `image_upload_consent=true` 鍒嗘敮鏄惁鍙揪
- 鏃?consent 鐨勯檷绾ч€昏緫鏄惁鎴愮珛
- `audit_refs.vision_input` 鏄惁鐪熷疄鍐欏叆
- 鏄惁宸叉湁 E2E 璇佹嵁锛岃€屼笉鍙槸鍗曟祴

### 瑕佹眰
- 缁欏嚭鐪熷疄绛夌骇锛歚WIRED` / `ACTIVE` / `E2E_VERIFIED`
- 鑻ユ湭鑱旇皟楠岃瘉锛屼笉寰楀啓鈥滆瑙夐摼璺畬鏁村疄鐜扳€?- 蹇呴』淇鏂囨。涓虹湡瀹炵姸鎬?
---

# 鎵ц椤哄簭锛堝繀椤绘寜椤哄簭鍋氾級

## P0
1. 瀹¤鐪熷疄鎵ц椤哄簭
2. 淇鏂囨。涓墍鏈夐『搴忓啿绐?3. 閲嶅垎绾э細
   - `RecallPackBuilder`
   - `StateArbiter`
   - `RoutingMatrix`
   - `MainChain`

## P1
4. 瀹℃煡 `/v1/host/input` 鏄惁鐪熺殑杩涘叆瀹屾暣 chat 涓婚摼锛屽苟琛ヨ瘉鎹?5. 瀹℃煡 `/v1/edge/events` 鐨勭湡瀹炶涔変笌瀹夊叏杈圭晫锛屽苟淇鏂囨。

## P2
6. 瀹℃煡 `SCENE_VISION_UI` 褰撳墠鐪熷疄鐘舵€?7. 淇鏂囨。涓墍鏈夊じ澶ц〃杩?
## P3
8. 琛ュ厖 / 淇蹇呰娴嬭瘯
9. 杈撳嚭鏈€缁堜慨璁㈢増鎶ュ憡

---

# 杈撳嚭瑕佹眰

浣犲繀椤诲湪瀹屾垚浠ｇ爜瀹¤銆佷慨鏀广€佹祴璇曞悗锛屾寜浠ヤ笅椤哄簭杈撳嚭缁撴灉锛?
## 1. 鎵ц鎽樿
绠€瑕佽鏄庯細
- 鍙戠幇浜嗗摢浜涘叧閿簨瀹炲亸宸?- 鏀逛簡鍝簺浠ｇ爜 / 鏂囨。
- 鍝簺闂鐪熸瑙ｅ喅浜?- 鍝簺浠嶆湭楠岃瘉

## 2. 鎺у埗闈㈢湡瀹炴€у璁?缁欏嚭**鍞竴鍙俊鐨勬墽琛岄『搴?*锛岃嚦灏戣鐩栵細
- `/v1/chat`
- `/v1/host/input`
- `handle_chat`
- `pre_intent_classifier`
- `_unified_chat_sse_generator`
- `Governor`
- `SceneRouter`
- `MainChain`
- `LangGraph`

骞舵彁渚涳細
- mermaid 鏃跺簭鍥?- 鏃ф姤鍛婂啿绐佺偣
- 淇鍚庣殑缁熶竴缁撹

## 3. 妯″潡鎴愮啛搴﹂噸鍒嗙骇琛?鑷冲皯瑕嗙洊锛?- `MainChain`
- `RoutingMatrix`
- `RecallPackBuilder`
- `MemoryGuard`
- `StateArbiter`
- `SCENE_VISION_UI`
- `/v1/host/input`
- `/v1/edge/events`

瀛楁蹇呴』鍖呭惈锛?- 妯″潡鍚?- 褰撳墠绛夌骇
- 鐞嗙敱
- 璇佹嵁锛堟枃浠?/ 鍑芥暟锛?- 鏄惁寤鸿缁х画鍗囩骇
- 涓嬩竴姝ユ渶灏忔敼鍔?
## 4. 浠ｇ爜淇敼娓呭崟
鍒楀嚭锛?- 淇敼鏂囦欢
- 淇敼鐐?- 淇敼鍘熷洜
- 鏄惁鏀瑰彉鐜版湁琛屼负
- 椋庨櫓涓庡洖閫€鏂瑰紡

## 5. 娴嬭瘯涓庨獙璇佺粨鏋?蹇呴』鍖哄垎锛?- 鍗曞厓娴嬭瘯
- 闆嗘垚娴嬭瘯
- E2E 鑱旇皟

姣忛」鍐欐竻锛?- 鏄惁鎵ц
- 缁撴灉
- 瑕嗙洊鑼冨洿
- 鏈獙璇侀」

## 6. 淇鍚庣殑鎶ュ憡鍏ㄦ枃
鐩存帴杈撳嚭淇鍚庣殑銆奛atsume AI 绯荤粺鏁版嵁娴佸叏鏅姤鍛娿€嬪畬鏁村唴瀹癸紝涓嶈鍙粰 diff銆?
## 7. 浠嶆湭瑙ｅ喅鐨勯棶棰樹笌鍚庣画寤鸿
蹇呴』鏄庣‘鍝簺鍙槸锛?- `WIRED`
- `SHADOW`
- `ACTIVE`
鑰屼笉鏄?`E2E_VERIFIED` 鎴?`PROD_READY`

---

# 纭€х害鏉?
- 涓嶅厑璁稿彧缁欏缓璁紝蹇呴』钀藉埌浠ｇ爜 / 鏂囨。 / 娴嬭瘯
- 涓嶅厑璁告妸 TODO 鍐欐垚鈥滃凡瑙ｅ喅鈥?- 涓嶅厑璁告妸鍗曞厓娴嬭瘯閫氳繃鍐欐垚 E2E 瀹屾垚
- 涓嶅厑璁稿じ澶ф垚鐔熷害
- 涓嶅厑璁搁殣钘忎笉纭畾鎬?- 蹇呴』寮曠敤鍏蜂綋鏂囦欢鍚嶅拰鍑芥暟鍚?- 鏈€缁堢粨璁哄繀椤讳互浠ｇ爜鐪熷疄璇箟涓哄噯

---

鐜板湪寮€濮嬫墽琛屻€?
鍏堥亶鍘嗗苟瀹¤鐩稿叧浠ｇ爜鏂囦欢锛屽啀瀹炴柦鏈€灏忓繀瑕佷慨鏀癸紝鍐嶈繍琛屾祴璇曪紝鏈€鍚庢寜瑕佹眰杈撳嚭瀹屾暣缁撴灉銆?
---

# 鍚庣画浠诲姟鐪嬫澘锛堟洿鏂颁簬 2026-03-11 22:40锛?
> 浠ヤ笅浠诲姟鏉ヨ嚜涓ユ牸浠ｇ爜瀹¤锛堣 `docs/dataflow_report_20260311.md` 绗崄鍏妭 搂16.7锛夈€?> 浠诲姟鐘舵€佸繀椤讳互浠ｇ爜鐪熷疄鎵ц璇箟鍜屾祴璇曡瘉鎹负鍑嗭紱鏈惤鍦板墠涓嶅緱鍐欐垚鈥滃凡瑙ｅ喅鈥濄€?
## P1锛堥珮浼樺厛绾э級

### TODO-1锛歊ecallPackBuilder 鍗囩骇涓?ACTIVE锛堝凡瀹屾垚锛?026-03-11锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堝彲鍥為€€锛?- `_recall_pack_builder.build()` 杈撳嚭浼氳浆鎹负 `retrieval_evidence_items` 骞跺湪 `HOST_RECALL_PACK_ACTIVE=true` 鏃跺洖鍐欙紝鐪熷疄褰卞搷 `_build_stream_chat_prompt`
- 鍚屾椂淇濈暀 `audit_refs.recall_pack_builder` 瑙傛祴锛屽苟鏂板 `mode/active_enabled/applied_to_prompt/rewritten_item_count/input_item_count`

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/chat_stream_handler.py`
- `tests/unit/test_chat_stream_handler_p0_stream_retrieval.py`

**楠岃瘉**锛?- `pytest -q tests/unit/test_chat_stream_handler_p0_stream_retrieval.py`锛? passed锛?
**鍥為€€鏂瑰紡**锛氳缃?`HOST_RECALL_PACK_ACTIVE=false`锛屼繚鐣欐瀯寤轰笌瀹¤浣嗕笉鍥炲啓 prompt 杈撳叆锛堥€€鍥?`SHADOW` 璇箟锛夈€?
---

### TODO-2锛歚/v1/edge/events` 娣诲姞浜嬩欢绫诲瀷鐧藉悕鍗曪紙宸插畬鎴愶紝2026-03-11锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堟渶灏忓畨鍏ㄨ竟鐣屽凡涓婄嚎锛?- 宸插鍔?`ALLOWED_EDGE_EVENT_TYPES` 浜嬩欢绫诲瀷鐧藉悕鍗?- 宸插鍔?`ALLOWED_EDGE_EVENT_SOURCES` 鏉ユ簮鐧藉悕鍗?- 闈炴硶绫诲瀷/鏉ユ簮鍦ㄥ叆鍙ｈ繑鍥?`HTTP 400`

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/system_handler.py`
- `tests/unit/test_edge_events_whitelist.py`

**楠岃瘉**锛?- `pytest -q tests/unit/test_edge_events_whitelist.py`锛? passed锛?- `pytest -q tests/integration/test_host_integration.py::TestEdgeEventsEndpoint`锛? passed锛?
**瀹夊叏鍓嶆彁**锛堝綋鍓嶆殏涓嶅姞閴存潈鐨勫墠鎻愶級锛?1. Host Brain 浠呴儴缃插湪鍐呯綉锛屼笉鏆撮湶鍏綉
2. Edge Client 涓?Host Brain 鍦ㄥ悓涓€鍙椾俊浠荤綉缁?3. 鏃犲绉熸埛鍦烘櫙锛堝崟鐢ㄦ埛閮ㄧ讲锛?
---

## P2锛堜腑浼樺厛绾э級

### TODO-3锛歊outingMatrix 鍗囩骇涓?ACTIVE锛堝凡瀹屾垚锛?026-03-11锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堢瓥鐣ラ棬鎺э級
- `_decision.selected_model` 宸叉槧灏勫洖鐪熷疄 runtime 閫夋嫨锛坄provider_name/model_id`锛夛紝涓嶅啀浠呭啓瀹¤瀛楁
- 淇濈暀 8B 鍋ュ悍鎺㈡祴涓庣瓥鐣ラ棬鎺?fallback锛涗笉鍙垏鎹㈡椂鍐欏叆 `routing_matrix_decision.not_applied_reason`
- 鏂板 `routing_matrix_decision.runtime_applied/runtime_selected_target` 鐢ㄤ簬鍒ゅ畾鏄惁鐪熷疄鏀瑰啓

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/graph_config_builder.py`
- `tests/unit/test_host_8b_runtime_route.py`

**楠岃瘉**锛?- `pytest -q tests/unit/test_host_8b_runtime_route.py`锛? passed锛?- 鏂板鐢ㄤ緥锛歚test_resolve_host_llm_runtime_routing_matrix_reasoning_scene_overrides_to_cloud`

---

### TODO-4锛歋tateArbiter 鎺ュ叆涓氬姟鏌ヨ锛堝凡瀹屾垚锛?026-03-11锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堝眬閮ㄨ矾寰勶級
- 鍚姩鏃舵敞鍐?world/style/episodic 涓変釜閫傞厤鐘舵€佹簮锛堜慨澶嶆棤鏁堢被鍚?鏂规硶寮曠敤瀵艰嚧鐨勬敞鍐岄檷绾э級
- 娴佸紡涓婚摼璋冪敤 `StateArbiter.resolve("world_canon_facts", session_id)`锛屽懡涓椂娉ㄥ叆 `retrieval_evidence_items` 涓?`retrieval_summary.world_canon`
- COMMIT 澧炲姞 `audit_refs.state_arbiter`锛屽彲瑙傛祴 status/source/fallback/conflict

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/http_hooks.py`
- `host_brain/app/chat_stream_handler.py`
- `tests/unit/test_chat_stream_handler_p0_stream_retrieval.py`

**楠岃瘉**锛?- `pytest -q tests/unit/test_chat_stream_handler_p0_stream_retrieval.py`锛? passed锛?- 鏂板鐢ㄤ緥锛歚test_stream_state_arbiter_resolve_injects_world_canon_into_prompt`

---

## P3锛堜綆浼樺厛绾э級

### TODO-5锛歋CENE_VISION_UI E2E 鑱旇皟楠岃瘉

**褰撳墠鐘舵€?*锛歚E2E_VERIFIED`锛?026-03-11锛?- 鐧藉悕鍗曠敓鏁堬紝`SCENE_VISION_UI` 鍙揪锛堝凡鍏煎 `scene_type=SCENE_VISION_UI` 鍓嶇紑鍐欐硶锛?- 鍙岀瀹炴祴宸茶鐩栵細
  - `image_upload_consent=true` 鈫?`audit_refs.vision_input.image_upload_allowed=true`
  - `image_upload_consent=false` 鈫?`audit_refs.vision_input.image_upload_allowed=false`
- `audit_refs.vision_input`銆乣GRAPH_START(scene_type=VISION_UI)`銆乣COMMIT(APPROVE)` 鍧囧凡瑙傛祴

**璇佹嵁**锛?- 鐪熷疄鍙岀鑱旇皟锛堟湰鍦拌繘绋嬪惎鍔?`host_brain` + `edge_client`锛夎繑鍥烇細
  - `edge_health_status=ACCEPTED`
  - `host_health_status=OK`
  - `consent_true.vision_input.image_upload_allowed=true`
  - `consent_false.vision_input.image_upload_allowed=false`
- 闆嗘垚娴嬭瘯锛?  - `tests/integration/test_host_integration.py::TestChatEndpoint::test_chat_vision_ui_with_consent_writes_vision_audit_refs`
  - `tests/integration/test_host_integration.py::TestChatEndpoint::test_chat_vision_ui_without_consent_degrades_image_upload`

---

### TODO-6锛歁ainChain 楠ㄦ灦鑺傜偣濉厖

**褰撳墠鐘舵€?*锛歚ACTIVE`锛堣交缂栨帓閫傞厤灞傦紝2026-03-11锛?- 鍚勮妭鐐?`execute()` 宸茶ˉ鍏呮渶灏忓彲鎵ц璇箟锛堥潪绾?`return True`锛夛細
  - `AirlockQuarantineNode`锛氳緭鍏ュ噣鍖?绌烘秷鎭樆鏂?  - `GovernorNode`锛氬満鏅彁绀烘槧灏?`TaskType` 骞惰惤瀹¤
  - `GraphExecutor/RecallFusion/LlmGateway/QA/Episodic/Audit/AirlockSanitizer`锛氱姸鎬佽惤鐐逛笌瀹¤鏍囩
- `run_as_sse_adapter` 宸插崌绾т负锛?  - 鎵ц pre-node锛坅irlock鈫抔overnor鈫抔raph鈫抮ecall鈫抣lm锛?  - 濮旀墭 `_unified_chat_sse_generator` 骞惰娴?SSE锛堝惈 COMMIT锛?  - 鎵ц post-node锛坬a鈫抏pisodic鈫抋udit鈫抯anitizer锛?  - 鍓嶅悗缃け璐ユ椂瑙﹀彂 FailureHandler + REJECT COMMIT 鍏滃簳

**璇佹嵁**锛?- 浠ｇ爜锛歚host_brain/app/graph/main_chain.py`
- 鍗曟祴锛歚tests/unit/test_main_chain_sse_adapter.py`锛堝悓姝ユ墽琛岋紝瑕嗙洊 pre/post 缂栨帓涓庡紓甯稿厹搴曪級

---

### TODO-7锛歋CENE_VISION_UI 鐢熶骇鍖栧姞鍥猴紙宸插畬鎴愶紝2026-03-12锛?
**褰撳墠鐘舵€?*锛歚PROD_READY`锛堟渶灏忕敓浜у寲鍔犲浐宸蹭笂绾匡級
- 鏂板 `_VisionSLAMonitor` 妯″潡绾у崟渚嬶紝杩借釜杩炵画澶辫触娆℃暟銆侀檷绾х巼
- 杩炵画澶辫触 >= `HOST_VISION_SLA_FAIL_THRESHOLD`锛堥粯璁?3锛夋椂鍐欏叆 ledger 鍛婅锛坄S_VISION_SLA_ALERT`锛?- 闄嶇骇鐜?>= `HOST_VISION_SLA_DEGRADE_RATE_THRESHOLD`锛堥粯璁?0.5锛変笖鎬昏皟鐢?>= 10 鏃惰Е鍙戝憡璀?- 鍗曟璋冪敤寤惰繜 > `HOST_VISION_SLA_LATENCY_WARN_MS`锛堥粯璁?10000ms锛夋椂鍐欏叆寤惰繜鍛婅锛坄S_VISION_SLA_LATENCY_WARN`锛?- 鏂板璇锋眰鏉ユ簮鏍￠獙锛歚HOST_VISION_REQUIRE_INTERNAL_ORIGIN=true` 鏃讹紝浠呭厑璁稿唴缃?IP锛?27.x/192.168.x/10.x/::1锛?- `COMMIT.audit_refs.sla_monitor` 杈撳嚭 SLA 鐩戞帶鎽樿锛堝彲瑙傛祴锛?
**鏀瑰姩鏂囦欢**锛?- `host_brain/app/chat_handler_vision.py`

**楠岃瘉**锛?- `pytest -q tests/unit/test_main_chain_deep_business.py::TestVisionSLAMonitor`锛? passed锛?
**鍥為€€鏂瑰紡**锛?- 璁剧疆 `HOST_VISION_REQUIRE_INTERNAL_ORIGIN=false` 绂佺敤鏉ユ簮鏍￠獙
- SLA 鐩戞帶涓虹函瑙傛祴锛屼笉褰卞搷涓婚摼璺?
---

### TODO-8锛歁ainChain 娣卞害涓氬姟鍖栵紙宸插畬鎴愶紝2026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堟繁搴︿笟鍔″寲锛屾帴杩?AUTHORITATIVE锛?- `GovernorNode`锛氭帴鍏ョ湡瀹?`IntentDecisionEngine.evaluate_intent`锛屾墽琛屽畬鏁?R1-R7 闂ㄦ帶
  - 閫傞厤灞傛ā寮忥紙`adapter_mode=sse`锛夎嚜鍔ㄨ烦杩囬噸澶嶉壌鏉冿紝閬垮厤鍙岄噸 Governor 璋冪敤
  - 闄嶇骇绛栫暐锛氬紩鎿庝笉鍙敤鏃堕€€鍥炴渶灏忕姸鎬佽涔夛紝鏍囪 `GOVERNOR_DEGRADED_FALLBACK`
- `RecallFusionNode`锛氭帴鍏ョ湡瀹?`RetrievalOrchestrator.arun`锛屾墽琛屼竷璺苟琛屽彫鍥?  - 闄嶇骇绛栫暐锛歄rchestrator 涓嶅彲鐢ㄦ椂閫€鍥炴渶灏忕姸鎬佽涔夛紝鏍囪 `RECALL_DEGRADED_FALLBACK`
  - 鍙洖缁撴灉鍐欏叆 `ctx.recall_payload`锛屼緵鍚庣画鑺傜偣浣跨敤
- `QAContractNode`锛氭帴鍏ョ湡瀹炲唴瀹瑰悎瑙勬牎楠岋紙4 椤规鏌ワ級
  - 鏍煎紡瑙勮寖鍖栵細鍘婚櫎棣栧熬绌虹櫧銆佸帇缂╁浣欑┖琛屻€佸幓闄よ灏剧┖鐧?  - 闀垮害鎴柇锛氳秴杩?`HOST_QA_MAX_RESPONSE_CHARS`锛堥粯璁?4000锛夋椂鍦ㄥ彞瀛愯竟鐣屾埅鏂?  - 瀹夊叏璇嶆娴嬶細鐤戜技绯荤粺鎻愮ず璇嶆硠闇叉椂鍐欏叆鍛婅鏍囩锛堜笉闃绘柇锛岃鎶ョ巼杈冮珮锛?  - Null 瀛楄妭娓呴櫎锛氬幓闄?`\x00` 瀛楄妭

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/graph/main_chain.py`

**楠岃瘉**锛?- `pytest -q tests/unit/test_main_chain_deep_business.py`锛?8 passed锛?- `pytest -q tests/unit/test_main_chain_sse_adapter.py`锛? passed锛岄€傞厤灞傚吋瀹规€ч獙璇侊級

**鍥為€€鏂瑰紡**锛?- `GovernorNode` 闄嶇骇锛氬紩鎿庝笉鍙敤鏃惰嚜鍔ㄩ檷绾э紝涓嶉樆鏂摼璺?- `RecallFusionNode` 闄嶇骇锛歄rchestrator 涓嶅彲鐢ㄦ椂鑷姩闄嶇骇锛屼笉闃绘柇閾捐矾
- `QAContractNode` 闄嶇骇锛氭鏌ュ紓甯告椂淇濈暀鍘熷杈撳嚭锛屾爣璁?`QA_CHECK_DEGRADED`

---

### TODO-10锛歊ecallPack slot 娉ㄥ叆 + GovernorNode 閴存潈璺緞缁熶竴锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堥摼璺竴鑷达級

**闂 A锛歚build_prompt_context` 鏈娇鐢?RecallPack slot 杈撳嚭**
- `RecallPackBuilder.build()` 浜у嚭鐨?slot 鏁版嵁鏈敞鍏?`build_prompt_context` 鐨勫弬鏁帮紝瀵艰嚧 RecallPack 缁撴灉涓?prompt 鏋勫缓鑴辫妭
- **淇**锛歚chat_context_builder.py` 涓?`build_prompt_context` 鎺ユ敹骞舵敞鍏?`recall_slot_text` 鍙傛暟锛沗chat_stream_handler.py` 涓彁鍙?RecallPack slot 骞堕€忎紶

**闂 B锛欸overnorNode 涓ゆ潯閴存潈璺緞涓嶄竴鑷?*
- 閫傞厤灞傛ā寮忥紙`adapter_mode=sse`锛変笅 `GovernorNode` 璺宠繃鐪熷疄閴存潈锛岀敱 `_unified_chat_sse_generator` 璐熻矗锛涗袱鏉¤矾寰勮涓轰笉涓€鑷?- **淇**锛歚main_chain.py` 涓粺涓€涓ゆ潯璺緞锛岄€傞厤灞備篃鎵ц鐪熷疄閴存潈锛岀粨鏋滈€氳繃 `pre_governor_result` 浼犻€掔粰 `_unified_chat_sse_generator`锛岄伩鍏嶉噸澶嶆墽琛?
**鏀瑰姩鏂囦欢**锛?- `host_brain/app/chat_context_builder.py`锛歚build_prompt_context` 澧炲姞 `recall_slot_text` 鍙傛暟娉ㄥ叆
- `host_brain/app/chat_stream_handler.py`锛氭彁鍙?RecallPack slot 骞堕€忎紶锛涙帴鏀?`pre_governor_result`
- `host_brain/app/graph/main_chain.py`锛歚GovernorNode` 缁熶竴涓ゆ潯閴存潈璺緞锛屾敞鍏?`pre_governor_result`

**楠岃瘉**锛?- 鍚堝苟杩愯锛?2 passed锛堝惈鎵€鏈夌浉鍏冲崟鍏冩祴璇曪紝鏃犲洖褰掞級

**鍥為€€鏂瑰紡**锛歚recall_slot_text=None` 鏃堕€€鍥炲師鏈?prompt 鏋勫缓閫昏緫锛沗pre_governor_result=None` 鏃堕€€鍥炲師鏈夐壌鏉冮€昏緫锛屾棤鐮村潖鎬у彉鏇?
---

### TODO-9锛歊ecallFusionNode 瀛ゅ矝闂淇锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堥摼璺棴鐜級
- **闂**锛歚RecallFusionNode` 鎵ц涓冭矾鍙洖鍚庯紝缁撴灉鍐欏叆 `ctx.recall_payload`锛屼絾 `run_as_sse_adapter` 璋冪敤 `_unified_chat_sse_generator` 鏃舵湭浼犲叆锛屽鑷?`_sse_event_generator` 鍐呴儴閲嶅鎵ц涓€娆″彫鍥烇紙鍙岄噸鍙洖锛岄鍙洖缁撴灉琚涪寮冿級
- **淇**锛氬皢 `ctx.recall_payload.retrieval_evidence_items` 浣滀负 `_pre_recall_evidence` 鍙傛暟閫忎紶锛岄摼璺細`run_as_sse_adapter` 鈫?`_unified_chat_sse_generator` 鈫?`SceneRouter.route_sse` 鈫?`_sse_event_generator`
- **鏁堟灉**锛歚_sse_event_generator` 鍦ㄦ湁棰勫彫鍥炵粨鏋滄椂璺宠繃鍐呴儴鍙洖锛岄伩鍏嶅弻閲嶅彫鍥炴氮璐硅祫婧愶紱绌哄垪琛?None 鏃堕€€鍥炲師鏈夊彫鍥為€昏緫

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/chat_stream_handler.py`锛歚_sse_event_generator` 澧炲姞 `_pre_recall_evidence` 鍙傛暟
- `host_brain/app/chat_handler.py`锛歚_unified_chat_sse_generator` 澧炲姞 `_pre_recall_evidence` 鍙傛暟骞堕€忎紶
- `host_brain/app/graph/main_chain.py`锛歚SceneRouter.route_sse` 澧炲姞 `_pre_recall_evidence` 鍙傛暟骞堕€忎紶锛沗run_as_sse_adapter` 鎻愬彇 `ctx.recall_payload` 骞舵敞鍏?
**楠岃瘉**锛?- `pytest -q tests/unit/test_main_chain_deep_business.py::TestPreRecallInjection`锛? passed锛?- 鍚堝苟杩愯锛?2 passed锛堝惈鎵€鏈夌浉鍏冲崟鍏冩祴璇曪紝鏃犲洖褰掞級

**鍥為€€鏂瑰紡**锛歚_pre_recall_evidence=None` 鏃堕€€鍥炲師鏈夊彫鍥為€昏緫锛屾棤鐮村潖鎬у彉鏇?
---

## 瀹屾垚鏍囧噯

| 浠诲姟 | 瀹屾垚鏍囧噯 | 鍗囩骇鍚庣瓑绾?|
|------|---------|-----------|\n| TODO-1 | `retrieval_evidence_items` 鐪熷疄浣跨敤鎴柇缁撴灉锛屾湁娴嬭瘯楠岃瘉 | `ACTIVE` |
| TODO-2 | 鐧藉悕鍗曟牎楠屼笂绾匡紝闈炴硶浜嬩欢绫诲瀷杩斿洖 400 | `ACTIVE`锛堝畨鍏ㄨ竟鐣屽畬鏁达級 |
| TODO-3 | 璺敱鐭╅樀鍐崇瓥鐪熷疄褰卞搷妯″瀷閫夋嫨锛屾湁 fallback | `ACTIVE` |
| TODO-4 | 鑷冲皯涓€鏉′笟鍔¤矾寰勮皟鐢?`resolve()`锛屾湁娴嬭瘯 | `ACTIVE` |
| TODO-5 | 鍙岀 E2E 鑱旇皟閫氳繃锛屾湁鎴浘/鏃ュ織璇佹嵁 | `E2E_VERIFIED` |
| TODO-6 | 楠ㄦ灦鑺傜偣濉厖鐪熷疄閫昏緫锛宍run_as_sse_adapter` 涓嶅啀鍙槸閫忎紶 | `ACTIVE` |
| TODO-7 | SLA 鐩戞帶涓婄嚎锛岃繛缁け璐?闄嶇骇鐜?寤惰繜鍛婅鍙娴嬶紝鏉ユ簮鏍￠獙鍙厤缃?| `PROD_READY`锛堟渶灏忓姞鍥猴級 |
| TODO-8 | Governor/Recall/QA 鎺ュ叆鐪熷疄涓氬姟璋冪敤锛屾湁闄嶇骇绛栫暐鍜屾祴璇曢獙璇?| `ACTIVE`锛堟繁搴︿笟鍔″寲锛?|
| TODO-9 | RecallFusionNode 棰勫彫鍥炵粨鏋滈€忎紶鑷?`_sse_event_generator`锛屾秷闄ゅ弻閲嶅彫鍥?| `ACTIVE`锛堥摼璺棴鐜級 |
| TODO-10 | RecallPack slot 娉ㄥ叆 + GovernorNode 涓ゆ潯閴存潈璺緞缁熶竴 | `ACTIVE`锛堥摼璺竴鑷达級 |

---

## 杩戞湡瀹屾垚锛?026-03-12 绗簩鎵癸級

### TODO-11锛歐ebUI 鐩戞帶鎵╁睍锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堢洃鎺ц鐩栫巼澶у箙鎻愬崌锛?
**鑳屾櫙**锛氬師 WebUI 浠呯洃鎺?6 涓竟缂樻湇鍔?+ 10 涓?LLM 鑺傜偣锛? 寮犳棩蹇楄〃锛夛紝瑕嗙洊鐜囨瀬浣庛€?
**瀹屾垚鍐呭**锛?
**鍓嶇淇锛? 澶?Bug锛?*锛?- `api/obs.js`锛氫慨澶?`fetchSessions` `time_range鈫抴indow_minutes` 鍙傛暟鏄犲皠澶辨晥
- `api/obs.js`锛氫慨澶?`fetchMetricsTimeseries` 鍙傛暟鏄犲皠锛坄range_minutes/interval_minutes`锛?- `Sessions.jsx`锛氫慨澶?`ts_ms` 鏃堕棿鏍煎紡鍖栥€乣status` 鏄犲皠銆佸瓧娈靛榻?- `Metrics.jsx`锛氬鍔?`transformApiData()` 閫傞厤鍚庣 `points[]` 鏍煎紡锛堝師鍥捐〃姘歌繙鏄剧ず mock 鏁版嵁锛?
**鍚庣鏂板 4 涓鐐?*锛?- `GET /obs/governor/stats`锛欸overnor 鍐崇瓥婕忔枟锛堟壒鍑?鎷掔粷/reason_code 鍒嗗竷/鏃跺簭瓒嬪娍锛?- `GET /obs/errors`锛氬叏灞€閿欒缁熻锛坰tage 鍒嗗竷/Top10 reason_code/鏈€杩戦敊璇垪琛級
- `GET /obs/subsystems/stats`锛?1 涓瓙绯荤粺鍋ュ悍鐭╅樀锛圡emory/Retrieval/Skill/Airlock/Bond/Vision/Sandbox/Channel/Intent/QA/Agent Loop锛?- `GET /obs/agent_loop/stats`锛欰gent Loop 璇︾粏缁熻锛堢洰鏍囪揪鎴愮巼/骞冲潎杞/鏈€杩戞墽琛屽垪琛級

**鍓嶇鏂板 Insights 椤甸潰**锛坄#/insights`锛夛細
- Governor 鍐崇瓥婕忔枟锛氭眹鎬荤粺璁?+ 鏃跺簭瓒嬪娍鍥?+ reason_code 妯悜鏌辩姸鍥?- 鍏ㄥ眬閿欒杩借釜锛氶敊璇秼鍔垮浘 + Top10 reason_code + 鏈€杩戦敊璇〃鏍?- 瀛愮郴缁熷仴搴风煩闃碉細11 涓瓙绯荤粺鍗＄墖锛堥鑹茬紪鐮侊細缁?姗?绾級
- Agent Loop 缁熻锛氳揪鎴愮巼姹囨€?+ 鏈€杩戞墽琛岃鎯呰〃鏍?
**鐩戞帶瑕嗙洊鐜?*锛? 寮犳棩蹇楄〃 鈫?**13 寮?*鏃ュ織琛?
**鏀瑰姩鏂囦欢**锛?- `host_brain/app/obs_routes.py`锛氭柊澧?4 涓鐐?- `host_brain/app/monitor_ui/src/api/obs.js`锛氫慨澶嶅弬鏁版槧灏?+ 鏂板 4 涓?API 鍑芥暟
- `host_brain/app/monitor_ui/src/pages/Insights.jsx`锛氭柊寤?Insights 椤甸潰
- `host_brain/app/monitor_ui/src/pages/Metrics.jsx`锛氫慨澶嶆暟鎹€傞厤
- `host_brain/app/monitor_ui/src/pages/Sessions.jsx`锛氫慨澶嶅瓧娈垫槧灏?- `host_brain/app/monitor_ui/src/pages/Overview.jsx`锛氬鍔犳湇鍔″垎缁?success_rate/绐楀彛閫夋嫨鍣?- `host_brain/app/monitor_ui/src/components/Layout.jsx`锛氬鍔犺疆璇㈠紑鍏冲拰闂撮殧閫夋嫨鍣?
---

### TODO-12锛歐ebUI Trace 鐢樼壒鍥撅紙宸插畬鎴愶紝2026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堣鍥惧畬鍠勶級

**闂**锛歍race 椤靛彧灞曠ず DAG 鑺傜偣鎷撴墤鍥撅紝鏃犳硶鐩磋鐪嬪埌鍚勮妭鐐圭殑鏃跺簭鍏崇郴鍜岃€楁椂鍗犳瘮銆?
**淇**锛?- 鏂板 `GanttView` 缁勪欢锛屽熀浜庤妭鐐?`latency_ms` + 鎷撴墤杈规帹绠楀悇鑺傜偣鏈€鏃╁紑濮嬫椂闂?- 鏀寔 DAG 鍥?/ 鐢樼壒鍥捐鍥惧垏鎹紙鍙充笂瑙掑垏鎹㈡寜閽級
- 鐢樼壒鍥剧壒鎬э細
  - 棰滆壊缂栫爜锛堟寜鑺傜偣绫诲瀷锛歩ntent/governor/memory/llm/tts/vts锛?  - 鏃堕棿杞村埢搴︼紙5 绛夊垎锛?  - 鐐瑰嚮琛岃Е鍙戣妭鐐硅鎯呬晶杈归潰鏉?  - 鐘舵€侀鑹茶儗鏅紙缁?姗?绾級
  - 鍥句緥璇存槑

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/monitor_ui/src/pages/Trace.jsx`锛氬鍔?`GanttView` 缁勪欢 + `computeGanttTimeline` 鍑芥暟 + 瑙嗗浘鍒囨崲閫昏緫

---

### TODO-13锛歚/v1/host/input` 鍓綔鐢ㄥ彲瑙傛祴鎬э紙宸插畬鎴愶紝2026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堝壇浣滅敤鐢熷懡鍛ㄦ湡鍙娴嬶級

**闂**锛歚/v1/host/input` 杩斿洖 `ACCEPTED` 鍚庯紝fire-and-forget 鐨?main_chain 澶勭悊缁撴灉瀹屽叏涓嶅彲瑙傛祴锛屾棤娉曞垽鏂壇浣滅敤鏄惁鐪熸瀹屾垚銆?
**淇**锛氬湪 `_dispatch_to_graph` 涓啓鍏?`ledger.log_voice_event`锛岃拷韪畬鏁寸敓鍛藉懆鏈燂細

| phase | 鍚箟 |
|-------|------|
| `ACCEPTED` | HTTP 鍝嶅簲宸茶繑鍥烇紝鍓綔鐢ㄥ嵆灏嗚Е鍙?|
| `DISPATCHED` | 寮傛浠诲姟宸插垱寤猴紝main_chain 璋冪敤鍗冲皢鍙戣捣 |
| `COMPLETED` | main_chain 澶勭悊瀹屾垚锛屽壇浣滅敤宸茶惤鍦?|
| `FAILED` | main_chain 澶勭悊澶辫触锛屽壇浣滅敤鏈惤鍦?|
| `NOT_WIRED` | main_chain 灏氭湭鎺ュ叆锛圛mportError 闄嶇骇锛?|

**鍙娴嬫€?*锛氶€氳繃 `GET /obs/subsystems/stats` 鐨?`channel` 瀛愮郴缁熷彲鏌ョ湅 INPUT_* 浜嬩欢缁熻銆?
**鏀瑰姩鏂囦欢**锛?- `host_brain/app/input_routes.py`锛氭柊澧?`_get_ledger_safe()`銆乣_log_input_event()` 鍑芥暟锛沗_dispatch_to_graph` 澧炲姞 ACCEPTED/DISPATCHED/COMPLETED/FAILED/NOT_WIRED 浜旈樁娈垫棩蹇?
---

### TODO-14锛歋CENE_VISION_UI HMAC 閴存潈鍔犲浐锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚PROD_READY`锛堝绉熸埛閴存潈鍔犲浐锛?
**闂**锛歚SCENE_VISION_UI` 浠呮湁鏉ユ簮 IP 鏍￠獙锛屾棤绛惧悕楠岃瘉锛屽绉熸埛鍦烘櫙涓嬪瓨鍦ㄤ吉閫犺姹傞闄┿€?
**淇**锛氬湪 `_vision_ui_sse_generator` 涓鍔?HMAC-SHA256 绛惧悕鏍￠獙锛?- 鐜鍙橀噺 `HOST_VISION_HMAC_SECRET` 闈炵┖鏃跺惎鐢?- 绛惧悕娑堟伅鏍煎紡锛歚f"{request_id}:{session_id}:{ts_ms}"`
- 璇锋眰鏂瑰湪 `request_body.hmac_signature` 涓彁渚涚鍚?- 鏍￠獙澶辫触鏃讹細鍐欏叆 `ledger.log_error(stage=S_VISION_HMAC_REJECTED)`锛岃繑鍥?`REJECT` COMMIT 浜嬩欢
- 鏍￠獙椤哄簭锛欻MAC 绛惧悕 鈫?鏉ユ簮 IP 鏍￠獙锛堜袱灞傞槻鎶わ級

**鍥為€€鏂瑰紡**锛氫笉璁剧疆 `HOST_VISION_HMAC_SECRET` 鐜鍙橀噺锛孒MAC 鏍￠獙鑷姩璺宠繃锛堝悜鍚庡吋瀹癸級銆?
**鏀瑰姩鏂囦欢**锛?- `host_brain/app/chat_handler_vision.py`锛歚_vision_ui_sse_generator` 澧炲姞 HMAC 绛惧悕鏍￠獙閫昏緫

---

## 杩戞湡瀹屾垚锛?026-03-12 绗洓鎵癸級

### TODO-18锛歋tateArbiter 鎺ュ叆 BondGraph 鏌ヨ锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堝洓璺?StateArbiter 鏌ヨ鍏ㄩ儴鎺ュ叆锛?
**闂**锛歚http_hooks.py` 娉ㄥ唽浜?world/style/episodic 涓夎矾鐘舵€佹簮锛屼絾 BondGraph 鍏崇郴寮哄害浠庢湭琚煡璇紝`bond_graph` slot 鍦?PromptContext 涓湁瀹氫箟浣嗗浜庣┖杞姸鎬併€?
**淇**锛?- 鏂板 `_resolve_state_arbiter_bond_fact()`锛氶€氳繃 `BondStageEngine.get_bond_state()` 鏌ヨ褰撳墠鍏崇郴闃舵
- 鏋勫缓鍏崇郴涓婁笅鏂囨枃鏈紙bond_stage/bond_score/bond_label/interaction_count锛?- 鏋勫缓 `slot_name=bond_graph` 璇佹嵁鏉＄洰锛坰core=0.65锛岃拷鍔犲埌 episodic_latest 涔嬪悗锛?- 鍦?`_sse_event_generator` 涓皢 style/episodic/bond 涓夎矾鏌ヨ鏀逛负 `asyncio.gather` 骞惰鎵ц
- `COMMIT.audit_refs.retrieval_summary` 鏂板 `bond_graph` 瀛楁锛屽彲瑙傛祴娉ㄥ叆鐘舵€?
**娉ㄥ叆浼樺厛绾?*锛?```
world_canon(1.0) > style_canon(0.85) > episodic_latest(0.75) > bond_graph(0.65) > 鍏朵粬鍙洖
```

**闄嶇骇绛栫暐**锛欱ondStageEngine 涓嶅彲鐢ㄦ椂鐙珛闄嶇骇锛屼笉闃绘柇涓婚摼璺?
**鏀瑰姩鏂囦欢**锛?- `host_brain/app/chat_stream_handler.py`锛氭柊澧?`_resolve_state_arbiter_bond_fact()`锛涗笁璺苟琛屾煡璇㈡敼涓?`asyncio.gather`

**楠岃瘉**锛?- `pytest -q tests/unit/test_new_features_todo18_20.py::TestBondGraphStateArbiter`锛? passed锛?
---

### TODO-19锛歐ebUI Canon 绠＄悊椤甸潰锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圕anon 绠＄悊鐣岄潰涓婄嚎锛?
**闂**锛歚canon_routes.py` 鏈夊畬鏁寸殑鎻愭 CRUD API锛堟彁妗堝垪琛?鎻愪氦/瀹℃牳/鎷掔粷/鏂囨。鍒楄〃锛夛紝浣?WebUI 瀹屽叏娌℃湁瀵瑰簲鐣岄潰锛岃繍缁翠汉鍛樻棤娉曢€氳繃 UI 绠＄悊 Canon 鎻愭銆?
**淇**锛?- 鏂板缓 `Canon.jsx` 椤甸潰锛屽寘鍚細
  - **鎻愭鍒楄〃**锛氬垎椤靛睍绀猴紝鏀寔 pending/APPLIED/rejected 鐘舵€佽繃婊わ紝鍙睍寮€鏌ョ湅璇︽儏
  - **鎻愪氦鎻愭**锛氬脊绐楄〃鍗曪紙proposal_id/target_doc_id/title/body/tags/summary锛?  - **瀹℃牳鎿嶄綔**锛氶€氳繃锛坅pprove锛? 鎷掔粷锛坮eject锛夋寜閽紝pending 鐘舵€佷笅鍙搷浣?  - **SSOT 鏂囨。**锛氬睍绀哄綋鍓?World Canon 鏂囨。蹇収锛屾敮鎸佸睍寮€鏌ョ湅姝ｆ枃
- 鏂板 5 涓?Canon API 鍑芥暟锛坄obs.js`锛夛細`fetchCanonProposals/fetchCanonDocuments/submitCanonProposal/approveCanonProposal/rejectCanonProposal`
- `App.jsx` 娉ㄥ唽 `/canon` 璺敱
- `Layout.jsx` 瀵艰埅鏍忔柊澧?Canon 鍏ュ彛锛圔ookOpen 鍥炬爣锛?
**鏀瑰姩鏂囦欢**锛?- `host_brain/app/monitor_ui/src/pages/Canon.jsx`锛氭柊寤?Canon 绠＄悊椤甸潰
- `host_brain/app/monitor_ui/src/api/obs.js`锛氭柊澧?5 涓?Canon API 鍑芥暟
- `host_brain/app/monitor_ui/src/App.jsx`锛氭敞鍐?`/canon` 璺敱
- `host_brain/app/monitor_ui/src/components/Layout.jsx`锛氬鑸爮鏂板 Canon 鍏ュ彛

---

### TODO-20锛欳anonProposalNode 鎺ュ叆涓婚摼锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圕anon 鎻愭鑷姩鎻愪氦锛?
**闂**锛歀LM 杈撳嚭鍖呭惈涓栫晫瑙傛洿鏂颁俊鍙锋椂锛屼富閾捐矾娌℃湁浠讳綍鑺傜偣鑷姩鎻愪氦 Canon 鎻愭锛屼笘鐣岃鏇存柊瀹屽叏渚濊禆浜哄伐鎿嶄綔 `/canon/proposals` API銆?
**淇**锛?- 鏂板 `CanonProposalNode`锛屾彃鍏ヤ富閾捐矾 `AuditNode` 涔嬪悗銆乣AirlockSanitizerNode` 涔嬪墠
- 瑙﹀彂鏉′欢锛堟弧瓒充换涓€锛夛細
  - `final_response` 鍖呭惈 `[CANON_UPDATE]`/`[涓栫晫瑙傛洿鏂癩`/`[WORLD_UPDATE]`/`[CANON_PROPOSAL]` 鏍囪
  - `audit_tags` 鍖呭惈 `CANON_PROPOSAL_REQUESTED`锛堢敱澶栭儴娉ㄥ叆锛?  - `ctx.metadata["canon_proposal"]` 涓湁棰勬瀯寤虹殑鎻愭鍐呭
- 瑙﹀彂鍚庨€氳繃 `WorldCanonStore.submit_proposal` 鍐欏叆 ledger锛坰tatus=pending锛岄渶浜哄伐瀹℃牳锛?- 闄嶇骇绛栫暐锛?  - 鎻愭閲嶅锛堝箓绛夛級鈫?`SKIPPED_DUPLICATE`锛屼笉鎶ラ敊
  - Store 涓嶅彲鐢?鈫?`DEGRADED`锛屾爣璁?`CANON_PROPOSAL_DEGRADED`锛屼笉闃绘柇閾捐矾
  - 鏃犺Е鍙戜俊鍙?鈫?`SKIPPED`锛岃烦杩?
**鏀瑰姩鏂囦欢**锛?- `host_brain/app/graph/main_chain.py`锛氭柊澧?`CanonProposalNode`锛沗NODE_TIMEOUT` 鏂板 `canon_proposal: 2.0`锛沗_NODE_FACTORIES` 鎻掑叆 `CanonProposalNode`锛沗post_node_order` 鏂板 `canon_proposal`

**楠岃瘉**锛?- `pytest -q tests/unit/test_new_features_todo18_20.py::TestCanonProposalNode`锛? passed锛?- 鍚堝苟杩愯锛? passed锛堝惈鎵€鏈夋柊澧炴祴璇曪紝鏃犲洖褰掞級

**鍥為€€鏂瑰紡**锛歚CanonProposalNode` 鏃犺Е鍙戜俊鍙锋椂鑷姩璺宠繃锛屼笉褰卞搷涓婚摼璺紱闄嶇骇鏃朵笉闃绘柇閾捐矾

---



### TODO-15锛欵pisodicStoreNode 鐪熷疄涓氬姟鍖栵紙宸插畬鎴愶紝2026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堢湡瀹炶蹇嗗啓鍏ワ級

**闂**锛歚EpisodicStoreNode.execute()` 鍙爣璁?`READY_TO_PERSIST` 鐘舵€侊紝鏈帴鍏ョ湡瀹?`persist_chat_episodic_memory`锛屾儏鑺傝蹇嗗疄闄呬笂浠庢湭閫氳繃 MainChain 鍐欏叆銆?
**淇**锛?- 鎺ュ叆鐪熷疄 `persist_chat_episodic_memory`锛屼粠 `ctx.final_response` + `ctx.user_message` 鏋勫缓璁板繂鏂囨湰
- 浠?`ctx.metadata["llm_gateway"]["emotion_context"]` 鎻愬彇鎯呯华涓婁笅鏂囷紙鍚?emotion_tag/turn_count锛?- 閫氳繃 `asyncio.to_thread` 寮傛鎵ц锛堜笉闃诲浜嬩欢寰幆锛?- 闄嶇骇绛栫暐锛?  - `EpisodicStore` 涓嶅彲鐢?鈫?`SKIPPED_NO_STORE`
  - `MemoryGuard` 鎷掔粷 鈫?`EPISODIC_SKIPPED:{reason_code}`
  - 寮傚父 鈫?`DEGRADED`锛屾爣璁?`EPISODIC_DEGRADED` 瀹¤鏍囩
  - 鏃犳渶缁堝搷搴?鈫?`SKIPPED_NO_RESPONSE`

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/graph/main_chain.py`锛歚EpisodicStoreNode.execute()` 鎺ュ叆鐪熷疄鍐欏叆閫昏緫

**楠岃瘉**锛?- `pytest -q tests/unit/test_new_features_todo15_17.py::TestEpisodicStoreNode`锛? passed锛?
**鍥為€€鏂瑰紡**锛歚EpisodicStore` 涓嶅彲鐢ㄦ椂鑷姩闄嶇骇锛屼笉闃绘柇涓婚摼璺?
---

### TODO-16锛欰uditNode 鐪熷疄鍐欏叆锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堝璁″揩鐓х湡瀹炶惤鍦帮級

**闂**锛歚AuditNode.execute()` 鍙啓 `ctx.metadata["audit"]`锛屾湭鎺ュ叆鐪熷疄 ledger 鍐欏叆锛屽璁″揩鐓у疄闄呬笂鍙瓨鍦ㄥ唴瀛樹腑锛岃繘绋嬮噸鍚悗涓㈠け銆?
**淇**锛?- 鎺ュ叆鐪熷疄 `ledger.log_error`锛屽皢 `ChainContext` 瀹¤蹇収鍐欏叆 ledger
- 蹇収鍐呭锛歚audit_id`銆乣request_id`銆乣session_id`銆乣task_type`銆乣audit_tags`銆乣node_timings_ms`銆乣metadata_keys`銆乣governor_passed`銆乣final_response_chars`銆乣failure_event`
- 閫氳繃 `asyncio.to_thread` 寮傛鎵ц锛堜笉闃诲浜嬩欢寰幆锛?- 闄嶇骇绛栫暐锛歭edger 涓嶅彲鐢ㄦ椂浠呰褰曟湰鍦版棩蹇楋紝涓嶉樆鏂富閾捐矾

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/graph/main_chain.py`锛歚AuditNode.execute()` 鎺ュ叆鐪熷疄 ledger 鍐欏叆

**楠岃瘉**锛?- `pytest -q tests/unit/test_new_features_todo15_17.py::TestAuditNode`锛? passed锛?
**鍥為€€鏂瑰紡**锛歭edger 涓嶅彲鐢ㄦ椂鑷姩闄嶇骇锛屼笉闃绘柇涓婚摼璺?
---

### TODO-17锛歋tateArbiter 鎵╁睍 style/episodic 鏌ヨ锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堜笁璺?StateArbiter 鏌ヨ鍏ㄩ儴鎺ュ叆锛?
**闂**锛歚_sse_event_generator` 涓彧鏌ヨ `world_canon_facts`锛宍style_canon_facts` 鍜?`episodic_latest` 铏藉凡鍦?`http_hooks.py` 娉ㄥ唽锛屼絾浠庢湭琚富閾捐矾鏌ヨ锛屾敞鍐岀殑鐘舵€佹簮澶勪簬绌鸿浆鐘舵€併€?
**淇**锛?- 鏂板 `_resolve_state_arbiter_style_fact()`锛氭煡璇?`style_canon_facts`锛屾瀯寤?`slot_name=style_canon` 璇佹嵁鏉＄洰锛坰core=0.85锛岃拷鍔犲埌 world_canon 涔嬪悗锛?- 鏂板 `_resolve_state_arbiter_episodic_fact()`锛氭煡璇?`episodic_latest`锛屾瀯寤?`slot_name=episodic_latest` 璇佹嵁鏉＄洰锛坰core=0.75锛岃拷鍔犲埌鏈熬锛?- 鍦?`_sse_event_generator` 涓皟鐢ㄤ袱涓柊鍑芥暟锛屽皢缁撴灉娉ㄥ叆 `retrieval_evidence_items`
- `COMMIT.audit_refs.retrieval_summary` 鏂板 `style_canon` 鍜?`episodic_latest` 瀛楁锛屽彲瑙傛祴娉ㄥ叆鐘舵€?
**娉ㄥ叆浼樺厛绾?*锛?```
world_canon锛坰core=1.0锛? style_canon锛坰core=0.85锛? episodic_latest锛坰core=0.75锛? 鍏朵粬鍙洖
```

**闄嶇骇绛栫暐**锛歋tateArbiter 涓嶅彲鐢ㄦ椂鍚勮矾鐙珛闄嶇骇锛屼笉闃绘柇涓婚摼璺?
**鏀瑰姩鏂囦欢**锛?- `host_brain/app/chat_stream_handler.py`锛氭柊澧?`_resolve_state_arbiter_style_fact()` 鍜?`_resolve_state_arbiter_episodic_fact()` 鍑芥暟锛沗_sse_event_generator` 涓皟鐢ㄥ苟娉ㄥ叆

**楠岃瘉**锛?- `pytest -q tests/unit/test_new_features_todo15_17.py::TestStateArbiterStyleEpisodicResolvers`锛? passed锛?- 鍚堝苟杩愯锛?3 passed锛堝惈鎵€鏈夌浉鍏冲崟鍏冩祴璇曪紝鏃犲洖褰掞級

**鍥為€€鏂瑰紡**锛歋tateArbiter 涓嶅彲鐢ㄦ椂鑷姩闄嶇骇锛屼笉闃绘柇涓婚摼璺?
---

### TODO-21锛歁emoryGuard 鎺ュ叆 RecallFusionNode锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圡emoryGuard 杩囨护宸叉帴鍏ュ彫鍥為摼璺級

**闂**锛歚RecallFusionNode` 瀹屾垚涓冭矾鍙洖鍚庯紝`retrieval_evidence_items` 鏈粡杩囨护鐩存帴娉ㄥ叆 prompt锛宍NARRATIVE_ONLY` 绾у埆鐨?dream 鍐呭鍙兘姹℃煋涓婁笅鏂囥€?
**淇**锛?- 鍦?`RecallFusionNode.execute()` 鐪熷疄鍙洖鎴愬姛鍚庯紝瀵?`retrieval_evidence_items` 閫愭潯妫€鏌?`evidence_level`
- 杩囨护鎺?`evidence_level=NARRATIVE_ONLY` 鐨勬潯鐩紙闃叉 dream 鍐呭姹℃煋 prompt锛?- 杩囨护缁撴灉鍐欏叆 `ctx.metadata["recall_fusion"]["memory_guard"]` 鍜?`ctx.metadata["audit_refs"]["memory_guard"]`
- 闄嶇骇绛栫暐锛歚get_memory_guard()` 涓嶅彲鐢ㄦ椂璺宠繃杩囨护锛坄SKIPPED_NOT_WIRED`锛夛紝涓嶉樆鏂富閾捐矾

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/graph/main_chain.py`锛歚RecallFusionNode.execute()` 鎺ュ叆 MemoryGuard 杩囨护

---

### TODO-22锛欶ailureMatrix 鎺ュ叆 FailureHandlerNode锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛團ailureMatrix 鍒嗙被缁撴灉鍙娴嬶級

**闂**锛歚FailureHandler.handle()` 杩斿洖鐨?`FailureResult`锛堝惈 `should_abort`/`degraded_mode`/`meta`锛変粠鏈鍐欏叆 `ctx.metadata`锛岄摼璺棤娉曟劅鐭ラ檷绾х瓥鐣ワ紝杩愮淮鏃犳硶瑙傛祴澶辫触鍒嗙被缁撴灉銆?
**淇**锛?- 閲嶅啓 `MainChain._invoke_failure_handler()`锛屽皢 `FailureResult` 鍐欏叆 `ctx.metadata["failure_matrix"]`
- 鍐欏叆瀛楁锛歚event`銆乣should_abort`銆乣degraded_mode`銆乣audit_tag`銆乣meta`銆乣user_response_present`
- 鍚屾椂杩藉姞 `FAILURE_MATRIX:{event}` 鍜?`DEGRADED_MODE:{mode}` 鍒?`ctx.audit_tags`
- `MainChain.run()` 涓殑鍐呰仈 failure_handler 璋冪敤缁熶竴鏀逛负璋冪敤 `_invoke_failure_handler`

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/graph/main_chain.py`锛歚_invoke_failure_handler()` 閲嶅啓锛沗run()` 涓粺涓€璋冪敤

---

### TODO-23锛歐ebUI Bond 绠＄悊椤甸潰锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圔ond 绠＄悊椤甸潰宸蹭笂绾匡級

**鏂板鍔熻兘**锛?- `Bond.jsx`锛氱敤鎴峰垪琛?+ 闃舵鍒嗗竷 SVG 楗煎浘 + 鍗曠敤鎴烽樁娈佃鎯咃紙绉垎/瓒嬪娍/鑳藉姏鎻愮ず锛? Bond 浜嬩欢鎻愪氦琛ㄥ崟
- `obs.js`锛氭柊澧?`fetchBondHealth`銆乣fetchBondUsers`銆乣fetchBondStage`銆乣fetchBondSummary`銆乣submitBondEvent` 5 涓?API 鍑芥暟
- `App.jsx`锛氭敞鍐?`/bond` 璺敱
- `Layout.jsx`锛氬鑸爮鏂板 Bond 鍏ュ彛锛圚eart 鍥炬爣锛?
**鏀瑰姩鏂囦欢**锛?- `monitor_ui/src/pages/Bond.jsx`锛堟柊寤猴級
- `monitor_ui/src/api/obs.js`
- `monitor_ui/src/App.jsx`
- `monitor_ui/src/components/Layout.jsx`

---

### TODO-24锛歐ebUI Tools 绠＄悊椤甸潰锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圱ools 绠＄悊椤甸潰宸蹭笂绾匡級

**鏂板鍔熻兘**锛?- `Tools.jsx`锛氬伐鍏峰垪琛紙READ/WRITE 鍒嗙粍杩囨护锛? 宸ュ叿璇︽儏鍗＄墖 + 鍐呭祵鎵ц闈㈡澘锛堟敮鎸?JSON 鍙傛暟杈撳叆锛?- `obs.js`锛氭柊澧?`fetchToolHealth`銆乣fetchToolMetaPack`銆乣fetchToolMeta`銆乣executeTool` 4 涓?API 鍑芥暟
- `App.jsx`锛氭敞鍐?`/tools` 璺敱
- `Layout.jsx`锛氬鑸爮鏂板 Tools 鍏ュ彛锛圵rench 鍥炬爣锛?
**鏀瑰姩鏂囦欢**锛?- `monitor_ui/src/pages/Tools.jsx`锛堟柊寤猴級
- `monitor_ui/src/api/obs.js`
- `monitor_ui/src/App.jsx`
- `monitor_ui/src/components/Layout.jsx`

---

### TODO-25锛歍askScheduler WebUI 绠＄悊椤甸潰锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圓utomation 绠＄悊椤甸潰宸蹭笂绾匡紝鍚庣 scheduler_routes 宸插瓨鍦級

**璇存槑**锛歚host_brain/scheduler/scheduler_routes.py` 宸叉彁渚涘畬鏁?REST API锛坄/scheduler/tasks`銆乣/scheduler/health` 绛夛級锛宍fastapi_app.py` 宸叉敞鍐岃矾鐢便€傛湰娆¤ˉ鍏ㄥ墠绔彲瑙嗗寲灞傘€?
**鏂板鍔熻兘**锛?- `Automation.jsx`锛氫换鍔″垪琛ㄥ崱鐗囷紙鐘舵€?浼樺厛绾?鎵ц娆℃暟/澶辫触娆℃暟锛? 鍚敤/绂佺敤/绔嬪嵆瑙﹀彂鎸夐挳 + 鍋ュ悍鐘舵€佺粺璁?- `obs.js`锛氭柊澧?`fetchSchedulerTasks`銆乣fetchSchedulerHealth`銆乣enableSchedulerTask`銆乣disableSchedulerTask`銆乣triggerSchedulerTask` 5 涓?API 鍑芥暟
- `App.jsx`锛氭敞鍐?`/automation` 璺敱
- `Layout.jsx`锛氬鑸爮鏂板 Automation 鍏ュ彛锛圕lock 鍥炬爣锛?
**鏀瑰姩鏂囦欢**锛?- `monitor_ui/src/pages/Automation.jsx`锛堟柊寤猴級
- `monitor_ui/src/api/obs.js`
- `monitor_ui/src/App.jsx`
- `monitor_ui/src/components/Layout.jsx`

---

### TODO-26锛歁emory 瀛愮郴缁熻缁嗙粺璁＄鐐癸紙宸插畬鎴愶紝2026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛坄GET /obs/memory/stats` 绔偣宸蹭笂绾匡級

**闂**锛歚GET /obs/subsystems/stats` 涓?Memory 瀛愮郴缁熷彧鏈夊仴搴风姸鎬侊紝缂哄皯鍐欏叆閲?鎷掔粷鐜?鍘嬬缉鐜?钂搁娆℃暟绛夊叧閿寚鏍囥€?
**鏂板绔偣**锛歚GET /obs/memory/stats`

瑕嗙洊鍥涗釜 Memory 瀛愮郴缁燂細
- **EpisodicStore**锛氬啓鍏ユ€婚噺銆佹垚鍔熺巼
- **MemoryGuard**锛氭嫆缁濈巼锛圢ARRATIVE_ONLY 杩囨护 + 瀹硶鏍￠獙鎷掔粷锛?- **ContextCompressor**锛氬钩鍧囧帇缂╃巼锛坥utput_tokens/input_tokens 姣斿€硷級
- **LongTermDistiller**锛氳捀棣忔鏁般€佽捀棣忔垚鍔熺巼

鍝嶅簲瀛楁锛歚subsystems[]`銆乣total_writes`銆乣guard_reject_rate`銆乣compress_rate`銆乣distill_count`

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/obs_routes.py`锛氭柊澧?`GET /obs/memory/stats` 绔偣
- `monitor_ui/src/api/obs.js`锛氭柊澧?`fetchMemoryStats` API 鍑芥暟

---

### TODO-27锛欵motionTimeline 鎺ュ叆 LlmGatewayNode锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圗motionTimeline 宸叉帴鍏ヤ富閾捐矾 LlmGatewayNode锛?
**闂**锛歚EmotionTimeline` 鍜?`compute_emotion_echo` 宸插湪 `emotion_timeline.py` 涓畬鏁村疄鐜帮紝浣嗕富閾捐矾浠庢湭璋冪敤 `EmotionTimeline.append()` 杩借釜鎯呮劅浜嬩欢锛屼篃浠庢湭璋冪敤 `rebuild_summary()` 灏嗘儏鎰熻建杩规敞鍏?prompt銆?
**鏂板鍔熻兘**锛?- `LlmGatewayNode.execute()` 涓粠 `ctx.metadata["emotion_tag"]` 鎻愬彇鎯呮劅鏍囩
- 璋冪敤 `EmotionTimeline.append()` 杩借釜鎯呮劅浜嬩欢锛坴alence/arousal 鑷姩鏄犲皠锛?- 璋冪敤 `EmotionTimeline.rebuild_summary(window_ms=300_000)` 鐢熸垚鎯呮劅杞ㄨ抗鎽樿锛屽啓鍏?`ctx.metadata["emotion_timeline"]`
- 璋冪敤 `compute_emotion_echo()` 鐢熸垚鎯呮劅鍥炲０锛坋cho_weight=0.4锛夛紝鍐欏叆 `ctx.metadata["emotion_echo"]`
- 闄嶇骇绛栫暐锛欵motionTimeline 涓嶅彲鐢ㄦ椂璺宠繃锛屼笉闃绘柇涓婚摼璺?
**鏀瑰姩鏂囦欢**锛?- `host_brain/app/graph/main_chain.py`锛氶噸鍐?`LlmGatewayNode.execute()`

---

### TODO-28锛欳ontextCompressor 鎺ュ叆 RecallFusionNode锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圕ontextCompressor 宸叉帴鍏ヤ富閾捐矾 RecallFusionNode锛?
**闂**锛歚ContextCompressor` 鏈夊畬鏁寸殑 `compress()` 鎺ュ彛锛屼絾涓婚摼璺殑 `RecallFusionNode` 鍦ㄥ彫鍥炲悗鐩存帴灏嗚瘉鎹紶閫掔粰 prompt锛屾病鏈夌粡杩囦笂涓嬫枃鍘嬬缉锛屽璇濊疆娆″澶氭椂涓婁笅鏂囦細鏃犻檺澧為暱銆?
**鏂板鍔熻兘**锛?- 鍦?`RecallFusionNode.execute()` 鐨?MemoryGuard 杩囨护涔嬪悗锛岃皟鐢?`ContextCompressor.compress()` 瀵瑰巻鍙茶疆娆¤繘琛屽帇缂╋紙`hybrid` 绛栫暐锛?- 灏?`CompressedContext` 缁熻鍐欏叆 `ctx.metadata["context_compressor"]`锛坰trategy/input_turns/output_turns/total_tokens/degraded锛?- 鍚屾椂鍐欏叆 `ctx.metadata["audit_refs"]["context_compressor"]`锛屼緵瀹¤鑺傜偣浣跨敤
- 闄嶇骇绛栫暐锛歚ContextCompressor` 涓嶅彲鐢ㄦ椂璺宠繃鍘嬬缉锛屼笉闃绘柇涓婚摼璺?
**鏀瑰姩鏂囦欢**锛?- `host_brain/app/graph/main_chain.py`锛歚RecallFusionNode.execute()` 鏂板鍘嬬缉閫昏緫

---

### TODO-29锛歐ebUI Agent Loop 绠＄悊椤甸潰锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圓gent Loop 绠＄悊椤甸潰宸蹭笂绾匡級

**闂**锛歚agent_routes.py` 鎻愪緵浜?`POST /v1/agent/run`锛圫SE 娴佸紡锛夊拰 `GET /v1/agent/info`锛屼絾 WebUI 瀹屽叏娌℃湁瀵瑰簲鐣岄潰銆?
**鏂板鍔熻兘**锛?- `AgentLoop.jsx`锛氱洰鏍囪緭鍏ユ + 鍙傛暟閰嶇疆锛坢ax_rounds/timeout/use_mock/use_edge_rpc/image_consent锛? SSE 瀹炴椂杩涘害灞曠ず锛圓GENT_INIT/ROUND_START/SCREENSHOT_DONE/PLAN_GENERATED/EXECUTE_DONE/LOOP_DONE锛? 鎵ц缁撴灉鎽樿鍗＄墖
- `App.jsx`锛氭敞鍐?`/agent` 璺敱
- `Layout.jsx`锛氬鑸爮鏂板 Agent 鍏ュ彛锛圔ot 鍥炬爣锛?
**鏀瑰姩鏂囦欢**锛?- `monitor_ui/src/pages/AgentLoop.jsx`锛堟柊寤猴級
- `monitor_ui/src/App.jsx`
- `monitor_ui/src/components/Layout.jsx`

---

### TODO-30锛歏TS 閫氶亾缁熻绔偣锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛坄GET /obs/vts/stats` 绔偣宸蹭笂绾匡級

**闂**锛歚channels/vts.py` 宸叉湁瀹屾暣鐨?`resolve_vts_expression()` 鍜?`request_talk_gate()` 鎺ュ彛锛屼絾 `obs_routes.py` 娌℃湁浠讳綍绔偣鏆撮湶 VTS 閫氶亾鐨勭粺璁℃暟鎹€?
**鏂板绔偣**锛歚GET /obs/vts/stats`

瑕嗙洊锛?- 琛ㄦ儏搴旂敤缁熻锛圓PPLIED/DEGRADED/REJECTED 鍒嗗竷锛?- Top 鎯呮劅鏍囩鍒嗗竷锛圱op 8锛?- Talk Gate 浜嬩欢缁熻锛堝厑璁?鎷掔粷锛?
**鏀瑰姩鏂囦欢**锛?- `host_brain/app/obs_routes.py`锛氭柊澧?`GET /obs/vts/stats` 绔偣
- `monitor_ui/src/api/obs.js`锛氭柊澧?`fetchVtsStats` API 鍑芥暟

---

### TODO-31锛歀ongTermDistiller 鎺ュ叆璋冨害鍣紙宸插畬鎴愶紝2026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堣捀棣忎换鍔″凡娉ㄥ唽鍒拌皟搴﹀櫒锛宍GET /obs/distillation/stats` 绔偣宸蹭笂绾匡級

**闂**锛歚LongTermMemoryDistiller.run_distillation_cycle()` 宸插疄鐜板畬鏁寸殑钂搁閫昏緫锛宍make_distillation_task_config()` 涔熸彁渚涗簡璋冨害鍣ㄤ换鍔￠厤缃紝浣嗚皟搴﹀櫒浠庢湭娉ㄥ唽杩欎釜浠诲姟锛岃捀棣忓惊鐜粠鏈瑙﹀彂銆?
**鏂板鍔熻兘**锛?- `http_hooks.py` 鐨?`runtime_lifespan` 涓紝璋冪敤 `make_distillation_task_config()` 骞舵敞鍐屽埌 `SchedulerTaskRegistry`锛堟瘡鏃?03:00 CRON锛?- `obs_routes.py` 鏂板 `GET /obs/distillation/stats`锛氳捀棣忓惊鐜粺璁★紙鎬诲惊鐜鏁?鎴愬姛鐜?鎻愭鎻愪氦閲?璇箟璁板繂鍒涘缓閲?鏈€杩?N 娆″惊鐜鎯咃級
- `obs.js` 鏂板 `fetchDistillationStats` API 鍑芥暟

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/http_hooks.py`锛歚runtime_lifespan` 娉ㄥ唽钂搁璋冨害浠诲姟
- `host_brain/app/obs_routes.py`锛氭柊澧?`GET /obs/distillation/stats` 绔偣
- `monitor_ui/src/api/obs.js`锛氭柊澧?`fetchDistillationStats` API 鍑芥暟

---

### TODO-32锛欼nsights 椤甸潰 Memory 缁熻鍗＄墖锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圡emory 瀛愮郴缁熺粺璁″尯鍧楀凡涓婄嚎锛?
**闂**锛歍ODO-26 宸插疄鐜?`GET /obs/memory/stats` 鍚庣绔偣锛屼絾 `Insights.jsx` 椤甸潰娌℃湁瀵瑰簲鐨?Memory 缁熻鍗＄墖锛屾暟鎹棤娉曞彲瑙嗗寲銆?
**鏂板鍔熻兘**锛?- `Insights.jsx` 鏂板 Memory 瀛愮郴缁熺粺璁″尯鍧楋紙Database 鍥炬爣锛?- 鑱氬悎鎸囨爣鍗＄墖锛欵pisodicStore 鍐欏叆閲?+ MemoryGuard 鎷掔粷鐜?+ ContextCompressor 鍘嬬缉鐜?+ LongTermDistiller 钂搁娆℃暟
- 瀛愮郴缁熻鎯呭崱鐗囷細4 涓瓙绯荤粺鍚勮嚜鐨勬垚鍔熺巼/涓撳睘鎸囨爣锛堟嫆缁濈巼/鍘嬬缉鐜?钂搁娆℃暟锛?鏈€鍚庢椿璺冩椂闂?- 骞惰鍔犺浇锛氫笌鍏朵粬 Insights 鏁版嵁鍚屾椂璇锋眰锛屼笉澧炲姞椤甸潰鍔犺浇鏃堕棿

**鏀瑰姩鏂囦欢**锛?- `monitor_ui/src/pages/Insights.jsx`锛氭柊澧?Memory 缁熻鍖哄潡
- `monitor_ui/src/api/obs.js`锛氬凡鍦?TODO-26 涓坊鍔?`fetchMemoryStats`

---

### TODO-33锛欵motionTimeline 鎽樿娉ㄥ叆 PromptBuilder锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圗motionTimeline 鎽樿宸叉敞鍏?EMOTION_BINDING 鍧楋級

**闂**锛歍ODO-27 宸插湪 `LlmGatewayNode` 涓敓鎴愪簡 `ctx.metadata["emotion_timeline"]`锛堝惈 `dominant_emotion`/`trend`/`entry_count`锛夊拰 `ctx.metadata["emotion_echo"]`锛屼絾 `prompt_builder.py` 鐨?`render_node_generation_prompt()` 瀹屽叏娌℃湁璇诲彇杩欎簺瀛楁锛屾儏鎰熻建杩规憳瑕佸 LLM 鐢熸垚娌℃湁浠讳綍褰卞搷銆?
**鏂板鍔熻兘**锛?- 鍦?`render_node_generation_prompt()` 鐨?`EMOTION_BINDING` 鍧椾腑锛岃拷鍔?`emotion_timeline_dominant`/`emotion_timeline_trend`/`emotion_timeline_entry_count`/`emotion_echo_tag`/`emotion_echo_hint` 瀛楁
- 浠呭湪 EmotionTimeline 鏈夋晥鏁版嵁鏃舵敞鍏ワ紙mode 涓嶄负 SKIPPED/DEGRADED 涓?entry_count > 0锛?- 鏃犳晥鏃舵敞鍏?`NONE` 鍗犱綅绗︼紝淇濇寔 prompt 鏍煎紡绋冲畾

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/prompt_builder.py`锛歚render_node_generation_prompt()` EMOTION_BINDING 鍧楄拷鍔?EmotionTimeline 瀛楁

---

### TODO-34锛欰uditRetention 鎺ュ叆璋冨害鍣紙宸插畬鎴愶紝2026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圓uditRetention 姣忔棩璋冨害浠诲姟宸叉敞鍐岋紝`GET /obs/retention/stats` 绔偣宸蹭笂绾匡級

**闂**锛歚audit/retention.py` 鏈夊畬鏁寸殑 `run_retention_policy()` 瀹炵幇锛屼絾璋冨害鍣ㄤ粠鏈敞鍐岃繖涓换鍔★紝瀹¤鏁版嵁浼氭棤闄愬闀裤€?
**鏂板鍔熻兘**锛?- `http_hooks.py` 鐨?`runtime_lifespan` 涓紝娉ㄥ唽 AuditRetention 璋冨害浠诲姟锛坱ask_id=`audit_retention_daily`锛屾瘡鏃?02:00 CRON锛?- `obs_routes.py` 鏂板 `GET /obs/retention/stats`锛氫繚鐣欑瓥鐣ユ墽琛岀粺璁★紙鏈€杩戞墽琛屾椂闂?鐘舵€?鍚勮〃鍊欓€夊垹闄ら噺/宸插垹闄ら噺/鍙椾繚鎶ら噺锛?
**鏀瑰姩鏂囦欢**锛?- `host_brain/app/http_hooks.py`锛歚runtime_lifespan` 娉ㄥ唽 AuditRetention 璋冨害浠诲姟
- `host_brain/app/obs_routes.py`锛氭柊澧?`GET /obs/retention/stats` 绔偣
- `monitor_ui/src/api/obs.js`锛氭柊澧?`fetchRetentionStats` API 鍑芥暟

---

### TODO-35锛欳anonVectorAutoSync 鎺ュ叆 CanonProposalNode锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圕anonProposalNode 鎻愭鎻愪氦鍚庤嚜鍔ㄨЕ鍙戝悜閲忓悓姝ワ級

**闂**锛歚canon_vector_auto_sync.py` 鏈夊畬鏁寸殑 `trigger_canon_vector_auto_sync()` 鎺ュ彛锛屼絾 `CanonProposalNode` 鍦ㄦ彁妗堟彁浜ゅ悗浠庢湭瑙﹀彂鍚戦噺鍚屾锛屽鑷?Canon SSOT 鍐欏叆鍚?Chroma 鍚戦噺绱㈠紩涓嶅悓姝ャ€?
**鏂板鍔熻兘**锛?- `CanonProposalNode.execute()` 鎻愭鎴愬姛鎻愪氦鍚庯紝璋冪敤 `trigger_canon_vector_auto_sync(include_world=True, include_style=False, reason="canon_proposal_node")`
- 闄嶇骇绛栫暐锛氬悜閲忓悓姝ュけ璐ユ椂浠呰褰曟棩蹇楋紝涓嶉樆鏂富閾捐矾
- 鍚屾缁撴灉鍐欏叆 `ctx.metadata["canon_proposal_node"]["vector_sync"]`
- 鎴愬姛鏃惰拷鍔?`CANON_VECTOR_SYNC_TRIGGERED` audit tag

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/graph/main_chain.py`锛歚CanonProposalNode.execute()` 杩藉姞鍚戦噺鍚屾璋冪敤

---

### TODO-36锛欵videnceCircuitBreaker 鎺ュ叆 RecallFusionNode锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圧ecallFusionNode 宸叉帴鍏ョ啍鏂櫒淇濇姢锛?
**闂**锛歚evidence_circuit_breaker.py` 鏈夊畬鏁寸殑 `EvidenceCircuitBreaker` 瀹炵幇锛圕LOSED/OPEN/HALF_OPEN 鐘舵€佹満锛夛紝浣?`RecallFusionNode` 鍦ㄥ彫鍥炲け璐ユ椂娌℃湁浠讳綍鐔旀柇淇濇姢锛岃繛缁け璐ユ椂浼氭寔缁噸璇曘€?
**鏂板鍔熻兘**锛?- `RecallFusionNode.execute()` 鍙洖鍓嶆鏌ョ啍鏂櫒鐘舵€侊細OPEN 鏃剁洿鎺ヨ烦杩囧彫鍥烇紝杩斿洖绌鸿瘉鎹紙`RECALL_CIRCUIT_OPEN`锛?- 鍙洖鎴愬姛鏃堕噸缃啍鏂櫒澶辫触璁℃暟锛坄_failure_count = 0`锛?- 鍙洖澶辫触鏃堕€氱煡鐔旀柇鍣ㄨ褰曞け璐ワ紙瑙﹀彂闃堝€硷細3 娆″け璐ワ紝鎭㈠瓒呮椂锛?0s锛?- 鐔旀柇鐘舵€佸啓鍏?`ctx.metadata["recall_fusion"]["circuit_breaker"]`

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/graph/main_chain.py`锛歚RecallFusionNode.execute()` 鎺ュ叆 EvidenceCircuitBreaker

---

### TODO-37锛歐ebUI WeChat 閫氶亾绠＄悊椤甸潰锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圵eChat 閫氶亾绠＄悊椤甸潰宸蹭笂绾匡級

**闂**锛歚channels/wechat.py` 鍜?`channels/wechat_health.py` 鏈夊畬鏁寸殑寰俊閫氶亾瀹炵幇锛屼絾 WebUI 瀹屽叏娌℃湁瀵瑰簲鐣岄潰锛屾棤娉曢€氳繃 UI 瑙傛祴寰俊閫氶亾鐘舵€併€?
**鏂板鍔熻兘**锛?- 鏂板缓 `WeChat.jsx` 椤甸潰锛氶€氶亾鍋ュ悍鐘舵€佸崱鐗囷紙UP/DEGRADED/DOWN/UNKNOWN锛? 7 涓粺璁″崱鐗囷紙鎬讳簨浠?鍏ョ珯/鍑虹珯/鎴愬姛/澶辫触/鎴愬姛鐜?棰戠巼闄愬埗娆℃暟锛? Top wxid 棰戠巼闄愬埗缁熻琛?+ 鏈€杩戜簨浠跺垪琛?+ 鎵嬪姩閲嶇疆鎸夐挳
- `obs_routes.py` 鏂板 `GET /obs/wechat/stats` 绔偣锛堥€氶亾缁熻锛夊拰 `POST /obs/wechat/reset` 绔偣锛堟墜鍔ㄩ噸缃級
- `obs.js` 鏂板 `fetchWeChatStats`銆乣resetWeChatChannel`銆乣fetchRetentionStats` 3 涓?API 鍑芥暟
- `App.jsx` 娉ㄥ唽 `/wechat` 璺敱
- `Layout.jsx` 瀵艰埅鏍忔柊澧?WeChat 鍏ュ彛锛圡essageSquare 鍥炬爣锛?
**鏀瑰姩鏂囦欢**锛?- 鏂板缓 `monitor_ui/src/pages/WeChat.jsx`
- `host_brain/app/obs_routes.py`锛氭柊澧?WeChat 缁熻绔偣
- `monitor_ui/src/api/obs.js`锛氭柊澧?WeChat API 鍑芥暟
- `monitor_ui/src/App.jsx`锛氭敞鍐?`/wechat` 璺敱
- `monitor_ui/src/components/Layout.jsx`锛氬鑸爮鏂板 WeChat 鍏ュ彛

---

### TODO-38锛欼nsights 椤甸潰 VTS 缁熻鍗＄墖锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圴TS 缁熻鍖哄潡宸蹭笂绾匡級

**闂**锛歍ODO-30 宸插疄鐜?`GET /obs/vts/stats` 鍚庣绔偣锛屼絾 `Insights.jsx` 椤甸潰娌℃湁瀵瑰簲鐨?VTS 缁熻鍗＄墖锛屾暟鎹棤娉曞彲瑙嗗寲銆?
**鏂板鍔熻兘**锛?- `Insights.jsx` 鏂板 VTS 閫氶亾缁熻鍖哄潡锛圱v 鍥炬爣锛岀矇鑹蹭富棰橈級
- 鑱氬悎鎸囨爣鍗＄墖锛氭€讳簨浠舵暟 + 琛ㄦ儏搴旂敤鐜?+ 闄嶇骇鐜?+ 鎷掔粷鐜?- Top 鎯呮劅鏍囩妯悜鏌辩姸鍥撅紙BarChart锛?- Talk Gate 缁熻闈㈡澘锛堟€昏姹?鍏佽/鎷掔粷 + 鍏佽鐜囪繘搴︽潯锛?- 骞惰鍔犺浇锛氫笌鍏朵粬 Insights 鏁版嵁鍚屾椂璇锋眰

**鏀瑰姩鏂囦欢**锛?- `monitor_ui/src/pages/Insights.jsx`锛氭柊澧?VTS 缁熻鍖哄潡锛屽紩鍏?`Tv` 鍥炬爣鍜?`fetchVtsStats`

---

### TODO-39锛欵motionVector VAD 杩炵画浣撴敞鍏?PromptBuilder锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圴AD 涓夌淮鏁板€煎凡娉ㄥ叆 EMOTION_BINDING 鍧楋級

**闂**锛歚core/metabolism.py` 鏈夊畬鏁寸殑 `EmotionVector`锛圴AD 涓夌淮妯″瀷锛夛紝浣?`prompt_builder.py` 鐨?`EMOTION_BINDING` 鍧楀彧娉ㄥ叆浜嗙鏁ｇ殑 `emotion_tag`/`mood_state`锛屼粠鏈敞鍏?VAD 杩炵画浣撴暟鍊硷紝LLM 鏃犳硶鎰熺煡鎯呮劅鐨勭粏绮掑害寮哄害銆?
**鏂板鍔熻兘**锛?- `render_node_generation_prompt()` 涓柊澧?VAD 鏁版嵁鎻愬彇閫昏緫锛堜粠 `runtime_payload.get("emotion_vector")` 璇诲彇锛?- `EMOTION_BINDING` 鍧楁湯灏捐拷鍔?`emotion_vad_valence`/`emotion_vad_arousal`/`emotion_vad_dominance` 涓変釜瀛楁
- 浠呭湪 VAD 鏁版嵁鏈夋晥鏃舵敞鍏ョ湡瀹炲€硷紙闈炲叏闆讹級锛屾棤鏁堟椂娉ㄥ叆 `NONE`

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/prompt_builder.py`锛氭柊澧?VAD 鎻愬彇閫昏緫 + EMOTION_BINDING 鍧楁墿灞?
---

### TODO-40锛歈A 杩濊缁熻鎺ュ叆 Insights 椤甸潰锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圦A 璐ㄩ噺鐩戞帶鍖哄潡宸蹭笂绾匡級

**闂**锛歚core/qa.py` 鏈夊畬鏁寸殑 QA 杩濊妫€娴嬬郴缁燂紝瀹¤鏁版嵁鍐欏叆 `qa_violation` 琛紝浣?WebUI 瀹屽叏娌℃湁 QA 杩濊缁熻闈㈡澘锛屾棤娉曡娴?LLM 杈撳嚭璐ㄩ噺銆?
**鏂板鍔熻兘**锛?- `obs_routes.py` 鏂板 `GET /obs/qa/stats`锛歈A 杩濊缁熻锛堣繚瑙勭爜鍒嗗竷銆侀檷绾х巼銆侀噸鍐欐垚鍔熺巼銆佹椂搴忚秼鍔裤€佹渶杩戣繚瑙勫垪琛級
- `Insights.jsx` 鏂板 QA 璐ㄩ噺鐩戞帶鍖哄潡锛團ileWarning 鍥炬爣锛岀孩鑹蹭富棰橈級锛氳繚瑙勭爜鍒嗗竷鏌辩姸鍥?+ 杩濊瓒嬪娍鎶樼嚎鍥?+ 鏈€杩戣繚瑙勫垪琛?- `obs.js` 鏂板 `fetchQaStats` API 鍑芥暟

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/obs_routes.py`锛氭柊澧?`/obs/qa/stats` 绔偣
- `monitor_ui/src/pages/Insights.jsx`锛氭柊澧?QA 杩濊缁熻鍖哄潡
- `monitor_ui/src/api/obs.js`锛氭柊澧?`fetchQaStats`

---

### TODO-41锛欵videncePlane LLM 璋冪敤鏃ュ織鎺ュ叆 obs_routes锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圠LM 璋冪敤鎴愭湰缁熻宸蹭笂绾匡級

**闂**锛歚integrations/graph_evidence.py` 鐨?`EvidencePlane` 鏈?`llm_call_log` 琛紝浣?`obs_routes.py` 瀹屽叏娌℃湁璇诲彇杩欎釜琛ㄧ殑绔偣锛孡LM 璋冪敤鎴愭湰鍜屽欢杩熸暟鎹棤娉曢€氳繃 WebUI 瑙傛祴銆?
**鏂板鍔熻兘**锛?- `obs_routes.py` 鏂板 `GET /obs/llm/call-stats`锛歀LM 璋冪敤缁熻锛堟寜 model_id 鍒嗙粍鐨?token 娑堣€?骞冲潎寤惰繜/璋冪敤娆℃暟/P95寤惰繜 + 鏃跺簭瓒嬪娍锛?- `Insights.jsx` 鏂板 LLM 璋冪敤鎴愭湰鍖哄潡锛圸ap 鍥炬爣锛岀惀鐝€鑹蹭富棰橈級锛氬悇妯″瀷 token 娑堣€楁í鍚戞煴鐘跺浘 + 璋冪敤閲忚秼鍔挎姌绾垮浘 + 鍚勬ā鍨嬭鎯呰〃鏍?- `obs.js` 鏂板 `fetchLlmCallStats` API 鍑芥暟

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/obs_routes.py`锛氭柊澧?`/obs/llm/call-stats` 绔偣
- `monitor_ui/src/pages/Insights.jsx`锛氭柊澧?LLM 璋冪敤鎴愭湰鍖哄潡
- `monitor_ui/src/api/obs.js`锛氭柊澧?`fetchLlmCallStats`

---

### TODO-42锛欳heckpointManager 鐘舵€佹帴鍏?obs_routes锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圕heckpoint 鐘舵€佹煡璇㈠凡涓婄嚎锛?
**闂**锛歚integrations/graph_checkpoint.py` 鏈夊畬鏁寸殑 `CheckpointManager`锛屼絾 `obs_routes.py` 娌℃湁浠讳綍 checkpoint 鐘舵€佹煡璇㈢鐐癸紝鏃犳硶瑙傛祴鏂偣缁窇鐘舵€併€?
**鏂板鍔熻兘**锛?- `obs_routes.py` 鏂板 `GET /obs/checkpoints`锛氭渶杩?checkpoint 鍒楄〃锛堝惈瓒呮椂鐘舵€佽繃婊わ級
- `obs_routes.py` 鏂板 `GET /obs/checkpoints/{checkpoint_id}`锛氬崟涓?checkpoint 璇︽儏锛堝惈瀹屾暣 payload锛?- `Trace.jsx` 鏂板 Checkpoint 渚ц竟鏍忥紙Bookmark 鍥炬爣锛岀惀鐝€鑹蹭富棰橈級锛氬綋 trace 涓瓨鍦?checkpoint 鏃舵樉绀猴紝灞曠ず涓柇鍘熷洜/鎴鏃堕棿/payload 鎽樿

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/obs_routes.py`锛氭柊澧?`/obs/checkpoints` 鍜?`/obs/checkpoints/{id}` 绔偣
- `monitor_ui/src/pages/Trace.jsx`锛氭柊澧?Checkpoint 渚ц竟鏍?- `monitor_ui/src/api/obs.js`锛氭柊澧?`fetchCheckpoints`銆乣fetchCheckpointDetail`

---

### TODO-43锛歊ateLimitStore 鍚庣鐘舵€佹帴鍏?WebUI锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堥€熺巼闄愬埗鍚庣鐘舵€佸崱鐗囧凡涓婄嚎锛?
**闂**锛歚governor/rate_limit_store.py` 鏈夊畬鏁寸殑涓夊悗绔疄鐜板拰 `DegradingRateLimitStore` 闄嶇骇鍖呰鍣紝浣?WebUI 瀹屽叏娌℃湁閫熺巼闄愬埗鍚庣鐘舵€侀潰鏉裤€?
**鏂板鍔熻兘**锛?- `obs_routes.py` 鏂板 `GET /obs/rate-limit/backend-stats`锛氶€熺巼闄愬埗鍚庣鐘舵€侊紙褰撳墠鍚庣绫诲瀷/鏄惁闄嶇骇/闄嶇骇鍘熷洜/Top-K key 璁℃暟锛?- `Metrics.jsx` 鏂板閫熺巼闄愬埗鍚庣鐘舵€佸崱鐗囷紙Database 鍥炬爣锛岄潧钃濊壊涓婚锛夛細鍚庣绫诲瀷/闄嶇骇鐘舵€?+ 娲昏穬 key 缁熻 + Top key 璁℃暟琛ㄦ牸

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/obs_routes.py`锛氭柊澧?`/obs/rate-limit/backend-stats` 绔偣
- `monitor_ui/src/pages/Metrics.jsx`锛氭柊澧為€熺巼闄愬埗鍚庣鐘舵€佸崱鐗?- `monitor_ui/src/api/obs.js`锛氭柊澧?`fetchRateLimitBackendStats`

---

### TODO-44锛歏ault Manifest 缁熻鎺ュ叆 WebUI锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圴ault Manifest 缁熻宸蹭笂绾匡級

**闂**锛歚lsp/vault.py` 鏈夊畬鏁寸殑 `ArtifactVault`锛坢anifest 鍒涘缓/undo/GC锛夛紝瀹¤鏁版嵁鍐欏叆 `vault_manifest` 琛紝浣?WebUI 瀹屽叏娌℃湁 Vault 缁熻闈㈡澘銆?
**鏂板鍔熻兘**锛?- `obs_routes.py` 鏂板 `GET /obs/vault/stats`锛歏ault 缁熻锛圓CTIVE/UNDONE/GC'd manifest 鏁伴噺銆佸彲閫嗘瘮渚嬨€佹渶杩?manifest 鍒楄〃锛?- `Canon.jsx` 鏂板 Vault 缁熻闈㈡澘锛圠ock 鍥炬爣锛岀传缃楀叞鑹蹭富棰橈級锛歮anifest 鐘舵€佸垎甯冭繘搴︽潯 + 4 涓仛鍚堟寚鏍囧崱鐗?+ 鏈€杩?manifest 璇︽儏琛ㄦ牸

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/obs_routes.py`锛氭柊澧?`/obs/vault/stats` 绔偣
- `monitor_ui/src/pages/Canon.jsx`锛氭柊澧?Vault Manifest 缁熻闈㈡澘
- `monitor_ui/src/api/obs.js`锛氭柊澧?`fetchVaultStats`

---

### TODO-45锛歋tateArbiter 鎺ュ叆 RecallFusionNode锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圫tateArbiter 浜嬪疄浠茶宸叉帴鍏ヤ富閾捐矾锛?
**闂**锛歚memory/state_arbiter.py` 鏈夊畬鏁寸殑鍏簮浼樺厛绾т徊瑁佸櫒锛屼絾涓婚摼璺殑 `RecallFusionNode` 鍦ㄤ竷璺彫鍥炲悗鐩存帴鍚堝苟缁撴灉锛屾病鏈夌粡杩?StateArbiter 浠茶锛屽璺彫鍥炵粨鏋滃瓨鍦ㄥ啿绐佹椂娌℃湁鏉冨▉瑁佸喅鏈哄埗銆?
**鏂板鍔熻兘**锛?- `RecallFusionNode.execute()` 鍦?ContextCompressor 涔嬪悗鎺ュ叆 `StateArbiter.resolve()`
- 瀵瑰叧閿簨瀹為敭锛坄user.identity`/`user.name`/`user.age`/`user.occupation`锛夎繘琛屼徊瑁?- 浠茶缁撴灉锛坄winner_source`/`conflict_detected`/`is_fallback`锛夊啓鍏?`ctx.metadata["state_arbiter"]`
- 鍐茬獊浜嬩欢鍐欏叆 `ctx.audit_tags`锛坄STATE_ARBITER_CONFLICT:N`锛?- 闄嶇骇绛栫暐锛歋tateArbiter 涓嶅彲鐢ㄦ椂璺宠繃锛屼笉闃绘柇涓婚摼璺?
**鏀瑰姩鏂囦欢**锛?- `host_brain/app/graph/main_chain.py`锛歊ecallFusionNode 鎺ュ叆 StateArbiter

---

### TODO-46锛歊outingMatrix 鎺ュ叆 LlmGatewayNode锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圧outingMatrix 璺敱鍐崇瓥宸叉帴鍏ヤ富閾捐矾锛?
**闂**锛歚llm/routing_matrix.py` 鏈夊畬鏁寸殑澶氱淮搴﹁矾鐢辩煩闃碉紝浣?`LlmGatewayNode` 鍦ㄩ€夋嫨妯″瀷鏃舵病鏈夎皟鐢?`RoutingMatrix.select()`锛屽畬鍏ㄧ粫杩囦簡璺敱閫昏緫銆?
**鏂板鍔熻兘**锛?- `LlmGatewayNode.execute()` 鎺ュ叆 `RoutingMatrix.select()`
- 浠?`ctx.metadata` 鎻愬彇 `task_type`/`risk_level`/`context_length`锛岃绠楄矾鐢卞喅绛?- `RouteDecision`锛坄selected_model`/`is_fallback`/`fallback_reason`锛夊啓鍏?`ctx.metadata["routing_matrix"]`
- 璺敱缁撴灉鍐欏叆 `ctx.audit_tags`锛坄ROUTING_SELECTED:model` 鎴?`ROUTING_FALLBACK:model`锛?- 闄嶇骇绛栫暐锛歊outingMatrix 涓嶅彲鐢ㄦ椂浣跨敤榛樿妯″瀷锛屼笉闃绘柇涓婚摼璺?
**鏀瑰姩鏂囦欢**锛?- `host_brain/app/graph/main_chain.py`锛歀lmGatewayNode 鎺ュ叆 RoutingMatrix

---

### TODO-47锛歊outingMatrix 璺敱鍐崇瓥缁熻鎺ュ叆 WebUI锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堣矾鐢卞喅绛栫粺璁″凡涓婄嚎锛?
**闂**锛歚llm_route_log` 琛ㄨ褰曚簡姣忔璺敱鍐崇瓥锛屼絾 `obs_routes.py` 娌℃湁浠讳綍璺敱缁熻绔偣锛屾棤娉曡娴嬫ā鍨嬮€夋嫨鍒嗗竷鍜岄檷绾ч鐜囥€?
**鏂板鍔熻兘**锛?- `obs_routes.py` 鏂板 `GET /obs/llm/route-stats`锛氳矾鐢卞喅绛栫粺璁★紙鎸?model_id 鍒嗙粍鐨勯€夋嫨娆℃暟/闄嶇骇娆℃暟/闄嶇骇鐜?task_type 鍒嗗竷锛?- `Insights.jsx` 鏂板璺敱鍐崇瓥鍒嗗竷鍖哄潡锛圙itBranch 鍥炬爣锛岄潧钃濊壊涓婚锛夛細妯″瀷閫夋嫨鍒嗗竷杩涘害鏉?+ 浠诲姟绫诲瀷鍒嗗竷杩涘害鏉?
**鏀瑰姩鏂囦欢**锛?- `host_brain/app/obs_routes.py`锛氭柊澧?`/obs/llm/route-stats` 绔偣
- `monitor_ui/src/pages/Insights.jsx`锛氭柊澧?RouteStatsSection 瀛愮粍浠?- `monitor_ui/src/api/obs.js`锛氭柊澧?`fetchLlmRouteStats`

---

### TODO-48锛欼nputDispatch 鐢熷懡鍛ㄦ湡缁熻鎺ュ叆 WebUI锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堣緭鍏ュ垎鍙戠粺璁″凡涓婄嚎锛?
**闂**锛歚input_routes.py` 鐨?`_dispatch_to_graph()` 鍐欏叆浜嗗畬鏁寸殑鐢熷懡鍛ㄦ湡鏃ュ織锛屼絾 WebUI 瀹屽叏娌℃湁瀵瑰簲鐨勮緭鍏ュ垎鍙戠粺璁￠潰鏉裤€?
**鏂板鍔熻兘**锛?- `obs_routes.py` 鏂板 `GET /obs/input/dispatch-stats`锛氳緭鍏ュ垎鍙戠粺璁★紙鎸?source 鍒嗙粍鐨?ACCEPTED/COMPLETED/FAILED/NOT_WIRED 鏁伴噺銆佹垚鍔熺巼銆佸钩鍧囧欢杩?+ 鏃跺簭瓒嬪娍锛?- `Overview.jsx` 鏂板杈撳叆鍒嗗彂缁熻鍗＄墖锛圓rrowDownToLine 鍥炬爣锛夛細4 涓仛鍚堟寚鏍囧崱鐗?+ 鎸?source 鍒嗙粍鐨勮鎯呭崱鐗?
**鏀瑰姩鏂囦欢**锛?- `host_brain/app/obs_routes.py`锛氭柊澧?`/obs/input/dispatch-stats` 绔偣
- `monitor_ui/src/pages/Overview.jsx`锛氭柊澧炶緭鍏ュ垎鍙戠粺璁″尯鍧?- `monitor_ui/src/api/obs.js`锛氭柊澧?`fetchInputDispatchStats`

---

### TODO-49锛歊ecallPackBuilder token 棰勭畻缁熻鎺ュ叆 WebUI锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圧ecallPack token 棰勭畻缁熻宸蹭笂绾匡級

**闂**锛歚recall/recall_pack_builder.py` 鐨?`RecallPackBuilder.build()` 鍦?`subgraph_trace` 涓褰曚簡鍚勮矾鍙洖鐨?token 娑堣€楀拰鎴柇鎯呭喌锛屼絾杩欎簺鏁版嵁鍙瓨鍦ㄤ簬 `ctx.metadata`锛屼粠鏈啓鍏ュ璁℃棩蹇楋紝涔熸病鏈?WebUI 闈㈡澘灞曠ず銆?
**鏂板鍔熻兘**锛?- `RecallFusionNode.execute()` 鎺ュ叆 `RecallPackBuilder.build()`锛屽皢 `subgraph_trace` 鍐欏叆 `ctx.metadata["recall_pack_trace"]`
- `obs_routes.py` 鏂板 `GET /obs/recall-pack/stats`锛歊ecallPack token 棰勭畻缁熻锛堝悇妲戒綅骞冲潎 token 娑堣€?鎴柇鐜?鍐茬獊鎶戝埗鐜囷級
- `Insights.jsx` 鏂板 PromptContext 棰勭畻缁熻鍖哄潡锛圠ayers 鍥炬爣锛岄潚鑹蹭富棰橈級锛? 涓仛鍚堟寚鏍囧崱鐗?+ 鍚勬Ы浣?token 娑堣€楄繘搴︽潯

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/graph/main_chain.py`锛歊ecallFusionNode 鎺ュ叆 RecallPackBuilder
- `host_brain/app/obs_routes.py`锛氭柊澧?`/obs/recall-pack/stats` 绔偣
- `monitor_ui/src/pages/Insights.jsx`锛氭柊澧?RecallPackSection 瀛愮粍浠?- `monitor_ui/src/api/obs.js`锛氭柊澧?`fetchRecallPackStats`

---

### TODO-50锛歎IAssertionGuard 缁熻鎺ュ叆 WebUI锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圲I 鏂█缁熻宸蹭笂绾匡級

**闂**锛歚vision/ui_assertion.py` 鐨?`UIAssertionGuard` 鍦ㄦ柇瑷€澶辫触鏃跺啓鍏ュ璁℃棩蹇楋紝浣?WebUI 鐨?`AgentLoop.jsx` 椤甸潰瀹屽叏娌℃湁灞曠ず杩欎簺鏂█缁熻銆?
**鏂板鍔熻兘**锛?- `obs_routes.py` 鏂板 `GET /obs/ui-assertion/stats`锛歎I 鏂█缁熻锛圫ENSITIVE_UI_BLOCKED/UI_PRECONDITION_FAILED/UI_POSTCONDITION_FAILED 鍒嗗竷銆佸墠缃柇瑷€澶辫触鍘熷洜鍒嗗竷锛?- `AgentLoop.jsx` 鏂板 UI 鏂█缁熻渚ц竟鏍忥紙ShieldAlert 鍥炬爣锛岀孩鑹蹭富棰橈級锛? 涓嫤鎴鏁板崱鐗?- `Insights.jsx` 鏂板 UIAssertionSection 瀛愮粍浠讹紙ShieldAlert 鍥炬爣锛岀孩鑹蹭富棰橈級锛? 涓仛鍚堟寚鏍囧崱鐗?+ 鍓嶇疆鏂█澶辫触鍘熷洜鍒嗗竷杩涘害鏉?
**鏀瑰姩鏂囦欢**锛?- `host_brain/app/obs_routes.py`锛氭柊澧?`/obs/ui-assertion/stats` 绔偣
- `monitor_ui/src/pages/AgentLoop.jsx`锛氭柊澧?UI 鏂█缁熻渚ц竟鏍?- `monitor_ui/src/pages/Insights.jsx`锛氭柊澧?UIAssertionSection 瀛愮粍浠?- `monitor_ui/src/api/obs.js`锛氭柊澧?`fetchUIAssertionStats`

---

### TODO-51锛歅ermissionGuard 鎺ュ叆 LlmGatewayNode锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堝伐鍏疯皟鐢ㄦ潈闄愭牎楠屽凡涓婄嚎锛?
**闂**锛歚LlmGatewayNode` 鍦ㄥ鐞嗗伐鍏疯皟鐢ㄦ椂锛屾病鏈夌粡杩?`PermissionGuard` 鏍￠獙锛孲CHEDULER 鏉ユ簮鍙互缁曡繃 max_tool_calls_per_cycle 鈮?3 鍜?HIGH risk_level 闄愬埗銆?
**鏂板鍔熻兘**锛?- `LlmGatewayNode.execute()` 鎺ュ叆 `PermissionGuard.check()`锛屽 `ctx.metadata["pending_tool_calls"]` 涓殑姣忎釜宸ュ叿璋冪敤杩涜鏉冮檺鏍￠獙
- 鎷掔粷缁撴灉鍐欏叆 `ctx.metadata["permission_guard"]`锛坱otal_tools/allowed_count/denied_count/denied_tools锛?- 鎷掔粷浜嬩欢杩藉姞鍒?`ctx.audit_tags`锛坄PERMISSION_DENIED:{tool_name}`銆乣PERMISSION_GUARD_DENIED:N`锛?- 闄嶇骇绛栫暐锛歅ermissionGuard 涓嶅彲鐢ㄦ椂璺宠繃锛屼笉闃绘柇涓婚摼璺?
**鏀瑰姩鏂囦欢**锛?- `host_brain/app/graph/main_chain.py`锛歀lmGatewayNode 鎺ュ叆 PermissionGuard

---

### TODO-52锛欶ailureHandler 鎺ュ叆 RecallFusionNode锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堢粺涓€澶辫触璇箟澶勭悊宸蹭笂绾匡級

**闂**锛歚RecallFusionNode` 鍦ㄥ彫鍥炰负绌猴紙EVIDENCE_EMPTY锛夋垨闄嶇骇锛圖EGRADED_FALLBACK锛夋椂锛屽彧鏄拷鍔?audit_tag锛屾病鏈夎蛋缁熶竴鐨?`FailureHandler` 澶勭悊锛屽鑷村け璐ヨ涔夊垎鏁ｃ€?
**鏂板鍔熻兘**锛?- `RecallFusionNode.execute()` 鏈熬鎺ュ叆 `FailureHandler.handle()`锛岀粺涓€澶勭悊 EVIDENCE_EMPTY 鍜?MODEL_FALLBACK 浜嬩欢
- 澶勭悊缁撴灉鍐欏叆 `ctx.metadata["failure_handler"]`锛坋vent/should_abort/degraded_mode/audit_tag锛?- 鏍囧噯鍖栫敤鎴峰搷搴斿啓鍏?`ctx.metadata["failure_user_response"]`锛堜粎鍦ㄦ棤鍏朵粬鍝嶅簲鏃讹級
- 闄嶇骇绛栫暐锛欶ailureHandler 涓嶅彲鐢ㄦ椂璺宠繃锛屼笉闃绘柇涓婚摼璺?
**鏀瑰姩鏂囦欢**锛?- `host_brain/app/graph/main_chain.py`锛歊ecallFusionNode 鎺ュ叆 FailureHandler

---

### TODO-53锛欶ailureMatrix 澶辫触浜嬩欢缁熻鎺ュ叆 WebUI锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堝け璐ヤ簨浠剁粺璁″凡涓婄嚎锛?
**闂**锛? 绉嶅け璐ヤ簨浠讹紙GOVERNOR_BLOCK/EVIDENCE_EMPTY/MODEL_FALLBACK/QA_REWRITE/EDGE_UNREACHABLE/VISION_LOW_CONFIDENCE/MODULE_TIMEOUT锛夋病鏈夌粺璁￠潰鏉匡紝杩愮淮鏃犳硶鎰熺煡澶辫触鍒嗗竷銆?
**鏂板鍔熻兘**锛?- `obs_routes.py` 鏂板 `GET /obs/failure/stats`锛氬け璐ヤ簨浠剁粺璁★紙7 绉嶄簨浠跺垎甯冦€佷腑姝㈢巼銆侀檷绾фā寮忓垎甯冦€佸皬鏃惰秼鍔匡級
- `Insights.jsx` 鏂板 FailureStatsSection 瀛愮粍浠讹紙TriangleAlert 鍥炬爣锛岀惀鐝€鑹蹭富棰橈級锛? 涓仛鍚堟寚鏍囧崱鐗?+ 澶辫触浜嬩欢鍒嗗竷杩涘害鏉?
**鏀瑰姩鏂囦欢**锛?- `host_brain/app/obs_routes.py`锛氭柊澧?`/obs/failure/stats` 绔偣
- `monitor_ui/src/pages/Insights.jsx`锛氭柊澧?FailureStatsSection 瀛愮粍浠?- `monitor_ui/src/api/obs.js`锛氭柊澧?`fetchFailureStats`

---

### TODO-54锛歅ermissionGuard 鏉冮檺鎷掔粷缁熻鎺ュ叆 WebUI锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堟潈闄愭嫆缁濈粺璁″凡涓婄嚎锛?
**闂**锛歅ermissionGuard 鐨勬嫆缁濅簨浠跺彧鍐欏叆 audit_tags锛屾病鏈夌粺璁￠潰鏉匡紝鏃犳硶鎰熺煡 SCHEDULER 婊ョ敤宸ュ叿鐨勬儏鍐点€?
**鏂板鍔熻兘**锛?- `obs_routes.py` 鏂板 `GET /obs/permission/stats`锛氭潈闄愭嫆缁濈粺璁★紙鍏佽/鎷掔粷鍒嗗竷銆丼CHEDULER 鎷掔粷娆℃暟銆乀op 鎷掔粷宸ュ叿锛?- `Insights.jsx` 鏂板 PermissionStatsSection 瀛愮粍浠讹紙Lock 鍥炬爣锛岀传鑹蹭富棰橈級锛? 涓仛鍚堟寚鏍囧崱鐗?+ Top 鎷掔粷宸ュ叿杩涘害鏉?- `Tools.jsx` 鏂板 PermissionGuardStats 瀛愮粍浠讹細鏉冮檺鎷掔粷缁熻鍗＄墖

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/obs_routes.py`锛氭柊澧?`/obs/permission/stats` 绔偣
- `monitor_ui/src/pages/Insights.jsx`锛氭柊澧?PermissionStatsSection 瀛愮粍浠?- `monitor_ui/src/pages/Tools.jsx`锛氭柊澧?PermissionGuardStats 瀛愮粍浠?- `monitor_ui/src/api/obs.js`锛氭柊澧?`fetchPermissionStats`

---

### TODO-55锛歍askScheduler 鎵ц鍘嗗彶瓒嬪娍鎺ュ叆 WebUI锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堣皟搴﹀櫒鍘嗗彶瓒嬪娍宸蹭笂绾匡級

**闂**锛歚HostTaskScheduler` 鐨?`run_count`/`error_count` 鍙湪鍐呭瓨涓淮鎶わ紝WebUI 鐨?`Automation.jsx` 鍙睍绀哄疄鏃剁姸鎬侊紝娌℃湁鍘嗗彶瓒嬪娍闈㈡澘銆?
**鏂板鍔熻兘**锛?- `obs_routes.py` 鏂板 `GET /obs/scheduler/history`锛氳皟搴﹀櫒鎵ц鍘嗗彶锛堜换鍔＄姸鎬佸垎甯冦€乺un_count/error_count 瓒嬪娍銆佸悇浠诲姟璇︽儏琛ㄦ牸锛?- `Automation.jsx` 鏂板 SchedulerHistorySection 瀛愮粍浠讹細6 涓仛鍚堟寚鏍囧崱鐗?+ 鍚勪换鍔¤鎯呰〃鏍硷紙鐘舵€?鎵ц娆℃暟/澶辫触娆℃暟/閿欒鐜?鏈€杩戦敊璇級

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/obs_routes.py`锛氭柊澧?`/obs/scheduler/history` 绔偣
- `monitor_ui/src/pages/Automation.jsx`锛氭柊澧?SchedulerHistorySection 瀛愮粍浠?- `monitor_ui/src/api/obs.js`锛氭柊澧?`fetchSchedulerHistory`

---

### TODO-56锛欰ctionPlan 鎵ц鎴愬姛鐜?鎸囦护绫诲瀷鍒嗗竷鎺ュ叆 WebUI锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圓ctionPlan 缁熻宸蹭笂绾匡級

**闂**锛歚vision/action_planner.py` 鐢熸垚鐨?ActionPlan 鎵ц缁撴灉锛堟垚鍔熺巼銆佺疆淇″害銆佹寚浠ょ被鍨嬪垎甯冿級娌℃湁缁熻闈㈡澘锛屾棤娉曟劅鐭?ClawdBot 鑷姩鍖栨墽琛岃川閲忋€?
**鏂板鍔熻兘**锛?- `obs_routes.py` 鏂板 `GET /obs/action-plan/stats`锛欰ctionPlan 缁熻锛堟垚鍔熺巼銆佸钩鍧囩疆淇″害銆佸钩鍧囨寚浠ゆ暟銆佹寚浠ょ被鍨嬪垎甯冦€侀檷绾у師鍥犲垎甯冿級
- `Tools.jsx` 鏂板 ActionPlanStats 瀛愮粍浠讹細4 涓仛鍚堟寚鏍囧崱鐗?+ 鎸囦护绫诲瀷鍒嗗竷杩涘害鏉?
**鏀瑰姩鏂囦欢**锛?- `host_brain/app/obs_routes.py`锛氭柊澧?`/obs/action-plan/stats` 绔偣
- `monitor_ui/src/pages/Tools.jsx`锛氭柊澧?ActionPlanStats 瀛愮粍浠?- `monitor_ui/src/api/obs.js`锛氭柊澧?`fetchActionPlanStats`

---

### TODO-57锛歁emoryGuard 瀹硶鏍￠獙澧炲己鎺ュ叆 EpisodicStoreNode锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛坉ream 鍐呭杩囨护 + 钂搁璧勬牸棰勫垽宸蹭笂绾匡級

**闂**锛歚EpisodicStoreNode` 閫氳繃 `persist_chat_episodic_memory` 鎺ュ叆浜?MemoryGuard锛屼絾缂哄皯 dream 鍐呭杩囨护鍜岃捀棣忚祫鏍奸鍒や袱涓叧閿牎楠屾楠ゃ€?
**鏂板鍔熻兘**锛?- 鍦?`persist_chat_episodic_memory` 璋冪敤鍓嶏紝鎵ц MemoryGuard 瀹硶鏍￠獙澧炲己锛?  - dream 鍐呭锛坄source_tag` 鍚?DREAM 鎴?`is_dream_content=True`锛夊己鍒?NARRATIVE_ONLY锛岄樆姝㈠啓鍏?EpisodicStore
  - `evidence_level=NARRATIVE_ONLY` 鐨勫唴瀹圭洿鎺ラ樆姝㈠啓鍏?  - 钂搁璧勬牸棰勫垽锛堥娆?鈮?3 鎴?MILESTONE 绾у埆锛夛紝鍐欏叆 `ctx.metadata["distill_eligible"]`
- 闃绘缁撴灉鍐欏叆 `ctx.metadata["memory_guard_write"]`锛坢ode/reason/evidence_level/distill_eligible锛?- 鎷掔粷浜嬩欢杩藉姞 `MEMORY_GUARD_DREAM_BLOCKED`/`MEMORY_GUARD_NARRATIVE_BLOCKED`/`MEMORY_GUARD_DISTILL_ELIGIBLE` 鍒?`ctx.audit_tags`

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/graph/main_chain.py`锛欵pisodicStoreNode 鎺ュ叆 MemoryGuard 瀹硶鏍￠獙澧炲己

---

### TODO-58锛歋tateArbiter SESSION_OVERRIDE 鍐欏叆鎺ュ叆 LlmGatewayNode锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圫ESSION_OVERRIDE 鍐欏叆宸蹭笂绾匡級

**闂**锛歚LlmGatewayNode` 杈撳嚭鐨?`intent_draft` 涓寘鍚柊浜嬪疄锛坄user_fact_updates`/`session_context`锛夛紝浣嗚繖浜涙柊浜嬪疄娌℃湁鍐欏叆 `StateArbiter.set_session_override()`锛屽鑷存柊浜嬪疄鐩存帴缁曡繃浠茶灞傘€?
**鏂板鍔熻兘**锛?- `LlmGatewayNode.execute()` 鏈熬锛圗motionTimeline 涔嬪悗锛夋帴鍏?`StateArbiter.set_session_override()`
- 浠?`ctx.metadata["llm_gateway"]["intent_draft"]` 涓彁鍙?`user_fact_updates` 鍜?`session_context`
- 灏嗘柊浜嬪疄鍐欏叆 SESSION_OVERRIDE锛堜笉鐩存帴鍐?Canon锛岀瓑寰呰捀棣忓懆鏈熷鐞嗭級
- 鍐欏叆缁撴灉璁板綍鍒?`ctx.metadata["session_override"]`锛坥verride_count/fact_keys锛?- 鍐欏叆浜嬩欢杩藉姞 `SESSION_OVERRIDE_WRITTEN:N` 鍒?`ctx.audit_tags`

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/graph/main_chain.py`锛歀lmGatewayNode 鎺ュ叆 StateArbiter SESSION_OVERRIDE 鍐欏叆

---

### TODO-59锛歁emoryGuard 瀹硶鏍￠獙缁熻鎺ュ叆 WebUI锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圡emoryGuard 缁熻宸蹭笂绾匡級

**闂**锛歁emoryGuard 鐨勫娉曟牎楠岀粨鏋滐紙杩囨护鐜?鎷掔粷鍘熷洜/NARRATIVE_ONLY 姣斾緥/钂搁璧勬牸锛夋病鏈夌粺璁￠潰鏉匡紝杩愮淮鏃犳硶鎰熺煡璁板繂鍐欏叆璐ㄩ噺銆?
**鏂板鍔熻兘**锛?- `obs_routes.py` 鏂板 `GET /obs/memory-guard/stats`锛歁emoryGuard 缁熻锛堣繃婊ょ巼/鎷掔粷鍘熷洜鍒嗗竷/NARRATIVE_ONLY 姣斾緥/钂搁璧勬牸娆℃暟锛?- `Insights.jsx` 鏂板 MemoryGuardStatsSection 瀛愮粍浠讹紙BookLock 鍥炬爣锛岀繝缁胯壊涓婚锛夛細4+3 涓仛鍚堟寚鏍囧崱鐗?+ 鎷掔粷鍘熷洜鍒嗗竷杩涘害鏉?- `Metrics.jsx` 鏂板 MemoryGuardMiniCard 杩蜂綘鍗＄墖锛? 涓仛鍚堟寚鏍?+ 鎷︽埅鐜囪繘搴︽潯

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/obs_routes.py`锛氭柊澧?`/obs/memory-guard/stats` 绔偣
- `monitor_ui/src/pages/Insights.jsx`锛氭柊澧?MemoryGuardStatsSection 瀛愮粍浠?- `monitor_ui/src/pages/Metrics.jsx`锛氭柊澧?MemoryGuardMiniCard 杩蜂綘鍗＄墖
- `monitor_ui/src/api/obs.js`锛氭柊澧?`fetchMemoryGuardStats`

---

### TODO-60锛歋tateArbiter 浜嬪疄浠茶缁熻鎺ュ叆 WebUI锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圫tateArbiter 缁熻宸蹭笂绾匡級

**闂**锛歋tateArbiter 鐨勪簨瀹炰徊瑁佺粨鏋滐紙鍐茬獊鐜?闄嶇骇鐜?鍚勬簮鍛戒腑鍒嗗竷/SESSION_OVERRIDE 娆℃暟锛夋病鏈夌粺璁￠潰鏉匡紝鏃犳硶鎰熺煡浜嬪疄鍐茬獊鎯呭喌銆?
**鏂板鍔熻兘**锛?- `obs_routes.py` 鏂板 `GET /obs/state-arbiter/stats`锛歋tateArbiter 缁熻锛堝啿绐佺巼/闄嶇骇鐜?鍚勬簮鍛戒腑鍒嗗竷/SESSION_OVERRIDE 娆℃暟/Top 鍐茬獊閿級
- `Insights.jsx` 鏂板 StateArbiterStatsSection 瀛愮粍浠讹紙GitMerge 鍥炬爣锛岄潚鑹蹭富棰橈級锛? 涓仛鍚堟寚鏍囧崱鐗?+ 鐘舵€佹簮鍛戒腑鍒嗗竷杩涘害鏉?- `Metrics.jsx` 鏂板 StateArbiterMiniCard 杩蜂綘鍗＄墖锛? 涓仛鍚堟寚鏍?
**鏀瑰姩鏂囦欢**锛?- `host_brain/app/obs_routes.py`锛氭柊澧?`/obs/state-arbiter/stats` 绔偣
- `monitor_ui/src/pages/Insights.jsx`锛氭柊澧?StateArbiterStatsSection 瀛愮粍浠?- `monitor_ui/src/pages/Metrics.jsx`锛氭柊澧?StateArbiterMiniCard 杩蜂綘鍗＄墖
- `monitor_ui/src/api/obs.js`锛氭柊澧?`fetchStateArbiterStats`

---

### TODO-61锛欳ontextCompressor 鍘嬬缉鏁堢巼缁熻鎺ュ叆 WebUI锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圕ontextCompressor 缁熻宸蹭笂绾匡級

**闂**锛欳ontextCompressor 鐨勫帇缂╂晥鐜囷紙鍘嬬缉鐜?token 鑺傜渷/绛栫暐鍒嗗竷/鎽樿鐢熸垚娆℃暟锛夋病鏈夌粺璁￠潰鏉匡紝鏃犳硶鎰熺煡涓婁笅鏂囧帇缂╄川閲忋€?
**鏂板鍔熻兘**锛?- `obs_routes.py` 鏂板 `GET /obs/context-compressor/stats`锛欳ontextCompressor 缁熻锛堝帇缂╃巼/token 鑺傜渷/绛栫暐鍒嗗竷/鎽樿鐢熸垚娆℃暟锛?- `Insights.jsx` 鏂板 ContextCompressorStatsSection 瀛愮粍浠讹紙Minimize2 鍥炬爣锛岄潚缁胯壊涓婚锛夛細4+3 涓仛鍚堟寚鏍囧崱鐗?+ 鍘嬬缉绛栫暐鍒嗗竷杩涘害鏉?- `Metrics.jsx` 鏂板 ContextCompressorMiniCard 杩蜂綘鍗＄墖锛? 涓仛鍚堟寚鏍?+ 骞冲潎鍘嬬缉鐜囪繘搴︽潯

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/obs_routes.py`锛氭柊澧?`/obs/context-compressor/stats` 绔偣
- `monitor_ui/src/pages/Insights.jsx`锛氭柊澧?ContextCompressorStatsSection 瀛愮粍浠?- `monitor_ui/src/pages/Metrics.jsx`锛氭柊澧?ContextCompressorMiniCard 杩蜂綘鍗＄墖
- `monitor_ui/src/api/obs.js`锛氭柊澧?`fetchContextCompressorStats`

---

### TODO-62锛歁etrics.jsx 鏂板 MemoryGuard/StateArbiter/ContextCompressor 杩蜂綘鍗＄墖锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堜笁涓糠浣犲崱鐗囧凡涓婄嚎锛?
**闂**锛歚Metrics.jsx` 鍙湁 LLM 鏃跺簭鎸囨爣鍜岄€熺巼闄愬埗鍗＄墖锛岀己灏戣蹇嗗瓙绯荤粺鐨勫叧閿寚鏍囨瑙堛€?
**鏂板鍔熻兘**锛?- `Metrics.jsx` 鏂板涓変釜杩蜂綘鍗＄墖锛歁emoryGuardMiniCard锛堢繝缁胯壊锛夈€丼tateArbiterMiniCard锛堥潚鑹诧級銆丆ontextCompressorMiniCard锛堥潚缁胯壊锛?- 姣忎釜鍗＄墖灞曠ず 4 涓仛鍚堟寚鏍?+ 鍏抽敭姣旂巼杩涘害鏉?- 鏁版嵁鏉ユ簮锛歚/obs/memory-guard/stats`銆乣/obs/state-arbiter/stats`銆乣/obs/context-compressor/stats`锛堢獥鍙?1h锛?
**鏀瑰姩鏂囦欢**锛?- `monitor_ui/src/pages/Metrics.jsx`锛氭柊澧炰笁涓糠浣犲崱鐗囩粍浠?
---

### TODO-63锛欰gentLoop.jsx 琛ュ叏 UIAssertion 鏁版嵁鍔犺浇锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圲IAssertion 鏁版嵁鍔犺浇宸蹭笂绾匡級

**闂**锛歚AgentLoop.jsx` 涓?`uiAssertionData` 鐘舵€佸凡澹版槑锛屼絾浠庢湭璋冪敤 `fetchUIAssertionStats` 鍔犺浇鏁版嵁锛屽鑷?UIAssertion 缁熻渚ц竟鏍忔案杩滀笉鏄剧ず銆?
**鏂板鍔熻兘**锛?- 鍦?`AgentLoop.jsx` 涓坊鍔?`useEffect`锛屽湪缁勪欢鎸傝浇鏃惰皟鐢?`fetchUIAssertionStats({ window_minutes: 60 })` 鍔犺浇鏁版嵁
- 姣?5 鍒嗛挓鑷姩鍒锋柊锛坄setInterval`锛夛紝淇濇寔鏁版嵁瀹炴椂鎬?- 缁勪欢鍗歌浇鏃舵竻鐞嗗畾鏃跺櫒锛坄clearInterval`锛?
**鏀瑰姩鏂囦欢**锛?- `monitor_ui/src/pages/AgentLoop.jsx`锛氳ˉ鍏?UIAssertion 鏁版嵁鍔犺浇 useEffect

---

### TODO-64锛歁emoryGuard 鎻愮ず璇嶆敞鍏ユ娴嬫帴鍏?PromptBuilder锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堟彁绀鸿瘝娉ㄥ叆妫€娴嬪凡涓婄嚎锛?
**闂**锛歚prompt_builder.py` 鐨?`render_node_generation_prompt` 鍜?`render_host_generation_prompt` 鍦ㄦ瀯寤?prompt 鍓嶆病鏈夋娴嬬敤鎴锋秷鎭腑鐨勬敞鍏ユ敾鍑伙紝瀛樺湪 LLM 琚姭鎸佺殑瀹夊叏椋庨櫓銆?
**鏂板鍔熻兘**锛?- `render_node_generation_prompt`锛氬湪 `user_message` 鎴柇鍚庢帴鍏?`MemoryGuard.check_prompt_injection()`
  - 妫€娴嬪埌 HIGH/CRITICAL 椋庨櫓娉ㄥ叆鏃讹紝鍦?`user_message` 鏈熬杩藉姞 `[SECURITY_ALERT: ...]` 璀﹀憡鏍囪
  - 娉ㄥ叆妫€娴嬬粨鏋滃啓鍏?`_injection_meta`锛坢ode/detected/risk_level/matched_patterns锛?  - 闄嶇骇鏃朵笉闃绘柇 prompt 鏋勫缓锛坱ry/except 淇濇姢锛?- `render_host_generation_prompt`锛氬悓鏍锋帴鍏ユ敞鍏ユ娴嬶紙绠€鍖栫増锛?
**鏀瑰姩鏂囦欢**锛?- `host_brain/app/prompt_builder.py`锛氫袱涓?render 鍑芥暟鎺ュ叆 MemoryGuard 鎻愮ず璇嶆敞鍏ユ娴?
---

### TODO-65锛歅romptBuilder 鏋勫缓缁熻鎺ュ叆 WebUI锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圥romptBuilder 缁熻宸蹭笂绾匡級

**闂**锛歅romptBuilder 鐨勬瀯寤虹粺璁★紙娉ㄥ叆妫€娴嬬巼/slot 濉厖鍒嗗竷/楂橀闄╂秷鎭瘮渚嬶級娌℃湁缁熻闈㈡澘锛屾棤娉曟劅鐭?prompt 瀹夊叏璐ㄩ噺銆?
**鏂板鍔熻兘**锛?- `obs_routes.py` 鏂板 `GET /obs/prompt-builder/stats`锛歅romptBuilder 缁熻锛堟敞鍏ユ娴嬬巼/slot 濉厖鍒嗗竷/楂橀闄╂秷鎭瘮渚?闄嶇骇娆℃暟锛?- `Insights.jsx` 鏂板 PromptBuilderStatsSection 瀛愮粍浠讹紙FileCode 鍥炬爣锛岀传鑹蹭富棰橈級锛?+3 涓仛鍚堟寚鏍囧崱鐗?+ Slot 濉厖鍒嗗竷杩涘害鏉?
**鏀瑰姩鏂囦欢**锛?- `host_brain/app/obs_routes.py`锛氭柊澧?`/obs/prompt-builder/stats` 绔偣
- `monitor_ui/src/pages/Insights.jsx`锛氭柊澧?PromptBuilderStatsSection 瀛愮粍浠?- `monitor_ui/src/api/obs.js`锛氭柊澧?`fetchPromptBuilderStats`

---

### TODO-66锛欵pisodicStore 鍐欏叆缁熻鎺ュ叆 WebUI锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圗pisodicStore 缁熻宸蹭笂绾匡級

**闂**锛欵pisodicStore 鐨勫啓鍏ョ粺璁★紙鍐欏叆鐜?MemoryGuard 鎷︽埅鐜?钂搁璧勬牸鐜?dream 鎷︽埅娆℃暟锛夋病鏈夌粺璁￠潰鏉匡紝鏃犳硶鎰熺煡璁板繂鍐欏叆璐ㄩ噺銆?
**鏂板鍔熻兘**锛?- `obs_routes.py` 鏂板 `GET /obs/episodic-store/stats`锛欵pisodicStore 缁熻锛堝啓鍏ョ巼/MemoryGuard 鎷︽埅鐜?钂搁璧勬牸鐜?dream 鎷︽埅娆℃暟锛?- `Insights.jsx` 鏂板 EpisodicStoreStatsSection 瀛愮粍浠讹紙Archive 鍥炬爣锛岀惀鐝€鑹蹭富棰橈級锛?+4 涓仛鍚堟寚鏍囧崱鐗?
**鏀瑰姩鏂囦欢**锛?- `host_brain/app/obs_routes.py`锛氭柊澧?`/obs/episodic-store/stats` 绔偣
- `monitor_ui/src/pages/Insights.jsx`锛氭柊澧?EpisodicStoreStatsSection 瀛愮粍浠?- `monitor_ui/src/api/obs.js`锛氭柊澧?`fetchEpisodicStoreStats`

---

### TODO-67/68锛欼nsights.jsx 鏂板 PromptBuilder 鍜?EpisodicStore 缁熻鍖哄潡锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堜袱涓柊缁熻鍖哄潡宸蹭笂绾匡級

**闂**锛歚Insights.jsx` 缂哄皯 PromptBuilder 鍜?EpisodicStore 鐨勭粺璁″尯鍧楋紝杩愮淮鏃犳硶鍦ㄥ悓涓€椤甸潰鎰熺煡 prompt 瀹夊叏鍜岃蹇嗗啓鍏ヨ川閲忋€?
**鏂板鍔熻兘**锛?- `Insights.jsx` 鍦?ContextCompressorStatsSection 涔嬪悗杩藉姞 PromptBuilderStatsSection 鍜?EpisodicStoreStatsSection 涓や釜鏂板尯鍧?- 鏁版嵁鏉ユ簮锛歚/obs/prompt-builder/stats`銆乣/obs/episodic-store/stats`

**鏀瑰姩鏂囦欢**锛?- `monitor_ui/src/pages/Insights.jsx`锛氳拷鍔犱袱涓柊缁熻鍖哄潡鐨勬覆鏌撹皟鐢?
---

### TODO-69锛歋essions.jsx 琛ュ叏浼氳瘽璇︽儏璺宠浆鍐欏叆 useAppStore锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堜細璇濊鎯呰烦杞凡琛ュ叏锛?
**闂**锛歚Sessions.jsx` 涓偣鍑讳細璇濊璺宠浆鍒?`/trace/{request_id}` 鏃讹紝娌℃湁灏?`request_id` 鍐欏叆 `useAppStore`锛屽鑷?Trace 椤甸潰鏃犳硶鑷姩鍔犺浇瀵瑰簲閾捐矾銆?
**鏂板鍔熻兘**锛?- 寮曞叆 `useAppStore`锛屽湪 `onClick` 涓厛璋冪敤 `setSelectedRequestId(s.request_id)` 鍐?`navigate`
- 浼氳瘽琛屾湯灏捐拷鍔?`ExternalLink` 鍥炬爣锛岃瑙夋彁绀哄彲璺宠浆

**鏀瑰姩鏂囦欢**锛?- `monitor_ui/src/pages/Sessions.jsx`锛氳ˉ鍏?useAppStore 鍐欏叆 + ExternalLink 鍥炬爣

---

### TODO-70锛歅romptBuilder 娉ㄥ叆妫€娴嬬粨鏋滃啓鍏?Ledger audit_log锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堟敞鍏ユ娴嬬粨鏋滃凡鍐欏叆 Ledger锛?
**闂**锛歚prompt_builder.py` 鐨勬敞鍏ユ娴嬬粨鏋滐紙`_injection_meta`锛夊彧瀛樺湪浜庡嚱鏁板眬閮ㄥ彉閲忎腑锛屾棤娉曡 Ledger 鏌ヨ锛屽鑷?`/obs/prompt-builder/stats` 绔偣鏃犳硶缁熻娉ㄥ叆浜嬩欢銆?
**鏂板鍔熻兘**锛?- `prompt_builder.py` 鏂板绾跨▼灞€閮ㄥ彉閲?`_injection_meta_local`锛坄threading.local()`锛?- 鏂板 `get_last_injection_meta()` 鍜?`_set_injection_meta()` 涓や釜鍏叡鍑芥暟
- `render_node_generation_prompt` 鍜?`render_host_generation_prompt` 鍦ㄦ敞鍏ユ娴嬪悗璋冪敤 `_set_injection_meta()`
- `provider_factory.py` 鍦?`_provider` 鍑芥暟涓鍙栨敞鍏ユ娴嬬粨鏋滐紝鍐欏叆 `runtime_payload["_injection_detected"]`
- `chat_stream_handler.py` 鍦?`_build_stream_chat_prompt` 璋冪敤鍚庤鍙栨敞鍏ユ娴嬬粨鏋滐紝鍐欏叆 Ledger `audit_log`锛坄audit_tag=INJECTION_DETECTED:{RISK_LEVEL}`锛?
**鏀瑰姩鏂囦欢**锛?- `host_brain/app/prompt_builder.py`锛氭柊澧炵嚎绋嬪眬閮ㄥ彉閲?+ get/set 鍑芥暟
- `host_brain/app/provider_factory.py`锛氳鍙栨敞鍏ユ娴嬬粨鏋滃啓鍏?runtime_payload
- `host_brain/app/chat_stream_handler.py`锛氳鍙栨敞鍏ユ娴嬬粨鏋滃啓鍏?Ledger audit_log

---

### TODO-71锛氫細璇濆垪琛?CSV 瀵煎嚭锛堝凡瀹屾垚锛岀敱 TODO-75/78 瀹炵幇锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堝悗绔鐐?+ 鍓嶇鎸夐挳鍧囧凡涓婄嚎锛岃 TODO-75 鍜?TODO-78锛?
---

### TODO-72锛歍race.jsx 鏂板鑺傜偣鑰楁椂鎺掕姒滐紙宸插畬鎴愶紝2026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圱op 5 鏈€鎱㈣妭鐐规帓琛屾宸蹭笂绾匡級

**闂**锛歚Trace.jsx` 鐨勭敇鐗瑰浘瑙嗗浘鍙睍绀鸿妭鐐规墽琛屾椂搴忥紝娌℃湁鐩磋鐨勮€楁椂鎺掕姒滐紝闅句互蹇€熷畾浣嶆€ц兘鐡堕銆?
**鏂板鍔熻兘**锛?- 鏂板 `SlowestNodesPanel` 瀛愮粍浠讹細灞曠ず Top 5 鏈€鎱㈣妭鐐癸紙鎸?`latency_ms` 闄嶅簭锛?- 姣忎釜鑺傜偣鏄剧ず锛氭帓鍚?鑺傜偣鍚?杩涘害鏉★紙鎸夎妭鐐圭被鍨嬬潃鑹诧級/鑰楁椂锛坢s锛?- 鍦ㄤ富浣撴覆鏌撳尯鍧楁湯灏捐拷鍔狅紝鏈?`traceData` 鏃舵樉绀?
**鏀瑰姩鏂囦欢**锛?- `monitor_ui/src/pages/Trace.jsx`锛氭柊澧?SlowestNodesPanel 瀛愮粍浠?+ 娓叉煋璋冪敤

---

### TODO-73锛歄verview.jsx 鏂板 PromptBuilder 娉ㄥ叆妫€娴嬫憳瑕佸崱鐗囷紙宸插畬鎴愶紝2026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圥rompt 瀹夊叏鎽樿鍗＄墖宸蹭笂绾匡級

**闂**锛歚Overview.jsx` 鎬昏椤甸潰缂哄皯 Prompt 瀹夊叏鐘舵€佹劅鐭ワ紝杩愮淮鏃犳硶鍦ㄦ€昏椤靛揩閫熷彂鐜版敞鍏ユ敾鍑汇€?
**鏂板鍔熻兘**锛?- 寮曞叆 `fetchPromptBuilderStats`锛屽湪 `load` 涓苟琛岃姹?PromptBuilder 缁熻鏁版嵁
- 鏂板 Prompt 瀹夊叏鎽樿鍗＄墖锛圫hieldAlert 鍥炬爣锛岀孩鑹蹭富棰橈級锛? 涓仛鍚堟寚鏍囷紙鎬绘瀯寤?娉ㄥ叆妫€娴?楂橀闄?妫€娴嬬巼锛?- 妫€娴嬪埌娉ㄥ叆鏃舵樉绀烘鑹茶鍛婃爣绛?
**鏀瑰姩鏂囦欢**锛?- `monitor_ui/src/pages/Overview.jsx`锛氬紩鍏?fetchPromptBuilderStats + 鏂板 Prompt 瀹夊叏鎽樿鍗＄墖

---

### TODO-74锛歁etrics.jsx 鏂板 PromptBuilder 鍜?EpisodicStore 杩蜂綘鍗＄墖锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堜袱涓柊杩蜂綘鍗＄墖宸蹭笂绾匡級

**闂**锛歚Metrics.jsx` 缂哄皯 PromptBuilder 鍜?EpisodicStore 鐨勮糠浣犲崱鐗囷紝鏃犳硶鍦?Metrics 椤甸潰鎰熺煡 prompt 瀹夊叏鍜岃蹇嗗啓鍏ヨ川閲忋€?
**鏂板鍔熻兘**锛?- 鏂板 `PromptBuilderMiniCard`锛團ileCode 鍥炬爣锛岀传鑹蹭富棰橈級锛? 涓仛鍚堟寚鏍?+ 娉ㄥ叆妫€娴嬬巼杩涘害鏉?- 鏂板 `EpisodicStoreMiniCard`锛圓rchive 鍥炬爣锛岀惀鐝€鑹蹭富棰橈級锛? 涓仛鍚堟寚鏍?+ 鍐欏叆鐜囪繘搴︽潯
- 鍦?ContextCompressorMiniCard 涔嬪悗娓叉煋

**鏀瑰姩鏂囦欢**锛?- `monitor_ui/src/pages/Metrics.jsx`锛氭柊澧炰袱涓糠浣犲崱鐗囩粍浠?+ 娓叉煋璋冪敤

---

### TODO-75锛欸ET /obs/sessions/export 鈥?浼氳瘽鍒楄〃 CSV 瀵煎嚭绔偣锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圕SV 瀵煎嚭绔偣宸蹭笂绾匡級

**鏂板鍔熻兘**锛?- `obs_routes.py` 鏂板 `GET /obs/sessions/export` 绔偣锛堟敮鎸?status/keyword/window_minutes/max_rows 杩囨护锛?- 杩斿洖 `text/csv; charset=utf-8` 鍝嶅簲锛屾枃浠跺悕涓?`sessions_{timestamp}.csv`
- 瀛楁锛歳equest_id/session_id/ts_ms/datetime/status/decision/reason_code/total_latency_ms

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/obs_routes.py`锛氭柊澧?/obs/sessions/export 绔偣

---

### TODO-76锛欸ET /canon/proposals/stats 鈥?Canon 鎻愭缁熻绔偣锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圕anon 鎻愭缁熻绔偣宸蹭笂绾匡級

**鏂板鍔熻兘**锛?- `canon_routes.py` 鏂板 `GET /canon/proposals/stats` 绔偣
- 杩斿洖锛氬叏閲?pending/applied/rejected 鍒嗗竷 + 閫氳繃鐜?+ 鏈€杩?10 鏉℃彁妗堝垪琛紙鎸夋椂闂寸獥鍙ｈ繃婊わ級

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/canon_routes.py`锛氭柊澧?/canon/proposals/stats 绔偣

---

### TODO-77锛欼nsights.jsx 鏂板 Canon 鎻愭缁熻鍖哄潡锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圕anon 鎻愭缁熻鍖哄潡宸蹭笂绾匡級

**鏂板鍔熻兘**锛?- 鏂板 `CanonProposalStatsSection` 瀛愮粍浠讹紙ScrollText 鍥炬爣锛岄潧钃濊壊涓婚锛?- 4 涓仛鍚堟寚鏍囷紙鎬绘彁妗?寰呭鏍?宸查€氳繃/宸叉嫆缁濓級+ 閫氳繃鐜囪繘搴︽潯 + 鐘舵€佸垎甯?+ 鏈€杩戞彁妗堝垪琛?
**鏀瑰姩鏂囦欢**锛?- `monitor_ui/src/pages/Insights.jsx`锛氬紩鍏?fetchCanonProposalStats + 鏂板 CanonProposalStatsSection

---

### TODO-78锛歋essions.jsx 鏂板 CSV 瀵煎嚭鎸夐挳锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圕SV 瀵煎嚭鎸夐挳宸蹭笂绾匡級

**鏂板鍔熻兘**锛?- 寮曞叆 `exportSessionsCsv`锛屽湪椤靛ご娣诲姞"瀵煎嚭 CSV"鎸夐挳锛圖ownload 鍥炬爣锛?- 瀵煎嚭鏃舵寜褰撳墠杩囨护鏉′欢锛堟椂闂磋寖鍥?鐘舵€?鍏抽敭璇嶏級瀵煎嚭锛屽鍑轰腑鏄剧ず loading 鐘舵€?
**鏀瑰姩鏂囦欢**锛?- `monitor_ui/src/pages/Sessions.jsx`锛氬紩鍏?exportSessionsCsv + 鏂板瀵煎嚭鎸夐挳

---

### TODO-79锛歄verview.jsx 鏂板 Canon 鎻愭寰呭鏍歌鍛婂窘绔狅紙宸插畬鎴愶紝2026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圕anon 鎻愭鐘舵€佸崱鐗囧凡涓婄嚎锛?
**鏂板鍔熻兘**锛?- 寮曞叆 `fetchCanonProposalStats`锛屽湪 load 涓苟琛岃姹?Canon 鎻愭缁熻鏁版嵁
- 鏂板 Canon 鎻愭鐘舵€佸崱鐗囷紙ScrollText 鍥炬爣锛岄潧钃濊壊涓婚锛夛細4 涓仛鍚堟寚鏍?- pending > 0 鏃舵樉绀烘鑹查棯鐑佽鍛婃爣绛撅紙`animate-pulse`锛?
**鏀瑰姩鏂囦欢**锛?- `monitor_ui/src/pages/Overview.jsx`锛氬紩鍏?fetchCanonProposalStats + 鏂板 Canon 鎻愭鐘舵€佸崱鐗?
---

### TODO-80锛歁etrics.jsx 鏂板 Canon 鎻愭杩蜂綘鍗＄墖锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圕anon 鎻愭杩蜂綘鍗＄墖宸蹭笂绾匡級

**鏂板鍔熻兘**锛?- 鏂板 `CanonProposalMiniCard`锛圫crollText 鍥炬爣锛岄潧钃濊壊涓婚锛夛細4 涓仛鍚堟寚鏍?+ 閫氳繃鐜囪繘搴︽潯
- pending > 0 鏃跺湪鍗＄墖鍙充笂瑙掓樉绀烘鑹?N 寰呭鏍?寰界珷

**鏀瑰姩鏂囦欢**锛?- `monitor_ui/src/pages/Metrics.jsx`锛氭柊澧?CanonProposalMiniCard 缁勪欢 + 娓叉煋璋冪敤

---

### TODO-81锛欳anon.jsx 鏂板鎻愭鍥炴粴鎸夐挳锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堝洖婊氭寜閽凡涓婄嚎锛?
**鏂板鍔熻兘**锛?- `obs.js` 杩藉姞 `rollbackCanonProposal(proposalId, body)` 鍑芥暟锛圥OST /canon/proposals/{id}/rollback锛?- `Canon.jsx` 鍦?APPLIED 鐘舵€佺殑鎻愭琛屼腑鏂板"鍥炴粴"鎸夐挳锛圧otateCcw 鍥炬爣锛屾鑹蹭富棰橈級
- 鐐瑰嚮鍚庡脊鍑虹‘璁ゅ璇濇锛岃緭鍏ュ洖婊氬師鍥犲悗璋冪敤 rollbackCanonProposal

**鏀瑰姩鏂囦欢**锛?- `monitor_ui/src/api/obs.js`锛氭柊澧?rollbackCanonProposal 鍑芥暟
- `monitor_ui/src/pages/Canon.jsx`锛氭柊澧炲洖婊氭寜閽?+ handleRollback 澶勭悊鍑芥暟

---

### TODO-82锛欸ET /obs/chain/stats 鈥?涓婚摼璺妭鐐圭粺璁＄鐐癸紙宸插畬鎴愶紝2026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堜富閾捐矾鑺傜偣缁熻绔偣宸蹭笂绾匡級

**鏂板鍔熻兘**锛?- `obs_routes.py` 鏂板 `GET /obs/chain/stats` 绔偣
- 浠?AUDIT_SNAPSHOT 璁板綍涓彁鍙?audit_tags 鍜?node_timings锛岃仛鍚堝悇鑺傜偣璋冪敤娆℃暟/闄嶇骇鐜?骞冲潎鑰楁椂/P95 鑰楁椂/瓒呮椂娆℃暟
- 鍏ㄥ眬缁熻锛氭€昏姹傛暟/Governor 鎷︽埅鐜?璁板繂鍐欏叆娆℃暟/QA 閲嶅啓娆℃暟/Canon 鎻愭娆℃暟

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/obs_routes.py`锛氭柊澧?/obs/chain/stats 绔偣

---

### TODO-83锛歍race.jsx 鏂板鑺傜偣鑰楁椂鍒嗗竷鐩存柟鍥撅紙宸插畬鎴愶紝2026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堣妭鐐硅€楁椂鍒嗗竷鐩存柟鍥惧凡涓婄嚎锛?
**鏂板鍔熻兘**锛?- 鏂板 `ChainStatsPanel` 缁勪欢锛圓ctivity 鍥炬爣锛岃摑鑹蹭富棰橈級
- 鎺ュ叆 `fetchChainStats`锛屽睍绀哄悇鑺傜偣骞冲潎鑰楁椂鍒嗗竷鐩存柟鍥撅紙鎸夋瘮渚嬫覆鏌撳僵鑹茶繘搴︽潯锛?- 鏄剧ず鍏ㄥ眬鎽樿锛氭€昏姹傛暟/Governor 鎷︽埅鐜?璁板繂鍐欏叆娆℃暟/Canon 鎻愭娆℃暟
- 濮嬬粓鏄剧ず锛堢嫭绔嬩簬 traceData锛屼笉闇€瑕佸厛鏌ヨ trace锛?
**鏀瑰姩鏂囦欢**锛?- `monitor_ui/src/pages/Trace.jsx`锛氬紩鍏?fetchChainStats + 鏂板 ChainStatsPanel 缁勪欢

---

### TODO-84锛欼nsights.jsx 鏂板涓婚摼璺妭鐐圭粺璁″尯鍧楋紙宸插畬鎴愶紝2026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堜富閾捐矾鑺傜偣缁熻鍖哄潡宸蹭笂绾匡級

**鏂板鍔熻兘**锛?- 鏂板 `ChainStatsSection` 瀛愮粍浠讹紙Activity 鍥炬爣锛岃摑鑹蹭富棰橈級
- 4 涓仛鍚堟寚鏍?+ 鍚勮妭鐐硅€楁椂鍒嗗竷鐩存柟鍥撅紙avg/p95 鑰楁椂 + 闄嶇骇鐜?+ 瓒呮椂娆℃暟锛?- 鏃犳暟鎹椂鏄剧ず鍙嬪ソ鎻愮ず

**鏀瑰姩鏂囦欢**锛?- `monitor_ui/src/pages/Insights.jsx`锛氬紩鍏?fetchChainStats + 鏂板 ChainStatsSection

---

### TODO-85锛欳anon.jsx 鏂板鏂囨。鎼滅储杩囨护锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堟枃妗ｆ悳绱㈣繃婊ゅ凡涓婄嚎锛?
**鏂板鍔熻兘**锛?- 鍦?SSOT 鏂囨。鍒楄〃 Tab 椤堕儴鏂板鎼滅储妗嗭紙Search 鍥炬爣锛?- 鏀寔鎸?title/doc_id/tags 鍏抽敭璇嶅疄鏃惰繃婊ゆ枃妗ｅ垪琛紙鍓嶇杩囨护锛屾棤闇€棰濆 API 璇锋眰锛?
**鏀瑰姩鏂囦欢**锛?- `monitor_ui/src/pages/Canon.jsx`锛氭柊澧?docKeyword 鐘舵€?+ 鎼滅储妗?+ 杩囨护閫昏緫

---

### TODO-86锛欰gentLoop.jsx 鏂板鍘嗗彶鎵ц璁板綍闈㈡澘锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堝巻鍙叉墽琛岃褰曢潰鏉垮凡涓婄嚎锛?
**鏂板鍔熻兘**锛?- `obs_routes.py` 鏂板 `GET /obs/agent-loop/history` 绔偣锛堜粠 LOOP_DONE 瀹¤璁板綍涓彁鍙栵級
- `obs.js` 杩藉姞 `fetchAgentLoopHistory(params)` 鍑芥暟
- `AgentLoop.jsx` 鍦ㄩ〉闈㈠簳閮ㄦ柊澧炲巻鍙叉墽琛岃褰曡〃鏍硷紙杩?24h锛屾渶杩?10 娆★級
- 瀛楁锛氱洰鏍?鐘舵€?杈炬垚/杞/鑰楁椂/妯″紡/鏃堕棿

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/obs_routes.py`锛氭柊澧?/obs/agent-loop/history 绔偣
- `monitor_ui/src/api/obs.js`锛氭柊澧?fetchAgentLoopHistory 鍑芥暟
- `monitor_ui/src/pages/AgentLoop.jsx`锛氭柊澧炲巻鍙叉墽琛岃褰曢潰鏉?
---

### TODO-87锛欰utomation.jsx 鏂板璋冨害鍣ㄦ墽琛屽巻鍙叉椂搴忓浘锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堣皟搴﹀櫒鎵ц鍘嗗彶鏃跺簭鍥惧凡涓婄嚎锛?
**鏂板鍔熻兘**锛?- 鏂板 `SchedulerTimelineChart` 瀛愮粍浠讹細灞曠ず鍚勪换鍔℃墽琛岄噺瀵规瘮锛堟按骞虫潯褰㈠浘锛?- 缁胯壊鏉¤〃绀烘墽琛屾鏁帮紝绾㈣壊鍙犲姞鏉¤〃绀哄け璐ユ鏁帮紙鎸夋瘮渚嬫覆鏌擄級
- 鍦?SchedulerHistorySection 鐨勮〃鏍间箣鍚庤拷鍔狅紝鏈変换鍔℃暟鎹椂鏄剧ず

**鏀瑰姩鏂囦欢**锛?- `monitor_ui/src/pages/Automation.jsx`锛氭柊澧?SchedulerTimelineChart 缁勪欢 + 娓叉煋璋冪敤

---

### TODO-88锛欸ET /obs/error-heatmap 鈥?閿欒鐑姏鍥剧鐐癸紙宸插畬鎴愶紝2026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堥敊璇儹鍔涘浘绔偣宸蹭笂绾匡級

**鏂板鍔熻兘**锛?- `obs_routes.py` 鏂板 `GET /obs/error-heatmap` 绔偣锛堟敮鎸?window_hours 鍙傛暟锛屾渶澶?7 澶╋級
- 浠?error_log 涓寜 (hour, node_name) 鑱氬悎閿欒鍒嗗竷
- 杩斿洖锛氭€婚敊璇暟/灏忔椂鍒楄〃/鑺傜偣鍒楄〃/鐑姏鍥惧崟鍏冩牸/姣忓皬鏃跺悎璁?鑺傜偣鍚堣

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/obs_routes.py`锛氭柊澧?/obs/error-heatmap 绔偣

---

### TODO-89锛欼nsights.jsx 鏂板閿欒鐑姏鍥惧尯鍧楋紙宸插畬鎴愶紝2026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堥敊璇儹鍔涘浘鍖哄潡宸蹭笂绾匡級

**鏂板鍔熻兘**锛?- 鏂板 `ErrorHeatmapSection` 瀛愮粍浠讹紙Flame 鍥炬爣锛岀孩鑹蹭富棰橈級
- 24 灏忔椂 脳 鑺傜偣缁村害鐨勭儹鍔涘浘琛ㄦ牸锛堥鑹叉繁搴?= 閿欒鏁伴噺锛?- 姣忓皬鏃跺悎璁¤ + 鍥句緥锛堥鑹叉笎鍙樿鏄庯級
- 鏃犻敊璇椂鏄剧ず鍙嬪ソ鎻愮ず"鉁?绐楀彛鍐呮棤閿欒璁板綍"

**鏀瑰姩鏂囦欢**锛?- `monitor_ui/src/pages/Insights.jsx`锛氬紩鍏?fetchErrorHeatmap + 鏂板 ErrorHeatmapSection
- `monitor_ui/src/api/obs.js`锛氭柊澧?fetchErrorHeatmap 鍑芥暟

---

### TODO-90锛歄verview.jsx 鏂板瀹炴椂鍋ュ悍璇勫垎鍗＄墖锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堢郴缁熷仴搴疯瘎鍒嗗崱鐗囧凡涓婄嚎锛?
**鏂板鍔熻兘**锛?- 寮曞叆 `fetchChainStats`锛屽湪 load 涓苟琛岃姹備富閾捐矾缁熻鏁版嵁
- 鏂板绯荤粺鍋ュ悍璇勫垎鍗＄墖锛圚eartPulse 鍥炬爣锛夛細缁煎悎璇勪及绠楁硶锛堟弧鍒?100锛?  - Governor 鎷︽埅鐜?>10% 鎵?20 鍒嗭紝>5% 鎵?10 鍒?  - 娉ㄥ叆妫€娴嬬巼 >5% 鎵?15 鍒嗭紝>1% 鎵?5 鍒?  - 姣忎釜 down 鏈嶅姟鎵?10 鍒嗭紝姣忎釜 degraded 鏈嶅姟鎵?5 鍒?  - 鍒嗗彂鎴愬姛鐜?<70% 鎵?20 鍒嗭紝<90% 鎵?10 鍒?  - Canon 寰呭鏍?>5 鏉℃墸 5 鍒?- 璇勫垎 鈮?0 缁胯壊锛堝仴搴凤級锛屸墺70 姗欒壊锛堟敞鎰忥級锛?70 绾㈣壊锛堝憡璀︼級
- 鏄剧ず鍏蜂綋鎵ｅ垎鍘熷洜鍒楄〃

**鏀瑰姩鏂囦欢**锛?- `monitor_ui/src/pages/Overview.jsx`锛氬紩鍏?fetchChainStats + 鏂板鍋ュ悍璇勫垎鍗＄墖

---

### TODO-91锛歍race.jsx 鏂板鑺傜偣鎼滅储杩囨护锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堣妭鐐规悳绱㈣繃婊ゅ凡涓婄嚎锛?
**鏂板鍔熻兘**锛?- 鍦?GanttView 缁勪欢涓坊鍔犺妭鐐规悳绱㈣繃婊ゆ锛圫earch 鍥炬爣锛?- 鏀寔鎸夎妭鐐瑰悕/绫诲瀷鍏抽敭璇嶅疄鏃惰繃婊ょ敇鐗瑰浘鑺傜偣
- 杩囨护鏃舵樉绀?宸茶繃婊?鎬绘暟"璁℃暟锛岀偣鍑?脳 娓呴櫎杩囨护

**鏀瑰姩鏂囦欢**锛?- `monitor_ui/src/pages/Trace.jsx`锛氭柊澧?nodeFilter 鐘舵€?+ 鎼滅储妗?+ 杩囨护閫昏緫

---

### TODO-92锛歁etrics.jsx 鏂板 Chain 涓婚摼璺糠浣犲崱鐗囷紙宸插畬鎴愶紝2026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圕hain 涓婚摼璺糠浣犲崱鐗囧凡涓婄嚎锛?
**鏂板鍔熻兘**锛?- 鏂板 `ChainMiniCard`锛圓ctivity 鍥炬爣锛岃摑鑹蹭富棰橈級锛? 涓仛鍚堟寚鏍?+ Governor 鎷︽埅鐜囪繘搴︽潯
- 鎸囨爣锛氭€昏姹傛暟 / Governor 鎷︽埅鐜?/ 璁板繂鍐欏叆娆℃暟 / QA 閲嶅啓娆℃暟
- 鎷︽埅鐜?>10% 鏃惰繘搴︽潯鍙樼孩

**鏀瑰姩鏂囦欢**锛?- `monitor_ui/src/pages/Metrics.jsx`锛氭柊澧?ChainMiniCard 缁勪欢 + 娓叉煋璋冪敤

---

### TODO-93锛歮emory/distillation_engine.py 鈥?鎯呰妭璁板繂钂搁寮曟搸锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堣捀棣忓紩鎿庡凡涓婄嚎锛?
**鏂板鍔熻兘**锛?- 鏂板缓 `host_brain/app/memory/distillation_engine.py`锛垀230 琛岋級
- `DistillationEngine` 绫伙細浠?EpisodicStore 鎵归噺钂搁婊¤冻鏉′欢鐨勬儏鑺傝蹇嗕负 Canon 鎻愭
- 钂搁鏉′欢锛氶娆?鈮?3 OR MILESTONE 绾у埆锛堟帴鍏?MemoryGuard.check_distillation_eligibility锛?- 钂搁鍚庢竻鐞?SESSION_OVERRIDE锛堟帴鍏?StateArbiter.clear_session_override锛?- 钂搁缁撴灉鐢熸垚 Canon 鎻愭锛團ACT_CANDIDATE / PREFERENCE_CANDIDATE锛?- 鎷︽埅 NARRATIVE_ONLY锛坉ream 鍐呭锛?- 钂搁杩囩▼鍐欏叆 audit_log锛圖ISTILL_STARTED / DISTILL_DONE锛?- `BatchDistillationReport`锛氭壒閲忚捀棣忔姤鍛婏紙鍊欓€夋暟/鎴愬姛鏁?璺宠繃鏁?澶辫触鏁?鑰楁椂锛?
**鏀瑰姩鏂囦欢**锛?- `host_brain/app/memory/distillation_engine.py`锛氭柊寤烘枃浠?
---

### TODO-94锛歮emory/episodic_store.py 鈥?EpisodicStore 鎯呰妭璁板繂瀛樺偍锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圗pisodicStore 宸蹭笂绾匡級

**鏂板鍔熻兘**锛?- 鏂板缓 `host_brain/app/memory/episodic_store.py`锛垀200 琛岋級
- `EpisodicStore` 绫伙細EpisodicRecord 鐨勫啓鍏?鏌ヨ/骞傜瓑淇濇姢
- 鍐欏叆鍓嶉€氳繃 MemoryGuard.check_episodic_write() 鏍￠獙
- source_hash 骞傜瓑淇濇姢锛堢浉鍚屽唴瀹规嫆缁濋噸澶嶅啓鍏ワ級
- 鎸?session_id 鏌ヨ鏈€杩?N 鏉¤褰曪紙鏀寔 evidence_level 杩囨护锛?- `query_distill_candidates()`锛氭煡璇㈡弧瓒宠捀棣忚祫鏍肩殑鍊欓€夎褰?- LRU 娣樻卑锛堝唴瀛樹腑鏈€澶氫繚鐣?1000 鏉★級
- 鍙€?SQLiteLedger 鎸佷箙鍖栵紙鍐欏叆 EPISODIC_WRITTEN 瀹¤鏃ュ織锛?- `EpisodicWriteResult`锛氬啓鍏ョ粨鏋滐紙success/skipped/distill_eligible/occurrence_count锛?
**鏀瑰姩鏂囦欢**锛?- `host_brain/app/memory/episodic_store.py`锛氭柊寤烘枃浠?
---

### TODO-95锛歳ecall/semantic_recall.py 鈥?璇箟鍙洖鍣紙宸插畬鎴愶紝2026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堣涔夊彫鍥炲櫒宸蹭笂绾匡級

**鏂板鍔熻兘**锛?- 鏂板缓 `host_brain/app/recall/semantic_recall.py`锛垀250 琛岋級
- `SemanticRecaller` 绫伙細涓冭矾鍙洖涓殑鍚戦噺鍙洖璺紙episodic_vector 妲戒綅锛?- 鏀寔涓ょ鍚庣锛欳hromaDB锛堢敓浜э級鍜屽唴瀛?Mock锛堝紑鍙?娴嬭瘯锛?- ChromaDB 鍚庣锛歀2 璺濈杞浉浼煎害鍒嗘暟 + 鐩镐技搴﹂槇鍊艰繃婊?- Mock 鍚庣锛氬熀浜庡叧閿瘝閲嶅彔鐜囩殑鐩镐技搴︿及绠?- 闄嶇骇绛栫暐锛欳hromaDB 涓嶅彲杈炬椂杩斿洖绌虹粨鏋滐紙涓嶉樆鏂富閾捐矾锛?- `to_context_text()`锛氬皢鍙洖缁撴灉杞崲涓?PromptContext 鍙敤鐨勬枃鏈牸寮忥紙[EPISODIC_VECTOR] 鏍煎紡锛?- `SemanticRecallResult`锛氬彫鍥炵粨鏋滐紙hits/degraded/latency_ms锛?
**鏀瑰姩鏂囦欢**锛?- `host_brain/app/recall/semantic_recall.py`锛氭柊寤烘枃浠?
---

### TODO-96锛歁emoryGuard.check_prompt_injection() 鈥?鎻愮ず璇嶆敞鍏ユ娴嬶紙宸插畬鎴愶紝2026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堟彁绀鸿瘝娉ㄥ叆妫€娴嬪凡涓婄嚎锛?
**闂**锛歍ODO-64 涓?`prompt_builder.py` 宸茶皟鐢?`MemoryGuard.check_prompt_injection()`锛屼絾璇ユ柟娉曚粠鏈疄鐜帮紝瀵艰嚧杩愯鏃?AttributeError銆?
**鏂板鍔熻兘**锛?- `memory_guard.py` 鏂板 `check_prompt_injection(user_message, strict_mode)` 鏂规硶
- 鍩轰簬姝ｅ垯瑙勫垯鐨勪笁绾ф娴嬶細CRITICAL / HIGH / MEDIUM
- CRITICAL 妯″紡锛? 鏉¤鍒欙級锛歩gnore previous instructions / jailbreak / DAN mode / override system prompt 绛?- HIGH 妯″紡锛? 鏉¤鍒欙級锛歴ystem: you are / [SYSTEM] / reveal your prompt / print your instructions 绛?- MEDIUM 妯″紡锛? 鏉¤鍒欙級锛歩gnore the above / from now on you will / repeat the following 绛?- 杩斿洖锛歿detected, risk_level, matched_patterns, scan_ms}
- strict_mode=True 鏃?MEDIUM 绾у埆涔熻涓烘娴嬪埌娉ㄥ叆

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/memory/memory_guard.py`锛氭柊澧?check_prompt_injection() 鏂规硶 + 涓夌粍娉ㄥ叆妯″紡甯搁噺

---

### TODO-97锛歵ask_scheduler.py 鏂板浼樺厛绾ч槦鍒?+ 渚濊禆閾撅紙宸插畬鎴愶紝2026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堜紭鍏堢骇璋冨害宸蹭笂绾匡級

**闂**锛歚task_scheduler.py` 鍙湁绠€鍗?cron 璋冨害锛岀己灏?P0/P1/P2/P3 浼樺厛绾у拰 depends_on 渚濊禆閾撅紝鏃犳硶淇濊瘉鍏抽敭浠诲姟锛堝 MemoryGuard 娓呯悊锛変紭鍏堟墽琛屻€?
**鏂板鍔熻兘**锛?- 鏂板 `TaskPriority` 鏋氫妇锛圥0/P1/P2/P3锛屾暟鍊艰秺灏忎紭鍏堢骇瓒婇珮锛?- `ScheduledTask` 鏂板 `priority: TaskPriority` 鍜?`depends_on: list[str]` 瀛楁
- `_scheduler_loop()` 鏀逛负鎸変紭鍏堢骇鎺掑簭鍚庤Е鍙戯紙P0 鏈€鍏堟墽琛岋級
- 鏂板 `_check_dependencies()` 鏂规硶锛氭墍鏈変緷璧栦换鍔℃渶杩戜竴娆℃墽琛屾垚鍔熷悗鎵嶈Е鍙戞湰浠诲姟
- 渚濊禆鏈弧瓒虫椂浠诲姟鐘舵€佽涓?`BLOCKED`
- `as_dict()` 鏂板 priority 鍜?depends_on 瀛楁

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/automation/task_scheduler.py`锛氭柊澧?TaskPriority 鏋氫妇 + priority/depends_on 瀛楁 + 浼樺厛绾ц皟搴﹂€昏緫

---

### TODO-98锛歊outingMatrix.select_with_cost_budget() 鈥?鎴愭湰棰勭畻璺敱锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堟垚鏈绠楄矾鐢卞凡涓婄嚎锛?
**闂**锛歚routing_matrix.py` 鍙湁鏁呴殰闄嶇骇璺敱锛岀己灏戞寜 token 鎴愭湰棰勭畻璺敱鐨勮兘鍔涳紝鏃犳硶鍦?cloud 鎴愭湰杩囬珮鏃惰嚜鍔ㄩ檷绾у埌 host_8b銆?
**鏂板鍔熻兘**锛?- `RoutingMatrix` 鏂板 `select_with_cost_budget()` 鏂规硶
- 鍚勬ā鍨嬬浉瀵规垚鏈細cloud=1.0 / host_8b=0.05 / edge_3b=0.01 / skills=0.02
- 涓昏矾鐢辨垚鏈秴鍑?max_cost_per_1k 鏃讹紝鑷姩鎸夋垚鏈粠浣庡埌楂樺鎵炬浛浠ｆā鍨?- HIGH risk_level 绂佹闄嶇骇鍒?edge_3b锛堜笌 select_with_fallback 淇濇寔涓€鑷达級
- 鎵€鏈夋ā鍨嬪潎瓒呭嚭棰勭畻鏃惰繑鍥炰富璺敱锛堣皟鐢ㄦ柟鑷澶勭悊锛?- 闄嶇骇鍐崇瓥鍐欏叆璺敱鏃ュ織锛坕s_fallback=True + fallback_reason 鍚垚鏈俊鎭級

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/llm/routing_matrix.py`锛氭柊澧?select_with_cost_budget() 鏂规硶 + _MODEL_COST_PER_1K 甯搁噺

---

### TODO-99锛歞ependencies.py 鏂板 get_distillation_engine() + get_semantic_recaller()锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堜緷璧栨敞鍏ュ伐鍘傚凡涓婄嚎锛?
**鏂板鍔熻兘**锛?- `dependencies.py` 鏂板 `get_distillation_engine()` 宸ュ巶鍑芥暟
  - 寤惰繜鍒濆鍖?DistillationEngine 鍗曚緥
  - 鑷姩鎺ュ叆 get_memory_guard() / get_state_arbiter() / get_ledger()
  - 鍏煎鏃х増 EpisodicStore锛坔ost_brain.memory.episodic锛夊拰鏂扮増锛坅pp/memory/episodic_store.py锛?- `dependencies.py` 鏂板 `get_semantic_recaller()` 宸ュ巶鍑芥暟
  - 寤惰繜鍒濆鍖?SemanticRecaller 鍗曚緥
  - 鏀寔鐜鍙橀噺閰嶇疆锛歋EMANTIC_RECALL_USE_MOCK / CHROMA_HOST / CHROMA_PORT / SEMANTIC_RECALL_TOP_K / SEMANTIC_RECALL_THRESHOLD
  - 榛樿 use_mock=True锛堝紑鍙戠幆澧冿級锛岀敓浜х幆澧冭 SEMANTIC_RECALL_USE_MOCK=false

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/dependencies.py`锛氭柊澧炲鍏?+ 鍗曚緥鍙橀噺 + 涓や釜宸ュ巶鍑芥暟

---

### TODO-100锛歨ttp_hooks.py 鎺ュ叆 DistillationEngine 钂搁瀹氭椂浠诲姟锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堣捀棣忓畾鏃朵换鍔″凡娉ㄥ唽锛?
**鏂板鍔熻兘**锛?- `http_hooks.py` lifespan 涓敞鍐?DistillationEngine 钂搁瀹氭椂浠诲姟鍒?HostTaskScheduler
- 浠诲姟閰嶇疆锛歵ask_id=distillation_engine_periodic / 闂撮殧 1800s锛?0 鍒嗛挓锛? P1 浼樺厛绾?/ 鏈€澶氶噸璇?2 娆?- 鍚姩鐘舵€佸啓鍏?new_module_statuses["distillation_engine_scheduler"]
- 澶辫触鏃堕檷绾э紙涓嶉樆鏂惎鍔ㄦ祦绋嬶級

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/http_hooks.py`锛氭柊澧炶捀棣忓畾鏃朵换鍔℃敞鍐屼唬鐮佸潡

---

### TODO-101锛歊ecallPackBuilder.build_with_semantic_recall() 鈥?鎺ュ叆璇箟鍙洖锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堣涔夊彫鍥炴帴鍏ュ凡涓婄嚎锛?
**鏂板鍔熻兘**锛?- `recall_pack_builder.py` 鏂板 `build_with_semantic_recall()` 寮傛鏂规硶
- 鑻?recall_results 涓凡鏈?episodic_vector锛岀洿鎺ヤ娇鐢紙璋冪敤鏂逛紭鍏堬級
- 鑻?episodic_vector 涓虹┖锛岃嚜鍔ㄨ皟鐢?SemanticRecaller.recall() 濉厖
- SemanticRecaller 澶辫触鏃堕檷绾т负绌猴紙涓嶉樆鏂富閾捐矾锛?- 鏀寔 semantic_recall_max_tokens 鍙傛暟鎺у埗 episodic_vector 妲戒綅 token 涓婇檺

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/recall/recall_pack_builder.py`锛氭柊澧?build_with_semantic_recall() 鏂规硶

---

### TODO-102锛歵ask_scheduler.py 鏂板 register_task() + get_all_tasks_status()锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堜究鎹锋柟娉曞凡涓婄嚎锛?
**鏂板鍔熻兘**锛?- `HostTaskScheduler` 鏂板 `register_task()` 渚挎嵎鏂规硶锛氱洿鎺ヤ紶鍏ュ弬鏁版瀯寤?ScheduledTask 骞舵敞鍐岋紙鏃犻渶鎵嬪姩鍒涘缓 ScheduledTask 瀹炰緥锛?- `HostTaskScheduler` 鏂板 `get_all_tasks_status()` 鏂规硶锛氳繑鍥炴寜浼樺厛绾ф帓搴忕殑鎵€鏈変换鍔＄姸鎬佸揩鐓у垪琛?- `typing.Optional` 瀵煎叆琛ュ叏

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/automation/task_scheduler.py`锛氭柊澧?register_task() + get_all_tasks_status() + Optional 瀵煎叆

---

### TODO-103锛歰bs_routes.py 鏂板 GET /obs/semantic-recall/stats锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堣涔夊彫鍥炵粺璁＄鐐瑰凡涓婄嚎锛?
**鏂板鍔熻兘**锛?- 鏂板 `GET /obs/semantic-recall/stats` 绔偣
- 杩斿洖 SemanticRecaller 鐨勯厤缃俊鎭細use_mock / chroma_host / chroma_port / top_k / similarity_threshold / collection_name / mock_doc_count
- 澶辫触鏃惰繑鍥?DEGRADED 鐘舵€侊紙涓嶆姏鍑?500锛?
**鏀瑰姩鏂囦欢**锛?- `host_brain/app/obs_routes.py`锛氭柊澧?SemanticRecallStatsResponse + get_semantic_recall_stats 绔偣

---

### TODO-104锛歮emory/__init__.py 鏇存柊瀵煎嚭锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堟柊妯″潡宸叉毚闇诧級

**鏂板鍔熻兘**锛?- `memory/__init__.py` 鏂板瀵煎嚭锛欵pisodicStore / EpisodicWriteResult / DistillationEngine / DistillationResult / BatchDistillationReport
- 浣垮閮ㄦā鍧楀彲閫氳繃 `from host_brain.app.memory import EpisodicStore` 鐩存帴瀵煎叆

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/memory/__init__.py`锛氭柊澧?4 涓被鐨勫鍑?
---

### TODO-105锛歰bs.js 鏂板 fetchSemanticRecallStats()锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`

**鏂板鍔熻兘**锛?- `obs.js` 鏈熬鏂板 `fetchSemanticRecallStats()` API 鍑芥暟
- 璋冪敤 `GET /obs/semantic-recall/stats` 绔偣

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/monitor_ui/src/api/obs.js`锛氭柊澧?fetchSemanticRecallStats()

---

### TODO-106锛歁etrics.jsx 鏂板 DistillationMiniCard + SemanticRecallMiniCard锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堜袱涓糠浣犲崱鐗囧凡涓婄嚎锛?
**鏂板鍔熻兘**锛?- `DistillationMiniCard`锛氬睍绀烘儏鑺傝蹇嗚捀棣忕粺璁★紙杩?24h锛?  - 鎬诲€欓€?/ 钂搁鎴愬姛 / 璺宠繃 / 澶辫触 鍥涙牸缁熻
  - 钂搁鎴愬姛鐜囪繘搴︽潯锛?70% 鏄剧ず姗欒壊璀﹀憡锛?- `SemanticRecallMiniCard`锛氬睍绀?SemanticRecaller 閰嶇疆鍜岀姸鎬?  - Top-K / 鐩镐技搴﹂槇鍊?/ Mock 鏂囨。鏁?/ 妯″紡锛圡ock/Chroma锛夊洓鏍肩粺璁?  - Mock 妯″紡鏃舵樉绀烘鑹茶鍛婃彁绀?  - ChromaDB 妯″紡鏃舵樉绀鸿繛鎺ヤ俊鎭?- 鏂板 `Layers`銆乣Search` 鍥炬爣瀵煎叆
- 鏂板 `fetchDistillationStats`銆乣fetchSemanticRecallStats` API 瀵煎叆

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/monitor_ui/src/pages/Metrics.jsx`锛氭柊澧炰袱涓糠浣犲崱鐗囩粍浠?
---

### TODO-107锛氭柊寤?automation/scheduler_routes.py锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圚ostTaskScheduler REST API 宸蹭笂绾匡級

**鏂板鍔熻兘**锛?- 鏂板缓 `host_brain/app/automation/scheduler_routes.py`
- 瀹炵幇 5 涓鐐癸細
  - `GET /host-scheduler/tasks` 鈥?鍒楀嚭鎵€鏈変换鍔★紙鍚姸鎬侊紝鎸変紭鍏堢骇鎺掑簭锛?  - `GET /host-scheduler/health` 鈥?璋冨害鍣ㄥ仴搴峰揩鐓?  - `POST /host-scheduler/tasks/{id}/enable` 鈥?鍚敤浠诲姟
  - `POST /host-scheduler/tasks/{id}/disable` 鈥?绂佺敤浠诲姟
  - `POST /host-scheduler/tasks/{id}/trigger` 鈥?绔嬪嵆瑙﹀彂浠诲姟
  - `PATCH /host-scheduler/tasks/{id}` 鈥?鏇存柊浠诲姟閰嶇疆锛堝吋瀹瑰墠绔?trigger_config 鍐欐硶锛?- 浣跨敤 `/host-scheduler` 鍓嶇紑锛岄伩鍏嶄笌鏃?`/scheduler` 璺敱鍐茬獊

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/automation/scheduler_routes.py`锛氭柊寤烘枃浠?
---

### TODO-108锛歠astapi_app.py 娉ㄥ唽 host_scheduler_router锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堣矾鐢卞凡娉ㄥ唽锛?
**鏂板鍔熻兘**锛?- `fastapi_app.py` 瀵煎叆骞舵敞鍐?`host_scheduler_router`
- 浣跨敤 `prefix="/host-scheduler"` 娉ㄥ唽锛岄伩鍏嶄笌鏃?`/scheduler` 璺敱鍐茬獊

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/fastapi_app.py`锛氭柊澧炲鍏?+ include_router

---

### TODO-109锛歱rompt_builder.py 鏂板 episodic_vector 妲戒綅娉ㄥ叆锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛堣涔夊彫鍥炵粨鏋滃凡娉ㄥ叆鎻愮ず璇嶏級

**鏂板鍔熻兘**锛?- `render_node_generation_prompt` 涓柊澧?`episodic_vector` 妲戒綅璇诲彇閫昏緫
- 浼樺厛浠?`prompt_context["episodic_vector"]` 璇诲彇锛屽叾娆′粠 `runtime_payload["episodic_vector"]` 璇诲彇
- 鏀寔瀛楃涓插拰鍒楄〃涓ょ鏍煎紡锛堝垪琛ㄦ牸寮忔敮鎸?`{"text": "...", "score": 0.9}` 缁撴瀯锛?- 娉ㄥ叆涓?`EPISODIC_VECTOR_RECALL` 鍧楋紝浣嶄簬 `EVIDENCE` 鍧椾箣鍚?- 浠呭湪鏈夊唴瀹规椂娉ㄥ叆锛堢┖鏃惰烦杩囷紝涓嶅奖鍝嶆彁绀鸿瘝缁撴瀯锛?
**鏀瑰姩鏂囦欢**锛?- `host_brain/app/prompt_builder.py`锛氭柊澧?episodic_vector 妲戒綅璇诲彇 + EPISODIC_VECTOR_RECALL 鍧楁敞鍏?
---

### TODO-110锛歠ailure_matrix.py 鏂板 PROMPT_INJECTION 澶辫触浜嬩欢锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`

**鏂板鍔熻兘**锛?- `FailureEvent.PROMPT_INJECTION` 鏋氫妇鍊?- 瀵瑰簲鐨勭敤鎴峰搷搴旀枃鏈紙"鎶辨瓑锛屾垜鏃犳硶澶勭悊杩欎釜璇锋眰銆?锛?- `ABORT_FLAGS[PROMPT_INJECTION] = True`锛堢洿鎺ヤ腑姝㈤摼璺級
- `_handle_specific` 涓褰曟敞鍏ユ娴嬭鎯咃細level / matched_pattern / raw_input_preview
- 鍐欏叆 WARNING 鏃ュ織锛岀‘淇濊繍缁村彲鎰熺煡

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/failure_matrix.py`锛氭柊澧?PROMPT_INJECTION 浜嬩欢鍏ㄩ摼璺鐞?
---

### TODO-111锛歞istillation_engine.py 鏂板 distill_single() 鏂规硶锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`

**鏂板鍔熻兘**锛?- `DistillationEngine.distill_single()` 鏂规硶
- 鏀寔鍗曟潯璁板繂寮哄埗钂搁锛堣烦杩囬娆￠棬妲?min_occurrence锛?- `force=True` 鏃惰烦杩?MemoryGuard 鐨勫箓绛変繚鎶わ紙鍏佽閲嶅钂搁锛?- `clear_session_override=True` 鏃惰捀棣忓畬鎴愬悗娓呯悊璇?session 鐨?SESSION_OVERRIDE
- NARRATIVE_ONLY锛坉ream 鍐呭锛夊嵆浣?force=True 涔熶笉鍏佽钂搁
- 鍐欏叆 DISTILL_SINGLE_STARTED / DISTILL_DONE / DISTILL_SKIPPED 瀹¤鏃ュ織

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/memory/distillation_engine.py`锛氭柊澧?distill_single() 鏂规硶

---

### TODO-112锛歴emantic_recall.py 鏂板 add_document() / add_documents() 鏂规硶锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`

**鏂板鍔熻兘**锛?- `SemanticRecaller.add_document()` 鏂规硶锛氳繍琛屾椂鍔ㄦ€佹坊鍔犲崟鏉℃枃妗?  - Mock 妯″紡锛氬啓鍏ュ唴瀛樺瓨鍌紙骞傜瓑锛氱浉鍚?doc_id 瑕嗙洊锛?  - ChromaDB 妯″紡锛歶psert 鍒板悜閲忔暟鎹簱
  - 鍐呭涓虹┖鏃惰烦杩囷紝澶辫触鏃朵笉鎶涘嚭寮傚父锛堥檷绾у畨鍏級
- `SemanticRecaller.add_documents()` 鏂规硶锛氭壒閲忔坊鍔犳枃妗?  - 杩斿洖 {total, success, failed, failed_ids} 缁熻
  - 璺宠繃缂哄皯 doc_id 鐨勬枃妗?
**鏀瑰姩鏂囦欢**锛?- `host_brain/app/recall/semantic_recall.py`锛氭柊澧?add_document() / add_documents() 鏂规硶

---

### TODO-113锛歵ool_meta.py HotLoader 鏂板杩囨护鏂规硶锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`

**鏂板鍔熻兘**锛?- `HotLoader.list_tools_by_tag(tag)` 鏂规硶锛氭寜鏍囩杩囨护宸ュ叿鍒楄〃锛堝ぇ灏忓啓涓嶆晱鎰燂級
- `HotLoader.list_tools_by_risk(risk_level, include_higher=False)` 鏂规硶锛氭寜椋庨櫓绛夌骇杩囨护
  - `include_higher=True` 鏃跺悓鏃惰繑鍥炴洿楂橀闄╃瓑绾х殑宸ュ叿
  - 缁撴灉鎸夐闄╃瓑绾у崌搴忋€佸伐鍏峰悕绉版帓搴?
**鏀瑰姩鏂囦欢**锛?- `host_brain/app/skills/tool_meta.py`锛氭柊澧?list_tools_by_tag() / list_tools_by_risk() 鏂规硶

---

### TODO-114锛歶i_assertion.py 鏂板 batch_pre_check() 鏂规硶锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`

**鏂板鍔熻兘**锛?- `UIAssertionGuard.batch_pre_check(actions, stop_on_first_failure=True)` 鏂规硶
- 瀵瑰姝ラ鎿嶄綔搴忓垪涓殑姣忎釜鍔ㄤ綔鎵ц鍓嶇疆鏂█
- `stop_on_first_failure=True` 鏃堕亣鍒扮涓€涓け璐ュ嵆鍋滄
- 杩斿洖璇︾粏姹囨€伙細passed / total / passed_count / failed_count / results / first_failure_index
- 澶辫触鏃跺啓鍏?BATCH_PRE_CHECK_FAILED 瀹¤浜嬩欢

**鏀瑰姩鏂囦欢**锛?- `host_brain/app/vision/ui_assertion.py`锛氭柊澧?batch_pre_check() 鏂规硶

---

### TODO-115锛歰bs_routes.py 鏂板 GET /obs/failure-events 绔偣锛堝凡瀹屾垚锛?026-03-12锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`

**鏂板鍔熻兘**锛?- `GET /obs/failure-events` 绔偣
- 鍙傛暟锛歸indow_minutes锛?~10080锛? event_type锛堝彲閫夎繃婊わ級/ limit锛?~200锛?- 浠?audit_log 鎸?audit_tag 鑱氬悎澶辫触浜嬩欢
- 杩斿洖锛歵otal_failures / event_counts锛堟寜绫诲瀷鍒嗙粍锛? recent_events锛堟渶杩?N 鏉★級
- 鏀寔 8 绉嶅け璐ヤ簨浠剁被鍨嬶紙鍚柊澧炵殑 PROMPT_INJECTION锛?
---

### TODO-116锛欼DE Skills 鎺ュ叆杩愯鏃讹紙宸插畬鎴愶紝2026-03-13锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`锛圛DE Skills 宸叉敞鍐屽埌杩愯鏃?SkillRegistry锛?
**闂**锛歚host_brain/skills/ide_registry.py` 涓殑 `IDESkillProvider` 宸插皝瑁?`open_cursor` / `run_pycharm_action` / `codex_task` 涓変釜 Skills锛屼絾 `dependencies.py` 鐨?`get_skill_registry()` 浠庢湭鍔犺浇 `IDESkillProvider`锛屽鑷磋繍琛屾椂鏃犳硶閫氳繃 Governor 璋冪敤 IDE Skills銆?
**淇**锛?- `dependencies.py` 鏂板 `IDESkillProvider` 瀵煎叆
- `get_skill_registry()` 鍦ㄦ瀯寤哄唴缃?registry 鍚庯紝璋冪敤 `_skill_registry.register_decorated(IDESkillProvider())`
- 鏀寔鐜鍙橀噺 `HOST_SKILL_IDE_ENABLED`锛堥粯璁?`true`锛夋帶鍒舵槸鍚﹀姞杞?IDE Skills
- 娉ㄥ唽澶辫触鏃堕檷绾э紙浠?warning 鏃ュ織锛屼笉闃绘柇鍚姩锛?
**鏂板 Skills**锛?| skill_name | clearance | risk_level | scope | 璇存槑 |
|---|---|---|---|---|
| `open_cursor` | LOW | 2 | system | 鍦?Cursor 涓墦寮€鏂囦欢/椤圭洰 |
| `run_pycharm_action` | MEDIUM | 3 | system | 瑙﹀彂 PyCharm action |
| `codex_task` | MEDIUM | 3 | system | 鎻愪氦缂栫爜浠诲姟鍒?Codex 鎵ц鍣?|

**鍥為€€鏂瑰紡**锛氳缃?`HOST_SKILL_IDE_ENABLED=false`锛孖DE Skills 涓嶆敞鍐岋紝涓嶅奖鍝嶅叾浠?Skills銆?
**鏀瑰姩鏂囦欢**锛?- `host_brain/app/dependencies.py`锛氭柊澧?`IDESkillProvider` 瀵煎叆 + `get_skill_registry()` 娉ㄥ唽閫昏緫

---

### TODO-117锛歜uiltin_registry 鏂板瀹炵敤 Skills锛堝凡瀹屾垚锛?026-03-13锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`

**闂**锛歚builtin_registry.py` 涓?4 涓唴缃?Skills 鍏ㄩ儴浣跨敤 `_builtin_noop_handler` 鍗犱綅绗︼紝娌℃湁瀹為檯璇箟锛孏overnor 鏃犳硶閫氳繃杩欎簺 Skills 鑾峰彇鏈夋晥鐨勮繍琛屾椂鏁版嵁锛堟儏鎰熺姸鎬併€丅ond 绉垎銆佷細璇濇憳瑕侊級銆?
**鏂板鍐呭**锛?- `_emotion_query_handler`锛氫粠 ctx 璇诲彇 `emotion_snapshot` / `emotion_context`锛岃繑鍥炴爣鍑嗗寲鎯呮劅鎽樿
- `_bond_query_handler`锛氫粠 ctx 璇诲彇 `bond_texture` / `bond_stage`锛岃繑鍥?Bond 绉垎鍜屽叧绯婚樁娈?- `_session_summary_handler`锛氫粠 ctx 璇诲彇 `session_id` / `turn_count` / `user_message`锛岃繑鍥炰細璇濊交閲忔憳瑕?- 鎵€鏈夊師鏈?Skills 琛ュ厖浜?`description` 瀛楁

**鏂板 Skills**锛?| skill_name | clearance | risk_level | scope | 璇存槑 |
|---|---|---|---|---|
| `emotion_query` | LOW | 1 | host | 鏌ヨ褰撳墠鎯呮劅鐘舵€佸揩鐓?|
| `bond_query` | LOW | 1 | host | 鏌ヨ Bond 绉垎鍜屽叧绯婚樁娈?|
| `session_summary` | LOW | 1 | host | 鐢熸垚浼氳瘽杞婚噺鎽樿 |

**鏀瑰姩鏂囦欢**锛?- `host_brain/skills/builtin_registry.py`锛氭柊澧?3 涓笓鐢?handler + 娉ㄥ唽 3 涓柊 Skills锛屽唴缃?Skills 鎬绘暟浠?4 涓鑷?7 涓?
---

### TODO-118锛歱rompt_builder 琛ュ厖 VISION_UI 鍦烘櫙 role instruction锛堝凡瀹屾垚锛?026-03-13锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`

**闂**锛歚_build_node_role_instruction()` 涓病鏈?`workbench_vision_ui` 鍜?`vision_ui_analyzer` 鑺傜偣鐨勪笓鐢ㄦ寚浠わ紝`SCENE_VISION_UI` 鍦烘櫙涓嬭繖涓や釜鑺傜偣浼氶€€鍖栧埌 `generic node` 鎸囦护锛屽鑷?LLM 杈撳嚭璐ㄩ噺涓嬮檷銆?
**鏂板鍐呭**锛?- `workbench_vision_ui`锛歎I 杈呭姪鍒嗘瀽鑺傜偣锛屽己璋冪簿纭弿杩?UI 鍏冪礌銆佸缓璁氦浜掓搷浣溿€佷笉澹扮О宸叉墽琛?- `vision_ui_analyzer`锛歎I 鍏冪礌缁撴瀯鍖栨彁鍙栬妭鐐癸紝寮鸿皟杈撳嚭绱у噾 JSON銆佷笉鎺ㄦ柇瑙嗚涔嬪鐨勬剰鍥俱€佹爣璁颁綆缃俊搴︽娴?
**鏀瑰姩鏂囦欢**锛?- `host_brain/app/prompt_builder.py`锛歚role_instructions` 瀛楀吀鏂板 `workbench_vision_ui` 鍜?`vision_ui_analyzer` 涓や釜鏉＄洰

---

### TODO-119锛歩de_registry 鏂板 open_file_in_editor 閫氱敤 Skill锛堝凡瀹屾垚锛?026-03-13锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`

**闂**锛歚IDESkillProvider` 涓?`open_cursor` 缁戝畾浜嗗叿浣?IDE锛岀己灏戜竴涓笉缁戝畾 IDE 鐨勯€氱敤鏂囦欢鎵撳紑鍏ュ彛锛屾棤娉曞湪鐢ㄦ埛鏈畨瑁?Cursor 鏃堕檷绾у埌鍏朵粬缂栬緫鍣ㄣ€?
**鏂板鍐呭**锛?- `open_file_in_editor`锛氶€氱敤鏂囦欢鎵撳紑 Skill锛屾敮鎸?`editor` 鍙傛暟锛坈ursor / pycharm / vscode / default锛夛紝鏀寔 `line` / `column` 璺宠浆锛屼笉缁戝畾鍏蜂綋 IDE

**鏂板 Skills**锛?| skill_name | clearance | risk_level | scope | 璇存槑 |
|---|---|---|---|---|
| `open_file_in_editor` | LOW | 2 | system | 鍦ㄦ寚瀹?鑷姩妫€娴嬬殑缂栬緫鍣ㄤ腑鎵撳紑鏂囦欢 |

**鏀瑰姩鏂囦欢**锛?- `host_brain/skills/ide_registry.py`锛歚IDESkillProvider` 鏂板 `open_file_in_editor` 鏂规硶锛孖DE Skills 鎬绘暟浠?3 涓鑷?4 涓?
---

### TODO-120锛氭柊澧炴祻瑙堝櫒鑷姩鍖?Skills锛堝凡瀹屾垚锛?026-03-13锛?
**褰撳墠鐘舵€?*锛歚ACTIVE`

**闂**锛氶」鐩己灏戞祻瑙堝櫒鑷姩鍖栬兘鍔涳紝鏃犳硶鏀寔 agent-browser / Playwright / Puppeteer 绛夎嚜鍔ㄥ寲鍦烘櫙锛孏overnor 鏃犳硶閫氳繃 Skill 绠￠亾璋冨害娴忚鍣ㄦ搷浣溿€?
**鏂板鍐呭**锛?- 鏂板缓 `host_brain/skills/browser_registry.py`锛屽寘鍚?`BrowserSkillProvider` 绫诲拰 `build_browser_skill_registry()` 宸ュ巶鍑芥暟
- 鍦?`dependencies.py` 涓€氳繃鐜鍙橀噺 `HOST_SKILL_BROWSER_ENABLED`锛堥粯璁?True锛夋帶鍒舵槸鍚︽敞鍐屾祻瑙堝櫒 Skills

**鏂板 Skills锛? 涓級**锛?| skill_name | clearance | risk_level | reversible | require_confirm | 璇存槑 |
|---|---|---|---|---|---|
| `browser_navigate` | LOW | 2 | True | False | 瀵艰埅鍒版寚瀹?URL锛屾敮鎸?wait_until / timeout_ms |
| `browser_click` | MEDIUM | 3 | True | False | 鐐瑰嚮 CSS/XPath 鍏冪礌锛屾敮鎸?left/right/middle 鎸夐敭 |
| `browser_type` | MEDIUM | 3 | True | False | 鍦ㄨ緭鍏ユ涓€愬瓧杈撳叆鏂囨湰锛屾敮鎸?delay_ms / clear_first |
| `browser_screenshot` | LOW | 2 | True | False | 鎴浘锛堝叏椤?鍏冪礌锛夛紝杩斿洖 base64 鎴栦繚瀛樺埌璺緞 |
| `browser_execute_script` | MEDIUM | 3 | False | False | 鎵ц浠绘剰 JS 鑴氭湰锛屾敮鎸佷紶鍙?|
| `browser_wait_for` | LOW | 2 | True | False | 绛夊緟 selector/url/function/timeout 鏉′欢婊¤冻 |
| `browser_scrape` | LOW | 2 | True | False | 鎶撳彇椤甸潰 text/html/attribute锛屾敮鎸?limit |
| `browser_close` | MEDIUM | 4 | False | **True** | 鍏抽棴 page/browser锛堜笉鍙€嗭紝闇€纭锛?|

**椋庨櫓鍒嗙骇璁捐**锛?- `risk_level=2`锛氬彧璇绘搷浣滐紙瀵艰埅銆佹埅鍥俱€佺瓑寰呫€佹姄鍙栵級鈫?ApprovalTier.CACHE 閫氶亾
- `risk_level=3`锛氬啓鎿嶄綔锛堢偣鍑汇€佽緭鍏ャ€佽剼鏈墽琛岋級鈫?ApprovalTier.FAST 閫氶亾
- `risk_level=4`锛氱牬鍧忔€ф搷浣滐紙鍏抽棴娴忚鍣級鈫?ApprovalTier.CONFIRM 閫氶亾锛宍require_confirm=True`

**鐜鍙橀噺**锛?- `HOST_SKILL_BROWSER_ENABLED`锛氭槸鍚﹀惎鐢ㄦ祻瑙堝櫒 Skills锛堥粯璁?`true`锛?
**鏀瑰姩鏂囦欢**锛?- `host_brain/skills/browser_registry.py`锛氭柊寤猴紝鍖呭惈 `BrowserSkillProvider`锛? 涓?Skills锛? `build_browser_skill_registry()`
- `host_brain/app/dependencies.py`锛氬鍏?`BrowserSkillProvider`锛屽湪 `get_skill_registry()` 涓敞鍐屾祻瑙堝櫒 Skills








