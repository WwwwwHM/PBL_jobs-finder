# AI 求职助手 MVP

基于 FastAPI + Gradio 的求职辅助应用，提供手机号登录、简历诊断、模拟面试和历史记录功能。

## 当前进度

- 9/29–30 部署与最终交付的复现入口见 [部署说明](部署说明.md)、[HTTP 接口](docs/api.md)、[回滚与演示步骤](docs/release_runbook.md)。验收证据与可选部署状态见 [工程验收记录](docs/engineering_acceptance.md)。

- 9 月 25–28 日工程化任务的本轮实现与验收见 [工程验收记录](docs/engineering_acceptance.md)：Redis AOF 重启、文件下载归属校验、24 小时导出清理、异常日志正文保护及故障恢复已补齐。新增 [文件存储说明](docs/storage.md)、[稳定性与重试边界](docs/stability.md)，并提供完整浏览器验收脚本。
- 9 月 24 日工程化首日已验收：232 项测试全部通过（含 Redis 与 Chromium PDF 集成），Ruff、依赖声明与锁文件检查通过；完整旧库迁移、历史复合索引和 WAL 备份恢复已验证。日常库的 5 用户、17 份简历、10 轮面试也完成独立恢复核对。详见 [当日记录](今日开发计划-2026-09-24.md)、[数据模型与备份恢复](docs/data_model.md) 和 [工程验收及后续待办](docs/engineering_acceptance.md)。
- Gradio 登录页和三个业务 Tab 已完成。
- 后端工程结构、环境配置和 SQLite 数据层已完成。
- 验证码、Token 鉴权、LocalStorage 登录态恢复和退出清理已接入。
- 简历诊断已接入 PDF/文本输入、GLM 结构化诊断、补充履历窗口，以及 JSON → HTML → Playwright 的新版 PDF 简历生成。
- 每日共享配额服务已接入简历诊断，支持每日 00:00 重置、并发限制和失败回滚；同一诊断生成 PDF、失败重试和下载不重复扣除配额。
- 模拟面试闭环已接通：内置 42 条结构化种子题，支持阿里云 1024 维 Embedding、ChromaDB 检索、GLM 定制问题、独立会话落库、当前优化简历直接导入、版本化面试节奏、即时或延迟反馈，以及有上限的动态追问。结束后生成并保存结构化报告，展示逻辑、专业、表达评分、总体评价、知识盲区、改进建议和参考回答；支持清空当前工作区后重新开始。
- “我的记录”已接入：按当前登录用户分别展示最近 5 条简历诊断和模拟面试，包含创建时间、目标岗位、匹配度或问答轮数；选择记录可重新查看诊断策略、六维证据、优化稿、完整面试问答和结构化报告。登录恢复、诊断和面试操作后自动刷新，也可手动刷新；详情查询强制校验记录归属，退出登录后会清空摘要和详情。
- 集成安全与性能检查已完成：SQLite 使用 WAL 和 30 秒忙等待，首次并发创建用户可安全重试；面试按会话串行、跨会话并行，Gradio 队列最多等待 100 个事件并允许 4 个不同面试请求并行。真实服务样本中，简历诊断 5 次最大 24.796 秒，完整面试首轮 10 次最大 2.996 秒。
- 9 月 23 日最终工作已于 9 月 20 日提前完成：134 项自动化测试全部可执行通过，发布预检 8/8 通过，真实服务已走完登录、诊断、PDF、5 题面试、报告、历史和退出。最终 5 次诊断耗时为 17.337～29.026 秒，全部满足 30 秒门禁；M6 已正式通过，详见 `今日开发计划-2026-09-23.md`。

## 环境准备

项目验收运行时为 Python 3.11，推荐使用 uv 0.12.2 按锁文件安装全部依赖及项目包。从项目根目录执行：

```powershell
uv sync --locked --python 3.11 --cache-dir .uv-cache
.\.venv\Scripts\Activate.ps1
```

首次配置环境变量：

```powershell
Copy-Item .env.example .env
```

仅在 `.env` 尚不存在时复制示例，再填写模型密钥。`.env` 不会被 Git 提交。备选 pip 安装必须额外执行 `python -m pip install -e .` 安装本项目；仅安装 `requirements.txt` 不包含项目包，而且未锁定全部传递依赖，不能视为已验收的锁定环境。

M1 已加入版本化简历诊断策略，但默认保持原有单分数流程。开发或测试环境可显式启用六维诊断：

```dotenv
RESUME_POLICY_VERSION=resume-general-v1
ENABLE_RESUME_DIMENSIONS=True
```

六维模式会分别校验硬技能、经验相关度、软性能力、教育背景、关键词覆盖和简历质量，最终总分由服务端按本地策略计算。外部 skill 仅作为产品方法参考，不在运行时下载或执行；来源登记见 `docs/skill_adoption.md`。

