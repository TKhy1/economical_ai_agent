# CampusWallet · 安装说明（Android 测试包）

| 项 | 值 |
|---|---|
| 文件 | `dist/CampusWallet-1.0.0-arm64.apk` |
| 版本 | 1.0.0（versionCode 1） |
| 大小 | 20.8 MB（21,836,630 字节） |
| SHA-256 | `848B25FF045483DEBCD9AC7DFD47A0C77DEC86D8D0DCF44C3EE246C48B75EAD1` |
| 适用 | Android 8.0+（minSdk 26），**仅 arm64-v8a**；已在荣耀 400 Pro / MagicOS 10 / Android 16 上验证 |
| 签名 | 自签名（`CN=CampusWallet`，v2 方案），未上架任何应用商店 |

> 这是内部测试包：不联网也能用（无 Key 时走内置规则引擎），填了 DeepSeek Key 才会调用 AI。
> 所有账单数据只存在手机本地，不上传。

---

## 一、装到手机（二选一）

### 方式 A：手机直接装（不需要电脑）

1. 把 `CampusWallet-1.0.0-arm64.apk` 拷到手机（USB 文件传输 / 微信「文件传输助手」/ 网盘都行）；
2. 用「文件管理」找到它，点一下；
3. 系统若提示**「禁止安装未知来源应用」**，按提示允许当前应用（文件管理/微信）安装；
4. 系统若提示**「未经检测」**，勾选「已了解此应用未经检测」→ 继续安装。

### 方式 B：电脑 adb 装

```powershell
adb install -r dist\CampusWallet-1.0.0-arm64.apk
```

（仓库内自带 adb：`android\tools\adb.cmd install -r dist\CampusWallet-1.0.0-arm64.apk`，
需要手机已开「USB 调试」。）

## 二、荣耀 / MagicOS 必须先关的开关

| 现象 | 处理 |
|---|---|
| 安装界面**连按钮都没有**，或提示「已被安全策略拦截」 | 设置 → 系统和更新 → **纯净模式** → 关闭「增强防护」 |
| 提示「此应用未经检测」 | 勾选「已了解」→ 继续安装 |
| 点 APK 没反应 | 设置 → 应用 → 特殊应用权限 → 安装未知应用 → 允许「文件管理」 |

## 三、首次启动

1. 点桌面图标 **CampusWallet**（蓝底白色 ¥）；
2. 约 0.5 秒完成启动（内置 Python 运行时 + 本地服务）；
3. 首次进入是空账本（金额全 0），页面语言默认**英文**；
4. 想换中文：底部 **Settings** → LANGUAGE → **中文**（立即生效，无需保存）；
5. 想先看效果：底部 **Settings** → THIS DEVICE → **Load demo data** → 确认，即灌入一套演示账目/预算/目标。

## 四、接入 AI（可选）

1. 底部 **Settings** → THIS DEVICE → **Connect the AI agent**；
2. 粘贴 DeepSeek Key（`sk-...`，[申请地址](https://platform.deepseek.com/api_keys)）；
3. 点 **测试连接（真实调用一次）**，看到 `"ok": true` 与回复 `pong` 说明 Key/网络/模型都通；
4. 点 **保存并应用**，返回后聊天页徽标会变成「DeepSeek 已接入」。

Key 只写入本机应用私有目录 `files/config.json`，不会随 APK 分发；不填 Key 时对话自动走内置规则引擎，功能不降级。

## 五、数据放在哪 / 怎么导出

| 内容 | 位置 |
|---|---|
| 账本数据库 | 应用私有目录 `databases/economical_agent.db`（文件管理器看不到） |
| 导出的账本 CSV | **下载/CampusWallet/**（每次导出生成一个文件，可用系统分享面板发出去） |

导出：**Settings → THIS DEVICE → Export ledger CSV**，完成后对话框会显示文件名与位置，并有「分享」。

## 六、卸载与彻底清理

```powershell
adb uninstall com.campuswallet.demo
```

或：设置 → 应用 → 应用管理 → 搜 `CampusWallet` → 卸载。

**卸载会一并删除**：APK、账本数据库、配置（含 AI Key）、Python 运行时解压文件。
**不会自动删除**：你导出过的 CSV（在 `下载/CampusWallet/`），需要手动删除该文件夹。

## 七、常见问题

| 现象 | 原因 / 处理 |
|---|---|
| 安装时报 `INSTALL_FAILED_UPDATE_INCOMPATIBLE` | 手机上已有其它签名的旧版本（比如之前的 debug 版），先卸载再装 |
| 打开后一直停在「正在启动…」 | 重启应用；仍不行则卸载重装（数据库损坏时也会这样） |
| 聊天一直回复规则引擎结果 | 未配置 Key，或 Key 失效；到 THIS DEVICE → Connect the AI agent → 测试连接 确认 |
| 导出后找不到文件 | 在「文件管理 → 下载 → CampusWallet」；或用系统「下载」应用查看 |
| 界面是英文 | Settings → LANGUAGE → 中文 |
