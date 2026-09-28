# -*- coding: utf-8 -*-
"""MCP 多实例并发池（2026-09-28）。

背景（实测数据）：
  - get_feed_detail 单篇 ~98s（6 次采样中位），search_feeds 单组 60~90s（偶发 240s 超时）
  - 图片下载仅 0.14s/张 —— 卡点 100% 在 MCP 用 rod 浏览器渲染页面，不在网络
  - 所以提速只能靠"同时开多个浏览器"

原理：
  - MCP 二进制支持 `-port`（默认 :18060），每个实例独立拉起一个 rod 浏览器
  - 登录态来自 MCP 目录下的 cookies.json，只在扫码时写入、正常请求只读
    → 多实例共享同一份 cookie，无需重复扫码

用法：
    from mcp_pool import MCPPool
    pool = MCPPool(workers=2)
    pool.start()
    results = pool.map(tasks, lambda w, t: do_something(w, t))
    pool.close()

    # 单实例场景（如登录检查）也可以用 pool.primary.call(...)
"""

import collections
import json
import os
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# MCP 二进制所在目录（cookies.json 也在这里）
DEFAULT_MCP_DIR = r"D:\AI agent\xiaohongshu-mcp-windows-amd64"
MCP_DIR = os.environ.get("XHS_MCP_DIR") or DEFAULT_MCP_DIR

# 浏览器二进制：必须显式指定独立 Chromium。
# 2026-09-28 教训：不传 -bin 时 rod 会 auto-detect（可能挑到系统 Chrome 149，
# 版本太新渲染异常），实测表现为前 1~2 次调用正常、之后全部卡满 4 分钟返回
# HTTP 204 —— 曾误判为「并发触发风控」，实为浏览器二进制不对。
CHROMIUM = (os.environ.get("XHS_CHROMIUM")
            or r"D:\AI agent\chromium\chrome-win\chrome.exe")

# 优先读 config.local.json 的 mcp_exe（与 update_report.py 保持一致）
def _load_exe():
    try:
        cfg = json.loads(
            open(os.path.join(BASE_DIR, "config.local.json"), encoding="utf-8").read())
        exe = cfg.get("mcp_exe")
        if exe and os.path.exists(exe):
            return exe
    except Exception:
        pass
    return os.path.join(MCP_DIR, "xiaohongshu-mcp-windows-amd64.exe")


MCP_EXE = os.environ.get("XHS_MCP_EXE") or _load_exe()


def _pick_cookie_dir():
    """选 cookies.json 最新的目录作为工作目录。

    本机有两份 cookies.json（C:\\Users\\...\\tools\\xiaohongshu-mcp 与
    D:\\AI agent\\xiaohongshu-mcp-windows-amd64），取 mtime 最新的那份，
    否则会拉起一个「没登录」的实例，搜索全空。
    """
    cands = [os.path.dirname(MCP_EXE), DEFAULT_MCP_DIR, os.getcwd()]
    best, best_m = None, -1
    for d in cands:
        p = os.path.join(d, "cookies.json")
        if d and os.path.exists(p):
            m = os.path.getmtime(p)
            if m > best_m:
                best, best_m = d, m
    return best or os.path.dirname(MCP_EXE) or DEFAULT_MCP_DIR


MCP_DIR = os.environ.get("XHS_MCP_DIR") or _pick_cookie_dir()
MCP_HOST = os.environ.get("XHS_MCP_HOST", "localhost")
BASE_PORT = int(os.environ.get("XHS_MCP_PORT", "18060"))

# 默认并发度：2（保守起步）。验证稳定后可提到 3
DEFAULT_WORKERS = int(os.environ.get("XHS_MCP_WORKERS", "2"))

# 单次调用超时（秒）。修正 -bin 后实测搜索 20~35s、详情 40s 上下，
# 卡死的调用会一直挂到 MCP 内部 4 分钟上限再返回 204 —— 早点失败早点换实例重试。
CALL_TIMEOUT = int(os.environ.get("XHS_MCP_CALL_TIMEOUT", "180"))

# 实例回收阈值：一个 MCP 实例（rod 浏览器）跑满 N 次调用或空闲超过 M 秒后主动重启。
# 2026-09-28 实测：同一实例第 2 次搜索（尤其前面隔了 30s 冷却）经常卡满 4 分钟
# 返回 HTTP 204；重启实例只要 4~5 秒，比干等便宜得多。
RECYCLE_AFTER_CALLS = int(os.environ.get("XHS_MCP_RECYCLE_CALLS", "2"))
RECYCLE_AFTER_IDLE = int(os.environ.get("XHS_MCP_RECYCLE_IDLE", "180"))

