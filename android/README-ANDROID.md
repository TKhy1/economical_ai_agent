# CampusWallet · Android 测试包

> 目标设备：荣耀 400 Pro（MagicOS 10 / Android 16 / arm64-v8a）
> 形态：**Chaquopy 内嵌 CPython 3.12 + 本地 Werkzeug 服务 + WebView**，业务代码零重写。
> 当前状态：**M4 完成 · 可直接安装使用**
>
> | 交付物 | 位置 |
> |---|---|
> | 安装包（20.8 MB，已签名） | [`dist/CampusWallet-1.0.0-arm64.apk`](../dist/CampusWallet-1.0.0-arm64.apk) |
> | 安装说明（Markdown） | [INSTALL.md](INSTALL.md) |
> | 安装说明卡片（可打印 HTML） | [install-card.html](install-card.html) |

---

## 1. 一键构建

```powershell
# 只需一次：把 JDK21 / Gradle 8.13 / Android SDK 36 装进 .toolchain（约 450 MB）
venv\Scripts\python.exe android\tools\fetch_toolchain.py

# 每次构建
powershell -ExecutionPolicy Bypass -File android\tools\build.ps1              # debug
powershell -ExecutionPolicy Bypass -File android\tools\build.ps1 -Task assembleRelease
```

产物：

| 构建 | 路径 | 大小 |
|---|---|---|
| release（交付用，已签名） | `dist\CampusWallet-1.0.0-arm64.apk` | 20.8 MB |
| release（原始产物） | `android\app\build\outputs\apk\release\app-release.apk` | 20.8 MB |
| debug（含自检入口与 tests/） | `android\app\build\outputs\apk\debug\app-debug.apk` | 21.9 MB |

构建脚本会自动：

- 把 Gradle 的 HOME 指向 `.toolchain\gradle-home`（否则会写 `%USERPROFILE%\.gradle`，被沙箱拦截）；
- 用 `.toolchain\jdk21`（本机默认 JDK 25 太新，Gradle 8.13 不支持）；
- 把 `ANDROID_USER_HOME` 指向工作区内（debug keystore 也留在里面）；
- 设置 `PYTHONPATH=android/tools/pyshim`（见第 5 节第 4 条）；
- release 用 `android/keystore.properties` 里的自签名密钥（缺失时回退 debug 签名）。

---

## 2. 安装到手机

### 路线 A：adb（推荐）

```powershell
powershell -ExecutionPolicy Bypass -File android\tools\device_check.ps1 -Install
```

手机会弹「允许 USB 调试吗？」，勾选**一直允许**。

### 路线 B：不接电脑

把 `app-debug.apk` 拷到手机（USB 文件传输 / 微信文件传输助手 / 网盘），用「文件管理」点击安装。

### 荣耀 / MagicOS 专项开关

| 目的 | 位置 |
|---|---|
| 打开开发者选项 | 设置 → 关于手机 → **版本号连点 7 次** |
| 允许 adb | 设置 → 系统和更新 → 开发人员选项 → **USB 调试** |
| 允许电脑安装 | 部分机型还需打开「仅充电时允许调试」 |
| 解除安装拦截 | 设置 → 系统和更新 → **纯净模式** → 关闭「增强防护」（否则安装界面可能连按钮都没有） |
| 未知来源 | 首次安装会询问「允许此来源安装应用」，选允许；应用市场提示「未经检测」时勾选「已了解」继续 |

---

## 3. 验证 M0 是否成功

打开 App，几秒后应出现一张自检页，逐项核对：

| 检查项 | 期望值 |
|---|---|
| 顶部徽标 | **自检通过**（绿）；若是红色说明 `templates/` 或 `static/` 没被解压出来 |
| python / implementation | `3.12.x` / `CPython` |
| sqlite | 有版本号（内置 SQLite 生效） |
| **page_size** | `4096`（4KB 页，16KB 风险关闭）或 `16384`（需评估） |
| port | 5000，或系统分配的其它端口 |
| 打包的依赖 | flask 3.1.3 / werkzeug 3.1.9 / jinja2 3.1.6 / markupsafe 3.0.3 |
| data_files | 四个文件都有字节数（不是「缺失」） |
| `module_dir` | 形如 `/data/user/0/com.campuswallet.demo/files/chaquopy/...` |

