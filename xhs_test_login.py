import subprocess, json, os, time

XHS = r"c:\Users\kelvinyye\tools\xiaohongshu-mcp\xiaohongshu-mcp-windows-amd64.exe"
COOKIES = r"c:\Users\kelvinyye\tools\xiaohongshu-mcp\cookies.json"

env = {**os.environ, "COOKIES_FILE": COOKIES}
proc = subprocess.Popen([XHS], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)

# Send init
init = json.dumps({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"1.0"}}}) + "\n"
proc.stdin.write(init.encode()); proc.stdin.flush()

time.sleep(2)

# Read all available stdout/stderr
import select
stdout_data = b""
stderr_data = b""
try:
    stdout_data = proc.stdout.read1(65536) if hasattr(proc.stdout, 'read1') else b""
except: pass

proc.stdin.close()
time.sleep(1)
proc.kill()
out, err = proc.communicate(timeout=5)
stdout_data += out
stderr_data += err

print("=== STDOUT ===")
print(stdout_data.decode('utf-8', errors='replace')[:2000])
print("\n=== STDERR ===")
print(stderr_data.decode('utf-8', errors='replace')[:2000])
print(f"\nReturn code: {proc.returncode}")
