# -*- coding: utf-8 -*-
"""修复 test_stream_chat_contract.py 中的乱码/未闭合字符串。"""
with open('tests/contract/test_stream_chat_contract.py', 'rb') as f:
    content = f.read()

# 当前第 127 行字节：b'                        "emotion_range_hint": "\xe7\xa7\xaf\xe6\x9e\x81/\xe9\xab\x98\xe6\xb6\xa8,\n'
# 问题：字符串末尾缺少闭合引号 "
# 修复：将 积极/高涨, 替换为 积极/高涨",
bad_bytes = b'\xe7\xa7\xaf\xe6\x9e\x81/\xe9\xab\x98\xe6\xb6\xa8,'
good_bytes = b'\xe7\xa7\xaf\xe6\x9e\x81/\xe9\xab\x98\xe6\xb6\xa8",'

count = content.count(bad_bytes)
print(f'found {count} occurrences')
new_content = content.replace(bad_bytes, good_bytes)

with open('tests/contract/test_stream_chat_contract.py', 'wb') as f:
    f.write(new_content)
print('done')
