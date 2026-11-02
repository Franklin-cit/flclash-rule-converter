# 准备 FlClash 配置测试环境：内核 + geoip/geosite + 所有 rule-providers (含 ads 规则集)
#
# 用法:
#   powershell -ExecutionPolicy Bypass -File scripts/setup_test_env.ps1 -Config flclash_converted.yaml

param(
    [string]$Config = "",
    [string]$Dir = "$env:TEMP\flclash_test_env",
    [string]$Version = "v1.19.33",
    [int]$ProxyPort = 0
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

Write-Output "== 准备 FlClash 测试环境目录: $Dir =="
New-Item -ItemType Directory -Force -Path $Dir | Out-Null

# ---------- 1. 自动探测当前 FlClash 代理端口 ----------
$candidatePorts = @()
if ($ProxyPort -gt 0) { $candidatePorts += $ProxyPort }

if ($Config -and (Test-Path $Config)) {
    $cfgText = Get-Content $Config -Raw -Encoding UTF8
    if ($cfgText -match "(?m)^mixed-port:\s*(\d+)") {
        $candidatePorts += [int]$Matches[1]
    }
}

try {
    $reg = Get-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings" -ErrorAction SilentlyContinue
    if ($reg.ProxyEnable -eq 1 -and $reg.ProxyServer -match ":(\d+)") {
        $candidatePorts += [int]$Matches[1]
    }
} catch {}

$candidatePorts += @(7890, 7897, 7891, 10808)
$candidatePorts = $candidatePorts | Select-Object -Unique

$proxy = $null
foreach ($p in $candidatePorts) {
    $tcp = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $tcp.BeginConnect("127.0.0.1", $p, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne(500, $false) -and $tcp.Connected) {
            $proxy = "http://127.0.0.1:$p"
            break
        }
    } catch {} finally {
        $tcp.Close()
    }
}

if ($proxy) {
    Write-Output "检测到正在运行的代理: $proxy（将通过代理高速拉取全部环境依赖）"
} else {
    Write-Output "警告: 未检测到本机代理！建议先打开 FlClash 再运行本脚本，否则将尝试使用镜像下载。"
}