_print_lock = threading.Lock()


def log(msg):
    with _print_lock:
        print(msg, flush=True)


def check_port(host="localhost", port=18060, timeout=1.0):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()


def _snapshot_children():
    """返回 {pid: [child_pid, ...]} 的进程树快照（ctypes Toolhelp，无需 psutil）。"""
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    snap = k32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    if snap == -1:
        return {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        kids = {}
        if k32.Process32FirstW(snap, ctypes.byref(entry)):
            while True:
                kids.setdefault(int(entry.th32ParentProcessID), []).append(
                    int(entry.th32ProcessID))
                if not k32.Process32NextW(snap, ctypes.byref(entry)):
                    break
        return kids
    finally:
        k32.CloseHandle(snap)


def kill_tree(pid, extra_children=None):
    """杀掉 pid 及其所有子孙进程（MCP 拉起的 Chromium 不会被漏掉）。"""
    try:
        kids = _snapshot_children()
        # 合并启动时记录的额外子进程（快照可能抓不到已退出的中间层）
        for parent, children in (extra_children or {}).items():
            kids.setdefault(parent, []).extend(children)
        stack, seen = [int(pid)], set()
        while stack:
            p = stack.pop()
            if p in seen or p <= 4:
                continue
            seen.add(p)
            for c in kids.get(p, []):
                stack.append(c)
        for p in sorted(seen, reverse=True):
            subprocess.run(["taskkill", "/F", "/PID", str(p)],
                           capture_output=True, timeout=10)
    except Exception as e:
        log(f"   ⚠️ kill_tree({pid}) 异常: {type(e).__name__}: {e}")


def spawn_instance(port, idx, headless=True):
    """拉起一个 MCP 实例（返回 proc、日志文件句柄）。不等待会话初始化。"""
    if not os.path.exists(MCP_EXE):
        raise FileNotFoundError(f"MCP 二进制不存在: {MCP_EXE}")
    logfile = os.path.join(BASE_DIR, f"mcp_w{idx}.log")
    fh = open(logfile, "a", encoding="utf-8", errors="replace")
    args = [MCP_EXE, "-port", f":{port}"]
    if headless:
        args.append("-headless=true")
    if CHROMIUM and os.path.exists(CHROMIUM):
        args += ["-bin", CHROMIUM]     # 必传，见顶部 CHROMIUM 注释
    else:
        log(f"   ⚠️ 未找到独立 Chromium: {CHROMIUM}（将走 rod 自动探测）")
    creation = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    proc = subprocess.Popen(args, cwd=MCP_DIR, stdout=fh, stderr=fh,
                            creationflags=creation)
    for _ in range(40):
        time.sleep(0.5)
        if check_port(MCP_HOST, port, timeout=1.0):
            return proc, fh
    try:
        fh.close()
    except Exception:
        pass
    kill_tree(proc.pid)
    raise RuntimeError(f"实例 {idx} 端口 {port} 未能在 20s 内就绪")


class MCPWorker:
    """单个 MCP 实例（占用一个端口 + 一个 rod 浏览器）。"""

    def __init__(self, idx, port, external=False, proc=None, logfile=None):
        self.idx = idx
        self.port = port
        self.url = f"http://{MCP_HOST}:{port}/mcp"
        self.external = external          # True = 复用已存在的实例，close() 不杀
        self.proc = proc
        self.logfile = logfile
        self.headers = None
        self.session = None
        self.n_calls = 0
        self.errors = 0
        self.timeouts = 0
        self.restarts = 0
        self.last_call_end = time.time()
        self.headless = True

    # ---------- 生命周期 ----------
    def init_session(self, retries=3):
        """建 MCP 会话，拿到 Mcp-Session-Id。失败抛异常。"""
        import requests
        self.session = requests.Session()
        last = None
        for _ in range(retries):
            try:
                h = {"Content-Type": "application/json",
                     "Accept": "application/json, text/event-stream"}
                r = self.session.post(self.url, json={
                    "jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                               "clientInfo": {"name": f"mcp_pool_w{self.idx}",
                                              "version": "1.0"}}
                }, headers=h, timeout=20)
                sid = r.headers.get("Mcp-Session-Id")
                if sid:
                    h["Mcp-Session-Id"] = sid
                self.session.post(self.url, json={
                    "jsonrpc": "2.0", "method": "notifications/initialized"
                }, headers=h, timeout=10)
                self.headers = h
                return True
            except Exception as e:
                last = e
                time.sleep(2)
        raise RuntimeError(f"worker{self.idx} 会话初始化失败: {last}")

    def alive(self):
        if self.proc is not None and self.proc.poll() is not None:
            return False
        return check_port(MCP_HOST, self.port, timeout=1.0)

    # ---------- 回收 / 重启 ----------
    def needs_recycle(self):
        """是否该主动重启这个实例（跑满调用数 / 空闲过久）。"""
        if self.external:
            return False
        if self.n_calls >= RECYCLE_AFTER_CALLS:
            return True
        if time.time() - self.last_call_end > RECYCLE_AFTER_IDLE:
            return True
        return False

    def restart(self):
        """原地重启（杀掉旧进程 + 同端口重新拉起 + 重新建会话）。

        对象身份不变，所以并发分发的线程持有的引用依然有效。
        """
        if self.external:
            return False       # 别人拉起的实例无权杀
        self.close()
        try:
            proc, fh = spawn_instance(self.port, self.idx, headless=self.headless)
        except Exception as e:
            log(f"   ❌ worker{self.idx} 重启失败: {e}")
            return False
        self.proc = proc
        self.logfile = fh
        self.session = None
        self.headers = None
        self.n_calls = 0
        self.timeouts = 0
        self.restarts += 1
        self.last_call_end = time.time()
        try:
            self.init_session()
        except Exception as e:
            log(f"   ❌ worker{self.idx} 重启后会话初始化失败: {e}")
            return False
        if self.restarts <= 6:
            log(f"   ♻️  worker{self.idx} 已重启（第 {self.restarts} 次）")
        return True

    # ---------- 调用 ----------
    def call(self, tool_name, arguments=None, timeout=None, request_id=2):
        """调用 MCP 工具，返回解析后的 JSON（不抛异常时一定有值）。"""
        if self.session is None:
            self.init_session()
        timeout = timeout or CALL_TIMEOUT
        try:
            resp = self.session.post(self.url, json={
                "jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments or {}}
            }, headers=self.headers, timeout=timeout)
        except Exception:
            # 失败的调用也要计入 n_calls：卡死的那次同样"用掉"了这个实例，
            # 否则 needs_recycle() 永远触发不了，下一个任务会复用一个已僵死的浏览器。
            self.timeouts += 1
            self.n_calls += 1
            self.last_call_end = time.time()
            raise
        self.n_calls += 1
        self.last_call_end = time.time()
        return resp.json()

    def call_text(self, tool_name, arguments=None, timeout=None):
        """调用工具并返回第一个 text 内容（没有则返回空串）。"""
        data = self.call(tool_name, arguments, timeout=timeout)
        for item in (data.get("result", {}) or {}).get("content", []) or []:
            if item.get("type") == "text":
                return item.get("text", "")
        return ""

    def close(self):
        if self.external or self.proc is None:
            return
        try:
            kill_tree(self.proc.pid)
            try:
                self.proc.terminate()
                self.proc.wait(timeout=8)
            except Exception:
                pass
        except Exception as e:
            log(f"   ⚠️ worker{self.idx} 关闭异常: {e}")


