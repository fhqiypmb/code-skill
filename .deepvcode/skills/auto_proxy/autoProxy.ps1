# ====================================
#   智能代理端口检测脚本 v2.0
#   自动检测并验证可用的 HTTP 代理端口
#   已修复：跳过 SOCKS5 端口，仅推荐 HTTP 代理
# ====================================

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "    智能代理端口检测工具 v2.0" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 步骤 1: 扫描所有监听在 127.0.0.1 的端口
Write-Host "[1/3] 🔍 扫描本地监听端口..." -ForegroundColor Yellow
$netstatOutput = netstat -ano | findstr "LISTENING" | findstr "127.0.0.1"
$ports = @()

if ($netstatOutput) {
    $netstatOutput | ForEach-Object {
        if ($_ -match '127\.0\.0\.1:(\d+)') {
            $port = $matches[1]
            if ($ports -notcontains $port) {
                $ports += $port
            }
        }
    }
    Write-Host "✓ 发现 $($ports.Count) 个监听端口" -ForegroundColor Green
} else {
    Write-Host "✗ 未发现任何监听端口" -ForegroundColor Red
    Write-Host ""
    Write-Host "请检查：" -ForegroundColor Yellow
    Write-Host "  1. 代理软件是否正在运行" -ForegroundColor Gray
    Write-Host "  2. 代理软件是否已启用本地监听" -ForegroundColor Gray
    exit
}

# 步骤 2: 测试每个端口是否是 HTTP 代理
Write-Host "`n[2/3] 🧪 测试端口代理功能..." -ForegroundColor Yellow
Write-Host "提示: 正在尝试通过每个端口访问 Google..." -ForegroundColor Gray
Write-Host "注意: 仅检测 HTTP/HTTPS 代理（SOCKS5 端口将被跳过）" -ForegroundColor Gray
Write-Host ""

$workingProxies = @()
$socksProxies = @()
$testUrl = "https://www.google.com"

# 已知的 SOCKS5 默认端口（跳过测试）
$knownSocksPorts = @("7891", "10809", "1080", "1081", "1088")

foreach ($port in $ports) {
    Write-Host "测试端口 $port ... " -NoNewline

    # 如果是已知的 SOCKS5 端口，直接跳过
    if ($knownSocksPorts -contains $port) {
        Write-Host "⊘ 跳过（SOCKS5 端口，PowerShell 不支持）" -ForegroundColor DarkGray
        $socksProxies += @{
            Port = $port
            Type = "SOCKS5"
            Url = "socks5://127.0.0.1:$port"
        }
        continue
    }

    # 测试 HTTP 代理
    $proxyUrl = "http://127.0.0.1:$port"
    try {
        $response = Invoke-WebRequest -Uri $testUrl -Proxy $proxyUrl -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
        if ($response.StatusCode -eq 200) {
            Write-Host "✓ HTTP 代理可用" -ForegroundColor Green
            $workingProxies += @{
                Port = $port
                Type = "HTTP"
                Url = $proxyUrl
            }
        }
    } catch {
        # 检查是否是 SOCKS5 代理的错误
        if ($_.Exception.Message -like "*socks5*" -or $_.Exception.Message -like "*SOCKS*") {
            Write-Host "⊘ SOCKS5 代理（PowerShell 不支持）" -ForegroundColor DarkGray
            $socksProxies += @{
                Port = $port
                Type = "SOCKS5"
                Url = "socks5://127.0.0.1:$port"
            }
        } else {
            Write-Host "✗ 非代理端口或无法连接" -ForegroundColor Red
        }
    }
}

# 步骤 3: 显示结果
Write-Host "`n[3/3] 📋 检测结果汇总" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Cyan

if ($workingProxies.Count -eq 0 -and $socksProxies.Count -eq 0) {
    Write-Host ""
    Write-Host "✗ 未发现任何代理端口" -ForegroundColor Red
    Write-Host ""
    Write-Host "可能的原因：" -ForegroundColor Yellow
    Write-Host "  1. 代理软件未运行" -ForegroundColor Gray
    Write-Host "  2. 代理软件未启用 HTTP 代理模式" -ForegroundColor Gray
    Write-Host "  3. 网络连接问题" -ForegroundColor Gray
    Write-Host ""
    Write-Host "建议操作：" -ForegroundColor Yellow
    Write-Host "  1. 检查 Clash/V2rayN 等代理软件是否运行" -ForegroundColor Gray
    Write-Host "  2. 确认代理软件的 HTTP 代理已启用" -ForegroundColor Gray
    Write-Host "  3. 查看代理软件设置中的端口号" -ForegroundColor Gray
    exit
}

# 显示 HTTP 代理
if ($workingProxies.Count -gt 0) {
    Write-Host ""
    Write-Host "✓ 发现 $($workingProxies.Count) 个可用的 HTTP 代理：" -ForegroundColor Green
    Write-Host ""

    $index = 1
    foreach ($proxy in $workingProxies) {
        Write-Host "  [$index] 端口: $($proxy.Port)" -ForegroundColor Cyan
        Write-Host "      类型: $($proxy.Type)" -ForegroundColor White
        Write-Host "      地址: $($proxy.Url)" -ForegroundColor White
        Write-Host ""
        $index++
    }
} else {
    Write-Host ""
    Write-Host "⚠ 未发现可用的 HTTP 代理" -ForegroundColor Yellow
    Write-Host ""
}

