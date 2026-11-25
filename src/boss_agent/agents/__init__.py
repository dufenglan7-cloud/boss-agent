# 组合独立 Agent 实例，并为每轮协作创建共享预算和调用追踪上下文。
"""Independent agent packages and a compatibility registry for workflow composition."""
from ..harness import RunContext
from .profile import ProfileAgent
from .matching import MatchingAgent
from .drafting import DraftingAgent
from .conversation import ConversationAgent

# 注册器只组合实例和转发调用，不集中实现各 Agent 的推理逻辑。
class Agents:
    """Own distinct agent instances; this registry contains no agent reasoning logic."""
    # 创建四个独立实例，但让整轮主子协作共用同一预算与事件列表。
    def __init__(self, model, context=None):
        self.context = context if context is not None else RunContext()
        self.profile_agent = ProfileAgent(model, self.context)
        self.matching_agent = MatchingAgent(model, self.context)
        self.drafting_agent = DraftingAgent(model, self.context)
        self.conversation_agent = ConversationAgent(model, self.context, self)

    # 保留预算配置但重建运行 ID、事件和任务栈，隔离不同轮次的追踪。
    def begin_run(self):
        previous = self.context
        self.context = RunContext(max_calls=previous.max_calls, max_steps=previous.max_steps,
                                  max_depth=previous.max_depth, max_seconds=previous.max_seconds)
        for agent in (self.profile_agent, self.matching_agent, self.drafting_agent, self.conversation_agent):
            agent.runner.context = self.context
        return self.context

    # 兼容调用转发到独立简历画像 Agent。
    def profile(self, resume, requirements):
        return self.profile_agent.run(resume, requirements)

    # 兼容调用转发到独立岗位匹配 Agent。
    def match(self, resume, requirements, profile, job):
        return self.matching_agent.run(resume, requirements, profile, job)

    # 兼容调用转发到独立文案 Agent。
    def draft(self, resume, requirements, job, match):
        return self.drafting_agent.run(resume, requirements, job, match)
