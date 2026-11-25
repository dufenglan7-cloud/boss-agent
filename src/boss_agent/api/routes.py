# 健康检查与显式完整计划接口；计划生成成功不代表实际投递成功。
import httpx
from fastapi import APIRouter, Depends, HTTPException
from ..service import PlanningService
from ..storage import Store
from .dependencies import get_store, get_agents_factory
from .identity import current_user
from .schemas import PlanRequest, PlanResponse

router = APIRouter()

# 检查本服务是否可响应，不请求模型或监测服务。
@router.get("/health", tags=["health"])
def health():
    return {"status": "ok", "service": "boss-agent", "real_submission_enabled": False}

# 调用完整计划服务并返回 201；不会执行真实投递。
@router.post("/api/v1/plans", response_model=PlanResponse, status_code=201, tags=["plans"])
def create_plan(payload: PlanRequest, store: Store = Depends(get_store),
                factory=Depends(get_agents_factory), user_id: str = Depends(current_user)):
    try:
        agents = factory(payload.mode)
    except ValueError:
        raise HTTPException(status_code=503, detail="Model configuration is missing or invalid") from None
    try:
        run_id, plan = PlanningService(agents, store).create(
            payload.resume_text, payload.requirements, payload.jobs, payload.mode, user_id)
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        # Do not echo upstream bodies, API credentials, resume text or provider errors.
        raise HTTPException(status_code=502, detail="Planning failed: model output or upstream request invalid") from None
    return PlanResponse(run_id=run_id, mode=payload.mode, plan=plan)

# 查询计划并检查用户归属，缺失或其他用户的计划均返回 404。
@router.get("/api/v1/plans/{run_id}", response_model=PlanResponse, tags=["plans"])
def get_plan(run_id: str, store: Store = Depends(get_store), user_id: str = Depends(current_user)):
    record = store.get(run_id)
    if record is None or record["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="Plan not found")
    return PlanResponse(run_id=record["run_id"], mode=record["mode"], plan=record["plan"])
