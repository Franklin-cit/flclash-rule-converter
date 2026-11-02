---
name: flclash-rule-converter
description: 把 Shadowrocket 白名单去广告分流规则转换为 FlClash 配置，保证 ChatGPT、Gemini、Antigravity 和 Muse 都走代理；转换后趁代理开启准备好内核、geoip 和 ads 等全部规则集，随后在关闭电脑所有代理的真实环境下运行分流测试并迭代修改直至全部通过。
agent_created: true
---

# Shadowrocket 白名单去广告规则转 FlClash 配置与真机分流测试

## 用途

根据用户提供的 FlClash 订阅配置，拉取 Shadowrocket 白名单去广告规则（`sr_top500_whitelist_ad.conf`）并转换为 FlClash 配置文件，确保 **ChatGPT、Gemini、Antigravity、Muse** 全部走代理。随后在代理开启状态下准备好全部运行环境（内核、`geoip.metadb` 及 `ads` 等所有规则集），再提醒用户**关闭 FlClash（关闭电脑所有代理）**，在纯真实网络环境下测试新配置的分流准确性，若有分流错误则持续修改并重测直至完全通过。

## 四步标准流程

### 第一步：拉取 Shadowrocket 白名单去广告规则

从固定地址拉取最新规则：
- 规则地址：`https://johnshall.github.io/Shadowrocket-ADBlock-Rules-Forever/sr_top500_whitelist_ad.conf`

```powershell
curl.exe -sL "https://johnshall.github.io/Shadowrocket-ADBlock-Rules-Forever/sr_top500_whitelist_ad.conf" -o sr_top500_whitelist_ad.conf
```

拉取后检查文件行数（约 5.3 万行，含 `[Rule]` 段），确认非空且未下载到错误页面。

### 第二步：根据 FlClash 订阅配置转换规则

先查看用户的 FlClash 订阅配置，确认主代理策略组名称（`proxy-groups` 中第一个 `type: select` 的组名，含 Emoji 需原样保留），然后执行转换：

```powershell
python scripts/convert.py `
  --sr sr_top500_whitelist_ad.conf `
  --base <用户的FlClash订阅配置.yaml> `
  --out <输出FlClash配置.yaml> `
  --group "<主代理策略组名>"
```

**转换核心逻辑（以 FlClash 为准）**：
1. **完整保留基础段**：原样保留订阅配置的 `proxies`、`proxy-groups`、`dns`、`sniffer`、`rule-providers` 等配置，仅重写 `rules:` 段（同时自动修正 `private` 规则集的 `behavior: domain`）。
2. **保证四大 AI 服务走代理**：
   - **ChatGPT**：`chatgpt.com`、`openai.com`、`oaistatic.com`、`oaiusercontent.com`、`sora.com`
   - **Gemini**：`gemini.google.com`、`generativelanguage.googleapis.com`、`ai.google.dev`、`deepmind.google`、`alkalimakersuite`
   - **Antigravity**：`antigravity.google`、`antigravity-unleash.goog`（`.google` / `.goog` 顶级域必须单独声明）
   - **Muse**：`muse.ai`、`meta.ai`、`meta.com`
   - **Google / Meta 公共静态与 API 域名**：`googleapis.com`、`gstatic.com`、`googleusercontent.com` 等排在广告拦截之后、直连白名单之前，既保证走代理又不破坏去广告。
3. **规则优先级顺序**：
   `局域网直连 (LAN / private)` → `核心 AI 专属域名强制代理` → `SR 去广告拦截 (Reject)` → `Google/Meta 公共域名代理` → `IP 检测站代理 (ipinfo.io / ip.sb / iplocate.io 等)` → `SR 直连白名单 (Direct)` → `SR 代理列表 (Proxy)` → `订阅规则集兜底 (含 ads 等)` → `国内分流 (cn / GEOIP,CN)` → `MATCH,<主代理策略组>`

### 第三步：打开代理准备好所有环境（内核、geoip、ads 等规则集）

