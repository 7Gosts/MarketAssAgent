from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """项目配置"""
    
    # FastAPI
    APP_NAME: str = "MarketReActAgent"
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    
    # 飞书配置
    FEISHU_APP_ID: Optional[str] = None
    FEISHU_APP_SECRET: Optional[str] = None
    
    # 数据库
    DATABASE_URL: str = "sqlite:///./market_agent.db"
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
