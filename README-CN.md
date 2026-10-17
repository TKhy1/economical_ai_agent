# CampusWallet — 大学生智能理财 Agent

一个移动端优先的网页 Demo：把学生有限的预算拆成**生存 / 缓冲 / 学习**三档资金池，
逐档核对花销、为储蓄目标攒钱，并通过 AI 助手对话记账——而 AI 只能**提议**，写入账本
必须经过确定性规则与用户确认。

后端 Flask + SQLite，前端原生 HTML/CSS/JS；配置了 API Key 时走 Claude，
没有 Key 时自动回退到内置的中英双语规则引擎，功能不降级。

---

## 功能

- **三档资金池** —— 每笔支出按类别归入 `survival` / `buffer` / `learning`；
  额度由月收入与风险偏好（保守 / 稳健 / 激进）推导。
- **锁定资金与可支配余额** —— 尚未攒满的储蓄目标会「锁定」对应金额，
  首页展示的才是真正能花的钱。
- **目标与截止日期** —— 完成百分比、剩余金额、剩余天数，以及「每周需存多少」
  （不足一周按一周算，避免除以过小的时间窗）。
- **洞察列表** —— 某一档超支 / 临近超支、余额低于安全阈值、目标达成、目标逾期，
  统一返回 `{level, key, params}`，语言由前端渲染。
- **带安全闸门的对话** —— 大模型（或规则解析器）只能**提议**交易；真正写入需要
  确认一条 10 分钟后自动过期的 `ChatAction`。
- **中英双语 UI**，语言随用户档案持久化；零构建步骤，不依赖 JS 框架。

## 目录结构

```
app.py              Flask 应用：路由、JSON 校验、对话动作状态机
ledger.py           确定性规则引擎 —— 唯一允许写财务数据的入口
agent.py            风险分配、类别归户、洞察生成、自然语言解析
llm_agent.py        可选 Claude 客户端（tool use），自身不碰 SQLite
config.py           读取环境变量 / .env（AI_API_KEY、AI_MODEL）
seed_demo_data.py   重置并重新灌入一套可信的演示数据
templates/index.html        单页外壳（三个 Tab）
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

三个套件分别覆盖接口契约（`tests/test_input_validation.py`）、截止日期与洞察计算
（`tests/test_goal_deadline.py`）、规则引擎（`tests/test_ledger_rules.py`）。
每个用例都跑在临时 SQLite 文件上，不会碰到真实数据库；若系统临时目录不可写，
可设置环境变量 `ECONOMICAL_AGENT_TEST_TMP` 指向可写目录。

## 配置

把 `.env.example` 复制为 `.env`：

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `AI_API_KEY` | 空 | 启用 Claude 对话后端；留空则 `/api/chat` 走规则引擎 |
| `AI_MODEL` | `claude-sonnet-5` | 传给 Messages API 的模型名 |

真实环境变量优先于 `.env`；`ANTHROPIC_API_KEY` 可作为 `AI_API_KEY` 的备用名。
若 `anthropic` 包缺失或调用失败，请求会自动回退到规则回复——Demo 始终可用。

## 预算模型

1. `agent.generate_budget_plan(monthly_income, risk_level, period_type)` 把收入拆成三份额度，
   周周期取月额度的四分之一。
2. 每笔支出经 `agent.category_tier(category, type)` 归户。
3. `ledger.compute_locked_amount` 汇总所有储蓄目标**未攒满**的部分。
4. `净余额 = 收入 − 支出`；`可支配余额 = 净余额 − 锁定金额`。

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
| POST | `/api/chat` | 自然语言对话（有 Key 走 LLM，否则走规则） |
| POST | `/api/chat/action` | `{"id": ..., "decision": "confirm" \| "cancel"}` |

所有 JSON 报错统一为 `{"error": "..."}` + 4xx 状态码：不会返回 HTML 错误页，
超范围的 id 或非有限金额也不会变成 500。

## 数据模型

`UserProfile`、`Transaction`、`BudgetPlan`、`Goal`、`ChatAction` —— 定义见 `app.init_db()`。
数据库文件在首次导入时自动创建，**不入库**（见 `.gitignore`）；
演示数据用 `seed_demo_data.py` 重新生成。
