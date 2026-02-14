# V6.1 技能元数据表 (纯文本字典)
# 这里定义了 Dify 能看到的所有工具

SKILL_CATALOG = {
    # === 系统控制 ===
    "control_process": {
        "module": "system_ops",           # 对应 skills/impl/system_ops.py
        "func": "manage_process",
        "desc": "管理系统进程(查找/关闭)。Action: find/kill",
        "params": {"action": "str", "process_name": "str", "target": "str (host/edge)"}
    },
    "system_power": {
        "module": "system_ops",
        "func": "power_action",
        "desc": "电源控制(关机/重启)。Action: shutdown/restart",
        "params": {"action": "str", "target": "str (host/edge)"}
    },
    # === 视觉能力 ===
    "analyze_screen": {
        "module": "vision_ops",           # 对应 skills/impl/vision_ops.py
        "func": "capture_and_analyze",
        "desc": "截取当前屏幕并分析内容",
        "params": {"prompt": "str", "target": "str (host/edge)"}
    },
    # === 情感表达 ===
    "express_emotion": {
        "module": "emotion_ops",          # 对应 skills/impl/emotion_ops.py
        "func": "change_face",
        "desc": "改变 TCOMAS 水冷屏表情 (happy/angry/sleep/gaming)",
        "params": {"emotion": "str"}
    },
    "set_indicator": {
        "module": "emotion_ops",
        "func": "change_gpu_status",
        "desc": "改变 iGame 显卡屏状态",
        "params": {"mode": "str"}
    }
}