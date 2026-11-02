# FlClash 规则转换与真机测试避坑指南（实战记录）

## 一、 规则类型映射（Shadowrocket → FlClash）

| Shadowrocket | FlClash 配置语法 | 备注 |
|---|---|---|
| `DOMAIN-SUFFIX,x,Direct` | `DOMAIN-SUFFIX,x,DIRECT` | SR 大小写混用，转换时统一归一 |
| `DOMAIN-SUFFIX,x,Proxy` | `DOMAIN-SUFFIX,x,<主代理策略组>` | 策略组名必须与 base 配置完全一致 |
| `DOMAIN-SUFFIX,x,Reject` | `DOMAIN-SUFFIX,x,REJECT` | `sr_top500_whitelist_ad.conf` 约含 5.2 万条 |
| `IP-CIDR,x,Proxy` | `IP-CIDR,x,<主代理策略组>,no-resolve` | 必须统一补齐 `no-resolve`（部分 SR 规则自带，需先去重再补） |
| `GEOIP,CN,DIRECT` | `GEOIP,CN,DIRECT,no-resolve` | 依赖 `geoip.metadb`，放在国内分流段末尾 |
| `FINAL,proxy` | `MATCH,<主代理策略组>` | 必须放在 `rules:` 的最后一行 |

---

## 二、 四大 AI 服务（ChatGPT / Gemini / Antigravity / Muse）分流要点

1. **`antigravity.google` 必须单独声明**
   `antigravity.google` 挂在 **`.google` 顶级域**下，不是 `google.com` 的子域名，`DOMAIN-SUFFIX,google.com` 无法覆盖它。同理还有 `antigravity-unleash.goog`、`deepmind.google`、`generativeai.google`、`notebooklm.google`、`ai.google.dev` 等，都必须显式单列。
2. **核心 AI 专属域名 vs 生态公共域名的层级划分**
   - **第 2 层（最高优先级，排在去广告之前）**：核心 AI 专属域名，确保绝不被任何规则误杀或走直连：
     - **ChatGPT / OpenAI**：`openai.com`、`chatgpt.com`、`oaistatic.com`、`oaiusercontent.com`、`sora.com`、`livekit.cloud`
       （**不要加 `auth0.com`**：OpenAI 登录域是 `auth0.openai.com`，已被 `openai.com` 覆盖；单列 `auth0.com` 会把所有用 Auth0 登录的第三方 SaaS 一并送进代理）
     - **Gemini / Google AI**：`gemini.google.com`、`generativeai.google`、`deepmind.com`、`deepmind.google`、`ai.google.dev`、`google.dev`、`makersuite.google.com`、`aistudio.google.com`、`notebooklm.google.com`、`notebooklm.google`
     - **Antigravity**：`antigravity.google`、`antigravity-unleash.goog`
     - **Muse / Meta AI**：`muse.ai`、`meta.ai`
     - **关键词兜底**：`openai`、`generativelanguage`、`alkalimakersuite`、`antigravity`
   - **第 4 层（排在去广告 `Reject` 之后、直连白名单 `Direct` 之前）**：Google / Meta 生态公共域名：
     - `google.com`、`gstatic.com`、`googleusercontent.com`、`googleapis.com`、`ggpht.com`、`withgoogle.com`、`meta.com`、`fbcdn.net`、`facebook.com`、`fb.com`、`cdninstagram.com`、`instagram.com`
   - **为什么要这样分层**：
     - 如果把整个 `google.com` 放在去广告（第 3 层）之前，`adservice.google.com` 等广告域名会先命中代理规则，导致去广告失效；
     - 而把 `gstatic.com`、`googleusercontent.com` 放在直连白名单和 `cn-domain` / `GEOIP,CN` 之前，是因为它们在国内有 CDN 节点 IP，若排在后面会被国内规则抢成 `DIRECT` 直连，导致 Gemini / Antigravity 页面能打开但样式、头像或静态资源加载失败。

---

## 三、 第三步与第四步的环境衔接坑位（核心！）

### 1. 必须在第三步（开着代理时）把内核、`geoip.metadb` 和 `ads` 等规则集全部准备好
- **原因**：第四步要求**关闭电脑上的所有代理（退出 FlClash）**来测试新配置在真实无代理环境下的表现。如果在第三步漏下了 `geoip.metadb` 或 `rule-providers`（如 `ads`、`private`、`cn-domain`、`cn-ip`），第四步在断掉代理的纯直连环境下启动时，内核会尝试去 GitHub 下载它们，在国内网络下必然卡死或超时报错：
  ```text
  can't initial GeoIP: can't download MMDB ...
  rule set [ads] download failed ...
  ```
