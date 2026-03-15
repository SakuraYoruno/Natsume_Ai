# LangGraph 瀹屾暣缂栨帓鑳藉姏 vs 鏍稿績绾︽潫鍐茬獊鍒嗘瀽鎶ュ憡

- **鏂囨。缂栧彿**锛歀ANGGRAPH_CONSTRAINT_CONFLICT_ANALYSIS
- **鐢熸垚鏃ユ湡**锛?026-03-06
- **瑙勬牸渚濇嵁**锛歚docs/30_devspec/ModelPlane.md`
- **鍒嗘瀽瀵硅薄**锛歚host_brain/integrations/graph_executor.py` / `graph_runtime.py`
- **缁撹鎽樿**锛歀angGraph 鐨?5 椤归珮绾х紪鎺掕兘鍔涗笌 ModelPlane.md 鐨勭‖绾︽潫瀛樺湪缁撴瀯鎬у啿绐侊紝褰撳墠鏋舵瀯涓烘湁鎰忛檷绾т娇鐢紝灞炰簬娌荤悊浼樺厛浜庢鏋惰兘鍔涚殑涓诲姩鍙栬垗銆?
---

## 涓€銆丩angGraph 瀹屾暣缂栨帓鑳藉姏娓呭崟

LangGraph 鍘熺敓鏀寔鐨勫畬鏁村浘缂栨帓鑳藉姏锛?
```
鐪熸鐨勫鑺傜偣鍥剧粨鏋?鈹溾攢鈹€ 鏉′欢杈癸紙conditional_edges锛夆€斺€?渚濊禆鑺傜偣杈撳嚭鍔ㄦ€佽矾鐢?鈹溾攢鈹€ 骞惰鍒嗘敮锛坒an-out / fan-in锛夆€斺€?澶氳妭鐐瑰悓鏃舵墽琛?鈹溾攢鈹€ 瀛愬浘锛圫ubGraph锛夆€斺€?宓屽缂栨帓锛岀埗鍥捐皟鐢ㄥ瓙鍥?鈹溾攢鈹€ 娴佸紡杈撳嚭锛坰tream_mode锛夆€斺€?閫?token 娴佸紡鎺ㄩ€?鈹斺攢鈹€ 鏂偣缁窇锛圕heckpointer锛夆€斺€?interrupt_before/after + 浜哄伐浠嬪叆
```

---

## 浜屻€佸綋鍓嶅疄闄呬娇鐢ㄧ殑鍥剧粨鏋?
`compile_runtime_graph()` 缂栬瘧鍑虹殑瀹為檯鍥撅細

```python
graph.add_node("step",   RunnableLambda(self._run_single_step))
graph.add_node("finish", RunnableLambda(lambda state: state))
graph.add_edge(START, "step")
graph.add_conditional_edges("step", self._route_after_step,
                            {"step": "step", "finish": "finish"})
graph.add_edge("finish", END)
```

**瀹為檯鎷撴墤**锛?
```
START 鈫?step 鉄?(while loop) 鈫?finish 鈫?END
```

LangGraph 鍦ㄦ浠呬綔涓?**鐘舵€佷紶鎾鍣?* 鍜?**鍙娴嬩笂涓嬫枃**锛屾墍鏈夌紪鎺掑喅绛栨潈鏀跺洖 Host 渚?`edge_map` 瀛楀吀銆?
---

## 涓夈€侀€愭潯鍐茬獊鍒嗘瀽

### 鍐茬獊 1锛氭潯浠惰竟锛圠LM 椹卞姩璺敱锛塿s 搂0.1 涓绘潈绾︽潫

| 缁村害 | LangGraph 鍘熺敓 | 褰撳墠瀹炵幇 |
|------|---------------|---------|
| **璺敱鍐崇瓥鑰?* | LLM 鑺傜偣杈撳嚭 鈫?`conditional_edges` 鍔ㄦ€佸喅瀹氫笅涓€鑺傜偣 | `edge_map` 瀛楀吀锛圥ython dict锛孒ost 渚ч潤鎬佸畾涔夛級 |
| **鍐茬獊鏉℃** | 搂0.1锛?LLM 鑺傜偣姘歌繙涓嶈鍐炽€佷笉鎵ц" | 鈥?|
| **椋庨櫓** | 鑻?`safety_verifier` 杈撳嚭 `verdict=FAIL` 鍚庣敱 LangGraph 鏉′欢杈瑰喅瀹氳矾鐢憋紝鍒欒矾鐢卞喅绛栨潈鍦?LLM 杈撳嚭锛岃繚鍙嶄富鏉冪害鏉?| 鈥?|

