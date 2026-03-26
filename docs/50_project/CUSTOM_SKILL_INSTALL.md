# Natsume 自定义技能安装指南

## 目标

在不改 `dependencies.py`、不改注册主流程的前提下，为 Natsume 新增宿主技能。

本仓库现已支持两种扩展方式：

- **内置 provider**：直接放在 `host_brain/skills/`，适合核心能力
- **自定义 provider**：放在 `host_brain/skills/custom_providers/`，适合快速试验、后装技能

## 推荐方式：放到 `custom_providers`

### 目录约定

- 目录：`host_brain/skills/custom_providers/`
- 文件名：`<name>_provider.py`
- 模块导出：`SKILL_PROVIDER_CLASS = YourProvider`

### 最小步骤

1. 复制 `host_brain/skills/custom_providers/template_provider.py.example`
2. 改名为 `host_brain/skills/custom_providers/<name>_provider.py`
3. 实现：
   - 一个或多个 `normalize_args()`
   - 一个 `DeclarativeSkillProvider`
   - 若干 `DeclarativeSkillSpec`
4. 重启 Host 服务，或在测试里重建 `SkillRegistry`
5. 用 `get_skill_registry().build_skill_manifest()` 确认技能已出现

## 自定义 provider 模板

推荐结构：

```python
from host_brain.skills.declarative import DeclarativeSkillProvider, DeclarativeSkillSpec

def _normalize_xxx(args, ctx):
    return {...}

class MySkillProvider(DeclarativeSkillProvider):
    skill_specs = (
        DeclarativeSkillSpec(
            skill_name="my_skill",
            clearance="LOW",
            risk_level=1,
            scope="host",
            reversible=True,
            description="...",
            tool="my_tool",
            action="run",
            normalize_args=_normalize_xxx,
        ),
    )

SKILL_PROVIDER_CLASS = MySkillProvider
```

## 另一种方式：外部包/模块

如果 provider 不想放进仓库，也可以用环境变量：

- `HOST_SKILL_EXTRA_PROVIDERS=package.module:ProviderClass`

多个 provider 支持用以下任一种分隔：

- `,`
- `;`
- 换行

## 让 Natsume 自己“安装”新技能

当前最稳妥的方式是：

1. 用现有 `file_write` 技能，把 provider 文件写入 `host_brain/skills/custom_providers/`
2. 用现有 `shell_run` 技能，重启 Host 进程或触发部署脚本

这样 Natsume 就能通过“写文件 + 重启服务”的方式自助安装新技能。

## 何时需要改核心目录

只有在以下场景才建议放入 `host_brain/skills/` 主目录：

- 需要长期维护的核心能力
- 需要和 executor/治理策略深度耦合
- 需要作为默认内置能力随发行版启用

## 验证清单

- provider 可导入
- `SKILL_PROVIDER_CLASS` 存在
- `skill_name` 唯一
- `risk_level >= 4` 时设置 `require_confirm=True`
- skill manifest 中能看到新技能
- 相关单测通过
