<#
.SYNOPSIS
M0 真机验证脚本：查设备、查页大小、装 APK、抓日志、开端口转发。

.DESCRIPTION
按顺序做四件事：
  1. 确认 adb 能看到设备（连不上时打印荣耀需要打开的开关）；
  2. 打印机型 / Android 版本 / ABI / **页大小**（16KB 页设备是老式 4KB 对齐
     .so 的硬门槛，M0 必须先确认这一点）；
  3. 安装并启动 APK；
  4. 抓 CampusWallet、python.stdout/stderr、AndroidRuntime 的日志。

.PARAMETER Install
安装并启动 APK。

.PARAMETER Apk
指定 APK 路径，默认取 debug 产物。

.PARAMETER Tail
持续跟踪日志（Ctrl+C 结束）。
#>
param(
    [switch]$Install,
    [string]$Apk = '',
    [switch]$Tail
)

$ErrorActionPreference = 'Stop'

$here = $PSScriptRoot
$repo = (Resolve-Path (Join-Path $here '..\..')).Path
$toolchain = Join-Path $repo '.toolchain'
$adb = Join-Path $toolchain 'android-sdk\platform-tools\adb.exe'
$package = 'com.campuswallet.demo'

if (-not (Test-Path $adb)) {
    throw "找不到 adb，请先运行: venv\Scripts\python.exe android\tools\fetch_toolchain.py"
}

& $adb start-server | Out-Null

$attached = (& $adb devices) | Select-Object -Skip 1 | Where-Object { $_ -match '\sdevice$' }
if (-not $attached) {
    Write-Host '没有检测到设备。请依次确认：' -ForegroundColor Yellow
    Write-Host '  1. USB 线已连接，并在手机通知栏选择「传输文件」（不要「仅充电」）'
    Write-Host '  2. 设置 → 关于手机 → 版本号 连点 7 次，打开「开发者选项」'
    Write-Host '  3. 开发者选项 → 打开「USB 调试」（荣耀可能还要打开「仅充电时允许调试」）'
    Write-Host '  4. 手机会弹「允许 USB 调试吗？」，勾选「一直允许」并确定'
    exit 1
}

Write-Host '=== 设备 ===' -ForegroundColor Cyan
& $adb shell 'getprop ro.product.manufacturer; getprop ro.product.model; getprop ro.build.version.release; getprop ro.build.version.sdk; getprop ro.product.cpu.abilist'

Write-Host ''
Write-Host '=== 页大小 ===' -ForegroundColor Cyan
$pageSize = (& $adb shell 'getconf PAGE_SIZE' 2>&1) | Select-Object -First 1
if ($pageSize -match '^\d+$') {
    Write-Host ("PAGE_SIZE = {0}" -f $pageSize.Trim())
    if ([int]$pageSize.Trim() -eq 16384) {
        Write-Host '注意：这是 16KB 页设备，4KB 对齐的原生库会加载失败。' -ForegroundColor Yellow
        Write-Host '      M0 若在启动阶段崩溃，先看 logcat 里的 linker 报错，再决定是否切 Python 3.13。' -ForegroundColor Yellow
    } else {
        Write-Host '4KB 页：16KB 兼容性风险与本机无关，该项可以永久关闭。' -ForegroundColor Green
    }
} else {
    Write-Host '设备上没有 getconf，安装后用 App 自检页的 page_size 字段确认。' -ForegroundColor Yellow
}

if ($Install) {
    $apkPath = if ($Apk) { $Apk } else {
        Join-Path $repo 'android\app\build\outputs\apk\debug\app-debug.apk'
    }
    if (-not (Test-Path $apkPath)) {
        throw "找不到 APK: $apkPath（先运行 android\tools\build.ps1）"
    }

    Write-Host ''
    Write-Host "=== 安装 $apkPath ===" -ForegroundColor Cyan
    & $adb install -r $apkPath
    if ($LASTEXITCODE -ne 0) {
        Write-Host '安装失败：荣耀/MagicOS 常见原因是「纯净模式」。' -ForegroundColor Yellow
        Write-Host '  设置 → 系统和更新 → 纯净模式 → 关闭「增强防护」，然后重试。' -ForegroundColor Yellow
        exit $LASTEXITCODE
    }

    Write-Host ''
    Write-Host '=== 启动 ===' -ForegroundColor Cyan
    & $adb shell am start -n "$package/.MainActivity" | Out-Null
    Start-Sleep -Seconds 8

    Write-Host ''
    Write-Host '=== 端口转发（PC 上访问手机里的 Flask） ===' -ForegroundColor Cyan
    & $adb forward tcp:5000 tcp:5000 | Out-Null
    try {
        $health = Invoke-WebRequest -Uri 'http://127.0.0.1:5000/healthz' -UseBasicParsing -TimeoutSec 5
        Write-Host ('http://127.0.0.1:5000/healthz -> ' + $health.StatusCode)
        Write-Host $health.Content
    } catch {
        Write-Host '端口 5000 上没有响应（可能被其它进程占用，App 自动换了端口）。' -ForegroundColor Yellow
        Write-Host '以 logcat 里 "python server ready on 127.0.0.1:<port>" 的实际端口为准，或者看 App 自检页。' -ForegroundColor Yellow
    }
}

Write-Host ''
Write-Host '=== 日志 ===' -ForegroundColor Cyan
$logArgs = @('-d', '-v', 'time', '-s',
    'CampusWallet:V', 'python.stdout:V', 'python.stderr:V',
    'AndroidRuntime:E', 'ActivityManager:I')
if ($Tail) {
    & $adb logcat -v time -s CampusWallet:V python.stdout:V python.stderr:V AndroidRuntime:E
} else {
    & $adb @logArgs | Select-Object -Last 100
}