M2 提供经典专业、紧凑清晰和技术重点三种本地 PDF 简历模板。模板功能默认关闭；启用后，生成简历时可以选择版式：

```dotenv
ENABLE_RESUME_TEMPLATES=True
```

模板只负责结构化简历的本地 HTML/CSS 排版，不参与 AI 内容生成。服务端会校验模板 ID，并在 PDF 成功生成后将所选版本记录到简历记录中。

模拟面试默认提供入门、标准、挑战三档难度，分别允许每题最多 0、1、2 次追问，同时调整问题深度与评价要求。难度选择不需要启用功能开关。可以点击“跳过本题”，或直接回复“跳过这题”“别问这个问题了”进入下一道主问题；跳过单独记入历史，未作答部分按未评估处理。

M3 提供版本化模拟面试策略。启用后可选择标准面试（5 道主问题）或快速面试（3 道主问题、每题最多 1 次追问，即使选择挑战难度），并可选择逐题即时反馈或面试结束后统一反馈：

```dotenv
INTERVIEW_POLICY_VERSION=interview-standard-v1
ENABLE_INTERVIEW_MODES=True
```

每次面试开始时，服务端会把模式、难度、反馈方式和事实中立的考察计划固化到会话中。模型只根据当前考察目标生成一道问题；题数、追问上限和结束条件由服务端控制。达到追问上限、检测到高度相似的问题或主动跳过时，按下一项考察目标切题，不会将原有追问直接改标为主问题。延迟反馈模式仍会保存经过校验的内部评价，但在面试结束前不会向用户展示。旧数据库启动时自动补充难度字段，历史会话默认按标准难度继续。

M4 打通历史详情与复盘闭环。“我的记录”表格会显示本地记录编号，选择某条简历诊断后可查看当时的策略版本、等级、模板、六维证据和优化稿；选择某轮模拟面试后可回放完整问答、反馈方式与最终报告。详情读取使用“记录编号 + 当前登录手机号”联合查询，不调用模型、不消耗每日配额，也不会通过不存在或不属于当前用户的编号泄露记录信息。旧记录没有结构化维度或报告时会显示可用的基础内容，损坏的 JSON 数据会降级为空白区块，不影响其他历史记录。

候选版本的完整安装、初始化、预检、启动、冒烟和回滚步骤见
[`部署说明.md`](部署说明.md)。

首次使用新版 PDF 简历生成时，需要安装 Playwright 的 Chromium 运行时：

```powershell
.\.venv\Scripts\playwright.exe install chromium
```

如果将浏览器安装到项目内的可写目录，启动前设置缓存路径：

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD\.playwright-browsers"
python frontend.py
```

生产环境应使用固定版本的 Chromium，并确保应用进程对 Playwright 浏览器缓存目录和 `data/exports/` 具有写权限。

要使用简历诊断，请在 `.env` 中填写智谱 API Key：

```dotenv
ZHIPU_API_KEY=你的智谱APIKey
ZHIPU_MODEL=glm-4-flash
ZHIPU_TIMEOUT_SECONDS=30
ZHIPU_MAX_RETRIES=2
ALIYUN_API_KEY=你的阿里云APIKey
ALIYUN_EMBEDDING_MODEL=qwen3.7-text-embedding
ALIYUN_EMBEDDING_URL=https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings
EMBEDDING_DIMENSIONS=1024
EMBEDDING_TIMEOUT_SECONDS=30
EMBEDDING_MAX_RETRIES=2
```

简历诊断支持 12 MB 以内的 PDF，并依次使用 PyPDF2 和 pdfplumber 提取文本；扫描件或图片型 PDF 会自动使用本地 RapidOCR 识别，图片不会发送给第三方 OCR 服务，图片型 PDF 最多支持 5 页。首次 OCR 需要加载本地模型，耗时会高于普通文本 PDF；无法识别时仍可直接粘贴简历内容。诊断结果包含岗位匹配度、缺失关键词、修改建议和 STAR 改写示例。诊断完成后，用户可以在生成新版简历前补充最多 6000 字的真实履历；模型会拆分并润色补充事实，将其归入技能、工作、项目、教育或证书等对应栏目，不会在简历末尾机械追加“补充信息”。模型只输出经过 Pydantic Schema 校验的 JSON，服务端使用固定 HTML/CSS 模板并通过 Playwright/Chromium 打印 A4 PDF。模型被明确要求不得新增经历或伪造数据，缺少量化信息时会保留待补充占位符。PDF 解析、OCR、模型调用、Schema 校验、浏览器渲染或保存失败时都会保留可重试输入，不会保存不完整记录。

## 初始化后端

```powershell
python -m pbl_jobs_finder
```

命令会创建以下运行目录和数据库：

```text
data/
├── job_assistant.db
├── exports/
├── uploads/
└── chroma_db/
```

数据库初始化可重复执行，不会删除已有数据。

## 初始化面试题向量库

配置 `ALIYUN_API_KEY` 后执行：

```powershell
.\.venv\Scripts\python.exe scripts\import_question_bank.py
```

命令会严格校验 `src/pbl_jobs_finder/resources/interview_questions.json`，按每批最多 20 条调用 Embedding，并使用稳定题目 ID 写入 `data/chroma_db/`。重复执行会更新同 ID 题目，不会增加重复记录。也可通过 `--source` 和 `--persist-directory` 指定其他结构化题库与持久化目录。

支持直接导入 Markdown 问答题库。先校验解析结果，再执行入库：

```powershell
.\.venv\Scripts\python.exe scripts\import_question_bank.py --source src\pbl_jobs_finder\resources\AI_interview_QA.md --dry-run
.\.venv\Scripts\python.exe scripts\import_question_bank.py --source src\pbl_jobs_finder\resources\AI_interview_QA.md
```

Markdown 中用 `##` 写主问题、`###` 写追问，标题下正文作为该题的参考答案；追问归属于前一个主问题。答案内的小标题请用 `####` 或更深级别。列表和代码块会保留，代码块中的标题不会被识别成题目。允许暂时没有答案的题目，它们仍参与出题检索，但不参与参考答案检索。`--dry-run` 只校验并统计，不调用 Embedding 或写入数据库。

