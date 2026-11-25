# FastAPI 依赖入口；测试可替换工厂而不访问真实模型。
from fastapi import Request
from ..agents import Agents
from ..llm import JsonModel
from ..storage import Store

# 复用应用启动时确定的存储对象，不为每个接口选择不同数据库。
def get_store(request: Request) -> Store:
    return request.app.state.store

# 每次按运行模式创建 Agent 组；依赖注入便于用离线替身测试。
def get_agents_factory():
    return lambda mode: Agents(JsonModel() if mode == "llm" else None)
