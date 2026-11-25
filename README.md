# boss-agent

基于自建 JSON ReAct Harness 的多 Agent 求职助手。主 Agent 根据意图动态调度独立子 Agent，支持直接岗位咨询、简历方向分析、逐步收集意向及生成计划。FastAPI 提供对话前端和 API。

## 当前能力

- Profile Agent：解析简历能力、用户约束和待补充偏好。
- Matching Agent：先检查明确城市、排除公司、薪资约束，再判断岗位匹配。
- Draft Agent：根据岗位生成沟通草稿，要求提供简历原文依据。
- 主 Agent 动态调度；各子 Agent 独立 ReAct 循环；JSON 协议、工具白名单、共享预算、SQLite 会话/计划/执行记录。
- LangGraph 完整计划组合工具继续兼容 CLI 和原计划接口，不再作为每轮聊天入口。
- demo 模式：无网络、无 API Key 的显式脚本驱动，用于检查循环与委派，不代表真实模型推理。
- llm 模式：通过 OpenAI-compatible Chat Completions 接口生成真实分析，职位仍来自导入 JSON。

**当前不具备 BOSS 搜索、登录、发送消息或发送附件功能；浏览器渠道只是显式抛出 NotImplementedError 的接口。没有任何真实投递发生。**

## 环境

使用已经创建的 Conda 环境，不要重复创建，不在 base 安装：

```bash
conda activate boss-agent
python -m pip install -e .
```

## FastAPI 服务（主要入口）

```bash
conda activate boss-agent
python -m uvicorn boss_agent.main:app --host 127.0.0.1 --port 8000 --reload
```

也可执行 `boss-agent-server`。

- 对话前端：http://127.0.0.1:8000/
- 交互式接口文档：http://127.0.0.1:8000/docs

前端可多轮描述求职要求、粘贴/导入 TXT 或 Markdown 简历、导入职位 JSON、生成并查看计划。会话保存到本地 SQLite，刷新可以恢复。演示模式使用脚本驱动；真实意图理解和工具选择需要服务端配置 Key 和模型名。

- `GET /health`：服务状态。
- `POST /api/v1/sessions`：创建会话。
- `GET /api/v1/sessions`：最近会话。
- `GET /api/v1/sessions/{session_id}`：会话历史。
- `POST /api/v1/sessions/{session_id}/messages`：主 Agent 动态处理多轮对话或生成计划。
- `GET /api/v1/sessions/{session_id}/executions/{run_id}`：JSON 执行记录。
- `POST /api/v1/plans`：提供简历文本、求职要求、职位列表和 mode，生成并保存计划。
- `GET /api/v1/plans/{run_id}`：读取已保存计划。

示例请求：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/plans -H 'Content-Type: application/json' --data-binary @examples/plan-request.json
```

服务默认只绑定本机，当前没有鉴权和后台任务队列；生成计划是同步请求，不执行真实投递。

架构、Harness 协议、用户画像、Langfuse、接口、运行配置与测试统一见 [技术方案](docs/technical-design.md)。

## 演示运行（CLI）

在项目根目录执行：

```bash
boss-agent --mode demo --resume examples/resume.md --jobs examples/jobs.json --requirements "杭州，AI Agent 应用开发，月薪不低于 20000"
```

结果写入 `data/plan.json` 和 `data/boss-agent.sqlite3`，终端明确显示真实投递数量为 0。
演示模式不实际解析输入，不用于判断真实匹配效果。

## 模型分析

参考 `.env.example`，在终端设置 `BOSS_AGENT_API_KEY`、`BOSS_AGENT_MODEL`，以及可选的 `BOSS_AGENT_BASE_URL`。
`.env.example` 不会自动加载。不要把 Key 写进代码或 Git。

```bash
boss-agent --mode llm --resume /absolute/path/resume.md --jobs examples/jobs.json --requirements "杭州，偏 Agent 应用开发"
```

省略 `--requirements` 可以运行简历驱动模式；缺失偏好应出现在结果中。
模型模式会将简历和输入职位发送给你配置的模型服务商。
当前仅接受 UTF-8 TXT/Markdown 简历，PDF/DOCX 解析尚未实现。

## 代码组织

- `models.py`：Agent 之间传递的数据结构。
- `agents/profile/`、`agents/matching/`、`agents/drafting/`、`agents/conversation/`：四个独立 Agent，各自管理类和提示词。
- `harness/`：统一 ReAct 运行器、JSON 协议、预算和事件。
- `workflow.py`：可选的完整计划组合工具，尚未启用检查点续跑。
- `channels.py`：职位导入与待实现的 BOSS 渠道。
- `llm.py`：模型适配，不绑定单一供应商。
- `storage.py`：计划落库，不代表已经执行投递。
- `cli.py`：命令行入口。

## 验证

```bash
python -m unittest discover -s tests -v
```

## 后续接入顺序

1. 验证本人登录后的 BOSS 网页，接入职位读取及搜索。
2. 增加简历文档解析、求职偏好确认和草稿审阅入口。
3. 实现单账号串行浏览器执行、职位去重、消息/附件分开记账。
4. 对提交后超时增加结果核对；结果未知时不能直接重复发送。
5. 补充检查点恢复、token/费用配额与真实模型匹配评测。

模型引用原文校验不能证明所有表述都准确，自动发送前还需要事实核查。
流程状态恢复无法撤回已经发送的消息。

普通岗位咨询无需简历或职位。仅简历方向分析无需导入职位；只有完整计划工具需要两者。执行记录包含私人资料，当前适合本地使用，用户标识只做逻辑分区，没有登录鉴权。运行事件是结束后保存的追踪记录，尚不支持逐步断点恢复。

## Langfuse Agent 监测

已接入主 Agent、子 Agent、ReAct 步骤、工具、模型 generation、校验失败与预算终止。默认关闭远端上报与正文采集，配置方式见 [技术方案](docs/technical-design.md)。SDK 是可选依赖：在已有 boss-agent 环境安装 `python -m pip install -e ".[observability]"`。

## 姓名 / user_id 与长期画像

页面顶部输入姓名或 user_id 后切换用户，画像和会话按标识保存。右侧“长期求职画像”支持查看、纠正和清除。新会话自动加载同一用户同一模式的职业背景和明确偏好；临时会话偏好不覆盖长期偏好。详见 [技术方案](docs/technical-design.md)。当前是本地逻辑分区，没有登录认证；重名请使用不同 ID。
