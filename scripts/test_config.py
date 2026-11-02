#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FlClash 配置文件真实环境测试器（要求在关闭电脑所有代理后运行）。
"""

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

DEFAULT_PROXY = [
    "chatgpt.com", "api.openai.com", "gemini.google.com",
    "antigravity.google", "muse.ai", "www.meta.ai",
    "www.gstatic.com", "lh3.googleusercontent.com",
    "iplocate.io", "ipinfo.io",
]
DEFAULT_DIRECT = ["baidu.com", "www.taobao.com", "ieee.org"]
DEFAULT_REJECT = ["doubleclick.net", "adservice.google.com"]

# 兼容带引号、不带引号、含嗅探括号的日志格式
LOG_RE = re.compile(r"\[TCP\] .+ --> ([^ ]+)(?: \([^)]+\))? match (.+?) using ([^\r\n\"]+)")


def log(msg=""):
    print(msg, flush=True)


def check_no_active_proxy():
    """确认电脑上的 FlClash 和系统代理已关闭，保证真实测试环境纯净。"""
    if os.name != "nt":
        return []
    warnings = []
    # 1. 检查 FlClash 进程
    r = subprocess.run(["tasklist", "/NH"], capture_output=True, text=True,
                       encoding="gbk", errors="replace", creationflags=CREATE_NO_WINDOW)
    out = (r.stdout or "").lower()
    if "flclash.exe" in out or "flclashcore.exe" in out:
        warnings.append("检测到 FlClash.exe / FlClashCore.exe 进程仍在后台运行")

    # 2. 检查 Windows 系统代理注册表
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
        enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
        if enable == 1:
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
            warnings.append(f"检测到 Windows 系统代理仍处于开启状态 ({server})")
    except Exception:
        pass
    return warnings


def http_json(url, secret, timeout=5):
    req = urllib.request.Request(url)
    if secret:
        req.add_header("Authorization", "Bearer " + secret)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def connect_test(host, port, timeout=8):
    """通过本地测试端口发起 CONNECT 请求，触发内核分流。"""
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout)
        s.sendall(f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n".encode())
        data = s.recv(512)
        s.close()
        return data.startswith(b"HTTP/1.1 200") or data.startswith(b"HTTP/1.0 200")
    except Exception:
        return False


def prepare_runtime_config(src, dst, port, ctrl, secret):
    with open(src, encoding="utf-8") as f:
        text = f.read()
    if re.search(r"^mixed-port:", text, flags=re.M):
        text = re.sub(r"^mixed-port:.*$", f"mixed-port: {port}", text, count=1, flags=re.M)
    else:
        text = f"mixed-port: {port}\n" + text

    if re.search(r"^log-level:", text, flags=re.M):
        text = re.sub(r"^log-level:.*$", "log-level: info", text, count=1, flags=re.M)
    else:
        text = "log-level: info\n" + text

    # 只关闭 tun 段内的 enable，绝不能动 dns.enable / sniffer.enable。
    # 早期版本用全文正则 ^(\s*enable:\s*)true 替换，会把 dns 的 fake-ip 和
    # sniffer 一起关掉，导致测试的其实是一份被改过 DNS 的配置，结论失真。
    lines, out, in_tun, tun_indent = text.splitlines(), [], False, 0
    for ln in lines:
        m = re.match(r"^(\s*)tun:\s*(#.*)?$", ln)
        if m:
            in_tun, tun_indent = True, len(m.group(1))
        elif in_tun and ln.strip() and not ln.startswith(" " * (tun_indent + 1)):
            in_tun = False
        if in_tun and re.match(r"^\s*enable:\s*true", ln):
            ln = re.sub(r"enable:\s*true", "enable: false", ln)
        out.append(ln)
    text = "\n".join(out)
    if not re.search(r"^tun:", text, flags=re.M):
        text = text.rstrip() + "\ntun:\n  enable: false\n"

    text = re.sub(r"^external-controller:.*$", "", text, flags=re.M)
    text = re.sub(r"^secret:.*$", "", text, flags=re.M)
    text = text.rstrip() + f"\nexternal-controller: 127.0.0.1:{ctrl}\nsecret: {secret}\n"
    with open(dst, "w", encoding="utf-8") as f:
        f.write(text)


def copy_preloaded_env(env_dir, work_dir):
    """把第三步预下载的 geoip.metadb 和 rules/ 规则集完整复制到测试目录。"""
    if not env_dir or not os.path.exists(env_dir):
        return
    for item in os.listdir(env_dir):
        if item.lower().endswith(".exe") or item.lower().endswith(".zip"):
            continue
        s = os.path.join(env_dir, item)
        d = os.path.join(work_dir, item)
        if os.path.isdir(s):
            shutil.copytree(s, d, dirs_exist_ok=True)
            log(f"[准备] 已注入预下载规则集目录: {item}/")
        else:
            shutil.copy2(s, d)
            log(f"[准备] 已注入环境数据文件: {item} ({os.path.getsize(s)/1048576:.1f} MB)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, help="待测 FlClash 配置文件")
    ap.add_argument("--env-dir", default=os.path.join(tempfile.gettempdir(), "flclash_test_env"),
                    help="第三步 setup_test_env.ps1 生成的环境目录")
    ap.add_argument("--exe", default=None, help="mihomo.exe 路径（默认从 env-dir 查找）")
    ap.add_argument("--port", type=int, default=17890)
    ap.add_argument("--controller", type=int, default=19090)
    ap.add_argument("--secret", default="flclashtest")
    ap.add_argument("--timeout", type=int, default=180, help="等待规则集解析的秒数")
    ap.add_argument("--expect-proxy", nargs="*", default=DEFAULT_PROXY)
    ap.add_argument("--expect-direct", nargs="*", default=DEFAULT_DIRECT)
    ap.add_argument("--expect-reject", nargs="*", default=DEFAULT_REJECT,
                    help="必须命中 REJECT 的广告域名（验证去广告真的生效）")
    ap.add_argument("--ignore-proxy-check", action="store_true", help="跳过本机代理关闭检查")
    args = ap.parse_args()

    exe_path = args.exe or os.path.join(args.env_dir, "mihomo.exe")
    if not os.path.exists(exe_path):
        sys.exit(f"找不到测试内核: {exe_path}，请先运行 setup_test_env.ps1")
    if not os.path.exists(args.config):
        sys.exit(f"找不到配置文件: {args.config}")

    # ---------- 0. 检查电脑上的代理是否已关闭 ----------
    if not args.ignore_proxy_check:
        active_warnings = check_no_active_proxy()
        if active_warnings:
            log("!" * 60)
            log("【环境检查未通过】为了测试真实环境分流，请先关闭电脑上的所有代理：")
            for w in active_warnings:
                log("  - " + w)
            log("请彻底退出 FlClash 后再重新运行本测试脚本！")
            log("!" * 60)
            return 2

    work = tempfile.mkdtemp(prefix="flclash_test_")
    proc = None
    failures = []
    try:
        # ---------- 注入第三步预下载的 geoip 和 ads 等规则集 ----------
        copy_preloaded_env(args.env_dir, work)

        # ---------- [1/4] 静态校验 ----------
        log("\n[1/4] 静态语法与规则校验")
        rt = os.path.join(work, "runtime.yaml")
        prepare_runtime_config(os.path.abspath(args.config), rt, args.port, args.controller, args.secret)
        r = subprocess.run([exe_path, "-t", "-f", rt, "-d", work],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", creationflags=CREATE_NO_WINDOW)
        for l in [x for x in (r.stdout or "").splitlines() if x.strip()][-3:]:
            log("  " + l)
        if r.returncode != 0:
            failures.append("配置文件静态校验失败")
            log("  => FAIL")
            raise RuntimeError("static check failed")
        log("  => PASS")

        # ---------- [2/4] 启动测试实例 ----------
        log(f"\n[2/4] 启动无代理真实环境测试实例 (port={args.port}, controller={args.controller})")
        logfile = os.path.join(work, "run.log")
        fh = open(logfile, "w", encoding="utf-8", errors="replace")
        proc = subprocess.Popen([exe_path, "-f", rt, "-d", work],
                                stdout=fh, stderr=subprocess.STDOUT,
                                creationflags=CREATE_NO_WINDOW)

        base = f"http://127.0.0.1:{args.controller}"
        ready = False
        for _ in range(40):
            try:
                v = http_json(base + "/version", args.secret, 2)
                log(f"  内核就绪: {v.get('version')}")
                ready = True
                break
            except Exception:
                time.sleep(0.5)
        if not ready:
            failures.append("测试实例未能启动")
            raise RuntimeError("startup failed")

        # ---------- [3/4] 验证规则集（含 ads）加载 ----------
        log("\n[3/4] 校验规则集加载状态")
        deadline = time.time() + args.timeout
        providers = {}
        while time.time() < deadline:
            try:
                providers = http_json(base + "/providers/rules", args.secret).get("providers", {})
                if not providers or all(v.get("ruleCount", 0) > 0 for v in providers.values()):
                    break
            except Exception:
                pass
            time.sleep(2)

        empty = [k for k, v in providers.items() if v.get("ruleCount", 0) == 0]
        total = sum(v.get("ruleCount", 0) for v in providers.values())
        log(f"  已加载规则集 {len(providers)} 个 / 共 {total} 条规则")
        if empty:
            failures.append("部分规则集未能加载: " + ", ".join(empty))
            log("  => FAIL 未加载: " + ", ".join(empty))
        else:
            log("  => PASS (所有规则集本地加载完毕)")

        # ---------- [4/4] 真实流量分流验证 ----------
        log("\n[4/4] 真实网络分流探测")
        targets = ([(d, "proxy") for d in args.expect_proxy]
                   + [(d, "DIRECT") for d in args.expect_direct]
                   + [(d, "REJECT") for d in args.expect_reject])

        for host, _ in targets:
            connect_test(host, args.port)
            time.sleep(0.3)

        time.sleep(1.5)
        fh.flush()
        hits = {}
        with open(logfile, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = LOG_RE.search(line)
                if m:
                    hits[m.group(1)] = (m.group(2).strip(), m.group(3).strip())

        log("  %-30s %-40s %-24s %s" % ("域名", "命中规则", "实际出口", "判定"))
        log("  " + "-" * 104)
        for host, want in targets:
            key = f"{host}:443"
            rule, tgt = hits.get(key, ("(未匹配到日志)", "(未知)"))
            if want == "proxy":
                good = (tgt != "(未知)") and ("DIRECT" not in tgt.upper()) and ("REJECT" not in tgt.upper())
            elif want == "REJECT":
                good = "REJECT" in tgt.upper()
            else:
                good = want.upper() in tgt.upper()
            if not good:
                failures.append(f"分流不符: {host} 预期 [{want}]，实际命中 [{rule}] 走 [{tgt}]")
            short_tgt = tgt.split("[")[0].strip()
            log("  %-30s %-40s %-24s %s" % (host, rule[:38], short_tgt, "PASS" if good else "<< 不符"))

    except RuntimeError:
        pass
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        try:
            fh.close()
        except Exception:
            pass
        # 失败时保留现场，否则第四步"看日志定位"无从下手
        if failures:
            keep = os.path.join(tempfile.gettempdir(), "flclash_last_failed")
            try:
                shutil.rmtree(keep, ignore_errors=True)
                shutil.copytree(work, keep)
                log(f"\n[调试] 失败现场已保留，日志: {os.path.join(keep, 'run.log')}")
            except Exception as e:
                log(f"\n[调试] 保留失败现场失败: {e}")
        shutil.rmtree(work, ignore_errors=True)

    log("\n" + "=" * 60)
    if failures:
        log(f"测试未通过，发现 {len(failures)} 项问题（请修改配置/脚本后重测）:")
        for f in failures:
            log("  - " + f)
        return 1
    log("全部真实环境分流测试 PASS！现在可以打开 FlClash 导入该配置使用。")
    return 0


if __name__ == "__main__":
    sys.exit(main())