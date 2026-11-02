# flclash-rule-converter

把 Shadowrocket 的白名单去广告分流规则（`sr_top500_whitelist_ad.conf`）转换成 FlClash / Mihomo 配置，并保证 ChatGPT、Gemini、Antigravity、Muse 全部走代理；转换后还会自动准备测试环境，在关闭所有代理的真实网络下验证分流是否正确。

> Convert Shadowrocket whitelist + adblock rules into a FlClash/Mihomo config, force AI services (ChatGPT / Gemini / Antigravity / Muse) through the proxy, then verify the routing in a real, proxy-free environment.

## 解决什么问题

Shadowrocket 的规则是给 iOS 客户端用的，FlClash 用的是 Clash/Mihomo 语法，两者不能直接混用。而直接把规则"翻译"过去，通常会踩三个坑：

1. **AI 服务被拆成两半** —— `gstatic.com`、`googleusercontent.com` 在国内有 CDN 节点，按 IP 判定会被误判成直连，表现就是 Gemini 页面能打开但样式和头像全裂。
2. **去广告失效** —— 广告规则集如果指向代理组而不是 `REJECT`，广告域名照样能连通，但规则看起来"都配了"。
3. **顺序错了全盘皆输** —— Clash 是自上而下首个匹配生效，`cn-domain` 这类规则集放错位置会把 AI 域名抢成直连。

这个工具把上面三件事固定下来：AI 域名分两层（核心域名在广告拦截**之前**，Google/Meta 生态域名在广告拦截**之后**），转换完直接用官方 mihomo 内核跑真实分流测试。

## 前置条件

- Python 3.8+（无第三方依赖）
- PowerShell 5.1+（仅 Windows 测试用）
- 一份你自己的 FlClash 订阅配置（含 `proxies` / `proxy-groups` / `rule-providers`）
- 可访问 GitHub（测试内核下载用，脚本内置 `ghproxy.net` 镜像回退）

## 快速开始

### 第一步：拉取 Shadowrocket 规则

```powershell
curl.exe -sL "https://johnshall.github.io/Shadowrocket-ADBlock-Rules-Forever/sr_top500_whitelist_ad.conf" -o sr_top500_whitelist_ad.conf
```

约 5.3 万行，含 `[Rule]` 段。检查一下不是下载到了错误页面。

### 第二步：转换成 FlClash 配置

```powershell
python scripts/convert.py `
  --sr sr_top500_whitelist_ad.conf `
  --base <你的订阅配置.yaml> `
  --out <输出配置.yaml>
```

策略组名会自动从你订阅的 `MATCH` 或 `proxy-groups` 里识别，不用手填。想手动指定就加 `--group "🚀 节点选择"`。

转换只重写 `rules:` 段，你的 `proxies`、`proxy-groups`、`dns`、`sniffer`、`rule-providers` 全部原样保留；同时会自动修正 `private` 规则集的 `behavior`（详见 `references/pitfalls.md`）。

### 第三步：准备测试环境（此时保持代理开启）

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup_test_env.ps1 -Config <输出配置.yaml>
```

脚本会自动探测本机代理端口，下载 mihomo 内核和 `geoip.metadb`，并把配置里声明的所有 `rule-providers`（含 19.5 万条的 `ads`）预下载到 `%TEMP%\flclash_test_env`。**必须趁代理开着做完这步**，否则第四步在断网环境下会卡在下载。

### 第四步：关闭所有代理，跑真实分流测试

**先把 FlClash 彻底退出**（包括 TUN 模式），然后：

```powershell
python scripts/test_config.py --config <输出配置.yaml> --env-dir "$env:TEMP\flclash_test_env"
```

测试器会先检查你的代理确实关了，再起一个独立实例（默认 17890/19090，不碰你正在用的 7890），然后逐个域名发真实连接，从内核日志核对命中的规则和实际出口。

测试失败时日志会保留在 `%TEMP%\flclash_last_failed\run.log`。

## 规则优先级

```
1. 局域网 / 私有地址        DIRECT
2. 核心 AI 域名             PROXY    ← chatgpt / openai / gemini / antigravity / muse
3. 广告拦截（5.2 万条）      REJECT
4. Google / Meta 生态域名   PROXY    ← google.com / gstatic / googleapis / fbcdn
5. IP 检测站                PROXY    ← ipinfo.io / ip.sb / iplocate.io / ping0.cc …
6. SR 直连白名单            DIRECT
7. SR 代理列表              PROXY
8. 订阅代理规则集           PROXY
9. 国内分流                 DIRECT   ← cn-domain / cn-ip / GEOIP,CN
10. MATCH                   PROXY
```

第 2 层和第 4 层必须分开：`google.com` 如果放在第 3 层之前，`adservice.google.com` 会先命中代理规则，去广告就废了；但 `gstatic.com` 必须排在第 9 层之前，否则会被国内规则抢成直连。

第 5 层单独列出来，是因为 IP 检测站（`ipinfo.io`、`ip.sb`、`iplocate.io` 等）如果落到 `GEOIP,CN` 兜底，一旦解析到国内 CDN 节点就会被判直连，检测结果永远显示本地运营商。注意**不要**把 `cloudflare.com` 加进去——`cdnjs.cloudflare.com` 是公共 JS CDN，会被误代理。

`antigravity.google` 挂在 `.google` 顶级域下，**不是** `google.com` 的子域，必须单独声明——`antigravity-unleash.goog` 同理。

