# 沟通文案子 Agent：先读取证据，再校验文案引用；这里只生成草稿，不发送消息。
from ...models import Job, Match, Draft
from ...harness import ReActRunner, Tool, EmptyArgs
from .prompts import SYSTEM_PROMPT

# 文案能力独立封装；引用校验只验证原文存在，不证明整段文案语义完全准确。
class DraftingAgent:
    # 注入模型与共享预算。
    def __init__(self, model, context=None):
        self.runner = ReActRunner(model, context)

    # 向模型开放只读证据工具，最终生成草稿而不产生外部写操作。
    def run(self, resume: str, requirements: str, job: Job, match: Match) -> Draft:
        # 模型模式下引用必须是简历中的非空原文；演示草稿明确不代表真实分析。
        def validate(result):
            if result.job_id != job.id:
                raise ValueError("mismatched job_id")
            if self.runner.model is not None and (not result.resume_evidence or any(not s.strip() or s not in resume for s in result.resume_evidence)):
                raise ValueError("invalid resume evidence")
        return self.runner.run("drafting", SYSTEM_PROMPT,
            {"job_id": job.id, "requirements": requirements}, Draft,
            tools=[Tool("read_evidence", "Read resume, job and matching result before drafting", EmptyArgs,
                        lambda _: {"resume": resume, "job": job.model_dump(), "match": match.model_dump()})],
            required=["read_evidence"], demo_result=Draft(job_id=job.id,
                message="【演示草稿】您好，我想进一步了解这个岗位。", resume_evidence=[]).model_dump(), validate=validate)
