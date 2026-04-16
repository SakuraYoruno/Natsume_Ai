"""精准扫描：找出所有用户可见的硬编码兜底回复文本（非 LLM prompt）。"""
import re
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

TARGET_DIRS = [
    "D:/Natsume_Ai_repo/host_brain/app",
    "D:/Natsume_Ai_repo/host_brain/tribe",
]

# 用户可见的兜底回复特征：
# 1. text = "中文..." 或 text = f"中文..."
# 2. return "中文..."
# 3. "summary": "中文..." 在 artifacts 里
# 4. default="中文..." 在 _mapping_text 调用里
# 5. 最后一道 fallback（if not text: text = "中文..."）

PATTERNS = [
    # text = "中文" 或 text = f"中文"
    (re.compile(r'^\s*text\s*=\s*[f]?["\']([^"\']*[\u4e00-\u9fff][^"\']*)["\']'), "text="),
    # return "中文"
    (re.compile(r'^\s*return\s+[f]?["\']([^"\']*[\u4e00-\u9fff][^"\']*)["\']'), "return"),
    # default="中文" 在函数调用里
    (re.compile(r'default\s*=\s*[f]?["\']([^"\']*[\u4e00-\u9fff][^"\']*)["\']'), "default="),
    # "summary": "中文"
    (re.compile(r'"summary"\s*:\s*[f]?["\']([^"\']*[\u4e00-\u9fff][^"\']*)["\']'), "summary:"),
]

SKIP_PREFIXES = ("#", "logger", "_logger", "logging", "log.", "re.compile", "re.sub", "re.search")
SKIP_KEYWORDS = [
    "raise ", "Exception(", "Error(", ".warning(", ".info(", ".debug(",
    "assert ", "pytest", "re.compile", "re.sub", "re.search", "re.match",
    "description=", "Field(", "description:", "docstring",
]

results = []

for d in TARGET_DIRS:
    if not os.path.exists(d):
        continue
    for root, dirs, files in os.walk(d):
        dirs[:] = [x for x in dirs if "__pycache__" not in x]
        for f in files:
            if not f.endswith(".py") or f.startswith("test_"):
                continue
            path = os.path.join(root, f)
            rel = path.replace("D:/Natsume_Ai_repo/", "")
            with open(path, encoding="utf-8", errors="ignore") as fh:
                for i, line in enumerate(fh, 1):
                    stripped = line.strip()
                    if any(stripped.startswith(p) for p in SKIP_PREFIXES):
                        continue
                    if any(k in stripped for k in SKIP_KEYWORDS):
                        continue
                    for pattern, label in PATTERNS:
                        m = pattern.search(line)
                        if m:
                            text = m.group(m.lastindex or 1)
                            if len(text) >= 4:
                                results.append((rel, i, label, text[:100], stripped[:120]))
                            break

print(f"共找到 {len(results)} 处用户可见的硬编码兜底文本\n")

by_file: dict = {}
for rel, i, label, text, line in results:
    by_file.setdefault(rel, []).append((i, label, text, line))

for rel, items in sorted(by_file.items()):
    print(f"\n=== {rel} ({len(items)} 处) ===")
    for i, label, text, line in items:
        print(f"  [{label}] L{i}: {text!r}")
