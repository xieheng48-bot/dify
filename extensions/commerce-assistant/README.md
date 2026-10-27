# 电商售后知识与工单助手

一个可运行的求职作品第一版：Python + FastAPI + SQLite，提供中文操作页、独立业务规则、公开政策检索工具，以及可选的 Dify Chatflow HTTP 集成。

此目录由 [谢恒（xieheng48-bot）](https://github.com/xieheng48-bot) 在个人下游 fork 维护；实现和测试使用 AI 辅助，详见 [AUTHORS.md](AUTHORS.md)。本扩展未合并到上游，也不代表 Dify 官方认可。Dify 平台及其原有功能属于原作者，原有许可证和署名保持不变。

**默认是离线规则模式，没有调用大模型。政策检索是品类/生效日期过滤加关键词匹配，没有实现向量 RAG、Embedding 或重排。** 这是原创的 Dify 配套工具扩展服务，没有修改 Dify 源码，也不是安装进 Dify 的插件。请按实际完成的功能描述简历。

## 启动

在仓库根目录执行以下命令。扩展使用自己的 `pyproject.toml` 和虚拟环境，独立于 Dify 核心后端：

```sh
cd extensions/commerce-assistant
uv sync --extra test
uv run uvicorn commerce_app.api:app --host 127.0.0.1 --port 8011
```

打开 [本地操作页](http://127.0.0.1:8011/)。需要已安装 [uv](https://docs.astral.sh/uv/) 和 Python 3.10 或更高版本。首次启动在当前目录创建 `commerce.db`，并按启动当天生成演示订单；再次启动保留原数据，不会把订单签收时间刷新成当天。

| 用户凭据（仅本地演示） | 订单 | 行为 |
| --- | --- | --- |
| `demo-alice` | A1001，蓝牙耳机，2 天前签收 | 可创建退货申请 |
| `demo-alice` | A1002，运动外套，40 天前签收 | 已超过 30 天申请窗口 |
| `demo-bob` | B2001，机械键盘，1 天前签收 | Bob 可查询，Alice 无权查询 |

操作顺序：核验订单 → 生成申请 → 检查订单、原因、政策 → 勾选明确确认 → 创建待审核工单。生成申请本身不创建工单，创建工单也不会退款。

## 第一版完成了什么

- 根据服务端 Bearer token 找到用户，订单与工单查询都校验归属，不接受请求体自报用户身份。
- 按订单品类与**签收日**匹配适用政策；历史版本不混入当前订单，缺失、过期或重叠政策直接拒绝自动处理。退货天数由代码计算。
- 把订单快照、完整政策及原因计算成 SHA-256 摘要，人工确认同时绑定摘要与随机确认凭据，15 分钟过期；提交时重新核验订单和政策。
- 在同一个 SQLite 写事务内完成幂等核验、工单创建、申请状态更新与审计。相同键相同请求返回原结果，不同请求返回 409。同一订单只允许一个待审核退货工单。
- 数据库存储订单、政策、申请、工单和审计；服务重启后保留状态。首页使用原生 HTML/JS，通过 `textContent` 展示数据，避免把返回文本拼成 HTML。

## 接口和身份边界

| 入口 | 身份 | 用途 |
| --- | --- | --- |
| `/health`、`/` | 无 | 健康检查及本地页面 |
| `/tools/policies/search` | `DIFY_TOOL_TOKEN` | 只查询公开演示政策，无订单和工单权限 |
| `/api/orders/{order_id}` | 用户 token | 查看自己的订单 |
| `/api/returns/check` | 用户 token | 确定性退货资格核验 |
| `/api/returns/proposals` | 用户 token | 生成待人工确认申请 |
| `/api/human/proposals/{id}`、`/api/human/confirm` | 用户 token | 查看确认内容、明确确认创建工单 |
| `/api/tickets/{ticket_id}`、`/api/audit` | 用户 token | 查询自己的工单与最近 50 条审计 |
| `/api/assistant` | 用户 token | 离线规则解释或可选 Chatflow 解释 |

[用户 API 文档](http://127.0.0.1:8011/api/docs) 使用 `/api/openapi.json`。`/openapi.json` **仅导出公开政策工具**，不会向 Dify 导入订单、申请或人工确认接口。Dify 共享工具密钥不能当成用户身份，默认 `demo-dify-tools` 不能访问 `/api/*`。

这里的演示 token 可直接复制，不具备生产登录安全性。公开政策工具的品类/日期来自调用者，只能用于知识解释；实际订单资格始终由用户 API 从数据库读取订单后独立计算。人工确认入口只由本地页面调用，Dify 未获得其凭据；真实部署需使用可靠用户登录与受信任审批界面。

## 可选：接入你自己的 Dify Chatflow

本机没有自动安装或启动 Dify，本项目也没有验证你的 Dify 实例。先运行离线功能，再自行准备 Dify 的 **Chatbot 或 Chatflow 应用**。适配器采用 `blocking` 调用，不能直接用于要求流式响应的 Agent/Legacy Agent 应用。

1. 按 [Dify 自定义工具文档](https://docs.dify.ai/en/cloud/use-dify/workspace/tools) 导入本服务的 `/openapi.json`。schema 已含必需的 `servers`，由 `DIFY_TOOL_BASE_URL` 配置（默认 `http://127.0.0.1:8011`）。例如 Dify 运行在同一 Windows 主机的 Docker Desktop 内时，先设置 `$env:DIFY_TOOL_BASE_URL = 'http://host.docker.internal:8011'`，以 `--host 0.0.0.0 --port 8011` 启动本服务，然后导入 `http://host.docker.internal:8011/openapi.json`，不需要手改 JSON。根据实际网络配置监听地址及防火墙；使用其他端口或部署地址时也要同步修改该变量。Dify Cloud 无法直接访问你的本机地址，须先自行部署到它可访问的环境；默认本地启动只用于本机演示。
2. 工具认证配置 Bearer `demo-dify-tools`（或你在进程环境中设置的 `DIFY_TOOL_TOKEN`）。只开放公开演示知识，不把用户 token 写入工具配置或提示词。
3. 配置一个输入仅需用户提问的 Chatflow，让它解释售后政策，并可调用这个只读工具。服务端传入的核验事实是回答依据；所有业务写入仍由用户界面执行。
4. 从自己的 Dify 应用取得 API 地址及应用密钥，在启动服务的 PowerShell 中设置：

```powershell
$env:DIFY_API_BASE = 'http://localhost/v1'
$env:DIFY_API_KEY = '<填写你自己的 Chatflow 应用密钥>'
uv run uvicorn commerce_app.api:app --host 127.0.0.1 --port 8011
```

`DIFY_API_BASE` 需要包含 `/v1`，程序追加 `/chat-messages`。`.env.example` 是配置示例，程序不自动读取 `.env`。实际请求传 `response_mode=blocking`，`user` 从本服务的认证身份派生；用户不能在请求体里覆盖它。Dify 的 `user` 字段用于应用内会话归属，本身不替代本服务的身份校验。[消息 API](https://docs.dify.ai/en/api-reference/chat-messages/send-chat-message) · [Dify 终端用户身份说明](https://docs.dify.ai/en/api-reference/guides/end-user-identity)

上游超时返回 504，非成功响应或缺少有效回答返回 502，不悄悄降级成“模型已成功调用”。这里没有执行真实 Dify 实例的端到端测试；自动化测试使用本地替身验证适配行为。

## 测试

```sh
uv run --extra test pytest -q
```

测试覆盖无认证、跨用户读取、共享工具密钥隔离、有效/历史/缺失/过期/重叠政策、申请与工单分离、摘要及确认凭据校验、过期确认、数据变化后的旧确认、重复请求与幂等冲突、一个订单多申请防重、工单权限、数据库重启持久化、Dify 超时/失败、离线模式标识和注入形状输入。所有测试使用临时 SQLite，无外部服务或模型密钥。

## 下一步与简历边界

当前可展示：业务规则核验、版本政策引用、人工确认与幂等工单、认证边界、SQLite 持久化、Dify 工具 API/Chatflow 适配。不可写成“已在企业生产上线”“实现向量 RAG”“完成退款”“提升准确率 xx%”。

下一步可真正补上向量检索与重排、用固定问答集测量引用准确率，接真实身份系统，并在具备授权的沙箱业务 API 上验证真实订单集成。先读 [代码导读](CODE_WALKTHROUGH.md)，按调用链自己修改一个规则并补测试。
