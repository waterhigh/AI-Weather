# Prism Scout｜棱镜商业观察

每天从商业与视觉工程 RSS、Tavily 的 X 搜索收集线索，用 OpenAI 兼容的 Chat Completions API 提炼中文商业机会，并发送适合手机阅读的 HTML 邮件。GitHub Actions 在 UTC 00:00（北京时间 08:00）触发。

## 项目文件

- `main.py`：抓取、时间筛选、模型提炼、Markdown/HTML 渲染与 SMTP 发送。
- `feeds.yaml`：商业与工程 RSS、时间窗口及条目限额；X 双查询在 `main.py` 中配置。
- `requirements.txt`：Python 依赖；推荐 Python 3.12，最低 3.11。
- `.github/workflows/daily_brief.yml`：每日调度、手动运行、离线检查与预览归档。
- `tests/`：完全离线的 RSS、Tavily、模型、邮件与异常路径测试。
- `.env.example`：环境变量清单，不含真实密钥。

## 部署到 GitHub Actions

1. 将这些文件上传到 GitHub 仓库，保留 `.github/workflows/daily_brief.yml` 的目录结构，并合并到默认分支。
2. 在仓库 **Settings → Secrets and variables → Actions → New repository secret** 添加以下 Secrets：

| Secret | 填写内容 |
| --- | --- |
| `LLM_API_KEY` | 模型供应商 API 密钥 |
| `LLM_BASE_URL` | API 根地址，例如 `https://api.openai.com/v1`；具体前缀以供应商为准 |
| `LLM_MODEL` | 供应商支持的 Chat Completions 文本模型标识 |
| `TAVILY_API_KEY` | Tavily Search API 密钥，用于 X 动态的两组搜索 |
| `SMTP_HOST` | SMTP 服务器地址 |
| `SMTP_PORT` | 通常为 `465`（SSL）或 `587`（STARTTLS） |
| `SMTP_USER` | SMTP 登录用户名，通常为完整邮箱地址 |
| `SMTP_PASS` | SMTP 授权码或应用专用密码 |
| `TO_EMAIL` | 收件邮箱；多个收件人用英文逗号分隔 |

可选 Secret：`FROM_EMAIL`（登录用户名不是邮箱时设置为实际发件邮箱）。旧的 `RSSHUB_BASE_URL` 已不再使用。缺少 Tavily 密钥时仅 X 来源显示失败，RSS 和邮件流程仍继续。

3. 打开仓库 **Actions → Prism Scout 每日商业与视觉情报 → Run workflow**。第一次可勾选 `dry_run`，只生成文件，不发邮件；仍会调用 Tavily，并在有内容时调用模型，消耗相应额度。
4. 在运行详情的 **Artifacts** 下载预览包，打开 HTML 检查排版；再取消勾选 `dry_run` 运行一次测试邮件。

工作流只有定时和手动触发，不会在 push 时自动发邮件。定时任务使用默认分支，GitHub 可能延迟执行，尤其在整点；公开仓库长时间没有活动也可能被停用定时任务。请在仓库中检查 Actions 是否启用。

## X 与其他信源

Indie Hackers 使用社区镜像 `https://ihrss.io/top/today`；备用地址为 `https://feed.indiehackers.world`，需要时在 `feeds.yaml` 中手动替换。商业类还包含 Product Hunt 和 Reddit 的 r/SideProject、r/SaaS 日榜；工程类新增 r/LocalLLaMA 日榜，用于寻找多模态与视觉应用线索。这些地址不需要额外 API Key，实际可用性以运行时健康报告为准。

新品排名和 Reddit 热度不代表真实付费数据；收入复盘属于作者自述。程序仍按发布时间筛选 24 小时窗口，日榜中发布时间较早的条目会被过滤。

X 动态通过 Tavily 每次执行两组 `basic` 搜索：核心账号 `levelsio`、`marc_louvion`、`dannypostmaa`、`dotey`、`op7418`（最多 15 条），以及 AI / VLM / OCR 商业实践（最多 10 条）。两组均使用 `time_range="day"`、限制 `x.com` 与 `twitter.com`，各自设置 20 秒超时；清洗后仅保留原帖链接，按帖子 ID 合并 Twitter/X 域名的重复结果。X 结果追加到 RSS 池后统一编号，不占 RSS 每分类配额。

