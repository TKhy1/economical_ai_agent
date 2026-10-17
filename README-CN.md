# CampusWallet — 大学生智能理财 Agent

一个移动端优先的网页 Demo：把学生有限的预算拆成**生存 / 缓冲 / 学习**三档资金池，
逐档核对花销、为储蓄目标攒钱，并通过 AI 助手对话记账——而 AI 只能**提议**，写入账本
必须经过确定性规则与用户确认。

后端 Flask + SQLite，前端原生 HTML/CSS/JS；配置了 API Key 时走 **DeepSeek**（默认，
OpenAI 兼容的 function calling），没有 Key 或调用失败时自动回退到内置的中英双语规则引擎，
功能不降级。Claude（Anthropic Messages API）作为可选后端保留。

---

## 功能

- **三档资金池** —— 每笔支出按类别归入 `survival` / `buffer` / `learning`；
  额度由月收入与风险偏好（保守 / 稳健 / 激进）推导。
- **锁定资金与可支配余额** —— 尚未攒满的储蓄目标会「锁定」对应金额，
  首页展示的才是真正能花的钱。
- **目标与截止日期** —— 完成百分比、剩余金额、剩余天数，以及「每周需存多少」
  （不足一周按一周算，避免除以过小的时间窗）。
- **洞察列表** —— 某一档超支 / 临近超支、可支配余额低于安全阈值、目标达成、目标逾期，
  统一返回 `{level, key, params}`，语言由前端渲染。
- **带安全闸门的对话** —— 大模型（或规则解析器）只能**提议**交易；真正写入需要
  确认一条 10 分钟后自动过期的 `ChatAction`。浏览器会把最近几轮对话作为纯文本历史
  一并发送，让模型能理解追问。
- **DeepSeek tool calling** —— 三个工具（记账提议、预算状态、目标进度）由模型按
  OpenAI 兼容协议调用，本地执行；系统提示里还会带一份**只读的预算快照**（当期三档额度、
  收支、锁定金额、可支配余额、目标进度），常见问题一次请求即可回答，省一轮 token。
  超时/限流/鉴权失败会**自动回退**到规则引擎，并在聊天标题栏标注当前后端。
- **中英双语 UI**，语言随用户档案持久化；零构建步骤，不依赖 JS 框架。

## 目录结构

```
app.py              Flask 应用：路由、JSON 校验、对话动作状态机
ledger.py           确定性规则引擎 —— 唯一允许写财务数据的入口
agent.py            风险分配、类别归户、洞察生成、自然语言解析
llm_agent.py        LLM 对话层：DeepSeek（OpenAI 兼容 tool calling）/ Anthropic（可选），
                    自身不碰 SQLite，只提议、不写账
limits.py           共享的金额/文本上限，保证聚合值有限
config.py           读取环境变量 / .env（AI_PROVIDER、DEEPSEEK_API_KEY、AI_MODEL…）
seed_demo_data.py   重置并重新灌入一套可信的演示数据
check_llm.py        对话后端自检：真实调用一次，打印 provider/模型/回复/耗时
templates/index.html        单页外壳（六个 Tab）
static/css/style.css        移动端优先的响应式布局
static/js/app.js            视图逻辑、接口调用、对话渲染
static/js/i18n.js           中英词条与插值
tests/                      单元测试（接口契约 + 规则引擎）
requirements.txt            直接依赖（钉版）
requirements.lock.txt       全量传递依赖锁
```

所有写操作都收敛到 `ledger.add_transaction`：应用、规则解析器、LLM 工具调用走的
是同一条路径，规则无法被绕过。

## 快速开始

需要 **Python 3.12+**。

```powershell
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt   # 钉版的直接依赖
venv\Scripts\python.exe app.py                               # http://127.0.0.1:5000
```

要复现测试验证过的完全一致环境，请装锁文件而不是直接依赖：

```powershell
venv\Scripts\python.exe -m pip install -r requirements.lock.txt
venv\Scripts\python.exe -m pip check
```

可选：灌入演示数据（可重复执行，会先清空演示用户的数据再重建）：

```powershell
venv\Scripts\python.exe seed_demo_data.py
```

## 测试

