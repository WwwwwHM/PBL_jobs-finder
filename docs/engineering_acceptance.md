# 系统工程化验收记录

## 2026-09-28：提前完成 9/29—9/30 排期

结论：**本机部署交付、真实浏览器最终演示与停写后的跨存储回滚已通过**。9/29 和 9/30 必须完成项均已验收；Docker / 远端部署按排期的条件项记录为未验证。实际执行日为 9/28，目录中的 9/30 表示计划交付日。

代码版本：`eed25a8`（基于 `7b2b926`）。本节及两天排期记录在随后文档提交中交付；该文档提交的父提交为上述代码版本，可用 `git log --oneline -2` 核对。没有提交或删除接手时已有的 `AI_interview_QA.md` 本地修改、求职资料、输出文件和录屏。

### 9/29：部署验证与资料

| 项目 | 结果 |
| --- | --- |
| 实测环境 | Windows、Python 3.11.9、uv 0.12.2；宿主 Ruff 0.12.0；本机 Redis 3.0.504、AOF always；Playwright 1.47.0 / Chromium 1134，复用项目内浏览器运行时 |
| 干净安装 | 86 个源码/测试/配置文件复制到独立目录，**不复制 `.env` 和日常数据**；新建虚拟环境，`uv sync --locked --offline` 安装 128 包，10.193 秒；源文件 SHA256 清单随证据保存 |
| 依赖 | 25 项直接依赖声明、锁文件、安装版本一致；128 个包兼容检查通过；`uv lock --check --offline` 通过，锁文件无改动 |
| 初始化 | 新 SQLite 连续初始化两次；真实 Embedding 导入两次，均为 42 条且总数仍为 42（14.931 / 6.081 秒），证明幂等 |
| 发布预检 | **9/9 PASS**：Python、5 类可写目录、4 张表、题库源、Redis、Key 配置、向量题库、Chromium 和 Gradio 构建 |
| HTTP 部署 | 新环境实际启动 Uvicorn factory 单 worker；首页、`/health/live`、`/health/ready` 返回 200，无身份 `/api/history` 返回 401 |
| 全量回归 | 新环境 **242/242 通过、0 跳过、0 失败，24.420 秒**，包括真实 Redis 与两项 Chromium 集成；原开发环境亦 242/242 通过，28.646 秒 |
| 文档 | 修正缺失项目包安装的旧说明；统一锁定安装；补充 HTTP 接口、Cookie/Token 区别、错误格式、Compose 初始化、备份恢复及演示手册 |
| Docker | 未找到 PATH 中 Docker 或常见安装路径中的 Docker Desktop；Dockerfile 改为按 `uv.lock` 安装，但镜像构建、Linux 依赖/字体/卷权限未验证 |
| 远端 | 未提供服务器/虚拟机地址和部署凭据；未执行远端部署 |

安装使用预先联网填充的依赖缓存，不复用任何已有虚拟环境。第一次源副本验收的题库调用被沙箱网络限制阻止，保留失败记录；获准联网后在另一个全新输出目录完整重跑并通过。两次均只使用隔离数据。源码副本来自当前候选工作区，而非干净 Git checkout；包含用户自定义题库文件，但本轮初始化和实际查询只使用内置 42 条 JSON 种子题，不把自定义题库修改纳入发布提交或验收范围。

### 9/30：最终演示与回滚

| 项目 | 结果 |
| --- | --- |
| 固定数据浏览器 | PDF 上传、登录、诊断、PDF 下载、5 题面试、报告、历史、应用重启、四存储回滚、退出全部通过；模型与 Embedding 固定，其他组件真实运行 |
| 真实浏览器 | 在上述全新安装环境完成同一闭环；使用合成履历与真实 LLM/Embedding，诊断 **25.072 秒**、PDF **19.124 秒**、首题 **3.997 秒**、5 次回答及报告 **63.467 秒** |
| 配额与持久化 | 诊断、新面试合计扣 2 次；应用进程实际终止重启后，浏览器恢复登录、历史不变、配额仍为 2、旧 PDF 可下载 |
| 停写快照 | 停止应用及 Chroma 写入者，正常关闭隔离 Redis；成组备份 SQLite、Chroma、Redis AOF 和导出，11 个文件逐文件校验 |
| 故障后变更 | 实际修改 SQLite 分数/面试状态、删除 PDF、删除一条向量题、配额从 2 加至 3 并撤销 Token；全部变更均有断言核对 |
| 独立恢复 | 快照复制到新目录并校验全部文件；42 条题库文档/元数据/向量摘要一致，真实 top 5 查询通过；切换应用路径与 Redis 恢复目录后重新启动 |
| 恢复结果 | 登录及配额恢复；历史摘要及两类详情相等；PDF 字节完全一致；快照后的模拟故障变更已丢弃；健康接口 200 |
| 退出隔离 | 最终退出后，旧 Token 查询历史和下载 PDF 均为 401；跨用户隔离由全量 API/业务回归验证 |
| 页面检查 | 已查看真实诊断/PDF 页面及面试报告截图，主要内容、按钮和报告正常显示；用户主观体验及现场人工演示不代替为自动确认 |

