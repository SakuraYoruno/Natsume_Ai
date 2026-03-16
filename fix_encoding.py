#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
修复文档文件编码乱码问题
常见问题：UTF-8编码被错误地解释为GBK编码
"""

import os
import sys

def fix_encoding_file(filepath):
    """修复单个文件的编码问题"""
    if not os.path.exists(filepath):
        print(f"错误：文件不存在 {filepath}")
        return False
    
    print(f"正在修复文件: {filepath}")
    
    try:
        # 尝试以GBK读取（模拟错误的读取方式）
        with open(filepath, 'rb') as f:
            raw_bytes = f.read()
        
        # 尝试将GBK编码的字节串解码为正确的中文
        # 常见乱码模式：
        # UTF-8 -> GBK误读 -> 显示乱码 -> 保存为UTF-8 -> 双重乱码
        # 我们需要反向操作：将乱码UTF-8先解码为GBK字符串，再编码为正确UTF-8
        
        # 方案1：直接尝试修复最常见的乱码模式
        try:
            # 首先尝试将当前内容作为GBK解码
            gbk_str = raw_bytes.decode('gbk', errors='ignore')
            # 再将GBK字符串编码为UTF-8
            fixed_content = gbk_str.encode('utf-8')
            
            # 检查修复效果
            test_str = fixed_content.decode('utf-8', errors='ignore')
            
            # 判断是否修复成功：检查常见乱码字符是否消失
            common_mojibake = ['瀹屾暣', '缂栨帓', '鑳藉姏', '鏂囨。', '鍒嗘瀽']
            found = any(moji in test_str for moji in common_mojibake)
            
            if not found:
                print(f"  修复成功！乱码字符已消失")
                # 保存修复后的文件
                with open(filepath, 'wb') as f:
                    f.write(fixed_content)
                return True
            else:
                print(f"  修复方法1无效，尝试方案2")
        except Exception as e:
            print(f"  方案1失败: {e}")
        
        # 方案2：手动替换常见乱码字符
        try:
            # 读取原始内容
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 常见乱码字符映射（基于观察的样本）
            mojibake_map = {
                '瀹屾暣': '完整',
                '缂栨帓': '编排',
                '鑳藉姏': '能力',
                '鏂囨。': '文档',
                '鍒嗘瀽': '分析',
                '鏃ユ湡': '日期',
                '瑙勬牸': '规则',
                '渚濇嵁': '依据',
                '瀵硅薄': '对象',
                '缁撹': '结论',
                '鎽樿': '摘要',
                '椤归珮': '高级',
                '绾х紪': '级别',
                '鎺掕兘': '安排能',
                '瀛樺湪': '存在',
                '缁撴瀯': '结构',
                '鎬у啿': '性冲',
                '绐侊紝': '突，',
                '褰撳墠': '当前',
                '鏋舵瀯': '架构',
                '涓烘湁': '为有',
                '鎰忛檷': '意降',
                '绾т娇': '级使',
                '鐢紝': '用，',
                '灞炰簬': '属于',
                '娌荤悊': '治理',
                '浼樺厛': '优先',
                '浜庢': '于框',
                '鏋惰兘': '架能',
                '鍔涚殑': '力的',
                '涓诲姩': '主动',
                '鍙栬垗': '取舍',
            }
            
            # 执行替换
            fixed_content = content
            for moji, correct in mojibake_map.items():
                fixed_content = fixed_content.replace(moji, correct)
            
            # 保存修复后的文件
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(fixed_content)
            
            print(f"  使用字符映射修复完成")
            return True
            
        except Exception as e:
            print(f"  方案2失败: {e}")
            return False
            
    except Exception as e:
        print(f"  读取文件失败: {e}")
        return False

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("用法: python fix_encoding.py <文件路径>")
        sys.exit(1)
    
    filepath = sys.argv[1]
    success = fix_encoding_file(filepath)
    
    if success:
        print(f"\n文件修复完成: {filepath}")
        # 显示修复后的前几行
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                print("\n修复后内容预览（前10行）:")
                print("-" * 50)
                for i, line in enumerate(lines[:10]):
                    if i >= 10:
                        break
                    print(f"{i+1:3d}: {line.rstrip()}")
                print("-" * 50)
        except Exception as e:
            print(f"无法预览修复后内容: {e}")
    else:
        print(f"\n修复失败，请尝试其他方法")