```powershell
venv\Scripts\python.exe -m unittest discover -s tests
```

六个套件分别覆盖接口契约（`tests/test_input_validation.py`）、截止日期与洞察计算
（`tests/test_goal_deadline.py`）、规则引擎与聊天解析（`tests/test_ledger_rules.py`）、
LLM 配置 / 历史处理 / DeepSeek tool-call 协议（`tests/test_llm_agent.py`）、演示数据脚本
（`tests/test_seed_demo.py`）、前端 i18n 插值（`tests/test_frontend_i18n.py`，需要 Node，
未安装时会跳过）。
每个用例都跑在临时 SQLite 文件上，不会碰到真实数据库；若系统临时目录不可写，
可设置环境变量 `ECONOMICAL_AGENT_TEST_TMP` 指向可写目录。

## 克隆后的钩子设置

仓库自带 `.githooks/pre-commit`，提交前会拦截 `.env`、数据库、私钥证书，以及内容里
形如 `sk-…`、`ghp_…`、`AKIA…` 的密钥特征。钩子文件随仓库一起分发，但 `core.hooksPath`
属于本地配置、不进版本库，**克隆后需要执行一次**：

```powershell
git config core.hooksPath .githooks
```

确认生效（无输出且退出码为 0 表示检查通过）：

```powershell
git hook run pre-commit
```

漏掉这一步不会报错，只是钩子静默不生效。遇到误报可 `git commit --no-verify` 跳过。

## 配置

