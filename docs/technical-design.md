# boss-agent 当前技术方案

更新日期：2026-11-26。本文是项目技术方案的唯一说明文件，依据当前源码整理，包含架构、运行协议、用户画像、监测、接口、部署与测试。项目根目录 README 作为启动入口指向本文。

## 1. 项目目标与实现范围

boss-agent 是本地运行的多 Agent 求职助手。用户可以直接咨询岗位技能，提供简历讨论适合的方向，在对话中逐步明确求职意向，或要求匹配导入的职位并生成沟通草稿。主 Agent 根据当前意图选择直接回复或委派子 Agent。

目前已经实现：

- FastAPI HTTP 服务及同源对话前端。
- 四个独立 Agent、自建 JSON ReAct Harness 和动态工具调度。
- 姓名 / user_id 标识、会话分区、跨会话长期求职画像。
- 明确偏好、会话临时侧重、近期关注和有来源的职业事实管理。
- 导入职位的匹配与计划生成、SQLite 持久化及执行记录查询。
- Langfuse 可选监测，包括 Agent、步骤、工具、模型请求和错误。
- 离线自动化测试及已有接口、CLI 兼容。

当前没有实现 BOSS 网页登录、职位搜索、发起沟通、发送附件简历或互联网检索。浏览器渠道的方法明确抛出 NotImplementedError；所有成功计划均标注 applications_sent=0，不能视为已经投递。

演示模式是公开标记的脚本驱动，不代表真实模型推理或真实匹配质量。模型模式已有调用接口和运行机制，仍需要配置服务商并评测真实样本；测试通过不等于生产效果已经验证。

## 2. 技术栈与分层

运行环境沿用已有 Conda 环境 boss-agent，当前开发解释器为 Python 3.12。项目声明 Python>=3.12；不重复创建环境，也不在 base 或系统 Python 安装依赖。

主要技术：FastAPI、Uvicorn、Pydantic、HTTPX、SQLite、自建 Harness。LangGraph 用于按需调用的完整计划组合工具。前端使用 HTML/CSS/JavaScript，无需 Node 构建。Langfuse SDK v4 属于 observability 可选依赖；Playwright 属于未来浏览器接入的 browser 可选依赖。

调用分层：HTTP 层校验输入和归属；ChatService 加载会话与用户画像；主 Agent 通过 Harness 直接回复或选择工具；工具可以委派独立子 Agent；结果校验后，业务层提交会话、画像、计划和执行记录。监测层嵌入运行过程，不决定业务路由。

Agent 的“独立”指独立类、提示词、工具和输出契约。目前它们在同一个 Python 服务进程中运行，并非独立网络服务。共享的模型适配器不等于共享可变对话状态。

## 3. 代码组织

所有业务源码位于 src/boss_agent/：

- main.py、server.py：FastAPI 应用工厂、生命周期、静态资源和启动入口。
- api/：会话、计划、画像接口；请求响应模型；依赖及用户归属检查。
- chat.py、chat_models.py：每轮对话、偏好合并、画像载入和事务提交。
- service.py：显式计划 API 的应用服务。
- agents/conversation/：主对话与动态调度 Agent，含 agent.py、prompts.py、schemas.py。
- agents/profile/：简历分析 Agent，含独立 agent.py 和 prompts.py。
- agents/matching/：匹配 Agent，含独立 agent.py 和 prompts.py。
- agents/drafting/：文案 Agent，含独立 agent.py 和 prompts.py。
- agents/__init__.py：注册 Agent、创建本轮共享上下文、兼容原调用接口。
- harness/protocol.py、harness/runtime.py：JSON 协议、ReAct 运行器、工具校验、预算和事件。
- portraits/：长期画像数据结构、事实更新、过期过滤与有限量检索。
- llm.py：OpenAI-compatible Chat Completions JSON 适配。
- models.py：Profile、Job、Match、Draft、Plan 业务模型。
- channels.py：JSON/内存职位源、待实现的 BOSS 浏览器渠道。
- workflow.py：完整计划的 LangGraph 组合工具。
- storage.py：SQLite 会话、画像、计划和执行记录存储。
- observability/：可选 Langfuse 适配与故障隔离。
- web/：对话页面、用户切换、资料输入、画像编辑及计划展示。
- cli.py：文件输入的命令行入口。

