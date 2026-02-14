受众：MASTER
标题：给 Codex 的启动指令（复制粘贴用）

你只允许把以下文档当作任务来源：
- docs/30_devspec/**
- docs/40_agents/**

你禁止把以下文档当作任务来源：
- docs/10_whitepaper/**
- docs/20_prd/**
- docs/50_project/**
- docs/60_trace/**

你的工作方式：
1）从 docs/30_devspec/DevTasks.md 选择要做的 TASK（按我指定的编号）
2）按 TASK 的输入/输出/验收执行
3）输出：改动摘要（按文件）+ 验证命令 + 预期结果
4）一次提交只解决一个 TASK；不要做额外重构

重要约束：
- 不要写长讨论
- 不要更改白皮书/PRD/Project 文档
- 不要引入任何绕过 Governor/Token/审计链的旁路执行

现在执行：我在本次消息里指定的 TASK 编号。（若我未指定，先停下来要求我指定任务编号。）
