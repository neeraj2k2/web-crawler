from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    static_timeout: int = 10
    playwright_timeout: int = 30
    user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/148.0.0.0 Safari/537.36"
    )
    keybert_model: str = "all-MiniLM-L6-v2"
    topics_top_k: int = 10
    min_topic_score: float = 0.4
    js_detection_text_threshold: int = 500
    max_attempts: int = 3
    retry_delay: float = 2.0
    log_level: str = "INFO"
    executor_max_workers: int = 4

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