本轮真实演示是一条合成样本，不替代 9/28 已通过的 5 次诊断 + 10 次面试首轮性能门禁。63.467 秒是 5 次回答及报告的合计，不是单轮延迟。没有发现阻塞这条完整演示的缺陷，未新增业务逻辑变更。

回滚证明同一候选代码下、维护窗口停写的四类存储恢复；不是在线原子快照，不承诺跨版本数据库降级，也不证明 Redis 7 / everysec 断电零丢失。恢复的登录态遵循原 TTL；生产恢复是否统一撤销旧 Token 需按部署策略执行。完整流程见 [发布、恢复与演示手册](release_runbook.md)。

### 本次交付与复现证据

```powershell
uv sync --locked --python 3.11 --cache-dir .uv-cache
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD\.playwright-browsers"
.\.venv\Scripts\python.exe -m scripts.check_clean_deployment --live --output tmp/clean-release-new
.\.venv\Scripts\python.exe -m scripts.check_browser_flow --rollback --output tmp/rollback-fixed-new
ruff check .
uv lock --check --offline --cache-dir .uv-cache
```

一体化命令需要两项 API Key、可访问外部模型的网络和 `redis-server`；输出目录必须尚不存在。真实服务调用产生费用，使用本机现有配置。脚本把 Key 传入子进程，不复制密钥文件，不打印密钥。

- 成功一体化汇总：`tmp/clean-deployment-final-2026-09-30/result.json`。
- 新环境分阶段日志与源码清单：同目录 `install.log`、`dependencies.log`、`pip-check.log`、`question-import-1.log`、`question-import-2.log`、`preflight.log`、`tests.log`、`server.log`、`source-manifest.json`。
- 真实浏览器与回滚：同目录 `browser/result.json` 以及 `upload.png`、`resume.png`、`report.png`、`history.png`、`rollback.png`。
- 固定浏览器与回滚：`tmp/browser-final-fixed-2026-09-30/result.json`；开发环境回归：`tmp/acceptance-2026-09-30.log`。
- 初次沙箱网络失败证据：`tmp/clean-deployment-2026-09-30/`。不计作成功证据。

`tmp/` 中日志、合成资料截图和隔离部署数据均不提交 Git。正式交付代码与文档按模块提交；交付范围包括 README、部署说明、API、恢复与演示手册、两天排期及本验收记录。

仍存在的已知限制：单 worker、Mock 验证码、Redis 退款失败无自动补偿、24 小时导出、历史无自助永久删除、Docker/服务器运行未验证。后续顺序见手册，不列为已完成的生产能力。

以下是先前验收快照；后续待办状态以上节结果为准。

## 2026-09-28：执行 9/25—9/28 排期

结论：四个执行包已完成，242 项自动化测试全部通过（0 失败、0 跳过，23.993 秒），固定数据与真实模型的浏览器闭环均通过。此次为既有未提交工作区上的增量验收，没有整理或提交原有暂存区；9/29 干净安装、容器部署和 9/30 跨存储回滚尚未执行。

### 逐日交付

| 计划日 | 完成内容与证据 |
| --- | --- |
| 9/25 | 隔离 Redis 的 TTL、发送冷却、5 次错误限制、并发单次消费、Token 复用/过期/撤销、20 个请求最多占用 3 次配额、失败退还和跨日退款；AOF always 写入后杀死真实 Redis 并重启，验证码/Token/配额恢复；两个独立 Python 进程读取相同状态；浏览器实际重启应用后仍保留登录态与配额 |
| 9/26 | PDF 后缀/文件头/空文件/12 MB/加密/损坏/100 页/路径校验；失败暂存清理；照片/联系方式/技能回归；PDF/Word 使用每次独立 UUID 文件名，数据库提交失败清理本次文件并保留旧导出；24 小时有效期、定期清理、归属校验下载、禁止通用 Gradio 文件路由；新增 storage.md |
| 9/27 | 限制可重试错误、关闭 SDK 内层重试、允许配置 0 次重试；Embedding 非法 URL/TLS/无效向量失败不盲重试；日志只留异常类型与栈位置，阻断正文/SQL 参数；报告失败重试不扣新配额、PDF/Word 落库失败可恢复；新增 stability.md 明确总等待与退款边界 |
| 9/28 | 实际 FastAPI + Gradio + SQLite + Redis + ChromaDB + Chromium；固定模型和真实模型各完成一次浏览器闭环及应用重启；HTTP 无身份/跨用户访问拒绝；真实服务层闭环一次；性能 5 次诊断和 10 次面试首轮全部达标 |

### 实测结果

| 项目 | 结果 |
| --- | --- |
| 接手基线 | 232 项全部通过，18.203 秒 |
| 最终全量 | **242 项全部通过，23.993 秒**；启用 Chromium 测试，真实 Redis 可用 |
| 日志最后补验 | 将 Uvicorn 独立处理器也切换为安全异常格式，防止 500 响应后再次打印原始异常；日志与稳定性专项 14 项全部通过，0.428 秒，见 `tmp/final-privacy.log` |
| Ruff / 依赖 | Ruff 通过；25 项直接依赖声明、锁文件、安装版本一致；离线锁检查 131 包；安装兼容检查 128 包 |
| 固定数据浏览器 | 诊断 0.174 秒，真实 Chromium PDF 2.982 秒，首题 0.423 秒，5 次回答与报告 1.309 秒；使用固定模型及确定性 Embedding、真实持久化 Chroma，不能解读成模型性能 |
| 真实模型服务层 | 诊断 17.323 秒，PDF 21.413 秒，完整面试 48.837 秒；5 次回答，历史各 1 条，准确扣除 2 次配额 |
| 真实模型浏览器 | 诊断 22.072 秒，PDF 20.140 秒，首题 6.036 秒，5 次回答与报告 52.093 秒；实际下载 PDF 文件头有效，未登录下载拒绝 |
| 应用重启 | 杀死并重启真实应用进程，浏览器刷新恢复登录；历史摘要完全一致、配额仍为 2，之前生成的 PDF 可用；退出后旧 Token 读取历史/下载均为 401 |
| 真实诊断性能 | 23.677、22.983、23.931、28.701、24.118 秒；平均 24.682 秒，**5/5 ≤ 30 秒** |
| 真实面试首轮性能 | 3.600、2.267、3.769、2.391、3.245、2.430、2.274、4.112、1.931、2.231 秒；平均 2.825 秒，**10/10 ≤ 15 秒** |
| 样本失败率 | 最终性能样本 0/15，最终服务层/固定浏览器/真实浏览器闭环 0/3；这是合成资料小样本验收，不是长期 SLA |

### 问题及处理记录

- 发现 Gradio 导出文件缓存缺少本应用的用户归属校验：改为专用下载路由，每次检查 Token 和记录所属用户，配套 HttpOnly Cookie，禁用通用文件访问。
- 发现固定导出文件名可能在数据库失败时覆盖或删除上次成功文件：改用每次独立 UUID，增加 Word 清理与旧 PDF 保留/再次成功生成的回归。
- 发现 SDK/数据库异常文本可能含简历、响应正文或 SQL 参数：保留异常类型链、堆栈位置与错误编号，禁止输出第三方异常值和源码行；限制第三方 DEBUG 日志。
- 真实服务验收脚本原来要求题库恰好 42 条，当前库已有 52 条合法扩展题：对齐部署预检的“至少 42 条”约束；没有删除题库或恢复旧版题库。
- 初次真实请求被沙箱网络限制阻止（WinError 10013），在批准的联网执行中完成验收。固定浏览器脚本调试中修复了 Mock 构造和 Gradio 文件名/重复表格节点的定位；不将这些脚本失败计作最终模型失败率。

### 复现与本地证据

```powershell
$env:RUN_PLAYWRIGHT_PDF_TESTS = '1'
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD\.playwright-browsers"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
ruff check .
.\.venv\Scripts\python.exe scripts/check_dependencies.py
uv lock --check --offline --cache-dir .uv-cache
uv pip check --python .venv/Scripts/python.exe --cache-dir .uv-cache
.\.venv\Scripts\python.exe -m scripts.check_browser_flow
.\.venv\Scripts\python.exe -m scripts.check_browser_flow --live --output tmp/browser-live-2026-09-28
.\.venv\Scripts\python.exe scripts/run_acceptance_demo.py --confirm-live-services
.\.venv\Scripts\python.exe scripts/check_live_performance.py
```

后三项真实服务命令使用现有 API 配置并产生实际模型调用。浏览器脚本使用临时业务库、独立 Redis 进程及题库副本；固定模式自行导入 42 条种子题，真实模式复制现有向量库。退出清理隔离进程和业务数据，保留合成资料的验收截图。

本地证据：`tmp/acceptance-2026-09-28.log`、`tmp/live-acceptance-2026-09-28.log`、`tmp/live-performance-2026-09-28.log`、`tmp/browser-2026-09-28/result.json`、`tmp/browser-live-2026-09-28/result.json`；两个浏览器目录均含上传、诊断/PDF、报告和历史截图。日志/截图位于 Git 忽略目录，不提交验证码或运行数据。

### 已明确的边界与下一步

- Redis 退款期间断连目前只有错误记录，没有持久化自动补偿；内存模式重启仍丢失状态，且不具备 Redis 模式完整的验证码防滥用机制。
- AOF always 的强制终止恢复测试不等于 Compose everysec 的断电零丢失；部署验证仍安排 9/29。
- 导出有效期 24 小时，定期清理只处理本版受管文件；旧文件、备份、进程强制终止残留的 Gradio 缓存需独立运维策略。历史业务数据长期保留，无用户自助永久删除入口。
- timeout 是网络阶段等待限制，不能当作完整业务硬性墙钟截止；默认重试预算与多模型调用关系详见 [稳定性](stability.md)。
- 本轮已验证本机完整服务启动；干净环境安装、容器/远端部署、跨存储一致性回滚和最终版本提交属于 9/29—30，不提前标为完成。

以下保留 9/24 的原始验收快照，其中“待办”是当日状态，以本节 9/28 结果为最新状态。

## 2026-09-24：基线收尾与数据存储

结论：当日排期通过。验证对象为基于 `c8b1a00` 的 9/24 交付工作区，不是该基线提交的干净版本；保留既有简历优化和后续工程化改动。本次没有调用真实 AI API，没有把后续部署或 Redis 持久化任务标记为通过。

### 环境与复现

Windows、Python 3.11.9、uv 0.12.2、Ruff 0.12.0；使用项目 `.venv`、项目内 Chromium 缓存以及本机 `redis-server`。测试为 Redis 启动独立端口和临时目录，不使用日常 Redis 数据。依赖检查覆盖 25 项直接依赖；锁文件解析 131 个包，当前环境的 128 个已安装包兼容检查通过（跨平台锁定包数量不要求与当前安装数量相等）。

从项目根目录执行：

```powershell
ruff check .
.\.venv\Scripts\python.exe scripts\check_dependencies.py
uv lock --check --offline --cache-dir .uv-cache
uv pip check --python .venv/Scripts/python.exe --cache-dir .uv-cache
$env:RUN_PLAYWRIGHT_PDF_TESTS = '1'
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD\.playwright-browsers"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Ruff 配置固定 Python 3.11，启用 `E4,E7,E9,F,I,SIM`，覆盖基础错误、导入排序、上下文管理等简化规则。较宽的 `E` 规则探索还发现既有行长问题；本阶段不把 `E501` 行长重排纳入门禁，避免对大量中文 Prompt 与前端文本进行无关重写。

### 实际结果

| 检查 | 结果与证据 |
| --- | --- |
| 初始回归 | 227 项，225 通过、2 跳过、0 失败，10.090 秒；两项均由 `RUN_PLAYWRIGHT_PDF_TESTS` 开关控制 |
| 最终全量回归 | **232 项全部通过，0 跳过、0 失败，15.949 秒**；包含本地 Redis 和真实 Chromium PDF 集成 |
| 静态检查 | Ruff 通过；修复 lambda 赋值、8 处导入块、5 处嵌套上下文管理、1 处静态列表构造 |
| 依赖声明 | `pyproject.toml` 与 `requirements.txt` 的 25 项直接依赖一致，当前直接依赖安装版本匹配 `uv.lock` |
| 锁文件 | `uv lock --check --offline` 通过，未重新求解改写锁文件 |
| 安装环境 | `uv pip check` 通过，128 个已安装包无兼容冲突 |
| 旧库升级 | 完整旧结构，2 用户、14 简历、14 面试及报告；重复初始化两次，逐字段无损，历史摘要与详情正常，跨用户拒绝 |
| 查询计划 | 捕获两类仓储实际查询；新库及升级库均命中 `(phone, created_at, id)` 复合索引，无临时排序 |
| WAL 与独立恢复 | 保持源连接打开、关闭自动 checkpoint，写入已提交 WAL；备份包括该写入且不含备份后的变更；恢复库可独立修改而不影响源库 |
| 备份失败边界 | 拒绝覆盖已有快照与源文件；拒绝非文件库和缺失来源；连接失败清理本次目标且源文件字节不变 |
| PDF 跳过项补验 | 中文多页 PDF 可读取；恢复照片的像素实际进入 Chromium 生成的 PDF，两项均通过 |

全量日志保存在本地 `tmp/baseline-2026-09-24.log` 与 `tmp/acceptance-2026-09-24.log`，不提交运行日志。

### 日常数据库实测

通过 `backup_sqlite` 对日常 `data/job_assistant.db` 创建快照 `data/backups/2026-09-24-acceptance-231328.db`，复制到独立临时目录后重复初始化，再比较备份的全部原字段，逐用户读历史摘要并逐条读全部详情。

| 项目 | 结果 |
| --- | --- |
| 用户 / 简历 / 面试 | 5 / 17 / 10，恢复后数量与全部原字段一致 |
| `PRAGMA integrity_check` | `ok` |
| `PRAGMA foreign_key_check` | 0 条违规 |
| 历史访问 | 全部 17 份简历与 10 轮面试详情可读 |
| 数据操作范围 | 源库使用只读备份连接；只初始化恢复副本；未改写源库业务数据 |

本地汇总为 `tmp/database-restore-2026-09-24.json`，仅记录数量和检查结果。快照保留于已忽略的备份目录，恢复用临时副本验证后已清理。复现合成数据演练的测试与恢复操作说明见 [数据模型](data_model.md)。

### 工作区改动归属

以下是接手时已有内容，按用途归类，不以 Git 是否已跟踪推断作者或完成状态；同一文件可同时包含两个阶段的代码。

| 范围 | 已有改动 |
| --- | --- |
| 上阶段简历与题库 | `frontend.py`、简历诊断/解析/PDF、`resume_contacts.py`、`resume_skills.py`、简历策略、照片与相关测试、`AI_interview_QA.md` |
| 共享模型文件 | `models/database.py`、`entities.py`、`repositories.py`、迁移测试同时包含照片字段与历史复合索引 |
| 工程化草案 | Redis 状态与鉴权配额适配、文件存储、FastAPI 入口、健康检查、备份工具、Docker/Compose、配置/依赖/锁文件及相应测试 |
| 本次完成 | 静态问题修复与 Ruff 配置；直接依赖一致性脚本；备份 CLI 显式源库参数及失败测试；新增数据存储专项测试；数据模型、验收、当日排期及文档入口 |
| 本地资料 | `job-search-cases/`、`output/`、录屏，以及密钥和运行数据；本次未归入代码提交，也未删除 |

本次提交收录以上代码与文档基线，既有简历增强与工程化草案的完成状态仍按各项验收证据区分。后续按模块整理时须查看具体差异，尤其共享文件，避免把既有改动误归为本次新实现。

### 遗留问题与后续验收

| 计划日 | 待办 / 边界 | 本日状态 |
| --- | --- | --- |
| 9/25 | Redis TTL、真实进程重启持久化、断连与恢复、应用配置集成 | 本地已有并发/业务串联测试通过；完整专项待验收；退款失败当前记录错误，没有自动补偿 |
| 9/26 | PDF/Word/照片/Gradio 缓存保留与删除、下载权限、OSS/MinIO 映射 | 待验收；当前无统一导出与备份到期清理 |
| 9/27 | 外部服务组合故障、重试总等待上界、恢复后的再次操作 | 待验收；当前自动化测试不代替整套故障演练 |
| 9/28 | 完整页面联调、当前版本真实 AI 与性能门禁 | 待验收；本日模型与检索业务测试使用固定数据，Chromium 仅验证 PDF 集成 |
| 9/29 | 干净环境安装、FastAPI + Gradio 实际部署、容器与持久化卷、接口文档 | 待验收；依赖检查不是干净安装或部署成功的证据 |
| 9/30 | 跨存储回滚、最终演示、版本提交号 | 待验收；本日 SQLite 恢复不代表 Redis/Chroma/文件的一致性恢复 |
| 后续维护 | 原排期的单文件 400 行目标、既有长行与大模块拆分 | 保留为维护项，不纳入本次存储验收；未通过大规模格式化掩盖已有变更 |

9/24 没有尚未通过的当日验收项。下一执行包为 9/25 Redis 专项。
