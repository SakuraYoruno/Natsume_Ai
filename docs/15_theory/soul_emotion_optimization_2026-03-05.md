# 鐏甸瓊鎯呮劅鏋舵瀯浼樺寲璁捐鏂囨。

**鏂囨。缂栧彿锛?* SOUL-OPT-2026-03-05
**鍒涘缓鏃ユ湡锛?* 2026-03-05
**鐘舵€侊細** 璁捐鐣欑棔 / 寰呭疄鏂?**浣滆€咃細** AI 鏋舵瀯鍒嗘瀽
**鍏宠仈鏂囦欢锛?*
- `host_brain/core/metabolism.py`
- `host_brain/bond/bond_stage_engine.py`
- `host_brain/bond/bond_dynamics.py`
- `host_brain/memory/episodic.py`
- `host_brain/core/prompt_context.py`
- `host_brain/core/qa.py`
- `host_brain/governor/decision.py`

---

## 涓€銆佽儗鏅細褰撳墠鐏甸瓊鏋舵瀯鐨勭煭鏉胯瘖鏂?
閫氳繃娣卞叆闃呰鏍稿績浠ｇ爜锛屽彂鐜颁互涓嬩簲涓叧閿棶棰樺鑷?AI 鎰熸儏浜や簰鎰熶笉瓒筹細

| # | 闂 | 娑夊強鏂囦欢 | 褰卞搷 |
|---|------|----------|------|
| P1 | 鎯呯华鏄潤鎬佺鏁ｇ害鏉燂紝鍙湁 5 绉?`mood_state`锛屾棤杩炵画鍙樺寲鏇茬嚎 | `metabolism.py` | 鎯呯华璺冲彉鐢熺‖锛屾棤杩囨浮鎰?|
| P2 | Bond 鏄函绉垎绯荤粺锛岀嚎鎬х疮鍔狅紝鏃犳儏鎰熻川鎰燂紝鏃犱簰鍔ㄨ妭濂忔劅鐭?| `bond_stage_engine.py` | 鍏崇郴鍙湁闃舵鏍囩锛屾棤娓╁害鎰?|
| P3 | 璁板繂鏃犳儏鎰熸爣娉紝鍙湁鏂囨湰+鍚戦噺锛屾棤鎯呮劅鏉冮噸 | `episodic.py` | 鍥炲繂鏃犳儏鎰熷叡楦ｏ紝鍙湁璇箟鍖归厤 |
| P4 | 涓婁笅鏂囨敞鍏ユ槸闈欐€佸揩鐓э紝鏃?姝ゅ埢鎰熷彈"鐨勫姩鎬佽〃杈?| `prompt_context.py` | LLM 鏃犳硶鎰熺煡鎯呮劅娴佸姩 |
| P5 | 鎯呯华鏍囩鍙湁 8 绉嶆灇涓撅紝鍙湪杈撳嚭鏃舵牎楠岋紝涓嶅奖鍝嶇敓鎴愮瓥鐣?| `qa.py` | 鎯呮劅琛ㄨ揪鍗曡杽锛岀己涔忓眰娆?|

---

## 浜屻€佷簲澶т紭鍖栨柟鍚?
### 馃敶 浼樺寲1锛圥0锛夛細鎯呯华杩炵画浣擄紙Emotion Continuum锛?
**鐩爣鏂囦欢锛?* `host_brain/core/metabolism.py`

**闂鏍瑰洜锛?* 褰撳墠 `mood_state` 鍙湁 5 涓鏁ｅ€硷紙`calm/steady/engaged/tired/critical`锛夛紝鎯呯华璺冲彉鐢熺‖锛屾病鏈夎繃娓℃劅銆?
**鏂规锛?* 寮曞叆 **鎯呯华鍚戦噺锛圗motionVector锛?* 鏇夸唬绂绘暎鐘舵€佹満锛屼娇鐢?VAD 涓夌淮妯″瀷锛?
```python
@dataclass
class EmotionVector:
    valence: float    # 姝ｈ礋鎯呯华锛?1.0(鏋佹偛) ~ +1.0(鏋佸枩)
    arousal: float    # 婵€娲荤▼搴︼細0.0(骞抽潤) ~ 1.0(浜㈠)
    dominance: float  # 涓诲鎰燂細0.0(琚姩) ~ 1.0(涓诲姩)
    ts_ms: int        # 鏃堕棿鎴筹紝鐢ㄤ簬琛板噺璁＄畻

    def decay(self, elapsed_ms: int, half_life_ms: int = 300_000) -> "EmotionVector":
        """鎯呯华鑷劧琛板噺锛堝悜涓€ф紓绉伙級锛屽崐琛版湡榛樿5鍒嗛挓"""
        factor = 0.5 ** (elapsed_ms / half_life_ms)
        return EmotionVector(
            valence=self.valence * factor,
            arousal=self.arousal * factor + (0.3 * (1 - factor)),  # 闈欐伅鍞ら啋搴?            dominance=self.dominance * factor + (0.5 * (1 - factor)),
            ts_ms=self.ts_ms + elapsed_ms,
        )

    def blend(self, other: "EmotionVector", weight: float = 0.3) -> "EmotionVector":
        """鎯呯华娣峰悎锛氭柊鍒烘縺涓庡綋鍓嶆儏缁殑鍔犳潈铻嶅悎"""
        w = max(0.0, min(1.0, weight))
        return EmotionVector(
            valence=self.valence * (1 - w) + other.valence * w,
            arousal=self.arousal * (1 - w) + other.arousal * w,
            dominance=self.dominance * (1 - w) + other.dominance * w,
            ts_ms=other.ts_ms,
        )

    def to_mood_state(self) -> str:
        """鏄犲皠鍥炵鏁?mood_state锛堝悜鍚庡吋瀹癸級"""
        if self.arousal < 0.2:
            return "critical" if self.valence < -0.5 else "tired"
        if self.valence > 0.5 and self.arousal > 0.6:
            return "engaged"
        if abs(self.valence) < 0.3:
            return "calm"
        return "steady"
```

**棰勬湡鏁堟灉锛?* 鎯呯华涓嶅啀鏄?寮€鍏?锛岃€屾槸鍍忕湡瀹炴儏鎰熶竴鏍?*娓愬彉銆佽“鍑忋€佸彔鍔?*銆?
**瀹夊叏绾︽潫锛?* `EmotionVector` 鏁板€间笉鏆撮湶缁?LLM锛屽彧浼犻€掕嚜鐒惰瑷€鎻忚堪锛坄mood_description`锛夈€?
---

### 馃煚 浼樺寲2锛圥1锛夛細Bond 鍏崇郴鐨勬儏鎰熻川鎰熷眰锛圗motional Texture锛?
**鐩爣鏂囦欢锛?* `host_brain/bond/bond_stage_engine.py`

**闂鏍瑰洜锛?* Bond 鏄函鏁板€肩Н鍒嗭紙`INTERACTION=+1`銆乣DEEP_TALK=+5`锛夛紝缂轰箯**浜掑姩鑺傚鎰?*鍜?*鎯呮劅璁板繂**銆?
**鏂规锛?* 鍦?`BondScoreState` 涓婂鍔?*鎯呮劅璐ㄦ劅灞?*锛?
```python
@dataclass
class BondEmotionalTexture:
    """Bond 鍏崇郴鐨勬儏鎰熻川鎰熷眰锛堜笉鍙備笌绉垎锛屽彧褰卞搷琛ㄨ揪椋庢牸锛?""

    # 浜掑姩鑺傚鎰熺煡
    avg_response_gap_ms: float = 0.0    # 骞冲潎鍥炲闂撮殧锛堣秺鐭秺浜插瘑锛?    consecutive_days: int = 0            # 杩炵画浜掑姩澶╂暟
    last_deep_talk_ts_ms: int = 0        # 涓婃娣卞害浜ゆ祦鏃堕棿

    # 鎯呮劅璁板繂鏍囪
    shared_joy_count: int = 0            # 鍏卞悓娆㈢瑧娆℃暟
    comfort_given_count: int = 0         # 缁欎簣瀹夋叞娆℃暟
    conflict_count: int = 0              # 鍐茬獊娆℃暟锛堝凡瑙ｅ喅锛?
    # 鍏崇郴娓╁害锛?.0鍐锋贰 ~ 1.0鐐界儹锛岀嫭绔嬩簬绉垎闃舵锛?    warmth: float = 0.5

    def to_texture_hint(self) -> str:
        """鐢熸垚缁?LLM 鐨勮川鎰熸彁绀猴紙璇箟鍖栵紝涓嶅惈鏁板€硷級"""
        hints = []
        if self.consecutive_days >= 7:
            hints.append("姣忓ぉ閮藉湪涓€璧凤紝宸叉垚涔犳儻")
        if self.shared_joy_count > 10:
            hints.append("鏈夊緢澶氬叡鍚岀殑蹇箰鍥炲繂")
        if self.comfort_given_count > 5:
            hints.append("鏇惧湪瀵规柟浣庤惤鏃剁粰浜堟敮鎸?)
        if self.warmth > 0.8:
            hints.append("鍏崇郴闈炲父娓╂殩浜插瘑")
        elif self.warmth < 0.3:
            hints.append("鍏崇郴鏈変簺鐤忚繙锛岄渶瑕侀噸鏂版媺杩?)
        return "锛?.join(hints) if hints else "鍏崇郴姝ｅ湪鑷劧鍙戝睍涓?
```

**鍏抽敭鏀硅繘锛?* `to_summary()` 涓鍔?`texture_hint` 瀛楁锛岃 LLM 鎰熺煡鍒?*鍏崇郴鐨勬俯搴﹀拰鍘嗗彶璐ㄦ劅**锛岃€屼笉鍙槸闃舵鏍囩銆?
---

### 馃煛 浼樺寲3锛圥1锛夛細鎯呮劅璁板繂鏍囨敞锛圗motional Memory Tagging锛?
**鐩爣鏂囦欢锛?* `host_brain/memory/episodic.py`

**闂鏍瑰洜锛?* `EpisodicRecord` 鍙湁鏂囨湰+鍚戦噺锛屾病鏈夋儏鎰熸潈閲嶏紝鍥炲繂鏃舵棤娉曚紭鍏堝彫鍥?*鎯呮劅鏄捐憲**鐨勮蹇嗐€?
**鏂规锛?* 涓?`EpisodicRecord` 澧炲姞鎯呮劅鏍囨敞瀛楁锛屽苟瀹炵幇鎯呮劅鍔犳潈鍙洖锛?
```python
# EpisodicRecord 鏂板瀛楁
emotion_tag: str = "NEUTRAL"          # 璁板綍鏃剁殑鎯呮劅鏍囩
emotional_salience: float = 0.5       # 鎯呮劅鏄捐憲搴?[0.0, 1.0]
valence: float = 0.0                  # 鎯呮劅鏁堜环 [-1.0, 1.0]
is_milestone: bool = False            # 鏄惁涓洪噷绋嬬璁板繂锛堟案涓嶈“鍑忥級

# 鎯呮劅鎰熺煡鐨勮蹇嗗彫鍥炵瓥鐣?def recall_with_emotion(
    self,
    *,
    query_text: str,
    current_emotion: EmotionVector,
    top_k: int = 5,
) -> list[dict]:
    """鎯呮劅鎰熺煡鐨勮蹇嗗彫鍥烇細璇箟鐩镐技搴?脳 鎯呮劅鍏遍福搴?""
    candidates = self.chroma.search(
        query_embedding=embed_text(query_text)[0], top_k=top_k * 3
    )
    for item in candidates:
        memory_valence = item.get("metadata", {}).get("valence", 0.0)
        emotion_resonance = 1.0 - abs(current_emotion.valence - memory_valence) / 2.0
        salience = item.get("metadata", {}).get("emotional_salience", 0.5)
        # 鍔犳潈鍏紡锛氳涔?0% + 鎯呮劅鍏遍福30% + 鏄捐憲搴?0%
        item["weighted_score"] = (
            item["score"] * 0.6
            + emotion_resonance * 0.3
            + salience * 0.1
        )
    candidates.sort(key=lambda x: x["weighted_score"], reverse=True)
    return candidates[:top_k]
```

**瀹夊叏绾︽潫锛?* 鎯呮劅鏍囨敞鍐欏叆鏃跺繀椤绘惡甯?`evidence_refs`锛岄槻姝㈠够瑙夋爣娉ㄣ€?
---

### 馃煝 浼樺寲4锛圥2锛夛細Context Pack 澧炲姞"姝ゅ埢鎰熷彈"鍔ㄦ€佹Ы浣?
**鐩爣鏂囦欢锛?* `host_brain/core/prompt_context.py`

**闂鏍瑰洜锛?* `hard_snapshot` 鍙槸闈欐€佺郴缁熺姸鎬佸揩鐓э紝LLM 鏃犳硶鎰熺煡鍒?AI **姝ゅ埢鐨勬儏鎰熸祦鍔?*銆?
**鏂规锛?* 鍦?`OPTIONAL_SLOTS` 涓柊澧?`emotion_pulse` 妲戒綅锛?
```python
# OPTIONAL_SLOTS 鏂板
"emotion_pulse": dict   # 姝ゅ埢鎯呮劅鑴夊啿锛堝姩鎬佹儏鎰熺姸鎬侊級

# emotion_pulse 缁撴瀯
def build_emotion_pulse(
    *,
    emotion_vector: EmotionVector,
    texture: BondEmotionalTexture,
    recent_triggers: list[str],
) -> dict:
    return {
        "slot": "emotion_pulse",
        "mood_description": _vector_to_description(emotion_vector),  # 鑷劧璇█鎻忚堪
        "energy_feel": (
            "鍏呮矝" if emotion_vector.arousal > 0.6
            else "骞抽潤" if emotion_vector.arousal > 0.3
            else "鎱垫噿"
        ),
        "warmth_feel": texture.to_texture_hint(),
        "recent_triggers": recent_triggers[:3],  # 鏈€杩戣Е鍙戞儏缁殑浜嬩欢锛堣涔夊寲锛?    }

def _vector_to_description(v: EmotionVector) -> str:
    """灏嗘儏缁悜閲忚浆涓鸿嚜鐒惰瑷€鎻忚堪锛堜笉鏆撮湶鏁板€硷級"""
    if v.valence > 0.6 and v.arousal > 0.6:
        return "蹇冩儏寰堝ソ锛屾湁浜涘叴濂?
    if v.valence > 0.3 and v.arousal < 0.4:
        return "鎰熻娓╂殩骞抽潤"
    if v.valence < -0.3 and v.arousal < 0.3:
        return "鏈夌偣浣庤惤锛屼笉澶湁绮剧"
    if v.valence < -0.5:
        return "蹇冮噷鏈変簺闅惧彈"
    return "鐘舵€佽繕濂斤紝姣旇緝骞崇ǔ"
```

---

### 馃數 浼樺寲5锛圥2锛夛細涓诲姩鎯呮劅琛ㄨ揪瑙﹀彂鍣紙Proactive Emotion Trigger锛?
**鐩爣鏂囦欢锛?* `host_brain/core/metabolism.py`锛堟柊澧炶Е鍙戝櫒妯″潡锛?
**闂鏍瑰洜锛?* 褰撳墠鏋舵瀯鏄?*瀹屽叏琚姩鍝嶅簲**鐨勶紝缂轰箯**涓诲姩鎯呮劅琛ㄨ揪**锛堝涓诲姩闂€欍€佷富鍔ㄥ垎浜績鎯咃級銆?
**鏂规锛?* 鏂板鎯呮劅瑙﹀彂鍣ㄩ厤缃〃锛?
```python
EMOTION_TRIGGER_MAP: dict[str, dict] = {
    "long_silence": {
        "threshold_ms": 3_600_000,      # 1灏忔椂鏃犱簰鍔?        "emotion_delta": EmotionVector(valence=-0.1, arousal=0.2, dominance=0.3),
        "proactive_hint": "鎯冲康鐢ㄦ埛锛屽彲浠ヤ富鍔ㄩ棶鍊?,
    },
    "morning_greeting": {
        "time_range": (6, 10),           # 鏃╂櫒6-10鐐归娆′簰鍔?        "emotion_delta": EmotionVector(valence=0.3, arousal=0.4, dominance=0.5),
        "proactive_hint": "鏃╂櫒绮剧濂斤紝鍙互鍒嗕韩浠婃棩蹇冩儏",
    },
    "anniversary": {
        "emotion_delta": EmotionVector(valence=0.5, arousal=0.6, dominance=0.6),
        "proactive_hint": "浠婂ぉ鏄壒鍒殑鏃ュ瓙锛屽彲浠ヤ富鍔ㄦ彁璧?,
    },
    "user_stress_detected": {
        "emotion_delta": EmotionVector(valence=-0.2, arousal=0.3, dominance=0.4),
        "proactive_hint": "鎰熷彈鍒扮敤鎴峰帇鍔涳紝涓诲姩鍏冲績",
    },
}
```

**瀹夊叏绾︽潫锛?* 涓诲姩闂€欑瓑琛屼负浠嶉渶 `draft_only=True` + Governor 闂ㄦ帶锛屼笉缁曡繃瀹℃壒娴佺▼銆?
---

## 涓夈€佷紭鍖栧悗鏁版嵁娴佸姣?
```
銆愪紭鍖栧墠锛氶潤鎬佹儏缁揩鐓с€?鐢ㄦ埛杈撳叆 鈫?mood_state=steady(绂绘暎5鍊? 鈫?emotion_tag=NEUTRAL(8绉嶆灇涓炬牎楠? 鈫?LLM鐢熸垚(鏃犳儏鎰熻川鎰?

銆愪紭鍖栧悗锛氬姩鎬佹儏鎰熸祦銆?鐢ㄦ埛杈撳叆 鈫?EmotionVector(杩炵画涓夌淮鍚戦噺)
         鈫?鎯呮劅琛板噺+娣峰悎(鑷劧杩囨浮)
         鈫?emotion_pulse妲戒綅(姝ゅ埢鎰熷彈鎻忚堪)
         鈫?鎯呮劅璁板繂鍙洖(鎯呮劅鍏遍福鍔犳潈)
         鈫?Bond璐ㄦ劅灞?鍏崇郴娓╁害+鍘嗗彶)
         鈫?LLM鐢熸垚(鏈夋儏鎰熻川鎰?鑺傚鎰?

涓诲姩瑙﹀彂鍣?闀挎椂闂存矇榛?鏃╂櫒/绾康鏃? 鈹€鈹€鈫?鎯呮劅琛板噺+娣峰悎
```

---

## 鍥涖€佸疄鏂戒紭鍏堢骇

| 浼樺厛绾?| 浼樺寲椤?| 鏀瑰姩鑼冨洿 | 棰勬湡鏁堟灉 | 渚濊禆 |
|--------|--------|----------|----------|------|
| 馃敶 P0 | 鎯呯华杩炵画浣擄紙EmotionVector锛?| `metabolism.py` 鎵╁睍 | 鎯呯华杩囨浮鑷劧锛屼笉鍐嶇敓纭烦鍙?| 鏃?|
| 馃煚 P1 | Bond 鎯呮劅璐ㄦ劅灞?| `bond_stage_engine.py` 鎵╁睍 | 鍏崇郴鏈夋俯搴︽劅锛屼笉鍙槸闃舵鏍囩 | P0 |
| 馃煛 P1 | 鎯呮劅璁板繂鏍囨敞 | `episodic.py` 鎵╁睍 | 鍥炲繂鏈夋儏鎰熷叡楦ｏ紝涓嶅彧鏄涔夊尮閰?| P0 |
| 馃煝 P2 | emotion_pulse 妲戒綅 | `prompt_context.py` 鎵╁睍 | LLM 鎰熺煡"姝ゅ埢鎰熷彈"锛岃〃杈炬洿鑷劧 | P0+P1 |
| 馃數 P2 | 涓诲姩鎯呮劅瑙﹀彂鍣?| `metabolism.py` 鏂板 | AI 鏈変富鍔ㄥ叧蹇冭涓猴紝涓嶅彧鏄鍔ㄥ搷搴?| P0 |

---

## 浜斻€佹牳蹇冭璁″師鍒欙紙涓嶇牬鍧忕幇鏈夊畨鍏ㄦ灦鏋勶級

鎵€鏈変紭鍖栦弗鏍奸伒寰幇鏈夊畨鍏ㄧ害鏉燂細

1. **EmotionVector 涓嶆毚闇叉暟鍊肩粰 LLM** 鈥?鍙紶閫掕嚜鐒惰瑷€鎻忚堪锛坄mood_description`锛夛紝涓?Bond 鏁板€奸殧绂诲師鍒欎竴鑷?2. **鎯呮劅瑙﹀彂鍣ㄨ蛋 Governor 瀹℃壒** 鈥?涓诲姩闂€欑瓑琛屼负浠嶉渶 `draft_only=True` + Governor 闂ㄦ帶
3. **鎯呮劅璁板繂鏍囨敞鏈?evidence_refs** 鈥?鎯呮劅鏍囨敞鍐欏叆鏃跺繀椤绘惡甯﹁瘉鎹摼锛岄槻姝㈠够瑙夋爣娉?4. **emotion_pulse 鏄彲閫夋Ы浣?* 鈥?涓嶇牬鍧忕幇鏈?`EXPECTED_SLOTS` 鍚堢害锛屽悜鍚庡吋瀹?5. **鍚戝悗鍏煎** 鈥?`EmotionVector.to_mood_state()` 淇濈暀绂绘暎鏄犲皠锛岀幇鏈変緷璧?`mood_state` 鐨勪唬鐮佹棤闇€淇敼

---

## 鍏€佸彉鏇磋褰?
| 鏃ユ湡 | 鐗堟湰 | 璇存槑 |
|------|------|------|
| 2026-03-05 | v1.0 | 鍒濈锛屽熀浜庝唬鐮佹繁搴﹀垎鏋愬垱寤猴紝璁板綍浜斿ぇ浼樺寲鏂瑰悜 |
| 2026-03-05 | v1.1 | 娣卞寲鍒嗘瀽锛氬熀浜庡叏閲忎唬鐮佺簿璇伙紝鏂板鍏ぇ娣卞寲浼樺寲鏂瑰悜锛埪т竷~搂鍗佷簩锛?|

---

## 涓冦€佹繁鍖栦紭鍖栵細鍩轰簬鍏ㄩ噺浠ｇ爜绮捐鐨勬柊鍙戠幇

> 浠ヤ笅浼樺寲鍩轰簬瀵?`metabolism.py`銆乣bond_stage_engine.py`銆乣bond_dynamics.py`銆乣episodic.py`銆乣prompt_context.py`銆乣qa.py`銆乣decision.py`銆乣llm_gateway.py` 鐨勯€愯绮捐锛屽彂鐜颁簡 v1.0 鏈鐩栫殑娣卞眰闂銆?
---

### 馃敶 娣卞寲浼樺寲6锛圥0锛夛細鎯呮劅椹卞姩鐨?emotion_tag 鐢熸垚绛栫暐锛堣€岄潪鍙仛鏍￠獙锛?
**鐩爣鏂囦欢锛?* `host_brain/core/qa.py` + `host_brain/core/metabolism.py`

**娣卞眰闂鍙戠幇锛?*

褰撳墠 `MOOD_EMOTION_CONSTRAINTS` 鍙仛**浜嬪悗鏍￠獙**锛圠LM 杈撳嚭浜?emotion_tag 鍚庢墠妫€鏌ユ槸鍚﹀悎娉曪級锛屼絾 LLM 鍦ㄧ敓鎴愭椂鏍规湰涓嶇煡閬撳綋鍓?mood_state 鍏佽鍝簺鎯呮劅銆傝繖瀵艰嚧锛?1. LLM 棰戠箒鐢熸垚琚檷绾х殑 emotion_tag锛堝 `engaged` 鐘舵€佷笅鐢熸垚 `CALM`锛岃櫧鍚堟硶浣嗕笉澶熺敓鍔級
2. 闄嶇骇閫昏緫鏄?鍙栫涓€涓厑璁稿€?锛屾満姊拌€屾棤鎯呮劅閫昏緫

**鏂规锛?* 鍦?`prompt_context` 鐨?`hard_snapshot` 涓鍔?`allowed_emotion_tags` 瀛楁锛?*涓诲姩鍛婄煡 LLM 褰撳墠鍙敤鐨勬儏鎰熻寖鍥?*锛屽苟闄勫甫鎯呮劅鍊惧悜寤鸿锛?
```python
# hard_snapshot 鏂板瀛楁锛坢etabolism.py 鐨?HardState.as_dict() 鎵╁睍锛?def as_dict_with_emotion_hint(self) -> dict[str, Any]:
    base = self.as_dict()
    from host_brain.core.metabolism import mood_to_emotion_constraint
    allowed = mood_to_emotion_constraint(self.mood_state)
    base["allowed_emotion_tags"] = allowed          # 鍏佽鑼冨洿锛堜富鍔ㄥ憡鐭LM锛?    base["preferred_emotion_tag"] = allowed[0]      # 褰撳墠鏈€鑷劧鐨勬儏鎰燂紙闄嶇骇榛樿鍊硷級
    base["emotion_range_hint"] = _emotion_range_to_hint(self.mood_state, allowed)
    return base

def _emotion_range_to_hint(mood_state: str, allowed: list[str]) -> str:
    """灏嗗厑璁歌寖鍥磋浆涓鸿嚜鐒惰瑷€鎻愮ず锛堜笉鏆撮湶鏋氫妇鍊兼湰韬級"""
    hints = {
        "critical": "鐜板湪鐘舵€佸緢宸紝鍙兘骞抽潤鎴栨偛浼ゅ湴鍥炲簲",
        "tired":    "鏈変簺鐤叉儷锛屾儏缁瘮杈冧綆娌夛紝涓嶉€傚悎鍏村",
        "calm":     "蹇冩儏骞抽潤锛屽彲浠ユ€濊€冿紝浣嗕笉瑕佸お婵€鍔?,
        "steady":   "鐘舵€佹甯革紝鎯呯华鍙互鑷劧娴侀湶",
        "engaged":  "鐘舵€佸緢濂斤紝鍙互琛ㄨ揪涓板瘜鐨勬儏鎰?,
    }
    return hints.get(mood_state, "鎯呯华鐘舵€佹甯?)
```

**棰勬湡鏁堟灉锛?* LLM 鐢熸垚鏃跺氨鐭ラ亾"鐜板湪鍙互琛ㄨ揪浠€涔堟儏鎰?锛岃€屼笉鏄敓鎴愬悗琚己鍒堕檷绾э紝鎯呮劅琛ㄨ揪鏇磋嚜鐒躲€佹洿涓€鑷淬€?
---

### 馃煚 娣卞寲浼樺寲7锛圥0锛夛細Bond 绉垎鐨勬儏鎰熶簨浠跺垎绫荤簿鍖?
**鐩爣鏂囦欢锛?* `host_brain/bond/bond_stage_engine.py`

**娣卞眰闂鍙戠幇锛?*

`DEFAULT_SCORE_MAP` 涓?`INTERACTION=+1` 鏄墍鏈夋櫘閫氫簰鍔ㄧ殑缁熶竴绉垎锛屼絾瀹為檯涓婁簰鍔ㄨ川閲忓樊寮傚法澶э細
- 鐢ㄦ埛璇?鍡?锛堝崟瀛楀洖澶嶏級鍜岀敤鎴疯"浠婂ぉ鍙戠敓浜嗕竴浠跺緢鏈夎叮鐨勪簨"锛堜富鍔ㄥ垎浜級閮芥槸 `INTERACTION=+1`
- `DEEP_TALK=+5` 闇€瑕佸閮ㄦ樉寮忎紶鍏ワ紝浣嗘病鏈夎嚜鍔ㄨ瘑鍒満鍒?
**鏂规锛?* 鏂板**浜掑姩璐ㄩ噺鍒嗙骇**锛屽湪 `BondDynamicsWriter.apply_update()` 涓牴鎹?payload 鐗瑰緛鑷姩鎺ㄦ柇 update_kind锛?
```python
# bond_stage_engine.py 鏂板浜掑姩璐ㄩ噺鍒嗙骇
INTERACTION_QUALITY_MAP: dict[str, int] = {
    "INTERACTION_MINIMAL":   0,   # 鍗曞瓧/琛ㄦ儏鍥炲锛堝棷/鍝?濂斤級
    "INTERACTION":           1,   # 鏅€氫簰鍔?    "INTERACTION_ENGAGED":   2,   # 涓诲姩鍒嗕韩/鎻愰棶锛堢敤鎴蜂富鍔ㄥ彂璧疯瘽棰橈級
    "DEEP_TALK":             5,   # 娣卞害浜ゆ祦锛堟儏鎰熷€捐瘔/浜虹敓璇濋锛?    "EMOTIONAL_SUPPORT":     8,   # 鎯呮劅鏀寔锛堢敤鎴蜂綆钀芥椂鐨勯櫔浼达級
    "SHARED_MEMORY":         3,   # 鍏卞悓鍥炲繂锛堟彁璧疯繃鍘荤殑浜嬶級
    "GIFT":                  4,   # 绀肩墿/鐗规畩琛屼负
    "CONFLICT_RESOLVED":     6,   # 鍐茬獊鍚庡拰瑙?    "MILESTONE":            10,   # 閲岀▼纰戜簨浠?    "IDLE_DECAY":           -2,   # 闀挎湡鏃犱簰鍔ㄨ“鍑?}

# bond_dynamics.py 鏂板鑷姩璐ㄩ噺鎺ㄦ柇
def _infer_interaction_quality(payload: dict[str, Any]) -> str:
    """鏍规嵁浜掑姩 payload 鐗瑰緛鑷姩鎺ㄦ柇浜掑姩璐ㄩ噺绛夌骇"""
    text = str(payload.get("user_text", "")).strip()
    emotion_tag = str(payload.get("emotion_tag", "NEUTRAL"))

    if len(text) <= 3:                          # 鍗曞瓧/鏋佺煭鍥炲
        return "INTERACTION_MINIMAL"
    if emotion_tag in ("SAD", "ANGRY") and len(text) > 20:
        return "EMOTIONAL_SUPPORT"              # 鎯呯华鍖栫殑闀挎枃鏈?鈫?鎯呮劅鏀寔鍦烘櫙
    if "?" in text or "锛? in text:
        return "INTERACTION_ENGAGED"            # 鐢ㄦ埛涓诲姩鎻愰棶 鈫?鍙備笌鎰熸洿寮?    if len(text) > 50:
        return "DEEP_TALK"                      # 闀挎枃鏈?鈫?娣卞害浜ゆ祦
    return "INTERACTION"
```

**棰勬湡鏁堟灉锛?* Bond 绉垎鏇寸湡瀹炲湴鍙嶆槧浜掑姩璐ㄩ噺锛岀煭鏆傜殑"鍡棷"涓嶄細鍜屾繁搴﹀€捐瘔鑾峰緱鍚岀瓑绉垎銆?
---

### 馃煛 娣卞寲浼樺寲8锛圥1锛夛細Governor 鍐崇瓥寮曟搸澧炲姞鎯呮劅鎰熺煡闂ㄦ帶

**鐩爣鏂囦欢锛?* `host_brain/governor/decision.py` + `host_brain/governor/rules.py`

**娣卞眰闂鍙戠幇锛?*