命令行复核（PC 侧直接打手机里的 Flask）：

```powershell
adb forward tcp:5000 tcp:5000
curl.exe http://127.0.0.1:5000/healthz
```

日志：

```powershell
adb logcat -s CampusWallet:V python.stdout:V python.stderr:V AndroidRuntime:E
```

关键行：`python server ready on 127.0.0.1:5000 after 1832 ms`。

---

## 4. 工程结构

```
android/
├─ settings.gradle / build.gradle / gradle.properties   构建配置（Groovy，无 Kotlin 插件）
├─ local.properties                由 fetch_toolchain.py 生成（不入库）
├─ app/
│  ├─ build.gradle                 AGP 8.13 + Chaquopy 17 + Python 3.12 + flask pin
│  └─ src/main/
│     ├─ AndroidManifest.xml       INTERNET、NSC、竖屏、adjustResize
│     ├─ java/com/campuswallet/demo/
│     │  ├─ App.java               Application.onCreate → Python.start（主线程，仅一次）
│     │  ├─ MainActivity.java      全屏 WebView + insets + 加载/错误遮罩 + 返回键
│     │  └─ PyServer.java          Kotlin→Python 桥：起服务、拿端口
│     ├─ res/                      主题、图标（矢量 ¥）、network_security_config
│     └─ python/                   由 sync_python.ps1 生成（不入库）
├─ python/android_boot.py          Python 启动入口（M0：自检服务；M1：真实 app）
└─ tools/
   ├─ fetch_toolchain.py           下载 JDK/Gradle/SDK（可换镜像）
   ├─ sync_python.ps1              白名单同步 Python 源码与资源
   ├─ build.ps1                    一键构建
   ├─ device_check.ps1             设备/页大小/安装/日志/端口转发
   └─ pyshim/sitecustomize.py      build 期补丁（见第 5 节第 4 条，不进 APK）
```

**安全闸门**：`sync_python.ps1` 显式拒绝 `.env`、`economical_agent.db`、`check_llm.py`，
并清掉 `__pycache__` / `*.pyc`；APK 已核验不含任何密钥与数据库。

---

## 5. 已踩的坑与对策（换机器复现时按表排查）

| # | 现象 | 根因 | 对策（已固化在脚本里） |
|---|---|---|---|
| 1 | `curl`/`Invoke-WebRequest` 全部 `SEC_E_NO_CREDENTIALS` | 沙箱进程拿不到 schannel 凭据 | 所有网络操作走 Python/`urllib` |
| 2 | `dl.google.com`、`services.gradle.org` 超时 | 网络不可达 | SDK 走腾讯云镜像、Gradle 走腾讯镜像、JDK 走华为镜像、AGP/androidx 走阿里云 Google 镜像 |
| 3 | AGP：`Several environment variables ... Android Preferences folder` | 同时设了 `ANDROID_SDK_HOME` 与 `ANDROID_USER_HOME` | 只设 `ANDROID_USER_HOME` |
| 4 | pip：`PermissionError: ...pip-unpack-xxx\flask-...whl` | 沙箱下 `tempfile.mkdtemp()`（固定 0o700）创建的目录**不可写** | `pyshim/sitecustomize.py` 用 0o777 重建 `mkdtemp`，经 `PYTHONPATH` 注入 |
| 5 | pip 在 `pypi.org` 上卡 15+ 分钟（CLOSE_WAIT、90% CPU） | 连接被对端关闭后 pip 空转 | `--index-url` 换清华镜像 + `--no-cache-dir` + `--retries 2` |
| 6 | pip：`No matching distribution found for markupsafe` | 指定 `--index-url` 后 Chaquopy 自己的轮子索引被覆盖 | 显式加 `--extra-index-url https://chaquo.com/pypi-13.1` |
| 7 | `Failed to find Build Tools revision 35.0.0` | AGP 8.13 默认 35.0.0 | `buildToolsVersion "36.0.0"` |
| 8 | 构建空等 2.5 分钟「waiting for package manifests」 | AGP 想联网补装 SDK | `android.builder.sdkDownload=false` |
| 9 | `Couldn't open current thread, error = 5`（Gradle file watcher） | 沙箱拒绝原生 watcher 线程 | `org.gradle.vfs.watch=false` |
| 10 | PowerShell 报 `Unexpected token ')'` | PS 5.1 按 ANSI 读无 BOM 的 UTF-8，中文注释把结构吃掉 | `android/**/*.ps1` 一律保存为 **UTF-8 with BOM** |

