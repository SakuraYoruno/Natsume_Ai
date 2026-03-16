#!/bin/bash
# 检查Git最近更改文件是否有乱码

files=(
  "README.md"
  "docs/20_prd/PRD.md"
  "docs/30_devspec/DevSpec.md"
  "docs/30_devspec/DevTasks.md"
  "docs/30_devspec/ModelPlane.md"
  "docs/60_trace/code_reviews/full_code_review_report_2026-03-02.md"
  "docs/60_trace/code_reviews/full_code_review_report_2026-03-03-v5.md"
  "docs/60_trace/code_reviews/full_code_review_report_2026-03-03.md"
  "docs/60_trace/requirements_trace.md"
  "docs/60_trace/risk_registry.md"
)

echo "检查最近更改文件的乱码情况..."
echo "====================================="

for file in "${files[@]}"; do
  if [ ! -f "$file" ]; then
    echo "警告: 文件 '$file' 不存在"
    continue
  fi
  
  echo "\n检查文件: $file"
  
  # 检查文件编码
  echo "- 编码检查:"
  file -i "$file"
  
  # 检查常见乱码字符
  echo "- 乱码字符检查:"
  
  # 检查替换字符 (U+FFFD)
  if grep -q $'\xEF\xBF\xBD' "$file"; then
    echo "  ⚠ 发现UTF-8替换字符 (���)"
  else
    echo "  ✓ 未发现UTF-8替换字符"
  fi
  
  # 检查连续的问号
  if grep -q '\?\?\?' "$file"; then
    echo "  ⚠ 发现连续问号 (可能表示乱码)"
  else
    echo "  ✓ 未发现连续问号"
  fi
  
  # 检查非ASCII字符
  echo "- 非ASCII字符统计:"
  non_ascii_count=$(grep -o '[^\x00-\x7F]' "$file" | wc -l)
  if [ "$non_ascii_count" -gt 0 ]; then
    echo "  ✓ 发现 $non_ascii_count 个非ASCII字符 (正常中文)"
    # 显示一些样本
    echo "    样本:"
    grep -o '[^\x00-\x7F]' "$file" | head -5 | tr '\n' ' '
    echo
  else
    echo "  ℹ 未发现非ASCII字符"
  fi
  
  echo "-------------------------------------"
done

echo "\n检查完成!"
