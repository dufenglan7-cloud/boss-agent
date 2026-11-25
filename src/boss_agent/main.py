# FastAPI 应用工厂；启动时初始化存储，关闭时刷新监测，前端与接口同源提供。
import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from .api.portrait_routes import router as portrait_router
from .api.chat_routes import router as chat_router
from .api.routes import router
from .storage import Store
from .observability import shutdown

# 通过工厂注入数据库路径，测试可使用临时库而不触碰真实资料。
def create_app(db_path: Path | None = None) -> FastAPI:
    # 把存储初始化与监测关闭绑定到服务生命周期，导入模块时不创建数据库。
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        path = db_path if db_path is not None else Path(os.environ.get("BOSS_AGENT_DB_PATH", "data/boss-agent.sqlite3"))
        app.state.store = Store(path)
        try:
            yield
        finally:
            shutdown()

    app = FastAPI(title="boss-agent", version="0.1.0", lifespan=lifespan,
                  description="Resume-aware application planning. Real BOSS submissions are not implemented.")
    app.include_router(router)
    app.include_router(chat_router)
    app.include_router(portrait_router)
    web = Path(__file__).parent / "web"
    app.mount("/static", StaticFiles(directory=web), name="static")

    # 提供同源静态前端，浏览器无需另启动前端开发服务器。
    @app.get("/", include_in_schema=False)
    def home():
        return FileResponse(web / "index.html")

    return app

app = create_app()