**鏍规湰鐭涚浘**锛歀angGraph 鐨勬潯浠惰竟澶╃劧璁捐涓?璁╄妭鐐硅緭鍑哄喅瀹氫笅涓€姝?锛岃€?搂0.1 瑕佹眰"LLM 涓嶅緱瑁佸喅"銆?
**褰撳墠鏇夸唬鏂规**锛歚edge_map` 瀛楀吀鐢?Host 渚ч潤鎬佸畾涔夛紝LLM 杈撳嚭鍙奖鍝?payload锛屼笉褰卞搷鍥剧粨鏋勩€?
---

### 鍐茬獊 2锛氬苟琛屽垎鏀紙fan-out锛塿s 搂0.2 AIRLOCK 涓茶瀹¤

**LangGraph 骞惰鍐欐硶**锛?
```python
graph.add_edge("node_a", "node_b")
graph.add_edge("node_a", "node_c")  # b 鍜?c 骞惰鎵ц
```

**鍐茬獊鏉℃**锛毬?.2 瑕佹眰姣忔鍑虹珯蹇呴』鐢熸垚 `sanitization_report_ref` 涓?`payload_digest`锛堝啓鍏?`airlock_log`锛夛紝鍚﹀垯**闃绘柇澶栧彂**銆?
**鍏蜂綋椋庨櫓**锛?- 骞惰鍑虹綉鏃舵棤娉曚繚璇佹瘡涓垎鏀兘鏈夌嫭绔嬬殑 `sanitization_report_ref`
- 鏃犳硶淇濊瘉瀹¤鍐欏叆鍦ㄥ嚭缃戝墠瀹屾垚锛坮ace condition锛?- `payload_digest` 闇€瑕佸姣忎釜鍒嗘敮鐨?`prompt_context` 鍗曠嫭璁＄畻锛屽苟鍙戝満鏅笅璁＄畻椤哄簭涓嶅彲鎺?
**褰撳墠鏇夸唬鏂规**锛歚while` 寰幆寮哄埗涓茶锛屾瘡涓妭鐐规墽琛屽悗鍚屾鍐欏璁★紝褰诲簳瑙勯伩骞跺彂瀹¤闂銆?
---

### 鍐茬獊 3锛氬瓙鍥撅紙SubGraph锛塿s 搂6 瀹¤閾惧畬鏁存€?/ 鍙洖鏀炬€?
**LangGraph 瀛愬浘鍐欐硶**锛?
```python
subgraph = StateGraph(SubState)
# ... 瀛愬浘鍐呴儴閫昏緫
parent_graph.add_node("sub", subgraph.compile())
```

**鍐茬獊鏉℃**锛毬? 瑕佹眰姣忔璋冪敤蹇呴』鍐欏叆锛?
```
model_profile_id / node_id / provider / model_id
prompt_profile_id锛堜粎瀛樺紩鐢?鍝堝笇锛?input_digest / output_digest
tool_visibility_hash
sanitization_report_ref
```

