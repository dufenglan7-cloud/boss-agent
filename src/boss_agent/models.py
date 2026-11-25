# 跨 Agent 传递的职业资料、岗位、匹配、草稿与计划业务结构。
from typing import Literal
from pydantic import BaseModel, Field

# 简历分析输出；与按用户持久化的 UserPortrait 是不同层次的画像。
class Profile(BaseModel):
    skills: list[str] = Field(default_factory=list)
    experience_summary: str
    # 从简历推测的方向，尚不能直接成为明确求职意向。
    suggested_roles: list[str] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
    # 统一按人民币月薪解释；来源单位转换不由此模型自动完成。
    minimum_monthly_salary: int | None = Field(default=None, ge=0)
    excluded_companies: list[str] = Field(default_factory=list)
    missing_preferences: list[str] = Field(default_factory=list)

# 导入职位的事实描述；薪资以人民币月薪整数表达，来源需自行统一单位。
class Job(BaseModel):
    id: str
    title: str
    company: str
    city: str
    # 薪资可未知，不能据此默认认定符合用户要求。
    salary_min: int | None = None
    salary_max: int | None = None
    description: str
    url: str = ""

# 单个岗位的判断及依据；job_id 必须与原任务对应。
class Match(BaseModel):
    job_id: str
    # recommend 推荐、consider 待确认、skip 跳过；后续流程据此决定是否生成草稿。
    decision: Literal["recommend", "consider", "skip"]
    evidence: list[str]
    gaps: list[str] = Field(default_factory=list)

# 一份待审阅沟通草稿及其简历引用，不具有外部发送副作用。
class Draft(BaseModel):
    job_id: str
    message: str
    # 模型模式下必须为简历原文子串，避免没有来源的经历声明。
    resume_evidence: list[str]

# 画像、岗位判断和文案的组合结果，表示本地准备完成。
class Plan(BaseModel):
    profile: Profile
    matches: list[Match]
    drafts: list[Draft]
