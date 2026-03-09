filepath = 'D:/nastume/Natsume_Ai/host_brain/app/fastapi_app.py'

with open(filepath, 'r', encoding='utf-8') as f:
    lines = f.readlines()

print('原始总行数:', len(lines))

# 保留第 1~312 行（索引 0~311）+ 第 3070~3094 行（索引 3069~3093）
clean_lines = lines[0:312] + lines[3069:3094]

print('清理后行数:', len(clean_lines))

# 写回文件
with open(filepath, 'w', encoding='utf-8') as f:
    f.writelines(clean_lines)

print('完成！文件已清理。')
