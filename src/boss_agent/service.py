# 显式完整计划的应用服务；与主对话自由调度入口相互独立。
from uuid import uuid4
from .agents import Agents
from .channels import MemoryJobSource
from .models import Job, Plan
from .storage import Store
from .workflow import Workflow

# 处理显式生成完整计划的请求，不负责每轮聊天意图路由。
class PlanningService:
    """Application boundary shared by HTTP clients; never performs external sends."""
    # 注入 Agent 组和存储，业务计算可以脱离 HTTP 层复用。
    def __init__(self, agents: Agents, store: Store):
        self.agents = agents
        self.store = store

    # 先完整计算计划，再生成记录标识并落库，避免保存半成品。
    def create(self, resume: str, requirements: str, jobs: list[Job], mode: str, user_id: str = "local-user") -> tuple[str, Plan]:
        plan = Workflow(self.agents, MemoryJobSource(jobs)).run(resume, requirements)
        run_id = str(uuid4())
        self.store.save(run_id, mode, plan, user_id)
        return run_id, plan
