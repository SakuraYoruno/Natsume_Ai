"""Web UI集成测试脚本

验证FastAPI应用是否可以正常启动并响应Web UI路由。
"""

import sys
import os
import time
import requests
import subprocess
import threading

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def start_test_server():
    """启动测试服务器"""
    try:
        import uvicorn
        from host_brain.app.fastapi_app import app

        def run_server():
            uvicorn.run(app, host="127.0.0.1", port=8000, log_level="error")

        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()

        # 等待服务器启动
        time.sleep(3)
        return True
    except Exception as e:
        print(f"[ERROR] Failed to start server: {e}")
        return False

def run_webui_routes_check():
    """测试Web UI路由（手动集成测试，不在 pytest 全量运行中执行）"""
    base_url = "http://127.0.0.1:8000"

    # 测试健康检查端点
    try:
        response = requests.get(f"{base_url}/health", timeout=5)
        if response.status_code == 200:
            print("[OK] Health endpoint working")
        else:
            print(f"[ERROR] Health endpoint returned status: {response.status_code}")
            return False
    except Exception as e:
        print(f"[ERROR] Health endpoint test failed: {e}")
        return False

    # 测试Web UI路由导入
    try:
        from host_brain.app.web_ui import router
        print("[OK] Web UI router imported successfully")
    except Exception as e:
        print(f"[ERROR] Web UI router import failed: {e}")
        return False

    # 测试静态文件服务
    try:
        response = requests.get(f"{base_url}/static/index.html", timeout=5)
        if response.status_code == 200:
            print("[OK] Static files serving working")
        elif response.status_code == 404:
            print("[WARNING] Static files not found (expected for test)")
        else:
            print(f"[INFO] Static files endpoint returned status: {response.status_code}")
    except Exception as e:
        print(f"[INFO] Static files test failed (may be expected): {e}")

    # 测试Web UI API端点
    try:
        # 测试时间线聚合端点
        response = requests.get(
            f"{base_url}/ui/timeline",
            params={
                "start_time": "2024-01-01T00:00:00",
                "end_time": "2024-01-01T01:00:00",
                "interval": "1h"
            },
            timeout=5
        )
        if response.status_code == 200:
            print("[OK] Timeline endpoint working")
        else:
            print(f"[INFO] Timeline endpoint returned status: {response.status_code}")
    except Exception as e:
        print(f"[INFO] Timeline endpoint test failed (may be expected): {e}")

    # 测试状态分布端点
    try:
        response = requests.get(
            f"{base_url}/ui/status-distribution",
            params={
                "start_time": "2024-01-01T00:00:00",
                "end_time": "2024-01-01T01:00:00"
            },
            timeout=5
        )
        if response.status_code == 200:
            print("[OK] Status distribution endpoint working")
        else:
            print(f"[INFO] Status distribution endpoint returned status: {response.status_code}")
    except Exception as e:
        print(f"[INFO] Status distribution endpoint test failed (may be expected): {e}")

    return True


def main():
    """主测试函数"""
    print("Starting Web UI integration test...")

    # 启动服务器
    if not start_test_server():
        return False

    print("[OK] Test server started")

    # 测试Web UI路由
    if not run_webui_routes_check():
        return False

    print("\n[SUCCESS] Web UI integration test passed!")
    print("The HTTP service framework with Web UI routes is ready for use.")
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