Markdown 默认岗位为“AI大模型应用开发工程师”、分类为“AI应用开发”、难度为 `medium`，可以用 `--position`、`--category`、`--difficulty basic|medium|advanced` 覆盖。JSON 题库也可添加 `reference_answer` 字段保存答案，旧题库格式继续兼容。

每次修改 Markdown 后需要重新执行导入。题目 ID 根据文件名、所属主问题和题目文本生成：只修改答案或编号时会更新原记录；修改题目文本、所属主问题或文件名会生成新记录。导入不会自动删除旧记录，不同题库应使用不同文件名。

模拟面试提交回答时，会按当前问题检索最多 3 条带答案的题库记录，将问题、参考答案、来源和所属主问题交给模型辅助反馈，最后一题和追问也会检索。报告生成时按本次实际问题再次检索最多 5 条记录。检索基于题目语义（追问包含所属主问题），答案作为配套资料保存，不会在首题提问时直接展示。模型需判断召回内容的相关性与正确性，避免把参考答案当成候选人的真实经历；无答案时继续按实际问答评估。每条答案最多保存 20,000 字符，传给模型时最多使用前 4,000 字符。

题库初始化后，登录用户可在“模拟面试”页填写目标岗位，并可选粘贴岗位 JD 与简历核心内容；也可以点击“使用当前优化稿”，直接复用简历诊断结果或生成新版 PDF 后回填的新版简历。开始面试会占用一次共享配额：系统使用三类输入检索参考题，由 GLM 生成第一道问题，并将 `in_progress` 会话、第一题、策略版本、问题计划和对话状态保存到 SQLite。首题下方可填写并提交回答，回答写入当前用户的会话且不重复扣配额。完成当前模式规定的主问题及必要追问后，系统生成经过严格 JSON 校验的面试报告，并将最终回答、反馈、完成状态和报告原子保存到 SQLite；报告生成失败时保留最后一道题供安全重试。已保存报告只能由会话所属用户读取。“重新开始”会清空当前页面输入、会话和结果，不删除历史数据。输入校验、Embedding、模型或数据库失败时，本次配额自动回滚且不会保存半成品会话。

“我的记录”只读取有效 Token 对应手机号的数据，并按创建时间倒序展示。每类记录最多 5 条；简历诊断显示岗位匹配度，模拟面试显示已完成的主问题轮数和当前状态。摘要接口不返回简历正文、岗位 JD、完整问答或报告；只有用户选择具体记录后，详情接口才会在再次校验登录态和记录归属后返回对应结果。

## 启动前端

```powershell
python frontend.py
```

默认访问地址为 `http://127.0.0.1:7860`。

此入口现在启动单 worker 的 FastAPI + Gradio，提供 `/health/live`、`/health/ready` 和鉴权下载。需要跨重启保留登录态和每日配额时，在 `.env` 设置 `STATE_BACKEND=redis` 并配置带持久化的 Redis。默认 memory 模式仍会在重启后丢失高频状态。PDF 下载必须保持登录，有效期 24 小时；历史文本不随导出到期删除。