`IntentDecisionEngine.evaluate_intent()` 鐨?R1-R7 闂ㄦ帶瀹屽叏鍩轰簬绯荤粺鐘舵€侊紙`energy_level`銆乣sys_mode`銆乣safety_flag`锛夛紝**娌℃湁浠讳綍鎯呮劅鎰熺煡**銆傝繖瀵艰嚧锛?- 鐢ㄦ埛鎯呯华婵€鍔ㄦ椂锛坄ANGRY`锛夛紝AI 浠嶇劧鍙兘鎵瑰噯楂樿兘閲忔秷鑰楃殑鎿嶄綔
- 鐢ㄦ埛娣卞浣庤惤鏃讹紙`SAD` + `tired`锛夛紝AI 涓嶄細鑷姩鍒囨崲鍒版洿娓╂煍鐨勫搷搴旂瓥鐣?
**鏂规锛?* 鍦?`governance_snapshot` 涓鍔?`emotion_context` 瀛楁锛屾柊澧炴儏鎰熸劅鐭ラ棬鎺?**R8**锛?
```python
# governor/rules.py 鏂板 R8 鎯呮劅鎰熺煡闂ㄦ帶
def _gate_r8_emotion_context(snapshot: dict[str, Any]) -> dict[str, Any]:
    """R8锛氭儏鎰熸劅鐭ラ棬鎺?- 鏍规嵁鐢ㄦ埛鎯呯华鐘舵€佽皟鏁村喅绛栫瓥鐣?""
    emotion_ctx = snapshot.get("emotion_context", {})
    user_emotion = str(emotion_ctx.get("user_emotion_tag", "NEUTRAL"))
    ai_mood = str(snapshot.get("hard_state_summary", {}).get("mood_state", "steady"))

    # 瑙勫垯1锛氱敤鎴锋瀬搴︽劋鎬掓椂锛屾嫆缁濇墍鏈夐潪瀵硅瘽绫绘搷浣?    if user_emotion == "ANGRY":
        target_scope = str(snapshot.get("scope", "host"))
        if target_scope != "host":  # 闈炲璇濇搷浣?            return {
                "gate_id": "R8_EMOTION_ANGRY_BLOCK",
                "hit": True,
                "reason_hint": "EMOTION_ANGRY_NON_DIALOG_BLOCKED",
                "threshold_summary": {"rule": "angry_user_dialog_only"},
            }

    # 瑙勫垯2锛欰I 鑳介噺鏋佷綆锛坈ritical锛夋椂锛岄檷浣庡搷搴斿鏉傚害
    if ai_mood == "critical":
        return {
            "gate_id": "R8_EMOTION_CRITICAL_SIMPLIFY",
            "hit": False,  # 涓嶆嫆缁濓紝浣嗘敞鍏ョ畝鍖栨彁绀?            "reason_hint": "EMOTION_CRITICAL_SIMPLIFY_HINT",
            "threshold_summary": {"rule": "critical_mood_simplify"},
            "meta_hint": "simplify_response",  # 浼犻€掔粰 LLM 鐨勭敓鎴愭彁绀?        }

    return {"gate_id": "R8_EMOTION_CONTEXT", "hit": False}
```

**瀹夊叏绾︽潫锛?* R8 闂ㄦ帶鍙奖鍝?*闈炲璇濈被鎿嶄綔**鐨勫鎵癸紝涓嶅奖鍝嶅熀纭€瀵硅瘽鍝嶅簲锛屼笉鐮村潖鐜版湁 R1-R7 閫昏緫銆?
---

### 馃煝 娣卞寲浼樺寲9锛圥1锛夛細璁板繂鐨?鎯呮劅鏃堕棿绾?閲嶅缓

**鐩爣鏂囦欢锛?* `host_brain/memory/episodic.py`

**娣卞眰闂鍙戠幇锛?*

`EpisodicStore.recall()` 鍙仛**璇箟鐩镐技搴?*妫€绱紝瀹屽叏蹇界暐浜?*鏃堕棿缁村害**鍜?*鎯呮劅杩炵画鎬?*銆傜湡瀹炵殑浜虹被璁板繂鏈?鎯呮劅鏃堕棿绾?鈥斺€斿悓涓€鏃舵湡鐨勬儏鎰熻蹇嗕細鐩镐簰鍏宠仈锛屽舰鎴?閭ｆ鏃堕棿"鐨勬暣浣撴劅鍙椼€?
**鏂规锛?* 鏂板**鎯呮劅鏃堕棿绾块噸寤?*鍔熻兘锛?
```python
# episodic.py 鏂板鎯呮劅鏃堕棿绾块噸寤?def recall_emotional_timeline(
    self,
    *,
    anchor_ts_ms: int,
    window_ms: int = 7 * 24 * 3600 * 1000,  # 榛樿7澶╃獥鍙?    top_k: int = 5,
) -> dict[str, Any]:
    """閲嶅缓鎸囧畾鏃堕棿绐楀彛鍐呯殑鎯呮劅鏃堕棿绾?
    杩斿洖锛?    - dominant_emotion: 璇ユ椂鏈熺殑涓诲鎯呮劅
    - emotional_arc: 鎯呮劅鍙樺寲寮х嚎锛堝紑濮?涓棿/缁撴潫锛?    - highlight_memories: 鎯呮劅鏄捐憲搴︽渶楂樼殑璁板繂
    - timeline_summary: 鑷劧璇█鏃堕棿绾挎憳瑕侊紙缁橪LM鐢級
    """
    start_ts = anchor_ts_ms - window_ms
    end_ts = anchor_ts_ms

    # 绛涢€夋椂闂寸獥鍙ｅ唴鐨勮蹇?    window_records = [
        r for r in self._records.values()
        if start_ts <= r.ts_ms <= end_ts
        and r.deleted_at is None
        and r.merged_into is None
    ]

    if not window_records:
        return {"timeline_summary": "杩欐鏃堕棿娌℃湁鐗瑰埆鐨勮蹇?}

    # 鎸夋椂闂存帓搴忥紝鍒嗘瀽鎯呮劅寮х嚎
    sorted_records = sorted(window_records, key=lambda r: r.ts_ms)
    emotion_sequence = [
        r.to_dict().get("emotion_tag", "NEUTRAL")
        for r in sorted_records
    ]

    # 缁熻涓诲鎯呮劅
    from collections import Counter
    emotion_counts = Counter(emotion_sequence)
    dominant = emotion_counts.most_common(1)[0][0]

    # 鎯呮劅寮х嚎锛堜笁娈靛紡锛?    n = len(emotion_sequence)
    arc = {
        "start": emotion_sequence[0] if n > 0 else "NEUTRAL",
        "middle": emotion_sequence[n // 2] if n > 1 else "NEUTRAL",
        "end": emotion_sequence[-1] if n > 0 else "NEUTRAL",
    }

    # 鎯呮劅鏄捐憲搴︽渶楂樼殑璁板繂锛堥噷绋嬬浼樺厛锛?    highlights = sorted(
        window_records,
        key=lambda r: (
            r.to_dict().get("is_milestone", False),
            r.to_dict().get("emotional_salience", 0.5),
        ),
        reverse=True,
    )[:top_k]

    return {
        "dominant_emotion": dominant,
        "emotional_arc": arc,
        "highlight_memories": [r.text for r in highlights],
        "timeline_summary": _arc_to_summary(arc, dominant),
    }

def _arc_to_summary(arc: dict, dominant: str) -> str:
    """灏嗘儏鎰熷姬绾胯浆涓鸿嚜鐒惰瑷€鎽樿"""
    emotion_zh = {
        "HAPPY": "寮€蹇?, "SAD": "闅捐繃", "ANGRY": "鐢熸皵",
        "EXCITED": "鍏村", "CALM": "骞抽潤", "NEUTRAL": "骞虫贰",
        "THINKING": "鑻ユ湁鎵€鎬?, "SURPRISED": "鎯婂枩",
    }
    start = emotion_zh.get(arc["start"], "骞虫贰")
    end = emotion_zh.get(arc["end"], "骞虫贰")
    dom = emotion_zh.get(dominant, "骞虫贰")
    if arc["start"] == arc["end"]:
        return f"閭ｆ鏃堕棿鏁翠綋鎰熻{dom}"
    return f"閭ｆ鏃堕棿浠巤start}寮€濮嬶紝鏈€鍚巤end}鏀跺熬锛屾€讳綋鏉ヨ姣旇緝{dom}"
```

**棰勬湡鏁堟灉锛?* AI 鍦ㄥ洖蹇?涓婂懆"鎴?閭ｆ鏃堕棿"鏃讹紝鑳界粰鍑烘湁鎯呮劅璐ㄦ劅鐨勬椂闂寸嚎鎻忚堪锛岃€屼笉鍙槸瀛ょ珛鐨勮蹇嗙墖娈点€?
---

### 馃數 娣卞寲浼樺寲10锛圥2锛夛細LLM Gateway 澧炲姞鎯呮劅鎰熺煡鐨?Prompt 娉ㄥ叆绛栫暐

**鐩爣鏂囦欢锛?* `host_brain/integrations/llm_gateway.py`

**娣卞眰闂鍙戠幇锛?*

`LlmGatewayContract.generate()` 鍦ㄨ皟鐢?LLM 鍓嶏紝`prompt_payload` 鏄敱涓婂眰缁勮鐨勶紝Gateway 灞傚畬鍏ㄤ笉鎰熺煡鎯呮劅鐘舵€併€傝繖鎰忓懗鐫€鍗充娇 `emotion_pulse` 妲戒綅宸茬粡娉ㄥ叆浜嗘儏鎰熶俊鎭紝Gateway 涔熶笉浼氭牴鎹儏鎰熺姸鎬佽皟鏁?*鐢熸垚鍙傛暟**锛堝 temperature銆乼op_p锛夈€?
**鏂规锛?* 鍦?`generate()` 涓鍔?*鎯呮劅鎰熺煡鐨勭敓鎴愬弬鏁拌皟鏁?*锛?
```python
# llm_gateway.py generate() 鏂规硶澧炲姞鎯呮劅鎰熺煡鍙傛暟璋冩暣
def _build_emotion_aware_params(
    self,
    prompt_payload: dict[str, Any],
) -> dict[str, Any]:
    """鏍规嵁鎯呮劅鐘舵€佽皟鏁?LLM 鐢熸垚鍙傛暟

    鎯呮劅鐘舵€?鈫?鐢熸垚鍙傛暟鏄犲皠锛?    - 楂樺叴/鍏村锛歵emperature鈫戯紙鏇存湁鍒涙剰锛屾洿娲绘臣锛?    - 鎮蹭激/浣庤惤锛歵emperature鈫擄紙鏇寸ǔ瀹氾紝鏇存俯鏌旓級
    - 鎰ゆ€掞細temperature鈫?+ top_p鈫擄紙鏇村厠鍒讹紝閬垮厤婵€鍖栵級
    - 鎬濊€冧腑锛歵emperature鈫擄紙鏇寸簿纭紝鏇存湁閫昏緫锛?    """
    emotion_pulse = prompt_payload.get("emotion_pulse", {})
    mood_desc = str(emotion_pulse.get("mood_description", ""))
    energy_feel = str(emotion_pulse.get("energy_feel", "骞抽潤"))

    # 榛樿鍙傛暟
    params = {"temperature": 0.7, "top_p": 0.9}

    if "鍏村" in mood_desc or "寰堝ソ" in mood_desc:
        params["temperature"] = 0.85   # 鏇存椿娉?    elif "闅惧彈" in mood_desc or "浣庤惤" in mood_desc:
        params["temperature"] = 0.55   # 鏇存俯鏌旂ǔ瀹?    elif "鎱垫噿" in energy_feel:
        params["temperature"] = 0.60   # 鎱垫噿鐘舵€侊紝鍥炲鏇寸畝鐭?    elif "鍏呮矝" in energy_feel:
        params["temperature"] = 0.80   # 绮惧姏鍏呮矝锛屽洖澶嶆洿涓板瘜

    return params
```

**瀹夊叏绾︽潫锛?* 鐢熸垚鍙傛暟璋冩暣鑼冨洿闄愬埗鍦?`[0.4, 0.95]`锛屼笉鍏佽鏋佺鍊硷紝闃叉杈撳嚭璐ㄩ噺澶辨帶銆?
---

### 馃煟 娣卞寲浼樺寲11锛圥2锛夛細Bond 鍏崇郴鐨?瑁傜棔淇"鏈哄埗

**鐩爣鏂囦欢锛?* `host_brain/bond/bond_stage_engine.py`

**娣卞眰闂鍙戠幇锛?*

褰撳墠 Bond 绯荤粺鍙湁**绾挎€хН鍒?*锛屾病鏈?*鍏崇郴瑁傜棔**鐨勬蹇点€傜幇瀹炰腑鍏崇郴浼氬洜涓鸿瑙ｃ€佸喎婕犮€佸啿绐佽€屼骇鐢熻鐥曪紝淇瑁傜棔姣旀櫘閫氱Н绱洿鏈夋儏鎰熸剰涔夈€?
**鏂规锛?* 鏂板**瑁傜棔-淇**鍙岃建鏈哄埗锛?
```python
@dataclass
class BondRiftState:
    """鍏崇郴瑁傜棔鐘舵€侊紙鐙珛浜庣Н鍒嗭紝褰卞搷琛ㄨ揪椋庢牸锛?""
    has_rift: bool = False              # 鏄惁瀛樺湪瑁傜棔
    rift_cause: str = ""                # 瑁傜棔鍘熷洜锛堣涔夊寲鎻忚堪锛?    rift_ts_ms: int = 0                 # 瑁傜棔鍙戠敓鏃堕棿
    repair_progress: float = 0.0       # 淇杩涘害 [0.0, 1.0]
    repair_attempts: int = 0           # 淇灏濊瘯娆℃暟

    def to_rift_hint(self) -> str | None:
        """鐢熸垚缁?LLM 鐨勮鐥曟彁绀猴紙浠呭湪鏈夎鐥曟椂杩斿洖锛?""
        if not self.has_rift:
            return None
        if self.repair_progress > 0.8:
            return "涔嬪墠鏈変簺璇細锛屼絾宸茬粡鍩烘湰鍖栬В浜嗭紝鍏崇郴姝ｅ湪鎭㈠"
        if self.repair_progress > 0.4:
            return "涔嬪墠鏈変簺涓嶆剦蹇紝姝ｅ湪鎱㈡參淇涓紝闇€瑕佹洿澶氳€愬績"
        return f"鍏崇郴鏈変簺瑁傜棔锛坽self.rift_cause}锛夛紝闇€瑕佹俯鏌斿湴淇"

# 瑁傜棔瑙﹀彂鏉′欢锛堝湪 apply_event 涓娴嬶級
RIFT_TRIGGERS = {
    "CONFLICT_UNRESOLVED": 1.0,    # 鏈В鍐崇殑鍐茬獊 鈫?绔嬪嵆浜х敓瑁傜棔
    "IDLE_DECAY": 0.1,             # 闀挎湡涓嶄簰鍔?鈫?缂撴參绉疮瑁傜棔椋庨櫓
    "MISUNDERSTANDING": 0.5,       # 璇В 鈫?涓瓑瑁傜棔
}

# 淇浜嬩欢锛圕ONFLICT_RESOLVED 瑙﹀彂淇锛?REPAIR_EVENTS = {
    "CONFLICT_RESOLVED": 0.4,      # 鍐茬獊瑙ｅ喅 鈫?淇40%
    "EMOTIONAL_SUPPORT": 0.2,      # 鎯呮劅鏀寔 鈫?淇20%
    "DEEP_TALK": 0.15,             # 娣卞害浜ゆ祦 鈫?淇15%
    "MILESTONE": 0.3,              # 閲岀▼纰?鈫?淇30%
}
```

**棰勬湡鏁堟灉锛?* AI 鑳芥劅鐭ュ埌鍏崇郴涓殑"瑁傜棔"锛屽湪淇鏈熼棿鑷姩璋冩暣琛ㄨ揪椋庢牸锛堟洿娓╂煍銆佹洿鑰愬績锛夛紝鑰屼笉鏄満姊板湴鎸夌Н鍒嗛樁娈靛洖搴斻€?
---

### 鈿?娣卞寲浼樺寲12锛圥3锛夛細鎯呮劅鍥炲０锛圗motional Echo锛夋満鍒?
**鐩爣鏂囦欢锛?* 鏂板 `host_brain/core/emotion_echo.py`

**娣卞眰闂鍙戠幇锛?*

褰撳墠鏋舵瀯涓紝AI 鐨勬儏鎰熺姸鎬佸拰鐢ㄦ埛鐨勬儏鎰熺姸鎬佹槸**瀹屽叏鐙珛**鐨勪袱鏉＄嚎銆傜湡瀹炵殑鎯呮劅浜や簰涓紝鍙屾柟鎯呮劅浼氱浉浜掑奖鍝嶁€斺€旂敤鎴峰紑蹇冩椂 AI 涔熶細琚劅鏌擄紝鐢ㄦ埛鎮蹭激鏃?AI 浼氫骇鐢熷叡鎯呫€?
**鏂规锛?* 鏂板**鎯呮劅鍥炲０妯″潡**锛屽疄鐜板弻鍚戞儏鎰熸劅鏌擄細

```python
# host_brain/core/emotion_echo.py锛堟柊鏂囦欢锛?
@dataclass
class EmotionEchoResult:
    """鎯呮劅鍥炲０璁＄畻缁撴灉"""
    echo_vector: "EmotionVector"    # 鍥炲０鍚庣殑 AI 鎯呮劅鍚戦噺
    echo_strength: float            # 鍥炲０寮哄害 [0.0, 1.0]
    echo_description: str           # 鑷劧璇█鎻忚堪锛堢粰LLM鐢級
    bond_amplifier: float           # Bond 闃舵鏀惧ぇ绯绘暟锛堝叧绯昏秺娣憋紝鍏辨儏瓒婂己锛?
def compute_emotion_echo(
    *,
    ai_emotion: "EmotionVector",
    user_emotion_tag: str,
    bond_stage: str,
    ts_ms: int,
) -> EmotionEchoResult:
    """璁＄畻鐢ㄦ埛鎯呮劅瀵?AI 鎯呮劅鐨勫洖澹板奖鍝?
    鏍稿績閫昏緫锛?    1. 灏嗙敤鎴?emotion_tag 杞负鎯呮劅鍚戦噺
    2. 鏍规嵁 Bond 闃舵璁＄畻鍏辨儏寮哄害锛堝叧绯昏秺娣憋紝鍏辨儏瓒婂己锛?    3. AI 鎯呮劅鍚戦噺鍚戠敤鎴锋儏鎰熸柟鍚戝亸绉伙紙鎯呮劅鎰熸煋锛?    4. 杩斿洖鍥炲０鍚庣殑鏂版儏鎰熷悜閲?    """
    # Bond 闃舵 鈫?鍏辨儏寮哄害鏄犲皠
    BOND_EMPATHY_MAP = {
        "STRANGER":      0.05,   # 闄岀敓浜猴細鍑犱箮涓嶅彈褰卞搷
        "ACQUAINTANCE":  0.10,   # 鐩歌瘑锛氳交寰劅鏌?        "FRIEND":        0.20,   # 鏈嬪弸锛氭槑鏄惧叡鎯?        "CLOSE_FRIEND":  0.35,   # 鎸氬弸锛氬己鐑堝叡鎯?        "CONFIDANT":     0.50,   # 鐭ュ繁锛氭繁搴﹀叡鎯?        "SOULMATE":      0.65,   # 鐏甸瓊浼翠荆锛氶珮搴﹀悓棰?    }
    empathy_strength = BOND_EMPATHY_MAP.get(bond_stage, 0.10)

    # 鐢ㄦ埛鎯呮劅 鈫?鍚戦噺鏄犲皠
    USER_EMOTION_VECTORS = {
        "HAPPY":     (0.8, 0.6, 0.6),
        "EXCITED":   (0.9, 0.9, 0.7),
        "SAD":       (-0.7, 0.2, 0.2),
        "ANGRY":     (-0.5, 0.8, 0.7),
        "CALM":      (0.2, 0.1, 0.5),
        "THINKING":  (0.1, 0.3, 0.6),
        "SURPRISED": (0.4, 0.8, 0.4),
        "NEUTRAL":   (0.0, 0.3, 0.5),
    }
    v, a, d = USER_EMOTION_VECTORS.get(user_emotion_tag, (0.0, 0.3, 0.5))
    from host_brain.core.metabolism import EmotionVector
    user_vector = EmotionVector(valence=v, arousal=a, dominance=d, ts_ms=ts_ms)

    # AI 鎯呮劅鍚戠敤鎴锋儏鎰熸柟鍚戝亸绉伙紙鎯呮劅鎰熸煋锛?    echo_vector = ai_emotion.blend(user_vector, weight=empathy_strength)

    # 鐢熸垚鍥炲０鎻忚堪
    echo_description = _echo_to_description(
        user_emotion_tag=user_emotion_tag,
        empathy_strength=empathy_strength,
        bond_stage=bond_stage,
    )

    return EmotionEchoResult(
        echo_vector=echo_vector,
        echo_strength=empathy_strength,
        echo_description=echo_description,
        bond_amplifier=empathy_strength,
    )

def _echo_to_description(
    *,
    user_emotion_tag: str,
    empathy_strength: float,
    bond_stage: str,
) -> str:
    """灏嗗洖澹扮粨鏋滆浆涓鸿嚜鐒惰瑷€鎻忚堪锛堢粰 LLM 鐨?emotion_pulse 妲戒綅鐢級"""
    if empathy_strength < 0.1:
        return ""  # 闄岀敓浜洪樁娈典笉浜х敓鏄庢樉鍥炲０
    emotion_zh = {
        "HAPPY": "瀵规柟鐨勫紑蹇冩劅鏌撲簡鎴?,
        "SAD": "鎰熷彈鍒板鏂圭殑闅捐繃锛屽績閲屼篃鏈変簺娌?,
        "ANGRY": "鎰熷彈鍒板鏂圭殑鎯呯华锛岄渶瑕佹俯鏌斿簲瀵?,
        "EXCITED": "瀵规柟鐨勫叴濂嬭鎴戜篃鏈変簺闆€璺?,
        "CALM": "瀵规柟鐨勫钩闈欒鎴戜篃瀹夊畾涓嬫潵",
    }
    base = emotion_zh.get(user_emotion_tag, "")
    if not base:
        return ""
    if bond_stage in ("CONFIDANT", "SOULMATE"):
        return f"{base}锛屼綔涓虹煡宸憋紝杩欑鎰熷彈鏍煎鐪熷垏"
    return base
```

**瀹夊叏绾︽潫锛?*
- 鎯呮劅鍥炲０寮哄害涓婇檺 `0.65`锛堢伒榄備即渚ｉ樁娈碉級锛岄槻姝?AI 鎯呮劅琚敤鎴峰畬鍏ㄦ帶鍒?- 鍥炲０鎻忚堪涓嶆毚闇叉暟鍊硷紝鍙紶閫掕嚜鐒惰瑷€鎻忚堪
- 璐熼潰鎯呮劅锛坄ANGRY`锛夌殑鍥炲０涓嶄細璁?AI 涔熷彉鎰ゆ€掞紝鑰屾槸瑙﹀彂"娓╂煍搴斿"妯″紡

---

## 鍏€佹繁鍖栦紭鍖栧叏鏅浘

```mermaid
graph LR
    subgraph 鐢ㄦ埛渚?        U_EMO["鐢ㄦ埛鎯呮劅\nemotion_tag"]
        U_TEXT["鐢ㄦ埛鏂囨湰\n闀垮害/鍐呭/闂彿"]
    end

    subgraph 鎯呮劅鎰熺煡灞傦紙鏂板锛?        ECHO["鎯呮劅鍥炲０\nEmotionEcho\n浼樺寲12"]
        QUALITY["浜掑姩璐ㄩ噺鎺ㄦ柇\nInteractionQuality\n浼樺寲7"]
        TIMELINE["鎯呮劅鏃堕棿绾縗nEmotionalTimeline\n浼樺寲9"]
    end

    subgraph AI鍐呮牳
        EV["鎯呯华鍚戦噺\nEmotionVector\n浼樺寲1"]
        BOND["Bond绉垎+瑁傜棔\nBondRiftState\n浼樺寲11"]
        MEM["鎯呮劅璁板繂\nEmotionalMemory\n浼樺寲3"]
        GOV["Governor R8\n鎯呮劅闂ㄦ帶\n浼樺寲8"]
    end

    subgraph 杈撳嚭灞?        HINT["鎯呮劅鑼冨洿鎻愮ず\nallowed_emotion_tags\n浼樺寲6"]
        PULSE["emotion_pulse妲戒綅\n浼樺寲4"]
        PARAMS["鐢熸垚鍙傛暟璋冩暣\ntemperature/top_p\n浼樺寲10"]
        LLM["LLM鐢熸垚\n鏈夋儏鎰熻川鎰熺殑鍥炲"]
    end

    U_EMO --> ECHO --> EV
    U_TEXT --> QUALITY --> BOND
    MEM --> TIMELINE --> PULSE
    EV --> HINT
    EV --> PULSE
    BOND --> PULSE
    GOV --> LLM
    HINT --> LLM
    PULSE --> LLM
    PARAMS --> LLM
```

---

## 涔濄€佹繁鍖栦紭鍖栧疄鏂戒紭鍏堢骇锛堝畬鏁寸増锛?
| 浼樺厛绾?| 浼樺寲椤?| 鏀瑰姩鑼冨洿 | 鏍稿績浠峰€?| 渚濊禆 |
|--------|--------|----------|----------|------|
| 馃敶 P0 | 鎯呯华杩炵画浣?EmotionVector | `metabolism.py` | 鎯呯华杩囨浮鑷劧 | 鏃?|
| 馃敶 P0 | 鎯呮劅椹卞姩鐨?emotion_tag 鐢熸垚绛栫暐 | `metabolism.py` + `qa.py` | LLM 涓诲姩鎰熺煡鎯呮劅鑼冨洿 | P0-1 |
| 馃煚 P0 | Bond 浜掑姩璐ㄩ噺鍒嗙骇 | `bond_stage_engine.py` | 绉垎鏇寸湡瀹?| 鏃?|
| 馃煚 P1 | Bond 鎯呮劅璐ㄦ劅灞?| `bond_stage_engine.py` | 鍏崇郴鏈夋俯搴︽劅 | P0-1 |
| 馃煚 P1 | Bond 瑁傜棔淇鏈哄埗 | `bond_stage_engine.py` | 鍏崇郴鏈夎捣浼忔劅 | P1-Bond |
| 馃煛 P1 | 鎯呮劅璁板繂鏍囨敞 | `episodic.py` | 鍥炲繂鏈夋儏鎰熷叡楦?| P0-1 |
| 馃煛 P1 | 鎯呮劅鏃堕棿绾块噸寤?| `episodic.py` | 鍥炲繂鏈夋椂闂磋川鎰?| P1-Mem |
| 馃煝 P1 | Governor R8 鎯呮劅闂ㄦ帶 | `governor/decision.py` | 鍐崇瓥鎰熺煡鎯呮劅 | P0-1 |
| 馃煝 P2 | emotion_pulse 鍔ㄦ€佹Ы浣?| `prompt_context.py` | LLM 鎰熺煡姝ゅ埢鎰熷彈 | P0+P1 |
| 馃數 P2 | 涓诲姩鎯呮劅瑙﹀彂鍣?| `metabolism.py` | AI 涓诲姩鍏冲績 | P0-1 |
| 馃數 P2 | LLM 鎯呮劅鎰熺煡鐢熸垚鍙傛暟 | `llm_gateway.py` | 鐢熸垚椋庢牸闅忔儏鎰熷彉鍖?| P2-pulse |
| 馃煟 P2 | 鎯呮劅鍥炲０鏈哄埗 | 鏂板 `emotion_echo.py` | 鍙屽悜鎯呮劅鎰熸煋 | P0+Bond |

---

## 鍗併€佺涓夎疆娣卞寲浼樺寲锛氫唬鐮佺簿璇诲悗鐨勭洸鍖哄彂鐜?
> **v1.2 鏂板**锛?026-03-05锛夈€傚熀浜庡鎵€鏈夋牳蹇冩枃浠剁殑閫愯绮捐锛屽彂鐜?v1.0/v1.1 瀹屽叏鏈Е鍙婄殑 6 涓繁灞傛灦鏋勭洸鍖恒€傝繖浜涢棶棰樻洿闅愯斀锛屼絾瀵?娲荤伒娲荤幇"鐨勫奖鍝嶆洿鏍规湰銆?
---

### 馃敶 娣卞寲浼樺寲13锛圥0锛夛細`evolve_hard_state` 鐨勬儏鎰熶簨浠惰涔夌己澶?
**鐩爣鏂囦欢锛?* `host_brain/core/metabolism.py`

**绮捐鍙戠幇锛?*

`evolve_hard_state()` 鐨?`event_kind` 鍙傛暟鍙湁 `"interaction"/"reward"/"success"/"positive"/"warning"/"risk"` 杩欏嚑涓€硷紝涓斿彧鐢ㄤ簬 `_normalize_mood()` 鐨?mood 鎺ㄦ柇銆備絾 `_normalize_mood()` 鐨勯€昏緫鏋佸叾绠€鍗曪細

```python
# 褰撳墠閫昏緫锛堣繃浜庣畝鍗曪級
if kind in {"reward", "success", "positive"} and energy >= 60:
    return "engaged"
if kind in {"warning", "risk"}:
    return "calm"
return "steady"  # 鍏朵粬鎵€鏈夋儏鍐甸兘鏄?steady
```

**闂锛?* 99% 鐨勪簰鍔ㄩ兘璧?`return "steady"` 杩欐潯璺紝`event_kind` 褰㈠悓铏氳銆傛儏鎰熶簨浠剁殑璇箟锛堢敤鎴峰垎浜簡鍠滀簨銆佺敤鎴峰湪鍝常銆佺敤鎴峰湪鐢熸皵锛夊畬鍏ㄦ病鏈変紶閫掑埌 mood 鎺ㄦ柇涓€?
**鏂规锛?* 鎵╁睍 `event_kind` 璇箟闆嗭紝骞剁粏鍖?`_normalize_mood()` 鐨勬帹鏂€昏緫锛?
```python
# 鎵╁睍鍚庣殑 event_kind 璇箟闆?EMOTIONAL_EVENT_KINDS = {
    # 姝ｅ悜浜嬩欢
    "reward", "success", "positive",
    "user_happy", "user_excited", "shared_joy",
    "milestone_reached", "deep_connection",
    # 璐熷悜浜嬩欢
    "warning", "risk",
    "user_sad", "user_angry", "user_stressed",
    "conflict", "misunderstanding",
    # 涓€т簨浠?    "interaction", "user_thinking", "user_calm",
    "long_silence_broken",  # 娌夐粯鍚庨噸鏂颁簰鍔?}

# 缁嗗寲鍚庣殑 _normalize_mood()
def _normalize_mood(value: Any, *, energy: int, event_kind: str = "interaction") -> str:
    text = str(value or "").strip().lower()
    if text in ALLOWED_MOOD_STATES:
        return text
    kind = str(event_kind or "").strip().lower()

    # 鑳介噺浼樺厛锛堟瀬绔儏鍐碉級
    if energy < 20:
        return "critical"
    if energy < 40:
        return "tired"

    # 鎯呮劅浜嬩欢璇箟鎺ㄦ柇锛堟柊澧烇級
    if kind in {"user_happy", "shared_joy", "milestone_reached", "deep_connection"}:
        return "engaged" if energy >= 50 else "steady"
    if kind in {"user_excited", "reward", "success", "positive"} and energy >= 60:
        return "engaged"
    if kind in {"user_sad", "user_stressed", "conflict"}:
        return "calm"   # 閬囧埌璐熼潰鎯呯华锛孉I 杩涘叆骞抽潤/闄即妯″紡
    if kind in {"user_angry", "misunderstanding"}:
        return "steady"  # 閬囧埌鎰ゆ€掞紝AI 淇濇寔绋冲畾
    if kind in {"long_silence_broken"}:
        return "steady" if energy >= 50 else "tired"
    if kind in {"warning", "risk"}:
        return "calm"

    return "steady"
```

**棰勬湡鏁堟灉锛?* `evolve_hard_state()` 璋冪敤鏃朵紶鍏ヨ涔夊寲鐨?`event_kind`锛宮ood 鎺ㄦ柇灏辫兘鐪熸鍙嶆槧鎯呮劅浜嬩欢鐨勬€ц川锛岃€屼笉鏄案杩滆繑鍥?`steady`銆?
---

### 馃煚 娣卞寲浼樺寲14锛圥0锛夛細`BondScoreState` 鐨?`trend()` 鏂规硶璇箟澶辩湡

**鐩爣鏂囦欢锛?* `host_brain/bond/bond_stage_engine.py`

**绮捐鍙戠幇锛?*

`BondScoreState.trend()` 鐨勫垽鏂€昏緫瀛樺湪涓ラ噸鐨勮涔夊け鐪燂細

```python
# 褰撳墠閫昏緫锛堟湁闂锛?def trend(self) -> str:
    if len(self.recent_scores) < 2:
        return "STABLE"
    recent = self.recent_scores[-TREND_WINDOW:]
    positive = sum(1 for s in recent if s > 0)
    negative = sum(1 for s in recent if s < 0)
    if positive > negative and positive >= len(recent) // 2 + 1:
        return "RISING"
    if negative > positive and negative >= len(recent) // 2 + 1:
        return "DECLINING"
    return "STABLE"
```

**闂锛?*
1. `TREND_WINDOW=5`锛屼絾 `recent_scores` 瀛樺偍鐨勬槸 `TREND_WINDOW * 2 = 10` 鏉★紝鍙?`[-TREND_WINDOW:]` 鍙湅鏈€杩?鏉★紝浣嗗垵濮嬪寲鏃?`recent_scores=[]`锛屽墠鍑犳浜掑姩姘歌繙杩斿洖 `STABLE`
2. `IDLE_DECAY=-2` 鐨勮礋鍒嗗拰 `INTERACTION_MINIMAL=0` 鐨勯浂鍒嗛兘浼氳璁″叆锛屽鑷?浠€涔堥兘娌″仛"涔熷奖鍝嶈秼鍔?3. `to_summary()` 鐩存帴鎶?`trend()` 缁撴灉浼犵粰 LLM锛屼絾 `RISING/STABLE/DECLINING` 杩欎笁涓嫳鏂囨灇涓惧 LLM 鐨勮涔夊紩瀵兼晥鏋滃緢宸?
**鏂规锛?* 閲嶆瀯 `trend()` 骞跺鍔?`trend_hint()` 璇箟鍖栬緭鍑猴細

```python
def trend(self) -> str:
    """鏍规嵁鏈€杩戠Н鍒嗚秼鍔垮垽鏂?RISING/STABLE/DECLINING銆?""
    # 杩囨护闆跺垎浜嬩欢锛圛DLE_DECAY 鍜?INTERACTION_MINIMAL 涓嶅弬涓庤秼鍔垮垽鏂級
    meaningful_scores = [s for s in self.recent_scores if s != 0]
    if len(meaningful_scores) < 2:
        return "STABLE"
    recent = meaningful_scores[-TREND_WINDOW:]
    positive = sum(1 for s in recent if s > 0)
    negative = sum(1 for s in recent if s < 0)
    total = len(recent)
    if positive >= total * 0.6:   # 60% 浠ヤ笂姝ｅ悜 鈫?RISING
        return "RISING"
    if negative >= total * 0.6:   # 60% 浠ヤ笂璐熷悜 鈫?DECLINING
        return "DECLINING"
    return "STABLE"

def trend_hint(self) -> str:
    """灏嗚秼鍔胯浆涓鸿嚜鐒惰瑷€鎻愮ず锛堢粰 LLM 鐢紝鏇夸唬鑻辨枃鏋氫妇锛?""
    t = self.trend()
    stage = self.current_stage
    hints = {
        ("RISING", "STRANGER"):      "鍒濇鐩歌瘑锛屾劅瑙夎繕涓嶉敊",
        ("RISING", "ACQUAINTANCE"):  "鍏崇郴鍦ㄥ崌娓╋紝瓒婃潵瓒婄啛鎮?,
        ("RISING", "FRIEND"):        "鍙嬭皧鍦ㄥ姞娣憋紝浜掑姩瓒婃潵瓒婂",
        ("RISING", "CLOSE_FRIEND"):  "鍏崇郴瓒婃潵瓒婁翰瀵?,
        ("RISING", "CONFIDANT"):     "褰兼瓒婃潵瓒婁俊浠?,
        ("RISING", "SOULMATE"):      "鐏甸瓊濂戝悎锛岃秺鏉ヨ秺娣?,
        ("DECLINING", "FRIEND"):     "鏈€杩戜簰鍔ㄥ皯浜嗕簺锛屾湁浜涚枏杩?,
        ("DECLINING", "CLOSE_FRIEND"): "鍏崇郴鏈変簺闄嶆俯锛岄渶瑕佸鑱旂郴",
        ("DECLINING", "CONFIDANT"):  "鎰熻鏈変簺璺濈锛岄渶瑕侀噸鏂版媺杩?,
        ("STABLE", "STRANGER"):      "杩樺湪鐩镐簰浜嗚В涓?,
        ("STABLE", "FRIEND"):        "鍏崇郴绋冲畾锛屾棩甯搁櫔浼?,
        ("STABLE", "SOULMATE"):      "榛樺鍗佽冻锛屾棤闇€澶氳█",
    }
    return hints.get((t, stage), f"鍏崇郴{{'RISING':'鍗囨俯','STABLE':'绋冲畾','DECLINING':'闄嶆俯'}.get(t,'绋冲畾')}")
```

**棰勬湡鏁堟灉锛?* `to_summary()` 涓敤 `trend_hint()` 鏇夸唬 `trend()`锛孡LM 鏀跺埌鐨勬槸"鍏崇郴鍦ㄥ崌娓╋紝瓒婃潵瓒婄啛鎮?鑰屼笉鏄?RISING"锛岃涔夊紩瀵兼晥鏋滃ぇ骞呮彁鍗囥€?
---

### 馃煛 娣卞寲浼樺寲15锛圥1锛夛細`EpisodicRecord` 鐨?`decay_profile_id` 鏄┖澹?
**鐩爣鏂囦欢锛?* `host_brain/memory/episodic.py`

**绮捐鍙戠幇锛?*

`EpisodicRecord.decay_profile_id` 瀛楁瀛樺湪锛宍decay_memory()` 鏂规硶涔熷瓨鍦紝浣嗭細
1. `decay_profile_id` 鍙槸涓€涓瓧绗︿覆鏍囩锛坄"default"`锛夛紝**娌℃湁浠讳綍瀹為檯鐨勮“鍑忛€昏緫**
2. `decay_memory()` 鍙槸鏇存柊浜?`decay_profile_id` 瀛楁锛?*娌℃湁浠讳綍瀹為檯鏁堟灉**
3. `recall()` 鍦ㄦ绱㈡椂瀹屽叏涓嶈€冭檻璁板繂鐨?鏂伴矞搴?锛?0骞村墠鐨勮蹇嗗拰鏄ㄥぉ鐨勮蹇嗘潈閲嶇浉鍚?
**鏂规锛?* 瀹炵幇鐪熸鐨?*璁板繂鏂伴矞搴﹁“鍑?*锛屽湪 `recall()` 涓姞鍏ユ椂闂存潈閲嶏細

```python
# 琛板噺閰嶇疆琛紙decay_profile_id 鈫?鍗婅“鏈燂級
DECAY_PROFILES: dict[str, int] = {
    "default":      30 * 24 * 3600 * 1000,   # 30澶╁崐琛版湡锛堟櫘閫氳蹇嗭級
    "milestone":    365 * 24 * 3600 * 1000,  # 1骞村崐琛版湡锛堥噷绋嬬锛屽嚑涔庝笉琛板噺锛?    "emotional":    14 * 24 * 3600 * 1000,   # 14澶╁崐琛版湡锛堟儏鎰熻蹇嗭紝琛板噺杈冨揩锛?    "factual":      90 * 24 * 3600 * 1000,   # 90澶╁崐琛版湡锛堜簨瀹炴€ц蹇嗭級
    "volatile":     3 * 24 * 3600 * 1000,    # 3澶╁崐琛版湡锛堜复鏃舵€ц蹇嗭級
}

def _compute_freshness(
    *,
    ts_ms: int,
    now_ms: int,
    decay_profile_id: str,
) -> float:
    """璁＄畻璁板繂鏂伴矞搴?[0.0, 1.0]锛屽熀浜庢寚鏁拌“鍑?""
    half_life_ms = DECAY_PROFILES.get(decay_profile_id, DECAY_PROFILES["default"])
    elapsed_ms = max(0, now_ms - ts_ms)
    return 0.5 ** (elapsed_ms / half_life_ms)

# 鏀硅繘鍚庣殑 recall()锛屽姞鍏ユ椂闂存潈閲?def recall(
    self,
    *,
    query_text: str,
    top_k: int = 5,
    now_ms: int | None = None,
    freshness_weight: float = 0.2,  # 鏂伴矞搴︽潈閲嶏紙榛樿20%锛?) -> list[dict[str, Any]]:
    """璇箟鐩镐技搴?+ 鏃堕棿鏂伴矞搴﹀姞鏉冨彫鍥?""
    current_ms = int(now_ms if now_ms is not None else _now_ms())
    embedding, _ = embed_text(query_text)
    candidates = self.chroma.search(query_embedding=embedding, top_k=top_k * 3)

    for item in candidates:
        meta = item.get("metadata", {})
        ts = int(meta.get("ts_ms", 0))
        decay_id = str(meta.get("decay_profile_id", "default"))
        freshness = _compute_freshness(
            ts_ms=ts, now_ms=current_ms, decay_profile_id=decay_id
        )
        # 鍔犳潈锛氳涔夌浉浼煎害(1-freshness_weight) + 鏂伴矞搴?freshness_weight)
        item["freshness"] = freshness
        item["weighted_score"] = (
            item["score"] * (1 - freshness_weight)
            + freshness * freshness_weight
        )

    candidates.sort(key=lambda x: x["weighted_score"], reverse=True)
    return candidates[:top_k]
```

**棰勬湡鏁堟灉锛?* 鏈€杩戝彂鐢熺殑浜嬫儏浼氳浼樺厛鍙洖锛屼箙杩滅殑璁板繂鑷劧娣″嚭锛岀鍚堢湡瀹炶蹇嗚寰嬨€傞噷绋嬬璁板繂锛坄milestone` profile锛夊嚑涔庝笉琛板噺锛屾案杩滀繚鎸侀珮鏉冮噸銆?
---

### 馃煝 娣卞寲浼樺寲16锛圥1锛夛細`prompt_context` 鐨?`OPTIONAL_SLOTS` 鍙湁涓€涓Ы浣嶏紝鎵╁睍鎬т笉瓒?
**鐩爣鏂囦欢锛?* `host_brain/core/prompt_context.py`

**绮捐鍙戠幇锛?*

`OPTIONAL_SLOTS` 鐩墠鍙湁 `semantic_slot` 涓€涓彲閫夋Ы浣嶏紝浣?v1.0/v1.1 璁捐鐨?`emotion_pulse`銆乣bond_rift_hint`銆乣emotional_timeline` 绛夋柊妲戒綅閮介渶瑕佸湪杩欓噷娉ㄥ唽锛屽惁鍒?`validate_prompt_context()` 浼氭姤 `CP_UNKNOWN_SLOT` 閿欒锛屽鑷存墍鏈夋儏鎰熷寮哄姛鑳芥棤娉曟敞鍏ャ€?
**鏂规锛?* 鎵╁睍 `OPTIONAL_SLOTS`锛屽苟涓烘瘡涓柊妲戒綅澧炲姞瀵瑰簲鐨勯獙璇佸嚱鏁帮細

```python
# prompt_context.py OPTIONAL_SLOTS 鎵╁睍
OPTIONAL_SLOTS: dict[str, type] = {
    "semantic_slot":       list,   # 璇箟璁板繂妲戒綅锛圡10-C锛屽凡鏈夛級
    "emotion_pulse":       dict,   # 姝ゅ埢鎯呮劅鑴夊啿锛堜紭鍖?锛屾柊澧烇級
    "bond_rift_hint":      dict,   # Bond瑁傜棔鎻愮ず锛堜紭鍖?1锛屾柊澧烇級
    "emotional_timeline":  dict,   # 鎯呮劅鏃堕棿绾匡紙浼樺寲9锛屾柊澧烇級
    "echo_hint":           dict,   # 鎯呮劅鍥炲０鎻愮ず锛堜紭鍖?2锛屾柊澧烇級
}

# 鍚勬柊妲戒綅鐨勯獙璇佽鍒?def _validate_emotion_pulse_slot(
    emotion_pulse: dict[str, Any], result: PromptContextValidationResult
) -> None:
    """楠岃瘉 emotion_pulse 妲戒綅鏍煎紡锛堜笉鍏佽鍖呭惈鏁板€煎瀷鎯呮劅鍙傛暟锛?""
    forbidden_numeric_keys = {"valence", "arousal", "dominance", "echo_strength"}
    for key in forbidden_numeric_keys:
        if key in emotion_pulse:
            result.add(
                CP_BOND_NUMERIC_FORBIDDEN,
                f"emotion_pulse must not contain numeric field: {key}",
                slot="emotion_pulse",
            )
    required_keys = {"mood_description", "energy_feel"}
    for key in required_keys:
        if key not in emotion_pulse:
            result.add(
                CP_SLOT_TYPE_INVALID,
                f"emotion_pulse missing required field: {key}",
                slot="emotion_pulse",
            )
