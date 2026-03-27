---
name: "example-echo"
version: "1.0.0"
description: "示例 Skill：回显传入的文本。用于验证 Skills Hub 加载机制。"
author: "koinosakura"
tags: ["example", "echo", "test"]
trigger_words: ["echo", "回显", "测试"]

clearance: LOW
risk_level: 1
scope: host
reversible: true
require_confirm: false
scheduler_allowed: true

tool: example_echo
action: run
---

# Example Echo Skill

这是一个示例 Skill，用于验证 Skills Hub 的 SKILL.md 解析和自动注册机制。

## 功能

接收一段文本，原样返回。

## 使用示例

```
用户：echo hello world
响应：hello world
```
