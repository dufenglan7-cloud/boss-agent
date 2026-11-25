# Agent 间的 JSON 协议；额外字段和动作形状通过 Pydantic 校验。
from typing import Literal
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field, model_validator

# 协议层拒绝多余字段，避免模型输出悄悄改变约定结构。
class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

# 一次 Agent 任务的 JSON 输入；父任务标识用于还原委派关系。
class Task(WireModel):
    task_id: str = Field(default_factory=lambda: str(uuid4()))
    # 顶层任务为空；子任务由运行器记录真实委派关系。
    parent_id: str | None = None
    agent: str
    input: dict

# 一次模型决策只能调用工具或提交结果，不能混用两种动作。
class Decision(WireModel):
    action: Literal["tool", "final"]
    tool: str | None = None
    # 仅对工具动作有意义，具体结构由被选工具的 schema 再次校验。
    arguments: dict = Field(default_factory=dict)
    # 仅对最终动作有意义，仍需通过 Agent 业务 schema。
    result: dict | None = None

    # 验证动作互斥约束；通过 schema 后仍需运行器校验工具权限。
    @model_validator(mode="after")
    def action_shape(self):
        if self.action == "tool" and (not self.tool or self.result is not None):
            raise ValueError("tool action requires tool and arguments only")
        if self.action == "final" and (self.result is None or self.tool is not None or self.arguments):
            raise ValueError("final action requires result only")
        return self

# 完成任务的统一结果信封，业务数据仍使用各 Agent 自己的 schema。
class Result(WireModel):
    task_id: str
    agent: str
    status: Literal["completed", "failed"]
    # 序列化后的业务结果，不包含任意可执行 Python 对象。
    data: dict = Field(default_factory=dict)
    error: str | None = None

# 工具结果或错误观察，必须回到模型上下文后再决定下一步。
class Observation(WireModel):
    tool: str | None = None
    status: Literal["completed", "failed"]
    data: dict | list | str | int | float | bool | None = None
    error: str | None = None
