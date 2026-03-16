# F-06 长稳 Soak 采集说明

## 产物

- `F-06_soak_samples_YYYYMMDD.json`：当日采样序列（ts_utc, pid, rss_mb, health）
- `F-06_soak_samples_YYYYMMDD.md`：同日前 100 条表格摘要

## 启动持续采集

```powershell
cd D:\Natsume_Ai
Start-Process python -ArgumentList "tools/diag_f06_soak_collect.py","--interval","30" -WindowStyle Hidden
```

- 每 **30 分钟** 采样一次 Host 进程 RSS（MB）与 `GET /health` 状态。
- 进程在后台运行，关闭终端不影响；重启或关机后需重新执行上述命令。

## 单次采样（不循环）

```bash
python tools/diag_f06_soak_collect.py --once
```

## 停止后台采集

在任务管理器中结束对应的 `python` 进程（命令行包含 `diag_f06_soak_collect.py`），或：

```powershell
Get-Process python | Where-Object { $_.CommandLine -like "*diag_f06_soak_collect*" } | Stop-Process
```

## 通过标准（封测计划 5.1）

- 连续运行不少于 **6h**，崩溃数 **0**，RSS 无单调失控增长。
