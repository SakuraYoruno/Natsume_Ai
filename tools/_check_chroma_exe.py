import sys, pathlib, shutil, subprocess

print("Python executable:", sys.executable)
scripts_dir = pathlib.Path(sys.executable).parent
print("Scripts dir:", scripts_dir)

chroma_in_scripts = scripts_dir / "chroma.exe"
print("chroma.exe in Scripts:", chroma_in_scripts.exists(), chroma_in_scripts)

if chroma_in_scripts.exists():
    r = subprocess.run([str(chroma_in_scripts), "--version"], capture_output=True, text=True)
    print("version:", r.stdout.strip(), r.stderr.strip())

chroma_in_path = shutil.which("chroma")
print("chroma in PATH:", chroma_in_path)
if chroma_in_path:
    r2 = subprocess.run([chroma_in_path, "--version"], capture_output=True, text=True)
    print("PATH version:", r2.stdout.strip(), r2.stderr.strip())
