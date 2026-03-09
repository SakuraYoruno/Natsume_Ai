import sys
sys.path.insert(0, 'D:/nastume/Natsume_Ai')

try:
    from host_brain.app import fastapi_app
    print("导入成功！")
except Exception as e:
    import traceback
    traceback.print_exc()
