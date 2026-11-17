# AI 独立开发与商业变现每日情报 Agent

每天从三类 RSS 信源收集新内容，用 OpenAI 兼容的 Chat Completions API 提炼中文商业机会，并发送适合手机阅读的 HTML 邮件。GitHub Actions 在 UTC 00:00（北京时间 08:00）触发。

## 项目文件

- `main.py`：抓取、时间筛选、模型提炼、Markdown/HTML 渲染与 SMTP 发送。
- `feeds.yaml`：三类信源、时间窗口及条目限额。
- `requirements.txt`：Python 依赖；推荐 Python 3.12，最低 3.11。
- `.github/workflows/daily_brief.yml`：每日调度、手动运行、离线检查与预览归档。
- `tests/test_main.py`：完全离线的异常路径与端到端流程测试。
- `.env.example`：环境变量清单，不含真实密钥。

## 部署到 GitHub Actions

1. 将这些文件上传到 GitHub 仓库，保留 `.github/workflows/daily_brief.yml` 的目录结构，并合并到默认分支。
2. 在仓库 **Settings → Secrets and variables → Actions → New repository secret** 添加以下 Secrets：

| Secret | 填写内容 |
| --- | --- |
| `LLM_API_KEY` | 模型供应商 API 密钥 |
| `LLM_BASE_URL` | API 根地址，例如 `https://api.openai.com/v1`；具体前缀以供应商为准 |
| `LLM_MODEL` | 供应商支持的 Chat Completions 文本模型标识 |
| `SMTP_HOST` | SMTP 服务器地址 |
| `SMTP_PORT` | 通常为 `465`（SSL）或 `587`（STARTTLS） |
| `SMTP_USER` | SMTP 登录用户名，通常为完整邮箱地址 |
| `SMTP_PASS` | SMTP 授权码或应用专用密码 |
| `TO_EMAIL` | 收件邮箱；多个收件人用英文逗号分隔 |

可选 Secrets：`FROM_EMAIL`（登录用户名不是邮箱时设置为实际发件邮箱）、`RSSHUB_BASE_URL`（支持 Twitter 路由的 RSSHub 实例地址，无尾部斜杠）。

3. 打开仓库 **Actions → AI 搞钱雷达每日简报 → Run workflow**。第一次可勾选 `dry_run`，只生成文件，不发邮件；仍会抓取真实资讯并在有内容时调用模型、产生相应费用。
4. 在运行详情的 **Artifacts** 下载预览包，打开 HTML 检查排版；再取消勾选 `dry_run` 运行一次测试邮件。

工作流只有定时和手动触发，不会在 push 时自动发邮件。定时任务使用默认分支，GitHub 可能延迟执行，尤其在整点；公开仓库长时间没有活动也可能被停用定时任务。请在仓库中检查 Actions 是否启用。

## X 与其他信源

`feeds.yaml` 提供商业、X 大佬、AI 工程三个分类。X 没有通用的官方 RSS；示例使用 `${RSSHUB_BASE_URL}/twitter/user/用户名`。需要自行提供可用的 RSSHub 实例，实例运营者通常还需要配置 X 登录凭据。也可以直接将这些 URL 替换为已订阅的第三方 RSS。

未设置 `RSSHUB_BASE_URL` 时，X 源会记录警告并跳过，其他源继续执行。不要假设公共 RSSHub 实例一定能抓取 X。RSS 地址也可能随网站改版、反爬策略或服务状态变化，运行后可在 `*-health.json` 检查每个源的状态，再调整 `feeds.yaml`。

默认窗口为 **过去 24 小时**。可将 `settings.lookback_hours` 改为 `36`；最大允许 36，不会悄悄扩大时间窗口。规则如下：

- 使用 RSS 发布时间，或在缺少发布时间时使用 Atom 更新时间；不使用更新时间覆盖已有的旧发布时间。
- 缺少可解析日期、未来日期、无有效 HTTP(S) 链接的条目被跳过。
- 按链接去重并去除常见追踪参数；单源默认最多 8 条、每分类最多 20 条，摘要最多 1400 字符。
- 每个源设置 15 秒超时，整个下载也限制为 15 秒；单个源异常不终止其他抓取。
- 不持久化跨日去重状态。36 小时窗口或手动重复运行可能重复展示资讯；手动重跑也会再次发邮件。

## 本地运行

安装依赖并生成完全离线的排版预览：

```powershell
python -m pip install -r requirements.txt
python main.py --demo
python -m unittest discover -s tests -v
```

预览文件在 `output/demo-YYYY-MM-DD.html`，明确标注为虚构演示数据。`--demo` 不联网、不调用模型、不发送邮件。

使用真实数据但不发邮件：

```powershell
$env:LLM_API_KEY = "你的密钥"
$env:LLM_BASE_URL = "https://你的供应商/v1"
$env:LLM_MODEL = "你的模型标识"
python main.py --dry-run
```

正式发送时再设置 `SMTP_HOST`、`SMTP_PORT`、`SMTP_USER`、`SMTP_PASS`、`TO_EMAIL`，执行 `python main.py`。Linux/macOS 使用 `export 变量名=值` 设置环境变量。程序只读取环境变量，**不会自动加载 `.env`**；不要把实际密钥提交到仓库。

其他参数：`--feeds 路径` 指定信源配置，`--output-dir 路径` 指定输出目录。默认输出包括 Markdown、HTML 和信源健康报告，Actions 中保留 7 天。

## 输出与异常处理

模型先返回结构化 JSON，程序校验字段、1—5 星评分以及来源编号，再转换为固定四板块的 Markdown 和经过 HTML 清洗的卡片邮件。纯文本备用正文与 HTML 正文一起发送。每个机会都包含痛点、产品形态、变现模式、星级和 MVP 切入建议，并附有原文链接。

RSS 标题和摘要是提炼依据，不等于阅读全文。程序要求模型区分事实与待验证的产品假设；有足够证据时给出 3 个灵感，不足时明确留空。来源编号检查能阻止虚构引用，具体分析仍需结合原文判断。

- **源超时、HTTP 错误、损坏条目：** 记录警告并跳过，报告列出失败或跳过的源。
- **没有新内容／全部信源不可用：** 不调用模型，发送四板块的“本次无有效资讯”邮件，正常退出；报告中的信源状态可区分暂无更新与抓取失败。
- **模型连接故障、429、5xx：** 最多请求 3 次；认证错误不重试。输出不符合格式时也进入异常处理。
- **模型最终失败：** 保存并发送明确标记的来源索引版，不编造分析；随后返回非零状态，让 Actions 标红。此时邮件可能已经送达，重跑前先检查收件箱。
- **SMTP 故障：** 返回非零状态；已生成的预览文件仍会归档。不自动重试发信，以降低重复投递概率。

465 端口使用 SMTP over SSL，其他端口强制 STARTTLS，均验证服务器证书。邮件标题以北京时间日期生成：`【AI搞钱雷达】YYYY-MM-DD 独立开发与技术情报简报`。

## 模型兼容性

请求地址为 `LLM_BASE_URL/chat/completions`，也兼容直接填入完整的 `/chat/completions` 地址。使用 Bearer 密钥、`model`、`messages` 和 `stream: false`；模型应支持 system/user 消息与文本 JSON 输出，不要求供应商实现 JSON Schema 等扩展参数。

接口参考：[OpenAI Chat Completions](https://platform.openai.com/docs/api-reference/chat/create)。实际模型、地址、额度和限流配置请按你所选的兼容服务商填写。
