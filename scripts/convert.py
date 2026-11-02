#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 Shadowrocket 分流规则（sr_top500_whitelist_ad.conf）转换为 FlClash 配置。
支持任意机场订阅自动识别主策略组，零第三方依赖，跨电脑开箱即用。
"""

import argparse
import os
import re
import sys

# 核心 AI 专属域名：最高优先级置顶（不受任何白名单或直连规则影响）
AI_CORE_SUFFIX = [
    # ChatGPT / OpenAI
    # 注意：不要加 auth0.com。OpenAI 的登录域是 auth0.openai.com，
    # 已被 openai.com 覆盖；单独加 auth0.com 会把所有用 Auth0 登录的
    # 第三方 SaaS 一并送进代理。
    "openai.com", "chatgpt.com", "oaistatic.com", "oaiusercontent.com",
    "sora.com", "livekit.cloud",
    # Gemini / Google AI 专属域名
    "gemini.google.com", "generativeai.google", "deepmind.com",
    "deepmind.google", "ai.google.dev", "google.dev", "makersuite.google.com",
    "aistudio.google.com", "notebooklm.google.com", "notebooklm.google",
    # Antigravity (Google AI IDE) —— 独立 .google / .goog 顶级域
    "antigravity.google", "antigravity-unleash.goog",
    # Muse / Meta AI 专属域名
    "muse.ai", "meta.ai",
]
AI_CORE_KEYWORD = ["openai", "generativelanguage", "alkalimakersuite", "antigravity"]

# 生态公共域名：放在 SR 去广告 (Reject) 之后、直连白名单 (Direct) 之前
AI_ECO_SUFFIX = [
    "google.com", "gstatic.com", "googleusercontent.com", "googleapis.com",
    "ggpht.com", "withgoogle.com", "meta.com", "fbcdn.net",
    "facebook.com", "fb.com", "cdninstagram.com", "instagram.com",
]

# IP 检测站：必须显式走代理。
# 否则它们会落到 GEOIP,CN 兜底，一旦解析到国内 CDN 节点就被判直连，
# 检测结果永远显示本地运营商（典型现象：面板显示 China Mobile / AS24445）。
# 注意：不要加 cloudflare.com —— cdnjs.cloudflare.com 是公共 JS CDN，会被误代理。
IP_CHECK_SUFFIX = [
    "iplocate.io", "ipinfo.io", "ip.sb", "ping0.cc",
    "ip-api.com", "ipapi.co", "ipapi.is", "ipapi.com",
    "ipwho.is", "ipwhois.app", "ipdata.co", "ipgeolocation.io",
    "ifconfig.me", "ifconfig.co", "icanhazip.com", "ipify.org",
    "ident.me", "myexternalip.com", "ipchicken.com",
    "whatismyipaddress.com", "whatismyip.com", "wtfismyip.com",
    "whatismyip.akamai.com", "checkip.amazonaws.com",
    "ip.me", "ip.gs", "ipcheck.ing", "ip.skk.moe",
    "db-ip.com", "myip.com",
]

LAN_RULES = [
    "DOMAIN,localhost,DIRECT",
    "DOMAIN-SUFFIX,localhost,DIRECT",
    "DOMAIN-SUFFIX,lan,DIRECT",
    "DOMAIN-SUFFIX,local,DIRECT",
    "IP-CIDR,0.0.0.0/8,DIRECT,no-resolve",
    "IP-CIDR,10.0.0.0/8,DIRECT,no-resolve",
    "IP-CIDR,100.64.0.0/10,DIRECT,no-resolve",
    "IP-CIDR,127.0.0.0/8,DIRECT,no-resolve",
    "IP-CIDR,169.254.0.0/16,DIRECT,no-resolve",
    "IP-CIDR,172.16.0.0/12,DIRECT,no-resolve",
    "IP-CIDR,192.168.0.0/16,DIRECT,no-resolve",
    "IP-CIDR,224.0.0.0/4,DIRECT,no-resolve",
    "IP-CIDR,240.0.0.0/4,DIRECT,no-resolve",
    "IP-CIDR6,::/128,DIRECT,no-resolve",
    "IP-CIDR6,::1/128,DIRECT,no-resolve",
    "IP-CIDR6,fc00::/7,DIRECT,no-resolve",
    "IP-CIDR6,fe80::/10,DIRECT,no-resolve",
    "IP-CIDR6,ff00::/8,DIRECT,no-resolve",
]

VALID_TYPES = {
    "DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD",
    "IP-CIDR", "IP-CIDR6", "SRC-IP-CIDR",
    "DST-PORT", "SRC-PORT", "PROCESS-NAME",
}


def parse_sr(path):
    """解析 Shadowrocket 配置的 [Rule] 段。"""
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    in_rule, out = False, []
    for ln in lines:
        s = ln.strip()
        if s == "[Rule]":
            in_rule = True
            continue
        if s.startswith("[") and s != "[Rule]":
            in_rule = False
        if in_rule and s and not s.startswith("#"):
            out.append(s)
    return out


def dedup(rules):
    seen, out = set(), []
    for r in rules:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def parse_sr_rule(r):
    """解析单条 SR 规则，返回 (type, value, action, no_resolve) 或 None。"""
    parts = [x.strip() for x in r.split(",")]
    if len(parts) < 3:
        return None
    typ = parts[0].upper()
    if typ not in VALID_TYPES:
        return None
    no_resolve = False
    if parts[-1].lower() == "no-resolve":
        no_resolve = True
        parts = parts[:-1]
    if len(parts) < 3:
        return None
    target = parts[-1].lower()
    val = ",".join(parts[1:-1])
    return typ, val, target, no_resolve


def classify(sr_rules):
    """按动作归类 SR 规则。"""
    reject, direct, proxy, other = [], [], [], []
    for r in sr_rules:
        parsed = parse_sr_rule(r)
        if not parsed:
            other.append(r)
            continue
        typ, val, low, _ = parsed
        norm_key = f"{typ},{val},{low}"
        if low == "reject":
            reject.append(norm_key)
        elif low == "direct":
            direct.append(norm_key)
        elif low == "proxy":
            proxy.append(norm_key)
        else:
            other.append(r)
    return dedup(reject), dedup(direct), dedup(proxy), other


def to_clash(norm_rule, proxy_group):
    """把归一化后的 SR 规则转成 FlClash 语法。"""
    parts = norm_rule.split(",")
    typ, val, low = parts[0], ",".join(parts[1:-1]), parts[-1]
    if low == "reject":
        new_target = "REJECT"
    elif low == "direct":
        new_target = "DIRECT"
    else:
        new_target = proxy_group
    if typ in ("IP-CIDR", "IP-CIDR6"):
        return f"{typ},{val},{new_target},no-resolve"
    return f"{typ},{val},{new_target}"


def split_base_config(base_lines):
    """安全切分 base.yaml 的 rules: 段，保留 rules: 之前和之后的其他顶级配置段。"""
    rule_idx = None
    for i, l in enumerate(base_lines):
        if re.match(r"^rules:\s*(#.*)?$", l):
            rule_idx = i
            break
    if rule_idx is None:
        sys.exit("错误: base 配置里找不到 rules: 行")

    end_idx = len(base_lines)
    base_rules = []
    for i in range(rule_idx + 1, len(base_lines)):
        ln = base_lines[i]
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        if not ln.startswith(" ") and not ln.startswith("\t") and not s.startswith("-"):
            end_idx = i
            break
        if s.startswith("- "):
            item = s[2:].strip()
            if (item.startswith('"') and item.endswith('"')) or (item.startswith("'") and item.endswith("'")):
                item = item[1:-1]
            base_rules.append(item)

    head = base_lines[:rule_idx]
    tail = base_lines[end_idx:]
    return head, base_rules, tail


def auto_detect_group(head_lines, base_rules, user_group):
    """若用户未指定 --group 或指定的组名在 base 中不存在，自动识别主代理策略组。"""
    full_head = "\n".join(head_lines)
    if user_group:
        if user_group in full_head:
            return user_group
        print(f"警告: --group \"{user_group}\" 在 base 配置的 proxy-groups 中不存在，"
              f"已改为自动识别（请核对拼写或 Emoji 转义）")

    # 1. 优先从原配置 rules 里的 MATCH 或常见代理 RULE-SET 提取目标策略组
    for r in reversed(base_rules):
        parts = [x.strip() for x in r.split(",")]
        if parts[0].upper() == "MATCH" and len(parts) >= 2:
            cand = parts[1]
            if cand.upper() not in ("DIRECT", "REJECT"):
                print(f"已从原配置 MATCH 自动识别主代理策略组: {cand}")
                return cand

    # 2. 从 proxy-groups 段找第一个 name
    in_pg = False
    for ln in head_lines:
        if re.match(r"^proxy-groups:\s*(#.*)?$", ln):
            in_pg = True
            continue
        if in_pg and ln and not ln.startswith(" ") and not ln.startswith("\t") and not ln.startswith("-"):
            break
        if in_pg:
            m = re.search(r"name:\s*['\"]?([^'\"#,\r\n}]+)['\"]?", ln)
            if m:
                cand = m.group(1).strip()
                print(f"已从 proxy-groups 自动识别主代理策略组: {cand}")
                return cand

    return user_group or "\\U0001F680 节点选择"


def fix_provider_behavior(head_lines):
    """修正 private 规则集 behavior：裸域名列表在 FlClash 中必须用 domain。"""
    for i, l in enumerate(head_lines):
        if re.match(r"^\s+private:\s*$", l):
            for j in range(i + 1, min(i + 8, len(head_lines))):
                if "behavior:" in head_lines[j] and "classical" in head_lines[j]:
                    head_lines[j] = head_lines[j].replace("classical", "domain")
                    return True
        elif re.match(r"^\s+private:\s*\{.*behavior:\s*classical.*\}$", l):
            head_lines[i] = l.replace("classical", "domain")
            return True
    return False


def build(args):
    sr_rules = parse_sr(args.sr)
    reject, direct, proxy, other = classify(sr_rules)
    print(f"SR 规则: Reject={len(reject)} Direct={len(direct)} Proxy={len(proxy)} 跳过={len(other)}")

    with open(args.base, encoding="utf-8") as f:
        base_lines = f.read().splitlines()

    head, base_rules, tail = split_base_config(base_lines)

    if fix_provider_behavior(head):
        print("已修正 private 规则集 behavior: classical -> domain")

    g = auto_detect_group(head, base_rules, args.group)

    # 识别 base 中的 RULE-SET
    lan_sets, cn_sets, ads_sets, proxy_sets = [], [], [], []
    for r in base_rules:
        if not r.upper().startswith("RULE-SET,"):
            continue
        parts = [x.strip() for x in r.split(",")]
        if len(parts) < 3:
            continue
        set_name = parts[1]
        set_low = set_name.lower()
        target_up = parts[-1].upper()

        if set_low in ("private", "lan", "local"):
            lan_sets.append(r)
        elif "ad" in set_low or "reject" in set_low or target_up == "REJECT":
            ads_sets.append(f"RULE-SET,{set_name},REJECT")
        elif "cn" in set_low or "china" in set_low or "direct" in set_low or target_up == "DIRECT":
            cn_sets.append(r)
        else:
            proxy_sets.append(r)

    R = []

    def add(s):
        R.append('  - "%s"' % s.replace('"', '\\"'))

    def cmt(s):
        R.append("  # " + s)

    cmt("===== 1. 局域网 / 私有地址 =====")
    for r in LAN_RULES:
        add(r)
    for r in lan_sets:
        add(r)

    R.append("")
    cmt("===== 2. 核心 AI 服务（ChatGPT / Gemini / Antigravity / Muse）最高优先级代理 =====")
    if args.ai:
        for d in AI_CORE_SUFFIX:
            add(f"DOMAIN-SUFFIX,{d.strip()},{g}")
        for k in AI_CORE_KEYWORD:
            add(f"DOMAIN-KEYWORD,{k.strip()},{g}")

    if reject or ads_sets:
        R.append("")
        cmt(f"===== 3. 广告拦截（SR Reject {len(reject)} 条 + 订阅 ads 规则集）=====")
        for r in ads_sets:
            add(r)
        for r in reject:
            add(to_clash(r, g))

    if args.ai:
        R.append("")
        cmt("===== 4. Google / Meta 生态公共域名代理 =====")
        for d in AI_ECO_SUFFIX:
            add(f"DOMAIN-SUFFIX,{d.strip()},{g}")

    R.append("")
    cmt(f"===== 5. IP 检测站（{len(IP_CHECK_SUFFIX)} 条，强制走代理）=====")
    for d in IP_CHECK_SUFFIX:
        add(f"DOMAIN-SUFFIX,{d.strip()},{g}")

    R.append("")
    cmt(f"===== 6. SR 直连白名单（{len(direct)} 条）=====")
    for r in direct:
        add(to_clash(r, g))

    R.append("")
    cmt(f"===== 7. SR 代理列表（{len(proxy)} 条）=====")
    for r in proxy:
        add(to_clash(r, g))

    if proxy_sets:
        R.append("")
        cmt("===== 8. 订阅代理规则集兜底 =====")
        for r in proxy_sets:
            add(r)

    R.append("")
    cmt("===== 9. 国内分流（CN 直连）=====")
    add("DOMAIN-SUFFIX,cn,DIRECT")
    for r in cn_sets:
        add(r)
    add("GEOIP,CN,DIRECT,no-resolve")

    R.append("")
    add(f"MATCH,{g}")

    out_lines = head + ["rules:"] + R + ([""] + tail if tail else [""])
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines))

    total = sum(1 for x in R if x.strip().startswith("- "))
    print(f"已生成 FlClash 配置: {args.out}")
    print(f"规则总数: {total}（其中 SR 转换 {len(reject) + len(direct) + len(proxy)} 条，主策略组: {g}）")


def main():
    p = argparse.ArgumentParser(description="Shadowrocket 规则转 FlClash 配置")
    p.add_argument("--sr", required=True, help="Shadowrocket .conf 路径")
    p.add_argument("--base", required=True, help="FlClash 订阅配置路径")
    p.add_argument("--out", required=True, help="输出 FlClash yaml 路径")
    p.add_argument("--group", default="", help="主代理策略组名称（不传则自动从订阅配置识别）")
    p.add_argument("--no-ai", dest="ai", action="store_false", help="不注入 AI 规则")
    p.set_defaults(ai=True)
    args = p.parse_args()
    for f in (args.sr, args.base):
        if not os.path.exists(f):
            sys.exit(f"文件不存在: {f}")
    build(args)


if __name__ == "__main__":
    main()