> 另注：第 4 条还会留下少数**当前用户无法删除**的 `android/app/build/tmp*` 空目录
> （0o700 权限）。它们在 `build/` 里、已被 `.gitignore` 覆盖，不影响构建；若确实要清，
> 用管理员 PowerShell 删除，或直接删除整个 `android/app/build`。

---

## 6. 进度：M0 / M1 均已在真机通过

设备：**HONOR DNP-AN00（荣耀 400 Pro）· MagicOS 10.0.0 · Android 16 (SDK 36) · arm64-v8a · PAGE_SIZE 4096**

### M0（运行时链路）实测

| 项 | 结果 |
|---|---|
| 内置 Python / SQLite | 3.12.12 / 3.50.4 |
| Flask 栈 | flask 3.1.3 · werkzeug 3.1.9 · jinja2 3.1.6 · markupsafe 3.0.3 |
| 冷启动 | `am start -W` TotalTime **606 ms**，其中 Python→Flask 就绪 **274–373 ms** |
| 数据文件 | `templates/` 与 `static/` 全部解压可读 |
| 16KB | `PAGE_SIZE=4096`，APK 亦通过 `zipalign -P 16` |

### M1（真实应用接入）实测

| 项 | 结果 |
|---|---|
| 六 Tab | 首页 / 账单 / 预算 / 目标 / 聊天 / 设置 全部渲染，`android.css` 生效（无假手机壳、无假状态栏） |
| 规则对话 | `source: rules`，「食堂花了 22 块」→ 生成 `risk_warning` 确认卡 → 确认 → 写入账本 |
| 真实调用 | 填 Key 后 `source: llm`，3.4 s 回复并引用注入的预算快照（−¥366、¥66、锁定 ¥300、阈值 ¥50） |
| 设置页 | Key 掩码回显、`测试连接` 返回 `ok:true / latency 860 ms / reply "pong"` |
| 演示数据 | 「灌入演示数据」`ok:true`，首页立刻显示 ¥2400 收入 / 三档 ¥375·¥187.5·¥187.5 / 超支红色进度条 |
| 清 Key 降级 | 删除 `files/config.json` 重启 → `configured:false, fallback:rules`，`source: rules` |
| 桌面回归 | `python -m unittest discover -s tests` → **203 passed** |

### M2（下拉刷新 / CSV 导出 / 设置合并）实测

| 项 | 结果 |
|---|---|
| 下拉刷新 | 顶部下拉 → 松手后重新请求 `budget/status`、`chat/status`、`transactions`、`insights`；不整页重载，当前 Tab 与聊天记录不丢 |
| CSV 导出 | 「导出账本 CSV」→ `已导出 11 笔账目`，文件落在 **下载/CampusWallet/campuswallet_ledger_YYYYMMDD-HHMM.csv**（780 B）；BOM `EF BB BF` + 中文表头 + 按时间排序 |
| 分享 | 导出对话框「分享」拉起系统分享面板（微信 / QQ / 邮件 / Honor Share），文件即 content:// URI，无需额外权限 |
| 统一设置入口 | 顶部齿轮已删除；AI 接入 / 导出 / 演示数据 / 清空 / 诊断全部并入底部 **Settings** Tab 的「本机功能」卡片。底部仍是 **6 个 Tab** |

> **为什么 CSV 不写「相册」**：Android 10+ 的 `MediaStore.Files` 只允许应用在
> **Download** 与 **Documents** 下创建非媒体文件，指定 `Pictures` 会被系统拒绝
> （`Primary directory Pictures not allowed for content://media/external_primary/file;
> allowed directories are [Download, Documents]`）。CSV 放进相册本来也不会被图库索引，
> 所以实际落点是 `下载/CampusWallet`；代码里保留了 `Documents/CampusWallet` 与
> 应用私有目录两级回退。

### 设置只有一个入口

底部那一个 **Settings** Tab 同时管：

