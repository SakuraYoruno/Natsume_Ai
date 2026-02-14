import psutil

from skills.registry import register_skill

@register_skill("进程管理")
def manage_process(action, process_name, target="host"):
    """进程管理实现"""
    print(f"⚙️ [System] 进程操作: {action} {process_name}")

    if action == "find":
        found = []
        for p in psutil.process_iter(['pid', 'name']):
            if process_name.lower() in p.info['name'].lower():
                found.append(f"{p.info['name']}({p.info['pid']})")
        return f"✅ 发现进程: {', '.join(found)}" if found else "未找到进程"

    elif action == "kill":
        count = 0
        for p in psutil.process_iter(['name']):
            if process_name.lower() in p.info['name'].lower():
                try:
                    p.kill()
                    count += 1
                except:
                    pass
        return f"🔪 已终止 {count} 个进程"

    return "未知操作"


@register_skill("电源管理")
def power_action(action, target="host"):
    """电源管理实现"""
    print(f"⚡ [System] 电源操作: {action}")
    cmd = "shutdown /s /t 5" if action == "shutdown" else "shutdown /r /t 5"
    # os.system(cmd) # 为了防止测试时误关机，先注释掉，确认无误后打开
    return f"⚡ 正在执行 {action} (测试模式: 命令已生成但未执行)"