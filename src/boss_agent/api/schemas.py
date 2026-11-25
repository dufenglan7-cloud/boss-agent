# HTTP 计划请求与响应结构；在业务执行前拒绝空简历和重复职位标识。
from typing import Literal
from pydantic import BaseModel, Field, field_validator, model_validator
from ..models import Job, Plan

# 完整计划接口的输入，区别于无需简历也能开始的普通聊天。
class PlanRequest(BaseModel):
    resume_text: str = Field(min_length=1, max_length=100000)
    requirements: str = Field(default="", max_length=20000)
    jobs: list[Job] = Field(max_length=100)
    mode: Literal["demo", "llm"] = "demo"

    # 排除只有空白字符的简历，避免进入模型流程后才发现没有事实来源。
    @field_validator("resume_text")
    @classmethod
    def require_resume(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Resume must not be empty")
        return value

    # 同一次请求内职位标识必须唯一，避免匹配结果与文案关联错位。
    @model_validator(mode="after")
    def unique_jobs(self):
        if len({job.id for job in self.jobs}) != len(self.jobs):
            raise ValueError("Duplicate job IDs in input")
        return self

# 创建或查询计划的响应，不将已生成计划等同于已投递。
class PlanResponse(BaseModel):
    run_id: str
    mode: Literal["demo", "llm"]
    status: Literal["planned"] = "planned"
    # 计划准备不产生真实投递，响应契约禁止返回非零发送数量。
    applications_sent: Literal[0] = 0
    plan: Plan
