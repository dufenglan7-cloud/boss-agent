# 职位数据源适配层；当前只支持导入数据，BOSS 网页操作仍是待实现接口。
import json
from pathlib import Path
from typing import Protocol
from .models import Job

# 职位来源协议，后续渠道可在不改动 Agent 的情况下替换数据源。
class JobSource(Protocol):
    # 按数据源实现读取职位；当前导入适配器不执行外部搜索。
    def search(self, roles: list[str]) -> list[Job]: ...

# 离线 JSON 数据源，不执行 BOSS 搜索，也不会依据 roles 自动联网发现职位。
class JsonJobSource:
    """Offline/imported job source. Does not scrape BOSS."""
    # 保存调用方提供的数据来源，不在构造时触发检索或投递。
    def __init__(self, path: Path):
        self.path = path

    # 按数据源实现读取职位；当前导入适配器不执行外部搜索。
    def search(self, roles: list[str]) -> list[Job]:
        jobs = [Job.model_validate(item) for item in json.loads(self.path.read_text(encoding="utf-8"))]
        ids = [job.id for job in jobs]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate job IDs in input")
        return jobs

# 真实网页能力的占位类；明确失败可以避免将模拟行为误报为真实投递。
class BossBrowserChannel:
    """Reserved for browser-session inspection and verified BOSS page operations."""
    # 按数据源实现读取职位；当前导入适配器不执行外部搜索。
    def search(self, roles: list[str]) -> list[Job]:
        raise NotImplementedError("BOSS browser integration is not implemented; use imported jobs")

    # 预留真实发送接口；当前明确抛出未实现异常，不伪造成功。
    def send_message(self, job: Job, message: str):
        raise NotImplementedError("Real message sending is not implemented")

    # 预留附件投递接口；当前没有任何附件发送行为。
    def send_resume(self, job: Job, resume_path: Path):
        raise NotImplementedError("Real resume sending is not implemented")

# HTTP 请求中的职位列表适配器，避免为一次请求创建临时文件。
class MemoryJobSource:
    """API job input adapter; no temporary file or browser operation required."""
    # 保存调用方提供的数据来源，不在构造时触发检索或投递。
    def __init__(self, jobs: list[Job]):
        if len({job.id for job in jobs}) != len(jobs):
            raise ValueError("Duplicate job IDs in input")
        self.jobs = list(jobs)

    # 按数据源实现读取职位；当前导入适配器不执行外部搜索。
    def search(self, roles: list[str]) -> list[Job]:
        return list(self.jobs)