把 `.env.example` 复制为 `.env`，填上 DeepSeek 的 Key 即可（[获取地址](https://platform.deepseek.com/api_keys)）：

```powershell
Copy-Item .env.example .env
# 编辑 .env：DEEPSEEK_API_KEY=sk-你的真实Key
venv\Scripts\python.exe check_llm.py   # 自检：真实调用一次，确认 Key/端点/模型都可用
venv\Scripts\python.exe app.py
```

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | 空 | DeepSeek 的 Key；留空则 `/api/chat` 走规则引擎 |
| `AI_PROVIDER` | 按 Key 推断 | `deepseek` / `anthropic`；`openai` 视为 OpenAI 兼容协议的别名 |
| `AI_MODEL` | 按后端 | DeepSeek：`deepseek-flash`；Claude：`claude-sonnet-5` |
| `AI_BASE_URL` | 按后端 | DeepSeek：`https://api.deepseek.com`；Claude：SDK 默认（仅当显式改过才传给 SDK） |
| `AI_TIMEOUT` | 按后端 | DeepSeek：`30` 秒；Claude：SDK 默认（不传） |
| `AI_MAX_TOKENS` | 按后端 | DeepSeek：`600`；Claude：`400`（与原 Claude 接入一致） |
| `AI_TEMPERATURE` | `0.7` | 采样温度（思考模式下不生效，因此不会发送） |
| `AI_THINKING` | `auto` | `auto`：DeepSeek 官方端点关闭思考、第三方网关不发送该字段；`enabled` 会自动回传 `reasoning_content` |
| `AI_REASONING_EFFORT` | 空 | 思考强度 `low` / `high` / `max`（仅思考模式） |
| `AI_CONTEXT` | `true` | 是否把只读预算快照注入系统提示（Claude 也可设 `false` 关掉） |
| `AI_MAX_RETRIES` | `1` | 429/5xx/网络错误的重试次数 |
| `ANTHROPIC_API_KEY` | 空 | Claude 的 Key（原有接入方式，见下面的兼容性说明） |

**模型名（V4.1 Flash）** —— DeepSeek 官方 API 把这个模型固定叫 `deepseek-flash`，而其它目录
（例如阿里云百炼）叫 `deepseek-v4.1-flash`。因此 `AI_MODEL` 在**官方端点**上会做等价映射：

| 配置的 `AI_MODEL` | 实际发送给 `api.deepseek.com` |
| --- | --- |
| `deepseek-flash` / `deepseek-v4.1-flash` / `deepseek-v41-flash` / `DeepSeek-V4.1-Flash` | `deepseek-flash`（即 V4.1-Flash-1017） |
| `deepseek-v4-flash` / `deepseek-v4.1-flash-1017` | `deepseek-flash`（历史/带日期的写法） |
| `deepseek-v4-pro` | `deepseek-v4-pro` |
| 其它自定义名字 | 原样发送 |

换到第三方 OpenAI 兼容网关时**原样透传**（模型 ID 由该网关定义）。例如用阿里云百炼跑同一个模型：

```dotenv
AI_PROVIDER=openai
AI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
AI_MODEL=deepseek-v4.1-flash
AI_API_KEY=<你的百炼/DashScope Key>
```

`GET /api/chat/status` 会同时给出 `model`（真正发送的 ID）和 `model_requested`（你配的写法），
以及 `thinking`（实际行为：`enabled` / `disabled` / `omitted`），方便核对。

### Claude 兼容性（原有接入不变）

这次加 DeepSeek 没有动 Claude 那条路，旧配置可以原样继续用：

- **Key 名字不变**：只设置 `ANTHROPIC_API_KEY` 或只设置 `AI_API_KEY` → 仍然走 Claude，
  且 `AI_API_KEY` 仍优先于 `ANTHROPIC_API_KEY`（旧顺序）。
  推断顺序：`AI_PROVIDER` → `DEEPSEEK_API_KEY` → `ANTHROPIC_API_KEY` → 单独的 `AI_API_KEY`（= Claude）→ 都没配时显示 DeepSeek。
  占位符不算"已配置"，所以复制 `.env.example` 不会把 Claude 误判成 DeepSeek。
- **默认值不变**：模型仍默认 `claude-sonnet-5`；`AI_MAX_TOKENS` 默认仍是 `400`；
  `AI_TIMEOUT` 不设时不向 SDK 传超时（用 SDK 自己的默认值）；`AI_BASE_URL` 用默认值时也不传给 SDK。
- **模型名不被改写**：别名映射只作用于 `api.deepseek.com`，`claude-*` 一律原样发送。
- **调用协议不变**：仍是 Messages API + tool use，工具结果、`is_error`、`add_transaction` 只提议不写账都一致。
- 唯一新增行为：系统提示末尾会附一段只读预算快照（`AI_CONTEXT=true`）。不想让 Claude 看到，
  设 `AI_CONTEXT=false` 即可，此时提示词与之前逐字相同。

真实环境变量优先于 `.env`。`AI_API_KEY` 是两个后端通用的键名：DeepSeek 优先用
`DEEPSEEK_API_KEY`，Claude 保持旧顺序（`AI_API_KEY` 优先于 `ANTHROPIC_API_KEY`）。
`.env` 用记事本保存为 UTF-8 即可（带 BOM 或用 ANSI/GBK 保存也能正确读取）。
`.env.example` 里的占位符（以及 `your-...-key`、`xxxx` 之类的明显假值）会被
视为未配置，因此不会发起注定失败的 API 调用。任何调用失败（未装 `anthropic`、网络不通、
401/402/429、回复为空）都会记录一条警告并自动回退到规则回复——Demo 始终可用。
当前生效的后端可以在界面聊天标题栏的徽标看到，也可以直接请求 `GET /api/chat/status`
（不返回 Key）。

## 输入上限

名字上限 120 字符，分类 60 字符，备注 1,000 字符，聊天消息 4,000 字符；任何金额不得超过
¥1,000,000,000,000。超限请求返回 400；金额上限同时保证收入/支出/目标锁定金额等聚合值有限，
API 响应不会出现非法 JSON 标记 `Infinity`。

## 预算模型

1. `agent.generate_budget_plan(monthly_income, risk_level, period_type)` 把收入拆成三份额度，
   周周期取月额度的四分之一。
2. 每笔支出经 `agent.category_tier(category, type)` 归户：收入一律进 `income` 档，
   支出一定落进三个资金池之一（支出若用了收入类类别，归入兜底的 `buffer`，
   因此三池合计恒等于 `expense_total`）。
3. `ledger.compute_locked_amount` 汇总所有储蓄目标**未攒满**的部分。
4. `净余额 = 收入 − 支出`；`可支配余额 = 净余额 − 锁定金额`。
5. 安全阈值由 `agent.exceeds_safety_threshold` 统一比较**可支配余额**，
   洞察列表与对话确认闸门都调用它，两处结论不会打架。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | 单页应用 |
| GET/PUT | `/api/profile` | 读取 / 更新姓名、风险偏好、安全阈值、收入、语言 |
| GET | `/api/transactions?limit=` | 最近流水（limit 限制在 1…1000） |
| POST | `/api/transactions` | 新增流水，返回落库后的行 |
| DELETE | `/api/transactions/<id>` | 删除流水 |
| POST | `/api/ledger/add` | 新增流水，同时返回最新预算状态 |
| GET/PUT | `/api/budget?period=` | 当前周期额度 / 覆写额度 |
| GET | `/api/budget/status?period=` | 各档花费、合计、锁定与可支配余额 |
| GET | `/api/goals` | 目标列表 |
| POST | `/api/goals` | 新建目标 |
| PUT/DELETE | `/api/goals/<id>` | 修改 / 删除目标 |
| GET | `/api/goal/progress?id=` | 附带 `pct`、`remaining`、`days_left` 的目标 |
| GET | `/api/insights?period=` | 首页洞察列表 |
| POST | `/api/chat` | 自然语言对话（有 Key 走 DeepSeek/Claude，否则走规则）；返回带 `source: llm \| rules` |
| GET | `/api/chat/status` | 当前对话后端（provider / model / configured，不含 Key） |
| POST | `/api/chat/action` | `{"id": ..., "decision": "confirm" \| "cancel"}` |

所有 JSON 报错统一为 `{"error": "..."}` + 4xx 状态码：不会返回 HTML 错误页。
格式错误或非对象请求体、字段类型错误、非有限或负数金额、格式错误的 deadline 都返回 400；
超范围的 id 返回 404。删除不存在的记录返回 404；`/api/goal/progress` 的 `id` 不是整数时返回 400。

## 数据模型

`UserProfile`、`Transaction`、`BudgetPlan`、`Goal`、`ChatAction` —— 定义见 `app.init_db()`。
数据库文件在首次导入时自动创建，**不入库**（见 `.gitignore`）；
演示数据用 `seed_demo_data.py` 重新生成。

时间戳统一存**无时区的本地时间** ISO 字符串（`ledger.now()`），与以周一为起点的
本地周 / 月周期窗口用同一个时钟，所以一笔账一定计入它在本地发生的那一周。
调用方可以带 UTC 偏移量传 `occurred_at`，`ledger` 会先换算成本地时间再落库。
当天到期的目标 `days_left = 0`，仍按「今天到期」给出储蓄建议，而不是判为逾期。

## 常见问题（Windows）

### `/api/chat` 返回 500：`attempt to write a readonly database`

数据库文件**不是当前进程可写的**：SQLite 能读、但建不了日志文件，于是任何写操作
（记一笔账、确认对话卡片、改预算、加目标）都会失败，聊天看起来像"AI 坏了"，其实和模型无关。
典型原因是这个 `.db` 文件是在工作区权限/沙箱建立**之前**创建的——沙箱只给之后新建的文件加
写入权限，旧文件会被漏掉。

修复：在同目录里新造一份副本替换它（新文件会继承当前正确的权限，数据不丢）：

```powershell
venv\Scripts\python.exe -c "import sqlite3; s=sqlite3.connect('economical_agent.db'); d=sqlite3.connect('economical_agent.db.fix'); s.backup(d); d.close(); s.close()"
Move-Item economical_agent.db economical_agent.db.old -Force
Move-Item economical_agent.db.fix economical_agent.db -Force
venv\Scripts\python.exe app.py
```

如果第二条 `Move-Item` 提示"拒绝访问"，说明当前终端本身也在受限沙箱里，请改用资源管理器
（或 `Win+R` 打开的普通终端）执行这两条。确认新库可用后，`economical_agent.db.old` 可以直接删掉。
`app.py` 启动时如果检测到数据库只读，会立刻在日志里打印这条提示，而不是等对话进行到一半才报错。

### 只改 `.env` 但后端没变

`.env` 只在进程启动时读取一次，改完必须重启 `app.py`；真实环境变量优先于 `.env`。
可以用 `venv\Scripts\python.exe check_llm.py` 随时确认当前生效的 provider / 模型 / Key 是否可用。