1. 资料与语言（网页原生表单）：姓名、月收入、风险偏好、安全阈值、界面语言；
2. 「本机功能」卡片（仅 Android 渲染）：接入 AI Agent（原生页 `AiSettingsActivity`）、导出账本 CSV、灌入演示数据、清空全部数据、诊断信息。

卡片按钮通过 `window.CampusNative` 调原生：

| JS 调用 | 原生实现 | 行为 |
|---|---|---|
| `openAiSettings()` | `AiSettingsActivity` | Key / 模型 / 接口 / 测试连接（保存后回页面自动刷新徽标） |
| `exportCsv()` | `NativeActions.exportCsv` | 写 下载/CampusWallet，弹原生对话框（含分享） |
| `seedDemo()` | `NativeActions.seedDemo` | 原生确认框 → 重新灌数据 → 回页面自动刷新 |
| `resetData()` | `NativeActions.resetData` | 原生确认框 → 删库重建 |
| `showDiagnostics()` | `NativeActions.showDiagnostics` | 原生对话框展示 JSON |

桥方法都是「立即返回、后台执行、原生弹窗反馈」，点按钮不会卡住页面。
桌面浏览器拿不到这个桥：卡片在模板里就不渲染，`android-native.js` 也不会加载。

卡片版式：五个按钮**一行一个、占满整行**（主按钮蓝底，其余白底描边，样式与表单按钮一致），
按钮之间不加任何说明文字；整张卡片只在最底部保留一句短提示，说明「清空全部数据」的后果：

> 「清空全部数据」会删除全部流水、预算与目标，并把资料重置为默认值，且无法恢复。

（英文界面下为 `'Clear all data' removes every transaction, budget and goal, resets your
profile to defaults, and cannot be undone.`）

### M3（在手机上跑现有单元测试）实测

**怎么跑**（debug 专属，release 包不含测试也不含入口）：

```powershell
# 1) 构建时把 tests/ 打进 debug 包
powershell -ExecutionPolicy Bypass -File android\tools\build.ps1 -Task assembleDebug -IncludeTests
# 2) 装机后触发（无 UI 入口，只能 adb 调起）
adb install -r android\app\build\outputs\apk\debug\app-debug.apk
adb shell am start -n com.campuswallet.demo/.TestRunnerActivity
adb logcat -s CampusWallet:V | Select-String SELFTEST
```

**结果**（荣耀 400 Pro · Android 16）：

| 指标 | 值 |
|---|---|
| 实跑用例 | **200** |
| 失败 / 错误 | **0 / 0** |
| 跳过 | 1（`test_frontend_i18n`：需要 node，设备上没有） |
| 排除 | 3（`AnthropicCompatibilityTest`：需要 `anthropic` SDK，Android 按设计只接 DeepSeek） |
| 耗时 | 3.15 s |
| 摘要 | `Ran 200 tests in 3.15s - OK` |
| 桌面同套测试 | 203 passed（未被改动） |

排除项**不是隐藏**：结果 JSON 里有 `excluded: 3`、`excluded_detail` 与
`excluded_reason`，而且只作用于 Android runner（`android_boot._ANDROID_EXCLUDED_TESTS`），
共享的 `tests/` 一个字没改。

**实现要点（踩过才知道）**：

1. 测试只放进 debug 源集 `app/src/debug/python/selftest/`（包形式，带 `__init__.py`），
   release 构建不会包含；
2. Chaquopy **从不把 `.py` 模块解压到磁盘**（模块直接从 APK 导入），而
   `unittest.discover` 需要真实目录 → 必须声明 `chaquopy { extractPackages "selftest" }`，
   `run_tests()` 先 `import selftest` 触发解压，再用 `selftest.__file__` 的目录做发现；
3. `pyc { src = false }`：源码随包（**M4 已恢复字节码编译**，见下；release 包不含 selftest，
   `extractPackages` 匹配不到任何包，构建正常）；
4. `test_llm_agent` 依赖桌面的 `check_llm.py`，否则整个模块导入失败（只有 140/203）；
5. 跑测试的线程要给大栈：默认 Java 线程栈上跑整套 import 会
   `SIGSEGV "stack pointer is close to top of stack"` → `new Thread(null, r, "selftest", 16MB)`；
6. 临时 SQLite 走 `files/selftest-tmp`（`tempfile.gettempdir()` 在 Android 上不可写）。

### M4（release 打包 · 签名 · 交付）实测

