from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "sqlite:///./backlot.db"
    kafka_bootstrap_servers: str = "localhost:9092"
    topic_events: str = "return-events"
    topic_dlq: str = "return-events.dlq"
    topic_partitions: int = 3
    log_level: str = "INFO"
    synthetic_seed: int = 42
    risk_model_path: str = "models/risk_model.joblib"


def get_settings() -> Settings:
    return Settings()