邮件附带 [X 监控列表](https://x.com/i/lists/2122591054356709465) 入口。程序搜索指定的五个账号，不会读取或同步列表成员，也不保证搜索引擎覆盖全部原帖。搜索结果缺少精确发布时间时会标注“搜索线索，发布时间未核实”；提供带时区时间戳时则检查其是否处于过去 24 小时内。Tavily 在底部显示 `success (N条)`、`partial (N条)` 或 `failed (0条)`，健康 JSON 同时记录两组请求状态；其中 N 为清洗去重后保留的条目数。

浏览器 User-Agent 已配置，但网站仍可能限流或拒绝 GitHub Actions 的出口 IP。RSS 地址也可能随网站改版变化，运行后可在 `*-health.json` 检查状态，再调整 `feeds.yaml`。

默认窗口为 **过去 24 小时**。可将 `settings.lookback_hours` 改为 `36`；最大允许 36，不会悄悄扩大时间窗口。规则如下：

- 使用 RSS 发布时间，或在缺少发布时间时使用 Atom 更新时间；不使用更新时间覆盖已有的旧发布时间。
- RSS 中缺少可解析日期、未来日期、无有效 HTTP(S) 链接的条目被跳过。X 搜索的时间标注规则见上文。
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
$env:TAVILY_API_KEY = "你的 Tavily 密钥"
python main.py --dry-run
```

正式发送时再设置 `SMTP_HOST`、`SMTP_PORT`、`SMTP_USER`、`SMTP_PASS`、`TO_EMAIL`，执行 `python main.py`。Linux/macOS 使用 `export 变量名=值` 设置环境变量。程序只读取环境变量，**不会自动加载 `.env`**；不要把实际密钥提交到仓库。

其他参数：`--feeds 路径` 指定信源配置，`--output-dir 路径` 指定输出目录。默认输出包括 Markdown、HTML 和信源健康报告，Actions 中保留 7 天。

## 输出与异常处理

模型先返回结构化 JSON，程序校验字段、1—5 星评分以及来源编号，再转换为固定四板块的 Markdown 和经过 HTML 清洗的卡片邮件。纯文本备用正文与 HTML 正文一起发送。每个机会都包含痛点、产品形态、变现模式、星级和 MVP 切入建议，并附有原文链接。

RSS 标题、摘要与 Tavily 搜索摘要是提炼依据，不等于阅读全文。程序要求模型区分事实与待验证的产品假设；有足够证据时给出 3 个灵感，不足时明确留空。来源编号检查能阻止不存在的引用编号，具体分析仍需结合原文判断。

System Prompt 已追加商业过滤规则：淘汰针对不愿升级免费云服务的开发者的收费点子；开源 CLI 必须有面向非技术用户的明确差异化方案；最高优先级结合 CV、VLM、OCR、复杂文档解析和图像/视频生成能力，服务电商卖家、创作者、专业人士与中小企业。行动建议使用 `action.overseas` 和 `action.domestic` 两个必填字段，分别提供 X/Reddit 与小红书/闲鱼各 30 分钟的验证动作。样本不足不直接判定需求不存在，中文反馈也不自动证明海外付费意愿。程序校验双轨字段、结构和引用编号；选品判断由模型执行。

- **源超时、HTTP 错误、损坏条目：** 记录警告并跳过，报告列出失败或跳过的源。
- **没有新内容／全部信源不可用：** 不调用模型，发送四板块的“本次无有效资讯”邮件，正常退出；报告中的信源状态可区分暂无更新与抓取失败。
- **模型连接故障、429、5xx：** 最多请求 3 次；认证错误不重试。输出不符合格式时也进入异常处理。
- **模型最终失败：** 保存并发送明确标记的来源索引版，不编造分析；随后返回非零状态，让 Actions 标红。此时邮件可能已经送达，重跑前先检查收件箱。
- **SMTP 故障：** 返回非零状态；已生成的预览文件仍会归档。不自动重试发信，以降低重复投递概率。

465 端口使用 SMTP over SSL，其他端口强制 STARTTLS，均验证服务器证书。邮件标题以北京时间日期生成：`【Prism Scout】YYYY-MM-DD 独立开发与技术情报简报`。

## 从旧版升级

将 `main.py`、`feeds.yaml`、`.github/workflows/daily_brief.yml`、`tests/`、`README.md`、`.env.example` 同步到 GitHub 默认分支。测试文件也需更新，因为行动建议从字符串改为双轨对象。依赖无需变更。新增 `TAVILY_API_KEY` Secret 后手动运行工作流；不勾选 `dry_run` 才会发送邮件。

## 模型兼容性

请求地址为 `LLM_BASE_URL/chat/completions`，也兼容直接填入完整的 `/chat/completions` 地址。使用 Bearer 密钥、`model`、`messages` 和 `stream: false`；模型应支持 system/user 消息与文本 JSON 输出，不要求供应商实现 JSON Schema 等扩展参数。

接口参考：[OpenAI Chat Completions](https://platform.openai.com/docs/api-reference/chat/create)。实际模型、地址、额度和限流配置请按你所选的兼容服务商填写。
