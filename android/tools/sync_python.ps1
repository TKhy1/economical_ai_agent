<#
.SYNOPSIS
同步 Python 应用到 Android 模块的 python 源码集（白名单，绝不整目录拷贝）。

.DESCRIPTION
Chaquopy 会把 src/main/python 下的所有文件打包进 APK：.py 编译成字节码，
templates/ 与 static/ 作为数据文件在首次启动时解压到磁盘（Flask 的
render_template 与 static 路由就是靠这个工作的）。

这里用显式白名单，因为一旦直接把仓库根目录当成 srcDir：
  * venv/ 会被打进去（几十 MB，还带 Windows 的 .pyd）；
  * .env 会被打进去（里面有真实 DeepSeek Key）；
  * economical_agent.db、__pycache__/*.pyc 会被打进去（.pyc 的 magic number
    与目标解释器绑定，跨版本会失效）。

.PARAMETER IncludeTests
同时把 tests/ 拷进去（debug 构建里做"手机上跑单元测试"的自检才算得上有用）。
#>
param(
    [switch]$IncludeTests
)

$ErrorActionPreference = 'Stop'

$here = $PSScriptRoot
$repo = (Resolve-Path (Join-Path $here '..\..')).Path
$dest = [System.IO.Path]::GetFullPath((Join-Path $here '..\app\src\main\python'))

# 参与运行时的模块；check_llm.py 是桌面自检脚本，不进 APK。
$modules = @(
    'app.py',
    'agent.py',
    'ledger.py',
    'limits.py',
    'config.py',
    'llm_agent.py',
    'seed_demo_data.py'
)

Write-Host "repo : $repo"
Write-Host "dest : $dest"

if (Test-Path $dest) {
    Remove-Item $dest -Recurse -Force
}
New-Item -ItemType Directory -Path $dest -Force | Out-Null

foreach ($module in $modules) {
    Copy-Item (Join-Path $repo $module) (Join-Path $dest $module)
}
Copy-Item (Join-Path $repo 'templates') (Join-Path $dest 'templates') -Recurse
Copy-Item (Join-Path $repo 'static') (Join-Path $dest 'static') -Recurse
Copy-Item (Join-Path $here '..\python\android_boot.py') (Join-Path $dest 'android_boot.py')

if ($IncludeTests) {
    # 测试只进 debug 变体：Chaquopy 会扫描 src/debug/python，release 包不会有它。
    #
    # 为什么要包一层 selftest/ 包（带 __init__.py）：
    # Chaquopy 把 .py 当模块直接从 APK 里导入，永远不解压到磁盘；只有声明了
    # extractPackages 的「包」才会在首次 import 时解压成真实文件。而
    # unittest.discover 需要磁盘上真实存在的 test_*.py。所以这里生成一个包，
    # 让 run_tests() 先 import selftest 触发解压，再在解压出来的目录上做发现。
    $debugDest = [System.IO.Path]::GetFullPath((Join-Path $here '..\app\src\debug\python'))
    if (Test-Path $debugDest) {
        Remove-Item $debugDest -Recurse -Force
    }
    $testsDest = Join-Path $debugDest 'selftest'
    New-Item -ItemType Directory -Path $testsDest -Force | Out-Null
    Set-Content -Path (Join-Path $testsDest '__init__.py') -Value '' -NoNewline -Encoding utf8
    Copy-Item (Join-Path $repo 'tests\*.py') $testsDest
    # test_llm_agent.py imports the desktop self-check script, so it has to be part
    # of the debug package too - otherwise that module fails to import and its 63
    # tests never run (140 of 203 was exactly that).
    Copy-Item (Join-Path $repo 'check_llm.py') $testsDest
    $debugCount = (Get-ChildItem $debugDest -Recurse -File | Measure-Object).Count
    Write-Host ("self-test package -> src/debug/python/selftest: {0} files" -f $debugCount)
} else {
    $debugDest = [System.IO.Path]::GetFullPath((Join-Path $here '..\app\src\debug\python'))
    if (Test-Path $debugDest) {
        Remove-Item $debugDest -Recurse -Force
        Write-Host 'self-test package removed from src/debug/python'
    }
}

# 清掉可能被递归拷贝带进来的缓存
Get-ChildItem $dest -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-Item $_.FullName -Recurse -Force }
Get-ChildItem $dest -Recurse -File -Include '*.pyc', '*.pyo' -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-Item $_.FullName -Force }

# 安全闸门：密钥、本地数据库、桌面专用脚本都不允许进 APK
$forbidden = @('.env', 'economical_agent.db', 'check_llm.py', 'requirements.txt', 'requirements.lock.txt')
foreach ($name in $forbidden) {
    if (Test-Path (Join-Path $dest $name)) {
        throw "禁止打包的文件出现在 python 源码集里: $name"
    }
}

$summary = Get-ChildItem $dest -Recurse -File | Group-Object Extension |
    Sort-Object Name |
    ForEach-Object { "{0}={1}" -f ($_.Name -replace '^$', '(no ext)'), $_.Count }
$total = (Get-ChildItem $dest -Recurse -File | Measure-Object).Count
Write-Host ("synced {0} files  ({1})" -f $total, ($summary -join ', '))