**鍏蜂綋椋庨櫓**锛?- 瀛愬浘鍐呴儴鑺傜偣瀵圭埗鍥炬槸**榛戠洅**锛宍graph_trace` 鏃犳硶鎹曡幏瀛愬浘鍐呴儴姣忎釜鑺傜偣鐨勮皟鐢?- 瀹¤閾炬柇瑁傦細瀛愬浘鍐呴儴鑺傜偣涓嶅啓 `llm_call_log`
- 鏃犳硶鍥炴斁锛歚replay_ref` 鍙兘瀹氫綅鍒板瓙鍥捐妭鐐癸紝涓嶈兘瀹氫綅鍒板瓙鍥惧唴閮ㄨ妭鐐?
**褰撳墠鏇夸唬鏂规**锛氭墍鏈夎妭鐐规墎骞冲寲鍦ㄥ悓涓€涓?`_run_single_step` 寰幆涓紝`trace` 鍒楄〃瀹屾暣璁板綍姣忎釜鑺傜偣鎵ц锛宍model_calls` 瀹屾暣璁板綍姣忔 LLM 璋冪敤銆?
---

### 鍐茬獊 4锛氭柇鐐圭画璺戯紙Checkpointer锛塿s 搂4.3 鏃堕棿椹卞姩闄嶇骇閾捐矾

**LangGraph Checkpointer 鍐欐硶**锛?
```python
graph.compile(checkpointer=MemorySaver())
# interrupt_before=["node_x"] 鏆傚仠锛屼汉宸ヤ粙鍏ュ悗缁х画
```

**鍐茬獊鏉℃**锛毬?.3 闄嶇骇閾捐矾涓烘椂闂撮┍鍔細

```
t0: Host 绔嬪嵆杈撳嚭"鍦ㄥ満鍥炲簲妯℃澘"
t1: core_reasoner_primary 瓒呮椂 鈫?core_reasoner_secondary
t2: 浠嶅け璐?鈫?host_fallback_policy
```

**鍏蜂綋椋庨櫓**锛?- LangGraph checkpointer 鏄?*鐘舵€侀┍鍔?*锛堢瓑寰呬汉宸ヤ粙鍏ユ垨澶栭儴浜嬩欢锛夛紝鑰岄檷绾ф槸**鏃堕棿椹卞姩**锛坄timeout_ms`锛?- checkpointer 鏆傚仠鏃讹紝`t1` 瓒呮椂璁℃椂鍣ㄥ凡缁忓け鏁?- 闄嶇骇鍐欏叆 `error_log/quality.stage` 闇€瑕佸湪 Host 渚х簿纭帶鍒讹紝checkpointer 鐨勬仮澶嶆椂鏈轰笉鍙帶

**褰撳墠鏇夸唬鏂规**锛歚handle_timeout()` / `handle_dependency_unavailable()` / `pick_fallback_node()` 鍏ㄩ儴鍦?`_run_single_step` 鍐呴儴鍚屾澶勭悊锛岄檷绾ч€昏緫瀹屽叏鍦?Host 渚у彲鎺с€?
---

### 鍐茬獊 5锛氭祦寮忚緭鍑猴紙stream_mode锛塿s 搂4.2 浜屾寮忚緭鍑鸿竟鐣岄攣瀹?
**LangGraph 娴佸紡鍐欐硶**锛?
```python
for chunk in graph.stream(input, stream_mode="values"):
    yield chunk  # 閫?token 鎺ㄩ€?```

**鍐茬獊鏉℃**锛毬?.2 寮哄埗浜屾寮忚緭鍑猴細

```
S1: core_reasoner_primary 鈫?safety_verifier 鈫?Host QA/Governor
S2: renderer_ja_c_style 鈫?Host RenderGuard
瑙勫垯锛歩ntent_draft 鍙兘鏉ヨ嚜 S1锛汼2 涓嶅緱鏂板/淇敼 intent_draft
```

**鍏蜂綋椋庨櫓**锛?- 娴佸紡杈撳嚭鎰忓懗鐫€ S1 鐨?`narrative_zh` 杩樻湭瀹屾垚鏃讹紝S2 鐨勬覆鏌撳櫒鍙兘宸插紑濮嬪伐浣?- `RenderGuard` 鏃犳硶鏍￠獙"S2 鏄惁鍔犳枡"锛圫1 杈撳嚭涓嶅畬鏁存椂鏃犳硶寤虹珛鍩哄噯锛?- `intent_draft` 杈圭晫鏃犳硶閿佸畾锛圫1 娴佸紡杈撳嚭涓€?S2 鍙兘璇诲埌涓嶅畬鏁寸殑 `intent_draft`锛?
**褰撳墠鏇夸唬鏂规**锛氫弗鏍间覆琛岋紝S1 瀹屾暣杈撳嚭鍚庢墠杩涘叆 S2锛宍edge_map` 淇濊瘉椤哄簭锛宍RenderGuard` 鍦?S1 瀹屾垚鍚庡 `intent_draft` 鍔犻攣銆?
---

## 鍥涖€佸啿绐佺煩闃垫€昏