- **正确做法**：
  - 第三步趁着 FlClash 开启，运行 `setup_test_env.ps1 -Config <新配置.yaml>`，一次性把 `mihomo.exe`、`geoip.metadb` 以及配置文件 `rule-providers:` 里声明的所有规则集（特别是体积达 5.6MB / 19.5 万条的 `ads` 规则集）全部预下载到 `$env:TEMP\flclash_test_env` 下的对应路径（如 `rules/ads.yaml`）。
  - 第四步 `test_config.py` 启动时，通过 `copy_preloaded_env()` 把预下载好的 `geoip.metadb` 和整个 `rules/` 目录直接复制到测试沙盒目录，实现断网/纯直连状态下秒级加载。

### 2. `ads` 规则集体积大，加载需留足解析时间
- 订阅中的 `ads` 规则集通常包含约 19.5 万条规则。即便文件已经在第三步预下载到了本地磁盘，内核冷启动读取并解析这 19.5 万条规则加上 SR 的 5.2 万条内联 `Reject` 规则，也需要 **10~40 秒** 左右。
- 启动初期 API 返回 `ruleCount: 0` 属于正在解析的正常现象，`test_config.py` 会轮询 `/providers/rules` 直到所有规则集 `ruleCount > 0` 再开始发包探测。

### 3. 为什么第四步测试前必须彻底关闭 FlClash（关闭电脑所有代理）？
- **目的**：如果电脑上还开着旧的 FlClash（系统代理未关或开启了 TUN 虚拟网卡），测试进程发出的 `DIRECT` 直连流量或节点连接流量可能会被旧的 FlClash 再次劫持/套娃代理，根本测不出新配置文件在真实物理网络下是否会出现分流错误。
- **自动防护**：`test_config.py` 启动前会自动调用 `check_no_active_proxy()` 检查后台是否仍有 `FlClash.exe` / `FlClashCore.exe` 进程，以及注册表 `ProxyEnable` 是否为 `1`。如果没关干净，脚本会直接拦截并提醒先关闭 FlClash。

### 4. 关 TUN 时绝不能误伤 `dns.enable` 和 `sniffer.enable`（曾导致测试结论失真）
- **踩过的坑**：早期 `prepare_runtime_config()` 用全文正则 `^(\s*enable:\s*)true` 去关 TUN，但这份订阅配置**根本没有 `tun:` 段**，于是它关掉的是 `dns.enable` 和 `sniffer.enable`：
  - `enhanced-mode: fake-ip` 失效
  - `nameserver-policy`（cn-domain → 阿里 DoH）失效
  - `nameserver` 走代理的防污染配置失效
  - 结果：**测的是一份被偷偷改过 DNS 的配置**，结论不可信
- **正确做法**：按行扫描，只在 `tun:` 段作用域内替换 `enable`；配置里没有 `tun:` 段时补一段 `tun:\n  enable: false`。
- **验证方法**：跑完 `prepare_runtime_config()` 后 diff 一下，改动应当只有 `mixed-port`、新增的 `tun`、`external-controller`、`secret` 四处；出现 `enable: false` 就说明又误伤了。

---

## 四、 FlClash 专属机制与常见配置坑位

### 1. `private` 规则集的 `behavior` 必须为 `domain`
- 许多机场订阅里的 `private` 规则集内容是裸域名列表（如 `routerlogin.com`、`tplogin.cn`），却在 `rule-providers` 里错误声明为 `behavior: classical`。
- 在 FlClash 中，`classical` 要求每行带有 `DOMAIN-SUFFIX,` 等前缀，裸域名会导致整份规则集解析失败（`ruleCount: 0`）。`convert.py` 已内置 `fix_provider_behavior()`，自动将其修正为 `behavior: domain`。