function Get-Asset($url, $outFile) {
    $parent = Split-Path -Parent $outFile
    if ($parent -and -not (Test-Path $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    $targets = @()
    if ($proxy) { $targets += $url }
    if ($url -match "^https://(raw\.githubusercontent\.com|github\.com)/") {
        $targets += ("https://ghproxy.net/" + $url)
        $targets += ("https://gh-proxy.com/" + $url)
    }
    if (-not $proxy) { $targets += $url }

    # 注意：本函数只能通过 return 输出布尔值。
    # 若用 Write-Output 打印进度，调用方 if (-not (Get-Asset ...)) 拿到的是
    # 非空数组（恒为真），下载失败也不会触发 throw，错误会被静默吞掉。
    foreach ($u in $targets) {
        try {
            Write-Host "  下载: $u -> $outFile"
            if ($proxy -and $u -notmatch "ghproxy\.net|gh-proxy\.com") {
                Invoke-WebRequest -Uri $u -OutFile $outFile -Proxy $proxy -UseBasicParsing -TimeoutSec 300
            } else {
                Invoke-WebRequest -Uri $u -OutFile $outFile -UseBasicParsing -TimeoutSec 300
            }
            if ((Test-Path $outFile) -and (Get-Item $outFile).Length -gt 0) {
                return $true
            }
        } catch {
            Write-Host "    失败: $($_.Exception.Message)"
            Remove-Item $outFile -Force -ErrorAction SilentlyContinue
        }
    }
    return $false
}

# ---------- 2. 准备测试内核 ----------
$exe = Join-Path $Dir "mihomo.exe"
if (Test-Path $exe) {
    Write-Output "== 测试内核已就绪，跳过下载 =="
} else {
    $zip = Join-Path $Dir "mihomo.zip"
    $url = "https://github.com/MetaCubeX/mihomo/releases/download/$Version/mihomo-windows-amd64-$Version.zip"
    if (-not (Get-Asset $url $zip)) {
        throw "内核下载失败，请确认 FlClash 代理已开启！"
    }
    Expand-Archive -Path $zip -DestinationPath $Dir -Force
    $raw = Get-ChildItem $Dir -Filter "mihomo-windows-amd64*.exe" | Select-Object -First 1
    if ($raw) { Move-Item $raw.FullName $exe -Force }
    Remove-Item $zip -Force -ErrorAction SilentlyContinue
}
Write-Output ("  内核版本: " + (& $exe -v | Select-Object -First 1))

# ---------- 3. 准备 geoip.metadb（优先复用本机 FlClash 数据） ----------
$flclashDataDir = Join-Path $env:APPDATA "com.follow\clash"
$flclashGeo = Join-Path $flclashDataDir "geoip.metadb"
$geo = Join-Path $Dir "geoip.metadb"

if ((Test-Path $geo) -and (Get-Item $geo).Length -gt 1MB) {
    Write-Output "== geoip.metadb 已存在，跳过下载 =="
} elseif ((Test-Path $flclashGeo) -and (Get-Item $flclashGeo).Length -gt 1MB) {
    Copy-Item $flclashGeo $geo -Force
    Write-Output "== 已直接复用本机 FlClash 的 geoip.metadb =="
} else {
    $url = "https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/geoip.metadb"
    if (-not (Get-Asset $url $geo)) {
        throw "geoip.metadb 下载失败！"
    }
}
Write-Output ("  GeoIP 数据: {0:N1} MB" -f ((Get-Item $geo).Length / 1MB))

# 若本机 FlClash 有 geosite.dat / ASN.mmdb 也一并复用
foreach ($extra in @("geosite.dat", "ASN.mmdb", "country.mmdb")) {
    $srcExtra = Join-Path $flclashDataDir $extra
    $dstExtra = Join-Path $Dir $extra
    if ((Test-Path $srcExtra) -and -not (Test-Path $dstExtra)) {
        Copy-Item $srcExtra $dstExtra -Force
    }
}

# ---------- 4. 预下载配置文件中的所有 rule-providers（兼容多行与单行格式） ----------
if ($Config -and (Test-Path $Config)) {
    Write-Output "== 解析并预下载 rule-providers 规则集（含 ads 等） =="
    $lines = Get-Content $Config -Encoding UTF8
    $inProviders = $false
    $providerIndent = -1
    $curName = ""
    $curUrl = ""
    $curPath = ""
    $providers = @()

    foreach ($ln in $lines) {
        if ($ln -match "^rule-providers:\s*(#.*)?$") {
            $inProviders = $true
            continue
        }
        if ($inProviders -and $ln -match "^[^\s#]") {
            $inProviders = $false
        }
        if ($inProviders) {
            if ($ln -match "^\s*#" -or $ln -match "^\s*$") { continue }

            # 匹配单行格式: name: {..., url: ..., path: ...}
            if ($ln -match "^\s+([a-zA-Z0-9_-]+):\s*\{(.+)\}") {
                if ($curName -and $curUrl) {
                    $providers += [PSCustomObject]@{ Name=$curName; Url=$curUrl; Path=$curPath }
                    $curName = ""; $curUrl = ""; $curPath = ""
                }
                $pName = $Matches[1]
                $body = $Matches[2]
                $pUrl = ""
                $pPath = "./rules/$pName.yaml"
                if ($body -match "url:\s*[`"']?([^`"'\s,}]+)[`"']?") { $pUrl = $Matches[1] }
                if ($body -match "path:\s*[`"']?([^`"'\s,}]+)[`"']?") { $pPath = $Matches[1] }
                if ($pUrl) {
                    $providers += [PSCustomObject]@{ Name=$pName; Url=$pUrl; Path=$pPath }
                }
                continue
            }

            # 匹配多行格式的 provider 名称行
            if ($ln -match "^(\s+)([a-zA-Z0-9_-]+):\s*(#.*)?$") {
                $indent = $Matches[1].Length
                if ($providerIndent -eq -1 -or $indent -eq $providerIndent) {
                    $providerIndent = $indent
                    if ($curName -and $curUrl) {
                        $providers += [PSCustomObject]@{ Name=$curName; Url=$curUrl; Path=$curPath }
                    }
                    $curName = $Matches[2]
                    $curUrl = ""
                    $curPath = "./rules/$curName.yaml"
                    continue
                }
            }
            if ($ln -match "^\s+url:\s*[`"']?([^`"'\s#]+)[`"']?") {
                $curUrl = $Matches[1]
            } elseif ($ln -match "^\s+path:\s*[`"']?([^`"'\s#]+)[`"']?") {
                $curPath = $Matches[1]
            }
        }
    }
    if ($curName -and $curUrl) {
        $providers += [PSCustomObject]@{ Name=$curName; Url=$curUrl; Path=$curPath }
    }

    foreach ($p in $providers) {
        $relPath = $p.Path -replace "^\./", "" -replace "/", "\"
        $targetFile = Join-Path $Dir $relPath
        if ((Test-Path $targetFile) -and (Get-Item $targetFile).Length -gt 100) {
            $sizeKB = (Get-Item $targetFile).Length / 1KB
            Write-Output ("  规则集 [{0}] 已就绪 ({1:N1} KB)" -f $p.Name, $sizeKB)
        } else {
            Write-Output ("  正在预下载规则集 [{0}] ..." -f $p.Name)
            if (-not (Get-Asset $p.Url $targetFile)) {
                throw "规则集 [$($p.Name)] 下载失败，请确保代理畅通后重试！"
            }
            $sizeKB = (Get-Item $targetFile).Length / 1KB
            Write-Output ("  规则集 [{0}] 下载完成 ({1:N1} KB)" -f $p.Name, $sizeKB)
        }
    }
} else {
    Write-Output "提示: 未通过 -Config 指定配置文件，跳过 rule-providers 预下载"
}

Write-Output ""
Write-Output "== 所有环境准备完毕 =="
Write-Output "  环境目录: $Dir"
Write-Output "  内核路径: $exe"
Write-Output "  GeoIP库 : $geo"
Write-Output ""
Write-Output "【下一步重要操作】："
Write-Output "1. 请现在彻底关闭 FlClash（关闭电脑上的所有代理与 TUN 模式）"
Write-Output "2. 关闭后运行第四步真实环境测试："
Write-Output "   python scripts/test_config.py --config `"$Config`" --env-dir `"$Dir`""