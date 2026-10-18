<#
.SYNOPSIS
用仓库内自带的工具链构建 APK（不需要 Android Studio）。

.DESCRIPTION
1. 把 Gradle 的 HOME 指到 .toolchain/gradle-home，所有缓存都留在工作区内，
   否则 Gradle 会写 %USERPROFILE%\.gradle 而被沙箱拦截；
2. 用 .toolchain/jdk21（Gradle 8.13 不支持本机默认的 JDK 25）；
3. 指向 .toolchain/android-sdk（local.properties 也指向它）。

.PARAMETER Task
默认 assembleDebug。发布包用 assembleRelease。

.PARAMETER SkipSync
跳过 Python 源码同步（只改了 Java/资源时可以用）。
#>
param(
    [string]$Task = 'assembleDebug',
    [switch]$SkipSync,
    # 让 debug 包带上 tests/（M3 在手机上跑单元测试用；release 不受影响）
    [switch]$IncludeTests
)

$ErrorActionPreference = 'Stop'

$here = $PSScriptRoot
$repo = (Resolve-Path (Join-Path $here '..\..')).Path
$toolchain = Join-Path $repo '.toolchain'

if (-not $SkipSync) {
    & (Join-Path $here 'sync_python.ps1') -IncludeTests:$IncludeTests
}

$javaHome = Join-Path $toolchain 'jdk21'
$gradleHome = Join-Path $toolchain 'gradle-home'
$androidSdk = Join-Path $toolchain 'android-sdk'
# pip unpacks wheels into tempfile.mkdtemp() directories, which the sandbox makes
# unwritable; keep scratch space on a path that is known-writable and load the
# pyshim/sitecustomize.py that recreates those directories with mode 0o777.
$scratch = Join-Path $repo 'android\app\build\tmp'
$pythonShim = Join-Path $here 'pyshim'
$androidUserHome = Join-Path $toolchain 'android-user-home'

foreach ($path in @($javaHome, $gradleHome, $scratch, $androidUserHome,
                    (Join-Path $androidUserHome '.android'))) {
    New-Item -ItemType Directory -Path $path -Force | Out-Null
}

$env:PYTHONPATH = $pythonShim

$env:JAVA_HOME = $javaHome
$env:GRADLE_USER_HOME = $gradleHome
$env:ANDROID_HOME = $androidSdk
$env:ANDROID_SDK_ROOT = $androidSdk
# AGP writes its auto-generated debug keystore here; keep it in the workspace so
# the sandbox never blocks it and it never lands in the user profile.
# NOTE: only ANDROID_USER_HOME may be set - AGP refuses to start (see
# AndroidLocationsException) if the deprecated ANDROID_SDK_HOME is set at all,
# even to the very same directory.
$env:ANDROID_USER_HOME = $androidUserHome
$env:TEMP = $scratch
$env:TMP = $scratch
$env:Path = (Join-Path $javaHome 'bin') + ';' + (Join-Path $androidSdk 'platform-tools') + ';' + $env:Path

$gradle = Join-Path $toolchain 'gradle-8.13\bin\gradle.bat'
if (-not (Test-Path $gradle)) {
    throw "Gradle 未安装，请先运行: venv\Scripts\python.exe android\tools\fetch_toolchain.py"
}

Write-Host "JAVA_HOME   : $env:JAVA_HOME"
Write-Host "GRADLE_HOME : $env:GRADLE_USER_HOME"
Write-Host "ANDROID_SDK : $env:ANDROID_HOME"
Write-Host "task        : $Task"
Write-Host ''

& $gradle -p (Join-Path $here '..') $Task --no-daemon --console=plain --stacktrace
exit $LASTEXITCODE
