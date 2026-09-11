# AI 求职助手 MVP

基于 Gradio 的求职辅助应用，规划提供手机号登录、简历诊断、模拟面试和历史记录功能。

## 当前进度

- Gradio 登录页和三个业务 Tab 已完成。
- 后端工程结构、环境配置和 SQLite 数据层已完成。
- 验证码、Token、配额、PDF 解析和 AI 服务尚未接入。

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

## 初始化后端

```powershell
python -m pbl_jobs_finder
```

命令会创建以下运行目录和数据库：

```text
data/
├── job_assistant.db
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