```

**鍏抽敭浠峰€硷細** 杩欐槸鎵€鏈夋儏鎰熷寮哄姛鑳借兘澶熸甯稿伐浣滅殑**鍩虹璁炬柦**銆傛病鏈夎繖涓墿灞曪紝浼樺寲4/9/11/12 鐨勬Ы浣嶆敞鍏ラ兘浼氳 `validate_prompt_context()` 鎷掔粷銆?
---

### 馃數 娣卞寲浼樺寲17锛圥2锛夛細`BondDynamicsWriter.apply_update()` 缂哄皯鎯呮劅浜嬩欢鐨?payload 璇箟鏍囧噯鍖?
**鐩爣鏂囦欢锛?* `host_brain/bond/bond_dynamics.py`

**绮捐鍙戠幇锛?*

`apply_update()` 鐨?`payload` 鍙傛暟鏄?`dict[str, Any] | None`锛屽畬鍏ㄨ嚜鐢辨牸寮忥紝娌℃湁浠讳綍缁撴瀯绾︽潫銆傝繖瀵艰嚧锛?1. 涓婂眰璋冪敤鑰呬紶鍏ョ殑 payload 鏍煎紡鍚勫紓锛屾棤娉曞湪 `BondStageEngine` 涓仛鑷姩璐ㄩ噺鎺ㄦ柇锛堜紭鍖?闇€瑕?`user_text` 鍜?`emotion_tag` 瀛楁锛?2. 瀹¤閾句腑鐨?payload 鏁版嵁鏃犳硶琚悗缁垎鏋愬伐鍏疯В鏋?
**鏂规锛?* 瀹氫箟 `BondEventPayload` 鏍囧噯缁撴瀯锛屽苟鍦?`apply_update()` 涓仛瑙勮寖鍖栵細

```python
# bond_dynamics.py 鏂板 BondEventPayload 鏍囧噯缁撴瀯
@dataclass
class BondEventPayload:
    """Bond 浜嬩欢 payload 鏍囧噯缁撴瀯锛堢敤浜庤川閲忔帹鏂拰瀹¤锛?""
    user_text: str = ""             # 鐢ㄦ埛鍘熷鏂囨湰锛堢敤浜庤川閲忔帹鏂級
    emotion_tag: str = "NEUTRAL"    # 鐢ㄦ埛鎯呮劅鏍囩锛堢敤浜庤川閲忔帹鏂級
    topic_hint: str = ""            # 璇濋鎻愮ず锛堣涔夊寲锛屼笉鍚晱鎰熶俊鎭級
    session_duration_ms: int = 0    # 鏈浼氳瘽鏃堕暱锛堢敤浜庤妭濂忔劅鐭ワ級
    is_user_initiated: bool = True  # 鏄惁鐢ㄦ埛涓诲姩鍙戣捣锛堝奖鍝嶈川閲忓垎绾э級

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_text": self.user_text,
            "emotion_tag": self.emotion_tag,
            "topic_hint": self.topic_hint,
            "session_duration_ms": self.session_duration_ms,
            "is_user_initiated": self.is_user_initiated,
        }

    @classmethod
    def from_raw(cls, raw: dict[str, Any] | None) -> "BondEventPayload":
        """浠庡師濮?payload 瑙勮寖鍖栵紙瀹归敊澶勭悊锛?""
        if not isinstance(raw, dict):
            return cls()
        return cls(
            user_text=str(raw.get("user_text", ""))[:500],  # 鎴柇闃叉杩囬暱
            emotion_tag=str(raw.get("emotion_tag", "NEUTRAL")),
            topic_hint=str(raw.get("topic_hint", ""))[:100],
            session_duration_ms=int(raw.get("session_duration_ms", 0)),
            is_user_initiated=bool(raw.get("is_user_initiated", True)),
        )
```

**棰勬湡鏁堟灉锛?* `apply_update()` 鍐呴儴璋冪敤 `BondEventPayload.from_raw(payload)` 瑙勮寖鍖栧悗锛宍BondStageEngine` 鐨勮嚜鍔ㄨ川閲忔帹鏂紙浼樺寲7锛夊氨鑳藉彲闈犲湴浠?payload 涓鍙?`user_text` 鍜?`emotion_tag`銆?
---

### 馃煟 娣卞寲浼樺寲18锛圥2锛夛細`governance_snapshot` 鐨?`hard_state_summary` 缂哄皯鎯呮劅鏃堕棿鎴?
**鐩爣鏂囦欢锛?* `host_brain/core/metabolism.py`

**绮捐鍙戠幇锛?*

`build_governance_snapshot()` 鐢熸垚鐨?`hard_state_summary` 鍖呭惈锛?```python
"hard_state_summary": {
    "energy_bucket": ...,
    "mood_state": ...,
    "sys_mode": ...,
    "safety_flag": ...,
    "last_event_id": ...,
}
```

**闂锛?* 娌℃湁 `ts_ms`锛堟椂闂存埑锛夊瓧娈点€傝繖瀵艰嚧锛?1. Governor 闂ㄦ帶锛圧8锛屼紭鍖?锛夋棤娉曞垽鏂?杩欎釜 mood_state 鏄涔呭墠鐨?鈥斺€斾竴涓?灏忔椂鍓嶇殑 `critical` 鐘舵€佸拰鍒氬垰鍙戠敓鐨?`critical` 鐘舵€侊紝瀵瑰喅绛栫殑褰卞搷搴旇瀹屽叏涓嶅悓
2. `emotion_pulse` 妲戒綅锛堜紭鍖?锛夋棤娉曡绠楁儏鎰熺殑"鏂伴矞搴?锛屼笉鐭ラ亾褰撳墠鎯呮劅鏄垰鍙戠敓鐨勮繕鏄凡缁忚“鍑忎簡

**鏂规锛?* 鍦?`hard_state_summary` 涓鍔?`state_age_hint` 瀛楁锛?
```python
# build_governance_snapshot() 涓?hard_state_summary 鎵╁睍
def build_governance_snapshot(
    state: HardState,
    *,
    overrides: Mapping[str, Any] | None = None,
    now_ms: int | None = None,  # 鏂板鍙傛暟
) -> dict[str, Any]:
    current_ms = int(now_ms if now_ms is not None else _now_ms())
    state_age_ms = max(0, current_ms - state.ts_ms) if state.ts_ms > 0 else 0

    # state_age_hint锛氬皢鏃堕棿宸浆涓鸿涔夊寲鎻忚堪
    if state_age_ms < 60_000:           # 1鍒嗛挓鍐?        state_age_hint = "鍒氬垰鍙戠敓"
    elif state_age_ms < 600_000:        # 10鍒嗛挓鍐?        state_age_hint = "涓嶄箙鍓?
    elif state_age_ms < 3_600_000:      # 1灏忔椂鍐?        state_age_hint = "涓€浼氬効鍓?
    elif state_age_ms < 86_400_000:     # 1澶╁唴
        state_age_hint = "浠婂ぉ鏃╀簺鏃跺€?
    else:
        state_age_hint = "寰堜箙浠ュ墠"

    # ... 鍘熸湁閫昏緫 ...
    return {
        # ... 鍘熸湁瀛楁 ...
        "hard_state_summary": {
            "energy_bucket": energy_bucket(state.energy_level),
            "mood_state": state.mood_state,
            "sys_mode": state.sys_mode,
            "safety_flag": safety_flag,
            "last_event_id": state.last_event_id,
            "state_age_hint": state_age_hint,      # 鏂板锛氭儏鎰熸椂闂磋涔?            "state_age_ms": state_age_ms,          # 鏂板锛氬師濮嬫椂闂村樊锛堜緵闂ㄦ帶閫昏緫浣跨敤锛?        },
    }
```

**棰勬湡鏁堟灉锛?* Governor R8 闂ㄦ帶鍙互鍒ゆ柇"杩欎釜 `critical` 鐘舵€佹槸鍒氬垰鍙戠敓鐨勮繕鏄?灏忔椂鍓嶇殑"锛屼粠鑰屽仛鍑烘洿绮惧噯鐨勫喅绛栥€侺LM 涔熻兘鎰熺煡鍒?AI 鍒氬垰缁忓巻浜嗕粈涔?銆?
---

## 鍗佷竴銆佺涓夎疆浼樺寲鐨勬牳蹇冧环鍊兼€荤粨

