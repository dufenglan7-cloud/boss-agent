# 岗位匹配子 Agent：将确定性硬条件结果交给模型观察，并校验最终判断。
from ...models import Profile, Job, Match
from ...harness import ReActRunner, Tool, EmptyArgs
from .prompts import SYSTEM_PROMPT

# 先以代码计算城市、公司和薪资约束，模型只能遵守该结果。
def hard_skip(profile: Profile, job: Job) -> str | None:
    if job.company in profile.excluded_companies:
        return "公司在排除列表中"
    if profile.cities and job.city not in profile.cities:
        return "城市不符合明确要求"
    if profile.minimum_monthly_salary is not None and job.salary_max is not None and job.salary_max < profile.minimum_monthly_salary:
        return "薪资上限低于最低要求"
    return None

# 匹配能力独立封装；最终结果必须遵守代码计算的硬约束。
class MatchingAgent:
    # 注入模型与共享预算。
    def __init__(self, model, context=None):
        self.runner = ReActRunner(model, context)

    # 把候选资料和硬约束作为工具观察提供给模型，再校验最终判断。
    def run(self, resume: str, requirements: str, profile: Profile, job: Job) -> Match:
        reason = hard_skip(profile, job)
        # 防止岗位标识串位，以及模型用推荐结论覆盖硬性跳过条件。
        def validate(result):
            if result.job_id != job.id:
                raise ValueError("mismatched job_id")
            if reason and result.decision != "skip":
                raise ValueError("hard constraints must be respected")
        demo = Match(job_id=job.id, decision="skip" if reason else "consider",
                     evidence=[reason or "【演示结果】尚未进行真实模型匹配"])
        return self.runner.run("matching", SYSTEM_PROMPT + " Always respect the constraint check observation.",
            {"job_id": job.id, "requirements": requirements}, Match,
            tools=[Tool("read_candidate", "Read resume and profile", EmptyArgs,
                        lambda _: {"resume": resume, "profile": profile.model_dump()}),
                   Tool("read_job", "Read job and deterministic constraint check", EmptyArgs,
                        lambda _: {"job": job.model_dump(), "hard_skip_reason": reason})],
            required=["read_candidate", "read_job"], demo_result=demo.model_dump(), validate=validate)
