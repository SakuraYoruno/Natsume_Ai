受众：CODEX
标题：Custom Skill Providers

# Custom Skill Providers

把新的宿主技能 provider 放到这个目录后，`host_brain/skills/provider_catalog.py`
会自动扫描 `*_provider.py` 并注册其中暴露的 `SKILL_PROVIDER_CLASS`。

## 最小约定

- 文件名：`<name>_provider.py`
- 模块内必须导出：`SKILL_PROVIDER_CLASS = YourProvider`
- `YourProvider` 推荐继承 `DeclarativeSkillProvider`

## 生效方式

- 开发环境：重启 Host 服务
- 测试环境：重建 `SkillRegistry`
- 关闭自动发现：设置 `HOST_SKILL_CUSTOM_PROVIDERS_ENABLED=false`

## 额外 provider 路径

也可以通过环境变量直接指定外部 provider：

- `HOST_SKILL_EXTRA_PROVIDERS=package.module:ProviderClass`
- 支持用逗号、分号或换行分隔多个 provider

## 推荐流程

1. 复制 `template_provider.py.example`
2. 重命名为 `<name>_provider.py`
3. 修改 skill spec / normalizer / description
4. 重启 Host 或重建 `SkillRegistry`
5. 用 `build_skill_manifest()` 或相关测试确认技能已出现