# 显示 SOCKS5 代理（仅供参考）
if ($socksProxies.Count -gt 0) {
    Write-Host "ℹ 发现 $($socksProxies.Count) 个 SOCKS5 代理（PowerShell 不支持，但可用于其他工具）：" -ForegroundColor DarkGray
    Write-Host ""

    foreach ($proxy in $socksProxies) {
        Write-Host "  • 端口: $($proxy.Port) - $($proxy.Url)" -ForegroundColor DarkGray
    }
    Write-Host ""
    Write-Host "  提示: SOCKS5 代理可用于 Git、curl、浏览器等工具" -ForegroundColor DarkGray
    Write-Host "        但 PowerShell 的 Invoke-WebRequest 不支持" -ForegroundColor DarkGray
    Write-Host ""
}

# 如果没有 HTTP 代理，退出
if ($workingProxies.Count -eq 0) {
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "建议：请在代理软件中启用 HTTP 代理模式" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Clash for Windows:" -ForegroundColor Cyan
    Write-Host "  General → Port (默认 7890 是 HTTP 端口)" -ForegroundColor Gray
    Write-Host ""
    Write-Host "V2rayN:" -ForegroundColor Cyan
    Write-Host "  参数设置 → 本地监听端口 (默认 10808 是 HTTP 端口)" -ForegroundColor Gray
    Write-Host ""
    exit
}

# 步骤 4: 推荐配置
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "🎯 推荐配置（使用第一个可用的 HTTP 代理）" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Cyan

$recommended = $workingProxies[0]
Write-Host ""
Write-Host "推荐代理: $($recommended.Url)" -ForegroundColor Green
Write-Host ""
Write-Host "临时配置命令（仅当前会话有效）：" -ForegroundColor Cyan
Write-Host "  `$env:HTTP_PROXY='$($recommended.Url)'" -ForegroundColor White
Write-Host "  `$env:HTTPS_PROXY='$($recommended.Url)'" -ForegroundColor White

Write-Host ""
Write-Host "永久配置命令（需管理员权限，重启终端生效）：" -ForegroundColor Cyan
Write-Host "  [System.Environment]::SetEnvironmentVariable('HTTP_PROXY', '$($recommended.Url)', 'User')" -ForegroundColor White
Write-Host "  [System.Environment]::SetEnvironmentVariable('HTTPS_PROXY', '$($recommended.Url)', 'User')" -ForegroundColor White

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan

# 步骤 5: 询问是否立即配置
Write-Host ""
$choice = Read-Host "是否立即配置推荐的代理到当前会话？(Y/N)"

if ($choice -eq 'Y' -or $choice -eq 'y') {
    $env:HTTP_PROXY = $recommended.Url
    $env:HTTPS_PROXY = $recommended.Url

    Write-Host ""
    Write-Host "✓ 代理已配置：$($recommended.Url)" -ForegroundColor Green
    Write-Host "⚠ 此配置仅在当前终端会话有效，关闭终端后失效" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "正在验证代理..." -ForegroundColor Cyan

    try {
        $testResponse = Invoke-WebRequest -Uri "https://www.google.com" -Proxy $env:HTTP_PROXY -TimeoutSec 10 -UseBasicParsing
        Write-Host "✓ 代理工作正常！可以正常访问 Google" -ForegroundColor Green

        # 显示当前 IP
        Write-Host ""
        Write-Host "尝试获取代理后的 IP 地址..." -ForegroundColor Cyan
        try {
            $ipInfo = Invoke-WebRequest -Uri "http://ip-api.com/json" -Proxy $env:HTTP_PROXY -UseBasicParsing -TimeoutSec 10 | ConvertFrom-Json
            Write-Host "✓ 当前 IP: $($ipInfo.query)" -ForegroundColor Green
            Write-Host "  位置: $($ipInfo.country), $($ipInfo.city)" -ForegroundColor Gray
        } catch {
            Write-Host "⚠ 无法获取 IP 信息（这不影响代理使用）" -ForegroundColor Yellow
        }
    } catch {
        Write-Host "✗ 代理验证失败：$($_.Exception.Message)" -ForegroundColor Red
        Write-Host ""
        Write-Host "可能的原因：" -ForegroundColor Yellow
        Write-Host "  1. 代理软件的节点未连接" -ForegroundColor Gray
        Write-Host "  2. 选择的节点不可用" -ForegroundColor Gray
        Write-Host "  3. 网络连接问题" -ForegroundColor Gray
    }

    Write-Host ""
    Write-Host "当前环境变量配置：" -ForegroundColor Cyan
    Write-Host "  HTTP_PROXY  = $env:HTTP_PROXY" -ForegroundColor White
    Write-Host "  HTTPS_PROXY = $env:HTTPS_PROXY" -ForegroundColor White
} else {
    Write-Host ""
    Write-Host "提示: 你可以手动复制上面的命令进行配置" -ForegroundColor Gray
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "检测完成！" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 显示使用提示
if ($workingProxies.Count -gt 1) {
    Write-Host "💡 提示: 发现多个可用代理，如果第一个代理速度慢，可以尝试其他代理：" -ForegroundColor Yellow
    $index = 1
    foreach ($proxy in $workingProxies) {
        Write-Host "  $index. `$env:HTTP_PROXY='$($proxy.Url)'; `$env:HTTPS_PROXY='$($proxy.Url)'" -ForegroundColor Gray
        $index++
    }
    Write-Host ""
}
