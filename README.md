# AI 求职助手 MVP

基于 Gradio 的求职辅助应用，规划提供手机号登录、简历诊断、模拟面试和历史记录功能。

## 当前进度

- Gradio 登录页和三个业务 Tab 已完成。
- 后端工程结构、环境配置和 SQLite 数据层已完成。
- 验证码、Token 鉴权、LocalStorage 登录态恢复和退出清理已接入。
- 简历诊断已接入 PDF/文本输入、GLM 结构化诊断、补充履历窗口，以及 JSON → HTML → Playwright 的新版 PDF 简历生成。
- 每日共享配额服务已接入简历诊断，支持每日 00:00 重置、并发限制和失败回滚；同一诊断生成 PDF、失败重试和下载不重复扣除配额。
- 模拟面试和历史记录尚未接入。

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
