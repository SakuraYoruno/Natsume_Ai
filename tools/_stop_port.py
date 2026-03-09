"""停止占用指定端口的进程"""
import subprocess
import socket
import sys

port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000

print(f"查找占用 {port} 端口的进程...")
r = subprocess.run(["netstat", "-ano"], capture_output=True, encoding="gbk", errors="replace")
pid = None
for line in r.stdout.splitlines():
    if f":{port} " in line and "LISTENING" in line:
        pid = line.strip().split()[-1]
        print(f"找到 PID: {pid}")
        break

if pid:
    r2 = subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True, encoding="gbk")
    print("stdout:", r2.stdout.strip())
    print("returncode:", r2.returncode)
    import time; time.sleep(1)
else:
    print(f"未找到占用 {port} 的进程")

s = socket.socket()
try:
    s.bind(("127.0.0.1", port))
    s.close()
    print(f"[OK] 端口 {port} 已释放")
except OSError as e:
    print(f"[WARN] 端口 {port} 仍被占用: {e}")