### 2. 为什么测试沙盒要用标准 `mihomo.exe` 而不是 `FlClashCore.exe`？
- FlClash 底层使用的就是 Mihomo 内核，但客户端自带的 `FlClashCore.exe` 被封装成了 GUI 专用的 Socket 管道通信入口（`core.StartServer`），不支持命令行传入 `-t`（语法测试）或 `-f <config.yaml>`。
- 因此第三步准备环境时使用同源的标准 `mihomo.exe`，并可优先复用本机 FlClash 目录（`%APPDATA%\com.follow\clash\` 下的 `GEOIP.metadb` / `GEOSITE.dat` / `ASN.mmdb`）的数据文件。只要 `prepare_runtime_config()` 没有误改 `dns` / `sniffer`（见第三节第 4 条），测试出的分流结果与导入 FlClash 客户端一致。

### 3. 转换脚本 `convert.py` 绝不能退回的 5 个细节
1. **每条规则必须加双引号**：主代理策略组名通常含有 Emoji（如 `🚀 节点选择`，转义为 `\U0001F680`），在 YAML 中只有包裹在双引号 `"..."` 内才能被正确解析为 Emoji，不加引号会被当成字面反斜杠导致找不到策略组。
2. **`lan_sets` 只允许放局域网规则集（`private` / `lan` / `local`）**：原订阅里的 `cn-domain`、`cn-ip`、`direct` 等目标虽然也是 `DIRECT`，但绝不能放到顶部的局域网段，必须留到第 8 层「国内分流段」，否则会排在 AI 规则前面抢走流量。
3. **带 `no-resolve` 后缀的 SR 规则需先剥离后缀再分类**：SR 里部分 `IP-CIDR` 规则形如 `IP-CIDR,x.x.x.x/24,Proxy,no-resolve`，解析时需先识别末尾的 `no-resolve`，再取倒数第二项作为动作（`Proxy`/`Direct`/`Reject`），否则会被误判丢弃。
4. **跳过 SR 中直接写完整 URL 的 `RULE-SET`**：SR 支持 `RULE-SET,https://.../xxx.list,PROXY` 这种内联 URL，但 FlClash 要求所有 `RULE-SET` 必须先在 `rule-providers:` 中声明名称，直接转换会导致静态校验报错 `rule set not found`，应直接跳过。
5. **测试沙盒只关 `tun` 段内的 `enable`**：不能全文替换，否则会误关 `dns` 和 `sniffer`（见第三节第 4 条）。

### 4. PowerShell 函数用 `Write-Output` 打印进度会污染返回值
- **踩过的坑**：`setup_test_env.ps1` 的 `Get-Asset` 里用 `Write-Output` 打印"下载中"，调用方写的是 `if (-not (Get-Asset $url $out)) { throw ... }`。函数返回值实际是 `["下载中...", "失败: ...", $false]` 这样的**数组**，而非空数组恒为真，于是 `-not` 恒为假 —— **下载失败永远不触发 throw**，直接掉到下一行 `Get-Item` 报 `PathNotFound`，真实错误被彻底吞掉。
- **正确做法**：函数内部一律用 `Write-Host` 打印进度，保证 `return` 是唯一的输出流来源。
- **同类检查点**：凡是 `if (Some-Function ...)` 里包了带 `Write-Output` 的函数，都要怀疑一遍。

---

## 五、 分流测试结果判读指南

1. **连接返回 403 / 421 / 404 不等于分流失败**
   测试器通过 `CONNECT host:443` 触发流量路由，只要内核日志记录了：
   `[TCP] ... --> host:443 match <命中规则> using <实际出口>`
   且 `<实际出口>` 符合预期（AI 与外网域名走代理策略组，国内与白名单域名走 `DIRECT`，广告域名走 `REJECT`），即代表分流规则 100% 正确生效。
2. **一定要单独测 REJECT**：只测"该代理的走了代理"会漏掉去广告失效——`RULE-SET,ads` 如果被错误地指向代理组，广告域名照样能连通，测试却全绿。`test_config.py` 默认带 `doubleclick.net`、`adservice.google.com` 两条 REJECT 断言。
3. **出现 `<< 不符` 时的排查步骤**
   - 看测试输出表格里的 **「命中规则」** 一列：
     - 如果某个应走代理的域名显示命中了 `RuleSet(cn-domain)` 或 `GeoIP(CN)` 走了 `DIRECT`，说明该域名没有被前排的 AI 规则或代理规则覆盖，需将其后缀补充进 `convert.py` 的 `AI_CORE_SUFFIX` 或 `AI_ECO_SUFFIX` 中；
     - 如果某个应走直连的域名命中了 `Match` 走了代理，说明该域名不在 SR 白名单或 `cn-domain` 中，按需补充到直连白名单即可。
   - 修改 `convert.py` 后重新生成 YAML，再次运行 `test_config.py` 验证，直至所有测试域名全部显示 `PASS`。