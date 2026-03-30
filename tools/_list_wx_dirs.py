import sys, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

base = 'C:/Users/v_wenloliu/Documents/xwechat_files'
dirs = [
    'decrypted',
    'wxid_6ofkq4h94pxe22_e4a7/decrypted',
    'wxid_6ofkq4h94pxe22_e4a7/db_storage',
    'wxid_6ofkq4h94pxe22_e4a7/msg',
]
for d in dirs:
    p = os.path.join(base, d)
    print(f'\n{d}:')
    if not os.path.exists(p):
        print('  (不存在)')
        continue
    for f in sorted(os.listdir(p)):
        fp = os.path.join(p, f)
        if os.path.isfile(fp):
            print(f'  {f} ({os.path.getsize(fp)//1024}KB)')
        else:
            print(f'  [{f}/]')