仓库其他目录：examples/ 保存虚构示例；tests/ 保存自动化测试；data/ 是默认运行数据位置，已忽略 Git；docs/ 仅保留本文。

## 4. 主 Agent 动态调度

每轮对话接收历史消息、有效求职偏好、相关长期画像、是否有简历、导入职位数量及 action_hint。主 Agent 不需要先执行画像、匹配和文案的完整流程。

可选行为：

- 岗位技能咨询：可以直接回答，不要求用户先交简历或职位。
- 简历方向分析：调用画像 Agent，观察结果后解释依据、缺口和可探索方向。
- 意向收集：根据交流更新明确偏好或追问必要信息。
- 岗位匹配：针对实际导入职位调用匹配 Agent。
- 文案准备：针对合适岗位调用文案 Agent。
- 计划生成：自然语言请求或按钮可以触发完整计划工具。
- 资料不足：说明当前任务缺少什么，不把所有偏好当成每轮必填项。

主 Agent 的工具注册表：

- analyze_resume：委派 ProfileAgent；需要当前简历，不需要导入职位。
- read_jobs：读取本次导入的职位描述，不联网搜索。
- match_job：按 job_id 委派 MatchingAgent；需要实际简历和该导入职位。
- draft_message：按 job_id 委派 DraftingAgent；必要时先取得匹配结果，skip 岗位拒绝生成草稿。
- build_plan：生成完整本地计划；需要非空简历和至少一个导入职位。

主 Agent 可以自由选择这些能力，但不能执行未注册的 shell、任意 HTTP 或外部发送操作。自然语言调度质量取决于模型，当前离线测试验证的是执行契约，不保证真实模型每次都会选对工具。

## 5. 子 Agent 与组合计划

ProfileAgent 通过 read_resume 工具读取实际简历，输出技能、经历摘要、建议方向和待了解偏好。

MatchingAgent 通过 read_candidate、read_job 读取候选资料、职位和 hard_skip_reason。公司命中排除名单、城市不在明确允许列表、薪资上限低于最低月薪时，最终 decision 必须为 skip，模型不能覆盖代码计算出的硬约束。结果 job_id 必须与任务一致。

DraftingAgent 通过 read_evidence 读取简历、职位和匹配依据。结果 job_id 必须一致；模型模式下 resume_evidence 非空且每项为简历的非空原文子串。该检查不能证明每句话都准确，不等于完成事实一致性评测。

三个子 Agent 都使用 ReAct 循环，并在最终输出前成功读取必要资料。工具结果先进入模型上下文，再由模型决定继续工具调用或提交结果。

build_plan 内部保留 LangGraph 顺序节点：profile_agent、discover、match_agent、draft_agent。它是按需调用的组合工具，也服务原有计划 API 和 CLI。discover 目前返回导入职位，不根据方向联网发现新职位；skip 不生成草稿。该组合工具不是每轮聊天的强制路线。

## 6. Harness 与 JSON 协议

