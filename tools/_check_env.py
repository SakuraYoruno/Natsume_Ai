import subprocess, os, pathlib

def run(cmd):
    r = subprocess.run(cmd, capture_output=True, shell=True, encoding="gbk", errors="replace")
    return ((r.stdout or "") + (r.stderr or "")).strip()

print("=== 环境检查 ===")
print("java:", run("where java"))
print("docker:", run("where docker"))
print("neo4j:", run("where neo4j"))

# 检查常见安装路径
paths = [
    r"C:\Program Files\Java",
    r"C:\Program Files\Eclipse Adoptium",
    r"C:\Program Files\Microsoft",
    r"C:\Program Files\Neo4j",
    r"C:\Program Files\Neo4j Desktop",
    r"C:\Program Files\Docker",
    r"D:\Java",
    r"D:\Neo4j",
]
for p in paths:
    exists = pathlib.Path(p).exists()
    if exists:
        print(f"  [FOUND] {p}")

# 检查 JAVA_HOME 环境变量
java_home = os.environ.get("JAVA_HOME", "")
print(f"JAVA_HOME env: {java_home!r}")

# 检查 PATH 中的 java
path_env = os.environ.get("PATH", "")
java_in_path = [p for p in path_env.split(os.pathsep) if "java" in p.lower() or "jdk" in p.lower() or "jre" in p.lower()]
print(f"Java in PATH: {java_in_path}")
