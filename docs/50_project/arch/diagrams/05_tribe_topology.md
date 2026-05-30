受众：MASTER
标题：图 05 — Tribe 23 family 拓扑（脑区 / 内分泌 / 外周 / 执行）

版本：v1.0
日期：2026-05-30
归属：docs/50_project/arch/diagrams/

---

## 怎么读这张图

23 family 不是一个扁平列表，而是按"功能层"自然分簇：

| 簇 | family | 角色 |
|---|---|---|
| 🧠 **认知中枢** | cortex / thalamus / prefrontal / hippocampus / language | 主链编排、意图、计划、记忆、语言 |
| 💛 **情感与动机** | limbic / pain_reward / endocrine / mirror_neuron / interoception | 情绪、奖惩、激素、共情、内感 |
| 👁️ **感知与监控** | proprioception / somatic / vestibular / immune | 本体感、躯体、平衡、风险 |
| 🌐 **社交与外周** | social_brain / peripheral / language（外向） | 社交模型、外围执行、对外语言 |
| 🌙 **节律与代谢** | circadian / glymphatic / ans / default_mode | 昼夜、清理、自主神经、默认模式 |
| ⚡ **执行与反射** | motor_cortex / cerebellum / initiative_governor | 动作、协调、自启动 |

## 23 family 全景图

```mermaid
graph TB
    subgraph Cognition["🧠 认知中枢"]
        C1["cortex<br/>CortexAgent"]
        C2["thalamus<br/>ThalamusAgent"]
        C3["prefrontal<br/>PrefrontalAgent"]
        C4["hippocampus<br/>HippocampusAgent"]
        C5["language<br/>LanguageSystemAgent"]
    end

    subgraph Affective["💛 情感与动机"]
        A1["limbic<br/>LimbicAgent"]
        A2["pain_reward<br/>PainRewardAgent"]
        A3["endocrine<br/>EndocrineAgent"]
        A4["mirror_neuron<br/>MirrorNeuronAgent"]
        A5["interoception<br/>InteroceptionAgent"]
    end

    subgraph Perception["👁️ 感知与监控"]
        P1["proprioception<br/>ProprioceptionAgent"]
        P2["somatic<br/>SomaticAgent"]
        P3["vestibular<br/>VestibularAgent"]
        P4["immune<br/>ImmuneAgent"]
    end

    subgraph Social["🌐 社交与外周"]
        S1["social_brain<br/>SocialBrainAgent"]
        S2["peripheral<br/>PeripheralAgent"]
    end

    subgraph Rhythm["🌙 节律与代谢"]
        R1["circadian<br/>CircadianAgent"]
        R2["glymphatic<br/>GlymphaticAgent"]
        R3["ans<br/>ANSAgent"]
        R4["default_mode<br/>DefaultModeAgent"]
    end

    subgraph Motor["⚡ 执行与反射"]
        M1["motor_cortex<br/>MotorCortexAgent"]
        M2["cerebellum<br/>CerebellumAgent"]
        M3["initiative_governor<br/>InitiativeGovernorAgent"]
    end

    C2 -.意图编译.-> C1
    C1 -.编排.-> M1
    C3 -.漂移告警.-> C1
    C4 -.记忆召回.-> C1
    A1 -.情绪态.-> C1
    P4 -.风险门控.-> C1
    A3 -.激素基调.-> A1
    R3 -.唤醒度.-> C2
    R1 -.昼夜.-> R2
    M3 -.主动触发.-> C1
    M2 -.动作协调.-> M1
```

## 主链关键 family 在 chat 流的位置

```mermaid
flowchart LR
    IN["用户输入"] --> THA["thalamus<br/>意图编译"]
    THA --> IMM["immune<br/>风险门控"]
    IMM --> COR["cortex<br/>主链编排"]
    COR --> LIM["limbic<br/>情绪/动机"]
    LIM --> PRE["prefrontal<br/>self-reflection"]
    PRE --> HIP["hippocampus<br/>记忆召回"]
    HIP --> LANG["language<br/>语言生成"]
    LANG --> MOT["motor_cortex<br/>动作派发"]
    MOT --> OUT["对外回复 / Edge 动作"]
```

## 23 family 完整代码事实清单

| # | family_id | 默认 display_name |
|---:|---|---|
| 1 | `cortex` | CortexAgent |
| 2 | `thalamus` | ThalamusAgent |
| 3 | `ans` | ANSAgent |
| 4 | `hippocampus` | HippocampusAgent |
| 5 | `limbic` | LimbicAgent |
| 6 | `prefrontal` | PrefrontalAgent |
| 7 | `social_brain` | SocialBrainAgent |
| 8 | `endocrine` | EndocrineAgent |
| 9 | `immune` | ImmuneAgent |
| 10 | `pain_reward` | PainRewardAgent |
| 11 | `language` | LanguageSystemAgent |
| 12 | `somatic` | SomaticAgent |
| 13 | `peripheral` | PeripheralAgent |
| 14 | `circadian` | CircadianAgent |
| 15 | `proprioception` | ProprioceptionAgent |
| 16 | `initiative_governor` | InitiativeGovernorAgent |
| 17 | `default_mode` | DefaultModeAgent |
| 18 | `mirror_neuron` | MirrorNeuronAgent |
| 19 | `interoception` | InteroceptionAgent |
| 20 | `motor_cortex` | MotorCortexAgent |
| 21 | `cerebellum` | CerebellumAgent |
| 22 | `glymphatic` | GlymphaticAgent |
| 23 | `vestibular` | VestibularAgent |

## 代码锚点

| 内容 | 文件 |
|---|---|
| 23 family 真源 | `host_brain/tribe/registry.py:_DEFAULT_FAMILY_SPECS` |
| `TribeFamilySpec` 定义 | `host_brain/tribe/registry.py:10` |
| `HostTribeRegistry` | `host_brain/tribe/registry.py:44` |
| 默认 agent 描述 | `host_brain/tribe/registry.py:_bootstrap_default_agents` |
| 主链 family agent | `host_brain/tribe/*_agent.py`（每个 family 各一） |

## 不变量

1. **23 family**，既不多也不少；任何文档说"22 个器官族"都是过期口径。
2. 灰度开关已**全数删除**；A 级化升级闭环。
3. family 注册以 `_DEFAULT_FAMILY_SPECS` 为唯一真源；运行时可 `set_host_tribe_registry(...)` 注入定制版本。
4. cortex 是默认 fallback：当 descriptor 没有明确 family 时归到 cortex。

## 已废弃路径

| 旧路径 | 当前事实 |
|---|---|
| 22 个器官族 | ❌ 已升级为 23（新增 `glymphatic`） |
| family 灰度开关 | ❌ 已全数删除 |
| C 级 / B 级 family | ❌ 全数升级到 A 级 |
