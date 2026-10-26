"""全局配置：全部通过环境变量覆盖，默认值保证零配置可启动。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py：parents[0]=app, [1]=backend, [2]=项目根
BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parent
DATA_DIR = ROOT_DIR / "data"
STORAGE_DIR = DATA_DIR / "storage"
FRONTEND_DIR = ROOT_DIR / "frontend"


class Settings(BaseSettings):
    """应用配置。优先级：环境变量 > .env > 默认值。"""

    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 基础 ----
    app_name: str = "针语 · AI 赋能湘绣文化数字化平台"
    app_version: str = "0.1.0-mvp"
    env: str = "dev"
    host: str = "127.0.0.1"
    port: int = 8130
    log_level: str = "INFO"

    # ---- 安全 ----
    secret_key: str = "zhenyu-dev-secret-change-me"
    access_token_ttl_min: int = 60 * 24 * 7
    password_min_length: int = 8

    # ---- 存储 ----
    db_url: str = f"sqlite:///{DATA_DIR / 'zhenyu.db'}"
    storage_dir: Path = STORAGE_DIR

    # ---- 图像生成 ----
    # mock = 本地程序化占位图（离线可跑）；api = 调用 OpenAI 兼容文生图接口
    image_provider: str = "mock"
    image_api_url: str = ""
    image_api_key: str = ""
    image_api_model: str = "sd-xl"
    image_timeout_sec: int = 60
    # 本地 mock 渲染画布尺寸（正方形）
    image_size: int = 640
    mock_render_strokes: int = 5200
    # 单次生成成本估算（元），用于成本台账与预算熔断
    image_cost_per_call: float = 0.35
    daily_budget_limit: float = 200.0

    # ---- 合规标识 ----
    ai_visible_label: str = "AI 效果示意 · 非实物绣品"
    ai_provider_code: str = "ZHENYU-MVP-001"

    # ---- 检索 ----
    retrieval_top_k: int = 4
    retrieval_min_score: float = 0.15

    # ---- 限流 ----
    rate_limit_generate_per_hour: int = 30

    @property
    def storage_path(self) -> Path:
        p = Path(self.storage_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