候选版本启动前可执行统一部署预检。`--release` 会额外要求两项 API Key、
42 条已初始化面试题、Playwright Chromium 和可正常构建的 Gradio 页面：

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD\.playwright-browsers"
python scripts/check_deployment.py --release
```

## 错误日志与调试

应用启动后会同时向控制台和 `data/logs/app.log` 输出日志。日志按天轮转，
默认保留最近 14 份；页面中的系统类错误会显示一个错误编号，可直接在日志中
搜索该编号以查看异常类型链和堆栈位置；不记录第三方异常文本、模型正文或 SQL 参数。日志会自动遮盖手机号、邮箱、Token、密码和 API Key，
业务代码也只记录输入长度、记录 ID 等诊断元数据，不记录简历正文。

可以在 `.env` 中调整日志配置：

```dotenv
LOG_LEVEL=DEBUG
LOG_DIR=
LOG_BACKUP_COUNT=14
```

`LOG_DIR` 留空时使用 `data/logs/`；生产环境通常使用 `INFO`，复现问题时可临时
切换为 `DEBUG`。修改配置后需要重启应用。

Windows 上若 `app.log` 被其他进程占用，日志会继续追加到当前文件，
并在 60 秒后有新日志写入时重试轮转；占用期间归档和清理会延后。
同时运行多个应用实例时，建议为各实例设置不同的 `LOG_DIR`，或关闭不再使用的旧实例。

## 运行测试

工程化基线的静态与依赖检查：

```powershell
ruff check .
.\.venv\Scripts\python.exe scripts\check_dependencies.py
uv lock --check --offline --cache-dir .uv-cache
uv pip check --python .venv/Scripts/python.exe --cache-dir .uv-cache
```

Ruff 和 uv 是开发检查工具；9/24 验证版本分别为 0.12.0 和 0.12.2。直接依赖检查脚本核对 `pyproject.toml`、`requirements.txt`、`uv.lock` 与当前解释器安装版本，运行时应使用项目虚拟环境。

```powershell
python -m unittest discover -s tests -v
```

默认跳过两项 Chromium PDF 集成测试；已安装项目内 Chromium 时可完整运行：

```powershell
$env:RUN_PLAYWRIGHT_PDF_TESTS = '1'
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD\.playwright-browsers"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Redis 集成测试需要 `redis-server` 在 PATH 中，运行时使用独立端口和临时目录；缺少该程序时会跳过对应集成测试。备份、恢复和旧库验证命令见 [数据模型](docs/data_model.md)。

配置真实 API Key 并初始化题库后，可重复执行性能门禁。默认测量 5 次简历诊断和 10 次完整面试首轮，任一次超过 30 秒或 15 秒时返回非零退出码；输出只包含耗时和汇总，不打印简历或模型内容：

```powershell
.\.venv\Scripts\python.exe scripts\check_live_performance.py
```

最终交付前可运行隔离的真实服务全链路。该命令会产生外部 AI 服务调用，使用临时业务数据库和导出目录，并且只输出阶段耗时与通过状态：

```powershell
.\.venv\Scripts\python.exe scripts\run_acceptance_demo.py --confirm-live-services
```

完整浏览器验收使用隔离 Redis、SQLite、Chroma 和真实 Chromium。默认固定模型数据；`--live` 使用当前 API 配置调用真实服务，只提交合成资料。报告和截图写入已忽略的 `tmp/`：

```powershell
.\.venv\Scripts\python.exe -m scripts.check_browser_flow
.\.venv\Scripts\python.exe -m scripts.check_browser_flow --live --output tmp/browser-live-2026-09-28
```

9/29–30 的一体化部署验收会复制源代码（不复制 `.env` 或日常数据）、创建全新虚拟环境、按缓存中的锁文件安装、两次初始化真实题库、检查 HTTP、运行全部测试，再完成真实浏览器演示和四类存储回滚。它需要 uv、redis-server、项目内 Chromium、已配置的两项 API Key；会产生实际模型调用。输出目录必须尚不存在：

```powershell
.\.venv\Scripts\python.exe -m scripts.check_clean_deployment --live --output tmp/clean-release-new
```

先运行正常的 `uv sync --locked --cache-dir .uv-cache` 下载依赖；验收脚本的安装步骤使用 `--offline` 检查锁定依赖可以重新安装。若缓存不全，先联网补齐，不复用已有虚拟环境冒充干净安装。无需外部模型的回滚演练：

```powershell
.\.venv\Scripts\python.exe -m scripts.check_browser_flow --rollback --output tmp/rollback-fixed-new
```

## 项目结构

```text
src/pbl_jobs_finder/
├── config/        # 环境配置和运行路径
├── models/        # SQLAlchemy实体、连接和CRUD
├── modules/       # 业务模块
├── utils/         # 通用工具
└── vector_store/  # 向量检索集成
```

完整需求、流程和排期见 `需求与设计文档.md` 与 `项目排期.md`。
