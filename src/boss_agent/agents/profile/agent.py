# 简历画像子 Agent：先读取真实简历，再输出职业能力与方向建议。
from ...models import Profile
from ...harness import ReActRunner, Tool, EmptyArgs
from .prompts import SYSTEM_PROMPT

# 简历画像能力独立封装，通过统一 Harness 完成读取与输出循环。
class ProfileAgent:
    # 注入模型与共享运行上下文。
    def __init__(self, model, context=None):
        self.runner = ReActRunner(model, context)

    # 注册只读简历工具，并要求模型观察简历后再提交画像结果。
    def run(self, resume: str, requirements: str) -> Profile:
        return self.runner.run("profile", SYSTEM_PROMPT,
            {"requirements": requirements}, Profile,
            tools=[Tool("read_resume", "Read the actual supplied resume", EmptyArgs, lambda _: {"resume": resume})],
            required=["read_resume"], demo_result=Profile(skills=["Python"],
                experience_summary="【演示数据】不代表真实简历解析", suggested_roles=["Python 开发"],
                missing_preferences=["演示模式不推断真实偏好"]).model_dump())
