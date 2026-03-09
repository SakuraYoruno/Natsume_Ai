"""停止旧的 host brain 进程并重启"""
import subprocess
import time
import sys
import socket

# 找到占用 18080 端口的 PID
print("查找占用 18080 端口的进程...")
r = subprocess.run(["netstat", "-ano"], capture_output=True, encoding="gbk")
pid = None
for line in r.stdout.splitlines():
    if ":18080 " in line and "LISTENING" in line:
        pid = line.strip().split()[-1]
        print(f"找到 PID: {pid}")
        break

if pid:
    print(f"停止 PID={pid} ...")
    r2 = subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True, encoding="gbk")
    print("stdout:", r2.stdout.strip())
    print("stderr:", r2.stderr.strip())
    print("returncode:", r2.returncode)
    time.sleep(1)
else:
    print("未找到占用 18080 的进程")

# 验证端口已释放
s = socket.socket()
try:
    s.bind(("127.0.0.1", 18080))
    s.close()
    print("[OK] 端口 18080 已释放")
except OSError as e:
    print(f"[WARN] 端口 18080 仍被占用: {e}")
