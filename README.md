# AI 求职助手 MVP

基于 Gradio 的求职辅助应用，规划提供手机号登录、简历诊断、模拟面试和历史记录功能。

## 当前进度

- Gradio 登录页和三个业务 Tab 已完成。
- 后端工程结构、环境配置和 SQLite 数据层已完成。
- 验证码、Token 鉴权、LocalStorage 登录态恢复和退出清理已接入。
- 简历诊断已接入 PDF/文本输入、GLM 结构化诊断、补充履历窗口，以及 JSON → HTML → Playwright 的新版 PDF 简历生成。
- 每日共享配额服务已接入简历诊断，支持每日 00:00 重置、并发限制和失败回滚；同一诊断生成 PDF、失败重试和下载不重复扣除配额。
- 模拟面试闭环已接通：内置 42 条结构化种子题，支持阿里云 1024 维 Embedding、ChromaDB top 5 检索、GLM 定制首题、独立会话落库、当前优化简历直接导入、逐题 AI 反馈、动态追问和 5 道主问题流程。结束后生成并保存结构化报告，展示逻辑、专业、表达评分、总体评价、知识盲区、改进建议和参考回答；支持清空当前工作区后重新开始。
- “我的记录”已接入：按当前登录用户分别展示最近 5 条简历诊断和模拟面试，包含创建时间、目标岗位、匹配度或问答轮数；登录恢复、诊断和面试操作后自动刷新，也可手动刷新。历史摘要不返回简历正文、JD、对话或报告全文，退出登录后会清空页面记录。
- 集成安全与性能检查已完成：SQLite 使用 WAL 和 30 秒忙等待，首次并发创建用户可安全重试；面试按会话串行、跨会话并行，Gradio 队列最多等待 100 个事件并允许 4 个不同面试请求并行。真实服务样本中，简历诊断 5 次最大 24.796 秒，完整面试首轮 10 次最大 2.996 秒。

## 环境准备

项目要求 Python 3.11。使用现有虚拟环境时：

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

首次配置环境变量：

```powershell
Copy-Item .env.example .env
```

`.env` 包含本地密钥，不会被 Git 提交。

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

题库初始化后，登录用户可在“模拟面试”页填写目标岗位，并可选粘贴岗位 JD 与简历核心内容；也可以点击“使用当前优化稿”，直接复用简历诊断结果或生成新版 PDF 后回填的新版简历。开始面试会占用一次共享配额：系统使用三类输入检索 top 5 参考题，由 GLM 生成第一道问题，并将 `in_progress` 会话、第一题和对话状态保存到 SQLite。首题下方可填写并提交回答，回答写入当前用户的会话且不重复扣配额。完成 5 道主问题及必要追问后，系统生成经过严格 JSON 校验的面试报告，并将最终回答、反馈、完成状态和报告原子保存到 SQLite；报告生成失败时保留最后一道题供安全重试。已保存报告只能由会话所属用户读取。“重新开始”会清空当前页面输入、会话和结果，不删除历史数据。输入校验、Embedding、模型或数据库失败时，本次配额自动回滚且不会保存半成品会话。

“我的记录”只读取有效 Token 对应手机号的数据，并按创建时间倒序展示。每类记录最多 5 条；简历诊断显示岗位匹配度，模拟面试显示已完成的主问题轮数和当前状态。页面不会展示或传输历史记录中的简历正文、岗位 JD、完整问答和报告详情。

## 启动前端

```powershell
python frontend.py
```

默认访问地址为 `http://127.0.0.1:7860`。

## 错误日志与调试

应用启动后会同时向控制台和 `data/logs/app.log` 输出日志。日志按天轮转，
默认保留最近 14 份；页面中的系统类错误会显示一个错误编号，可直接在日志中
搜索该编号以查看完整异常链。日志会自动遮盖手机号、邮箱、Token、密码和 API Key，
业务代码也只记录输入长度、记录 ID 等诊断元数据，不记录简历正文。

可以在 `.env` 中调整日志配置：

```dotenv
LOG_LEVEL=DEBUG
LOG_DIR=
LOG_BACKUP_COUNT=14
```

`LOG_DIR` 留空时使用 `data/logs/`；生产环境通常使用 `INFO`，复现问题时可临时
切换为 `DEBUG`。修改配置后需要重启应用。

## 运行测试

```powershell
python -m unittest discover -s tests -v
```

配置真实 API Key 并初始化题库后，可重复执行性能门禁。默认测量 5 次简历诊断和 10 次完整面试首轮，任一次超过 30 秒或 15 秒时返回非零退出码；输出只包含耗时和汇总，不打印简历或模型内容：

```powershell
.\.venv\Scripts\python.exe scripts\check_live_performance.py
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
