#!/bin/bash
# 检查指定文档文件的乱码情况

echo "检查您提到的三个文档文件乱码情况..."
echo "============================================="

files=(
  "docs/15_theory/arch_analysis_report_20260311.md"
  "docs/15_theory/architecture_overview_20260310.md"
  "docs/15_theory/LANGGRAPH_CONSTRAINT_CONFLICT_ANALYSIS_2026-03-06.md"
)

for file in "${files[@]}"; do
  echo "\n检查文件: $file"
  
  if [ ! -f "$file" ]; then
    echo "❌ 文件不存在"
    continue
  fi
  
  # 检查文件基本信息
  echo "- 文件信息:"
  ls -lh "$file"
  file -i "$file"
  
  # 检查编码问题
  echo "- 乱码检查:"
  
  # 检查UTF-8替换字符
  if grep -q $'\xEF\xBF\xBD' "$file"; then
    echo "  ⚠ 发现UTF-8替换字符 (���)"
    echo "    位置:"
    grep -n $'\xEF\xBF\xBD' "$file" | head -3
  else
    echo "  ✓ 未发现UTF-8替换字符"
  fi
  
  # 检查奇怪的问号组合
  echo "- 可疑字符检查:"
  
  # 检查???连续问号
  if grep -q '???' "$file"; then
    echo "  ⚠ 发现连续问号:"
    grep -n '???' "$file" | head -3
  else
    echo "  ✓ 未发现连续问号"
  fi
  
  # 检查类似乱码的字符组合
  if grep -q '[锘�沤���]' "$file"; then
    echo "  ⚠ 发现疑似乱码字符:"
    grep -n '[锘�沤���]' "$file" | head -3
  else
    echo "  ✓ 未发现常见乱码字符"
  fi
  
  # 统计非ASCII字符
  echo "- 非ASCII字符统计:"
  non_ascii_count=$(grep -o '[^\x00-\x7F]' "$file" | wc -l)
  total_chars=$(wc -c < "$file")
  if [ "$total_chars" -gt 0 ]; then
    ratio=$(echo "scale=2; $non_ascii_count * 100 / $total_chars" | bc 2>/dev/null || echo "N/A")
    echo "  总字符数: $total_chars"
    echo "  非ASCII字符数: $non_ascii_count"
    if [ "$ratio" != "N/A" ]; then
      echo "  非ASCII占比: ${ratio}%"
    fi
  fi
  
  echo "-------------------------------------"
done

echo "\n检查完成!"
