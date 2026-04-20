import subprocess, json, os, time

XHS = r"c:\Users\kelvinyye\tools\xiaohongshu-mcp\xiaohongshu-mcp-windows-amd64.exe"
COOKIES = r"c:\Users\kelvinyye\tools\xiaohongshu-mcp\cookies.json"

env = {**os.environ, "COOKIES_FILE": COOKIES}
proc = subprocess.Popen([XHS], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)

# Send init
init_req = json.dumps({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"1.0"}}}) + "\n"
proc.stdin.write(init_req.encode('utf-8'))
proc.stdin.flush()
time.sleep(1)

# Try to read stdout
import io
stdout_bytes = b""
try:
    # Non-blocking read
    import msvcrt
    os.set_blocking(proc.stdout.fileno(), False)
    while True:
        chunk = proc.stdout.read(4096)
        if not chunk:
            break
        stdout_bytes += chunk
except:
    pass

# Send notification + search
notif = json.dumps({"jsonrpc":"2.0","method":"notifications/initialized"}) + "\n"
search = json.dumps({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"search","arguments":{"keyword":"银行消费达标活动","page":1,"sort":"general","noteType":0}}}) + "\n"

proc.stdin.write(notif.encode('utf-8'))
proc.stdin.flush()
time.sleep(0.3)
proc.stdin.write(search.encode('utf-8'))
proc.stdin.flush()

# Wait for response
time.sleep(10)

# Kill and collect all output
proc.kill()
out, err = proc.communicate(timeout=5)
stdout_bytes += out

print("=== STDOUT ===")
s = stdout_bytes.decode('utf-8', errors='replace')
print(s[:3000] if s else "(empty)")
print(f"\n=== STDERR ===")
e = err.decode('utf-8', errors='replace')
print(e[:3000] if e else "(empty)")
print(f"\nReturn code: {proc.returncode}")
