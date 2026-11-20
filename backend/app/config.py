"""Application settings, loaded from environment variables or a `.env` file."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- LLM (any OpenAI-compatible server: vLLM, Ollama, llama.cpp) ---
    llm_enabled: bool = True
    llm_base_url: str = "http://localhost:8001/v1"
    llm_model: str = "flowlens-llm"
    llm_api_key: str = "not-needed"
    llm_timeout_s: float = 180.0
    llm_temperature: float = 0.3
    llm_max_tokens: int = 4096
    llm_fix_retries: int = 2
    max_file_summaries: int = 40  # files summarized up front (the most-used ones first)
    llm_concurrency: int = 2  # parallel model requests; match the model server's LLM_PARALLEL
    summary_batch_size: int = 10
    # Save every model request + answer to DATA_DIR/training/*.jsonl (training data).
    # Off by default: turn on only for code you are allowed to keep.
    training_log: bool = False

    # --- Upload limits (protect the server) ---
    max_upload_mb: int = 50
    max_files: int = 5000
    max_uncompressed_mb: int = 300
    max_file_kb: int = 512
    single_file_max_kb: int = 300     # single-file mode upload limit
    file_max_chars: int = 24000       # code sent to the model in single-file mode (~7k tokens)

    # --- Jobs & hosting ---
    max_concurrent_jobs: int = 2
    job_ttl_minutes: int = 60
    rate_limit_per_hour: int = 20
    trust_proxy_headers: bool = False
    cors_origins: list[str] = ["http://localhost:5173"]
    data_dir: Path = Path("./data")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings
