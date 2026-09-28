# HTTP 接口与 Gradio 业务入口

适用版本：9/29–30 工程化交付。实际路由定义在 `src/pbl_jobs_finder/server.py`；启动后可访问 `/docs` 和 `/openapi.json`。以下 REST 路由在挂载根路径 Gradio 之前注册。

## 鉴权与会话

受保护接口使用 `Authorization: Bearer <token>`。Token 由页面验证码登录获得；当前验证码为控制台 Mock 投递，没有短信服务，也没有独立的 REST 登录接口。手机号、验证码、Token 不应写入调用日志。

| 方法与路径 | 身份要求 | 成功响应 / 用途 |
| --- | --- | --- |
| `GET /health/live` | 无 | 200，`{"status":"ok"}` |
| `GET /health/ready` | 无 | 200 或 503，检查 SQLite 连接及配置的 Redis |
| `POST /api/session` | Bearer Token | 204；验证登录并设置下载 Cookie，无请求体 |
| `DELETE /api/session` | 无 | 204；清除下载 Cookie，无请求体 |
| `GET /api/quota` | Bearer Token | 200；当天共享配额 |
| `GET /api/history` | Bearer Token | 200；当前用户各最近 5 条简历、面试摘要 |
| `GET /api/history/resumes/{record_id}` | Bearer Token | 200；当前用户的简历详情 |
| `GET /api/history/interviews/{session_id}` | Bearer Token | 200；当前用户的面试详情 |
| `GET /api/downloads/{key}` | Bearer Token 或下载 Cookie | 200；归属校验后的 PDF / DOCX 文件 |

`DELETE /api/session` 仅清 Cookie，不撤销 Token。完整退出由 Gradio 退出回调撤销 Token、清浏览器 LocalStorage，再清下载 Cookie。下载 Cookie 名为 `pbl_download_session`，HttpOnly、SameSite=Strict、Path=/api/downloads、最长 7 天；HTTPS 请求下带 Secure。反向代理必须正确传递协议，仅信任实际代理地址。

所有 `/api/` 响应设置 `Cache-Control: no-store`。下载若同时提供 Header 与 Cookie，以 Header 为准。其他 REST 接口不接受下载 Cookie。历史读取、下载、会话 Cookie 同步均不扣配额。

## 请求与响应示例

在已登录浏览器的同源开发者控制台中读取当前 Token，勿粘贴至工单或保存日志：

```javascript
const token = localStorage.getItem('pbl_jobs_finder.auth_token');
const response = await fetch('/api/quota', {
  headers: {Authorization: `Bearer ${token}`}
});
await response.json();
```

配额示例（`day` 使用 `APP_TIMEZONE`，默认 Asia/Hong_Kong）：

```json
{"limit":10,"used":2,"day":"2026-09-28","remaining":8}
```

空历史为 `{"resumes":[],"interviews":[]}`；有记录时返回：

```json
{
  "resumes": [{"record_id":1,"created_at":"2026-09-28 10:00","target_position":"Python 工程师","score":82}],
  "interviews": [{"session_id":1,"created_at":"2026-09-28 10:05","position":"Python 工程师","question_rounds":5,"status":"completed"}]
}
```

`created_at` 是应用时区格式化后的展示文本；摘要按创建时间和 ID 倒序。无分页参数。详情 ID 必须为整数；不存在及不属于当前用户的记录均返回 403，避免区分记录是否存在。

| 简历详情字段 | 含义 |
| --- | --- |
| `record_id, created_at, target_position, score` | 标识、时间、目标岗位与匹配分 |
| `grade, policy_version, template_id` | 等级、诊断策略、导出模板 |
| `missing_keywords, suggestions, optimized_text` | 缺失关键词数组、建议、优化稿 |
| `diagnosis` | 结构化诊断对象；旧数据或损坏 JSON 按实现降级 |

| 面试详情字段 | 含义 |
| --- | --- |
| `session_id, created_at, position, status, question_rounds` | 会话摘要 |
| `mode, feedback_mode, policy_version, difficulty` | 模式、反馈时机、策略与难度 |
| `conversation` | 已保存的问答及反馈数组 |
| `report` | 结构化最终报告；未完成或旧记录可能为空对象 |

下载 key 使用页面返回的值，形式为 `<record_id>-<32位十六进制UUID>.pdf` 或 `.docx`，不要自行拼接路径。文件自生成起有效 24 小时，每次请求验证所属记录与当前登录态；过期或文件缺失返回 404。系统每小时清理受管过期文件，过期判断不依赖清理是否已经执行。通用 Gradio `/file=...`、`/file/...`、`/stream/...` 被拒绝，返回 403。

## 健康检查边界

```json
{"status":"ready","checks":{"database":"ok","state":"redis"}}
```

内存模式的 `state` 是 `memory`。SQLite 执行 `SELECT 1` 或 Redis PING 失败时，相关值为 `unavailable`，HTTP 503，`status` 为 `unavailable`。存活检查仍可为 200。就绪检查不调用 LLM/Embedding，不检查题库条数、索引检索、Chromium、中文字体或目录可写性；发布前还需执行 `scripts/check_deployment.py --release` 和浏览器验收。

## 错误格式

应用已知错误返回以下结构，`error_id` 在 5xx 时提供可查日志的编号：

```json
{
  "success": false,
  "error": {"code":"11001","description":"身份认证失败","message":"登录已失效，请重新登录"},
  "error_id": null
}
```

| HTTP | 业务码 | 场景 |
| --- | --- | --- |
| 401 | `11001` | 缺少、过期或已撤销 Token |
| 403 | `12003` / `13003` | 无权读取面试 / 简历记录 |
| 429 | `11002` | 配额耗尽（主要由业务回调触发） |
| 503 | `11003` | Redis 鉴权/配额状态不可用 |
| 503 | `12002, 13002, 13004, 13006, 13008, 14001, 14002, 15001, 15002` | 对应基础服务/模型/导出类错误；并非每类都会经 REST 暴露 |
| 400 | `12001, 13001, 13005, 13007` | 已知输入/结构校验错误 |
| 500 | `10000` | 未预期异常；仅返回通用消息及错误编号 |

框架自身错误采用 FastAPI 格式：下载 404 和文件路由 403 返回 `{"detail":"..."}`；非法整数路径参数返回 422 与 `detail` 数组。不要假定所有非 2xx 响应都含 `error.code`。完整业务码定义见 `exceptions.py`。

## Gradio 与 REST 的区别

发送验证码、登录、诊断、生成简历、开始面试、提交/跳过回答和退出均由页面 Gradio 队列回调完成，`api_name=False`。这些入口不是稳定公开 REST API，也没有承诺通用 `gradio_client` 接入合同。客户端应使用现有页面完成业务流程，受支持的查询和下载接口以上表为准。一次完整诊断及一次新建面试各计 1 次；同会话答题、报告、历史、下载不重复计次。