Harness 使用结构化动作/观察循环，设计参考 [ReAct 原始论文](https://arxiv.org/abs/2210.03629)。它记录可观察的动作、结果与错误，不要求模型输出或保存完整私有思维链。

运行契约：

- Task：task_id、parent_id、agent、input。
- Decision：action=tool 或 final；包含工具名与 arguments，或最终 result。
- Observation：tool、status、data、error。
- Result：task_id、agent、status、data、error。

工具决策示例：

```json
{"action":"tool","tool":"analyze_resume","arguments":{}}
```

最终决策示例：

```json
{"action":"final","result":{"reply":"该岗位通常需要 Python 基础和工程实践。","intent":"question"}}
```

tool 决策不能同时提交 result；final 决策不能同时指定工具与参数。协议模型拒绝额外字段。空参数工具也会拒绝多余参数；JobArgs 要求 job_id。

一次执行先检查预算和深度，再建立任务、工具目录及输出 schema。模型提交工具调用后，Harness 校验白名单和参数、执行工具、将 JSON 观察加入本轮 history，随后再次调用模型。最终输出需通过业务 schema 和证据校验，才能形成完成结果。

任务与完成结果经过 JSON 序列化、反序列化校验，工具观察也使用 Observation 校验。非法工具、参数、决策或最终输出作为标准错误观察反馈，在剩余预算内可修正；不自动假装成功。主 Agent 委派子任务时，parent_id 和共享运行上下文保留关联。

每个 Agent 仅看到自己的工具目录。简历、职位、工具结果作为待处理数据，提示词要求不能将其当系统指令；这不是完整提示注入防护的证明。

## 7. 用户身份与逻辑分区

第一版使用显式 user_id 或姓名。页面顶部输入并切换用户，后端通过 X-User-ID 识别。中文在请求头中使用 URL 编码，前端自动处理。

标识做 NFC 规范化与首尾空格清理，允许字母、数字、中文、内部空格、下划线、短横线，长度 1–64。英文大小写不同视为不同标识。同名代表同一逻辑用户，重名应加后缀区分。未提供请求头时使用 local-user。

会话列表、会话读取、消息发送、计划读取、执行查询按归属检查，其他用户的记录 ID 返回 404。画像以当前用户为目标读取或修改。

这只是本地开发中的逻辑分区。没有登录、密码、令牌或真实身份校验，用户可以声明别人的姓名；不能把它描述为安全的多租户认证。未来可以替换 current_user 依赖接入登录系统。

## 8. 长期画像、会话状态与时间变化

长期画像与单次聊天分开存储，UserPortrait 的主键是 user_id + mode。demo 与 llm 模式隔离，避免虚构演示材料污染真实记忆。

画像包含：

- preferences：明确的 target_roles、cities、minimum_monthly_salary、excluded_companies。
- preference_evidence：原话、来源会话、来源类型和更新时间。
- facts：skills、experience、current_focus；每项带 value、evidence、source、session_id、updated_at、expires_at。
- revision：画像版本。
- history：替换、补充、移除的变更事件，最多保留最近 100 次。

Session 保存本轮消息、当前简历、职位、preferences、preference_overrides、画像快照与版本、建议方向、计划关联和执行摘要。完整简历不会因为画像记住某项技能而在新会话中自动补全。

创建会话时加载画像；每轮发送前再次读取最新版本。长期偏好作为基础，当前会话覆盖和最新明确指示可以调整本轮重点。

preference_updates 引用当前用户消息的非空原文。scope=persistent 修改长期偏好；scope=session 仅覆盖本次会话。例如长期城市是杭州，但本次明确先看上海，则当前会话用上海，新会话仍加载杭州。

portrait_updates 只允许技能、经历和近期关注，包含 source、evidence、operation。message 证据必须出自本轮用户消息；resume 证据必须出自当前简历，且本轮已经成功调用画像子 Agent。普通 question 意图不能写职业事实。推测适合的岗位放在 suggested_roles，不自动当成已掌握技能或明确目标。

技能和经历按字段及值去重，不因一次没提到就删除；目前没有语义实体合并。近期关注默认 30 天后从活动上下文排除，原记录不会自动物理删除。职业事实总量上限为 150 条。

每轮给主 Agent 的画像排除 history 和过期关注，优先近期关注、英文技能关键词的词面匹配、再按更新时间排序，最多 24 条。当前没有向量数据库或独立语义检索系统。

原文引用校验不保证语义正确，例如“我不会 Python”能否被正确识别仍需样本评测。不会根据岗位咨询推断性格、健康等无关特征。

右侧画像面板支持读取、JSON 纠正和清除。清除需再次点击确认，写入更高版本的空画像，阻止运行中的旧快照覆盖；它不删除历史会话、原简历或执行记录。之后主动重新分析旧简历，仍可能重新形成相关事实。

## 9. 数据存储与一致性

默认数据库为 data/boss-agent.sqlite3，API 通过 BOSS_AGENT_DB_PATH 配置；相对路径以服务启动目录为准。CLI 使用自己的 --db 参数，默认值相同，但不读取该环境变量。

SQLite 表：

- chat_sessions：id、version、payload、updated_at；payload 保存用户归属与会话 JSON。
- user_portraits：user_id、mode、revision、payload；联合主键隔离画像。
- plans：run_id、mode、payload、created_at、user_id。
- executions：run_id、session_id、payload，包含父子任务事件及监测关联 ID。

初始化创建缺失表，并为旧 plans 表增加 user_id；旧记录默认归 local-user。新增会话字段有默认值。不会自动批量解析旧聊天生成长期画像。

模型请求在数据库事务之外执行。成功后，更新会话时同时检查 session.version 与本轮读取的 portrait.revision，再在同一事务保存画像变化、新计划和执行记录。冲突返回 409 并回滚，避免跨会话旧画像覆盖新内容。失败执行另存审计，不提交半条对话。

普通问答且画像无变化时不增加画像版本。手工编辑或自动更新会增加 revision。并发失败仍可能已消耗模型调用成本；当前没有请求幂等键。

无关问答保留已有计划；简历、导入职位或有效偏好变化使当前计划关联失效，历史计划不删除。计划计算完成才提交，CLI 的 JSON 文件与数据库写入没有跨资源事务。

执行事件在任务结束或失败时保存，包含 sequence、task_id、parent_id、agent、决策、工具观察和状态。它不是逐步 checkpoint，进程崩溃后不能自动恢复未完成步骤。

## 10. 模型接入与配置

llm.py 使用 HTTPX 请求 BOSS_AGENT_BASE_URL/chat/completions，兼容标准 Chat Completions 消息结构。system 包含任务要求和 JSON schema，模型文本结果通过 Pydantic 校验。

必要配置为 BOSS_AGENT_API_KEY、BOSS_AGENT_MODEL；BOSS_AGENT_BASE_URL 默认 https://api.openai.com/v1。不绑定单一模型供应商，但不是所有兼容服务都已经验证。

当前不使用供应商专有的强制结构化输出扩展，不自动剥离 Markdown 代码围栏；不合法 JSON 或不符合 schema 的输出可能在 Harness 剩余步数内被要求修正。

模型模式会把相关对话、简历、导入职位和检索出的长期画像发送给配置的服务商。没有真实服务商配置时仍可使用 demo 模式。demo 不伪装真实的偏好提取和画像学习，手工编辑可用于验证画像加载机制。

.env.example 只是配置示例，项目不自动读取 .env 文件。需在启动服务的终端设置环境变量；修改后重启。实际 Key 不应写进仓库或文档。

## 11. Langfuse 的 Agent 运行监测

observability 可选依赖声明 langfuse>=4.18,<5，当前适配 SDK v4。Langfuse 是可选监测，不是运行求职服务的前置条件，也不是业务画像的存储来源。

一轮协作对应 boss-agent.run 根 trace，向下有 Agent、ReAct 步骤、工具与子 Agent、真实模型 generation：

- agent.conversation / profile / matching / drafting：任务、驱动类型、状态、耗时及包含委派的决策计数。
- react.step：步骤序号、action、所选工具；未知工具归为 unregistered。
- tool.*：参数校验、调用、观察状态；委派子 Agent 嵌套在相应工具节点下。
- llm.request：真实 HTTP 模型请求、模型名、耗时及供应商真实返回的用量。
- harness.protocol_error / task_failed：结构或业务校验失败、异常与预算终止。

SDK 上下文传播保留父子关系，session_id 关联同一用户会话的多轮运行，harness_run_id 对应本地运行 ID。user_id 使用姓名/标识的 SHA-256 摘要。run_id 的稳定哈希映射为 trace ID，写入 last_execution.langfuse_trace_id 及本地执行记录。

有 trace ID 只表示本地创建过追踪上下文，不证明远端成功接收。没有配置真实 Langfuse 项目时，远端控制台接收尚未验证。

只映射供应商实际返回的 prompt_tokens、completion_tokens、total_tokens 为 input、output、total；缺少用量时不填造数据。演示模式没有真实 generation。没有硬编码费用，成本估算依赖 Langfuse 是否识别模型及价格配置。

监测默认关闭。开启但缺少 Key 或地址时记录警告并继续业务。SDK 初始化、observation 开始/更新/结束、flush、shutdown 都尽力隔离故障，不吞掉业务异常。

默认正文采集关闭，仅上报结构、ID、状态、耗时和用量。开启 BOSS_AGENT_LANGFUSE_CAPTURE_CONTENT 后，相关对话、简历、画像、职位、工具正文和模型输入输出可能发送到配置的 Langfuse 服务。适配器递归屏蔽常见凭证字段和当前环境密钥值，截断单个字符串至 20000 字，但不是全面个人信息匿名化。

异常只上报类型和标准码，避免业务异常原文/堆栈自动进入 SDK。导出过滤仅接受本应用 Langfuse SDK observation。FastAPI 正常关闭和 CLI finally 会 flush、shutdown，不对每次工具调用同步刷新。强制杀进程不能保证最后一批监测记录完整。

用户选择 Langfuse Cloud 时，无需自己部署 Langfuse 服务；需在对应项目取得地址、Public Key 和 Secret Key。代码也接受自建地址，不会自动创建账户或部署 Langfuse。

## 12. HTTP 接口与请求规则

服务默认监听本机 127.0.0.1:8000。根路径 / 是前端，/docs 为自动生成的 API 调试页，/openapi.json 为接口规范；这与仓库 docs/ 下的技术方案文件不同。

基础及会话接口：

- GET /health：服务可响应及真实投递关闭标志，不检测模型/监测远端连通性。
- GET /api/v1/config：报告模型配置是否存在，不证明调用可成功。
- POST /api/v1/sessions：mode=demo 或 llm，创建当前用户会话并加载画像。
- GET /api/v1/sessions：当前用户最近最多 50 条会话摘要，不返回完整简历。
- GET /api/v1/sessions/{session_id}：历史、资料、偏好和计划/执行关联。
- POST /api/v1/sessions/{session_id}/messages：提交 version、message，及可选 action、resume_text、jobs。
- GET /api/v1/sessions/{session_id}/executions/{run_id}：读取本会话的 JSON 执行记录。

消息 action 默认 chat，plan 是明确生成完整计划的快捷请求。message 非空且最多 6000 字；会话最多 30 轮；resume_text 最多 100000 字；jobs 最多 100 个且 ID 唯一。省略或传 null 的资料字段保留原值；resume_text 传空字符串、jobs 传空数组可清空对应资料。显式 plan 需要非空简历及至少一个职位。

画像接口：

- GET /api/v1/users/me/portrait?mode=llm：完整画像、history 和 active_facts。
- PUT /api/v1/users/me/portrait?mode=llm：revision 必填，可修改 preferences、skills、experience、current_focus；省略字段保留。
- DELETE /api/v1/users/me/portrait?mode=llm&revision=N：清除该模式长期画像，保留更高版本。

画像接口 mode 默认 demo。手工技能数组最多 100 项，经历最多 50 项，单条事实最多 1000 字，合计仍受 150 条事实限制。空数组清空对应列表，current_focus=null 清空近期关注。

兼容的显式计划接口：POST /api/v1/plans 接受 resume_text、requirements、jobs、mode；GET /api/v1/plans/{run_id} 读取当前用户计划。requirements 最多 20000 字；此入口允许空职位数组。成功创建返回 201、status=planned、applications_sent=0。

会话和画像都使用版本号处理并发。不存在或不属于当前标识的会话/计划/记录返回 404。422 为输入或必要资料错误，503 为模型配置缺失，429 为会话 Harness 预算耗尽，502 为模型/协议失败，409 为版本冲突，内部数据库等未处理异常为 500。显式计划接口将 ValueError 类预算失败按其现有错误转换返回 502。

FastAPI 默认请求校验错误可能包含输入字段值；上游模型错误正文不会直接回显。错误响应目前不返回失败执行的 run_id。

## 13. 前端交互

页面包括最近会话、姓名/user_id 切换、聊天区、模式选择、简历/职位输入、长期画像编辑、意向及协作摘要、计划与文案展示。窄屏通过按钮展开资料与历史会话区域。

TXT/Markdown 简历和职位 JSON 先在浏览器读取，2 MB 以内；简历另受字数限制。内容随消息发送，提交成功后才保存。PDF/DOCX 解析尚未实现。

普通问答不会因为未完成的无效职位 JSON 草稿而被阻止；显式生成计划时检查资料。模型未配置时显示错误并保留消息，不伪造智能分析。当前不支持流式输出、语音、后台任务或完成推送。

浏览器 localStorage 只保存当前用户标识，sessionStorage 按用户保存当前会话 ID；不把完整简历持久保存在浏览器存储。切换用户会清除页面中的上一用户内容，加载对应会话和画像。尚未发送的编辑不保证保留。

## 14. 安装、配置与运行

在项目根目录执行，始终使用已存在的 boss-agent 环境：

```bash
conda activate boss-agent
cd /Users/dufeng/个人资料/项目/boss-agent
python -m pip install -e .
```

如需 Langfuse 可选能力：

```bash
python -m pip install -e '.[observability]'
```

模型配置示例，实际值仅在本地终端设置：

```bash
export BOSS_AGENT_API_KEY='你的模型服务 Key'
export BOSS_AGENT_MODEL='你的模型名称'
export BOSS_AGENT_BASE_URL='你的兼容接口地址，包含 /v1 等服务商要求的前缀'
export BOSS_AGENT_DB_PATH='data/boss-agent.sqlite3'
```

Langfuse Cloud 可选配置：

```bash
export BOSS_AGENT_LANGFUSE_ENABLED=true
export LANGFUSE_BASE_URL='你的 Langfuse 项目所属地区地址'
export LANGFUSE_PUBLIC_KEY='你的项目 Public Key'
export LANGFUSE_SECRET_KEY='你的项目 Secret Key'
export LANGFUSE_TRACING_ENVIRONMENT=development
export BOSS_AGENT_LANGFUSE_CAPTURE_CONTENT=false
```

Key 与地区地址须匹配。未准备监测服务时保持 BOSS_AGENT_LANGFUSE_ENABLED=false，不影响业务启动。

启动前端与 API：

```bash
python -m uvicorn boss_agent.main:app --host 127.0.0.1 --port 8000 --reload
```

也可运行 boss-agent-server，固定本地地址和端口、不启用 reload。保持终端运行，Control+C 正常停止。自动 reload 会重新载入服务；环境变量变更应在正确终端重新启动。

CLI 演示：

```bash
boss-agent --mode demo --resume examples/resume.md --jobs examples/jobs.json --requirements '杭州，偏 Agent 应用开发'
```

CLI 默认生成 data/plan.json 与数据库计划记录。没有真实投递。默认用户标识为 local-user，CLI 不管理跨会话长期画像。

## 15. 接口体验示例

健康检查和虚构计划：

```bash
curl http://127.0.0.1:8000/health
curl -X POST http://127.0.0.1:8000/api/v1/plans -H 'Content-Type: application/json' --data-binary @examples/plan-request.json
```

为便于复制，下面使用 ASCII 用户 ID。中文姓名可由 urllib.parse.quote 生成请求头值，前端已自动处理。

```bash
curl -X POST http://127.0.0.1:8000/api/v1/sessions -H 'Content-Type: application/json' -H 'X-User-ID: demo-user-01' -d '{"mode":"demo"}'
curl 'http://127.0.0.1:8000/api/v1/users/me/portrait?mode=demo' -H 'X-User-ID: demo-user-01'
```

后续消息使用返回的 session_id 和当前 version；画像编辑使用最新 revision，避免重复发送过期版本。前端或 /docs 页面可以直接调试，具体响应结构以当前 OpenAPI 为准。

## 16. 预算、故障处理与安全边界

一轮主 Agent 及全部子 Agent 共用 RunContext，默认最多 80 次决策；每个 Agent 任务最多 10 步；嵌套深度 3；协作时间预算 240 秒。演示步骤也计入。begin_run 重置本轮 ID、计数、事件，避免跨轮复用记录。

预算不是 token 或费用硬配额。时间在模型/工具调用前后检查，不会强制取消正在进行的同步 HTTP 请求，实际结束时间可能超预算；模型适配器的 HTTPX 90 秒是请求阶段超时，不是完整工作流总超时。

模型调用按普通 def 路由在线程池执行；HTTP 请求仍同步等待，容量受线程池、模型吞吐和预算影响。大批量职位可能在 80 次全局决策内无法完成，应缩小批次。尚无队列、Worker、任务取消或跨进程执行锁。

数据库与本地执行记录包含私人求职材料，默认 data/ 已忽略 Git。被忽略不代表可以删除。源码、依赖配置、.env、Conda 环境、数据库均不属于开发残留；尤其数据库保存真实会话、长期画像和计划。

当前没有登录鉴权、业务数据加密、完整脱敏日志或数据保留周期管理。监测默认不采集正文，也不等于所有本地文件已经脱敏。

未来外部投递至少需要区分 confirmed_success、confirmed_failure、unknown。提交后超时应先核对外部记录，再决定是否重试。恢复图状态或数据库快照不能撤回已经发送的消息；当前没有任何外部发送工具。

## 17. 测试与验收

测试使用 unittest、TestClient、HTTPX MockTransport、脚本模型、临时 SQLite 数据库；真实 Langfuse SDK 验证使用内存导出器，不访问 Cloud。只在已有 boss-agent 环境执行：

```bash
conda activate boss-agent
python -m unittest discover -s tests -v
```

覆盖模块：

- test_harness.py：动态委派、观察回传、非法工具/参数、结果修正、预算/深度/时间、偏好证据及运行审计。
- test_agents.py：子 Agent 必需读取、硬约束不可覆盖、job_id 与文案原文证据。
- test_chat.py：会话持久化、跨轮上下文、资料变化和计划失效、配置错误、版本冲突。
- test_portraits.py：中文姓名、归属分区、跨会话加载、临时/长期偏好、过期过滤、纠正清除、模式隔离、CAS 回滚、迁移及简历事实。
- test_observability.py：禁用/缺配置、监测故障隔离、正文开关、凭证屏蔽、父子关系、真实用量、trace ID 与关闭刷新。
- test_api.py：显式计划接口、输入校验、读取、服务重建和错误转换。
- test_llm.py：模型协议、结构校验、上游 HTTP 错误。
- test_service.py、test_workflow.py：组合工具兼容、skip 不生成草稿、空输入、重复职位、参数化查询。
- test_cli.py：离线命令、输出文件和失败退出码。

本次文档整理对当前代码重新执行上述测试：64 项通过。若未安装 Langfuse 可选依赖，真实 SDK 专项会跳过，不能将跳过理解为该集成已验证。

已有浏览器验证使用独立测试数据库和虚构用户，覆盖对话/计划展示、刷新恢复、中文用户切换、画像保存、新会话加载，以及另一用户看到空画像。此类手工验证不计入 unittest 数量。

可手工验收：先直接问技能，再只用简历分析方向；交流长期偏好和本轮临时关注；新建会话检查长期画像；切换姓名检查分区；显式请求计划后查询执行记录。真实模型的意图识别、工具选择、否定句理解、事实一致性及匹配效果仍需匿名标注集评测。

## 18. 尚未实现与后续顺序

尚未交付：BOSS 网页登录/搜索/真实投递、外部知识检索、PDF/DOCX 解析、正式登录鉴权、语义画像合并、逐步 checkpoint、进程崩溃续跑、token/费用硬限制、强制取消、后台队列、生产部署和负载验证。Langfuse 已有可配置接入，但没有代替用户创建 Cloud 项目或证明真实项目成功接收。

建议后续顺序：先配置模型并做意图与画像质量评测；随后接只读职位搜索和简历文档解析；再建立发送台账、账号串行执行、幂等和超时核对后接真实投递；长期运行需要进一步补鉴权、Worker、配额、持久检查点和运维能力。

## 19. 参考资料

- [ReAct 原始论文](https://arxiv.org/abs/2210.03629)
- [FastAPI 服务运行](https://fastapi.tiangolo.com/deployment/manually/)
- [FastAPI 测试](https://fastapi.tiangolo.com/tutorial/testing/)
- [LangGraph 项目](https://github.com/langchain-ai/langgraph)
- [Langfuse 埋点与父子 observation](https://langfuse.com/docs/observability/sdk/instrumentation)
- [Langfuse Python API](https://python.reference.langfuse.com/langfuse)
- [Langfuse Cloud 地区](https://langfuse.com/security/data-regions)
- [Playwright Python](https://playwright.dev/python/docs/intro)