**产物**：`dist/CampusWallet-1.0.0-arm64.apk` · 20.8 MB（21,836,630 字节）
· SHA-256 `848B25FF045483DEBCD9AC7DFD47A0C77DEC86D8D0DCF44C3EE246C48B75EAD1`

```powershell
powershell -ExecutionPolicy Bypass -File android\tools\build.ps1 -Task assembleRelease
```

| 项 | 结果 |
|---|---|
| 签名 | 自签名 `CN=CampusWallet`（RSA 2048 / 10 年），`apksigner verify` → **v2 方案 verified** |
| 证书 SHA-256 | `7463f7d1f2248178e8bee7f7512125c86be1be1691f3a7306ec1147783358b47` |
| 包内 | `arm64-v8a` 单一 ABI；8 个模块编译为 `.pyc`；`templates/`、`static/` 齐全 |
| 不含 | `tests/`、`selftest`、self-test Activity、keystore、`.env`、数据库 |
| 体积 | debug 21.9 MB → release **20.8 MB**（字节码编译恢复后仍略小） |
| 冷启动 | `TotalTime 480 ms`，Python→Flask 411 ms |
| 功能 | 首页/六个 Tab、灌入演示数据、CSV 导出（写 下载/CampusWallet）、分享面板 全部通过 |

**签名密钥**（勿删，后续要覆盖安装必须同一把）：

| 文件 | 说明 |
|---|---|
| `android/keystore/campuswallet-release.jks` | PKCS12 密钥库（gitignore） |
| `android/keystore.properties` | 密码/别名（gitignore） |

换机器或重新克隆后，这两样不在仓库里 → `build.gradle` 会自动回退到 debug 签名，
那种 APK 覆盖安装会被系统拒绝（`INSTALL_FAILED_UPDATE_INCOMPATIBLE`），需先卸载。

**体积说明**：APK 的大头是内置 CPython 运行时（`libpython3.12.so` 6.4 MB、
stdlib 归档 5.7 MB、`libcrypto` 3.6 MB），已开字节码编译、只保留 arm64 单一 ABI；
再往下压只能裁 Python 标准库，风险不值得。

**交付文档**：[INSTALL.md](INSTALL.md)（Markdown 步骤）与
[install-card.html](install-card.html)（可打印卡片，含纯净模式、Key 配置、清理说明）。

**对主应用的影响**：无。冷启动 `TotalTime 488 ms`（改动前 606 ms），Python→Flask 354 ms。
自检会在应用私有目录留下 `files/selftest-tmp/`（几十 KB），可删。



### 数据落在哪（想彻底删除时照此清理）

| 内容 | 位置 | 大小 |
|---|---|---|
| APK | `/data/app/~~…/com.campuswallet.demo-…/base.apk` | 21.9 MB |
| 私有数据 | `/data/user/0/com.campuswallet.demo/` | ~14 MB（`files/chaquopy/` 是解压的 Python 运行时；`databases/economical_agent.db` 是账本） |
| API Key | `/data/user/0/com.campuswallet.demo/files/config.json` | 100 字节左右，**只在填过 Key 后存在** |
| 导出的 CSV | `/sdcard/Download/CampusWallet/` | 每次导出约 1 KB，**点一次生成一个**，可直接删除 |
| 其余共享存储 | 无 | 除导出外不写 `/sdcard`；应用未申请任何存储权限 |

清除：`adb uninstall com.campuswallet.demo`（或 设置 → 应用 → 卸载）。导出的 CSV 不会随卸载删除，需要自己在文件管理里删 `下载/CampusWallet`。

### 已知待打磨（不影响功能）

1. `GET /favicon.ico` 返回 404（无害）。
2. 预算额度只在「当周期首次创建」时按收入推导：中途改收入不会重算当前周期（桌面行为一致，可用「灌入演示数据」重建）。
3. AI 页里改 Key 后，网页徽标要等返回主界面（`onResume`）才刷新；以「测试连接」结果为准。
4. 下拉刷新没有时长保证：指示器最多转 1.8 s，即使后台请求还没回来（本地服务通常 <100 ms）。
5. 从 AI 页返回会触发整页重载（为了刷新徽标），因此会回到首页 Tab；如果不想跳走，可改成只刷新 `/api/chat/status`。



