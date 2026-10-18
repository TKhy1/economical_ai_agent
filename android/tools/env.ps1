<#
.SYNOPSIS
在当前 PowerShell 会话里临时启用仓库内的 Android 工具链（不修改系统环境变量）。

.DESCRIPTION
工具链全部装在仓库内的 .toolchain\ 下，没有进系统 PATH，所以裸 `adb` / `gradle`
会提示「无法将 adb 项识别为 cmdlet」。两种用法：

    # 用法一：点号（dot-source）加载，之后本窗口内 adb / gradle / java 直接可用
    . .\android\tools\env.ps1
    adb devices

    # 用法二：不加载，直接用完整路径
    .\.toolchain\android-sdk\platform-tools\adb.exe devices

点号前面的「. 」不能省：没有它，脚本里的 $env:Path 改动只作用于脚本自己的子作用域，
当前窗口仍然找不到 adb。

关闭窗口或执行 Remove-Item Env:ANDROID_HOME 后即恢复原状，系统设置不受影响。
#>

$here = $PSScriptRoot
$repo = (Resolve-Path (Join-Path $here '..\..')).Path
$toolchain = Join-Path $repo '.toolchain'

$javaHome = Join-Path $toolchain 'jdk21'
$platformTools = Join-Path $toolchain 'android-sdk\platform-tools'
$gradleBin = Join-Path $toolchain 'gradle-8.13\bin'
$adb = Join-Path $platformTools 'adb.exe'

if (-not (Test-Path $adb)) {
    Write-Host '找不到 adb，请先运行：venv\Scripts\python.exe android\tools\fetch_toolchain.py' -ForegroundColor Yellow
    return
}

$env:JAVA_HOME = $javaHome
$env:ANDROID_HOME = Join-Path $toolchain 'android-sdk'
$env:ANDROID_SDK_ROOT = $env:ANDROID_HOME
$env:ANDROID_USER_HOME = Join-Path $toolchain 'android-user-home'
$env:GRADLE_USER_HOME = Join-Path $toolchain 'gradle-home'

# 只追加，不重复追加。用 [string]::Join 拼路径，避免数组被隐式转成字符串时
# 用空格当分隔符（那会拼出一整条无效 PATH，adb 就永远找不到）。
$wanted = @(
    [string](Join-Path $javaHome 'bin'),
    [string]$platformTools,
    [string]$gradleBin
)
$current = [string]$env:Path
$missing = @($wanted | Where-Object { $current.IndexOf($_, [StringComparison]::OrdinalIgnoreCase) -lt 0 })
if ($missing.Count -gt 0) {
    $env:Path = [string]::Join(';', $missing) + ';' + $current
}

Write-Host 'Android 工具链已在本会话启用：' -ForegroundColor Cyan
Write-Host ("  adb    : {0}" -f $adb)
Write-Host ("  java   : {0}" -f (Join-Path $javaHome 'bin\java.exe'))
Write-Host ("  gradle : {0}" -f (Join-Path $gradleBin 'gradle.bat'))
Write-Host ("  sdk    : {0}" -f $env:ANDROID_HOME)
Write-Host ''
Write-Host '现在可以直接用：adb devices / adb shell pm list packages com.campuswallet' -ForegroundColor Green
