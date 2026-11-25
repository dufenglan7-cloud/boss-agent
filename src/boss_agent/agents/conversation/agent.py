# 主 Agent 按当前意图选择工具；子 Agent 的结果需回传模型后才能形成答复。
import json
from pydantic import ConfigDict, Field
from ...harness import ReActRunner, Tool, EmptyArgs
from ...models import Job
from .prompts import SYSTEM_PROMPT
from .schemas import ConversationReply, Preferences

# 模型只能指定已导入职位的标识，不能传入任意网页或服务器文件。
class JobArgs(EmptyArgs):
    job_id: str = Field(min_length=1)

# 自由调度的主 Agent；工具闭包只持有当前运行的资料和缓存。
class ConversationAgent:
    # 绑定本轮运行器和 Agent 注册器；生成计划缓存属于当前调用，不是长期记忆。
    def __init__(self, model, context=None, registry=None):
        self.runner = ReActRunner(model, context)
        self.registry = registry
        self.generated_plan = None

    # 构造受控工具闭包和最终输出校验，由主 Agent 决定调用顺序。
    def run(self, messages: list[dict], resume: str, job_count: int, *, jobs=None,
            preferences=None, action="chat", user_portrait=None) -> ConversationReply:
        jobs = jobs or []
        preferences = preferences or Preferences()
        latest = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        # 子 Agent 同时获得已知条件和本轮消息，以理解最新的明确修改。
        requirements = json.dumps(preferences.model_dump(), ensure_ascii=False) + "\nLatest request: " + latest
        profile_cache, matches = {}, {}
        self.generated_plan = None
        # 按需分析并在本轮缓存，避免多个工具重复读取同一份简历。
        def profile(_):
            if not resume.strip():
                raise ValueError("resume missing")
            if "value" not in profile_cache:
                profile_cache["value"] = self.registry.profile(resume, requirements)
            return profile_cache["value"]
        # 只允许引用当前导入列表中的职位，不接受模型臆造的岗位 ID。
        def job_by_id(job_id):
            job = next((j for j in jobs if j.id == job_id), None)
            if job is None:
                raise ValueError("unknown imported job")
            return job
        # 取得画像后分析指定职位，将结果缓存给后续文案工具使用。
        def matching(args):
            job = job_by_id(args.job_id)
            result = self.registry.match(resume, requirements, profile(None), job)
            matches[job.id] = result
            return result
        # 按需复用匹配结果；被跳过的岗位不生成可发送草稿。
        def drafting(args):
            job = job_by_id(args.job_id)
            match = matches.get(job.id) or matching(args)
            if match.decision == "skip":
                raise ValueError("skipped job")
            return self.registry.draft(resume, requirements, job, match)
        # 将完整计划流程作为可选工具，并缓存成功结果供本轮持久化。
        def plan(_):
            if not resume.strip() or not jobs:
                raise ValueError("resume and imported jobs required")
            from ...workflow import Workflow
            from ...channels import MemoryJobSource
            if self.generated_plan is None:
                self.generated_plan = Workflow(self.registry, MemoryJobSource(jobs)).run(resume, requirements)
            return self.generated_plan
        # 模型只能在这些受控能力中选择，不能通过工具名请求任意系统操作。
        tools = [Tool("analyze_resume", "Delegate to ProfileAgent for evidenced career directions; resume required, jobs not required", EmptyArgs, profile),
                 Tool("read_jobs", "Read imported job descriptions; no web search", EmptyArgs, lambda _: [j.model_dump() for j in jobs]),
                 Tool("match_job", "Delegate one job to MatchingAgent; requires actual resume", JobArgs, matching),
                 Tool("draft_message", "Delegate one matching job to DraftingAgent; does not send", JobArgs, drafting),
                 Tool("build_plan", "Generate a complete local plan only when requested; requires resume and jobs", EmptyArgs, plan)]
        # 校验结果是否有真实工具依据，且偏好和画像引用来自本轮有效来源。
        def validate(result):
            if result.intent == "resume_analysis" and "value" not in profile_cache:
                raise ValueError("resume analysis requires observed profile")
            if result.intent == "matching" and not matches:
                raise ValueError("matching requires observed matching results")
            if result.intent == "planning" and self.generated_plan is None:
                raise ValueError("planning requires successful tool result")
            # 长期记忆必须有当前来源；从简历学习还要求本轮已观察画像子任务结果。
            for update in result.portrait_updates:
                if update.source == "resume" and "value" not in profile_cache:
                    raise ValueError("resume memory requires observed profile")
                source = latest if update.source == "message" else resume
                if result.intent == "question" or not update.evidence.strip() or update.evidence not in source:
                    raise ValueError("ungrounded portrait update")
            merged = preferences.model_dump()
            # 问某岗位不等于想申请该岗位；这里只接受可引用的当前用户原话。
            for update in result.preference_updates:
                if update.evidence not in latest or not update.evidence.strip():
                    raise ValueError("preference evidence must come from current user")
                if update.field != "minimum_monthly_salary" and not isinstance(update.value, list):
                    raise ValueError("list preference required")
                if update.field == "minimum_monthly_salary" and update.value is not None and type(update.value) is not int:
                    raise ValueError("integer salary required")
                merged[update.field] = update.value
            Preferences.model_validate(merged)
        # A transparent scripted driver for offline demo; it is not real intent understanding.
        # 以下分支只是可见标记的演示脚本，不能作为真实意图识别效果。
        wants_plan = action == "plan" or (any(s in latest for s in ("生成计划", "生成求职计划")) and not any(s in latest for s in ("不要", "先别", "不生成")))
        demo_steps = []
        if wants_plan and resume.strip() and jobs:
            demo_steps = [{"action": "tool", "tool": "build_plan", "arguments": {}}]
            demo = ConversationReply(intent="planning", reply="【演示模式】已生成演示求职计划，可在右侧查看。实际投递为 0。")
        elif wants_plan:
            demo = ConversationReply(intent="clarify", reply="【演示模式】生成计划需要简历和至少一个导入职位；你也可以先只聊岗位技能或简历方向。")
        elif any(s in latest for s in ("技能", "什么要求")):
            demo = ConversationReply(reply="【演示模式 · 固定示例】岗位技能可以从编程基础、相关技术栈、项目实践和沟通协作来了解。真实岗位解读需要配置模型；咨询技能不需要先提交简历或职位。")
        elif resume.strip() and any(s in latest for s in ("简历", "适合", "方向")):
            demo_steps = [{"action": "tool", "tool": "analyze_resume", "arguments": {}}]
            demo = ConversationReply(intent="resume_analysis", reply="【演示模式】已运行简历分析子 Agent。示例方向为 Python 开发，不代表你的真实能力判断。你更希望做哪一类工作？", suggested_roles=["Python 开发"])
        else:
            demo = ConversationReply(intent="clarify", reply="【演示模式】可以直接咨询岗位技能，也可以提供简历聊适合的方向。你目前最想了解什么？配置模型后可进行真实的意图判断和多轮交流。")
        return self.runner.run("conversation", SYSTEM_PROMPT,
            {"conversation": messages, "preferences": preferences.model_dump(), "has_resume": bool(resume.strip()),
             "job_count": job_count, "action_hint": action, "user_portrait": user_portrait or {}}, ConversationReply,
            tools=tools, required=["build_plan"] if action == "plan" else [],
            demo_result=demo.model_dump(), demo_steps=demo_steps, validate=validate)
