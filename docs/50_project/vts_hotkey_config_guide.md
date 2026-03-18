受众：PROJECT
标题：VTS 热键配置文件说明

# VTS 热键配置文件说明

> 文档版本：v1.0.0 | 关联任务：TASK-0227 M26-F | 最后更新：2026-03-05

---

## 1. 概述

`configs/vts/vts_hotkey_config.yaml` 是 VTube Studio（VTS）热键的**唯一配置入口**，用于：

1. 定义 `emotion_tag → expression_id` 映射表（供情感驱动对话链路使用）
2. 记录所有热键的快捷键绑定、分组、触发参数及说明
3. 替代 `vts_mapping_store.py` 中的 `DEFAULT_EMOTION_MAPPING` 硬编码

---

## 2. 文件位置

```
configs/
└── vts/
    └── vts_hotkey_config.yaml   ← 热键配置文件（本文档描述的对象）
```

---

## 3. 配置文件结构

```yaml
meta:                    # 元信息
  model_name: "..."
  config_version: "..."
  description: "..."

defaults:                # 全局默认触发参数
  trigger_delay_ms: 0
  priority: 0
  fallback_tag: "NEUTRAL"

emotion_mapping:         # emotion_tag → expression_id 映射表
  NEUTRAL: "exp_neutral_01"
  HAPPY:   "exp_happy_01"
  ...

hotkey_groups:           # 热键分组列表
  - group: "分组名称"
    hotkeys:
      - name: "热键名称"
        hotkey_id: "..."
        shortcut: "..."
        emotion_tag: "HAPPY" | null
        description: "..."
        trigger_delay_ms: 0    # 可选，覆盖 defaults
        priority: 0            # 可选，覆盖 defaults
```

---

## 4. 字段说明

### 4.1 `meta` — 元信息

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `model_name` | string | ✅ | Live2D 模型名称 |
| `config_version` | string | ✅ | 配置文件版本号（语义化版本，如 `v1.0.0`） |
| `description` | string | ❌ | 配置文件描述 |

### 4.2 `defaults` — 全局默认触发参数

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `trigger_delay_ms` | int | `0` | 热键触发延迟（毫秒），用于动画衔接 |
| `priority` | int | `0` | 触发优先级，数值越大越优先 |
| `fallback_tag` | string | `"NEUTRAL"` | `emotion_tag` 不存在时的降级标签 |

### 4.3 `emotion_mapping` — 情感映射表

`emotion_tag`（大写枚举）→ `expression_id`（VTS 内部表情 ID）的映射字典。

**当前支持的 emotion_tag 枚举：**

| emotion_tag | 含义 | 对应 expression_id |
|-------------|------|-------------------|
| `NEUTRAL` | 平静/默认 | `exp_neutral_01` |
| `HAPPY` | 开心/愉快 | `exp_happy_01` |
| `SAD` | 难过/悲伤 | `exp_sad_01` |
| `ANGRY` | 生气/愤怒 | `exp_angry_01` |
| `SURPRISED` | 惊讶 | `exp_surprised_01` |
| `THINKING` | 思考 | `exp_thinking_01` |
| `CALM` | 平和/冷静 | `exp_calm_01` |
| `EXCITED` | 兴奋 | `exp_excited_01` |

> ⚠️ `expression_id` 必须与 VTube Studio 软件中实际配置的表情 ID 完全一致，否则触发无效。

### 4.4 `hotkey_groups` — 热键分组

每个分组包含 `group`（分组名）和 `hotkeys`（热键列表）。

**热键字段说明：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | ✅ | 热键显示名称（中文） |
| `hotkey_id` | string | ✅ | VTS 内部热键 ID，需与 VTS 软件配置一致 |
| `shortcut` | string | ✅ | 快捷键绑定（仅供说明，实际触发由 VTS 软件管理） |
| `emotion_tag` | string \| null | ❌ | 关联的情感标签；`null` 表示该热键不参与情感驱动 |
| `description` | string | ❌ | 热键功能说明 |
| `trigger_delay_ms` | int | ❌ | 覆盖全局 `defaults.trigger_delay_ms` |
| `priority` | int | ❌ | 覆盖全局 `defaults.priority` |

---

## 5. 热键分组总览（宅家少女模型）

### 5.1 基础外观（4 个）

| 名称 | 快捷键 | 说明 |
|------|--------|------|
| 去水印 | `Numpad0` | 去除模型水印 |
| 飞头 | `Shift+N1` | 飞头效果 |
| 变Q | `Shift+N2` | 切换 Q 版形态 |
| 改色 | `Shift+N3` | 改变模型配色 |

### 5.2 表情（15 个）

| 名称 | 快捷键 | emotion_tag |
|------|--------|-------------|
| 生气 | `Numpad1` | ANGRY |
| 鄙视脸黑 | `Numpad2` | ANGRY |
| 脸黑 | `Numpad3` | ANGRY |
| 脸红 | `Numpad4` | HAPPY |
| 眩晕 | `Numpad5` | SURPRISED |
| 哭哭 | `Numpad6` | SAD |
| 剪刀眼 | `Numpad7` | HAPPY |
| 星星眼 | `Numpad8` | EXCITED |
| 爱心眼 | `Numpad9` | HAPPY |
| 四叶草 | `Ctrl+N0` | HAPPY |
| 黑眼 | `Ctrl+N1` | NEUTRAL |
| 白眼 | `Ctrl+N2` | NEUTRAL |
| 无语眼 | `Ctrl+N3` | NEUTRAL |
| 圈圈眼 | `Ctrl+N4` | SURPRISED |
| 蓝瞳孔 | `Ctrl+N5` | CALM |

