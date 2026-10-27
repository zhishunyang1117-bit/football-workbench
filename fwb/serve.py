"""本机一键分享：Streamlit 应用 + 后台定时更新数据 + Cloudflare 临时公网隧道。

    python -m fwb.serve                 # 全部开启
    python -m fwb.serve --no-tunnel     # 只在本机/局域网访问
    python -m fwb.serve --interval 15   # 每 15 分钟增量更新一次

- 实时比分由应用自己每 60 秒拉取（全站共享缓存）；积分榜/预测等由这里的后台管道定时更新。
- 隧道使用 Cloudflare Quick Tunnel（无需账号，链接形如 https://xxx.trycloudflare.com，每次启动会变）。
  Cloudflare 将其定位为测试用途、无可用性保证；只适合临时分享给朋友。
- 运行期间阻止 Windows 进入睡眠（关闭窗口即恢复），屏幕仍可正常息屏。
- 按 Ctrl+C 或关闭窗口即停止全部服务，公网链接随之失效。
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from fwb.settings import DATA_DIR, ROOT, SECRETS_FILE, TOKEN_KEY, fdorg_token

LOG_DIR = ROOT / "logs"
SHARE_FILE = ROOT / "share_url.txt"
# 排除 api.trycloudflare.com（cloudflared 申请隧道时请求的接口地址，也会出现在日志里）
URL_RE = re.compile(r"https://(?!api\.)[a-z0-9-]+\.trycloudflare\.com")
_stop = threading.Event()


def say(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# ------------------------------------------------------------------ token
def ensure_token(interactive: bool) -> bool:
    if fdorg_token():
        return True
    if not interactive or not sys.stdin or not sys.stdin.isatty():
        say("未配置 football-data.org token：实时比分页会显示注册指引，其余功能正常。")
        return False
    print("\n实时比分需要 football-data.org 的免费 token（https://www.football-data.org/client/register 注册后发到邮箱）。")
    tok = getpass.getpass("请粘贴 token 后回车（输入内容不会显示；直接回车跳过）：").strip()
    if not tok:
        return False
    ok = _check_token(tok)
    if ok is False:
        say("❌ token 无效（football-data.org 返回 400/401/403），未保存。可稍后重新运行再试。")
        return False
    SECRETS_FILE.parent.mkdir(exist_ok=True)
    lines = [ln for ln in (SECRETS_FILE.read_text(encoding="utf-8").splitlines() if SECRETS_FILE.exists() else [])
             if not ln.strip().startswith(TOKEN_KEY)]
    lines.append(f'{TOKEN_KEY} = "{tok}"')
    SECRETS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    say(f"✅ token 已保存到 {SECRETS_FILE.relative_to(ROOT)}（仅本机，已在 .gitignore 中排除）")
    return True


def _check_token(tok: str) -> bool | None:
    """True=有效，False=无效，None=网络问题无法判断。"""
    req = urllib.request.Request("https://api.football-data.org/v4/competitions/PL",
                                 headers={"X-Auth-Token": tok, "User-Agent": "football-workbench/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status == 200
    except urllib.error.HTTPError as e:
        return False if e.code in (400, 401, 403) else None
    except OSError:
        return None


# ------------------------------------------------------------------ keep awake (Windows)
def keep_awake(on: bool) -> None:
    if os.name != "nt":
        return
    import ctypes

    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))


# ------------------------------------------------------------------ data updater
def _last_success(mode: str) -> datetime | None:
    try:
        runs = json.loads((DATA_DIR / "manifest.json").read_text(encoding="utf-8")).get("runs", [])
    except (OSError, ValueError):
        return None
    for r in runs:
        if r.get("mode") == mode and r.get("status") in ("success", "partial"):
            return datetime.fromisoformat(r["finished_at"])
    return None


def updater(interval_min: float) -> None:
    """后台循环：启动时立即跑一次；每周至少一次全量兜底，其余为增量。"""
    LOG_DIR.mkdir(exist_ok=True)
    while not _stop.is_set():
        last_full = _last_success("full")
        mode = "full" if last_full is None or (datetime.now(timezone.utc) - last_full).days >= 7 else "incremental"
        say(f"数据更新开始（{mode}）…")
        t0 = time.monotonic()
        with open(LOG_DIR / "pipeline.log", "a", encoding="utf-8") as log:
            log.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} {mode} =====\n")
            log.flush()
            code = subprocess.call([sys.executable, "-m", "fwb.pipeline", "--mode", mode], cwd=ROOT,
                                   stdout=log, stderr=subprocess.STDOUT, env={**os.environ, "PYTHONUTF8": "1"})
        status = "完成" if code == 0 else f"失败（退出码 {code}，详见 logs/pipeline.log）"
        say(f"数据更新{status}，用时 {time.monotonic() - t0:.0f} 秒；{interval_min:g} 分钟后再次更新")
        _stop.wait(interval_min * 60)


# ------------------------------------------------------------------ processes
def start_streamlit(port: int) -> subprocess.Popen:
    LOG_DIR.mkdir(exist_ok=True)
    log = open(LOG_DIR / "streamlit.log", "a", encoding="utf-8")
    return subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(ROOT / "streamlit_app.py"), "--server.port", str(port),
         "--server.headless", "true"],
        cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env={**os.environ, "PYTHONUTF8": "1"},
    )


def wait_healthy(port: int, timeout: float = 90) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=3) as r:
                if r.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(1)
    return False


def find_cloudflared() -> str | None:
    local = ROOT / "tools" / ("cloudflared.exe" if os.name == "nt" else "cloudflared")
    return str(local) if local.exists() else shutil.which("cloudflared")


def start_tunnel(exe: str, port: int, on_url) -> subprocess.Popen:
    proc = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", cwd=ROOT)

    def pump() -> None:
        with open(LOG_DIR / "tunnel.log", "a", encoding="utf-8") as log:
            for line in proc.stdout:
                log.write(line)
                log.flush()
                m = URL_RE.search(line)
                if m:
                    on_url(m.group(0))

    threading.Thread(target=pump, daemon=True).start()
    return proc


def announce(url: str) -> None:
    SHARE_FILE.write_text(url + "\n", encoding="utf-8")
    copied = False
    if os.name == "nt":
        try:
            subprocess.run("clip", input=url, text=True, check=True, shell=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            copied = True
        except (OSError, subprocess.CalledProcessError):
            pass
    bar = "=" * 64
    print(f"\n{bar}\n  🌐 分享给朋友的链接：{url}\n  {'（已复制到剪贴板）' if copied else ''}"
          f"链接也保存在 share_url.txt；关闭本窗口后失效\n{bar}\n", flush=True)


# ------------------------------------------------------------------ main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fwb.serve", description="本机一键分享足球分析工作台")
    ap.add_argument("--port", type=int, default=8501)
    ap.add_argument("--interval", type=float, default=30, help="增量更新间隔（分钟），默认 30")
    ap.add_argument("--no-tunnel", action="store_true", help="不开公网隧道（只在本机/局域网访问）")
    ap.add_argument("--no-update", action="store_true", help="不在后台更新数据")
    ap.add_argument("--no-keep-awake", action="store_true", help="允许电脑按电源计划睡眠")
    ap.add_argument("--no-prompt", action="store_true", help="不提示输入 token")
    args = ap.parse_args(argv)

    os.chdir(ROOT)
    ensure_token(interactive=not args.no_prompt)
    if not args.no_keep_awake:
        keep_awake(True)
        say("运行期间已阻止电脑睡眠（屏幕仍可息屏；关闭本窗口即恢复）")

    if wait_healthy(args.port, timeout=1):
        say(f"❌ 端口 {args.port} 已被占用（可能已经开着一个窗口）。请关闭它，或用 --port 8502 换一个端口。")
        return 1
    say(f"启动应用：http://localhost:{args.port}")
    app = start_streamlit(args.port)
    if not wait_healthy(args.port):
        say("❌ 应用启动失败，详见 logs/streamlit.log")
        app.terminate()
        return 1
    say(f"✅ 应用已就绪：http://localhost:{args.port}")

    if not args.no_update:
        threading.Thread(target=updater, args=(args.interval,), daemon=True).start()

    tunnel, exe = None, None
    if not args.no_tunnel:
        exe = find_cloudflared()
        if exe:
            say("正在建立公网隧道（Cloudflare Quick Tunnel）…")
            tunnel = start_tunnel(exe, args.port, announce)
        else:
            say("未找到 tools/cloudflared.exe，跳过公网隧道（朋友只能在同一局域网访问）")

    try:
        while True:  # 看门狗：子进程意外退出时自动拉起
            time.sleep(5)
            if app.poll() is not None:
                say("应用进程退出，正在重启…")
                app = start_streamlit(args.port)
                wait_healthy(args.port)
            if tunnel is not None and tunnel.poll() is not None:
                say("隧道断开，正在重连（新链接会重新显示）…")
                time.sleep(5)
                tunnel = start_tunnel(exe, args.port, announce)
    except KeyboardInterrupt:
        say("正在停止…")
    finally:
        _stop.set()
        for p in (tunnel, app):
            if p is not None and p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    p.kill()
        if not args.no_keep_awake:
            keep_awake(False)
        SHARE_FILE.unlink(missing_ok=True)
        say("已停止，公网链接已失效。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
