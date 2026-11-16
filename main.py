from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from sqlalchemy import text
from database import SessionLocal

# 导入刚才写好的空间服务初始化函数
from services.spatial_service import init_database

# 导入路由模块
from routers.chat_router import router as chat_router
from routers.pois_router import router as pois_router

# 定义应用生命周期钩子
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 服务启动时触发：自动检查并初始化数据库和种子数据（只跑一次！）
    init_database()
    yield
    # 服务关闭时的清理逻辑（暂时留空即可）

# 初始化 FastAPI 实例
app = FastAPI(title="城市空间兴趣点智能检索与 Geo-AI 智能体系统", lifespan=lifespan)

# 挂载静态文件
app.mount("/static", StaticFiles(directory="static"), name="static")

# 注册各个模块的路由
app.include_router(chat_router)
app.include_router(pois_router)