## 测试输出示例

```
[1/4] 静态语法与规则校验   => PASS
[2/4] 启动无代理测试实例   => v1.19.33
[3/4] 校验规则集加载状态   => 20 个 / 318,297 条
[4/4] 真实网络分流探测

域名                         命中规则                              实际出口        判定
chatgpt.com                  DomainSuffix(chatgpt.com)             🚀 节点选择     PASS
gemini.google.com            DomainSuffix(gemini.google.com)       🚀 节点选择     PASS
antigravity.google           DomainSuffix(antigravity.google)      🚀 节点选择     PASS
www.gstatic.com              DomainSuffix(gstatic.com)             🚀 节点选择     PASS
doubleclick.net              RuleSet(ads)                          REJECT          PASS
baidu.com                    DomainSuffix(baidu.com)               DIRECT          PASS
```

## 验证出口 IP（以及为什么 FlClash 面板会骗你）

转换完成后，**不要用 FlClash 仪表盘上的「网络检测」卡片判断出口**。它读的是本地缓存表，不会自动刷新：

```
%APPDATA%\com.follow\clash\database.sqlite  →  ip_qualities（ip / quality / checked_at）
```

卡片直接显示这条缓存。所以经常出现「规则早就改好了，面板还挂着几小时前的 China Mobile」。想确认缓存有多旧：

```python
import sqlite3, datetime
p = r"C:/Users/<用户名>/AppData/Roaming/com.follow/clash/database.sqlite"
for ip, q, ts in sqlite3.connect(p).execute("select ip, quality, checked_at from ip_qualities"):
    print(ip, datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M:%S"), q[:90])
```

如果 `checked_at` 是很久以前，点一下卡片刷新即可。如果刷新后仍是本地运营商，说明该卡片的请求本身走了直连（客户端为避免回环会把自身进程排除在 TUN 外），它不适合做判据——**这种情况加多少条规则都没用**。

实测佐证：同一台机器间隔 77 秒，卡片自己查 `iplocate.io` 拿到 `120.219.76.6`（China Mobile），而 `curl` 查同一个域名拿到 `209.107.75.136`（NTT America）。同域名、不同进程、结果不同，即证明客户端自身流量不经过规则引擎。想让仪表盘不误导，直接在仪表盘设置里移除这个「网络检测」组件。

**正确的验证方式**——curl 三条路径交叉对比：

```bash
# ① 走代理端口
curl -s -x http://127.0.0.1:7890 https://ipinfo.io/json

# ② 纯 TUN（不加 -x），最接近普通程序的真实路径
curl -s https://ipinfo.io/json

# ③ 关掉 FlClash 后直连，作为对照组
curl -s https://ipinfo.io/json
```

判读：①② 应返回**同一个海外 IP**，③ 返回本地运营商 IP。

| 现象 | 结论 |
|---|---|
| ①② 都是海外 IP | 分流正常 |
| ① 海外、② 本地 | TUN 没接管或 `auto-route` 失效，不是规则问题 |
| ①② 都是本地 | 规则没生效，检查 `IP_CHECK_SUFFIX` 是否写进了 `rules:` |

或者直接用浏览器打开 `https://ipinfo.io` / `https://ip.sb` / `https://api.ipify.org` / `https://ifconfig.co`。

> **浏览器里看到的可能是假象。** 浏览器有连接池，改配置前建立的 keep-alive 连接不会因为内核重载而重新分流，于是连接列表里那条 `msedge.exe → ipinfo.io:443` 会一直显示「直连」，关掉标签页重开就正常了。改完配置请 `Ctrl+Shift+R` 强制刷新，或去 `edge://net-internals/#sockets` 点 `Flush socket pools`。想确认「此刻」规则是否生效，用 curl 起新进程测，别用已经开着的页面。

> 判断直连组是否真的直连，看**耗时**而不是状态码：`www.taobao.com` 约 83ms、`cdnjs.cloudflare.com` 约 284ms，都是本地往返量级。若被误代理到美国节点，至少 200ms 以上。

## 作为 AI Skill 使用

本仓库同时是一份 Claude / WorkBuddy 技能包。把整个目录放进 `~/.workbuddy-ai/skills/` 后，可以直接说"用 flclash-rule-converter 把白名单去广告规则转成 FLClash 配置"，四步流程会自动执行。

## 常见坑

`references/pitfalls.md` 记录了实战踩过的坑，包括：

- `private` 规则集 `behavior: classical` 导致 133 条规则全部失效
- 策略组名含 Emoji 时规则必须加双引号，否则 `\U0001F680` 被当字面量
- 测试时用全文正则关 TUN 会误关 `dns.enable` / `sniffer.enable`，导致测的不是同一份配置
- PowerShell 函数用 `Write-Output` 打印进度会污染返回值，让失败检查永远不触发
- FlClash 的 `ip_qualities` 缓存导致面板长期显示旧 IP（见上一节）

## 免责声明

本项目只做**配置格式转换和本地分流测试**，不提供任何代理节点、订阅或翻墙服务。请自行准备合法的网络环境，并遵守所在地区的法律法规。

规则来源：[Johnshall/Shadowrocket-ADBlock-Rules-Forever](https://github.com/Johnshall/Shadowrocket-ADBlock-Rules-Forever)（每天北京时间 08:00 自动更新）。测试内核：[MetaCubeX/mihomo](https://github.com/MetaCubeX/mihomo)。

## License

MIT
