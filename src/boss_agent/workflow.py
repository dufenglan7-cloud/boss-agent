# 按需调用的完整计划组合工具；保留 LangGraph 编排以兼容 API 和 CLI。
from typing import TypedDict
from .observability import run_scope
from .harness import RunContext
from langgraph.graph import StateGraph, START, END
from .agents import Agents
from .channels import JobSource
from .models import Profile, Job, Match, Draft, Plan

# 完整计划组合工具的节点状态，不是所有对话都要经过的固定状态机。
class State(TypedDict, total=False):
    resume: str
    requirements: str
    profile: Profile
    jobs: list[Job]
    matches: list[Match]
    drafts: list[Draft]

# 显式完整计划工具；主 Agent 可选择调用，也可以只使用部分子能力。
class Workflow:
    # 组装完整计划的四个节点，这条路线仅在显式计划任务中运行。
    def __init__(self, agents: Agents, source: JobSource):
        self.context = getattr(agents, "context", None) or RunContext()
        # 从当前简历与要求构建职业画像。
        def profile(s: State):
            return {"profile": agents.profile(s["resume"], s["requirements"])}
        # 通过来源协议读取导入职位；并没有联网扩展搜索。
        def discover(s: State):
            return {"jobs": source.search(s["profile"].suggested_roles)}
        # 逐职位进行判断，不在此节点生成文案。
        def match(s: State):
            return {"matches": [agents.match(s["resume"], s["requirements"], s["profile"], j) for j in s["jobs"]]}
        # 只为非 skip 的岗位准备草稿，使用同一 job_id 关联原职位。
        def compose(s: State):
            jobs = {j.id: j for j in s["jobs"]}
            return {"drafts": [agents.draft(s["resume"], s["requirements"], jobs[m.job_id], m)
                               for m in s["matches"] if m.decision != "skip"]}
        # 这是按需完整计划工具的固定组合，并非主 Agent 每轮对话的强制路线。
        graph = StateGraph(State)
        for name, node in [("profile_agent", profile), ("discover", discover), ("match_agent", match), ("draft_agent", compose)]:
            graph.add_node(name, node)
        graph.add_edge(START, "profile_agent")
        graph.add_edge("profile_agent", "discover")
        graph.add_edge("discover", "match_agent")
        graph.add_edge("match_agent", "draft_agent")
        graph.add_edge("draft_agent", END)
        self.graph = graph.compile()

    # 复用协作追踪作用域，完成所有节点后才返回完整 Plan。
    def run(self, resume: str, requirements: str) -> Plan:
        with run_scope(self.context):
            if not resume.strip():
                raise ValueError("Resume must not be empty")
            state = self.graph.invoke({"resume": resume, "requirements": requirements})
            return Plan(profile=state["profile"], matches=state["matches"], drafts=state["drafts"])