class MCPPool:
    """MCP 多实例池：N 个端口各跑一个实例，任务按 worker 分片并行。"""

    def __init__(self, workers=None, base_port=None, headless=True, verbose=True):
        self.n = int(workers or DEFAULT_WORKERS)
        if self.n < 1:
            self.n = 1
        self.base_port = int(base_port or BASE_PORT)
        self.headless = headless
        self.verbose = verbose
        self.workers = []
        self._owned = []

    # ---------- 启动 ----------
    def _spawn(self, port, idx):
        proc, fh = spawn_instance(port, idx, headless=self.headless)
        w = MCPWorker(idx, port, external=False, proc=proc, logfile=fh)
        w.headless = self.headless
        return w, fh

    def start(self):
        t0 = time.time()
        handles = []
        for i in range(self.n):
            port = self.base_port + i
            if check_port(MCP_HOST, port, timeout=1.0):
                w = MCPWorker(i, port, external=True)
                if self.verbose:
                    log(f"   🔗 worker{i}: 复用已运行实例 :{port}")
            else:
                try:
                    w, fh = self._spawn(port, i)
                    handles.append(fh)
                    if self.verbose:
                        log(f"   🚀 worker{i}: 已拉起 :{port}")
                except Exception as e:
                    log(f"   ❌ worker{i}: 拉起失败 {e}")
                    continue
            try:
                w.init_session()
            except Exception as e:
                log(f"   ❌ worker{i}: {e}")
                w.close()
                continue
            self.workers.append(w)
            if not w.external:
                self._owned.append(w)
        self._log_handles = handles
        ok_n = len(self.workers)
        if ok_n == 0:
            raise RuntimeError("没有任何 MCP 实例可用")
        if self.verbose:
            log(f"   ✅ MCP 池就绪: {ok_n} 实例 ({time.time()-t0:.1f}s)"
                f" 端口 {', '.join(str(w.port) for w in self.workers)}")
        return ok_n

    @property
    def primary(self):
        return self.workers[0]

    # ---------- 任务分发 ----------
    def map(self, tasks, fn, desc="task"):
        """把 tasks 交给 worker 池并行执行（动态抢占式队列）。

        fn(worker, task) -> 任意返回值；抛异常会被捕获并记录为 None。

        2026-09-28 实测教训：静态轮询分片会被慢 worker 拖垮——某实例偶发
        ReadTimeout 卡满 240s，另一实例早已空闲，总耗时反而接近串行。
        改为共享队列 + 谁空闲谁取下一个任务（work stealing），慢实例只拖自己。
        单个 worker 内部仍严格串行（一个 rod 浏览器不能同时渲染两个页面）。
        """
        tasks = list(tasks)
        if not self.workers:
            raise RuntimeError("MCP 池未启动")
        results = [None] * len(tasks)

        def run_one(w, t):
            """取任务前的回收检查 + 执行 + 失败后按需重启。"""
            if w.needs_recycle():
                w.restart()
            try:
                return fn(w, t)
            except Exception as e:
                w.errors += 1
                log(f"   ❌ [{desc}] worker{w.idx} 异常: "
                    f"{type(e).__name__}: {str(e)[:120]}")
                # 卡死（读超时）多半是浏览器僵了 —— 换一个实例比干等划算
                if "Timeout" in type(e).__name__ or "timeout" in str(e).lower():
                    w.restart()
                return None

        if len(self.workers) == 1:
            w = self.workers[0]
            for order, t in enumerate(tasks):
                results[order] = run_one(w, t)
            return results

        queue = collections.deque(enumerate(tasks))
        qlock = threading.Lock()

        def run_bucket(w):
            while True:
                with qlock:
                    if not queue:
                        return
                    order, t = queue.popleft()
                results[order] = run_one(w, t)

        with ThreadPoolExecutor(max_workers=len(self.workers)) as ex:
            list(ex.map(run_bucket, self.workers))
        return results

    # ---------- 关闭 ----------
    def close(self):
        for w in self._owned:
            w.close()
        self._owned = []
        self.workers = []
        for fh in getattr(self, "_log_handles", []):
            try:
                fh.close()
            except Exception:
                pass


