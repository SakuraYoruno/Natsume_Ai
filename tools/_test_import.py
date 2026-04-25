"""测试 ASGI app 导入和基本路由"""
import sys
sys.path.insert(0, ".")

print("=== 测试 asgi app 导入 ===")
try:
    from host_brain.app.asgi import app
    print(f"[OK] app 导入成功: {app}")
    print(f"[OK] 路由数量: {len(app.routes)}")
    for r in app.routes:
        if hasattr(r, 'path'):
            print(f"  路由: {r.path}")
except Exception as e:
    import traceback
    print(f"[FAIL] 导入失败: {e}")
    traceback.print_exc()

print()
print("=== 测试 health_routes 导入 ===")
try:
    from host_brain.app import health_routes
    print(f"[OK] health_routes 导入成功")
except Exception as e:
    import traceback
    print(f"[FAIL] health_routes 导入失败: {e}")
    traceback.print_exc()

print()
print("=== 测试 dependencies 导入 ===")
try:
    from host_brain.app import dependencies
    print(f"[OK] dependencies 导入成功")
except Exception as e:
    import traceback
    print(f"[FAIL] dependencies 导入失败: {e}")
    traceback.print_exc()
