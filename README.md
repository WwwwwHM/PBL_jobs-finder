# AI 求职助手 MVP

基于 Gradio 的求职辅助应用，规划提供手机号登录、简历诊断、模拟面试和历史记录功能。

## 当前进度

- Gradio 登录页和三个业务 Tab 已完成。
- 后端工程结构、环境配置和 SQLite 数据层已完成。
- 验证码、Token 鉴权、LocalStorage 登录态恢复和退出清理已接入。
- 简历诊断已接入 PDF/文本输入、GLM 结构化诊断、完整优化稿编辑与 Word 简历导出。
- 每日共享配额服务已接入简历诊断，支持每日 00:00 重置、并发限制和失败回滚；导出 Word 不重复扣除配额。
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

要使用简历诊断，请在 `.env` 中填写智谱 API Key：

```dotenv
ZHIPU_API_KEY=你的智谱APIKey
ZHIPU_MODEL=glm-4-flash
ZHIPU_TIMEOUT_SECONDS=30
ZHIPU_MAX_RETRIES=2
```

简历诊断支持 10 MB 以内的 PDF，并依次使用 PyPDF2 和 pdfplumber 提取文本；扫描件或图片型 PDF 会自动使用本地 RapidOCR 识别，图片不会发送给第三方 OCR 服务，图片型 PDF 最多支持 5 页。首次 OCR 需要加载本地模型，耗时会高于普通文本 PDF；无法识别时仍可直接粘贴简历内容。诊断结果包含岗位匹配度、缺失关键词、修改建议和 STAR 改写示例。诊断完成后可编辑完整优化稿，再生成并下载 `.docx` 文件。模型被明确要求不得新增经历或伪造数据，缺少量化信息时会保留待补充占位符。PDF 解析、OCR、模型调用或保存失败时都会回滚本次配额占用，不会保存不完整记录。

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