if __name__ == "__main__":
    # 自检：拉起 N 个实例并发跑 M 次 search_feeds，用「墙钟 vs 单任务耗时之和」
    # 直接算加速比（二者之比即并发收益，不必再单独跑一遍串行基线）
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_WORKERS
    m = int(sys.argv[2]) if len(sys.argv) > 2 else n * 3
    kw = sys.argv[3] if len(sys.argv) > 3 else "银行立减金"
    pool = MCPPool(workers=n)
    pool.start()
    try:
        tasks = [f"{kw}{i}" for i in range(m)]
        durations = []

        def do(w, keyword):
            t0 = time.time()
            txt = w.call_text("search_feeds", {"keyword": keyword}, timeout=240)
            dt = time.time() - t0
            durations.append(dt)
            log(f"   w{w.idx} [{keyword}] {dt:.1f}s {len(txt)}B")
            return len(txt)

        t0 = time.time()
        res = pool.map(tasks, do, desc="search")
        wall = time.time() - t0
        serial = sum(durations)
        log(f"\n任务 {m} 个 / 实例 {n} 个")
        log(f"  墙钟 {wall:.1f}s ｜ 单任务耗时之和(≈串行) {serial:.1f}s")
        log(f"  加速比 {serial/wall:.2f}x ｜ 成功 {sum(1 for r in res if r)}/{m}")
        log(f"  各 worker 调用数: "
            f"{[(w.idx, w.n_calls, w.errors) for w in pool.workers]}")
    finally:
        pool.close()