**保持 FlClash 开启（电脑处于有代理状态）**，运行环境准备脚本。脚本会自动检测当前 FlClash 的代理端口，下载测试所需的内核、`geoip.metadb`，并**解析配置文件中的 `rule-providers`，把 `ads`、`private`、`cn-domain`、`cn-ip` 等所有远程规则集全部预下载到本地测试环境目录**：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup_test_env.ps1 -Config <输出FlClash配置.yaml>
```

> **⚠️ 准备完毕后的强制交互节点**：
> 当脚本输出 `== 所有环境准备完毕 ==` 后，**必须停下来提醒用户关闭 FlClash（彻底退出 FlClash，确保电脑上关闭所有代理与 TUN 模式）**。
> **原因**：第四步需要在没有任何外部代理干扰的真实网络环境下，检验新配置能否独自正常工作并准确分流。**必须等用户回复确认已关闭 FlClash 后，再执行第四步。**

### 第四步：在无代理真实环境下测试新配置并迭代修复

用户确认关闭 FlClash 后，运行自动化测试脚本：

```powershell
python scripts/test_config.py --config <输出FlClash配置.yaml> --env-dir "$env:TEMP\flclash_test_env"
```

**测试器执行流程**：
1. **检查电脑代理已关闭**：检测本机是否仍有 `FlClash.exe` 运行或系统代理未关，若有则报警中止，确保测试环境纯净。
2. **注入预置环境**：将第三步准备好的 `geoip.metadb` 和 `ads` 等全部本地 `rule-providers` 规则集复制到运行目录，实现断代理状态下秒级加载。
3. **静态校验与真实分流探测**：启动内核，校验所有规则集 `ruleCount > 0`，随后对目标域名发起真实连接探测，核对实际命中的规则与出口：
   - **必须走代理**：`chatgpt.com`、`api.openai.com`、`gemini.google.com`、`antigravity.google`、`muse.ai`、`www.meta.ai`、`www.gstatic.com`、`lh3.googleusercontent.com`
   - **IP 检测站必须走代理**：`iplocate.io`、`ipinfo.io`（用于验证出口 IP，若落到 `GEOIP,CN` 会被判直连）
   - **必须走直连**：`baidu.com`、`www.taobao.com`、`ieee.org`（白名单域名）
   - **必须被拦截**：`doubleclick.net`、`adservice.google.com` 必须命中 `REJECT`（验证去广告真的生效，而不只是规则存在）

   > **测试器只改 3 处**：`mixed-port` 换成测试端口、追加独立的 `external-controller` / `secret`、只关 `tun` 段内的 `enable`。
   > **绝不能碰 `dns.enable` 和 `sniffer.enable`** —— 一旦关掉，`fake-ip`、`nameserver-policy` 全部失效，测的就不是你最终要导入的那份配置了。
4. **问题迭代修复**：
   如果出现任何分流错误（`<< 不符`）或语法/规则集异常，立即根据日志定位是哪条规则顺序或配置项导致，修改 `convert.py` 或配置文件后重新生成并重新运行 `test_config.py`，**循环迭代直至所有测试项 100% 通过**。
   测试失败时日志会保留在 `%TEMP%\flclash_last_failed\run.log`（含 `runtime.yaml` 现场），直接看这个文件排查。

## 交付

测试通过后，向用户展示实测分流结果表与最终生成的 FlClash 配置文件路径，提醒用户可以重新打开 FlClash 并导入该配置使用。

### ⚠️ 交付时必说的一句话：不要用 FlClash 仪表盘判断出口

FlClash 仪表盘上的「网络检测」卡片读的是本地缓存表（`%APPDATA%\com.follow\clash\database.sqlite` 的 `ip_qualities`），**不会自动刷新**，而且可能测的是直连出口。用户看到它显示本地运营商（如 China Mobile / AS24445）时，极易误判为配置失败。

正确验证方式（任选其一）：
- **浏览器**打开 `https://ipinfo.io` 或 `https://ip.sb`；
- **curl 交叉验证**：`curl -x http://127.0.0.1:7890 https://ipinfo.io/json`（走代理端口）与 `curl https://ipinfo.io/json`（纯 TUN）应返回**同一个海外 IP**。

如果用户坚持面板显示不对，先让他点卡片刷新；详细排查流程见 `references/pitfalls.md` 第六节。