| LangGraph 鑳藉姏 | 琚樆鏂殑绾︽潫鏉℃ | 鍐茬獊绫诲瀷 | 褰撳墠鏇夸唬鏂规 |
|---------------|----------------|---------|------------|
| 鏉′欢杈癸紙LLM 椹卞姩璺敱锛?| 搂0.1 涓绘潈绾︽潫 | 鍐崇瓥鏉冨綊灞炲啿绐?| `edge_map` 瀛楀吀锛圚ost 闈欐€佸畾涔夛級 |
| 骞惰鍒嗘敮锛坒an-out锛?| 搂0.2 AIRLOCK 涓茶瀹¤ | 骞跺彂瀹夊叏鍐茬獊 | `while` 寰幆寮哄埗涓茶 |
| 瀛愬浘锛圫ubGraph锛?| 搂6 瀹¤閾惧畬鏁存€?鍙洖鏀炬€?| 鍙娴嬫€у啿绐?| 鎵佸钩鍖?`trace` 鍒楄〃 |
| 鏂偣缁窇锛圕heckpointer锛?| 搂4.3 鏃堕棿椹卞姩闄嶇骇閾捐矾 | 瑙﹀彂鏈哄埗鍐茬獊 | 鍚屾 `handle_timeout()` |
| 娴佸紡杈撳嚭锛坰tream_mode锛?| 搂4.2 浜屾寮忚緭鍑鸿竟鐣岄攣瀹?| 杈撳嚭杈圭晫鍐茬獊 | 涓ユ牸涓茶 S1鈫扴2 |

---

## 浜斻€佹灦鏋勫喅绛栫粨璁?
### 5.1 褰撳墠鏋舵瀯鐨勬湰璐?
LangGraph 鍦ㄥ綋鍓嶇郴缁熶腑琚?*涓诲姩闄嶇骇浣跨敤**锛?
- **淇濈暀**锛歚StateGraph` 鐘舵€佷紶鎾€乣compile()` 鍙娴嬩笂涓嬫枃銆乣RunnableLambda` 鑺傜偣灏佽
- **鏀惧純**锛氭潯浠惰竟銆佸苟琛屽垎鏀€佸瓙鍥俱€佹柇鐐圭画璺戙€佹祦寮忚緭鍑?
### 5.2 杩欐槸鎶€鏈€哄悧锛?
**鍚?*銆傝繖鏄不鐞嗕紭鍏堜簬妗嗘灦鑳藉姏鐨勬湁鎰忓彇鑸嶏細

> LangGraph 鐨勬瘡涓€椤归珮绾ц兘鍔涢兘涓?ModelPlane.md 鐨勬煇鏉＄‖绾︽潫瀛樺湪缁撴瀯鎬у啿绐併€?> 鍦?AI 娌荤悊鍚堣瑕佹眰涓嬶紝妗嗘灦鑳藉姏蹇呴』鏈嶄粠绾︽潫浣撶郴锛岃€岄潪鍙嶈繃鏉ャ€?
### 5.3 鏈潵鎵╁睍璺緞

鑻ユ湭鏉ラ渶瑕佸紩鍏?LangGraph 楂樼骇鑳藉姏锛岄渶鍏堝湪 ModelPlane.md 涓负瀵瑰簲绾︽潫鏉℃澧炲姞璞佸厤鏉′欢锛屽苟閫氳繃瀹屾暣鐨勫畨鍏ㄨ瘎瀹★紝鏂瑰彲瑙ｉ攣瀵瑰簲鑳藉姏銆?
---

## 鍏€佺浉鍏虫枃浠剁储寮?
| 鏂囦欢 | 璇存槑 |
|------|------|
| `docs/30_devspec/ModelPlane.md` | 鏍稿績绾︽潫瑙勬牸鏂囨。 |
| `host_brain/integrations/graph_executor.py` | LangGraph 鍥剧紪璇戜笌鎵ц |
| `host_brain/integrations/graph_runtime.py` | 杩愯鏃跺悎绾﹀眰 |
| `docs/60_trace/HOST_CHAT_REAL_CHAIN_TRACE_req_bc56917feac740b4adea1486d88a6435.md` | 鐪熷疄璋冪敤閾捐矾 Trace |
| `docs/60_trace/HOST_CHAT_CHAIN_REAL_REPORT_2026-03-06.md` | 鑺傜偣缂栨帓闂鍒嗘瀽鎶ュ憡 |

