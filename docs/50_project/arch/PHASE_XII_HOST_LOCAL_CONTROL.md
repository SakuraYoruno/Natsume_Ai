受众：CODEX
标题：Phase XII Host 本地操控能力 — 五阶段技术文档（阶段方案留痕）

# Phase XII Host 本地操控能力

当前代码校准说明（2026-04-19）：
- 本文是 2026-03-29 的阶段方案文档，不应直接当作当前运行时事实或实现任务清单。
- 文中六个 Host 工具模块目前均已真实落库：`host_command_executor.py`、`secret_vault.py`、`file_forensics.py`、`browser_automation.py`、`file_organizer.py`、`system_monitor.py`（含 `ProcessGuard`）。
- 当前工具执行面应以实际代码为准：`host_brain/tools/catalog.py` / `broker.py` / `mcp_adapter.py` 负责元数据、单入口执行与 MCP 适配，`host_brain/app/dependencies.py` 暴露 `ToolCatalog` / `ToolBroker`，`host_brain/app/tool_routes.py` 提供 `/tools/meta-pack`、`/tools/execute`、`/tools/health`，技能侧由 `SkillRegistry` 与各类 provider/registry 负责接线。
- 因此，本文列出的能力应理解为“当前仓库已具备的 Host 工具实现方向”；是否对模型默认暴露、以何种方式暴露、是否需审批/确认，取决于运行时注册、allowlist 与 ToolBroker/SkillRegistry 配置，而不是由本文直接定义。
- 若要查看现行契约与实际边界，请优先阅读 `docs/15_theory/TOOLS_MEMORY_LAYER_CONTRACT.md`、`docs/15_theory/APP_AUDIT_SKILLS_CONTRACT.md` 与实际代码。

## 概述

让 Natsume_Ai 从"只会聊天"升级为"能在 Host 电脑上干活"。安全地执行文件操作、浏览器自动化、系统监控等本地任务。

核心原则：**能力要强，安全更强**——分层安全审批 + 白名单命令 + 沙箱目录 + 审计日志。

## 阶段 1：HostCommandExecutor + SecretVault

### HostCommandExecutor（安全命令执行引擎）

**文件**：`host_brain/tools/host_command_executor.py`

4 级安全分层：

| 级别 | 说明 | 审批 | 命令示例 |
|------|------|------|---------|
| L0 | 只读 | 自动通过 | dir, ls, cat, systeminfo, ping |
| L1 | 白名单目录写 | 自动通过 | 7z, copy, git, docker, curl |
| L2 | 中风险 | 需确认 | del, rm, rmdir, schtasks |
| L3 | 高风险 | 双重确认 | shutdown, reg, netsh, diskpart |
| L4 | 禁止 | 永不执行 | format C:, rm -rf /, del /S /Q C:\ |

安全机制：命令白名单 + 目录沙箱 + 禁止模式正则 + 审批回调 + 审计日志。

### SecretVault（加密密钥保险库）

**文件**：`host_brain/tools/secret_vault.py`

- AES-256 加密（PBKDF2 密钥派生，100K 迭代）
- 按名称/分类存取密码
- `get_by_category("archive_password")` → 获取所有压缩包密码
- 文件持久化到 `runtime/secrets/vault.enc.json`

## 阶段 2：FileForensics（智能文件处理）

**文件**：`host_brain/tools/file_forensics.py`

| 能力 | 说明 |
|------|------|
| Magic bytes 检测 | 24 种文件签名，识别被改后缀的文件 |
| 后缀自动修复 | .mp4 实际是 .rar → 自动改名 |
| 批量解压 | 目录扫描（含 magic bytes 识别）→ 逐个解压 |
| 密码列表尝试 | 无密码 → 密码1 → 密码2... 直到成功 |
| 递归解压 | 压缩包里有压缩包 → 递归最深 3 层 |
| 大文件保护 | >4GB 跳过（防压缩炸弹） |

## 阶段 3：BrowserAutomation（浏览器自动化）

**文件**：`host_brain/tools/browser_automation.py`

基于 Playwright，支持 Chromium 无头/有头模式。

| 操作 | 方法 | 说明 |
|------|------|------|
| 导航 | `navigate(url)` | 带域名黑名单检查 |
| 截图 | `screenshot()` | 自动编号，支持全页 |
| 点击 | `click(selector)` | CSS 选择器 |
| 填写 | `fill(selector, value)` | 安全：不填 password/cvv 字段 |
| 提取 | `extract_text(selector)` | 提取页面文本 |
| 脚本执行 | `execute_script(script)` | 多步自动化 + 每步截图留证 |

安全机制：
- 域名黑名单（政府/银行/支付网站）
- 敏感字段不自动填写（password/credit-card/cvv）
- 每步操作自动截图存档
- Cookie 通过 SecretVault 加密管理

## 阶段 4：FileOrganizer（高级文件整理）

**文件**：`host_brain/tools/file_organizer.py`

| 能力 | 说明 |
|------|------|
| 按类型分类 | 9 大类（视频/音频/图片/文档/压缩包/程序/代码/字体/模型），70+ 后缀 |
| 按日期归档 | 按文件修改时间分到 YYYY-MM 目录 |
| 重复文件检测 | MD5 hash 精确比对，按浪费空间排序 |
| 空文件夹清理 | 自底向上遍历删除空目录 |
| dry_run 模式 | 先扫描报告，不实际移动 |

## 阶段 5：SystemMonitor + ProcessGuard

**文件**：`host_brain/tools/system_monitor.py`

### SystemMonitor

| 能力 | 实现 |
|------|------|
| 系统快照 | CPU/内存/磁盘/GPU/网络/运行时间 |
| 进程列表 | 按 CPU 或内存排序，top N |
| GPU 状态 | nvidia-smi 查询 |
| 网络连接 | 列出所有网络连接+对应进程 |
| Ping 测试 | 延迟测量 |
| 终止进程 | 安全终止指定 PID |

### ProcessGuard（进程守护）

- 监控指定进程，崩溃自动重启
- 可配置：重启延迟、最大重启次数、检查间隔
- 守护线程独立运行，不阻塞主线程
- 达到最大重启次数后自动停止

## 文件清单

| # | 文件 | 行数 | 说明 |
|---|------|------|------|
| 1 | `tools/host_command_executor.py` | ~310 | 安全命令执行 |
| 2 | `tools/secret_vault.py` | ~210 | 加密密钥管理 |
| 3 | `tools/file_forensics.py` | ~350 | 智能文件处理 |
| 4 | `tools/browser_automation.py` | ~390 | 浏览器自动化 |
| 5 | `tools/file_organizer.py` | ~280 | 文件整理 |
| 6 | `tools/system_monitor.py` | ~330 | 系统监控+进程守护 |

## 使用场景示例

```
用户: "帮我把 D:\Downloads 里的压缩包全部解压，有些文件后缀被改了，密码是 sakura2026"

Natsume 执行链:
1. FileForensics.scan_and_fix_extensions("D:\\Downloads")
   → 发现 3 个文件后缀错误，已修复
2. SecretVault.store("download_pwd", "sakura2026", category="archive_password")
   → 密码已加密存储
3. FileForensics.batch_extract("D:\\Downloads", passwords=["sakura2026"], recursive=True)
   → 15 个文件全部解压完成，2 个有嵌套压缩包已递归处理
4. FileOrganizer.organize_by_type("D:\\Downloads\\extracted")
   → 解压后的文件按类型分类整理完毕
```