### 5.3 配件（10 个）

| 名称 | 快捷键 |
|------|--------|
| 白耳机 | `Ctrl+N6` |
| 黑耳机 | `Ctrl+N7` |
| 兽耳消失 | `Ctrl+N8` |
| 眼镜 | `Ctrl+N9` |
| 兔兔消失 | `Tab+N1` |
| 兔兔位置 | `Tab+N2` |
| 脖子项圈 | `Tab+N3` |
| 项链1 | `Tab+N4` |
| 项链2消失 | `Tab+N5` |
| 黑鞋 | `Tab+N6` |

### 5.4 发型（9 个）

| 名称 | 快捷键 |
|------|--------|
| 左小啾啾 | `CAPS+N1` |
| 右啾啾 | `CAPS+N2` |
| 啾啾短发 | `CAPS+N3` |
| 长发 | `CAPS+N4` |
| 水母短发 | `CAPS+N5` |
| 水母长发 | `CAPS+N6` |
| 紫色挑染 | `CAPS+N7` |
| 左小啾啾消失 | `CAPS+N8` |
| 右小啾啾消失 | `CAPS+N9` |

### 5.5 道具（7 个）

| 名称 | 快捷键 | emotion_tag |
|------|--------|-------------|
| 手柄 | `Shift+N4` | — |
| 麦克风 | `Shift+N5` | — |
| 黑麦克风 | `Shift+N6` | — |
| 抱兔兔 | `Shift+N7` | — |
| 比耶 | `Shift+N8` | HAPPY |
| 拿手机 | `Shift+N9` | — |
| 指脸 | `Shift+N0` | — |

### 5.6 屏幕特效（4 个）

| 名称 | 快捷键 | emotion_tag |
|------|--------|-------------|
| 屏幕 | `Alt+N1` | — |
| 屏幕竖眼 | `Alt+N2` | — |
| 屏幕笑眼 | `Alt+N3` | HAPPY |
| 移除所有按键表情 | `Alt+N0` | NEUTRAL |

---

## 6. 与 VTSMappingStore 的关联关系

```
vts_hotkey_config.yaml
        │
        │  emotion_mapping 节
        ▼
VTSMappingStore.load_from_config_file(path)
        │
        │  写入 SQLite vts_expression_mapping 表
        ▼
VTSMappingStore.get_expression_id(emotion_tag)
        │
        │  返回 expression_id
        ▼
VTSDriver.set_expression(emotion_tag)
        │
        │  调用 VTS WebSocket API TriggerHotkey
        ▼
Live2D 模型表情切换
```

**加载优先级：**
1. `load_from_config_file()` 从 YAML 文件加载（优先）
2. YAML 未配置的 `emotion_tag` 使用 `DEFAULT_EMOTION_MAPPING` 作为 fallback
3. 两者都没有时，`get_expression_id()` 抛出 `AVATAR_INVALID_EMOTION_TAG`

---

## 7. 扩展方式

### 7.1 新增 emotion_tag

在 `emotion_mapping` 节添加新条目：

```yaml
emotion_mapping:
  # ... 现有条目 ...
  SLEEPY: "exp_sleepy_01"   # 新增：困倦
```

同时在 `host_brain/core/qa.py` 的 `VALID_EMOTION_TAGS` 枚举中添加对应值。

### 7.2 新增热键

在对应分组的 `hotkeys` 列表中追加：

```yaml
- name: "新热键名称"
  hotkey_id: "hotkey_xxx"      # 需与 VTS 软件中配置的 ID 一致
  shortcut: "Ctrl+N0"
  emotion_tag: "HAPPY"         # 或 null
  description: "功能说明"
```

### 7.3 新增分组

在 `hotkey_groups` 列表末尾追加新分组：

```yaml
- group: "新分组名称"
  hotkeys:
    - name: "..."
      hotkey_id: "..."
      shortcut: "..."
      emotion_tag: null
      description: "..."
```

### 7.4 切换模型配置

为不同模型创建独立配置文件：

```
configs/vts/
├── vts_hotkey_config.yaml          # 当前激活的配置（宅家少女）
├── vts_hotkey_config.zhaijia.yaml  # 宅家少女备份
└── vts_hotkey_config.other.yaml    # 其他模型
```

在启动参数或 `configs/model_plane/model_profiles.yaml` 中指定激活的配置文件路径。

---

## 8. 注意事项

1. **`hotkey_id` 必须与 VTube Studio 软件中实际配置的热键 ID 完全一致**，否则触发无效。可在 VTS 软件的热键管理界面查看。
2. **`shortcut` 字段仅供说明**，实际快捷键触发由 VTS 软件管理，代码通过 `hotkey_id` 触发，不依赖 `shortcut`。
3. **`emotion_tag: null`** 表示该热键不参与情感驱动对话链路，仅作为外观/道具切换使用。
4. 修改配置文件后，需调用 `VTSMappingStore.load_from_config_file()` 重新加载，或重启服务使配置生效。
5. 配置文件变更会写入 `vts_mapping_change_log` 审计表（含变更前后 hash）。

---

## 9. 相关文件

| 文件 | 说明 |
|------|------|
| `configs/vts/vts_hotkey_config.yaml` | 本配置文件 |
| `host_brain/channels/vts_mapping_store.py` | 映射表持久化存储，`load_from_config_file()` 方法 |
| `edge_client/actuators/vts_driver.py` | VTS 驱动，`set_expression()` 方法 |
| `host_brain/core/qa.py` | QA 校验，`_validate_emotion_tag()` 方法 |
| `docs/30_devspec/DevTasks.md` | TASK-0226 M26-E / TASK-0227 M26-F |