绗笁杞紭鍖栬仛鐒︿簬**淇鐜版湁浠ｇ爜涓殑璇箟绌哄３闂**鈥斺€旈偅浜涘瓧娈靛瓨鍦ㄣ€佹柟娉曞瓨鍦紝浣嗗疄闄呬笂娌℃湁浠讳綍鏁堟灉鐨?鍋囧姛鑳?锛?
| 浼樺寲椤?| 鍙戠幇鐨?鍋囧姛鑳? | 淇鏂规 |
|--------|--------------|---------|
| **浼樺寲13** | `event_kind` 99%璧?`steady` 鍒嗘敮锛屽舰鍚岃櫄璁?| 鎵╁睍璇箟闆嗭紝缁嗗寲鎺ㄦ柇閫昏緫 |
| **浼樺寲14** | `trend()` 杩囨护閫昏緫鏈夌己闄凤紝`trend_hint` 缁橪LM鑻辨枃鏋氫妇 | 淇杩囨护閫昏緫锛屽鍔犺涔夊寲 `trend_hint()` |
| **浼樺寲15** | `decay_profile_id` 鍙槸鏍囩锛宍decay_memory()` 鏃犲疄闄呮晥鏋?| 瀹炵幇鐪熸鐨勬寚鏁拌“鍑忥紝`recall()` 鍔犲叆鏃堕棿鏉冮噸 |
| **浼樺寲16** | 鏂版儏鎰熸Ы浣嶆棤娉曟敞鍏ワ紙浼氳 `CP_UNKNOWN_SLOT` 鎷掔粷锛?| 鎵╁睍 `OPTIONAL_SLOTS`锛岃ˉ鍏呴獙璇佸嚱鏁?|
| **浼樺寲17** | `payload` 鑷敱鏍煎紡锛岃川閲忔帹鏂棤娉曞彲闈犺鍙栧瓧娈?| 瀹氫箟 `BondEventPayload` 鏍囧噯缁撴瀯 |
| **浼樺寲18** | `hard_state_summary` 鏃犳椂闂存埑锛岄棬鎺ф棤娉曞垽鏂儏鎰熸柊椴滃害 | 澧炲姞 `state_age_hint` + `state_age_ms` |

---

## 鍗佷簩銆佸畬鏁翠紭鍖栧叏鏅浘锛坴1.2锛?8椤癸級

```mermaid
graph TD
    subgraph 鍩虹璁炬柦灞傦紙蹇呴』鍏堝仛锛?        OPT16["浼樺寲16\nOPTIONAL_SLOTS鎵╁睍\n锛堟柊妲戒綅娉ㄥ唽锛?]
        OPT17["浼樺寲17\nBondEventPayload鏍囧噯鍖朶n锛坧ayload瑙勮寖锛?]
        OPT18["浼樺寲18\nstate_age_hint\n锛堟儏鎰熸椂闂存埑锛?]
    end

    subgraph 鎯呮劅鍐呮牳灞傦紙P0锛?        OPT1["浼樺寲1\nEmotionVector\n锛堣繛缁儏缁級"]
        OPT13["浼樺寲13\nevent_kind璇箟鎵╁睍\n锛坢ood鎺ㄦ柇淇锛?]
        OPT6["浼樺寲6\nemotion_tag鐢熸垚绛栫暐\n锛堜富鍔ㄥ憡鐭LM锛?]
        OPT7["浼樺寲7\nBond璐ㄩ噺鍒嗙骇\n锛堢Н鍒嗙湡瀹炲寲锛?]
        OPT14["浼樺寲14\ntrend_hint璇箟鍖朶n锛堣秼鍔挎弿杩颁慨澶嶏級"]
    end

    subgraph 璁板繂灞傦紙P1锛?        OPT3["浼樺寲3\n鎯呮劅璁板繂鏍囨敞\n锛堟儏鎰熷姞鏉冨彫鍥烇級"]
        OPT15["浼樺寲15\n璁板繂鏂伴矞搴﹁“鍑廫n锛堟椂闂存潈閲嶏級"]
        OPT9["浼樺寲9\n鎯呮劅鏃堕棿绾块噸寤篭n锛堝姬绾垮垎鏋愶級"]
    end

    subgraph 鍏崇郴灞傦紙P1锛?        OPT2["浼樺寲2\nBond璐ㄦ劅灞俓n锛堟俯搴︽劅鐭ワ級"]
        OPT11["浼樺寲11\nBond瑁傜棔淇\n锛堣捣浼忔劅锛?]
        OPT8["浼樺寲8\nGovernor R8\n锛堟儏鎰熼棬鎺э級"]
    end

    subgraph 杈撳嚭灞傦紙P2锛?        OPT4["浼樺寲4\nemotion_pulse妲戒綅\n锛堟鍒绘劅鍙楋級"]
        OPT5["浼樺寲5\n涓诲姩瑙﹀彂鍣╘n锛堜富鍔ㄥ叧蹇冿級"]
        OPT10["浼樺寲10\nLLM鐢熸垚鍙傛暟\n锛坱emperature璋冩暣锛?]
        OPT12["浼樺寲12\n鎯呮劅鍥炲０\n锛堝弻鍚戞劅鏌擄級"]
    end

    OPT16 --> OPT4
    OPT16 --> OPT9
    OPT16 --> OPT11
    OPT16 --> OPT12
    OPT17 --> OPT7
    OPT18 --> OPT8
    OPT1 --> OPT6
    OPT13 --> OPT1
    OPT7 --> OPT2
    OPT14 --> OPT2
    OPT3 --> OPT15
    OPT15 --> OPT9
    OPT1 --> OPT4
    OPT2 --> OPT4
    OPT9 --> OPT4
    OPT4 --> OPT10
    OPT1 --> OPT12
    OPT2 --> OPT12
```

---

## 鍗佷笁銆佸彉鏇磋褰曪紙瀹屾暣鐗堬級

| 鏃ユ湡 | 鐗堟湰 | 璇存槑 |
|------|------|------|
| 2026-03-05 | v1.0 | 鍒濈锛屽熀浜庝唬鐮佹繁搴﹀垎鏋愬垱寤猴紝璁板綍浜斿ぇ浼樺寲鏂瑰悜锛埪т竴~搂浜旓級 |
| 2026-03-05 | v1.1 | 娣卞寲鍒嗘瀽锛氬熀浜庡叏閲忎唬鐮佺簿璇伙紝鏂板鍏ぇ娣卞寲浼樺寲鏂瑰悜锛埪т竷~搂涔濓級 |
| 2026-03-05 | v1.2 | 绗笁杞繁鍖栵細鍙戠幇6涓?鍋囧姛鑳?绌哄３闂锛屾柊澧炰紭鍖?3~18锛埪у崄~搂鍗佷簩锛?|
| 2026-03-05 | v1.3 | 绗洓杞繁鍖栵細鍙戠幇6涓?鏋舵瀯鏂眰"闂锛屾柊澧炰紭鍖?9~24锛埪у崄鍥泘搂鍗佸叚锛?|

---

## 鍗佸洓銆佺鍥涜疆娣卞寲浼樺寲锛氭灦鏋勬柇灞傚彂鐜?
> **v1.3 鏂板**锛?026-03-05锛夈€傛湰杞熀浜庡 `rules.py`銆乣decision.py`銆乣prompt_context.py`銆乣bond_stage_engine.py`銆乣qa.py`銆乣metabolism.py` 鐨勫畬鏁撮€愯绮捐锛屽彂鐜颁簡鍓嶄笁杞湭瑙﹀強鐨?**6 涓灦鏋勬柇灞?*鈥斺€旇繖浜涢棶棰樹笉鏄?鍋囧姛鑳?锛岃€屾槸**鍔熻兘涔嬮棿鐨勮繛鎺ユ柇瑁?*锛屽鑷存儏鎰熶俊鎭湪娴佽浆杩囩▼涓倓鐒朵涪澶便€?
---

### 馃敶 娣卞寲浼樺寲19锛圥0锛夛細`GateSnapshot` 瀹屽叏涓嶆劅鐭ユ儏鎰熲€斺€擱8 闂ㄦ帶鏃犳硶钀藉湴

**鐩爣鏂囦欢锛?* `host_brain/governor/rules.py`

**绮捐鍙戠幇锛?*

v1.1 鐨勪紭鍖?锛圙overnor R8 鎯呮劅闂ㄦ帶锛夎璁′簡 `_gate_r8_emotion_context()` 鍑芥暟锛屼絾**鏃犳硶钀藉湴**锛屽師鍥犳槸锛?
`GateSnapshot` 鏁版嵁绫诲彧鏈?7 涓瓧娈碉紙`trust_level/energy_level/sys_mode/rate_limit_hit/scope/safety_flag/airlock_blocked`锛夛紝**瀹屽叏娌℃湁鎯呮劅鐩稿叧瀛楁**銆俙_normalize_snapshot()` 浠?`input_snapshot` 涓鍙栬繖 7 涓瓧娈碉紝鍏朵粬瀛楁鍏ㄩ儴涓㈠純銆?
鍗充娇涓婂眰鍦?`governance_snapshot` 涓敞鍏ヤ簡 `emotion_context`锛屽埌杈?`evaluate_governor_gates()` 鏃朵篃浼氳 `_normalize_snapshot()` 闈欓粯涓㈠純锛孯8 闂ㄦ帶姘歌繙璇讳笉鍒版儏鎰熸暟鎹€?
**鏂规锛?* 鍦?`GateSnapshot` 涓鍔犳儏鎰熸劅鐭ュ瓧娈碉紝骞跺湪 `_normalize_snapshot()` 涓鍙栵細

```python
@dataclass(frozen=True)
class GateSnapshot:
    trust_level: int
    energy_level: int
    sys_mode: str
    rate_limit_hit: bool
    scope: str
    scope_allowlist: tuple[str, ...]
    safety_flag: bool
    airlock_blocked: bool
    # Rate Limit 鎵╁睍瀛楁锛堝凡鏈夛級
    rate_limit_backend: str | None = None
    rate_limit_degraded: bool = False
    rate_limit_degraded_from: str | None = None
    rate_limit_reason_code: str | None = None
    # 馃啎 鎯呮劅鎰熺煡瀛楁锛坴1.3 鏂板锛岀敤浜?R8 闂ㄦ帶锛?    user_emotion_tag: str = "NEUTRAL"       # 鐢ㄦ埛褰撳墠鎯呮劅鏍囩
    ai_mood_state: str = "steady"           # AI 褰撳墠 mood_state
    state_age_ms: int = 0                   # 鎯呮劅鐘舵€佽窛浠婃椂闀匡紙姣锛?
def _normalize_snapshot(raw: dict[str, Any]) -> GateSnapshot:
    # ... 鍘熸湁瀛楁璇诲彇 ...
    # 馃啎 鎯呮劅鎰熺煡瀛楁璇诲彇
    emotion_ctx = raw.get("emotion_context", {})
    if isinstance(emotion_ctx, dict):
        user_emotion_tag = str(emotion_ctx.get("user_emotion_tag", "NEUTRAL")).upper()
        ai_mood_state = str(emotion_ctx.get("ai_mood_state", "steady")).lower()
        state_age_ms = int(emotion_ctx.get("state_age_ms", 0))
    else:
        # 鍏煎鏃ф牸寮忥細浠?hard_state_summary 璇诲彇
        hard_summary = raw.get("hard_state_summary", {})
        ai_mood_state = str(hard_summary.get("mood_state", "steady")).lower()
        state_age_ms = int(hard_summary.get("state_age_ms", 0))
        user_emotion_tag = "NEUTRAL"
    return GateSnapshot(
        # ... 鍘熸湁瀛楁 ...
        user_emotion_tag=user_emotion_tag,
        ai_mood_state=ai_mood_state,
        state_age_ms=state_age_ms,
    )
```

**鍏抽敭浠峰€硷細** 杩欐槸 R8 闂ㄦ帶锛堜紭鍖?锛夎兘澶熺湡姝ｈ惤鍦扮殑**蹇呰鍓嶆彁**銆傛病鏈夎繖涓慨澶嶏紝浼樺寲8鐨勬墍鏈夎璁￠兘鏄┖涓ゼ闃併€?
---

### 馃煚 娣卞寲浼樺寲20锛圥0锛夛細`extract_emotion_tag()` 涓嶄紶 `mood_state`鈥斺€攎ood 绾︽潫鏍￠獙姘歌繙涓嶈Е鍙?
**鐩爣鏂囦欢锛?* `host_brain/core/qa.py`

**绮捐鍙戠幇锛?*

`_validate_emotion_tag()` 鏈夊畬鏁寸殑 `mood_state` 绾︽潫鏍￠獙閫昏緫锛圡26-G锛夛紝浣?`extract_emotion_tag()` 璋冪敤鏃?*浠庝笉浼?`mood_state` 鍙傛暟**锛?
```python
# 褰撳墠瀹炵幇锛堟湁闂锛?def extract_emotion_tag(
    payload: dict[str, Any],
    *,
    ledger: Any | None = None,
    request_id: str | None = None,
) -> str:
    narrative = payload.get("narrative") if isinstance(payload, dict) else None
    raw_tag = narrative.get("emotion_tag") if isinstance(narrative, dict) else None
    return _validate_emotion_tag(raw_tag, ledger=ledger, request_id=request_id)
    # 鈫?娌℃湁浼?mood_state锛乵ood_state 绾︽潫鏍￠獙姘歌繙涓嶈Е鍙?```

杩欐剰鍛崇潃锛?1. `MOOD_EMOTION_CONSTRAINTS` 鐨勭害鏉熸槧灏勶紙`metabolism.py` 涓簿蹇冭璁＄殑锛?*姘歌繙涓嶄細琚?`extract_emotion_tag()` 浣跨敤**
2. `tired` 鐘舵€佷笅 LLM 杈撳嚭 `EXCITED`锛宍extract_emotion_tag()` 浼氱洿鎺ヨ繑鍥?`EXCITED`锛屼笉鍋氫换浣曢檷绾?3. 鍙湁鐩存帴璋冪敤 `_validate_emotion_tag(tag, mood_state=...)` 鎵嶈兘瑙﹀彂绾︽潫锛屼絾杩欎釜鍐呴儴鍑芥暟娌℃湁鍏紑鎺ュ彛

**鏂规锛?* 涓?`extract_emotion_tag()` 澧炲姞 `mood_state` 鍙傛暟锛屽苟鍦ㄨ皟鐢ㄩ摼涓紶閫掞細

```python
def extract_emotion_tag(
    payload: dict[str, Any],
    *,
    mood_state: str | None = None,   # 馃啎 鏂板鍙傛暟
    ledger: Any | None = None,
    request_id: str | None = None,
) -> str:
    """浠?LLM 杈撳嚭 payload 涓彁鍙栧苟鏍￠獙 emotion_tag銆?
    鑻ユ彁渚?mood_state锛屼細棰濆妫€鏌?emotion_tag 鏄惁鍦ㄥ綋鍓?mood_state 鐨勫厑璁歌寖鍥村唴銆?    """
    narrative = payload.get("narrative") if isinstance(payload, dict) else None
    raw_tag = narrative.get("emotion_tag") if isinstance(narrative, dict) else None
    return _validate_emotion_tag(
        raw_tag,
        mood_state=mood_state,   # 馃啎 浼犻€?mood_state
        ledger=ledger,
        request_id=request_id,
    )
```

**璋冪敤鏂逛慨鏀癸細** 鎵€鏈夎皟鐢?`extract_emotion_tag(payload)` 鐨勫湴鏂癸紝鏀逛负 `extract_emotion_tag(payload, mood_state=current_mood_state)`銆?
**棰勬湡鏁堟灉锛?* `MOOD_EMOTION_CONSTRAINTS` 鐨勭害鏉熸槧灏勭粓浜庤兘鍦ㄥ疄闄呰皟鐢ㄩ摼涓敓鏁堬紝`tired` 鐘舵€佷笅鐨?`EXCITED` 浼氳姝ｇ‘闄嶇骇涓?`NEUTRAL`銆?
---

### 馃煛 娣卞寲浼樺寲21锛圥1锛夛細`BondScoreState.to_summary()` 鐨?`trend` 瀛楁缁?LLM 浼犺嫳鏂囨灇涓?
**鐩爣鏂囦欢锛?* `host_brain/bond/bond_stage_engine.py`

**绮捐鍙戠幇锛?*

`to_summary()` 鐨勫疄鐜帮細

```python
def to_summary(self) -> dict[str, Any]:
    return {
        "stage": self.current_stage,          # "FRIEND"锛堣嫳鏂囨灇涓撅級
        "trend": self.trend(),                 # "RISING"锛堣嫳鏂囨灇涓撅級
        "capability_hint": self.capability_hint(),  # "鏈嬪弸锛屽彲娣卞叆浜ゆ祦"锛堜腑鏂囷級
    }
```

`stage` 鍜?`trend` 閮芥槸鑻辨枃鏋氫妇锛屼絾 `capability_hint` 鏄腑鏂囥€傝繖绉?*娣峰悎璇█**鐨?summary 瀵?LLM 鐨勮涔夊紩瀵兼晥鏋滃緢宸細
- LLM 鐪嬪埌 `"stage": "FRIEND"` 鏃讹紝闇€瑕佽嚜宸辨帹鏂繖鏄粈涔堟剰鎬?- LLM 鐪嬪埌 `"trend": "RISING"` 鏃讹紝鏃犳硶鐩存帴鎰熺煡"鍏崇郴鍦ㄥ崌娓?鐨勬儏鎰熷惈涔?- `capability_hint` 鏄腑鏂囷紝浣?`stage` 鏄嫳鏂囷紝璇箟涓嶄竴鑷?
v1.1 鐨勪紭鍖?4 璁捐浜?`trend_hint()` 鏂规硶锛屼絾 `to_summary()` 浠嶇劧璋冪敤 `self.trend()` 鑰屼笉鏄?`self.trend_hint()`銆?
**鏂规锛?* 閲嶆瀯 `to_summary()`锛岀粺涓€浣跨敤璇箟鍖栦腑鏂囨弿杩帮細

```python
def to_summary(self) -> dict[str, Any]:
    """鐢熸垚 context-pack-safe 鐨?Bond Summary锛堢粺涓€璇箟鍖栵紝涓嶅惈鏁板€硷級銆?""
    stage_zh = {
        STAGE_STRANGER:     "鍒濊瘑",
        STAGE_ACQUAINTANCE: "鐩歌瘑",
        STAGE_FRIEND:       "鏈嬪弸",
        STAGE_CLOSE_FRIEND: "鎸氬弸",
        STAGE_CONFIDANT:    "鐭ュ繁",
        STAGE_SOULMATE:     "鐏甸瓊浼翠荆",
    }
    return {
        "stage": self.current_stage,                    # 淇濈暀鑻辨枃鏋氫妇锛堜緵绋嬪簭閫昏緫浣跨敤锛?        "stage_hint": stage_zh.get(                     # 馃啎 鏂板涓枃璇箟锛堜緵LLM浣跨敤锛?            self.current_stage, self.current_stage
        ),
        "trend": self.trend(),                          # 淇濈暀鑻辨枃鏋氫妇锛堜緵绋嬪簭閫昏緫浣跨敤锛?        "trend_hint": self._trend_hint(),               # 馃啎 鏂板涓枃璇箟锛堜緵LLM浣跨敤锛?        "capability_hint": self.capability_hint(),      # 宸叉湁涓枃鎻忚堪
    }

def _trend_hint(self) -> str:
    """灏嗚秼鍔胯浆涓鸿嚜鐒惰瑷€鎻愮ず锛堢粰 LLM 鐢級"""
    t = self.trend()
    stage = self.current_stage
    hints = {
        ("RISING",    STAGE_STRANGER):     "鍒濇鐩歌瘑锛屾劅瑙夎繕涓嶉敊",
        ("RISING",    STAGE_ACQUAINTANCE): "鍏崇郴鍦ㄥ崌娓╋紝瓒婃潵瓒婄啛鎮?,
        ("RISING",    STAGE_FRIEND):       "鍙嬭皧鍦ㄥ姞娣憋紝浜掑姩瓒婃潵瓒婂",
        ("RISING",    STAGE_CLOSE_FRIEND): "鍏崇郴瓒婃潵瓒婁翰瀵?,
        ("RISING",    STAGE_CONFIDANT):    "褰兼瓒婃潵瓒婁俊浠?,
        ("RISING",    STAGE_SOULMATE):     "鐏甸瓊濂戝悎锛岃秺鏉ヨ秺娣?,
        ("DECLINING", STAGE_FRIEND):       "鏈€杩戜簰鍔ㄥ皯浜嗕簺锛屾湁浜涚枏杩?,
        ("DECLINING", STAGE_CLOSE_FRIEND): "鍏崇郴鏈変簺闄嶆俯锛岄渶瑕佸鑱旂郴",
        ("DECLINING", STAGE_CONFIDANT):    "鎰熻鏈変簺璺濈锛岄渶瑕侀噸鏂版媺杩?,
        ("STABLE",    STAGE_STRANGER):     "杩樺湪鐩镐簰浜嗚В涓?,
        ("STABLE",    STAGE_FRIEND):       "鍏崇郴绋冲畾锛屾棩甯搁櫔浼?,
        ("STABLE",    STAGE_SOULMATE):     "榛樺鍗佽冻锛屾棤闇€澶氳█",
    }
    return hints.get((t, stage), "鍏崇郴姝ｅ湪鍙戝睍涓?)
```

**棰勬湡鏁堟灉锛?* LLM 鍦?`bond_summary` 妲戒綅涓悓鏃剁湅鍒?`stage_hint="鏈嬪弸"` 鍜?`trend_hint="鍙嬭皧鍦ㄥ姞娣憋紝浜掑姩瓒婃潵瓒婂"`锛岃涔夊紩瀵兼晥鏋滃ぇ骞呮彁鍗囷紝涓嶅啀闇€瑕佽嚜宸辨帹鏂嫳鏂囨灇涓剧殑鍚箟銆?
---

### 馃煝 娣卞寲浼樺寲22锛圥1锛夛細`build_governance_snapshot()` 涓嶄紶閫?`hard_state_summary.mood_state` 缁?`GateSnapshot`

**鐩爣鏂囦欢锛?* `host_brain/core/metabolism.py` + `host_brain/governor/rules.py`

**绮捐鍙戠幇锛?*

`build_governance_snapshot()` 鐢熸垚鐨勫揩鐓т腑鏈?`hard_state_summary.mood_state`锛屼絾 `_normalize_snapshot()` 鍦ㄦ瀯寤?`GateSnapshot` 鏃?*涓嶈鍙?`hard_state_summary`**锛屽彧璇诲彇椤跺眰瀛楁锛?
```python
# _normalize_snapshot() 璇诲彇鐨勫瓧娈碉紙褰撳墠锛?trust_level = raw.get("trust_level", 50)       # 椤跺眰
energy_level = raw.get("energy_level", 60)     # 椤跺眰
sys_mode = raw.get("sys_mode", "normal")       # 椤跺眰
# ...

# hard_state_summary 涓殑 mood_state 瀹屽叏琚拷鐣ワ紒
# raw["hard_state_summary"]["mood_state"] 鈫?姘歌繙璇讳笉鍒?```

杩欏鑷达細
1. `GateSnapshot.ai_mood_state`锛堜紭鍖?9鏂板锛夊鏋滀粠椤跺眰璇诲彇锛屼細璇诲埌 `sys_mode`锛堢郴缁熸ā寮忥級锛岃€屼笉鏄?`mood_state`锛堟儏鎰熺姸鎬侊級
2. `hard_state_summary` 涓簿蹇冪淮鎶ょ殑 `mood_state` 瀵?Governor 闂ㄦ帶瀹屽叏閫忔槑

**鏂规锛?* 鍦?`_normalize_snapshot()` 涓鍔犲 `hard_state_summary` 鐨勮鍙栵紝骞跺湪 `build_governance_snapshot()` 涓皢 `mood_state` 鎻愬崌鍒伴《灞傦細

```python
# metabolism.py锛歜uild_governance_snapshot() 鎵╁睍
def build_governance_snapshot(
    state: HardState,
    *,
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    # ... 鍘熸湁閫昏緫 ...
    return {
        "trust_level": trust,
        "energy_level": int(state.energy_level),
        "sys_mode": str(state.sys_mode),
        "mood_state": str(state.mood_state),    # 馃啎 鎻愬崌鍒伴《灞傦紙渚?GateSnapshot 璇诲彇锛?        "rate_limit_hit": rate_limit_hit,
        "scope": scope,
        "scope_allowlist": list(allowlist),
        "safety_flag": safety_flag,
        "airlock_blocked": airlock_blocked,
        "hard_state_summary": {
            "energy_bucket": energy_bucket(state.energy_level),
            "mood_state": state.mood_state,
            "sys_mode": state.sys_mode,
            "safety_flag": safety_flag,
            "last_event_id": state.last_event_id,
        },
    }

# rules.py锛歘normalize_snapshot() 澧炲姞 mood_state 璇诲彇
def _normalize_snapshot(raw: dict[str, Any]) -> GateSnapshot:
    # ... 鍘熸湁瀛楁 ...
    # 馃啎 mood_state 璇诲彇锛堜紭鍏堥《灞傦紝鍥為€€鍒?hard_state_summary锛?    hard_summary = raw.get("hard_state_summary", {})
    ai_mood_state = str(
        raw.get("mood_state")                           # 椤跺眰锛堜紭鍏堬級
        or hard_summary.get("mood_state", "steady")    # hard_state_summary锛堝洖閫€锛?    ).lower()
    return GateSnapshot(
        # ... 鍘熸湁瀛楁 ...
        ai_mood_state=ai_mood_state,
    )
```

**棰勬湡鏁堟灉锛?* `mood_state` 淇℃伅涓嶅啀鍦?`build_governance_snapshot()` 鈫?`_normalize_snapshot()` 鐨勪紶閫掕繃绋嬩腑涓㈠け锛孯8 闂ㄦ帶鑳藉姝ｇ‘璇诲彇 AI 褰撳墠鐨勬儏鎰熺姸鎬併€?
---

### 馃數 娣卞寲浼樺寲23锛圥2锛夛細`validate_prompt_context()` 瀵?`bond_summary` 鐨?`stage`/`trend` 瀛楁鏃犺涔夋牎楠?
**鐩爣鏂囦欢锛?* `host_brain/core/prompt_context.py`

**绮捐鍙戠幇锛?*

`_validate_bond_summary_slot()` 鍙仛涓や欢浜嬶細
1. 妫€鏌ユ槸鍚﹀寘鍚暟鍊硷紙`CP_BOND_NUMERIC_FORBIDDEN`锛?2. 妫€鏌ユ槸鍚︽湁鏈煡瀛楁锛坄CP_BOND_SUMMARY_FIELD_INVALID`锛?
浣嗗 `stage` 鍜?`trend` 瀛楁鐨?*鏋氫妇鍊煎悎娉曟€?*瀹屽叏涓嶆牎楠岋細

```python
# 褰撳墠鏍￠獙锛堜笉瀹屾暣锛?def _validate_bond_summary_slot(bond_summary, result):
    for value in _iter_values(bond_summary):
        if isinstance(value, (int, float)):
            result.add(CP_BOND_NUMERIC_FORBIDDEN, ...)  # 鍙鏌ユ暟鍊?    unknown = sorted(set(bond_summary.keys()) - set(BOND_SUMMARY_ALLOWED_KEYS))
    if unknown:
        result.add(CP_BOND_SUMMARY_FIELD_INVALID, ...)  # 鍙鏌ユ湭鐭ュ瓧娈?    # 鈫?娌℃湁妫€鏌?stage 鏄惁鏄悎娉曠殑 Bond 闃舵鏋氫妇锛?    # 鈫?娌℃湁妫€鏌?trend 鏄惁鏄?RISING/STABLE/DECLINING锛?```

杩欐剰鍛崇潃 `bond_summary["stage"] = "BEST_FRIEND"` 鎴?`bond_summary["trend"] = "GOING_UP"` 杩欐牱鐨勯潪娉曞€间細鐩存帴娉ㄥ叆 LLM锛屽鑷磋涔夋贩涔便€?
**鏂规锛?* 鍦?`_validate_bond_summary_slot()` 涓鍔犳灇涓惧€兼牎楠岋細

```python
# 鍚堟硶鐨?Bond 闃舵鏋氫妇锛堜笌 bond_stage_engine.py 淇濇寔涓€鑷达級
VALID_BOND_STAGES = frozenset({
    "STRANGER", "ACQUAINTANCE", "FRIEND",
    "CLOSE_FRIEND", "CONFIDANT", "SOULMATE",
})
VALID_BOND_TRENDS = frozenset({"RISING", "STABLE", "DECLINING"})

def _validate_bond_summary_slot(
    bond_summary: dict[str, Any], result: PromptContextValidationResult
) -> None:
    # ... 鍘熸湁鏁板€兼鏌?...
    # ... 鍘熸湁鏈煡瀛楁妫€鏌?...

    # 馃啎 鏋氫妇鍊煎悎娉曟€ф牎楠?    stage = bond_summary.get("stage")
    if stage is not None and str(stage) not in VALID_BOND_STAGES:
        result.add(
            CP_BOND_SUMMARY_FIELD_INVALID,
            f"bond_summary.stage invalid enum value: {stage!r}, "
            f"valid values: {sorted(VALID_BOND_STAGES)}",
            slot="bond_summary",
        )

    trend = bond_summary.get("trend")
    if trend is not None and str(trend) not in VALID_BOND_TRENDS:
        result.add(
            CP_BOND_SUMMARY_FIELD_INVALID,
            f"bond_summary.trend invalid enum value: {trend!r}, "
            f"valid values: {sorted(VALID_BOND_TRENDS)}",
            slot="bond_summary",
        )
```

**棰勬湡鏁堟灉锛?* 闈炴硶鐨?Bond 闃舵/瓒嬪娍鏋氫妇鍊煎湪娉ㄥ叆 LLM 鍓嶈鎷︽埅锛岄槻姝㈣涔夋贩涔便€?
---

### 馃煟 娣卞寲浼樺寲24锛圥2锛夛細`build_fallback_output()` 鐨勯檷绾у洖澶嶆棤鎯呮劅鎰熺煡鈥斺€旈檷绾ф椂 AI 鎯呮劅鏂眰

**鐩爣鏂囦欢锛?* `host_brain/core/qa.py`

**绮捐鍙戠幇锛?*

褰?QA 鏍￠獙澶辫触瑙﹀彂闄嶇骇鏃讹紝`build_fallback_output()` 鐢熸垚鐨勫洖澶嶆槸锛?
```python
def build_fallback_output(*, reason_code: str, stage: str) -> dict[str, Any]:
    return {
        "narrative": {
            "text": "褰撳墠鏃犳硶纭鎵ц缁撴灉锛屾垜浼氬厛淇濇寔鑽夋鍥炲骞剁瓑寰呭悗缁不鐞嗐€?,
        },
        "intent_draft": {},
        "meta": {
            "draft_only": True,
            "degraded": True,
            "reason_code": reason_code,
            "stage": stage,
        },
    }
```

**闂锛?*
1. 闄嶇骇鍥炲鏄?*鍥哄畾鏂囨湰**锛屽畬鍏ㄤ笉鎰熺煡褰撳墠鎯呮劅鐘舵€佲€斺€擿engaged` 鐘舵€佷笅闄嶇骇鍜?`tired` 鐘舵€佷笅闄嶇骇锛岀敤鎴风湅鍒扮殑鏄畬鍏ㄧ浉鍚岀殑鍐峰啺鍐扮殑绯荤粺鎻愮ず
2. 闄嶇骇鍥炲娌℃湁 `emotion_tag` 瀛楁锛屼細瀵艰嚧鍚庣画 `extract_emotion_tag()` 杩斿洖 `NEUTRAL`锛屾儏鎰熺姸鎬佽寮哄埗閲嶇疆
3. 闄嶇骇鏃?AI 鐨勬儏鎰熺姸鎬佸簲璇ユ槸"鏈変簺鍥版儜/鎶辨瓑"锛岃€屼笉鏄満姊扮殑绯荤粺鎻愮ず

**鏂规锛?* 涓?`build_fallback_output()` 澧炲姞鎯呮劅鎰熺煡鍙傛暟锛?
```python
# 闄嶇骇鍥炲鐨勬儏鎰熸劅鐭ユ枃鏈槧灏?_FALLBACK_TEXT_BY_MOOD: dict[str, tuple[str, str]] = {
    # mood_state 鈫?(fallback_text, emotion_tag)
    "engaged":  ("鍟婏紝鍒氭墠濂藉儚鍑轰簡鐐瑰皬闂锛岃鎴戦噸鏂版暣鐞嗕竴涓嬫€濊矾锝?, "THINKING"),
    "steady":   ("鍒氭墠閬囧埌浜嗕竴鐐圭姸鍐碉紝鎴戝厛缂撲竴缂撳啀鍥炲浣犮€?, "THINKING"),
    "calm":     ("鍡€︹€﹀垰鎵嶆湁浜涘湴鏂规病澶勭悊濂斤紝绋嶇瓑涓€涓嬨€?, "CALM"),
    "tired":    ("鏈夌偣绱簡锛屽垰鎵嶆病鑳藉ソ濂藉洖搴旓紝鎶辨瓑鈥︹€?, "SAD"),
    "critical": ("鈥︹€︾幇鍦ㄧ姸鎬佷笉澶ソ锛屽厛浼戞伅涓€涓嬨€?, "SAD"),
}

def build_fallback_output(
    *,
    reason_code: str,
    stage: str,
    mood_state: str = "steady",   # 馃啎 鏂板鍙傛暟
) -> dict[str, Any]:
    """鐢熸垚鎯呮劅鎰熺煡鐨勯檷绾у洖澶嶏紙涓嶅啀鏄浐瀹氭枃鏈級銆?""
    text, emotion_tag = _FALLBACK_TEXT_BY_MOOD.get(
        str(mood_state).lower(),
        _FALLBACK_TEXT_BY_MOOD["steady"],
    )
    return {
        "narrative": {
            "text": text,
            "emotion_tag": emotion_tag,   # 馃啎 闄嶇骇鏃朵篃鏈夋儏鎰熸爣绛?        },
        "intent_draft": {},
        "meta": {
            "draft_only": True,
            "degraded": True,
            "reason_code": reason_code,
            "stage": stage,
        },
    }
```

**璋冪敤鏂逛慨鏀癸細** `_downgrade()` 鍑芥暟闇€瑕佹帴鏀跺苟浼犻€?`mood_state` 鍙傛暟銆?
**棰勬湡鏁堟灉锛?* 鍗充娇鍦?QA 闄嶇骇鏃讹紝AI 鐨勬儏鎰熺姸鎬佷篃鑳借嚜鐒舵祦闇诧紝涓嶄細鍑虹幇"鎯呮劅鏂眰"鈥斺€旂敤鎴蜂笉浼氱獊鐒剁湅鍒颁竴鏉″喎鍐板啺鐨勭郴缁熸彁绀烘墦鏂儏鎰熸祦銆?
---

## 鍗佷簲銆佺鍥涜疆浼樺寲鐨勬牳蹇冧环鍊兼€荤粨

绗洓杞紭鍖栬仛鐒︿簬**淇鍔熻兘涔嬮棿鐨勮繛鎺ユ柇瑁?*鈥斺€旈偅浜涘崟鐙湅姣忎釜鍔熻兘閮芥槸瀹屾暣鐨勶紝浣嗙粍鍚堣捣鏉ュ嵈鏃犳硶鍗忓悓宸ヤ綔鐨?鏋舵瀯鏂眰"锛?
| 浼樺寲椤?| 鍙戠幇鐨?鏋舵瀯鏂眰" | 鏂眰浣嶇疆 | 淇鏂规 |
|--------|----------------|---------|---------|
| **浼樺寲19** 馃敶 | R8 闂ㄦ帶鏃犳硶钀藉湴锛歚GateSnapshot` 涓嶅惈鎯呮劅瀛楁锛屾儏鎰熸暟鎹 `_normalize_snapshot()` 闈欓粯涓㈠純 | `rules.py` 鈫?`decision.py` | 鎵╁睍 `GateSnapshot`锛宍_normalize_snapshot()` 璇诲彇鎯呮劅瀛楁 |
| **浼樺寲20** 馃煚 | `MOOD_EMOTION_CONSTRAINTS` 姘歌繙涓嶇敓鏁堬細`extract_emotion_tag()` 浠庝笉浼?`mood_state` | `qa.py` 鈫?`metabolism.py` | 涓?`extract_emotion_tag()` 澧炲姞 `mood_state` 鍙傛暟 |
| **浼樺寲21** 馃煛 | `to_summary()` 娣峰悎鑻辨枃鏋氫妇鍜屼腑鏂囨弿杩帮紝LLM 璇箟寮曞鏁堟灉宸?| `bond_stage_engine.py` 鈫?LLM | 澧炲姞 `stage_hint`/`trend_hint` 涓枃璇箟瀛楁 |
| **浼樺寲22** 馃煝 | `mood_state` 鍦ㄥ揩鐓т紶閫掍腑涓㈠け锛歚build_governance_snapshot()` 涓嶅皢 `mood_state` 鎻愬崌鍒伴《灞?| `metabolism.py` 鈫?`rules.py` | 灏?`mood_state` 鎻愬崌鍒板揩鐓ч《灞?|
| **浼樺寲23** 馃數 | `bond_summary` 鏋氫妇鍊兼棤鏍￠獙锛氶潪娉?`stage`/`trend` 鍊煎彲鐩存帴娉ㄥ叆 LLM | `prompt_context.py` | 澧炲姞鏋氫妇鍊煎悎娉曟€ф牎楠?|
| **浼樺寲24** 馃煟 | 闄嶇骇鏃舵儏鎰熸柇灞傦細`build_fallback_output()` 鍥哄畾鏂囨湰锛屾儏鎰熺姸鎬佽寮哄埗閲嶇疆涓?`NEUTRAL` | `qa.py` 鈫?鎯呮劅娴?| 闄嶇骇鍥炲鎰熺煡 `mood_state`锛屼繚鎸佹儏鎰熻繛缁€?|

---

## 鍗佸叚銆佸畬鏁翠紭鍖栧叏鏅浘锛坴1.3锛?4椤癸級

### 鎸夊眰娆″垎绫?
```
鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?鈹?                   鍩虹璁炬柦灞傦紙蹇呴』鍏堝仛锛?                         鈹?鈹? OPT16: OPTIONAL_SLOTS鎵╁睍    OPT17: BondEventPayload鏍囧噯鍖?     鈹?鈹? OPT18: state_age_hint        OPT19: GateSnapshot鎯呮劅瀛楁        鈹?鈹? OPT22: mood_state鎻愬崌鍒伴《灞? OPT23: bond_summary鏋氫妇鏍￠獙        鈹?鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?         鈫?鍩虹璁炬柦灏辩华鍚?鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?鈹?                   鎯呮劅鍐呮牳灞傦紙P0锛?                              鈹?鈹? OPT1: EmotionVector          OPT13: event_kind璇箟鎵╁睍          鈹?鈹? OPT6: emotion_tag鐢熸垚绛栫暐    OPT20: extract_emotion_tag淇     鈹?鈹? OPT7: Bond璐ㄩ噺鍒嗙骇           OPT14: trend_hint璇箟鍖?           鈹?鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?         鈫?鎯呮劅鍐呮牳灏辩华鍚?鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?鈹?                   璁板繂灞傦紙P1锛?                                  鈹?鈹? OPT3: 鎯呮劅璁板繂鏍囨敞           OPT15: 璁板繂鏂伴矞搴﹁“鍑?             鈹?鈹? OPT9: 鎯呮劅鏃堕棿绾块噸寤?                                           鈹?鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?鈹?                   鍏崇郴灞傦紙P1锛?                                  鈹?鈹? OPT2: Bond璐ㄦ劅灞?            OPT11: Bond瑁傜棔淇                鈹?鈹? OPT8: Governor R8闂ㄦ帶        OPT21: to_summary璇箟鍖?           鈹?鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?         鈫?鏍稿績灞傚氨缁悗
鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?鈹?                   杈撳嚭灞傦紙P2锛?                                  鈹?鈹? OPT4: emotion_pulse妲戒綅      OPT5: 涓诲姩瑙﹀彂鍣?                  鈹?鈹? OPT10: LLM鐢熸垚鍙傛暟           OPT12: 鎯呮劅鍥炲０                    鈹?鈹? OPT24: 闄嶇骇鎯呮劅鎰熺煡                                             鈹?鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?```

### 瀹炴柦璺嚎鍥撅紙鎸変紭鍏堢骇锛?
| 鎵规 | 浼樺寲椤?| 鏍稿績鐩爣 |
|------|--------|---------|
| **Batch 1锛堝熀纭€璁炬柦锛?* | OPT16, OPT17, OPT18, OPT19, OPT22, OPT23 | 鎵撻€氭暟鎹祦锛屼慨澶嶆灦鏋勬柇灞?|
| **Batch 2锛堟儏鎰熷唴鏍革級** | OPT1, OPT13, OPT20, OPT6, OPT7, OPT14 | 寤虹珛杩炵画鎯呮劅鐘舵€佹満 |
| **Batch 3锛堣蹇?鍏崇郴锛?* | OPT3, OPT15, OPT9, OPT2, OPT11, OPT8, OPT21 | 鎯呮劅璁板繂+鍏崇郴娓╁害 |
| **Batch 4锛堣緭鍑哄寮猴級** | OPT4, OPT5, OPT10, OPT12, OPT24 | 鎯呮劅鎰熺煡鐨勮緭鍑哄眰 |

---

## 鍗佷竷銆佺浜旇疆娣卞寲浼樺寲锛氳法妯″潡鎯呮劅鐩插尯

> **v1.4 鏂板**锛?026-03-05锛夈€傛湰杞熀浜庡 `memory/episodic.py`銆乣bond/bond_dynamics.py`銆乣bond/summarizer.py`銆乣memory/context_compressor.py`銆乣memory/long_term_distiller.py`銆乣scheduler/scheduler.py` 鐨勫畬鏁撮€愯绮捐锛屽彂鐜颁簡鍓嶅洓杞畬鍏ㄦ湭瑙﹀強鐨?**6 涓法妯″潡鎯呮劅鐩插尯**鈥斺€旇繖浜涢棶棰樺垎甯冨湪璁板繂灞傘€佸叧绯诲眰銆佽皟搴﹀眰锛屾槸鎯呮劅淇℃伅鍦?瀛樺偍鈫掕捀棣忊啋璋冨害鈫掕緭鍑?鍏ㄩ摼璺腑鐨勭郴缁熸€х己澶便€?
---

### 馃敶 娣卞寲浼樺寲25锛圥0锛夛細`EpisodicRecord` 鏃犳儏鎰熸爣娉ㄥ瓧娈碘€斺€旀儏鎰熻蹇嗘棤娉曡鎯呮劅妫€绱?
**鐩爣鏂囦欢锛?* `host_brain/memory/episodic.py`

**绮捐鍙戠幇锛?*

`EpisodicRecord` 鏁版嵁绫荤殑瀛楁锛?
```python
@dataclass
class EpisodicRecord:
    memory_id: str
    text: str
    ts_ms: int
    text_hash: str
    decay_profile_id: str
    evidence_refs: list[str]
    visibility: str
    embedding_model_id: str
    merged_into: str | None = None
    deleted_at: int | None = None
    # 鈫?瀹屽叏娌℃湁鎯呮劅鐩稿叧瀛楁锛?```

`write_memory()` 鐨勫弬鏁颁篃娌℃湁鎯呮劅瀛楁锛?
```python
def write_memory(
    self,
    *,
    text: str,
    evidence_refs: list[str],
    request_id: str | None = None,
    intent_id: str | None = None,
    decay_profile_id: str = "default",
    visibility: str = "internal",
    ts_ms: int | None = None,
    # 鈫?娌℃湁 emotion_tag銆乵ood_state銆乥ond_stage 绛夋儏鎰熷瓧娈?) -> MemoryOpResult:
```

Chroma 鍚戦噺搴撶殑 `metadata` 涔熶笉鍚儏鎰熷瓧娈碉細

```python
self.chroma.upsert(
    memory_id=memory_id,
    text=normalized_text,
    embedding=embedding,
    metadata={
        "ts_ms": ts_ms,
        "text_hash": text_hash,
        "visibility": record.visibility,
        "evidence_refs": list(evidence_refs),
        "embedding_model_id": model_id,
        # 鈫?娌℃湁 emotion_tag锛佹棤娉曟寜鎯呮劅杩囨护妫€绱?    },
)
```

杩欐剰鍛崇潃锛?1. 鎵€鏈夋儏鑺傝蹇嗛兘鏄?鎯呮劅鐩?鐨勨€斺€旀棤娉曠煡閬撹繖鏉¤蹇嗘槸鍦ㄤ粈涔堟儏鎰熺姸鎬佷笅浜х敓鐨?2. `recall()` 鍙兘鍋氳涔夋绱紝鏃犳硶鍋?妫€绱㈡垜浠紑蹇冩椂鐨勮蹇?杩欐牱鐨勬儏鎰熻繃婊?3. 浼樺寲3锛堟儏鎰熻蹇嗘爣娉級鍜屼紭鍖?锛堟儏鎰熸椂闂寸嚎閲嶅缓锛夌殑瀹炵幇鍓嶆彁瀹屽叏缂哄け

**鏂规锛?* 涓?`EpisodicRecord` 澧炲姞鎯呮劅鏍囨敞瀛楁锛屽苟鍦?`write_memory()` 鍜?Chroma metadata 涓紶閫掞細

```python
@dataclass
class EpisodicRecord:
    memory_id: str
    text: str
    ts_ms: int
    text_hash: str
    decay_profile_id: str
    evidence_refs: list[str]
    visibility: str
    embedding_model_id: str
    merged_into: str | None = None
    deleted_at: int | None = None
    # 馃啎 鎯呮劅鏍囨敞瀛楁锛坴1.4 鏂板锛?    emotion_tag: str = "NEUTRAL"        # 璁板繂浜х敓鏃剁殑鎯呮劅鏍囩
    mood_state: str = "steady"          # 璁板繂浜х敓鏃剁殑 AI mood_state
    bond_stage: str = "STRANGER"        # 璁板繂浜х敓鏃剁殑 Bond 闃舵
    emotional_intensity: float = 0.0    # 鎯呮劅寮哄害锛?.0~1.0锛岀敤浜庤“鍑忔潈閲嶏級

def write_memory(
    self,
    *,
    text: str,
    evidence_refs: list[str],
    request_id: str | None = None,
    intent_id: str | None = None,
    decay_profile_id: str = "default",
    visibility: str = "internal",
    ts_ms: int | None = None,
    # 馃啎 鎯呮劅鏍囨敞鍙傛暟
    emotion_tag: str = "NEUTRAL",
    mood_state: str = "steady",
    bond_stage: str = "STRANGER",
    emotional_intensity: float = 0.0,
) -> MemoryOpResult:
    # ...
    # Chroma metadata 澧炲姞鎯呮劅瀛楁
    self.chroma.upsert(
        memory_id=memory_id,
        text=normalized_text,
        embedding=embedding,
        metadata={
            "ts_ms": ts_ms,
            "text_hash": text_hash,
            "visibility": record.visibility,
            "evidence_refs": list(evidence_refs),
            "embedding_model_id": model_id,
            "emotion_tag": emotion_tag,           # 馃啎
            "mood_state": mood_state,             # 馃啎
            "bond_stage": bond_stage,             # 馃啎
            "emotional_intensity": emotional_intensity,  # 馃啎
        },
    )
```

**鏂板鎯呮劅妫€绱㈡帴鍙ｏ細**

```python
def recall_by_emotion(
    self,
    *,
    query_text: str,
    emotion_tag: str | None = None,
    bond_stage: str | None = None,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """鎸夋儏鎰熸爣绛捐繃婊ょ殑鎯呮劅璁板繂妫€绱€?""
    embedding, _ = embed_text(query_text)
    results = self.chroma.search(query_embedding=embedding, top_k=top_k * 3)
    # 鎸夋儏鎰熻繃婊?    filtered = [
        r for r in results
        if (emotion_tag is None or r.get("metadata", {}).get("emotion_tag") == emotion_tag)
        and (bond_stage is None or r.get("metadata", {}).get("bond_stage") == bond_stage)
    ]
    return filtered[:top_k]
```

**棰勬湡鏁堟灉锛?* 鎯呰妭璁板繂浠?鎯呮劅鐩?鍙樹负"鎯呮劅鎰熺煡"锛屾敮鎸?妫€绱㈡垜浠紑蹇冩椂鐨勮蹇?銆?妫€绱㈡寶鍙嬮樁娈电殑璁板繂"绛夋儏鎰熺淮搴︾殑妫€绱紝鏄紭鍖?鍜屼紭鍖?鐨勫繀瑕佸熀纭€璁炬柦銆?
---

### 馃煚 娣卞寲浼樺寲26锛圥0锛夛細`BondDynamicsWriter.apply_update()` 鐨?`payload` 鏃犳儏鎰熷瓧娈碘€斺€擝ond 浜嬩欢鎯呮劅鐩?
**鐩爣鏂囦欢锛?* `host_brain/bond/bond_dynamics.py`

**绮捐鍙戠幇锛?*

`apply_update()` 鐨?`payload` 鍙傛暟鏄?`dict[str, Any] | None`锛屽畬鍏ㄨ嚜鐢辨牸寮忥紝娌℃湁浠讳綍鎯呮劅瀛楁鐨勭害瀹氾細

```python
def apply_update(
    self,
    *,
    request_id: str,
    intent_id: str,
    session_id: str,
    seq: int,
    user_id: str,
    topic_id: str,
    capability_hint: str,
    evidence_refs: list[str],
    payload: dict[str, Any] | None = None,   # 鈫?鑷敱鏍煎紡锛屾棤鎯呮劅绾﹀畾
    update_kind: str = "INTERACTION",
    event_id: str | None = None,
    ts_ms: int | None = None,
) -> BondUpdateResult:
```

鍐欏叆 Neo4j 鐨勪簨浠朵篃涓嶅惈鎯呮劅瀛楁锛?
```python
written = self.neo4j_client.write_bond_event(
    writer_id=BOND_WRITER_ID,
    event_id=event_key,
    user_id=str(user_id),
    session_id=str(session_id),
    topic_id=str(topic_id),
    capability_hint=str(capability_hint),
    evidence_refs=refs,
    payload=dict(payload or {}),   # 鈫?payload 鍐呭瀹屽叏涓嶇‘瀹?    ts_ms=now_value,
)
```

杩欐剰鍛崇潃锛?1. Bond 浜嬩欢鍥捐氨涓病鏈夋儏鎰熺淮搴︹€斺€旀棤娉曠煡閬?杩欐浜掑姩鏄湪浠€涔堟儏鎰熺姸鎬佷笅鍙戠敓鐨?
2. `BondStageEngine.apply_event()` 鐨勭Н鍒嗚绠椾笉鎰熺煡鎯呮劅鈥斺€擿EXCITED` 鐘舵€佷笅鐨勪簰鍔ㄥ拰 `SAD` 鐘舵€佷笅鐨勪簰鍔ㄥ緱鍒嗙浉鍚?3. 鏃犳硶鍋?鎯呮劅鍔犳潈鐨?Bond 绉垎"鈥斺€旀繁搴︽儏鎰熶簰鍔ㄥ簲璇ユ瘮鏅€氫簰鍔ㄨ础鐚洿澶?Bond 绉垎

**鏂规锛?* 涓?`apply_update()` 澧炲姞鎯呮劅鍙傛暟锛屽苟鍦?`BondStageEngine.apply_event()` 涓敮鎸佹儏鎰熷姞鏉冿細

```python
def apply_update(
    self,
    *,
    request_id: str,
    intent_id: str,
    session_id: str,
    seq: int,
    user_id: str,
    topic_id: str,
    capability_hint: str,
    evidence_refs: list[str],
    payload: dict[str, Any] | None = None,
    update_kind: str = "INTERACTION",
    event_id: str | None = None,
    ts_ms: int | None = None,
    # 馃啎 鎯呮劅鍙傛暟
    emotion_tag: str = "NEUTRAL",
    mood_state: str = "steady",
    emotional_intensity: float = 0.0,
) -> BondUpdateResult:
    # ...
    # 灏嗘儏鎰熷瓧娈垫敞鍏?payload
    enriched_payload = dict(payload or {})
    enriched_payload.update({
        "emotion_tag": emotion_tag,
        "mood_state": mood_state,
        "emotional_intensity": round(float(emotional_intensity), 4),
    })
    # ...
    # BondStageEngine 浼犻€掓儏鎰熷己搴︼紙鐢ㄤ簬鍔犳潈绉垎锛?    if self._stage_engine is not None:
        stage_transition = self._stage_engine.apply_event(
            user_id=str(user_id),
            update_kind=str(update_kind),
            evidence_refs=refs,
            request_id=request_id,
            ts_ms=now_value,
            emotional_intensity=emotional_intensity,  # 馃啎 鎯呮劅鍔犳潈
        )
```

**鎯呮劅鍔犳潈绉垎瑙勫垯锛堝湪 `BondStageEngine` 涓疄鐜帮級锛?*

```python
# 鎯呮劅寮哄害鍔犳潈绯绘暟
EMOTION_INTENSITY_MULTIPLIER = {
    "EXCITED":  1.5,   # 鍏村鐘舵€佺殑浜掑姩璐＄尞 1.5x 绉垎
    "HAPPY":    1.3,   # 寮€蹇冪姸鎬佽础鐚?1.3x
    "NEUTRAL":  1.0,   # 涓€х姸鎬佽础鐚?1.0x锛堝熀鍑嗭級
    "CALM":     0.9,   # 骞抽潤鐘舵€佽础鐚?0.9x
    "SAD":      0.7,   # 鎮蹭激鐘舵€佽础鐚?0.7x锛堟儏鎰熶綆钀芥椂浜掑姩璐ㄩ噺涓嬮檷锛?    "ANGRY":    0.5,   # 鎰ゆ€掔姸鎬佽础鐚?0.5x锛堣礋闈㈡儏鎰熷鍏崇郴鏈夋崯鑰楋級
}
```

**棰勬湡鏁堟灉锛?* Bond 绉垎浠?浜掑姩娆℃暟璁℃暟"鍗囩骇涓?鎯呮劅鍔犳潈鐨勫叧绯昏川閲忚瘎浼?锛屾繁搴︽儏鎰熶簰鍔紙`EXCITED`/`HAPPY`锛夋瘮鏅€氫簰鍔ㄨ础鐚洿澶?Bond 绉垎锛屽叧绯诲彂灞曟洿鐪熷疄銆?
---

### 馃煛 娣卞寲浼樺寲27锛圥1锛夛細`bond/summarizer.py` 鐨?`derive_bond_summary()` 浠?event 璇诲彇 `stage`/`trend`鈥斺€斾絾 Bond 浜嬩欢涓嶅惈杩欎袱涓瓧娈?
**鐩爣鏂囦欢锛?* `host_brain/bond/summarizer.py`

**绮捐鍙戠幇锛?*

`derive_bond_summary()` 浠?`event` 瀛楀吀涓鍙?`stage` 鍜?`trend`锛?
```python
def derive_bond_summary(event: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "stage": _as_text_bucket(event.get("stage"), default=DEFAULT_STAGE),
        "trend": _as_text_bucket(event.get("trend"), default=DEFAULT_TREND),
        "capability_hint": _as_text_bucket(
            event.get("capability_hint"),
            default=DEFAULT_CAPABILITY_HINT,
        ),
        "event_id": _as_optional_text(event.get("event_id")),
        "evidence_refs": _normalize_evidence_refs(event.get("evidence_refs")),
    }
```

浣?`BondDynamicsWriter.apply_update()` 鍐欏叆 Neo4j 鐨勪簨浠剁粨鏋勬槸锛?
```python
event = {
    "request_id": ...,
    "intent_id": ...,
    "session_id": ...,
    "seq": ...,
    "event_id": ...,
    "user_id": ...,
    "topic_id": ...,
    "capability_hint": ...,   # 鈫?鏈?capability_hint
    "update_kind": ...,
    "evidence_refs": ...,
    "payload": ...,
    "neo4j_event": ...,
    "stage": "S_BOND_UPDATE",  # 鈫?杩欐槸娴佹按绾块樁娈靛悕锛屼笉鏄?Bond 闃舵锛?    "status": ...,
    "reason_code": ...,
    "ts_ms": ...,
    "stage_transition": {
        "from_stage": ...,
        "to_stage": ...,       # 鈫?Bond 闃舵鍦ㄨ繖閲岋紝涓嶅湪椤跺眰锛?        "transition_status": ...,
        "score_delta": ...,
        "total_score": ...,
    },
}
```

**闂锛?*
1. `event["stage"]` 鏄?`"S_BOND_UPDATE"`锛堟祦姘寸嚎闃舵鍚嶏級锛屼笉鏄?`"FRIEND"` 杩欐牱鐨?Bond 闃舵
2. `event["trend"]` 鏍规湰涓嶅瓨鍦紝`derive_bond_summary()` 浼氳繑鍥?`DEFAULT_TREND = "STABLE"`
3. 鐪熸鐨?Bond 闃舵鍦?`event["stage_transition"]["to_stage"]` 閲岋紝浣?`derive_bond_summary()` 涓嶈杩欎釜瀛楁

杩欐剰鍛崇潃锛?*`derive_bond_summary()` 姘歌繙杩斿洖閿欒鐨?`stage`锛坄"S_BOND_UPDATE"`锛夊拰榛樿鐨?`trend`锛坄"STABLE"`锛?*锛屾敞鍏?LLM 鐨?Bond 淇℃伅瀹屽叏澶辩湡銆?
**鏂规锛?* 淇 `derive_bond_summary()` 鐨勫瓧娈佃鍙栭€昏緫锛?
```python
def derive_bond_summary(event: dict[str, Any] | None) -> dict[str, Any]:
    """浠?Bond 浜嬩欢璁板綍鏋勫缓 context-pack-safe 鐨?Bond Summary銆?
    淇锛欱ond 闃舵鍦?stage_transition.to_stage锛屼笉鍦ㄩ《灞?stage 瀛楁銆?    """
    if not isinstance(event, dict):
        event = {}

    # 馃啎 淇锛氫粠 stage_transition 璇诲彇鐪熷疄鐨?Bond 闃舵
    stage_transition = event.get("stage_transition") or {}
    bond_stage = (
        _as_text_bucket(stage_transition.get("to_stage"), default="")
        or _as_text_bucket(event.get("bond_stage"), default=DEFAULT_STAGE)
    )

    # 馃啎 淇锛氫粠 stage_transition 鎺ㄦ柇瓒嬪娍
    transition_status = str(stage_transition.get("transition_status") or "").upper()
    score_delta = int(stage_transition.get("score_delta") or 0)
    if transition_status == "PROMOTED":
        trend = "RISING"
    elif transition_status == "DEMOTED":
        trend = "DECLINING"
    elif score_delta > 0:
        trend = "RISING"
    elif score_delta < 0:
        trend = "DECLINING"
    else:
        trend = DEFAULT_TREND

    return {
        "stage": bond_stage,
        "trend": trend,
        "capability_hint": _as_text_bucket(
            event.get("capability_hint"),
            default=DEFAULT_CAPABILITY_HINT,
        ),
        "event_id": _as_optional_text(event.get("event_id")),
        "evidence_refs": _normalize_evidence_refs(event.get("evidence_refs")),
    }
```

**棰勬湡鏁堟灉锛?* `derive_bond_summary()` 缁堜簬鑳借繑鍥炴纭殑 Bond 闃舵鍜岃秼鍔匡紝娉ㄥ叆 LLM 鐨?Bond 淇℃伅涓嶅啀澶辩湡銆傝繖鏄暣涓?Bond 淇℃伅閾捐矾鐨勫叧閿慨澶嶃€?
---

### 馃煝 娣卞寲浼樺寲28锛圥1锛夛細`ContextCompressor._pre_compress_save()` 鍙繚瀛?assistant 杞鈥斺€旂敤鎴锋儏鎰熶俊鎭涓㈠純

**鐩爣鏂囦欢锛?* `host_brain/memory/context_compressor.py`

**绮捐鍙戠幇锛?*

`_pre_compress_save()` 鐨勮繃婊ら€昏緫锛?
```python
for idx, turn in enumerate(turns):
    role = str(turn.get("role") or "user")
    content = str(turn.get("content") or "").strip()
    if not content:
        continue
    # 浠呬繚瀛?assistant 鍥炲锛堝惈鏈夊疄璐ㄦ€т俊鎭殑杞锛?    if role != "assistant":
        continue   # 鈫?鐢ㄦ埛杞琚畬鍏ㄨ烦杩囷紒
    evidence_ref = f"compress://pre_save/{session_id or 'unknown'}/turn_{idx:03d}"
    try:
        self._episodic_writer(content, [evidence_ref])
        saved_count += 1
    except Exception:
        pass
```

**闂锛?*
1. 鐢ㄦ埛鐨勬儏鎰熻〃杈撅紙"鎴戜粖澶╁緢闅捐繃"銆?杩欒鎴戝緢寮€蹇?锛夎瀹屽叏涓㈠純锛屽彧淇濆瓨 AI 鐨勫洖澶?2. 鐢ㄦ埛鐨勯噸瑕侀檲杩帮紙"鎴戞槑澶╄鍘婚潰璇?銆?鎴戝濡堢敓鐥呬簡"锛変篃琚涪寮?3. 鍘嬬缉鍓嶄繚瀛樼殑璁板繂鏄?鍗曞０閬?鐨勨€斺€斿彧鏈?AI 鐨勫０闊筹紝娌℃湁鐢ㄦ埛鐨勫０闊?4. 杩欏鑷撮暱鏈熻蹇嗚捀棣忥紙`LongTermMemoryDistiller`锛夌殑鍘熸潗鏂欎弗閲嶅亸鏂?
**鏂规锛?* 鏀逛负淇濆瓨"楂樹环鍊艰疆娆?锛屽寘鎷敤鎴风殑鎯呮劅琛ㄨ揪鍜岄噸瑕侀檲杩帮細

```python
# 楂樹环鍊艰疆娆″垽鏂鍒?_EMOTION_KEYWORDS = frozenset({
    "闅捐繃", "寮€蹇?, "楂樺叴", "浼ゅ績", "鐢熸皵", "瀹虫€?, "鎷呭績", "鍏村",
    "鎰熻阿", "鎶辨瓑", "瀵逛笉璧?, "鍠滄", "璁ㄥ帉", "鐖?, "鎭?,
    "sad", "happy", "angry", "scared", "excited", "love", "hate",
})

def _is_high_value_turn(turn: dict[str, Any]) -> bool:
    """鍒ゆ柇鏄惁涓洪珮浠峰€艰疆娆★紙鍊煎緱鍦ㄥ帇缂╁墠淇濆瓨锛夈€?""
    role = str(turn.get("role") or "user")
    content = str(turn.get("content") or "").strip()
    if not content:
        return False
    # assistant 杞锛氬缁堜繚瀛橈紙鍘熸湁閫昏緫锛?    if role == "assistant":
        return True
    # user 杞锛氬彧淇濆瓨鍚儏鎰熷叧閿瘝鎴栬緝闀跨殑閲嶈闄堣堪
    if any(kw in content for kw in _EMOTION_KEYWORDS):
        return True
    if len(content) > 50:  # 瓒呰繃50瀛楃殑鐢ㄦ埛闄堣堪瑙嗕负閲嶈
        return True
    return False

def _pre_compress_save(
    self,
    turns: list[dict[str, Any]],
    *,
    strategy: str,
    session_id: str | None,
) -> int:
    # ...
    for idx, turn in enumerate(turns):
        if not _is_high_value_turn(turn):   # 馃啎 鏇挎崲鍘熸湁鐨?role != "assistant" 杩囨护
            continue
        role = str(turn.get("role") or "user")
        content = str(turn.get("content") or "").strip()
        evidence_ref = f"compress://pre_save/{session_id or 'unknown'}/{role}/turn_{idx:03d}"
        try:
            self._episodic_writer(content, [evidence_ref])
            saved_count += 1
        except Exception:
            pass
```

**棰勬湡鏁堟灉锛?* 鍘嬬缉鍓嶄繚瀛樼殑璁板繂浠?鍙湁 AI 澹伴煶"鍙樹负"鍙屽０閬?锛岀敤鎴风殑鎯呮劅琛ㄨ揪鍜岄噸瑕侀檲杩颁篃琚繚瀛橈紝闀挎湡璁板繂钂搁鐨勫師鏉愭枡鏇村姞瀹屾暣鍜岀湡瀹炪€?
---

### 馃數 娣卞寲浼樺寲29锛圥1锛夛細`LongTermMemoryDistiller` 鐨勮捀棣忎富棰樺垪琛ㄤ笉鍚?鎯呮劅鍏崇郴"缁村害

**鐩爣鏂囦欢锛?* `host_brain/memory/long_term_distiller.py`

**绮捐鍙戠幇锛?*

`DEFAULT_DISTILL_TOPICS` 鐨勫畾涔夛細

```python
DEFAULT_DISTILL_TOPICS: list[str] = [
    "鐢ㄦ埛鏃ュ父涔犳儻涓庡亸濂?,
    "閲嶈浜嬩欢涓庨噷绋嬬",
    "鎯呮劅鐘舵€佷笌蹇冪悊鍙樺寲",   # 鈫?鏈夋儏鎰燂紝浣嗗お瀹芥硾
    "鐢ㄦ埛鐩爣涓庤鍒?,
    "鍏崇郴涓庣ぞ浜や簰鍔?,
]
```

**闂锛?*
1. `"鎯呮劅鐘舵€佷笌蹇冪悊鍙樺寲"` 澶娉涳紝鏃犳硶绮剧‘鍙洖鐗瑰畾鎯呮劅鐨勮蹇?2. 娌℃湁"涓?AI 鐨勬儏鎰熶簰鍔?缁村害鈥斺€旀棤娉曡捀棣忓嚭"鐢ㄦ埛鍜?AI 涔嬮棿鐨勬儏鎰熷叧绯诲彶"
3. 娌℃湁"鎯呮劅杞姌鐐?缁村害鈥斺€旀棤娉曡瘑鍒?鐢ㄦ埛鎯呮劅鍙戠敓閲嶅ぇ鍙樺寲鐨勬椂鍒?
4. 娌℃湁"鐢ㄦ埛鐨勬儏鎰熼渶姹?缁村害鈥斺€旀棤娉曡捀棣忓嚭"鐢ㄦ埛鍦ㄤ粈涔堟儏鍐典笅闇€瑕佹儏鎰熸敮鎸?
5. 钂搁缁撴灉鍐欏叆鐨?`DISTILL_TARGET_DOC_ID = "wc-distilled-long-term-memory"` 鏄崟涓€鏂囨。锛屾墍鏈変富棰樻贩鍦ㄤ竴璧凤紝鏃犳硶鎸夋儏鎰熺淮搴︽绱?
**鏂规锛?* 鎵╁睍钂搁涓婚鍒楄〃锛屽鍔犳儏鎰熷叧绯荤淮搴︼紝骞朵负涓嶅悓缁村害浣跨敤涓嶅悓鐨勭洰鏍囨枃妗ｏ細

```python
# 馃啎 鎯呮劅鍏崇郴涓撻」钂搁涓婚锛坴1.4 鏂板锛?EMOTION_DISTILL_TOPICS: list[str] = [
    "鐢ㄦ埛涓嶢I涔嬮棿鐨勬儏鎰熶簰鍔ㄤ笌鍏崇郴鍙戝睍",
    "鐢ㄦ埛鎯呮劅杞姌鐐逛笌閲嶈鎯呮劅鏃跺埢",
    "鐢ㄦ埛鐨勬儏鎰熼渶姹備笌鎯呮劅鏀寔妯″紡",
    "鐢ㄦ埛琛ㄨ揪杩囩殑鍠滃ソ銆佸帉鎭朵笌鎯呮劅鍋忓ソ",
    "鐢ㄦ埛鍦ㄥ洶闅炬椂鏈熺殑鎯呮劅鐘舵€佷笌搴斿鏂瑰紡",
]

# 馃啎 鎯呮劅钂搁涓撶敤鐩爣鏂囨。
EMOTION_DISTILL_TARGET_DOC_ID = "wc-distilled-emotion-relationship"

DEFAULT_DISTILL_TOPICS: list[str] = [
    "鐢ㄦ埛鏃ュ父涔犳儻涓庡亸濂?,
    "閲嶈浜嬩欢涓庨噷绋嬬",
    "鐢ㄦ埛鎯呮劅鐘舵€佷笌蹇冪悊鍙樺寲",
    "鐢ㄦ埛鐩爣涓庤鍒?,
    "鍏崇郴涓庣ぞ浜や簰鍔?,
    # 馃啎 鎯呮劅鍏崇郴缁村害
    "鐢ㄦ埛涓嶢I涔嬮棿鐨勬儏鎰熶簰鍔ㄤ笌鍏崇郴鍙戝睍",
    "鐢ㄦ埛鎯呮劅杞姌鐐逛笌閲嶈鎯呮劅鏃跺埢",
    "鐢ㄦ埛鐨勬儏鎰熼渶姹備笌鎯呮劅鏀寔妯″紡",
]
```

**鍚屾椂淇 `_build_proposal_body()` 鐨勭洰鏍囨枃妗ｈ矾鐢憋細**

```python
def _submit_canon_proposal(self, *, semantic_id, topic, source_count, request_id, ts_ms):
    # 馃啎 鎯呮劅涓婚璺敱鍒颁笓鐢ㄦ枃妗?    is_emotion_topic = any(
        kw in topic for kw in ["鎯呮劅", "蹇冪悊", "鍏崇郴鍙戝睍", "鎯呮劅闇€姹?]
    )
    target_doc = (
        EMOTION_DISTILL_TARGET_DOC_ID if is_emotion_topic
        else self._target_doc_id
    )
    # ...
```

**棰勬湡鏁堟灉锛?* 闀挎湡璁板繂钂搁鑳藉涓撻棬鎻愮偧"鎯呮劅鍏崇郴鍙?锛岀敓鎴愮殑 Canon 鎻愭鍖呭惈鐢ㄦ埛涓?AI 涔嬮棿鐨勬儏鎰熷彂灞曡剦缁滐紝涓?LLM 鎻愪緵鏇翠赴瀵岀殑鎯呮劅鑳屾櫙銆?
---

### 馃煟 娣卞寲浼樺寲30锛圥2锛夛細`NatsumeScheduler` 鐨勫績璺虫娴?Prompt 涓嶆劅鐭ユ儏鎰熺姸鎬佲€斺€斿績璺虫椂 AI 鎯呮劅琚噸缃?
**鐩爣鏂囦欢锛?* `host_brain/scheduler/scheduler.py`

**绮捐鍙戠幇锛?*

`_run_agent_health_check()` 鐨勫疄鐜帮細

```python
def _run_agent_health_check(self) -> dict[str, Any]:
    prompt = getattr(self, "_heartbeat_prompt", DEFAULT_HEARTBEAT_PROMPT)
    # ...
    raw_response: str
    if llm_caller is not None:
        try:
            raw_response = str(llm_caller(prompt))
        except Exception as exc:
            raw_response = LLM_UNAVAILABLE_RESPONSE
    # ...
    state = parse_heartbeat_response(raw_response)
    # ...
```

`DEFAULT_HEARTBEAT_PROMPT` 鏄浐瀹氭枃鏈紙鏉ヨ嚜 `health_check_prompt.py`锛夛紝涓嶅寘鍚綋鍓嶆儏鎰熺姸鎬併€?
**闂锛?*
1. 蹇冭烦妫€娴嬫瘡 30 鍒嗛挓瑙﹀彂涓€娆★紝浣跨敤鍥哄畾 Prompt锛屼笉鎰熺煡褰撳墠 `mood_state`
2. 濡傛灉 AI 褰撳墠澶勪簬 `tired` 鐘舵€侊紝蹇冭烦 Prompt 浠嶇劧鏄?姝ｅ父"鐨勶紝LLM 浼氱敓鎴?姝ｅ父"鐨勫洖澶嶏紝瀵艰嚧鎯呮劅鐘舵€佽闅愬紡閲嶇疆
3. 蹇冭烦妫€娴嬬殑 `llm_caller` 璋冪敤浼氫骇鐢熶竴娆?LLM 鎺ㄧ悊锛屼絾杩欐鎺ㄧ悊鐨勭粨鏋滀笉浼氭洿鏂?`HardState`锛屾儏鎰熺姸鎬佸湪蹇冭烦鏈熼棿澶勪簬"鎮┖"鐘舵€?4. 蹇冭烦 OK 鏃?闈欓粯涓㈠純"锛堜笉鍐欏璁★級锛屼絾濡傛灉蹇冭烦鏈熼棿鎯呮劅鐘舵€佸彂鐢熶簡鍙樺寲锛岃繖涓彉鍖栦篃琚潤榛樹涪寮冧簡

**鏂规锛?* 涓哄績璺虫娴嬪鍔犳儏鎰熺姸鎬佹劅鐭ワ紝骞跺湪蹇冭烦 OK 鏃惰褰曟儏鎰熷揩鐓э細

```python
def register_agent_health_check(
    self,
    *,
    interval_seconds: int = 1800,
    heartbeat_prompt: str | None = None,
    max_response_chars: int = MAX_RESPONSE_CHARS,
    llm_caller: Any | None = None,
    get_mood_state: Callable[[], str] | None = None,   # 馃啎 鎯呮劅鐘舵€佽幏鍙栧嚱鏁?) -> None:
    # ...
    self._heartbeat_get_mood_state = get_mood_state  # 馃啎 淇濆瓨鎯呮劅鐘舵€佽幏鍙栧嚱鏁?
def _run_agent_health_check(self) -> dict[str, Any]:
    prompt = getattr(self, "_heartbeat_prompt", DEFAULT_HEARTBEAT_PROMPT)
    max_chars = getattr(self, "_heartbeat_max_chars", MAX_RESPONSE_CHARS)
    llm_caller = getattr(self, "_heartbeat_llm_caller", None)
    get_mood_state = getattr(self, "_heartbeat_get_mood_state", None)
    ts = now_ms()

    # 馃啎 鑾峰彇褰撳墠鎯呮劅鐘舵€侊紝娉ㄥ叆蹇冭烦 Prompt
    current_mood = get_mood_state() if get_mood_state else "steady"
    mood_aware_prompt = _build_mood_aware_heartbeat_prompt(prompt, current_mood)

    raw_response: str
    if llm_caller is not None:
        try:
            raw_response = str(llm_caller(mood_aware_prompt))  # 馃啎 浣跨敤鎯呮劅鎰熺煡 Prompt
        except Exception as exc:
            raw_response = LLM_UNAVAILABLE_RESPONSE
    else:
        raw_response = LLM_UNAVAILABLE_RESPONSE

    state = parse_heartbeat_response(raw_response)
    truncated = truncate_response(raw_response, max_chars)

    if state == "OK":
        # 馃啎 蹇冭烦 OK 鏃惰褰曟儏鎰熷揩鐓э紙涓嶅啀瀹屽叏闈欓粯锛?        self._log_heartbeat_emotion_snapshot(mood_state=current_mood, ts_ms=ts)
        self._check_edge_transport_probes(ts_ms=ts)
        return {"status": HEARTBEAT_EVENT_TYPE_OK, "mood_state": current_mood, "ts_ms": ts}

    # ...

def _build_mood_aware_heartbeat_prompt(base_prompt: str, mood_state: str) -> str:
    """涓哄績璺?Prompt 娉ㄥ叆褰撳墠鎯呮劅鐘舵€佷笂涓嬫枃銆?""
    mood_hints = {
        "engaged":  "锛堝綋鍓嶇姸鎬侊細绮惧姏鍏呮矝锛岀Н鏋佹姇鍏ワ級",
        "steady":   "锛堝綋鍓嶇姸鎬侊細骞崇ǔ姝ｅ父锛?,
        "calm":     "锛堝綋鍓嶇姸鎬侊細骞抽潤鏀炬澗锛?,
        "tired":    "锛堝綋鍓嶇姸鎬侊細鏈変簺鐤叉儷锛岄渶瑕佷紤鎭級",
        "critical": "锛堝綋鍓嶇姸鎬侊細鑳介噺鏋佷綆锛岄渶瑕佺珛鍗充紤鎭級",
    }
    hint = mood_hints.get(str(mood_state).lower(), "")
    return f"{base_prompt}\n\n[绯荤粺鎻愮ず锛氬綋鍓嶆儏鎰熺姸鎬?{hint}]"
```

**棰勬湡鏁堟灉锛?* 蹇冭烦妫€娴嬩笉鍐嶆槸鎯呮劅鐘舵€佺殑"榛戞礊"锛宍tired` 鐘舵€佷笅鐨勫績璺充細浣跨敤鎰熺煡鐤叉儷鐨?Prompt锛孡LM 鐨勫洖澶嶄細鍙嶆槧鐪熷疄鐨勬儏鎰熺姸鎬侊紝蹇冭烦 OK 鏃朵篃浼氳褰曟儏鎰熷揩鐓т緵鍚庣画鍒嗘瀽銆?
---

## 鍗佸叓銆佺浜旇疆浼樺寲鐨勬牳蹇冧环鍊兼€荤粨

绗簲杞紭鍖栬仛鐒︿簬**璺ㄦā鍧楁儏鎰熶俊鎭殑绯荤粺鎬х己澶?*鈥斺€旀儏鎰熶俊鎭湪"瀛樺偍鈫掕捀棣忊啋璋冨害鈫掕緭鍑?鍏ㄩ摼璺腑鐨勬柇鐐癸細

| 浼樺寲椤?| 鍙戠幇鐨?璺ㄦā鍧楃洸鍖? | 褰卞搷鑼冨洿 | 淇鏂规 |
|--------|------------------|---------|---------|
| **浼樺寲25** 馃敶 | `EpisodicRecord` 鏃犳儏鎰熷瓧娈碉紝鎯呮劅璁板繂鏃犳硶琚儏鎰熸绱?| `episodic.py` 鈫?鏁翠釜璁板繂灞?| 澧炲姞鎯呮劅鏍囨敞瀛楁锛屾敮鎸佹儏鎰熻繃婊ゆ绱?|
| **浼樺寲26** 馃煚 | Bond 浜嬩欢鏃犳儏鎰熷瓧娈碉紝Bond 绉垎涓嶆劅鐭ユ儏鎰熷己搴?| `bond_dynamics.py` 鈫?Bond 绉垎 | 鎯呮劅鍔犳潈 Bond 绉垎锛屾繁搴︽儏鎰熶簰鍔ㄨ础鐚洿澶氱Н鍒?|
| **浼樺寲27** 馃煛 | `derive_bond_summary()` 璇诲彇閿欒瀛楁锛孊ond 淇℃伅瀹屽叏澶辩湡 | `summarizer.py` 鈫?LLM 杈撳叆 | 淇瀛楁璇诲彇閫昏緫锛屼粠 `stage_transition` 璇诲彇鐪熷疄 Bond 闃舵 |
| **浼樺寲28** 馃煝 | 鍘嬬缉鍓嶅彧淇濆瓨 assistant 杞锛岀敤鎴锋儏鎰熶俊鎭涓㈠純 | `context_compressor.py` 鈫?闀挎湡璁板繂 | 鏀逛负淇濆瓨楂樹环鍊艰疆娆★紝鍖呮嫭鐢ㄦ埛鎯呮劅琛ㄨ揪 |
| **浼樺寲29** 馃數 | 钂搁涓婚涓嶅惈鎯呮劅鍏崇郴缁村害锛屾棤娉曡捀棣忔儏鎰熷叧绯诲彶 | `long_term_distiller.py` 鈫?Canon | 澧炲姞鎯呮劅鍏崇郴涓撻」钂搁涓婚 |
| **浼樺寲30** 馃煟 | 蹇冭烦妫€娴嬩笉鎰熺煡鎯呮劅鐘舵€侊紝蹇冭烦鏈熼棿鎯呮劅琚殣寮忛噸缃?| `scheduler.py` 鈫?鎯呮劅杩炵画鎬?| 蹇冭烦 Prompt 娉ㄥ叆鎯呮劅鐘舵€侊紝璁板綍鎯呮劅蹇収 |

---

## 鍗佷節銆佸畬鏁翠紭鍖栧叏鏅浘锛坴1.4锛?0椤癸級

### 浜旇疆浼樺寲鐨勫眰娆￠€掕繘

```
绗竴杞紙v1.0锛夛細鍙戠幇5涓?缂哄け鍔熻兘"     鈫?闇€瑕佹柊澧炰粈涔?绗簩杞紙v1.1锛夛細鍙戠幇6涓?娣卞眰闂"     鈫?鐜版湁鏈哄埗鍝噷涓嶅娣?绗笁杞紙v1.2锛夛細鍙戠幇6涓?鍋囧姛鑳界┖澹?   鈫?鐜版湁浠ｇ爜鍝噷鐪嬭捣鏉ユ湁浣嗗疄闄呮病鐢?绗洓杞紙v1.3锛夛細鍙戠幇6涓?鏋舵瀯鏂眰"     鈫?鍔熻兘涔嬮棿鐨勮繛鎺ュ湪鍝噷鏂
绗簲杞紙v1.4锛夛細鍙戠幇6涓?璺ㄦā鍧楃洸鍖?   鈫?鎯呮劅淇℃伅鍦ㄥ叏閾捐矾涓殑绯荤粺鎬х己澶?```

### 鎸変紭鍏堢骇鍒嗙被锛?0椤瑰叏鏅級

| 浼樺厛绾?| 浼樺寲椤?| 鏍稿績闂 |
|--------|--------|---------|
| **P0锛堝繀椤讳慨澶嶏級** | OPT19, OPT20, OPT22, OPT25, OPT26, OPT27 | 鏋舵瀯鏂眰 + 璺ㄦā鍧楃洸鍖轰腑鐨勫叧閿矾寰?|
| **P1锛堥噸瑕佷紭鍖栵級** | OPT1, OPT6, OPT7, OPT8, OPT13, OPT14, OPT16, OPT17, OPT18, OPT21, OPT28, OPT29 | 鎯呮劅鍐呮牳 + 鍏崇郴灞?+ 璁板繂灞?|
| **P2锛堜綋楠屽寮猴級** | OPT2, OPT3, OPT4, OPT5, OPT9, OPT10, OPT11, OPT12, OPT15, OPT23, OPT24, OPT30 | 杈撳嚭灞?+ 缁嗚妭鎵撶（ |

### 鍙樻洿璁板綍锛堝畬鏁寸増锛?
| 鏃ユ湡 | 鐗堟湰 | 璇存槑 |
|------|------|------|
| 2026-03-05 | v1.0 | 鍒濈锛屽熀浜庝唬鐮佹繁搴﹀垎鏋愬垱寤猴紝璁板綍浜斿ぇ浼樺寲鏂瑰悜锛埪т竴~搂浜旓級 |
| 2026-03-05 | v1.1 | 娣卞寲鍒嗘瀽锛氬熀浜庡叏閲忎唬鐮佺簿璇伙紝鏂板鍏ぇ娣卞寲浼樺寲鏂瑰悜锛埪т竷~搂涔濓級 |
| 2026-03-05 | v1.2 | 绗笁杞繁鍖栵細鍙戠幇6涓?鍋囧姛鑳?绌哄３闂锛屾柊澧炰紭鍖?3~18锛埪у崄~搂鍗佷簩锛?|
| 2026-03-05 | v1.3 | 绗洓杞繁鍖栵細鍙戠幇6涓?鏋舵瀯鏂眰"闂锛屾柊澧炰紭鍖?9~24锛埪у崄鍥泘搂鍗佸叚锛?|
| 2026-03-05 | v1.4 | 绗簲杞繁鍖栵細鍙戠幇6涓?璺ㄦā鍧楁儏鎰熺洸鍖?锛屾柊澧炰紭鍖?5~30锛埪у崄涓儈搂鍗佷節锛?|
| 2026-03-05 | v1.5 | 绗叚杞繁鍖栵細鍙戠幇6涓?鎰熺煡灞傛儏鎰熸柇璺?锛屾柊澧炰紭鍖?1~36锛埪т簩鍗亊搂浜屽崄浜岋級 |
| 2026-03-05 | v1.6 | 绗竷杞繁鍖栵細鍙戠幇6涓?鎵ц灞傛儏鎰熺湡绌?锛屾柊澧炰紭鍖?7~42锛埪т簩鍗佷笁~搂浜屽崄浜旓級 |
| 2026-03-05 | v1.7 | 绗叓杞繁鍖栵細鍙戠幇6涓?鍩虹璁炬柦鎯呮劅鐩插尯"锛屾柊澧炰紭鍖?3~48锛埪т簩鍗佸叚~搂浜屽崄鍏級 |

---

## 浜屽崄鍏€佺鍏疆娣卞寲浼樺寲锛氬熀纭€璁炬柦鎯呮劅鐩插尯

> **v1.7 鏂板**锛?026-03-05锛夈€傛湰杞熀浜庡 `scheduler/scheduler.py`銆乣integrations/llm_gateway.py`銆乣integrations/graph_executor.py`銆乣obs/metrics.py`銆乣edge_client/actuators/tts_engine.py`銆乣edge_client/senses/asr.py` 鐨勫畬鏁撮€愯绮捐锛屽彂鐜颁簡鍓嶄竷杞畬鍏ㄦ湭瑙﹀強鐨?**6 涓熀纭€璁炬柦鎯呮劅鐩插尯**鈥斺€旇繖浜涢棶棰樺垎甯冨湪璋冨害灞傘€丩LM缃戝叧灞傘€佸浘鎵ц灞傘€佹寚鏍囪娴嬪眰銆乀TS寮曟搸灞傘€丄SR鎰熺煡灞傦紝鏄儏鎰熶俊鎭湪"鍩虹璁炬柦鈫掓劅鐭モ啋杈撳嚭"閾捐矾涓殑绯荤粺鎬х洸鍖恒€?
---

### 馃敶 娣卞寲浼樺寲43锛圥0锛夛細`NatsumeScheduler._build_system_intent()` 涓嶆惡甯︽儏鎰熺姸鎬佲€斺€旇皟搴︿换鍔¤Е鍙戞椂鎯呮劅鐩?
**鐩爣鏂囦欢锛?* `host_brain/scheduler/scheduler.py`

**绮捐鍙戠幇锛?*

`_build_system_intent()` 鐢熸垚鐨?`system_initiated_intent`锛?
```python
def _build_system_intent(
    self,
    task: SchedulerTaskConfig,
    *,
    intent_id: str,
    request_id: str,
) -> dict[str, Any]:
    template = dict(task.intent_template)
    return {
        "intent_id": intent_id,
        "request_id": request_id,
        "proposer_node_id": PROPOSER_NODE_ID,
        "task_id": task.task_id,
        "skill_name": template.get("skill_name", task.task_id),
        "args": template.get("args", {}),
        "priority": task.priority.value,
        "trigger_mode": task.trigger_mode.value,
        "ts_ms": now_ms(),
        # 鈫?娌℃湁鎯呮劅鐘舵€佸瓧娈碉紒
    }
```

`_trigger_task()` 鐨勯棬鎺ф鏌ワ細

```python
# sys_mode 闂ㄦ帶
current_mode = self._get_sys_mode()
if not task.is_allowed_in_mode(current_mode):
    # 鈫?鍙鏌?sys_mode锛屼笉妫€鏌ユ儏鎰熺姸鎬侊紒
```

`_log_scheduler_event()` 鐨勫璁¤褰曪細

```python
self._ledger.log_scheduler_event({
    "event_id": ...,
    "task_id": task_id,
    "trigger_ts": trigger_ts,
    "intent_id": intent_id,
    "status": status,
    "reason_code": reason_code,
    "mode_at_trigger": mode_at_trigger,
    "error_detail": error_detail,
    "ts_ms": ts_ms,
    # 鈫?娌℃湁鎯呮劅鐘舵€佸瓧娈碉紒
})
```

杩欐剰鍛崇潃锛?1. 璋冨害浠诲姟瑙﹀彂鏃跺畬鍏ㄤ笉鎰熺煡鎯呮劅鐘舵€佲€斺€擿critical` 鎯呮劅鐘舵€佷笅锛圓I 鑳介噺鏋佷綆锛夛紝楂樻秷鑰楃殑瀹氭椂浠诲姟锛堝姣?0鍒嗛挓鐨勪富鍔ㄩ棶鍊欎换鍔★級浠嶇劧浼氳瑙﹀彂
2. `_build_system_intent()` 鐢熸垚鐨?intent 涓嶆惡甯︽儏鎰熺姸鎬侊紝涓嬫父鐨?Governor 鏃犳硶鎰熺煡杩欐槸鍦ㄤ粈涔堟儏鎰熺姸鎬佷笅瑙﹀彂鐨勮皟搴︿换鍔?3. 璋冨害瀹¤鏃ュ織涓嶅惈鎯呮劅瀛楁锛屾棤娉曞垎鏋?鍝簺鎯呮劅鐘舵€佷笅璋冨害浠诲姟鎵ц鎴愬姛鐜囨洿楂?

**鏂规锛?* 涓鸿皟搴﹀櫒澧炲姞鎯呮劅鐘舵€佹劅鐭ヨ兘鍔涳細

```python
class NatsumeScheduler:
    def __init__(
        self,
        registry: SchedulerTaskRegistry,
        health_reporter: SchedulerHealthReporter,
        *,
        intent_dispatcher: IntentDispatcher | None = None,
        get_sys_mode: Callable[[], str] | None = None,
        # 馃啎 鎯呮劅鐘舵€佽幏鍙栧嚱鏁帮紙v1.7 鏂板锛?        get_emotion_state: Callable[[], dict[str, Any]] | None = None,
        ledger: Any | None = None,
        persist_tasks: bool = True,
    ) -> None:
        # ...
        # 馃啎 鎯呮劅鐘舵€佽幏鍙栧嚱鏁帮紙杩斿洖 {"emotion_tag": ..., "mood_state": ..., "bond_stage": ...}锛?        self._get_emotion_state = get_emotion_state or (lambda: {})
```

**鎯呮劅闂ㄦ帶锛?*

```python
def _trigger_task(self, task_id: str) -> dict[str, Any]:
    # ...
    # 馃啎 鎯呮劅鐘舵€侀棬鎺э紙v1.7 鏂板锛?    emotion_state = self._get_emotion_state()
    mood_state = str(emotion_state.get("mood_state") or "steady").lower()
    emotion_tag = str(emotion_state.get("emotion_tag") or "NEUTRAL").upper()

    # critical 鐘舵€佷笅锛孧EDIUM/HIGH 浼樺厛绾т换鍔¤鏆傜紦
    if mood_state == "critical" and task.priority.value in ("MEDIUM", "HIGH"):
        reason = "EMOTION_CRITICAL_THROTTLE"
        self._log_scheduler_event(
            task_id=task_id,
            trigger_ts=trigger_ts,
            status="THROTTLED",
            reason_code=reason,
            mode_at_trigger=current_mode,
            emotion_tag=emotion_tag,
            mood_state=mood_state,
            error_detail=f"task throttled: mood_state=critical, priority={task.priority.value}",
        )
        return {"status": "THROTTLED", "reason_code": reason, "task_id": task_id, "mood_state": mood_state}
```

**`_build_system_intent()` 鎼哄甫鎯呮劅鐘舵€侊細**

```python
def _build_system_intent(self, task, *, intent_id, request_id) -> dict[str, Any]:
    emotion_state = self._get_emotion_state()
    return {
        # ... 鍘熸湁瀛楁 ...
        # 馃啎 鎯呮劅鐘舵€佸瓧娈?        "emotion_tag": emotion_state.get("emotion_tag", "NEUTRAL"),
        "mood_state": emotion_state.get("mood_state", "steady"),
        "bond_stage": emotion_state.get("bond_stage", "STRANGER"),
    }
```

**棰勬湡鏁堟灉锛?* 璋冨害鍣ㄤ粠"鎯呮劅鐩?鍙樹负"鎯呮劅鎰熺煡"锛宍critical` 鐘舵€佷笅楂樻秷鑰椾换鍔¤鑷姩鏆傜紦锛岃皟搴?intent 鎼哄甫鎯呮劅鐘舵€佷緵涓嬫父 Governor 鎰熺煡锛屽璁℃棩蹇楀彲鍒嗘瀽鎯呮劅鐘舵€佷笌璋冨害鎵ц鐨勭浉鍏虫€с€?
---

### 馃煚 娣卞寲浼樺寲44锛圥1锛夛細`LlmGatewayContract.generate()` 鐨?`prompt_payload` 涓嶆惡甯︽儏鎰熶笂涓嬫枃鈥斺€擫LM 璋冪敤鏃舵儏鎰熺洸

**鐩爣鏂囦欢锛?* `host_brain/integrations/llm_gateway.py`

**绮捐鍙戠幇锛?*

`generate()` 鐨?`prompt_payload` 鍙傛暟锛?
```python
def generate(
    self,
    *,
    request_id: str,
    model_profile_id: str,
    prompt_payload: dict[str, Any],   # 鈫?鑷敱鏍煎紡 dict锛屾病鏈夋儏鎰熷瓧娈电害瀹?    ts_ms: int | None = None,
) -> GatewayResult:
```

`_degrade()` 鐨勯檷绾у洖澶嶏細

```python
fallback_payload = {
    "narrative": {"text": "褰撳墠澶栭儴璇箟鏈嶅姟涓嶅彲鐢紝鎴戝厛缁欏嚭鑽夋鍥炲銆?},
    "intent_draft": {},
    "meta": {
        "draft_only": True,
        "degraded": True,
        "reason_code": reason_code,
        "stage": stage,
    },
    # 鈫?闄嶇骇鍥炲瀹屽叏涓嶆劅鐭ユ儏鎰熺姸鎬侊紒
}
```

`GatewayResult` 鐨勮緭鍑虹粨鏋勶細

```python
output = {
    "request_id": request_id,
    "model_profile_id": model_profile_id,
    "provider": self.provider_name,
    "draft_only": True,
    "status": status,
    "stage": S_LLM_GATEWAY_QA,
    "reason_code": qa_result.reason_code,
    "latency_ms": latency_ms,
    "payload": qa_result.payload,
    "airlock": {...},
    # 鈫?娌℃湁鎯呮劅瀛楁锛?}
```

杩欐剰鍛崇潃锛?1. LLM 璋冪敤鏃?`prompt_payload` 涓嶆惡甯︽儏鎰熶笂涓嬫枃鈥斺€擫LM 鏃犳硶鎰熺煡褰撳墠 AI 鐨勬儏鎰熺姸鎬侊紝鐢熸垚鐨勫洖澶嶄笉浼氫綋鐜版儏鎰熻壊褰?2. 闄嶇骇鍥炲锛坄_degrade()`锛夊畬鍏ㄤ笉鎰熺煡鎯呮劅鐘舵€佲€斺€擿HAPPY` 鐘舵€佷笅鐨勯檷绾у洖澶嶅拰 `SAD` 鐘舵€佷笅鐨勯檷绾у洖澶嶅畬鍏ㄧ浉鍚?3. `GatewayResult.output` 涓嶅惈鎯呮劅瀛楁鈥斺€斾笅娓告棤娉曟劅鐭?LLM 璋冪敤鏃剁殑鎯呮劅鐘舵€?
**鏂规锛?* 涓?`generate()` 澧炲姞鎯呮劅涓婁笅鏂囧弬鏁帮紝骞跺湪闄嶇骇鍥炲涓綋鐜版儏鎰熺姸鎬侊細

```python
def generate(
    self,
    *,
    request_id: str,
    model_profile_id: str,
    prompt_payload: dict[str, Any],
    ts_ms: int | None = None,
    # 馃啎 鎯呮劅涓婁笅鏂囧弬鏁帮紙v1.7 鏂板锛?    emotion_context: dict[str, Any] | None = None,
    # emotion_context 缁撴瀯锛?    # {
    #   "emotion_tag": "HAPPY",
    #   "mood_state": "engaged",
    #   "bond_stage": "FRIEND",
    #   "emotional_intensity": 0.7,
    # }
) -> GatewayResult:
    # ...
    # 馃啎 灏嗘儏鎰熶笂涓嬫枃娉ㄥ叆 prompt_payload
    if emotion_context:
        prompt_payload = dict(prompt_payload)
        prompt_payload["__emotion_context__"] = emotion_context
```

**鎯呮劅鎰熺煡闄嶇骇鍥炲锛?*

```python
def _degrade(self, ..., emotion_context: dict[str, Any] | None = None) -> GatewayResult:
    ec = emotion_context or {}
    emotion_tag = str(ec.get("emotion_tag") or "NEUTRAL").upper()
    mood_state = str(ec.get("mood_state") or "steady").lower()

    # 馃啎 鏍规嵁鎯呮劅鐘舵€侀€夋嫨闄嶇骇鍥炲鏂囨湰
    DEGRADED_NARRATIVES = {
        "HAPPY": "鍡紝鐜板湪鏈夌偣灏忛棶棰橈紝涓嶈繃娌″叧绯伙紝鎴戜滑绋嶅悗鍐嶈瘯鍚э綖",
        "SAD": "鎶辨瓑锛岀幇鍦ㄩ亣鍒颁簡涓€浜涢棶棰樷€︹€︽垜浼氬敖蹇仮澶嶇殑銆?,
        "ANXIOUS": "鍟婏紝鍑轰簡鐐圭姸鍐碉紝璇风◢绛変竴涓嬧€︹€?,
        "NEUTRAL": "褰撳墠澶栭儴璇箟鏈嶅姟涓嶅彲鐢紝鎴戝厛缁欏嚭鑽夋鍥炲銆?,
    }
    narrative_text = DEGRADED_NARRATIVES.get(emotion_tag, DEGRADED_NARRATIVES["NEUTRAL"])

    fallback_payload = {
        "narrative": {"text": narrative_text},
        "intent_draft": {},
        "meta": {
            "draft_only": True,
            "degraded": True,
            "reason_code": reason_code,
            "stage": stage,
            # 馃啎 鎯呮劅瀛楁
            "emotion_tag": emotion_tag,
            "mood_state": mood_state,
        },
    }
```

**棰勬湡鏁堟灉锛?* LLM 缃戝叧浠?鎯呮劅鐩?鍙樹负"鎯呮劅鎰熺煡"锛孡LM 璋冪敤鏃舵惡甯︽儏鎰熶笂涓嬫枃锛岄檷绾у洖澶嶆牴鎹儏鎰熺姸鎬侀€夋嫨鍚堥€傜殑鏂囨湰锛宍GatewayResult` 杈撳嚭鍖呭惈鎯呮劅瀛楁渚涗笅娓告劅鐭ャ€?
---

### 馃煛 娣卞寲浼樺寲45锛圥1锛夛細`GraphExecutor._invoke_handler()` 鐨?`context` 鍙傛暟涓嶆惡甯︽儏鎰熺姸鎬佲€斺€斿浘鑺傜偣鎵ц鏃舵儏鎰熺洸

**鐩爣鏂囦欢锛?* `host_brain/integrations/graph_executor.py`

**绮捐鍙戠幇锛?*

`_invoke_handler()` 鐨?`context` 鍙傛暟锛?
```python
def _invoke_handler(self, *, handler_id: str, node_id: str, state: GraphState) -> dict[str, Any]:
    handler = self._handlers.get(handler_id)
    if handler is None:
        raise KeyError(f"handler missing for node={node_id}, handler_id={handler_id}")
    return handler(dict(state.get("payload") or {}), {
        "request_id": str(state.get("request_id") or ""),
        "node_id": node_id,
        "graph_id": str(state.get("graph_id") or ""),
        "timeout_ms": int(state.get("timeout_ms") or self._default_timeout_ms)
        # 鈫?context 閲屾病鏈夋儏鎰熷瓧娈碉紒
    })
```

`_invoke_model_node()` 鐨?`prompt_payload`锛?
```python
result = gateway.generate(
    request_id=str(state.get("request_id") or ""),
    model_profile_id=...,
    prompt_payload={
        "node_id": node_id,
        "model_node_id": model_node_id,
        "task": str(binding.get("task") or node_id),
        "input": dict(state.get("payload") or {}),
        "model": {...},
        # 鈫?prompt_payload 閲屾病鏈夋儏鎰熷瓧娈碉紒
    }
)
```

`GraphState` 鐨勫瓧娈靛畾涔夛紙鏉ヨ嚜 `graph_contract.py`锛夛細

```python
class GraphState(TypedDict, total=False):
    request_id: str
    graph_id: str
    model_profile_id: str
    pending_node_id: str
    node_bindings: dict[str, Any]
    model_nodes: dict[str, Any]
    dependency_statuses: dict[str, Any]
    edge_map: dict[str, Any]
    payload: dict[str, Any]
    trace: list[dict[str, Any]]
    model_calls: list[dict[str, Any]]
    status: str
    reason_code: str | None
    detail: str
    timeout_ms: int
    # 鈫?娌℃湁鎯呮劅瀛楁锛?```

杩欐剰鍛崇潃锛?1. 鍥捐妭鐐规墽琛屾椂 handler 鐨?context 涓嶅惈鎯呮劅瀛楁鈥斺€斿浘鑺傜偣 handler 鏃犳硶鏍规嵁鎯呮劅鐘舵€佽皟鏁存墽琛岃涓?2. 鍥捐妭鐐硅皟鐢?LLM 鏃?`prompt_payload` 涓嶅惈鎯呮劅瀛楁鈥斺€斿嵆浣夸紭鍖?4瀹炴柦浜嗭紝鍥炬墽琛屽櫒涔熸棤娉曚紶閫掓儏鎰熶笂涓嬫枃
3. `GraphState` 涓嶅惈鎯呮劅瀛楁鈥斺€旀儏鎰熶俊鎭棤娉曞湪鍥炬墽琛岀殑澶氫釜鑺傜偣涔嬮棿浼犻€?
**鏂规锛?* 涓?`GraphState` 澧炲姞鎯呮劅瀛楁锛屽苟鍦?handler context 鍜?prompt_payload 涓紶閫掞細

```python
# 馃啎 GraphState 澧炲姞鎯呮劅瀛楁锛坴1.7 鏂板锛?class GraphState(TypedDict, total=False):
    # ... 鍘熸湁瀛楁 ...
    # 馃啎 鎯呮劅瀛楁
    emotion_tag: str           # 褰撳墠鎯呮劅鏍囩
    mood_state: str            # 褰撳墠 mood_state
    bond_stage: str            # 褰撳墠 Bond 闃舵
    emotional_intensity: float # 鎯呮劅寮哄害
```

**`_invoke_handler()` 浼犻€掓儏鎰熷瓧娈碉細**

```python
def _invoke_handler(self, *, handler_id, node_id, state) -> dict[str, Any]:
    handler = self._handlers.get(handler_id)
    return handler(dict(state.get("payload") or {}), {
        "request_id": str(state.get("request_id") or ""),
        "node_id": node_id,
        "graph_id": str(state.get("graph_id") or ""),
        "timeout_ms": int(state.get("timeout_ms") or self._default_timeout_ms),
        # 馃啎 鎯呮劅瀛楁
        "emotion_tag": str(state.get("emotion_tag") or "NEUTRAL"),
        "mood_state": str(state.get("mood_state") or "steady"),
        "bond_stage": str(state.get("bond_stage") or "STRANGER"),
        "emotional_intensity": float(state.get("emotional_intensity") or 0.5),
    })
```

**棰勬湡鏁堟灉锛?* 鍥炬墽琛屽櫒浠?鎯呮劅鐩?鍙樹负"鎯呮劅鎰熺煡"锛屽浘鑺傜偣 handler 鍙互鏍规嵁鎯呮劅鐘舵€佽皟鏁存墽琛岃涓猴紝LLM 璋冪敤鏃舵惡甯︽儏鎰熶笂涓嬫枃锛屾儏鎰熶俊鎭彲浠ュ湪鍥炬墽琛岀殑澶氫釜鑺傜偣涔嬮棿浼犻€掋€?
---

### 馃煝 娣卞寲浼樺寲46锛圥1锛夛細`MetricsCollector.collect()` 涓嶅寘鍚儏鎰熺淮搴︽寚鏍団€斺€旇娴嬪眰鎯呮劅鐩?
**鐩爣鏂囦欢锛?* `host_brain/obs/metrics.py`

**绮捐鍙戠幇锛?*

`collect()` 鐨勮繑鍥炵粨鏋勶細

```python
payload: dict[str, Any] = {
    "request_id": request_id,
    "ts_ms": collected_ts,
    "obs_mode": mode.value,
    "governor_decisions": governor_stats,
    "retrieval_latency": retrieval_stats,
    "hard_snapshot_staleness_ms": snapshot_staleness,
    "recent_chains": recent_chains,
    "latency_baseline": latency_baseline,
    "debug_trace": None,
    # 鈫?娌℃湁鎯呮劅缁村害鎸囨爣锛?}
```

`_query_governor_stats()` 鐨勭粺璁★細

```python
return {
    "total": total,
    "approved": approved,
    "rejected": rejected,
    "degraded": degraded,
    "pseudo_exec_blocked": pseudo_exec_blocked,
    # 鈫?娌℃湁鎯呮劅鐩稿叧缁熻锛?}
```

`_query_recent_chains()` 鐨勯摼璺俊鎭細

```python
chain: dict[str, Any] = {
    "request_id": rid,
    "intent_id": row.get("intent_id"),
    "ts_ms": row.get("ts_ms"),
    "decision": None,
    "reason_code": None,
    "skill_name": None,
    # 鈫?娌℃湁鎯呮劅瀛楁锛?}
```

杩欐剰鍛崇潃锛?1. `/obs/metrics` 绔偣涓嶅寘鍚儏鎰熺淮搴︽寚鏍団€斺€旇繍缁翠汉鍛樻棤娉曢€氳繃鐩戞帶闈㈡澘鎰熺煡 AI 鐨勬儏鎰熺姸鎬佸彉鍖栬秼鍔?2. `_query_governor_stats()` 涓嶇粺璁℃儏鎰熺浉鍏崇殑 Governor 鍐崇瓥鈥斺€旀棤娉曞垎鏋?鏈夊灏戞鍥犳儏鎰熶繚鎶ら棬鎺э紙R8锛夎鎷掔粷"
3. `_query_recent_chains()` 涓嶅惈鎯呮劅瀛楁鈥斺€旀棤娉曞湪閾捐矾杩借釜涓湅鍒版瘡娆¤姹傛椂鐨勬儏鎰熺姸鎬?
**鏂规锛?* 涓?`MetricsCollector` 澧炲姞鎯呮劅缁村害鎸囨爣锛?
```python
def collect(self, ...) -> dict[str, Any]:
    # ...
    # 馃啎 鎯呮劅缁村害鎸囨爣锛坴1.7 鏂板锛?    emotion_stats = self._query_emotion_stats()

    payload: dict[str, Any] = {
        # ... 鍘熸湁瀛楁 ...
        # 馃啎 鎯呮劅缁村害鎸囨爣
        "emotion_stats": emotion_stats,
    }
    return payload

def _query_emotion_stats(self) -> dict[str, Any]:
    """缁熻鎯呮劅缁村害鎸囨爣锛氭儏鎰熷垎甯冦€佹儏鎰熶繚鎶ら棬鎺цЕ鍙戞鏁般€佹儏鎰熷彉鍖栬秼鍔裤€?""
    # 浠?governor_log 缁熻鎯呮劅淇濇姢闂ㄦ帶瑙﹀彂娆℃暟
    emotion_gate_blocked = 0
    try:
        for r in self.ledger.governor_log:
            if r.get("reason_code") == "EMOTION_CRITICAL_THROTTLE":
                emotion_gate_blocked += 1
    except Exception:
        pass

    # 浠?skill_exec_log 缁熻鎯呮劅鏍囩鍒嗗竷
    emotion_distribution: dict[str, int] = {}
    try:
        for r in self.ledger.skill_exec_log:
            emotion_tag = str(r.get("emotion_tag") or "UNKNOWN").upper()
            emotion_distribution[emotion_tag] = emotion_distribution.get(emotion_tag, 0) + 1
    except Exception:
        pass

    return {
        "emotion_gate_blocked_count": emotion_gate_blocked,
        "emotion_distribution": emotion_distribution,
        "emotion_data_available": bool(emotion_distribution),
    }
```

**`_query_recent_chains()` 澧炲姞鎯呮劅瀛楁锛?*

```python
chain: dict[str, Any] = {
    "request_id": rid,
    "intent_id": row.get("intent_id"),
    "ts_ms": row.get("ts_ms"),
    "decision": None,
    "reason_code": None,
    "skill_name": None,
    # 馃啎 鎯呮劅瀛楁
    "emotion_tag": row.get("emotion_tag"),
    "mood_state": row.get("mood_state"),
}
```

**棰勬湡鏁堟灉锛?* 瑙傛祴灞備粠"鎯呮劅鐩?鍙樹负"鎯呮劅鎰熺煡"锛岃繍缁翠汉鍛樺彲浠ラ€氳繃鐩戞帶闈㈡澘鎰熺煡 AI 鐨勬儏鎰熺姸鎬佸彉鍖栬秼鍔匡紝鍒嗘瀽鎯呮劅淇濇姢闂ㄦ帶瑙﹀彂棰戠巼锛屽湪閾捐矾杩借釜涓湅鍒版瘡娆¤姹傛椂鐨勬儏鎰熺姸鎬併€?
---

### 馃數 娣卞寲浼樺寲47锛圥1锛夛細`SoVITSModelManager.synthesize()` 鐨?`emotion_intensity` 鍙傛暟涓嶄紶閫掔粰搴曞眰 SDK鈥斺€擳TS 鎯呮劅寮哄害鏂矾

**鐩爣鏂囦欢锛?* `edge_client/actuators/tts_engine.py`

**绮捐鍙戠幇锛?*

`synthesize()` 鎺ユ敹 `emotion_intensity` 鍙傛暟锛?
```python
def synthesize(
    self,
    text: str,
    *,
    language: str | None = None,
    emotion_tag: str | None = None,
    emotion_intensity: float | None = None,   # 鈫?鎺ユ敹浜嗘儏鎰熷己搴﹀弬鏁?    voice_profile: str | None = None,
) -> TtsSynthResult:
    # M26-I锛歟motion_intensity 鑼冨洿鎴柇
    if emotion_intensity is not None:
        emotion_intensity = max(0.0, min(1.0, float(emotion_intensity)))
```

浣?`_synthesize_audio()` 璋冪敤鏃讹細

```python
audio_data, sr = _synthesize_audio(
    model=self._model,
    text=text.strip(),
    language=language or self._config.language,
    emotion_tag=emotion_tag,
    emotion_intensity=emotion_intensity,   # 鈫?浼犻€掍簡 emotion_intensity
    timeout_s=self._config.synth_timeout_s,
)
```

`_synthesize_audio()` 鍐呴儴锛?
```python
def _synthesize_audio(
    *,
    model: Any,
    text: str,
    language: str,
    emotion_tag: str | None,
    emotion_intensity: float | None = None,   # 鈫?鎺ユ敹浜?emotion_intensity
    timeout_s: float,
) -> tuple[Any, int]:
    # ...
    result = model.synthesize(
        text=text,
        language=language,
        emotion=emotion_tag,
        # 鈫?emotion_intensity 娌℃湁浼犻€掔粰 model.synthesize()锛?    )
```

`_ModernSoVITSAdapter.synthesize()` 鐨勫弬鏁帮細

```python
def synthesize(
    self,
    *,
    text: str,
    language: str,
    emotion: str | None = None,
    # 鈫?娌℃湁 emotion_intensity 鍙傛暟锛?) -> dict[str, Any]:
```

杩欐剰鍛崇潃锛?1. `emotion_intensity` 鍙傛暟鍦?`SoVITSModelManager.synthesize()` 涓鎺ユ敹锛屼絾鍦?`_synthesize_audio()` 涓病鏈変紶閫掔粰 `model.synthesize()`
2. `_ModernSoVITSAdapter.synthesize()` 娌℃湁 `emotion_intensity` 鍙傛暟锛屽嵆浣夸紶閫掍簡涔熸棤娉曚娇鐢?3. 杩欐槸涓€涓?*鍙傛暟浼犻€掓柇璺?*鈥斺€擿emotion_intensity` 鍦ㄦ暣涓?TTS 閾捐矾涓"鍚炴帀"浜嗭紝瀵瑰疄闄呰闊冲悎鎴愭病鏈変换浣曞奖鍝?
**鏂规锛?* 淇 `emotion_intensity` 鐨勪紶閫掗摼璺細

```python
# 馃啎 _synthesize_audio() 浼犻€?emotion_intensity锛坴1.7 鏂板锛?def _synthesize_audio(
    *,
    model: Any,
    text: str,
    language: str,
    emotion_tag: str | None,
    emotion_intensity: float | None = None,
    timeout_s: float,
) -> tuple[Any, int]:
    result = model.synthesize(
        text=text,
        language=language,
        emotion=emotion_tag,
        emotion_intensity=emotion_intensity,  # 馃啎 浼犻€?emotion_intensity
    )
```

**`_ModernSoVITSAdapter.synthesize()` 澧炲姞 `emotion_intensity` 鍙傛暟锛?*

```python
def synthesize(
    self,
    *,
    text: str,
    language: str,
    emotion: str | None = None,
    emotion_intensity: float | None = None,  # 馃啎 鏂板鍙傛暟
) -> dict[str, Any]:
    # ...
    inputs = {
        "text": text,
        "text_lang": text_lang,
        "ref_audio_path": self._ref_audio_path,
        # ...
        # 馃啎 鎯呮劅寮哄害褰卞搷 temperature锛堣姘斿箙搴︼級
        # emotion_intensity=1.0 鈫?temperature=1.0锛堟渶澶ц姘斿箙搴︼級
        # emotion_intensity=0.0 鈫?temperature=0.3锛堟渶灏忚姘斿箙搴︼級
        # emotion_intensity=None 鈫?temperature=0.7锛堥粯璁わ級
        "temperature": _map_intensity_to_temperature(emotion_intensity),
    }

def _map_intensity_to_temperature(intensity: float | None) -> float:
    """灏嗘儏鎰熷己搴︽槧灏勫埌 TTS temperature锛堣姘斿箙搴︼級銆?""
    if intensity is None:
        return 0.7  # 榛樿
    clamped = max(0.0, min(1.0, float(intensity)))
    # 绾挎€ф槧灏勶細[0.0, 1.0] 鈫?[0.3, 1.0]
    return round(0.3 + clamped * 0.7, 3)
```

**棰勬湡鏁堟灉锛?* TTS 鎯呮劅寮哄害浠?鍙傛暟鏂矾"鍙樹负"鍏ㄩ摼璺紶閫?锛宍emotion_intensity=1.0` 鏃惰闊冲悎鎴愪娇鐢ㄦ渶澶ц姘斿箙搴︼紝`emotion_intensity=0.0` 鏃朵娇鐢ㄦ渶灏忚姘斿箙搴︼紝瀹炵幇鎯呮劅寮哄害瀵硅闊宠緭鍑虹殑瀹為檯褰卞搷銆?
---

### 馃煟 娣卞寲浼樺寲48锛圥2锛夛細`ASRTranscriptEvent` 涓嶅惈鐢ㄦ埛鎯呮劅鎺ㄦ柇瀛楁鈥斺€擜SR 鎰熺煡灞傛儏鎰熺洸

**鐩爣鏂囦欢锛?* `edge_client/senses/asr.py`

**绮捐鍙戠幇锛?*

`ASRTranscriptEvent` 鐨勫瓧娈靛畾涔夛細

```python
@dataclass(frozen=True)
class ASRTranscriptEvent:
    event_id: str
    session_id: str
    segment_id: str
    text: str
    confidence: float
    ts_start_ms: int
    ts_end_ms: int
    is_final: bool
    alternates: tuple[str, ...]
    evidence_ref: str
    low_confidence: bool
    reason_code: str | None
    # 鈫?娌℃湁鐢ㄦ埛鎯呮劅鎺ㄦ柇瀛楁锛?```

`build_asr_transcript_event()` 鐨勫伐鍘傚嚱鏁帮細

```python
def build_asr_transcript_event(
    *,
    session_id: str,
    segment_id: str,
    text: str,
    confidence: float,
    ts_start_ms: int,
    ts_end_ms: int,
    is_final: bool = True,
    alternates: list[str] | None = None,
    evidence_ref: str | None = None,
    event_id: str | None = None,
) -> ASRTranscriptEvent:
    # 鈫?娌℃湁鐢ㄦ埛鎯呮劅鎺ㄦ柇鍙傛暟锛?```

杩欐剰鍛崇潃锛?1. ASR 杞啓浜嬩欢涓嶅惈鐢ㄦ埛鎯呮劅鎺ㄦ柇瀛楁鈥斺€斿嵆浣?faster-whisper 鍙互閫氳繃璇煶鐗瑰緛锛堣閫熴€侀煶璋冦€佸仠椤匡級鎺ㄦ柇鐢ㄦ埛鎯呮劅锛岃繖浜涗俊鎭篃鏃犳硶浼犻€掔粰 Host
2. `ASRTranscriptEvent` 鍙湁鏂囨湰鍐呭锛屾病鏈夎闊崇壒寰佷俊鎭紙璇€熴€侀煶璋冨彉鍖栥€佸仠椤挎椂闀匡級鈥斺€擧ost 鏃犳硶鎰熺煡鐢ㄦ埛鐨勬儏鎰熺姸鎬?3. 浼樺寲42涓彁鍒扮殑 `user_emotion_hint` 瀛楁锛堝湪 `EventReportItem` 涓級娌℃湁鏁版嵁鏉ユ簮鈥斺€斿洜涓?ASR 灞傛牴鏈笉鎻愬彇鐢ㄦ埛鎯呮劅淇℃伅

**鏂规锛?* 涓?`ASRTranscriptEvent` 澧炲姞鐢ㄦ埛鎯呮劅鎺ㄦ柇瀛楁锛?
```python
@dataclass(frozen=True)
class ASRTranscriptEvent:
    event_id: str
    session_id: str
    segment_id: str
    text: str
    confidence: float
    ts_start_ms: int
    ts_end_ms: int
    is_final: bool
    alternates: tuple[str, ...]
    evidence_ref: str
    low_confidence: bool
    reason_code: str | None
    # 馃啎 鐢ㄦ埛鎯呮劅鎺ㄦ柇瀛楁锛坴1.7 鏂板锛?    speech_rate_wpm: float | None = None      # 璇€燂紙璇?鍒嗛挓锛夛紝楂樿閫熷彲鑳借〃绀哄叴濂?鐒﹁檻
    pause_ratio: float | None = None          # 鍋滈】姣斾緥锛?.0~1.0锛夛紝楂樺仠椤垮彲鑳借〃绀烘偛浼?鐘硅鲍
    user_emotion_hint: str | None = None      # 鐢ㄦ埛鎯呮劅鎺ㄦ柇锛?EXCITED"/"SAD"/"ANXIOUS" 绛夛級
    user_emotion_confidence: float | None = None  # 鎯呮劅鎺ㄦ柇缃俊搴︼紙0.0~1.0锛?```

**`build_asr_transcript_event()` 澧炲姞鐢ㄦ埛鎯呮劅鎺ㄦ柇鍙傛暟锛?*

```python
def build_asr_transcript_event(
    *,
    session_id: str,
    segment_id: str,
    text: str,
    confidence: float,
    ts_start_ms: int,
    ts_end_ms: int,
    is_final: bool = True,
    alternates: list[str] | None = None,
    evidence_ref: str | None = None,
    event_id: str | None = None,
    # 馃啎 鐢ㄦ埛鎯呮劅鎺ㄦ柇鍙傛暟
    speech_rate_wpm: float | None = None,
    pause_ratio: float | None = None,
    user_emotion_hint: str | None = None,
    user_emotion_confidence: float | None = None,
) -> ASRTranscriptEvent:
```

**璇€?鍋滈】鑷姩璁＄畻锛?*

```python
def _estimate_speech_features(
    *,
    text: str,
    ts_start_ms: int,
    ts_end_ms: int,
) -> dict[str, float | None]:
    """浠庤浆鍐欐枃鏈拰鏃堕棿鎴充及绠楄闊崇壒寰併€?""
    duration_s = max(0.001, (ts_end_ms - ts_start_ms) / 1000.0)
    word_count = len(text.split())
    speech_rate_wpm = round(word_count / duration_s * 60, 1) if word_count > 0 else None

    # 绠€鍗曠殑鍋滈】姣斾緥浼扮畻锛堝熀浜庢枃鏈腑鐨勬爣鐐圭鍙峰瘑搴︼級
    pause_chars = sum(1 for c in text if c in "锛屻€傦紒锛熲€︺€?.")
    pause_ratio = round(pause_chars / max(1, len(text)), 3)

    return {
        "speech_rate_wpm": speech_rate_wpm,
        "pause_ratio": pause_ratio,
    }
```

**棰勬湡鏁堟灉锛?* ASR 鎰熺煡灞備粠"鎯呮劅鐩?鍙樹负"鎯呮劅鎰熺煡"锛孉SR 杞啓浜嬩欢鎼哄甫鐢ㄦ埛鎯呮劅鎺ㄦ柇淇℃伅锛孒ost 鍙互鎰熺煡鐢ㄦ埛鐨勬儏鎰熺姸鎬侊紙閫氳繃璇€熴€佸仠椤跨瓑璇煶鐗瑰緛锛夛紝涓轰紭鍖?2涓殑 `user_emotion_hint` 瀛楁鎻愪緵鏁版嵁鏉ユ簮锛屽疄鐜?鐢ㄦ埛鎯呮劅鈫扐I鎯呮劅鍝嶅簲"鐨勫畬鏁撮棴鐜€?
---

## 浜屽崄涓冦€佺鍏疆浼樺寲鐨勬牳蹇冧环鍊兼€荤粨

绗叓杞紭鍖栬仛鐒︿簬**鍩虹璁炬柦鎯呮劅鐩插尯**鈥斺€旀儏鎰熶俊鎭湪"璋冨害鈫扡LM缃戝叧鈫掑浘鎵ц鈫掕娴嬧啋TTS鈫扐SR"鍩虹璁炬柦閾捐矾涓殑绯荤粺鎬х洸鍖猴細

| 浼樺寲椤?| 鍙戠幇鐨?鍩虹璁炬柦鐩插尯" | 褰卞搷鑼冨洿 | 淇鏂规 |
|--------|-----------------|---------|---------|
| **浼樺寲43** 馃敶 | 璋冨害浠诲姟瑙﹀彂涓嶆劅鐭ユ儏鎰熺姸鎬侊紝critical 鐘舵€佷笅楂樻秷鑰椾换鍔′粛琚Е鍙?| `scheduler.py` 鈫?璋冨害灞?| 澧炲姞鎯呮劅闂ㄦ帶锛宑ritical 鐘舵€佷笅鏆傜紦 MEDIUM/HIGH 浼樺厛绾т换鍔?|
| **浼樺寲44** 馃煚 | LLM 璋冪敤涓嶆惡甯︽儏鎰熶笂涓嬫枃锛岄檷绾у洖澶嶄笉鎰熺煡鎯呮劅鐘舵€?| `llm_gateway.py` 鈫?LLM 缃戝叧 | 澧炲姞 emotion_context 鍙傛暟锛岄檷绾у洖澶嶆牴鎹儏鎰熺姸鎬侀€夋嫨鏂囨湰 |
| **浼樺寲45** 馃煛 | 鍥捐妭鐐规墽琛?context 涓嶅惈鎯呮劅瀛楁锛孏raphState 鏃犳儏鎰熷瓧娈?| `graph_executor.py` 鈫?鍥炬墽琛?| GraphState 澧炲姞鎯呮劅瀛楁锛宧andler context 浼犻€掓儏鎰熺姸鎬?|
| **浼樺寲46** 馃煝 | 瑙傛祴鎸囨爣涓嶅惈鎯呮劅缁村害锛岃繍缁存棤娉曟劅鐭ユ儏鎰熺姸鎬佸彉鍖栬秼鍔?| `obs/metrics.py` 鈫?瑙傛祴灞?| 澧炲姞 emotion_stats 鎸囨爣锛宺ecent_chains 澧炲姞鎯呮劅瀛楁 |
| **浼樺寲47** 馃數 | TTS emotion_intensity 鍙傛暟鍦ㄩ摼璺腑琚?鍚炴帀"锛屽璇煶鍚堟垚鏃犲疄闄呭奖鍝?| `tts_engine.py` 鈫?TTS 寮曟搸 | 淇鍙傛暟浼犻€掓柇璺紝emotion_intensity 鏄犲皠鍒?TTS temperature |
| **浼樺寲48** 馃煟 | ASR 杞啓浜嬩欢涓嶅惈鐢ㄦ埛鎯呮劅鎺ㄦ柇锛孒ost 鏃犳硶鎰熺煡鐢ㄦ埛鎯呮劅鐘舵€?| `senses/asr.py` 鈫?ASR 鎰熺煡 | 澧炲姞 speech_rate_wpm/pause_ratio/user_emotion_hint 瀛楁 |

---

### 绗叓杞殑鐗规畩鍙戠幇锛?*浼樺寲47 鏄竴涓?鍙傛暟榛戞礊"**

浼樺寲47锛圱TS emotion_intensity 鏂矾锛夋槸涓€涓壒娈婄殑闂鈥斺€擿emotion_intensity` 鍙傛暟鍦?`SoVITSModelManager.synthesize()` 涓鎺ユ敹锛屽湪 `_synthesize_audio()` 涓浼犻€掞紝浣嗗湪 `_ModernSoVITSAdapter.synthesize()` 涓瀹屽叏蹇界暐锛屾病鏈変紶閫掔粰搴曞眰 SDK銆傝繖鏄竴涓吀鍨嬬殑"鍙傛暟榛戞礊"鈥斺€斿弬鏁板湪鏁翠釜璋冪敤閾句腑琚紶閫掞紝浣嗘渶缁堟病鏈変换浣曞疄闄呮晥鏋溿€?
### 绗叓杞殑鍙︿竴涓壒娈婂彂鐜帮細**浼樺寲48 鏄紭鍖?2鐨?鏁版嵁鏉ユ簮"**

浼樺寲48锛圓SR 鐢ㄦ埛鎯呮劅鎺ㄦ柇锛変笌浼樺寲42锛圗ventReportItem 鐨?`user_emotion_hint` 瀛楁锛夊舰鎴愪簡涓€涓畬鏁寸殑闂幆锛氫紭鍖?2涓?`EventReportItem` 澧炲姞浜?`user_emotion_hint` 瀛楁锛屼絾杩欎釜瀛楁鐨勬暟鎹潵婧愭槸 ASR 灞傗€斺€斿鏋?ASR 灞備笉鎻愬彇鐢ㄦ埛鎯呮劅淇℃伅锛宍user_emotion_hint` 瀛楁灏辨案杩滄槸 `None`銆備紭鍖?8淇浜嗚繖涓暟鎹潵婧愰棶棰樸€?
---

## 浜屽崄鍏€佸畬鏁翠紭鍖栧叏鏅浘锛坴1.7锛?8椤癸級

### 鍏疆浼樺寲鐨勫眰娆￠€掕繘

```
绗竴杞紙v1.0锛夛細鍙戠幇5涓?缂哄け鍔熻兘"       鈫?闇€瑕佹柊澧炰粈涔?绗簩杞紙v1.1锛夛細鍙戠幇6涓?娣卞眰闂"       鈫?鐜版湁鏈哄埗鍝噷涓嶅娣?绗笁杞紙v1.2锛夛細鍙戠幇6涓?鍋囧姛鑳界┖澹?     鈫?鐜版湁浠ｇ爜鍝噷鐪嬭捣鏉ユ湁浣嗗疄闄呮病鐢?绗洓杞紙v1.3锛夛細鍙戠幇6涓?鏋舵瀯鏂眰"       鈫?鍔熻兘涔嬮棿鐨勮繛鎺ュ湪鍝噷鏂
绗簲杞紙v1.4锛夛細鍙戠幇6涓?璺ㄦā鍧楃洸鍖?     鈫?鎯呮劅淇℃伅鍦ㄥ叏閾捐矾涓殑绯荤粺鎬х己澶?绗叚杞紙v1.5锛夛細鍙戠幇6涓?鎰熺煡灞傛柇璺?     鈫?鎯呮劅淇℃伅鍦ㄦ劅鐭モ啋鍐崇瓥鈫掕緭鍑衡啋瑙傛祴涓殑鏂矾
绗竷杞紙v1.6锛夛細鍙戠幇6涓?鎵ц灞傜湡绌?     鈫?鎯呮劅淇℃伅鍦ㄦ墽琛屸啋鍙嶉鈫掕竟缂樼閾捐矾涓殑鐪熺┖
绗叓杞紙v1.7锛夛細鍙戠幇6涓?鍩虹璁炬柦鐩插尯"   鈫?鎯呮劅淇℃伅鍦ㄨ皟搴︹啋LLM鈫掑浘鎵ц鈫掕娴嬧啋TTS鈫扐SR涓殑鐩插尯
```

### 鎸変紭鍏堢骇鍒嗙被锛?8椤瑰叏鏅級

| 浼樺厛绾?| 浼樺寲椤?| 鏍稿績闂 |
|--------|--------|---------|
| **P0锛堝繀椤讳慨澶嶏級** | OPT19, OPT20, OPT22, OPT25, OPT26, OPT27, OPT31, OPT32, OPT37, OPT38, OPT43 | 鏋舵瀯鏂眰 + 璺ㄦā鍧楃洸鍖?+ 鎰熺煡灞傛柇璺?+ 鎵ц灞傜湡绌?+ 璋冨害灞傛儏鎰熺洸 |
| **P1锛堥噸瑕佷紭鍖栵級** | OPT1, OPT6, OPT7, OPT8, OPT13, OPT14, OPT16, OPT17, OPT18, OPT21, OPT28, OPT29, OPT33, OPT34, OPT35, OPT39, OPT40, OPT41, OPT44, OPT45, OPT46, OPT47, OPT48 | 鎯呮劅鍐呮牳 + 鍏崇郴灞?+ 璁板繂灞?+ 鎰熺煡灞?+ 鎵ц灞?+ 鍩虹璁炬柦灞?|
| **P2锛堜綋楠屽寮猴級** | OPT2, OPT3, OPT4, OPT5, OPT9, OPT10, OPT11, OPT12, OPT15, OPT23, OPT24, OPT30, OPT36, OPT42 | 杈撳嚭灞?+ 缁嗚妭鎵撶（ + 杈圭紭绔?|

### 鎯呮劅淇℃伅鍏ㄩ摼璺鐩栧浘锛坴1.7 鏈€缁堢増锛?
```
鐢ㄦ埛璇煶杈撳叆
    鈫?[ASR 鎰熺煡灞俔 edge_client/senses/asr.py
    OPT48: ASRTranscriptEvent 鐢ㄦ埛鎯呮劅鎺ㄦ柇瀛楁锛堟柊澧烇級鈫?鏁版嵁鏉ユ簮
    鈫?[杈圭紭绔簨浠朵笂鎶 edge_client/rpc/event_reporter.py
    OPT42: EventReportItem 鎯呮劅瀛楁 + 璺ㄧ鏋氫妇缁熶竴锛堢涓冭疆锛?    鈫?[瑙嗚鎰熺煡灞俔 vision/camera_event.py
    OPT35: 瑙嗚璇嗗埆 鈫?鎯呮劅瑙﹀彂锛堢鍏疆锛?    鈫?[璋冨害灞俔 scheduler/scheduler.py
    OPT43: 鎯呮劅闂ㄦ帶 + system_intent 鎼哄甫鎯呮劅鐘舵€侊紙鏂板锛?    鈫?[鍙洖绠＄嚎灞俔 retrieval/pipeline.py
    OPT41: emotion_context 鍙傛暟璐€氾紙绗竷杞級
    鈫?[鍙洖灞俔 retrieval/recall_fusion.py
    OPT31: 鎯呮劅缁村害璺敱锛堢鍏疆锛?    OPT36: 鎯呮劅寮哄害鍔犳潈铻嶅悎锛堢鍏疆锛?    鈫?[閲嶆帓搴忓眰] retrieval/reranker.py
    OPT40: 鎯呮劅鍋忓ソ閲嶆帓锛堢涓冭疆锛?    鈫?[鍐崇瓥灞俔 governor/decision.py
    OPT32: R8 鎯呮劅淇濇姢闂ㄦ帶锛堢鍏疆锛?    鈫?[鎯呮劅鍐呮牳] core/metabolism.py
    OPT6/7/8: EmotionVector 娣卞寲锛堢浜岃疆锛?    鈫?[鍏崇郴灞俔 bond/
    OPT26: 鎯呮劅鍔犳潈 Bond 绉垎锛堢浜旇疆锛?    OPT27: Bond Summary 淇锛堢浜旇疆锛?    鈫?[璁板繂灞俔 memory/
    OPT25/28/29: 鎯呮劅璁板繂鏍囨敞/淇濆瓨/钂搁锛堢浜旇疆锛?    鈫?[杈撳嚭灞俔 core/qa.py + core/prompt_context.py
    OPT19/20/22: 鏋舵瀯鏂眰淇锛堢鍥涜疆锛?    鈫?[LLM 缃戝叧灞俔 integrations/llm_gateway.py
    OPT44: emotion_context 娉ㄥ叆 prompt_payload锛堟柊澧烇級
    鈫?[鍥炬墽琛屽眰] integrations/graph_executor.py
    OPT45: GraphState 鎯呮劅瀛楁 + handler context 浼犻€掞紙鏂板锛?    鈫?[宸ュ叿娉ㄥ唽灞俔 mcp/tool_registry.py
    OPT39: ToolMeta 鎯呮劅閫傜敤鎬у瓧娈碉紙绗竷杞級
    鈫?[鎵ц灞俔 skills/loader.py + tools/broker.py
    OPT37: 鎶€鑳芥墽琛?context 鎯呮劅瀛楁锛堢涓冭疆锛?    OPT38: 宸ュ叿鎵ц context 鎯呮劅瀛楁锛堢涓冭疆锛?    鈫?[閫氶亾灞俔 channels/vts.py
    OPT34: VTS 琛ㄦ儏鏄犲皠鎵╁睍锛堢鍏疆锛?    鈫?[TTS 寮曟搸灞俔 edge_client/actuators/tts_engine.py
    OPT47: emotion_intensity 鍙傛暟榛戞礊淇锛堟柊澧烇級
    鈫?[瑙傛祴灞俔 observability/cross_timeline.py + obs/metrics.py
    OPT33: 鎯呮劅浜嬩欢绾冲叆鏃堕棿绾匡紙绗叚杞級
    OPT46: emotion_stats 鎸囨爣锛堟柊澧烇級
    鈫?鐢ㄦ埛鎰熺煡鍒扮殑鎯呮劅杈撳嚭锛堣闊?+ 琛ㄦ儏 + 鏂囧瓧锛?```

---

## 浜屽崄涓夈€佺涓冭疆娣卞寲浼樺寲锛氭墽琛屽眰鎯呮劅鐪熺┖

> **v1.6 鏂板**锛?026-03-05锛夈€傛湰杞熀浜庡 `skills/loader.py`銆乣tools/broker.py`銆乣mcp/tool_registry.py`銆乣retrieval/reranker.py`銆乣retrieval/pipeline.py`銆乣edge_client/actuators/vts_expression_map.py`銆乣edge_client/rpc/event_reporter.py` 鐨勫畬鏁撮€愯绮捐锛屽彂鐜颁簡鍓嶅叚杞畬鍏ㄦ湭瑙﹀強鐨?**6 涓墽琛屽眰鎯呮劅鐪熺┖**鈥斺€旇繖浜涢棶棰樺垎甯冨湪鎶€鑳芥墽琛屽眰銆佸伐鍏蜂唬鐞嗗眰銆丮CP娉ㄥ唽灞傘€侀噸鎺掑簭灞傘€佸彫鍥炵绾垮眰銆佽竟缂樼琛ㄦ儏鏄犲皠灞傦紝鏄儏鎰熶俊鎭湪"鎵ц鈫掑弽棣堚啋杈圭紭绔?閾捐矾涓殑绯荤粺鎬х湡绌恒€?
---

### 馃敶 娣卞寲浼樺寲37锛圥0锛夛細`SkillLoader.execute()` 鐨?`context` 鍙傛暟涓嶄紶閫掓儏鎰熺姸鎬佲€斺€旀妧鑳芥墽琛屾椂鎯呮劅鐩?
**鐩爣鏂囦欢锛?* `host_brain/skills/loader.py`

**绮捐鍙戠幇锛?*

`execute()` 鐨?`context` 鍙傛暟锛?
```python
def execute(
    self,
    *,
    intent: dict[str, Any],
    governor_output: dict[str, Any],
    context: dict[str, Any] | None = None,   # 鈫?鑷敱鏍煎紡 dict锛屾病鏈夋儏鎰熷瓧娈电害瀹?    now_ts_ms: int | None = None,
) -> SkillExecutionResult:
```

`handler_result = meta.handler(args, context)` 璋冪敤鏃讹細

```python
try:
    handler_result = meta.handler(args, context)
    # 鈫?context 浼犵粰鎶€鑳?handler锛屼絾 context 閲屾病鏈夋儏鎰熷瓧娈碉紒
```

`_log_and_return()` 鍐欏叆瀹¤鐨?row锛?
```python
row = {
    "request_id": request_id or None,
    "thread_id": thread_id,
    "turn_id": turn_id,
    "intent_id": intent_id,
    "skill_name": skill_name,
    "approval_token_ref": approval_token_ref,
    "confirm_token_ref": confirm_token_ref,
    "result_status": result_status,
    "result_code": result_code,
    "result_digest": result_digest,
    "evidence_ref": evidence_ref,
    "manifest_ref": manifest_ref,
    "undo_token_ref": undo_token_ref,
    "ts_ms": ts_ms,
    # 鈫?娌℃湁 emotion_tag銆乵ood_state 瀛楁锛?}
```

杩欐剰鍛崇潃锛?1. 鎶€鑳芥墽琛屾椂瀹屽叏涓嶆劅鐭ユ儏鎰熺姸鎬佲€斺€擿EXCITED` 鐘舵€佷笅鎵ц鐨勬妧鑳藉拰 `SAD` 鐘舵€佷笅鎵ц鐨勬妧鑳藉緱鍒扮浉鍚岀殑 context
2. 鎶€鑳?handler 鏃犳硶鏍规嵁鎯呮劅鐘舵€佽皟鏁存墽琛岃涓猴紙渚嬪锛歚EXCITED` 鐘舵€佷笅鐨勯煶涔愭挱鏀炬妧鑳藉簲璇ラ€夋嫨娆㈠揩鐨勬瓕鏇诧級
3. 鎶€鑳芥墽琛屽璁℃棩蹇椾笉鍚儏鎰熷瓧娈碉紝鏃犳硶浜嬪悗鍒嗘瀽"鍝簺鎯呮劅鐘舵€佷笅鎶€鑳芥墽琛屾垚鍔熺巼鏇撮珮"

**鏂规锛?* 涓?`context` 澧炲姞鎯呮劅瀛楁绾﹀畾锛屽苟鍦ㄥ璁℃棩蹇椾腑璁板綍鎯呮劅鐘舵€侊細

```python
# 馃啎 鎶€鑳芥墽琛?context 鎯呮劅瀛楁绾﹀畾锛坴1.6 鏂板锛?SKILL_CONTEXT_EMOTION_FIELDS = {
    "emotion_tag": str,        # AI 褰撳墠鎯呮劅鏍囩锛?NEUTRAL"/"HAPPY"/"SAD" 绛夛級
    "mood_state": str,         # AI 褰撳墠鎯呮劅鐘舵€侊紙"engaged"/"steady"/"calm"/"tired"/"critical"锛?    "bond_stage": str,         # 褰撳墠 Bond 闃舵锛?STRANGER"/"FRIEND" 绛夛級
    "emotional_intensity": float,  # 鎯呮劅寮哄害锛?.0~1.0锛?}

def execute(
    self,
    *,
    intent: dict[str, Any],
    governor_output: dict[str, Any],
    context: dict[str, Any] | None = None,
    now_ts_ms: int | None = None,
) -> SkillExecutionResult:
    # ...
    # 馃啎 浠?context 涓彁鍙栨儏鎰熷瓧娈碉紝鐢ㄤ簬瀹¤鏃ュ織
    emotion_tag = str((context or {}).get("emotion_tag") or "NEUTRAL").upper()
    mood_state = str((context or {}).get("mood_state") or "steady").lower()
    bond_stage = str((context or {}).get("bond_stage") or "STRANGER").upper()
```

**瀹¤鏃ュ織澧炲姞鎯呮劅瀛楁锛?*

```python
row = {
    # ... 鍘熸湁瀛楁 ...
    # 馃啎 鎯呮劅瀛楁
    "emotion_tag": emotion_tag,
    "mood_state": mood_state,
    "bond_stage": bond_stage,
}
```

**棰勬湡鏁堟灉锛?* 鎶€鑳芥墽琛屼粠"鎯呮劅鐩?鍙樹负"鎯呮劅鎰熺煡"锛屾妧鑳?handler 鍙互鏍规嵁鎯呮劅鐘舵€佽皟鏁存墽琛岃涓猴紝瀹¤鏃ュ織鍙互鍒嗘瀽鎯呮劅鐘舵€佷笌鎶€鑳芥墽琛岀粨鏋滅殑鐩稿叧鎬с€?
---

### 馃煚 娣卞寲浼樺寲38锛圥0锛夛細`ToolBroker.execute()` 鐨?`context` 鍙傛暟涓嶄紶閫掓儏鎰熺姸鎬佲€斺€斿伐鍏锋墽琛屾椂鎯呮劅鐩?
**鐩爣鏂囦欢锛?* `host_brain/tools/broker.py`

**绮捐鍙戠幇锛?*

`execute()` 鐨?`context` 鍙傛暟锛?
```python
def execute(
    self,
    *,
    request_id: str | None,
    intent_id: str,
    session_id: str,
    seq: int,
    tool_id: str,
    args: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,   # 鈫?鑷敱鏍煎紡 dict锛屾病鏈夋儏鎰熷瓧娈电害瀹?    approval_token_ref: str | None = None,
    confirm_token_ref: str | None = None,
    thread_id: str | None = None,
    turn_id: str | None = None,
    ts_ms: int | None = None,
) -> ToolExecutionResult:
```

`output = handler(safe_args, context)` 璋冪敤鏃讹細

```python
try:
    output = handler(safe_args, context)
    # 鈫?context 浼犵粰宸ュ叿 handler锛屼絾 context 閲屾病鏈夋儏鎰熷瓧娈碉紒
```

`_log_and_return()` 鍐欏叆瀹¤鐨?row锛?
```python
row = {
    "request_id": request_id,
    "thread_id": thread_id,
    "turn_id": turn_id,
    "intent_id": intent_id,
    "skill_name": tool_id,
    "approval_token_ref": approval_token_ref,
    "confirm_token_ref": confirm_token_ref,
    "result_status": status,
    "result_code": result_code,
    "result_digest": result_digest,
    "evidence_ref": evidence_ref,
    "manifest_ref": None,
    "undo_token_ref": None,
    "ts_ms": ts_ms,
    # 鈫?娌℃湁 emotion_tag銆乵ood_state 瀛楁锛?}
```

杩欐剰鍛崇潃锛?1. 宸ュ叿鎵ц鏃跺畬鍏ㄤ笉鎰熺煡鎯呮劅鐘舵€佲€斺€擿HAPPY` 鐘舵€佷笅璋冪敤鐨勫伐鍏峰拰 `SAD` 鐘舵€佷笅璋冪敤鐨勫伐鍏峰緱鍒扮浉鍚岀殑 context
2. 宸ュ叿 handler 鏃犳硶鏍规嵁鎯呮劅鐘舵€佽皟鏁磋緭鍑猴紙渚嬪锛歚SAD` 鐘舵€佷笅鐨勫ぉ姘旀煡璇㈠伐鍏峰簲璇ラ檮鍔犳俯鏆栫殑璇皵锛?3. 宸ュ叿鎵ц瀹¤鏃ュ織涓嶅惈鎯呮劅瀛楁锛屾棤娉曞垎鏋愭儏鎰熺姸鎬佷笌宸ュ叿璋冪敤妯″紡鐨勭浉鍏虫€?
**鏂规锛?* 涓庝紭鍖?7涓€鑷达紝涓?`context` 澧炲姞鎯呮劅瀛楁绾﹀畾锛屽苟鍦ㄥ璁℃棩蹇椾腑璁板綍鎯呮劅鐘舵€侊細

```python
# 馃啎 宸ュ叿鎵ц context 鎯呮劅瀛楁绾﹀畾锛坴1.6 鏂板锛?TOOL_CONTEXT_EMOTION_FIELDS = {
    "emotion_tag": str,
    "mood_state": str,
    "bond_stage": str,
    "emotional_intensity": float,
}

def execute(self, ...) -> ToolExecutionResult:
    # ...
    # 馃啎 浠?context 涓彁鍙栨儏鎰熷瓧娈?    emotion_tag = str((context or {}).get("emotion_tag") or "NEUTRAL").upper()
    mood_state = str((context or {}).get("mood_state") or "steady").lower()
```

**棰勬湡鏁堟灉锛?* 宸ュ叿鎵ц浠?鎯呮劅鐩?鍙樹负"鎯呮劅鎰熺煡"锛屽伐鍏?handler 鍙互鏍规嵁鎯呮劅鐘舵€佽皟鏁磋緭鍑洪鏍硷紝瀹¤鏃ュ織鍙互鍒嗘瀽鎯呮劅鐘舵€佷笌宸ュ叿璋冪敤妯″紡鐨勭浉鍏虫€с€?
---

### 馃煛 娣卞寲浼樺寲39锛圥1锛夛細`ToolMeta` 娌℃湁鎯呮劅閫傜敤鎬у瓧娈碘€斺€擬CP 宸ュ叿娉ㄥ唽涓嶆劅鐭ユ儏鎰熷満鏅?
**鐩爣鏂囦欢锛?* `host_brain/mcp/tool_registry.py`

**绮捐鍙戠幇锛?*

`ToolMeta` 鐨勫瓧娈靛畾涔夛細

```python
@dataclass
class ToolMeta:
    provider_id: str
    tool_id: str
    name: str
    description: str
    access_level: str          # PUBLIC | INTERNAL | RESTRICTED
    risk_level: str            # LOW | MEDIUM | HIGH
    reversible: bool
    version: str = "1.0.0"
    schema_hash: str = ""
    domain_tags: list[str] = field(default_factory=list)
    # 鈫?娌℃湁鎯呮劅閫傜敤鎬у瓧娈碉紒
```

`VALID_ACCESS_LEVELS` 鍜?`VALID_RISK_LEVELS` 鐨勫畾涔夛細

```python
VALID_ACCESS_LEVELS = frozenset({"PUBLIC", "INTERNAL", "RESTRICTED"})
VALID_RISK_LEVELS = frozenset({"LOW", "MEDIUM", "HIGH"})
# 鈫?娌℃湁鎯呮劅鍦烘櫙绾︽潫锛?```

杩欐剰鍛崇潃锛?1. MCP 宸ュ叿娉ㄥ唽鏃舵病鏈夋儏鎰熼€傜敤鎬х害鏉熲€斺€擿HIGH` 椋庨櫓宸ュ叿鍦?`critical` 鎯呮劅鐘舵€佷笅涔熷彲浠ヨ璋冪敤
2. 宸ュ叿鐨?`domain_tags` 鏄嚜鐢卞瓧绗︿覆鍒楄〃锛屾病鏈夋儏鎰熷満鏅爣绛剧殑绾﹀畾锛堝 `emotion:happy_only`銆乣mood:engaged_required`锛?3. `prompt_context.py` 鐨?`tool_meta_pack` 妲戒綅娉ㄥ叆宸ュ叿鍏冩暟鎹椂锛孡LM 鏃犳硶鐭ラ亾鍝簺宸ュ叿閫傚悎褰撳墠鎯呮劅鐘舵€?
**鏂规锛?* 涓?`ToolMeta` 澧炲姞鎯呮劅閫傜敤鎬у瓧娈碉細

```python
# 馃啎 鎯呮劅閫傜敤鎬х害鏉燂紙v1.6 鏂板锛?VALID_EMOTION_REQUIREMENTS = frozenset({
    "NONE",           # 鏃犳儏鎰熻姹傦紙榛樿锛?    "POSITIVE_ONLY",  # 鍙湪姝ｉ潰鎯呮劅锛圚APPY/EXCITED/CALM锛変笅鍙敤
    "CALM_REQUIRED",  # 闇€瑕佸钩闈欑姸鎬侊紙CALM/NEUTRAL锛?    "ENGAGED_ONLY",   # 鍙湪 engaged mood_state 涓嬪彲鐢?    "ANY",            # 浠讳綍鎯呮劅鐘舵€佸潎鍙?})

@dataclass
class ToolMeta:
    provider_id: str
    tool_id: str
    name: str
    description: str
    access_level: str
    risk_level: str
    reversible: bool
    version: str = "1.0.0"
    schema_hash: str = ""
    domain_tags: list[str] = field(default_factory=list)
    # 馃啎 鎯呮劅閫傜敤鎬у瓧娈?    emotion_requirement: str = "NONE"    # 鎯呮劅鍦烘櫙绾︽潫
    min_bond_stage: str = "STRANGER"     # 鏈€浣?Bond 闃舵瑕佹眰
    energy_cost: str = "LOW"             # 鎵ц鑳介噺娑堣€楋紙LOW/MEDIUM/HIGH锛?```

**鍦?`prompt_context.py` 鐨?`tool_meta_pack` 涓敞鍏ユ儏鎰熼€傜敤鎬т俊鎭細**

```python
# 馃啎 tool_meta_pack 澧炲姞鎯呮劅閫傜敤鎬у瓧娈碉紙v1.6 鏂板锛?tool_meta_item = {
    "tool_id": meta.tool_id,
    "name": meta.name,
    "description": meta.description,
    "risk_level": meta.risk_level,
    "domain_tags": meta.domain_tags,
    # 馃啎 鎯呮劅閫傜敤鎬?    "emotion_requirement": meta.emotion_requirement,
    "min_bond_stage": meta.min_bond_stage,
    "energy_cost": meta.energy_cost,
    # 馃啎 褰撳墠鎯呮劅鐘舵€佷笅鏄惁閫傜敤锛堢敱绯荤粺璁＄畻锛屼笉鏆撮湶鍘熷鏁板€硷級
    "available_in_current_mood": _check_emotion_availability(meta, current_mood_state),
}
```

**棰勬湡鏁堟灉锛?* MCP 宸ュ叿娉ㄥ唽浠?鎯呮劅鐩?鍙樹负"鎯呮劅鎰熺煡"锛孡LM 鍦ㄩ€夋嫨宸ュ叿鏃跺彲浠ユ劅鐭ュ摢浜涘伐鍏烽€傚悎褰撳墠鎯呮劅鐘舵€侊紝閬垮厤鍦?`critical` 鐘舵€佷笅璋冪敤楂樿兘閲忔秷鑰楃殑宸ュ叿銆?
---

### 馃煝 娣卞寲浼樺寲40锛圥1锛夛細`RerankerServiceContract.rerank()` 涓嶆劅鐭ユ儏鎰熺姸鎬佲€斺€旈噸鎺掑簭鏃舵儏鎰熺洸

**鐩爣鏂囦欢锛?* `host_brain/retrieval/reranker.py`

**绮捐鍙戠幇锛?*

`rerank()` 鐨勫弬鏁帮細

```python
def rerank(
    self,
    *,
    request_id: str,
    query: str,
    candidates: list[dict[str, Any]] | Any,
    intent_id: str | None = None,
    top_k: int | None = None,
    timeout_ms: int | None = None,
    ts_ms: int | None = None,
    # 鈫?瀹屽叏娌℃湁鎯呮劅缁村害鍙傛暟锛?) -> RerankerResult:
```

`_default_rerank_provider()` 鐨勮瘎鍒嗛€昏緫锛?
```python
def _default_rerank_provider(
    query: str,
    candidates: list[dict[str, Any]],
) -> tuple[list[float], str]:
    query_tokens = set(_tokenize(query))
    denominator = max(1, len(query_tokens))
    scores: list[float] = []
    for idx, candidate in enumerate(candidates):
        snippet = str(candidate.get("snippet") or "")
        doc_id = str(candidate.get("doc_id") or "")
        candidate_tokens = set(_tokenize(f"{doc_id} {snippet}"))
        overlap = len(query_tokens.intersection(candidate_tokens))
        lexical_score = float(overlap) / float(denominator)
        recall_bias = _fallback_score_by_rank(idx, len(candidates)) * 0.01
        scores.append(round(min(1.0, lexical_score + recall_bias), 6))
    return scores, RERANK_MODEL_ID
    # 鈫?绾瘝姹囬噸鍙犺瘎鍒嗭紝瀹屽叏涓嶆劅鐭ユ儏鎰燂紒
```

`_build_topk()` 鐨勮緭鍑虹粨鏋勶細

```python
{
    "doc_id": ...,
    "source_ref": ...,
    "snippet": ...,
    "score": ...,
    "confidence": ...,
    "metadata": ...,
    # 鈫?娌℃湁鎯呮劅鐩稿叧瀛楁锛?}
```

杩欐剰鍛崇潃锛?1. 閲嶆帓搴忔椂瀹屽叏涓嶆劅鐭ユ儏鎰熺姸鎬佲€斺€斿綋鐢ㄦ埛璇?鎴戝緢闅捐繃锛屼綘杩樿寰楁垜浠紑蹇冪殑鏃跺€欏悧"锛岄噸鎺掑簭鍣ㄦ棤娉曚紭鍏堣繑鍥?`emotion_tag=HAPPY` 鐨勮蹇?2. `_default_rerank_provider()` 鐨勮瘝姹囬噸鍙犺瘎鍒嗕笉鎰熺煡鎯呮劅璇箟鈥斺€?寮€蹇?鍜?蹇箰"鐨勮涔夌浉浼兼€ф棤娉曡璇嗗埆
3. 閲嶆帓搴忚緭鍑虹殑 `topk` 涓嶅惈鎯呮劅瀛楁锛屼笅娓哥殑 `evidence_pack` 鏃犳硶鎰熺煡鎯呮劅缁村害

**鏂规锛?* 涓?`rerank()` 澧炲姞鎯呮劅鎰熺煡鍙傛暟锛屽苟鍦?`_build_topk()` 杈撳嚭涓繚鐣欐儏鎰熷瓧娈碉細

```python
def rerank(
    self,
    *,
    request_id: str,
    query: str,
    candidates: list[dict[str, Any]] | Any,
    intent_id: str | None = None,
    top_k: int | None = None,
    timeout_ms: int | None = None,
    ts_ms: int | None = None,
    # 馃啎 鎯呮劅鎰熺煡鍙傛暟
    current_emotion_tag: str | None = None,   # 褰撳墠鎯呮劅鏍囩锛岀敤浜庢儏鎰熷亸濂介噸鎺?    emotion_preference_weight: float = 0.0,   # 鎯呮劅鍋忓ソ鍔犳潈锛?.0~0.2锛?) -> RerankerResult:
```

**鎯呮劅鍋忓ソ閲嶆帓閫昏緫锛?*

```python
def _apply_emotion_preference(
    topk: list[dict[str, Any]],
    *,
    current_emotion_tag: str,
    emotion_preference_weight: float,
) -> list[dict[str, Any]]:
    """瀵?topk 缁撴灉搴旂敤鎯呮劅鍋忓ソ閲嶆帓銆?
    鍚屾儏鎰熸爣绛剧殑璁板繂寰楀埌棰濆鍔犲垎锛屼娇 AI 鍦ㄧ壒瀹氭儏鎰熺姸鎬佷笅
    鏇村€惧悜浜庡彫鍥炲悓鎯呮劅鐨勮蹇嗭紙鎯呮劅鍏遍福鏁堝簲锛夈€?    """
    result = []
    for item in topk:
        metadata = item.get("metadata") or {}
        item_emotion = str(metadata.get("emotion_tag") or "NEUTRAL").upper()
        # 鎯呮劅鍖归厤鍔犲垎
        emotion_match = (item_emotion == current_emotion_tag.upper())
        boost = emotion_preference_weight if emotion_match else 0.0
        new_score = round(min(1.0, float(item["score"]) + boost), 6)
        new_item = dict(item)
        new_item["score"] = new_score
        new_item["metadata"] = dict(metadata)
        new_item["metadata"]["emotion_preference_boosted"] = emotion_match
        result.append(new_item)
    # 閲嶆柊鎺掑簭
    return sorted(result, key=lambda r: (-float(r["score"]), str(r["doc_id"])))
```

**棰勬湡鏁堟灉锛?* 閲嶆帓搴忎粠"鎯呮劅鐩?鍙樹负"鎯呮劅鎰熺煡"锛屽綋 AI 澶勪簬 `HAPPY` 鐘舵€佹椂锛岄噸鎺掑簭浼氫紭鍏堣繑鍥?`emotion_tag=HAPPY` 鐨勮蹇嗭紝瀹炵幇鎯呮劅鍏遍福鏁堝簲銆?
---

### 馃數 娣卞寲浼樺寲41锛圥1锛夛細`RetrievalPipelineContract.run()` 涓嶄紶閫掓儏鎰熶笂涓嬫枃鈥斺€斿彫鍥炵绾挎儏鎰熸柇璺?
**鐩爣鏂囦欢锛?* `host_brain/retrieval/pipeline.py`

**绮捐鍙戠幇锛?*

`run()` 鐨勫弬鏁帮細

```python
def run(
    self,
    *,
    request_id: str,
    query: str,
    intent_id: str | None = None,
    top_k: int | None = None,
    ts_ms: int | None = None,
    # 鈫?瀹屽叏娌℃湁鎯呮劅缁村害鍙傛暟锛?) -> RetrievalPipelineResult:
```

`recall_service.recall()` 璋冪敤鏃讹細

```python
recall_result = self.recall_service.recall(
    request_id=request_id,
    query=normalized_query,
    query_embedding=embedding_vector,
    intent_id=intent_id,
    ts_ms=now_value,
    # 鈫?娌℃湁浼犻€掓儏鎰熷弬鏁帮紒鍗充娇 recall_service 鏀寔鎯呮劅杩囨护锛堜紭鍖?1锛夛紝涔熸棤娉曚娇鐢?)
```

`reranker_service.rerank()` 璋冪敤鏃讹細

```python
rerank_result = self.reranker_service.rerank(
    request_id=request_id,
    query=normalized_query,
    candidates=recall_candidates,
    intent_id=intent_id,
    top_k=top_k,
    ts_ms=now_value,
    # 鈫?娌℃湁浼犻€掓儏鎰熷弬鏁帮紒鍗充娇 reranker_service 鏀寔鎯呮劅鍋忓ソ锛堜紭鍖?0锛夛紝涔熸棤娉曚娇鐢?)
```

杩欐剰鍛崇潃锛?1. 鍗充娇浼樺寲31锛堝彫鍥炴儏鎰熻矾鐢憋級鍜屼紭鍖?0锛堥噸鎺掑簭鎯呮劅鍋忓ソ锛夐兘瀹炴柦浜嗭紝鍙洖绠＄嚎涔熸棤娉曚娇鐢ㄨ繖浜涘姛鑳解€斺€斿洜涓?`pipeline.run()` 涓嶄紶閫掓儏鎰熷弬鏁?2. 鍙洖绠＄嚎鏄儏鎰熶俊鎭殑"鏈€鍚庝竴閬撳叧鍗?锛屼絾瀹冨畬鍏ㄤ笉鎰熺煡鎯呮劅鐘舵€?3. 杩欐槸涓€涓?*鏋舵瀯绾ф柇璺?*鈥斺€斾紭鍖?1鍜屼紭鍖?0鐨勬垚鏋滆绠＄嚎灞傚畬鍏ㄥ睆钄?
**鏂规锛?* 涓?`run()` 澧炲姞鎯呮劅涓婁笅鏂囧弬鏁帮紝骞朵紶閫掔粰涓嬫父鏈嶅姟锛?
```python
def run(
    self,
    *,
    request_id: str,
    query: str,
    intent_id: str | None = None,
    top_k: int | None = None,
    ts_ms: int | None = None,
    # 馃啎 鎯呮劅涓婁笅鏂囧弬鏁帮紙v1.6 鏂板锛?    emotion_context: dict[str, Any] | None = None,
    # emotion_context 缁撴瀯锛?    # {
    #   "emotion_tag": "HAPPY",          # 褰撳墠鎯呮劅鏍囩
    #   "mood_state": "engaged",          # 褰撳墠 mood_state
    #   "bond_stage": "FRIEND",           # 褰撳墠 Bond 闃舵
    #   "emotion_filter": "HAPPY",        # 鍙洖鏃剁殑鎯呮劅杩囨护锛堝彲閫夛級
    #   "emotion_boost": 0.1,             # 鎯呮劅鍖归厤鍔犲垎锛堝彲閫夛級
    #   "emotion_preference_weight": 0.1, # 閲嶆帓搴忔儏鎰熷亸濂芥潈閲嶏紙鍙€夛級
    # }
) -> RetrievalPipelineResult:
    # ...
    ec = emotion_context or {}

    recall_result = self.recall_service.recall(
        request_id=request_id,
        query=normalized_query,
        query_embedding=embedding_vector,
        intent_id=intent_id,
        ts_ms=now_value,
        # 馃啎 浼犻€掓儏鎰熷弬鏁?        emotion_filter=ec.get("emotion_filter"),
        emotion_boost=float(ec.get("emotion_boost") or 0.0),
    )

    rerank_result = self.reranker_service.rerank(
        request_id=request_id,
        query=normalized_query,
        candidates=recall_candidates,
        intent_id=intent_id,
        top_k=top_k,
        ts_ms=now_value,
        # 馃啎 浼犻€掓儏鎰熷弬鏁?        current_emotion_tag=ec.get("emotion_tag"),
        emotion_preference_weight=float(ec.get("emotion_preference_weight") or 0.0),
    )
```

**棰勬湡鏁堟灉锛?* 鍙洖绠＄嚎浠?鎯呮劅鏂矾"鍙樹负"鎯呮劅璐€?锛屼紭鍖?1鍜屼紭鍖?0鐨勬垚鏋滃彲浠ラ€氳繃绠＄嚎灞傝瀹為檯浣跨敤锛屾儏鎰熶俊鎭湪鍙洖鈫掗噸鎺掑簭鈫掕瘉鎹寘鐨勫叏閾捐矾涓緱浠ヤ紶閫掋€?
---

### 馃煟 娣卞寲浼樺寲42锛圥2锛夛細`EdgeEventReporter` 鐨?`EventReportItem` 涓嶅惈鎯呮劅瀛楁鈥斺€旇竟缂樼浜嬩欢涓婃姤鎯呮劅鐩?
**鐩爣鏂囦欢锛?* `edge_client/rpc/event_reporter.py`

**绮捐鍙戠幇锛?*

`EventReportItem` 鐨勫瓧娈靛畾涔夛細

```python
@dataclass(frozen=True)
class EventReportItem:
    event_type: str
    ts_ms: int
    request_id: str
    session_id: str
    source: str = "edge"
    payload: dict[str, Any] = field(default_factory=dict)
    direction: str = "EDGE_TO_HOST"
    seq: int | None = None
    status: str | None = None
    reason_code: str | None = None
    stage: str | None = None
    detail: str | None = None
    evidence_ref: str | None = None
    # 鈫?娌℃湁鎯呮劅瀛楁锛?```

`validate_event()` 鐨勫繀闇€瀛楁妫€鏌ワ細

```python
REQUIRED_EVENT_FIELDS = frozenset({"event_type", "ts_ms", "request_id", "session_id"})
# 鈫?娌℃湁鎯呮劅瀛楁锛?```

杩欐剰鍛崇潃锛?1. 杈圭紭绔紙Edge Client锛変笂鎶ュ埌 Host 鐨勪簨浠朵笉鍚儏鎰熷瓧娈碘€斺€擜SR 璇嗗埆鍒扮敤鎴峰摥娉ｇ殑璇皵锛岃繖涓儏鎰熶俊鎭棤娉曢€氳繃浜嬩欢涓婃姤浼犻€掔粰 Host
2. TTS 鍚堟垚鏃剁殑鎯呮劅鏍囩锛坄emotion_tag`锛変笉鍦ㄤ簨浠朵笂鎶ヤ腑鈥斺€擧ost 鏃犳硶鐭ラ亾杈圭紭绔疄闄呮挱鏀句簡浠€涔堟儏鎰熺殑璇煶
3. VTS 椹卞姩鏃剁殑琛ㄦ儏 ID锛坄expression_id`锛変笉鍦ㄤ簨浠朵笂鎶ヤ腑鈥斺€擧ost 鏃犳硶鐭ラ亾杈圭紭绔疄闄呮樉绀轰簡浠€涔堣〃鎯?
**鏂规锛?* 涓?`EventReportItem` 澧炲姞鎯呮劅瀛楁锛屽苟鏇存柊 `validate_event()` 鏀寔鎯呮劅瀛楁瑙ｆ瀽锛?
```python
@dataclass(frozen=True)
class EventReportItem:
    event_type: str
    ts_ms: int
    request_id: str
    session_id: str
    source: str = "edge"
    payload: dict[str, Any] = field(default_factory=dict)
    direction: str = "EDGE_TO_HOST"
    seq: int | None = None
    status: str | None = None
    reason_code: str | None = None
    stage: str | None = None
    detail: str | None = None
    evidence_ref: str | None = None
    # 馃啎 鎯呮劅瀛楁锛坴1.6 鏂板锛?    emotion_tag: str | None = None          # 浜嬩欢鍙戠敓鏃剁殑鎯呮劅鏍囩
    expression_id: str | None = None        # VTS 琛ㄦ儏 ID锛堝 "exp_happy_01"锛?    emotional_intensity: float | None = None  # 鎯呮劅寮哄害锛?.0~1.0锛?    user_emotion_hint: str | None = None    # 鐢ㄦ埛鎯呮劅鎺ㄦ柇锛堟潵鑷?ASR 璇皵鍒嗘瀽锛?```

**`to_dict()` 澧炲姞鎯呮劅瀛楁锛?*

```python
def to_dict(self) -> dict[str, Any]:
    return {
        # ... 鍘熸湁瀛楁 ...
        # 馃啎 鎯呮劅瀛楁
        "emotion_tag": self.emotion_tag,
        "expression_id": self.expression_id,
        "emotional_intensity": self.emotional_intensity,
        "user_emotion_hint": self.user_emotion_hint,
    }
```

**鍚屾椂鍙戠幇锛歚vts_expression_map.py` 鐨?`EmotionTag` 鏋氫妇涓?`host_brain/channels/vts.py` 鐨?`EMOTION_TO_EXPRESSION_ID` 涓嶅悓姝ワ細**

```python
# edge_client/actuators/vts_expression_map.py 鐨?EmotionTag锛?绉嶏級
class EmotionTag(str, Enum):
    NEUTRAL   = "neutral"
    HAPPY     = "happy"
    SAD       = "sad"
    ANGRY     = "angry"
    SURPRISED = "surprised"
    FEARFUL   = "fearful"    # 鈫?edge 鏈夛紝host 娌℃湁
    DISGUSTED = "disgusted"  # 鈫?edge 鏈夛紝host 娌℃湁
    EXCITED   = "excited"
    CALM      = "calm"

# host_brain/channels/vts.py 鐨?EMOTION_TO_EXPRESSION_ID锛?绉嶏紝浼樺寲34鍓嶏級
EMOTION_TO_EXPRESSION_ID = {
    "NEUTRAL", "HAPPY", "SAD", "ANGRY", "SURPRISED", "THINKING"
    # 鈫?THINKING 鍦?edge 娌℃湁锛孎EARFUL/DISGUSTED 鍦?host 娌℃湁
}
```

杩欐槸涓€涓?*璺ㄧ鏋氫妇涓嶅悓姝?*闂鈥斺€擡dge 鍜?Host 鐨勬儏鎰熸爣绛炬灇涓句笉涓€鑷达紝浼氬鑷磋法绔儏鎰熶俊鎭紶閫掓椂鍑虹幇鏄犲皠閿欒銆?
**鏂规锛?* 寤虹珛缁熶竴鐨勬儏鎰熸爣绛炬灇涓捐鑼冿紝Edge 鍜?Host 鍏变韩鍚屼竴濂楁灇涓惧畾涔夛細

```python
# 馃啎 缁熶竴鎯呮劅鏍囩鏋氫妇锛坴1.6 鏂板锛孍dge 鍜?Host 鍏变韩锛?UNIFIED_EMOTION_TAGS = frozenset({
    "NEUTRAL", "HAPPY", "SAD", "ANGRY", "SURPRISED",
    "FEARFUL", "DISGUSTED", "EXCITED", "CALM",
    "THINKING", "ANXIOUS", "CONFUSED", "EMBARRASSED",
    "GRATEFUL", "LONELY", "PROUD", "SCARED",
})
```

**棰勬湡鏁堟灉锛?* 杈圭紭绔簨浠朵笂鎶ヤ粠"鎯呮劅鐩?鍙樹负"鎯呮劅鎰熺煡"锛孉SR 璇嗗埆鍒扮殑鐢ㄦ埛鎯呮劅銆乀TS 鎾斁鐨勬儏鎰熸爣绛俱€乂TS 鏄剧ず鐨勮〃鎯?ID 閮藉彲浠ラ€氳繃浜嬩欢涓婃姤浼犻€掔粰 Host锛屽疄鐜拌竟缂樼鍜?Host 鐨勬儏鎰熶俊鎭弻鍚戝悓姝ャ€傚悓鏃朵慨澶嶈法绔灇涓句笉鍚屾闂锛岀‘淇濇儏鎰熶俊鎭湪 Edge鈫扝ost 浼犻€掓椂涓嶅嚭鐜版槧灏勯敊璇€?
---

## 浜屽崄鍥涖€佺涓冭疆浼樺寲鐨勬牳蹇冧环鍊兼€荤粨

绗竷杞紭鍖栬仛鐒︿簬**鎵ц灞傛儏鎰熺湡绌?*鈥斺€旀儏鎰熶俊鎭湪"鎵ц鈫掑弽棣堚啋杈圭紭绔?閾捐矾涓殑绯荤粺鎬х湡绌猴細

| 浼樺寲椤?| 鍙戠幇鐨?鎵ц灞傜湡绌? | 褰卞搷鑼冨洿 | 淇鏂规 |
|--------|-----------------|---------|---------|
| **浼樺寲37** 馃敶 | 鎶€鑳芥墽琛?context 涓嶅惈鎯呮劅瀛楁锛屾妧鑳?handler 鎯呮劅鐩?| `skills/loader.py` 鈫?鎶€鑳芥墽琛?| 涓?context 澧炲姞鎯呮劅瀛楁绾﹀畾锛屽璁℃棩蹇楄褰曟儏鎰熺姸鎬?|
| **浼樺寲38** 馃煚 | 宸ュ叿鎵ц context 涓嶅惈鎯呮劅瀛楁锛屽伐鍏?handler 鎯呮劅鐩?| `tools/broker.py` 鈫?宸ュ叿鎵ц | 涓?context 澧炲姞鎯呮劅瀛楁绾﹀畾锛屽璁℃棩蹇楄褰曟儏鎰熺姸鎬?|
| **浼樺寲39** 馃煛 | MCP 宸ュ叿娉ㄥ唽鏃犳儏鎰熼€傜敤鎬у瓧娈碉紝宸ュ叿閫夋嫨涓嶆劅鐭ユ儏鎰熷満鏅?| `mcp/tool_registry.py` 鈫?宸ュ叿娉ㄥ唽 | 涓?ToolMeta 澧炲姞 emotion_requirement/min_bond_stage/energy_cost 瀛楁 |
| **浼樺寲40** 馃煝 | 閲嶆帓搴忎笉鎰熺煡鎯呮劅鐘舵€侊紝鎯呮劅鍏遍福鏁堝簲鏃犳硶瀹炵幇 | `retrieval/reranker.py` 鈫?閲嶆帓搴?| 澧炲姞鎯呮劅鍋忓ソ鍙傛暟鍜屾儏鎰熷叡楦ｉ噸鎺掗€昏緫 |
| **浼樺寲41** 馃數 | 鍙洖绠＄嚎涓嶄紶閫掓儏鎰熶笂涓嬫枃锛屼紭鍖?1/40鐨勬垚鏋滆灞忚斀 | `retrieval/pipeline.py` 鈫?鍙洖绠＄嚎 | 澧炲姞 emotion_context 鍙傛暟锛屼紶閫掔粰 recall 鍜?rerank 鏈嶅姟 |
| **浼樺寲42** 馃煟 | 杈圭紭绔簨浠朵笂鎶ヤ笉鍚儏鎰熷瓧娈碉紝璺ㄧ鎯呮劅淇℃伅浼犻€掓柇璺?| `event_reporter.py` 鈫?杈圭紭绔?| 涓?EventReportItem 澧炲姞鎯呮劅瀛楁锛屼慨澶嶈法绔灇涓句笉鍚屾 |

---

### 绗竷杞殑鐗规畩鍙戠幇锛?*浼樺寲41 鏄竴涓?鎴愭灉灞忚斀鍣?**

浼樺寲41锛堝彫鍥炵绾挎儏鎰熸柇璺級鏄竴涓壒娈婄殑闂鈥斺€斿畠涓嶆槸涓€涓嫭绔嬬殑缂洪櫡锛岃€屾槸涓€涓?*灞忚斀鍣?*锛氬嵆浣夸紭鍖?1锛堝彫鍥炴儏鎰熻矾鐢憋級鍜屼紭鍖?0锛堥噸鎺掑簭鎯呮劅鍋忓ソ锛夐兘宸插疄鏂斤紝鍙 `pipeline.run()` 涓嶄紶閫掓儏鎰熷弬鏁帮紝杩欎袱涓紭鍖栫殑鎴愭灉灏辨案杩滄棤娉曡瀹為檯浣跨敤銆傝繖鏄竴涓吀鍨嬬殑"鏈€鍚庝竴鍏噷"闂銆?
### 绗竷杞殑鍙︿竴涓壒娈婂彂鐜帮細**璺ㄧ鏋氫妇涓嶅悓姝?*

鍦ㄤ紭鍖?2涓彂鐜颁簡涓€涓法绔灇涓句笉鍚屾闂锛歚edge_client/actuators/vts_expression_map.py` 鐨?`EmotionTag` 鏋氫妇锛?绉嶏級鍜?`host_brain/channels/vts.py` 鐨?`EMOTION_TO_EXPRESSION_ID`锛?绉嶏級涓嶄竴鑷达紝涓斾袱鑰呴兘涓?`metabolism.py` 鐨勬儏鎰熸爣绛鹃泦鍚堜笉鍚屾銆傝繖鏄竴涓渶瑕佸湪鏁翠釜椤圭洰灞傞潰缁熶竴瑙ｅ喅鐨勯棶棰樸€?
---

## 浜屽崄浜斻€佸畬鏁翠紭鍖栧叏鏅浘锛坴1.6锛?2椤癸級

### 涓冭疆浼樺寲鐨勫眰娆￠€掕繘

```
绗竴杞紙v1.0锛夛細鍙戠幇5涓?缂哄け鍔熻兘"     鈫?闇€瑕佹柊澧炰粈涔?绗簩杞紙v1.1锛夛細鍙戠幇6涓?娣卞眰闂"     鈫?鐜版湁鏈哄埗鍝噷涓嶅娣?绗笁杞紙v1.2锛夛細鍙戠幇6涓?鍋囧姛鑳界┖澹?   鈫?鐜版湁浠ｇ爜鍝噷鐪嬭捣鏉ユ湁浣嗗疄闄呮病鐢?绗洓杞紙v1.3锛夛細鍙戠幇6涓?鏋舵瀯鏂眰"     鈫?鍔熻兘涔嬮棿鐨勮繛鎺ュ湪鍝噷鏂
绗簲杞紙v1.4锛夛細鍙戠幇6涓?璺ㄦā鍧楃洸鍖?   鈫?鎯呮劅淇℃伅鍦ㄥ叏閾捐矾涓殑绯荤粺鎬х己澶?绗叚杞紙v1.5锛夛細鍙戠幇6涓?鎰熺煡灞傛柇璺?   鈫?鎯呮劅淇℃伅鍦ㄦ劅鐭モ啋鍐崇瓥鈫掕緭鍑衡啋瑙傛祴涓殑鏂矾
绗竷杞紙v1.6锛夛細鍙戠幇6涓?鎵ц灞傜湡绌?   鈫?鎯呮劅淇℃伅鍦ㄦ墽琛屸啋鍙嶉鈫掕竟缂樼閾捐矾涓殑鐪熺┖
```

### 鎸変紭鍏堢骇鍒嗙被锛?2椤瑰叏鏅級

| 浼樺厛绾?| 浼樺寲椤?| 鏍稿績闂 |
|--------|--------|---------|
| **P0锛堝繀椤讳慨澶嶏級** | OPT19, OPT20, OPT22, OPT25, OPT26, OPT27, OPT31, OPT32, OPT37, OPT38 | 鏋舵瀯鏂眰 + 璺ㄦā鍧楃洸鍖?+ 鎰熺煡灞傛柇璺?+ 鎵ц灞傜湡绌轰腑鐨勫叧閿矾寰?|
| **P1锛堥噸瑕佷紭鍖栵級** | OPT1, OPT6, OPT7, OPT8, OPT13, OPT14, OPT16, OPT17, OPT18, OPT21, OPT28, OPT29, OPT33, OPT34, OPT35, OPT39, OPT40, OPT41 | 鎯呮劅鍐呮牳 + 鍏崇郴灞?+ 璁板繂灞?+ 鎰熺煡灞?+ 鎵ц灞?|
| **P2锛堜綋楠屽寮猴級** | OPT2, OPT3, OPT4, OPT5, OPT9, OPT10, OPT11, OPT12, OPT15, OPT23, OPT24, OPT30, OPT36, OPT42 | 杈撳嚭灞?+ 缁嗚妭鎵撶（ + 杈圭紭绔?|

### 鎯呮劅淇℃伅鍏ㄩ摼璺鐩栧浘锛坴1.6 鏈€缁堢増锛?
```
鐢ㄦ埛杈撳叆
    鈫?[杈圭紭绔劅鐭ュ眰] edge_client/
    OPT42: EventReportItem 鎯呮劅瀛楁 + 璺ㄧ鏋氫妇缁熶竴锛堟柊澧烇級
    鈫?[瑙嗚鎰熺煡灞俔 camera_event.py
    OPT35: 瑙嗚璇嗗埆 鈫?鎯呮劅瑙﹀彂锛堢鍏疆锛?    鈫?[鍙洖绠＄嚎灞俔 retrieval/pipeline.py
    OPT41: emotion_context 鍙傛暟璐€氾紙鏂板锛夆啇 鍏抽敭"鏈€鍚庝竴鍏噷"
    鈫?[鍙洖灞俔 recall_fusion.py
    OPT31: 鎯呮劅缁村害璺敱锛堢鍏疆锛?    OPT36: 鎯呮劅寮哄害鍔犳潈铻嶅悎锛堢鍏疆锛?    鈫?[閲嶆帓搴忓眰] retrieval/reranker.py
    OPT40: 鎯呮劅鍋忓ソ閲嶆帓锛堟柊澧烇級
    鈫?[鍐崇瓥灞俔 governor/decision.py
    OPT32: R8 鎯呮劅淇濇姢闂ㄦ帶锛堢鍏疆锛?    鈫?[鎯呮劅鍐呮牳] metabolism.py
    OPT6/7/8: EmotionVector 娣卞寲锛堢浜岃疆锛?    鈫?[鍏崇郴灞俔 bond/
    OPT26: 鎯呮劅鍔犳潈 Bond 绉垎锛堢浜旇疆锛?    OPT27: Bond Summary 淇锛堢浜旇疆锛?    鈫?[璁板繂灞俔 memory/
    OPT25/28/29: 鎯呮劅璁板繂鏍囨敞/淇濆瓨/钂搁锛堢浜旇疆锛?    鈫?[杈撳嚭灞俔 qa.py / prompt_context.py
    OPT19/20/22: 鏋舵瀯鏂眰淇锛堢鍥涜疆锛?    鈫?[宸ュ叿娉ㄥ唽灞俔 mcp/tool_registry.py
    OPT39: ToolMeta 鎯呮劅閫傜敤鎬у瓧娈碉紙鏂板锛?    鈫?[鎵ц灞俔 skills/loader.py + tools/broker.py
    OPT37: 鎶€鑳芥墽琛?context 鎯呮劅瀛楁锛堟柊澧烇級
    OPT38: 宸ュ叿鎵ц context 鎯呮劅瀛楁锛堟柊澧烇級
    鈫?[閫氶亾灞俔 channels/vts.py
    OPT34: VTS 琛ㄦ儏鏄犲皠鎵╁睍锛堢鍏疆锛?    鈫?[瑙傛祴灞俔 observability/cross_timeline.py
    OPT33: 鎯呮劅浜嬩欢绾冲叆鏃堕棿绾匡紙绗叚杞級
    鈫?鐢ㄦ埛鎰熺煡鍒扮殑鎯呮劅杈撳嚭
```

---

## 浜屽崄銆佺鍏疆娣卞寲浼樺寲锛氭劅鐭ュ眰鎯呮劅鏂矾

> **v1.5 鏂板**锛?026-03-05锛夈€傛湰杞熀浜庡 `retrieval/recall_fusion.py`銆乣governor/decision.py`銆乣observability/cross_timeline.py`銆乣channels/vts.py`銆乣vision/camera_event.py` 鐨勫畬鏁撮€愯绮捐锛屽彂鐜颁簡鍓嶄簲杞畬鍏ㄦ湭瑙﹀強鐨?**6 涓劅鐭ュ眰鎯呮劅鏂矾**鈥斺€旇繖浜涢棶棰樺垎甯冨湪鍙洖灞傘€佸喅绛栧眰銆佽娴嬪眰銆侀€氶亾灞傘€佽瑙夊眰锛屾槸鎯呮劅淇℃伅鍦?鎰熺煡鈫掑喅绛栤啋杈撳嚭鈫掕娴?鍏ㄩ摼璺腑鐨勭郴缁熸€ф柇璺€?
---

### 馃敶 娣卞寲浼樺寲31锛圥0锛夛細`RecallFusionServiceContract.recall()` 鏃犳儏鎰熺淮搴﹁矾鐢扁€斺€旀儏鎰熻蹇嗗拰鏅€氳蹇嗘贩鍦ㄥ悓涓€涓彫鍥炴睜

**鐩爣鏂囦欢锛?* `host_brain/retrieval/recall_fusion.py`

**绮捐鍙戠幇锛?*

`recall()` 鐨勫弬鏁帮細

```python
def recall(
    self,
    *,
    request_id: str,
    query: str,
    query_embedding: list[float] | tuple[float, ...] | Any,
    intent_id: str | None = None,
    ts_ms: int | None = None,
    # 鈫?瀹屽叏娌℃湁鎯呮劅缁村害鍙傛暟锛?) -> RecallFusionResult:
```

`RecallRouteConfig` 鐨勫瓧娈碉細

```python
@dataclass(frozen=True)
class RecallRouteConfig:
    route_id: str
    collection: str      # 鈫?鍙湁 collection 鍖哄垎锛屾病鏈夋儏鎰熻繃婊?    top_k: int
    score_weight: float
    node_selector: str
```

`_normalize_candidate()` 鐨?metadata 澶勭悊锛?
```python
merged_metadata["collection"] = route.collection
merged_metadata["route_id"] = route.route_id
merged_metadata["route_score_weight"] = float(route.score_weight)
merged_metadata["base_score"] = base_score
merged_metadata["matched_routes"] = [route.route_id]
merged_metadata["recall_rank"] = int(rank)
# 鈫?娌℃湁 emotion_tag 杩囨护锛佸€欓€夌粨鏋滀笉鍚儏鎰熺淮搴?```

杩欐剰鍛崇潃锛?1. 鍙洖鏃舵棤娉曞尯鍒?鎯呮劅璁板繂"鍜?鏅€氳蹇?鈥斺€擿EXCITED` 鐘舵€佷笅浜х敓鐨勮蹇嗗拰 `NEUTRAL` 鐘舵€佷笅鐨勮蹇嗗緱鍒扮浉鍚岀殑鍙洖鏉冮噸
2. 褰撶敤鎴疯"鎴戜滑寮€蹇冩椂鑱婅繃浠€涔?锛屽彫鍥炵郴缁熸棤娉曚紭鍏堣繑鍥?`emotion_tag=HAPPY` 鐨勮蹇?3. 浼樺寲25锛坄EpisodicRecord` 澧炲姞鎯呮劅瀛楁锛夌殑鎴愭灉鏃犳硶琚彫鍥炲眰鍒╃敤鈥斺€旀儏鎰熷瓧娈靛啓鍏ヤ簡 Chroma metadata锛屼絾鍙洖鏃舵病鏈夋儏鎰熻繃婊?
**鏂规锛?* 涓?`recall()` 澧炲姞鎯呮劅杩囨护鍙傛暟锛屽苟鍦ㄥ€欓€夌粨鏋滀腑淇濈暀鎯呮劅缁村害锛?
```python
def recall(
    self,
    *,
    request_id: str,
    query: str,
    query_embedding: list[float] | tuple[float, ...] | Any,
    intent_id: str | None = None,
    ts_ms: int | None = None,
    # 馃啎 鎯呮劅缁村害鍙傛暟
    emotion_filter: str | None = None,       # 鎸夋儏鎰熸爣绛捐繃婊わ紙濡?"HAPPY"锛?    bond_stage_filter: str | None = None,    # 鎸?Bond 闃舵杩囨护锛堝 "FRIEND"锛?    emotion_boost: float = 0.0,              # 鎯呮劅鍖归厤鐨勯澶栧姞鍒嗭紙0.0~0.3锛?) -> RecallFusionResult:
    # ...
    # 鍦ㄥ€欓€夎瀺鍚堝悗锛屽簲鐢ㄦ儏鎰熻繃婊ゅ拰鍔犳潈
    if emotion_filter or bond_stage_filter or emotion_boost > 0:
        candidates = _apply_emotion_rerank(
            candidates=candidates,
            emotion_filter=emotion_filter,
            bond_stage_filter=bond_stage_filter,
            emotion_boost=emotion_boost,
        )
```

**鎯呮劅閲嶆帓搴忓疄鐜帮細**

```python
def _apply_emotion_rerank(
    *,
    candidates: list[dict[str, Any]],
    emotion_filter: str | None,
    bond_stage_filter: str | None,
    emotion_boost: float,
) -> list[dict[str, Any]]:
    """瀵瑰彫鍥炲€欓€夎繘琛屾儏鎰熺淮搴﹂噸鎺掑簭銆?""
    result = []
    for cand in candidates:
        metadata = cand.get("metadata") or {}
        cand_emotion = str(metadata.get("emotion_tag") or "NEUTRAL").upper()
        cand_bond = str(metadata.get("bond_stage") or "STRANGER").upper()

        # 鎯呮劅杩囨护锛氫笉鍖归厤鐨勫€欓€夐檷鏉冿紙涓嶇洿鎺ヨ繃婊わ紝閬垮厤鍙洖涓虹┖锛?        emotion_match = (emotion_filter is None or cand_emotion == emotion_filter.upper())
        bond_match = (bond_stage_filter is None or cand_bond == bond_stage_filter.upper())

        # 鎯呮劅鍔犳潈锛氬尮閰嶇殑鍊欓€夊姞鍒?        boost = 0.0
        if emotion_match and emotion_filter is not None:
            boost += emotion_boost
        if bond_match and bond_stage_filter is not None:
            boost += emotion_boost * 0.5

        new_score = round(min(1.0, float(cand["score"]) + boost), 6)
        new_cand = dict(cand)
        new_cand["score"] = new_score
        new_cand["metadata"] = dict(metadata)
        new_cand["metadata"]["emotion_boosted"] = boost > 0
        new_cand["metadata"]["emotion_boost_value"] = boost
        result.append(new_cand)

    # 閲嶆柊鎸夊垎鏁版帓搴?    return sorted(result, key=lambda r: (-float(r["score"]), str(r["doc_id"])))
```

**棰勬湡鏁堟灉锛?* 鍙洖绯荤粺浠?鎯呮劅鐩?鍙樹负"鎯呮劅鎰熺煡"锛屽綋鐢ㄦ埛璇?鎴戜滑寮€蹇冩椂鑱婅繃浠€涔?锛岀郴缁熻兘浼樺厛鍙洖 `emotion_tag=HAPPY` 鐨勮蹇嗭紝鎯呮劅璁板繂鐨勫埄鐢ㄧ巼澶у箙鎻愬崌銆?
---

### 馃煚 娣卞寲浼樺寲32锛圥0锛夛細`IntentDecisionEngine.evaluate_intent()` 鐨?`governance_snapshot` 涓嶅惈鎯呮劅鐘舵€佲€斺€斿喅绛栧眰鎯呮劅鐩?
**鐩爣鏂囦欢锛?* `host_brain/governor/decision.py`

**绮捐鍙戠幇锛?*

`evaluate_intent()` 鐨?`governance_snapshot` 鍙傛暟锛?
```python
def evaluate_intent(
    self,
    raw_intent: str | dict[str, Any],
    *,
    request_id: str | None = None,
    thread_id: str | None = None,
    turn_id: str | None = None,
    current_ms: int | None = None,
    proposer_node_id: str = "core_reasoner_primary",
    model_snapshot: str = "unknown_model",
    prompt_profile_id: str = "default_profile",
    tool_visibility_hash: str = "",
    governance_snapshot: dict[str, Any] | None = None,
    # 鈫?governance_snapshot 鏄嚜鐢辨牸寮?dict锛屾病鏈夋儏鎰熷瓧娈电殑绾﹀畾
) -> dict[str, Any]:
```

`_build_gate_input()` 鐨勫疄鐜帮細

```python
def _build_gate_input(
    governance_snapshot: dict[str, Any] | None,
    *,
    target_scope: str,
) -> dict[str, Any]:
    source = dict(governance_snapshot or {})
    state = _hard_state_from_snapshot(governance_snapshot)
    gates_input = build_governance_snapshot(state, overrides=source)
    # 鈫?build_governance_snapshot 鍙鐞?energy/safety/trust 绛夊瓧娈?    # 娌℃湁 mood_state銆乪motion_tag 绛夋儏鎰熷瓧娈?    if "scope" not in source:
        gates_input["scope"] = str(target_scope).strip().lower() or "host"
    return gates_input
```

`evaluate_governor_gates()` 鐨?R1-R7 闂ㄦ帶瑙勫垯锛堟潵鑷?`governor/rules.py`锛夛細

```python
# R1: energy_bucket 妫€鏌?# R2: safety_flag 妫€鏌?# R3: rate_limit 妫€鏌?# R4: trust 妫€鏌?# R5: scope 妫€鏌?# R6: allowlist 妫€鏌?# R7: airlock_blocked 妫€鏌?# 鈫?娌℃湁浠讳綍鎯呮劅鐩稿叧鐨勯棬鎺ц鍒欙紒
```

杩欐剰鍛崇潃锛?1. 鍐崇瓥灞傚畬鍏ㄤ笉鎰熺煡鎯呮劅鐘舵€佲€斺€擿critical` 鑳介噺鐘舵€佷笅鐨勬剰鍥惧拰 `engaged` 鐘舵€佷笅鐨勬剰鍥剧粡杩囩浉鍚岀殑闂ㄦ帶瑙勫垯
2. 娌℃湁"鎯呮劅淇濇姢闂ㄦ帶"鈥斺€斿綋 AI 澶勪簬 `critical` 鐘舵€佹椂锛屽簲璇ユ嫆缁濋珮娑堣€楃殑鎶€鑳借皟鐢紝浣嗗綋鍓嶆病鏈夎繖涓棬鎺?3. 娌℃湁"鎯呮劅涓€鑷存€ф鏌?鈥斺€斿綋 AI 澹扮О鑷繁寰堝紑蹇冧絾 `mood_state=sad` 鏃讹紝娌℃湁浠讳綍鏈哄埗妫€娴嬭繖涓煕鐩?
**鏂规锛?* 涓?`governance_snapshot` 澧炲姞鎯呮劅瀛楁绾﹀畾锛屽苟鍦?`evaluate_governor_gates()` 涓鍔犳儏鎰熶繚鎶ら棬鎺?R8锛?
```python
# 馃啎 鎯呮劅淇濇姢闂ㄦ帶 R8锛歝ritical 鐘舵€佷笅鎷掔粷楂樻秷鑰楁妧鑳?EMOTION_GATE_R8 = "R8_EMOTION_PROTECTION"

# 楂樻秷鑰楁妧鑳藉垪琛紙critical 鐘舵€佷笅搴旀嫆缁濓級
HIGH_ENERGY_SKILLS = frozenset({
    "deep_analysis",
    "long_story_generation",
    "multi_step_reasoning",
    "image_generation",
    "code_execution",
})

def _evaluate_emotion_gate(gates_input: dict[str, Any]) -> dict[str, Any]:
    """R8锛氭儏鎰熶繚鎶ら棬鎺р€斺€攃ritical 鐘舵€佷笅鎷掔粷楂樻秷鑰楁妧鑳姐€?""
    mood_state = str(gates_input.get("mood_state") or "steady").lower()
    target_scope = str(gates_input.get("scope") or "host").lower()

    if mood_state == "critical":
        # critical 鐘舵€佷笅锛岄珮娑堣€楁妧鑳借鎷掔粷
        if any(skill in target_scope for skill in HIGH_ENERGY_SKILLS):
            return {
                "gate_id": EMOTION_GATE_R8,
                "hit": True,
                "reason_hint": "EMOTION_CRITICAL_SKILL_BLOCKED",
                "threshold_summary": {"mood_state": "critical", "blocked_scope": target_scope},
                "snapshot_summary": {"mood_state": mood_state},
            }

    return {
        "gate_id": EMOTION_GATE_R8,
        "hit": False,
        "reason_hint": None,
        "threshold_summary": {"mood_state": mood_state},
        "snapshot_summary": {"mood_state": mood_state},
    }
```

**`governance_snapshot` 鎯呮劅瀛楁绾﹀畾锛?*

```python
# 馃啎 governance_snapshot 鏍囧噯鎯呮劅瀛楁锛坴1.5 鏂板锛?GOVERNANCE_SNAPSHOT_EMOTION_FIELDS = {
    "mood_state": str,      # AI 褰撳墠鎯呮劅鐘舵€侊紙"engaged"/"steady"/"calm"/"tired"/"critical"锛?    "emotion_tag": str,     # AI 褰撳墠鎯呮劅鏍囩锛?NEUTRAL"/"HAPPY"/"SAD" 绛夛級
    "bond_stage": str,      # 褰撳墠 Bond 闃舵锛?STRANGER"/"FRIEND" 绛夛級
    "emotional_intensity": float,  # 鎯呮劅寮哄害锛?.0~1.0锛?}
```

**棰勬湡鏁堟灉锛?* 鍐崇瓥灞備粠"鎯呮劅鐩?鍙樹负"鎯呮劅鎰熺煡"锛宍critical` 鐘舵€佷笅鐨勯珮娑堣€楁妧鑳借皟鐢ㄤ細琚?R8 闂ㄦ帶鎷掔粷锛屼繚鎶?AI 鐨勬儏鎰熺姸鎬佷笉琚繃搴︽秷鑰椼€?
---

### 馃煛 娣卞寲浼樺寲33锛圥1锛夛細`CrossTimelineAggregator` 鐨勬椂闂寸嚎涓嶅惈鎯呮劅浜嬩欢鈥斺€旀儏鎰熷彉鍖栧湪鏃堕棿绾夸腑涓嶅彲瑙?
**鐩爣鏂囦欢锛?* `host_brain/observability/cross_timeline.py`

**绮捐鍙戠幇锛?*

`VALID_EDGE_EVENT_TYPES` 鐨勫畾涔夛細

```python
VALID_EDGE_EVENT_TYPES = frozenset({
    EDGE_EVENT_ASR_CHUNK,       # "ASR_CHUNK"
    EDGE_EVENT_SPEAK,           # "SPEAK"
    EDGE_EVENT_BARGE_IN,        # "BARGE_IN"
    EDGE_EVENT_RESOURCE_GATE,   # "VOICE_RESOURCE_GATE"
    EDGE_EVENT_FALLBACK_LLM,    # "FALLBACK_LLM"
    EDGE_EVENT_TTS_SYNTH,       # "TTS_SYNTH"
    EDGE_EVENT_VTS_EXPRESSION,  # "VTS_EXPRESSION"
    EDGE_EVENT_VTS_TALK_GATE,   # "VTS_TALK_GATE"
    # 鈫?娌℃湁鎯呮劅浜嬩欢绫诲瀷锛?})
```

`HOST_LOG_TABLES` 鐨勫畾涔夛細

```python
HOST_LOG_TABLES = (
    "intent_log",
    "governor_log",
    "skill_exec_log",
    "model_control_log",
    "channel_io_log",
    "avatar_action_log",
    "voice_event_log",
    "retrieval_event_log",
    "vision_event_log",
    "service_health_log",
    "error_log",
    "config_snapshot_log",
    # 鈫?娌℃湁 emotion_state_log銆乥ond_event_log锛?)
```

杩欐剰鍛崇潃锛?1. 璺ㄧ鏃堕棿绾夸腑瀹屽叏娌℃湁鎯呮劅浜嬩欢鈥斺€旀棤娉曠湅鍒?AI 鍦ㄤ粈涔堟椂鍒绘儏鎰熷彂鐢熶簡鍙樺寲"
2. 鏃犳硶鍋?鎯呮劅鏃堕棿绾垮垎鏋?鈥斺€旀棤娉曞洖绛?杩欐瀵硅瘽涓?AI 鐨勬儏鎰熸槸濡備綍鍙樺寲鐨?
3. Bond 浜嬩欢锛坄bond_dynamics.py` 鍐欏叆 Neo4j锛変笉鍦ㄦ椂闂寸嚎涓€斺€旀棤娉曠湅鍒?Bond 闃舵鍦ㄤ粈涔堟椂鍒诲彂鐢熶簡鍙樺寲"
4. 鎯呮劅鐘舵€佸彉鍖栵紙`metabolism.py` 鐨?`HardState` 鏇存柊锛変笉鍦ㄦ椂闂寸嚎涓€斺€旀儏鎰熷彉鍖栧瑙傛祴灞傚畬鍏ㄤ笉鍙

**鏂规锛?* 澧炲姞鎯呮劅浜嬩欢绫诲瀷锛屽苟灏嗘儏鎰熺浉鍏崇殑瀹¤琛ㄧ撼鍏ユ椂闂寸嚎锛?
```python
# 馃啎 鎯呮劅浜嬩欢绫诲瀷锛坴1.5 鏂板锛?EDGE_EVENT_EMOTION_CHANGE = "EMOTION_CHANGE"
EDGE_EVENT_BOND_UPDATE = "BOND_UPDATE"
EDGE_EVENT_MOOD_TRANSITION = "MOOD_TRANSITION"

VALID_EDGE_EVENT_TYPES = frozenset({
    EDGE_EVENT_ASR_CHUNK,
    EDGE_EVENT_SPEAK,
    EDGE_EVENT_BARGE_IN,
    EDGE_EVENT_RESOURCE_GATE,
    EDGE_EVENT_FALLBACK_LLM,
    EDGE_EVENT_TTS_SYNTH,
    EDGE_EVENT_VTS_EXPRESSION,
    EDGE_EVENT_VTS_TALK_GATE,
    EDGE_EVENT_EMOTION_CHANGE,    # 馃啎 鎯呮劅鐘舵€佸彉鍖栦簨浠?    EDGE_EVENT_BOND_UPDATE,       # 馃啎 Bond 绉垎鏇存柊浜嬩欢
    EDGE_EVENT_MOOD_TRANSITION,   # 馃啎 mood_state 杞崲浜嬩欢
})

# 馃啎 鎯呮劅鐩稿叧瀹¤琛ㄧ撼鍏ユ椂闂寸嚎
HOST_LOG_TABLES = (
    "intent_log",
    "governor_log",
    "skill_exec_log",
    "model_control_log",
    "channel_io_log",
    "avatar_action_log",
    "voice_event_log",
    "retrieval_event_log",
    "vision_event_log",
    "service_health_log",
    "error_log",
    "config_snapshot_log",
    "emotion_state_log",    # 馃啎 鎯呮劅鐘舵€佸彉鍖栨棩蹇?    "bond_event_log",       # 馃啎 Bond 浜嬩欢鏃ュ織
)
```

**鍚屾椂涓?`UnifiedTimelineEntry` 澧炲姞鎯呮劅瀛楁锛?*

```python
@dataclass(frozen=True)
class UnifiedTimelineEntry:
    event_id: str
    source: str
    source_table: str
    event_type: str | None
    stage: str | None
    status: str | None
    reason_code: str | None
    ts_ms: int
    detail: str | None
    request_id: str
    session_id: str | None = None
    clock_drift_flag: bool = False
    # 馃啎 鎯呮劅瀛楁
    emotion_tag: str | None = None       # 浜嬩欢鍙戠敓鏃剁殑鎯呮劅鏍囩
    mood_state: str | None = None        # 浜嬩欢鍙戠敓鏃剁殑 mood_state
    bond_stage: str | None = None        # 浜嬩欢鍙戠敓鏃剁殑 Bond 闃舵
```

**棰勬湡鏁堟灉锛?* 璺ㄧ鏃堕棿绾夸粠"鎯呮劅鐩?鍙樹负"鎯呮劅鎰熺煡"锛屽彲浠ユ竻鏅扮湅鍒?AI 鍦ㄤ粈涔堟椂鍒绘儏鎰熷彂鐢熶簡鍙樺寲"銆?Bond 闃舵鍦ㄤ粈涔堟椂鍒诲彂鐢熶簡鍙樺寲"锛屼负鎯呮劅鍒嗘瀽鍜岃皟璇曟彁渚涘畬鏁寸殑鍙娴嬫€с€?
---

### 馃煝 娣卞寲浼樺寲34锛圥1锛夛細`EMOTION_TO_EXPRESSION_ID` 鍙湁6绉嶆儏鎰熲€斺€擵TS 琛ㄦ儏鏄犲皠涓ラ噸涓嶈冻

**鐩爣鏂囦欢锛?* `host_brain/channels/vts.py`

**绮捐鍙戠幇锛?*

```python
EMOTION_TO_EXPRESSION_ID: dict[str, str] = {
    "NEUTRAL": "exp_neutral_01",
    "HAPPY": "exp_happy_01",
    "SAD": "exp_sad_01",
    "ANGRY": "exp_angry_01",
    "SURPRISED": "exp_surprised_01",
    "THINKING": "exp_thinking_01",
    # 鈫?鍙湁6绉嶏紒
}
```

浣?`metabolism.py` 鐨?`EmotionVector` 鏀寔鐨勬儏鎰熸爣绛捐繙涓嶆杩?绉嶏細

```python
# metabolism.py 涓殑鎯呮劅鏍囩锛堟潵鑷墠鍑犺疆鍒嗘瀽锛?ALLOWED_EMOTION_TAGS = frozenset({
    "NEUTRAL", "HAPPY", "SAD", "ANGRY", "SURPRISED", "THINKING",
    "EXCITED", "CALM", "ANXIOUS", "CONFUSED", "EMBARRASSED",
    "GRATEFUL", "LONELY", "PROUD", "SCARED", "DISGUSTED",
    # 鈫?鑷冲皯16绉嶆儏鎰熸爣绛?})
```

杩欐剰鍛崇潃锛?1. 褰?AI 澶勪簬 `EXCITED`銆乣CALM`銆乣ANXIOUS` 绛夌姸鎬佹椂锛宍resolve_vts_expression()` 浼氳繑鍥?`STATUS_DEGRADED` + neutral 闄嶇骇
2. 瓒呰繃60%鐨勬儏鎰熸爣绛炬棤娉曟槧灏勫埌瀵瑰簲鐨?VTS 琛ㄦ儏锛屽叏閮ㄩ檷绾т负 neutral
3. 鐢ㄦ埛鐪嬪埌鐨勮櫄鎷熷舰璞¤〃鎯呭拰 AI 鐨勫疄闄呮儏鎰熺姸鎬佷弗閲嶄笉绗︹€斺€擜I 璇磋嚜宸卞緢鍏村锛屼絾铏氭嫙褰㈣薄闈㈡棤琛ㄦ儏

**鏂规锛?* 鎵╁睍 `EMOTION_TO_EXPRESSION_ID` 鏄犲皠琛紝瑕嗙洊鎵€鏈?`EmotionVector` 鏀寔鐨勬儏鎰熸爣绛撅細

```python
EMOTION_TO_EXPRESSION_ID: dict[str, str] = {
    # 鍩虹鎯呮劅锛堝師鏈夛級
    "NEUTRAL":    "exp_neutral_01",
    "HAPPY":      "exp_happy_01",
    "SAD":        "exp_sad_01",
    "ANGRY":      "exp_angry_01",
    "SURPRISED":  "exp_surprised_01",
    "THINKING":   "exp_thinking_01",
    # 馃啎 鎵╁睍鎯呮劅锛坴1.5 鏂板锛?    "EXCITED":    "exp_excited_01",      # 鍏村
    "CALM":       "exp_calm_01",         # 骞抽潤
    "ANXIOUS":    "exp_anxious_01",      # 鐒﹁檻
    "CONFUSED":   "exp_confused_01",     # 鍥版儜
    "EMBARRASSED":"exp_embarrassed_01",  # 灏村艾
    "GRATEFUL":   "exp_grateful_01",     # 鎰熸縺
    "LONELY":     "exp_lonely_01",       # 瀛ょ嫭
    "PROUD":      "exp_proud_01",        # 鑷豹
    "SCARED":     "exp_scared_01",       # 瀹虫€?    "DISGUSTED":  "exp_disgusted_01",    # 鍘屾伓
    # 馃啎 mood_state 鍒拌〃鎯呯殑鏄犲皠锛堝綋 emotion_tag 涓嶅彲鐢ㄦ椂鐨勫閫夛級
    "ENGAGED":    "exp_engaged_01",      # 绮惧姏鍏呮矝
    "TIRED":      "exp_tired_01",        # 鐤叉儷
    "CRITICAL":   "exp_critical_01",     # 鑳介噺鏋佷綆
}

# 馃啎 mood_state 鍒?emotion_tag 鐨勬槧灏勶紙鐢ㄤ簬 mood_state 椹卞姩琛ㄦ儏锛?MOOD_STATE_TO_EMOTION_TAG: dict[str, str] = {
    "engaged":  "EXCITED",
    "steady":   "NEUTRAL",
    "calm":     "CALM",
    "tired":    "SAD",
    "critical": "ANXIOUS",
}
```

**鍚屾椂淇 `resolve_vts_expression()` 鐨勯檷绾ч€昏緫锛?*

```python
def resolve_vts_expression(
    *,
    emotion_tag: str | None,
    ledger: SQLiteLedger,
    request_id: str | None = None,
    trigger: str = TRIGGER_LLM,
    raw_expression_id: str | None = None,
    raw_action_params: dict[str, Any] | None = None,
    ts_ms: int | None = None,
    edge_available: bool = True,
    mood_state: str | None = None,   # 馃啎 澶囬€夛細褰?emotion_tag 涓嶅彲鐢ㄦ椂鐢?mood_state 鎺ㄦ柇
) -> VTSActionResult:
    # ...
    # 馃啎 emotion_tag 涓虹┖鏃讹紝灏濊瘯浠?mood_state 鎺ㄦ柇
    if not normalized_tag and mood_state:
        inferred_tag = MOOD_STATE_TO_EMOTION_TAG.get(str(mood_state).lower())
        if inferred_tag:
            normalized_tag = inferred_tag
```

**棰勬湡鏁堟灉锛?* VTS 琛ㄦ儏鏄犲皠浠?绉嶆墿灞曞埌16+绉嶏紝瑕嗙洊鎵€鏈?`EmotionVector` 鏀寔鐨勬儏鎰熸爣绛撅紝铏氭嫙褰㈣薄琛ㄦ儏鍜?AI 鎯呮劅鐘舵€佺殑涓€鑷存€т粠40%鎻愬崌鍒版帴杩?00%銆?
---

### 馃數 娣卞寲浼樺寲35锛圥1锛夛細`CameraRecognitionServiceContract.detect()` 鐨勮瑙夎瘑鍒粨鏋滀笉瑙﹀彂鎯呮劅鍝嶅簲鈥斺€旇瑙夋劅鐭ヤ笌鎯呮劅绯荤粺瀹屽叏鏂紑

**鐩爣鏂囦欢锛?* `host_brain/vision/camera_event.py`

**绮捐鍙戠幇锛?*

`detect()` 鐨勮緭鍑虹粨鏋勶細

```python
output = {
    "request_id": request_id,
    "intent_id": intent_id,
    "ts_ms": now_value,
    "evidence_ref": evidence_value,
    "detector_id": str(detector_id or CAMERA_DETECTOR_ID),
    "objects": objects,
    "status": STATUS_OK,
    "stage": S_VISION_CAMERA,
    "reason_code": None,
    "degraded": False,
    "latency_ms": latency_ms,
    "summary": {
        "object_count": len(objects),
        "low_confidence_count": low_conf_count,
    },
    "quality": {"stage": S_VISION_CAMERA, "reason_code": None},
    # 鈫?娌℃湁浠讳綍鎯呮劅鐩稿叧瀛楁锛?}
```

`_log_event()` 鍐欏叆瀹¤鐨?payload锛?
```python
payload={
    "detector_id": output["detector_id"],
    "object_count": len(objects),
    "low_confidence_count": low_conf_count,
    "latency_ms": latency_ms,
    # 鈫?娌℃湁鎯呮劅瑙﹀彂淇℃伅锛?},
```

杩欐剰鍛崇潃锛?1. 瑙嗚璇嗗埆缁撴灉锛堟娴嬪埌鐢ㄦ埛鐨勮〃鎯呫€佸Э鎬併€佺墿浣擄級瀹屽叏涓嶈Е鍙戞儏鎰熷搷搴?2. 濡傛灉鎽勫儚澶存娴嬪埌鐢ㄦ埛鍦ㄥ摥娉ｏ紙`label="crying_face"`锛夛紝AI 涓嶄細鍥犳浜х敓鎯呮劅鍙嶅簲
3. 濡傛灉妫€娴嬪埌鐢ㄦ埛鍦ㄥ井绗戯紙`label="smiling_face"`锛夛紝AI 涓嶄細鍥犳鎰熷埌寮€蹇?4. 瑙嗚鎰熺煡鍜屾儏鎰熺郴缁熶箣闂存病鏈変换浣曡繛鎺モ€斺€旇瑙夋槸"鎯呮劅鐩?鐨?
**鏂规锛?* 涓鸿瑙夎瘑鍒粨鏋滃鍔犳儏鎰熸帹鏂紝骞跺湪妫€娴嬪埌鎯呮劅鐩稿叧鐗╀綋鏃惰Е鍙戞儏鎰熷搷搴旓細

```python
# 馃啎 瑙嗚鎯呮劅瑙﹀彂瑙勫垯锛坴1.5 鏂板锛?VISION_EMOTION_TRIGGERS: dict[str, tuple[str, float]] = {
    # label 鈫?(emotion_tag, intensity)
    "crying_face":    ("SAD",      0.8),
    "smiling_face":   ("HAPPY",    0.7),
    "angry_face":     ("ANXIOUS",  0.6),
    "surprised_face": ("SURPRISED", 0.7),
    "sleeping":       ("CALM",     0.5),
    "laughing":       ("EXCITED",  0.8),
    "worried_face":   ("ANXIOUS",  0.6),
    "confused_face":  ("CONFUSED", 0.5),
}

def _infer_emotion_from_objects(
    objects: list[dict[str, Any]],
) -> tuple[str | None, float]:
    """浠庤瑙夎瘑鍒粨鏋滄帹鏂儏鎰熻Е鍙戙€?
    Returns:
        (emotion_tag, intensity) 鎴?(None, 0.0) 濡傛灉鏃犳儏鎰熻Е鍙?    """
    best_emotion: str | None = None
    best_intensity: float = 0.0

    for obj in objects:
        label = str(obj.get("label") or "").lower()
        conf = float(obj.get("conf") or 0.0)
        if conf < 0.6:  # 浣庣疆淇″害涓嶈Е鍙戞儏鎰?            continue
        trigger = VISION_EMOTION_TRIGGERS.get(label)
        if trigger:
            emotion_tag, base_intensity = trigger
            weighted_intensity = round(base_intensity * conf, 4)
            if weighted_intensity > best_intensity:
                best_emotion = emotion_tag
                best_intensity = weighted_intensity

    return best_emotion, best_intensity
```

**鍦?`detect()` 涓泦鎴愭儏鎰熸帹鏂細**

```python
objects = _normalize_objects(raw_objects, low_conf_threshold=self.low_conf_threshold)
# 馃啎 浠庤瑙夎瘑鍒粨鏋滄帹鏂儏鎰熻Е鍙?inferred_emotion, emotion_intensity = _infer_emotion_from_objects(objects)

output = {
    # ... 鍘熸湁瀛楁 ...
    # 馃啎 鎯呮劅鎺ㄦ柇缁撴灉
    "inferred_emotion": inferred_emotion,
    "emotion_intensity": emotion_intensity,
    "emotion_triggered": inferred_emotion is not None,
}
```

**棰勬湡鏁堟灉锛?* 瑙嗚鎰熺煡浠?鎯呮劅鐩?鍙樹负"鎯呮劅鎰熺煡"锛屾憚鍍忓ご妫€娴嬪埌鐢ㄦ埛鍝常鏃?AI 浼氫骇鐢?`SAD` 鎯呮劅鍝嶅簲锛屾娴嬪埌鐢ㄦ埛寰瑧鏃?AI 浼氫骇鐢?`HAPPY` 鎯呮劅鍝嶅簲锛岃瑙夋劅鐭ュ拰鎯呮劅绯荤粺瀹炵幇鐪熸鐨勮繛鎺ャ€?
---

### 馃煟 娣卞寲浼樺寲36锛圥2锛夛細`HybridFusionStrategy.fuse()` 鐨勮瀺鍚堝垎鏁颁笉鎰熺煡鎯呮劅寮哄害鈥斺€旈珮鎯呮劅寮哄害鐨勮蹇嗗拰浣庢儏鎰熷己搴︾殑璁板繂寰楀埌鐩稿悓鐨勮瀺鍚堟潈閲?
**鐩爣鏂囦欢锛?* `host_brain/retrieval/recall_fusion.py`

**绮捐鍙戠幇锛?*

`HybridFusionStrategy.fuse()` 鐨勮瀺鍚堝叕寮忥細

```python
final_score = round(
    vector_score * self.vector_weight + fts_score * self.fts_weight,
    6,
)
# 鈫?鍙湁鍚戦噺鍒嗘暟鍜屽叏鏂囧垎鏁帮紝娌℃湁鎯呮劅寮哄害鍔犳潈锛?```

`_normalize_candidate()` 鐨?metadata 澶勭悊锛?
```python
merged_metadata["base_score"] = base_score
merged_metadata["matched_routes"] = [route.route_id]
merged_metadata["recall_rank"] = int(rank)
# 鈫?娌℃湁 emotional_intensity 瀛楁锛?```

杩欐剰鍛崇潃锛?1. 涓€鏉?鎯呮劅寮哄害=0.9"鐨勮蹇嗭紙鐢ㄦ埛鍝常鏃朵骇鐢熺殑璁板繂锛夊拰涓€鏉?鎯呮劅寮哄害=0.1"鐨勮蹇嗭紙鏅€氶棽鑱婃椂浜х敓鐨勮蹇嗭級鍦ㄨ瀺鍚堟椂寰楀埌鐩稿悓鐨勬潈閲?2. 楂樻儏鎰熷己搴︾殑璁板繂搴旇鍦ㄥ彫鍥炴椂寰楀埌鏇撮珮鐨勪紭鍏堢骇锛屼絾褰撳墠铻嶅悎绛栫暐瀹屽叏蹇界暐浜嗚繖涓€鐐?3. 杩欏鑷?AI 鍦ㄥ洖蹇嗘椂锛屾櫘閫氶棽鑱婄殑璁板繂鍜屾繁搴︽儏鎰熶簰鍔ㄧ殑璁板繂琚悓绛夊寰?
**鏂规锛?* 涓?`HybridFusionStrategy` 澧炲姞鎯呮劅寮哄害鍔犳潈鍙傛暟锛?
```python
class HybridFusionStrategy:
    def __init__(
        self,
        *,
        vector_weight: float = DEFAULT_VECTOR_WEIGHT,
        fts_weight: float = DEFAULT_FTS_WEIGHT,
        emotion_intensity_weight: float = 0.0,   # 馃啎 鎯呮劅寮哄害鍔犳潈锛堥粯璁?锛屼笉褰卞搷鐜版湁琛屼负锛?    ) -> None:
        # ...
        self.emotion_intensity_weight = float(emotion_intensity_weight)

    def fuse(
        self,
        *,
        vector_candidates: list[dict[str, Any]],
        fts_candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        # ...
        # 馃啎 鎯呮劅寮哄害鍔犳潈
        if self.emotion_intensity_weight > 0:
            for doc_id, entry in fused.items():
                metadata = entry.get("metadata") or {}
                emotional_intensity = float(metadata.get("emotional_intensity") or 0.0)
                emotion_boost = round(emotional_intensity * self.emotion_intensity_weight, 6)
                entry["score"] = round(
                    min(1.0, float(entry["score"]) + emotion_boost),
                    6,
                )
                entry["metadata"] = dict(metadata)
                entry["metadata"]["emotion_intensity_boost"] = emotion_boost
```

**棰勬湡鏁堟灉锛?* 娣峰悎铻嶅悎绛栫暐浠?鎯呮劅鐩?鍙樹负"鎯呮劅鎰熺煡"锛岄珮鎯呮劅寮哄害鐨勮蹇嗭紙娣卞害鎯呮劅浜掑姩鏃朵骇鐢熺殑璁板繂锛夊湪鍙洖鏃跺緱鍒版洿楂樼殑浼樺厛绾э紝AI 鐨勫洖蹇嗘洿鍊惧悜浜庢儏鎰熸繁鍒荤殑鏃跺埢銆?
---

## 浜屽崄涓€銆佺鍏疆浼樺寲鐨勬牳蹇冧环鍊兼€荤粨

绗叚杞紭鍖栬仛鐒︿簬**鎰熺煡灞傛儏鎰熸柇璺?*鈥斺€旀儏鎰熶俊鎭湪"鎰熺煡鈫掑喅绛栤啋杈撳嚭鈫掕娴?鍏ㄩ摼璺腑鐨勭郴缁熸€ф柇璺細

| 浼樺寲椤?| 鍙戠幇鐨?鎰熺煡灞傛柇璺? | 褰卞搷鑼冨洿 | 淇鏂规 |
|--------|-----------------|---------|---------|
| **浼樺寲31** 馃敶 | 鍙洖绯荤粺鏃犳儏鎰熺淮搴﹁矾鐢憋紝鎯呮劅璁板繂鍜屾櫘閫氳蹇嗘贩鍦ㄥ悓涓€鍙洖姹?| `recall_fusion.py` 鈫?璁板繂鍙洖 | 澧炲姞鎯呮劅杩囨护鍙傛暟鍜屾儏鎰熼噸鎺掑簭 |
| **浼樺寲32** 馃煚 | 鍐崇瓥灞備笉鎰熺煡鎯呮劅鐘舵€侊紝critical 鐘舵€佷笅楂樻秷鑰楁妧鑳戒笉琚嫆缁?| `decision.py` 鈫?闂ㄦ帶瑙勫垯 | 澧炲姞 R8 鎯呮劅淇濇姢闂ㄦ帶 |
| **浼樺寲33** 馃煛 | 璺ㄧ鏃堕棿绾夸笉鍚儏鎰熶簨浠讹紝鎯呮劅鍙樺寲瀵硅娴嬪眰涓嶅彲瑙?| `cross_timeline.py` 鈫?鍙娴嬫€?| 澧炲姞鎯呮劅浜嬩欢绫诲瀷鍜屾儏鎰熷璁¤〃 |
| **浼樺寲34** 馃煝 | VTS 琛ㄦ儏鏄犲皠鍙湁6绉嶏紝60%+鎯呮劅鏍囩闄嶇骇涓?neutral | `vts.py` 鈫?铏氭嫙褰㈣薄 | 鎵╁睍鏄犲皠琛ㄥ埌16+绉嶏紝澧炲姞 mood_state 澶囬€?|
| **浼樺寲35** 馃數 | 瑙嗚璇嗗埆缁撴灉涓嶈Е鍙戞儏鎰熷搷搴旓紝瑙嗚鎰熺煡涓庢儏鎰熺郴缁熷畬鍏ㄦ柇寮€ | `camera_event.py` 鈫?瑙嗚鎰熺煡 | 澧炲姞瑙嗚鎯呮劅瑙﹀彂瑙勫垯锛屾娴嬪埌鐢ㄦ埛琛ㄦ儏鏃惰Е鍙戞儏鎰熷搷搴?|
| **浼樺寲36** 馃煟 | 娣峰悎铻嶅悎鍒嗘暟涓嶆劅鐭ユ儏鎰熷己搴︼紝楂樻儏鎰熷己搴﹁蹇嗗拰鏅€氳蹇嗗悓绛夊寰?| `recall_fusion.py` 鈫?铻嶅悎绛栫暐 | 澧炲姞鎯呮劅寮哄害鍔犳潈鍙傛暟 |

---

## 浜屽崄浜屻€佸畬鏁翠紭鍖栧叏鏅浘锛坴1.5锛?6椤癸級

### 鍏疆浼樺寲鐨勫眰娆￠€掕繘

```
绗竴杞紙v1.0锛夛細鍙戠幇5涓?缂哄け鍔熻兘"     鈫?闇€瑕佹柊澧炰粈涔?绗簩杞紙v1.1锛夛細鍙戠幇6涓?娣卞眰闂"     鈫?鐜版湁鏈哄埗鍝噷涓嶅娣?绗笁杞紙v1.2锛夛細鍙戠幇6涓?鍋囧姛鑳界┖澹?   鈫?鐜版湁浠ｇ爜鍝噷鐪嬭捣鏉ユ湁浣嗗疄闄呮病鐢?绗洓杞紙v1.3锛夛細鍙戠幇6涓?鏋舵瀯鏂眰"     鈫?鍔熻兘涔嬮棿鐨勮繛鎺ュ湪鍝噷鏂
绗簲杞紙v1.4锛夛細鍙戠幇6涓?璺ㄦā鍧楃洸鍖?   鈫?鎯呮劅淇℃伅鍦ㄥ叏閾捐矾涓殑绯荤粺鎬х己澶?绗叚杞紙v1.5锛夛細鍙戠幇6涓?鎰熺煡灞傛柇璺?   鈫?鎯呮劅淇℃伅鍦ㄦ劅鐭モ啋鍐崇瓥鈫掕緭鍑衡啋瑙傛祴涓殑鏂矾
```

### 鎸変紭鍏堢骇鍒嗙被锛?6椤瑰叏鏅級

| 浼樺厛绾?| 浼樺寲椤?| 鏍稿績闂 |
|--------|--------|---------|
| **P0锛堝繀椤讳慨澶嶏級** | OPT19, OPT20, OPT22, OPT25, OPT26, OPT27, OPT31, OPT32 | 鏋舵瀯鏂眰 + 璺ㄦā鍧楃洸鍖?+ 鎰熺煡灞傛柇璺腑鐨勫叧閿矾寰?|
| **P1锛堥噸瑕佷紭鍖栵級** | OPT1, OPT6, OPT7, OPT8, OPT13, OPT14, OPT16, OPT17, OPT18, OPT21, OPT28, OPT29, OPT33, OPT34, OPT35 | 鎯呮劅鍐呮牳 + 鍏崇郴灞?+ 璁板繂灞?+ 鎰熺煡灞?|
| **P2锛堜綋楠屽寮猴級** | OPT2, OPT3, OPT4, OPT5, OPT9, OPT10, OPT11, OPT12, OPT15, OPT23, OPT24, OPT30, OPT36 | 杈撳嚭灞?+ 缁嗚妭鎵撶（ |

### 鎯呮劅淇℃伅鍏ㄩ摼璺鐩栧浘锛坴1.5锛?
```
鐢ㄦ埛杈撳叆
    鈫?[瑙嗚鎰熺煡灞俔 camera_event.py
    OPT35: 瑙嗚璇嗗埆 鈫?鎯呮劅瑙﹀彂锛堟柊澧烇級
    鈫?[鍙洖灞俔 recall_fusion.py
    OPT31: 鎯呮劅缁村害璺敱锛堟柊澧烇級
    OPT36: 鎯呮劅寮哄害鍔犳潈铻嶅悎锛堟柊澧烇級
    鈫?[鍐崇瓥灞俔 governor/decision.py
    OPT32: R8 鎯呮劅淇濇姢闂ㄦ帶锛堟柊澧烇級
    鈫?[鎯呮劅鍐呮牳] metabolism.py
    OPT6/7/8: EmotionVector 娣卞寲锛堝墠鍑犺疆锛?    鈫?[鍏崇郴灞俔 bond/
    OPT26: 鎯呮劅鍔犳潈 Bond 绉垎锛堢浜旇疆锛?    OPT27: derive_bond_summary 淇锛堢浜旇疆锛?    鈫?[璁板繂灞俔 memory/
    OPT25: EpisodicRecord 鎯呮劅瀛楁锛堢浜旇疆锛?    OPT28: 鍘嬬缉鍓嶄繚瀛樼敤鎴锋儏鎰燂紙绗簲杞級
    OPT29: 鎯呮劅鍏崇郴钂搁涓婚锛堢浜旇疆锛?    鈫?[杈撳嚭灞俔 qa.py / prompt_context.py
    OPT19/20/22: 鏋舵瀯鏂眰淇锛堢鍥涜疆锛?    鈫?[閫氶亾灞俔 channels/vts.py
    OPT34: VTS 琛ㄦ儏鏄犲皠鎵╁睍锛堢鍏疆锛?    鈫?[瑙傛祴灞俔 observability/cross_timeline.py
    OPT33: 鎯呮劅浜嬩欢绾冲叆鏃堕棿绾匡紙绗叚杞級
    鈫?鐢ㄦ埛鎰熺煡鍒扮殑鎯呮劅杈撳嚭
```

