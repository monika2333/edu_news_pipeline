from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping, Optional, Set

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ENV_LOADED = False
# 本进程从 env 文件读入的变量名；显式导出的系统变量不在此列。
# 重读 env 文件时据此区分「文件来源」与「进程原本的环境」：
# 文件值可以覆盖文件来源的旧值，但绝不覆盖显式导出的变量。
_ENV_FILE_KEYS: Set[str] = set()
_ENV_RELOAD_LOCK = threading.Lock()
_ENV_FILES = (
    _REPO_ROOT / ".env.local",
    _REPO_ROOT / ".env",
    _REPO_ROOT / "config" / "abstract.env",
)
# env 文件上次读取时的状态快照（存在性、mtime、大小），控制台进程在处理
# 请求前对照它检测文件是否被改过：改了才重读，没变不碰文件。
_ENV_FILES_SIGNATURE: Optional[tuple] = None
BGE_EMBEDDING_MODEL = "BAAI/bge-large-zh"
# 接入点主机白名单的内置默认列表：LLM_ALLOWED_HOSTS 只能**追加**域名，
# 不能替换或删掉这里的条目。覆盖 OpenRouter、DeepSeek、智谱 GLM、Kimi
# （国内/国际账号）、阿里云百炼（通义）、火山方舟（豆包，华北2北京地域）、
# 硅基流动；域名以各家官方文档为准（2026-11-07 核对）。百炼按业务空间分配
# 的专属域名（<WorkspaceId>.cn-beijing.maas.aliyuncs.com）每个账号不同，
# 不进默认列表，需要时追加到 LLM_ALLOWED_HOSTS。
DEFAULT_LLM_ALLOWED_HOSTS = (
    "openrouter.ai",
    "api.deepseek.com",
    "open.bigmodel.cn",
    "api.moonshot.cn",
    "api.moonshot.ai",
    "dashscope.aliyuncs.com",
    "ark.cn-beijing.volces.com",
    "api.siliconflow.cn",
)


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse one env file into key→value pairs; missing or malformed → {}."""
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                value = value[1:-1]
            if key:
                values[key] = value
    except Exception:
        # Gracefully ignore malformed env files; explicit env vars win anyway.
        pass
    return values


def _parse_env_files() -> dict[str, str]:
    """所有 env 文件合并出的「文件想要的值」；同一变量先读到的文件优先。"""

    values: dict[str, str] = {}
    for candidate in _ENV_FILES:
        for key, value in _parse_env_file(candidate).items():
            values.setdefault(key, value)
    return values


def _apply_env_values(values: Mapping[str, str]) -> None:
    """把文件值应用到 os.environ，全程不产生「变量短暂消失」的空档。

    与文件重读的两条既有语义一致：进程启动前显式导出的变量优先（文件值
    不覆盖它们）；文件里删掉的变量随之消失。先整体覆盖文件来源的变量，
    再只删除文件里已不存在的变量——前后都在文件里的变量从未被删除。"""

    exported = set(os.environ) - _ENV_FILE_KEYS
    for key, value in values.items():
        if key in exported:
            continue
        os.environ[key] = value
        _ENV_FILE_KEYS.add(key)
    for key in sorted(_ENV_FILE_KEYS - set(values)):
        os.environ.pop(key, None)
        _ENV_FILE_KEYS.discard(key)


def _env_files_signature() -> tuple:
    """env 文件的轻量指纹：存在性 + mtime + 大小（新建与删除也体现为变化）。"""

    signature: list[tuple[Path, Optional[tuple[int, int]]]] = []
    for candidate in _ENV_FILES:
        try:
            stat = candidate.stat()
            signature.append((candidate, (stat.st_mtime_ns, stat.st_size)))
        except OSError:
            signature.append((candidate, None))
    return tuple(signature)


def load_environment() -> None:
    """Load environment files once, preferring explicitly exported values."""
    global _ENV_LOADED, _ENV_FILES_SIGNATURE
    if _ENV_LOADED:
        return
    with _ENV_RELOAD_LOCK:
        if _ENV_LOADED:
            return
        _apply_env_values(_parse_env_files())
        _ENV_FILES_SIGNATURE = _env_files_signature()
        _ENV_LOADED = True


def reload_environment() -> int:
    """Re-read the env files and invalidate the cached Settings.

    Mirrors a restart's env semantics without one, without a gap where
    file-sourced variables are missing: new file values are parsed first,
    then applied over the old ones in place, and only variables removed
    from the files get dropped. Variables exported in the shell before the
    process started keep winning over file values, as on initial load.
    Returns the number of env vars now sourced from the files.
    """
    global _ENV_LOADED, _ENV_FILES_SIGNATURE
    with _ENV_RELOAD_LOCK:
        _apply_env_values(_parse_env_files())
        _ENV_FILES_SIGNATURE = _env_files_signature()
        _ENV_LOADED = True
    get_settings.cache_clear()
    return len(_ENV_FILE_KEYS)


def refresh_environment_if_changed() -> bool:
    """env 文件被改过时重读并失效设置缓存；没改就只做一次文件信息检查。

    控制台进程在处理请求前调用（见 app.py 的中间件）：用户在服务器上改完
    `.env.local`（比如补了接入点的 Key）后无需重启、也不用点任何按钮，
    下一个请求就能读到新值。流水线各步骤是计划任务新起的进程，启动时本来
    就读最新文件，不经过这里。返回是否发生了重读。"""
    global _ENV_FILES_SIGNATURE
    current = _env_files_signature()
    if current == _ENV_FILES_SIGNATURE:
        return False
    reload_environment()
    return True



def _get_env(*keys: str) -> Optional[str]:
    for key in keys:
        value = os.getenv(key)
        if value:
            return value
    return None


def _optional_int(value: Optional[str]) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _bool_from_env(value: Optional[str], *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Settings:
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: Optional[str]
    db_schema: str
    llm_api_key: Optional[str]
    llm_allowed_hosts: tuple[str, ...]
    llm_api_http_referer: Optional[str]
    llm_api_title: Optional[str]
    llm_reasoning_effort: Optional[str]
    llm_reasoning_max_tokens: Optional[int]
    llm_reasoning_exclude: bool
    llm_scoring_timeout: int
    llm_summary_timeout: int
    llm_external_filter_timeout: int
    llm_beijing_gate_timeout: int
    llm_global_budget: int
    llm_beijing_gate_budget: int
    llm_scoring_budget: int
    llm_summary_budget: int
    llm_external_filter_budget: int
    llm_sentiment_budget: int
    llm_source_budget: int
    llm_duplicate_review_budget: int
    llm_prompt_optimizer_timeout: int
    llm_prompt_optimizer_budget: int
    llm_quota_alert_enabled: bool
    llm_quota_alert_cooldown_seconds: int
    llm_quota_alert_state_path: Path
    llm_request_alert_enabled: bool
    llm_request_alert_cooldown_seconds: int
    llm_request_alert_state_path: Path
    score_promotion_threshold: int
    process_limit: Optional[int]
    default_concurrency: int
    summary_concurrency: int
    console_basic_username: Optional[str]
    console_basic_password: Optional[str]
    console_api_token: Optional[str]
    console_cookie_secure: bool
    console_session_days: int
    duty_shift_boundary_hour: int
    feishu_app_id: Optional[str]
    feishu_app_secret: Optional[str]
    feishu_receive_id: Optional[str]
    feishu_receive_id_type: str
    feishu_archive_allowed_open_ids: tuple[str, ...]
    external_filter_threshold: int
    external_filter_negative_threshold: int
    internal_filter_threshold: int
    internal_filter_negative_threshold: int
    external_filter_prompt_path: Path
    external_negative_filter_prompt_path: Path
    internal_filter_prompt_path: Path
    internal_negative_filter_prompt_path: Path
    beijing_gate_prompt_path: Path
    external_filter_batch_size: int
    external_filter_max_retries: int
    beijing_gate_max_retries: int


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached project settings sourced from env variables."""
    load_environment()

    db_host = _get_env("DB_HOST", "POSTGRES_HOST") or "localhost"
    db_port = _optional_int(_get_env("DB_PORT", "POSTGRES_PORT")) or 5432
    db_name = _get_env("DB_NAME", "POSTGRES_DB", "POSTGRES_DB_NAME") or "postgres"
    db_user = _get_env("DB_USER", "POSTGRES_USER") or "postgres"
    db_password = _get_env("DB_PASSWORD", "POSTGRES_PASSWORD")
    db_schema = _get_env("DB_SCHEMA", "POSTGRES_SCHEMA") or "public"

    llm_api_key = os.getenv("LLM_API_KEY")
    # 白名单 = 内置默认列表 + LLM_ALLOWED_HOSTS 追加的域名（去重、小写化、
    # 默认在前）。环境变量不能替换默认列表：只写新域名时，已有服务商的
    # 接入点必须仍然在白名单里，否则整条流水线的配置加载会失败。
    raw_allowed_hosts = os.getenv("LLM_ALLOWED_HOSTS") or ""
    extra_allowed_hosts = tuple(
        dict.fromkeys(
            host.strip().lower()
            for host in raw_allowed_hosts.split(",")
            if host.strip()
        )
    )
    llm_allowed_hosts = tuple(
        dict.fromkeys((*DEFAULT_LLM_ALLOWED_HOSTS, *extra_allowed_hosts))
    )
    llm_api_http_referer = os.getenv("LLM_API_HTTP_REFERER")
    llm_api_title = os.getenv("LLM_API_TITLE")
    raw_reasoning_effort = (os.getenv("LLM_REASONING_EFFORT") or "").strip().lower()
    llm_reasoning_effort = raw_reasoning_effort if raw_reasoning_effort in {"low", "medium", "high"} else None
    llm_reasoning_max_tokens = _optional_int(os.getenv("LLM_REASONING_MAX_TOKENS"))
    llm_reasoning_exclude = _bool_from_env(
        os.getenv("LLM_REASONING_EXCLUDE"),
        default=True,
    )
    # LLM timeout configuration (in seconds)
    llm_global_timeout = _optional_int(os.getenv("LLM_TIMEOUT")) or 90
    llm_scoring_timeout = _optional_int(os.getenv("LLM_SCORING_TIMEOUT")) or llm_global_timeout or 30
    llm_summary_timeout = (
        _optional_int(os.getenv("LLM_SUMMARY_TIMEOUT")) or llm_global_timeout or 60
    )
    llm_external_filter_timeout = (
        _optional_int(os.getenv("LLM_EXTERNAL_FILTER_TIMEOUT")) or llm_global_timeout or 30
    )
    llm_beijing_gate_timeout = (
        _optional_int(os.getenv("LLM_BEIJING_GATE_TIMEOUT")) or llm_global_timeout or 60
    )
    llm_prompt_optimizer_timeout = (
        _optional_int(os.getenv("LLM_PROMPT_OPTIMIZER_TIMEOUT"))
        or max(llm_global_timeout, 120)
    )
    llm_global_budget = _optional_int(os.getenv("LLM_BUDGET")) or 180
    llm_beijing_gate_budget = (
        _optional_int(os.getenv("LLM_BEIJING_GATE_BUDGET")) or llm_global_budget
    )
    llm_scoring_budget = (
        _optional_int(os.getenv("LLM_SCORING_BUDGET")) or llm_global_budget
    )
    llm_summary_budget = (
        _optional_int(os.getenv("LLM_SUMMARY_BUDGET")) or llm_global_budget
    )
    llm_external_filter_budget = (
        _optional_int(os.getenv("LLM_EXTERNAL_FILTER_BUDGET")) or llm_global_budget
    )
    llm_sentiment_budget = (
        _optional_int(os.getenv("LLM_SENTIMENT_BUDGET")) or llm_global_budget
    )
    llm_source_budget = (
        _optional_int(os.getenv("LLM_SOURCE_BUDGET")) or llm_global_budget
    )
    llm_duplicate_review_budget = (
        _optional_int(os.getenv("LLM_DUPLICATE_REVIEW_BUDGET")) or llm_global_budget
    )
    llm_prompt_optimizer_budget = (
        _optional_int(os.getenv("LLM_PROMPT_OPTIMIZER_BUDGET"))
        or max(llm_global_budget, 300)
    )
    llm_quota_alert_enabled = _bool_from_env(
        os.getenv("LLM_QUOTA_ALERT_ENABLED"),
        default=True,
    )
    llm_quota_alert_cooldown_seconds = (
        _optional_int(os.getenv("LLM_QUOTA_ALERT_COOLDOWN_SECONDS")) or 21600
    )
    llm_request_alert_enabled = _bool_from_env(
        os.getenv("LLM_REQUEST_ALERT_ENABLED"),
        default=True,
    )
    raw_request_alert_cooldown = _optional_int(
        os.getenv("LLM_REQUEST_ALERT_COOLDOWN_SECONDS")
    )
    llm_request_alert_cooldown_seconds = (
        raw_request_alert_cooldown if raw_request_alert_cooldown is not None else 1800
    )
    raw_score_threshold = _optional_int(
        _get_env("SCORE_PROMOTION_THRESHOLD", "SCORE_THRESHOLD")
    )
    score_promotion_threshold = raw_score_threshold if raw_score_threshold is not None else 60
    raw_external_threshold = _optional_int(
        os.getenv("EXTERNAL_FILTER_POSITIVE_THRESHOLD")
    )
    external_filter_threshold = raw_external_threshold if raw_external_threshold is not None else 20
    raw_external_negative_threshold = _optional_int(os.getenv("EXTERNAL_FILTER_NEGATIVE_THRESHOLD"))
    external_filter_negative_threshold = (
        raw_external_negative_threshold if raw_external_negative_threshold is not None else external_filter_threshold
    )
    raw_internal_threshold = _optional_int(
        os.getenv("INTERNAL_FILTER_POSITIVE_THRESHOLD")
    )
    internal_filter_threshold = (
        raw_internal_threshold if raw_internal_threshold is not None else external_filter_threshold
    )
    raw_internal_negative_threshold = _optional_int(os.getenv("INTERNAL_FILTER_NEGATIVE_THRESHOLD"))
    internal_filter_negative_threshold = (
        raw_internal_negative_threshold
        if raw_internal_negative_threshold is not None
        else internal_filter_threshold
    )
    external_filter_batch_size = _optional_int(os.getenv("EXTERNAL_FILTER_BATCH_SIZE")) or 50
    external_filter_max_retries = _optional_int(os.getenv("EXTERNAL_FILTER_MAX_RETRIES")) or 3
    beijing_gate_max_retries = _optional_int(os.getenv("BEIJING_GATE_MAX_RETRIES")) or 3

    process_limit = _optional_int(os.getenv("PROCESS_LIMIT"))
    default_concurrency = _optional_int(os.getenv("CONCURRENCY")) or 50
    summary_concurrency = _optional_int(os.getenv("SUMMARY_CONCURRENCY")) or default_concurrency

    def _resolve_path(env_value: Optional[str], *, default: Path) -> Path:
        if env_value:
            raw_path = Path(env_value).expanduser()
            return raw_path if raw_path.is_absolute() else (_REPO_ROOT / raw_path)
        return default

    config_dir = _REPO_ROOT / "config"

    prompts_dir = config_dir / "prompts"

    raw_external_prompt = os.getenv("EXTERNAL_FILTER_PROMPT_PATH")
    external_filter_prompt_path = _resolve_path(
        raw_external_prompt,
        default=prompts_dir / "external_positive_importance_prompt.md",
    )

    raw_external_negative_prompt = os.getenv("EXTERNAL_NEGATIVE_FILTER_PROMPT_PATH")
    external_negative_filter_prompt_path = _resolve_path(
        raw_external_negative_prompt,
        default=prompts_dir / "external_negative_importance_prompt.md",
    )

    raw_internal_prompt = os.getenv("INTERNAL_FILTER_PROMPT_PATH")
    internal_filter_prompt_path = _resolve_path(
        raw_internal_prompt,
        default=prompts_dir / "internal_positive_importance_prompt.md",
    )

    raw_internal_negative_prompt = os.getenv("INTERNAL_NEGATIVE_FILTER_PROMPT_PATH")
    internal_negative_filter_prompt_path = _resolve_path(
        raw_internal_negative_prompt,
        default=prompts_dir / "internal_negative_importance_prompt.md",
    )

    raw_beijing_gate_prompt = os.getenv("BEIJING_GATE_PROMPT_PATH")
    beijing_gate_prompt_path = _resolve_path(
        raw_beijing_gate_prompt,
        default=prompts_dir / "beijing_gate_prompt.md",
    )

    raw_quota_alert_state_path = os.getenv("LLM_QUOTA_ALERT_STATE_PATH")
    llm_quota_alert_state_path = _resolve_path(
        raw_quota_alert_state_path,
        default=_REPO_ROOT / "logs" / "llm_quota_alert_state.json",
    )

    raw_request_alert_state_path = os.getenv("LLM_REQUEST_ALERT_STATE_PATH")
    llm_request_alert_state_path = _resolve_path(
        raw_request_alert_state_path,
        default=_REPO_ROOT / "logs" / "llm_request_alert_state.json",
    )

    console_basic_username = os.getenv("CONSOLE_BASIC_USERNAME")
    console_basic_password = os.getenv("CONSOLE_BASIC_PASSWORD")
    console_api_token = os.getenv("CONSOLE_API_TOKEN")
    console_cookie_secure = _bool_from_env(
        os.getenv("CONSOLE_COOKIE_SECURE"),
        default=False,
    )
    console_session_days = _optional_int(os.getenv("CONSOLE_SESSION_DAYS")) or 14
    raw_shift_boundary_hour = _optional_int(
        os.getenv("DUTY_SHIFT_BOUNDARY_HOUR")
    )
    duty_shift_boundary_hour = (
        raw_shift_boundary_hour
        if raw_shift_boundary_hour is not None
        and 0 <= raw_shift_boundary_hour <= 23
        else 22
    )

    feishu_app_id = _get_env("FEISHU_APP_ID", "feishu_APP_ID")
    feishu_app_secret = _get_env("FEISHU_APP_SECRET", "feishu_APP_Secret")
    feishu_receive_id = _get_env("FEISHU_RECEIVE_ID", "FEISHU_OPEN_ID", "my_open_id")
    feishu_receive_id_type = os.getenv("FEISHU_RECEIVE_ID_TYPE", "open_id")
    if feishu_receive_id_type not in {"open_id", "user_id", "union_id"}:
        feishu_receive_id_type = "open_id"
    raw_archive_allowed_ids = os.getenv("FEISHU_ARCHIVE_ALLOWED_OPEN_IDS", "")
    feishu_archive_allowed_open_ids = tuple(
        dict.fromkeys(
            value.strip()
            for value in raw_archive_allowed_ids.split(",")
            if value.strip()
        )
    )
    if (
        not feishu_archive_allowed_open_ids
        and feishu_receive_id
        and feishu_receive_id_type == "open_id"
    ):
        feishu_archive_allowed_open_ids = (feishu_receive_id,)

    external_filter_prompt_path = external_filter_prompt_path.resolve()
    external_negative_filter_prompt_path = external_negative_filter_prompt_path.resolve()
    internal_filter_prompt_path = internal_filter_prompt_path.resolve()
    internal_negative_filter_prompt_path = internal_negative_filter_prompt_path.resolve()
    beijing_gate_prompt_path = beijing_gate_prompt_path.resolve()
    llm_quota_alert_state_path = llm_quota_alert_state_path.resolve()
    llm_request_alert_state_path = llm_request_alert_state_path.resolve()

    return Settings(
        db_host=db_host,
        db_port=db_port,
        db_name=db_name,
        db_user=db_user,
        db_password=db_password,
        db_schema=db_schema,
        llm_api_key=llm_api_key,
        llm_allowed_hosts=llm_allowed_hosts,
        llm_api_http_referer=llm_api_http_referer,
        llm_api_title=llm_api_title,
        llm_reasoning_effort=llm_reasoning_effort,
        llm_reasoning_max_tokens=llm_reasoning_max_tokens,
        llm_reasoning_exclude=llm_reasoning_exclude,
        llm_scoring_timeout=llm_scoring_timeout,
        llm_summary_timeout=llm_summary_timeout,
        llm_external_filter_timeout=llm_external_filter_timeout,
        llm_beijing_gate_timeout=llm_beijing_gate_timeout,
        llm_global_budget=llm_global_budget,
        llm_beijing_gate_budget=llm_beijing_gate_budget,
        llm_scoring_budget=llm_scoring_budget,
        llm_summary_budget=llm_summary_budget,
        llm_external_filter_budget=llm_external_filter_budget,
        llm_sentiment_budget=llm_sentiment_budget,
        llm_source_budget=llm_source_budget,
        llm_duplicate_review_budget=llm_duplicate_review_budget,
        llm_prompt_optimizer_timeout=llm_prompt_optimizer_timeout,
        llm_prompt_optimizer_budget=llm_prompt_optimizer_budget,
        llm_quota_alert_enabled=llm_quota_alert_enabled,
        llm_quota_alert_cooldown_seconds=llm_quota_alert_cooldown_seconds,
        llm_quota_alert_state_path=llm_quota_alert_state_path,
        llm_request_alert_enabled=llm_request_alert_enabled,
        llm_request_alert_cooldown_seconds=llm_request_alert_cooldown_seconds,
        llm_request_alert_state_path=llm_request_alert_state_path,
        score_promotion_threshold=score_promotion_threshold,
        process_limit=process_limit,
        default_concurrency=default_concurrency,
        summary_concurrency=summary_concurrency,
        console_basic_username=console_basic_username,
        console_basic_password=console_basic_password,
        console_api_token=console_api_token,
        console_cookie_secure=console_cookie_secure,
        console_session_days=console_session_days,
        duty_shift_boundary_hour=duty_shift_boundary_hour,
        feishu_app_id=feishu_app_id,
        feishu_app_secret=feishu_app_secret,
        feishu_receive_id=feishu_receive_id,
        feishu_receive_id_type=feishu_receive_id_type,
        feishu_archive_allowed_open_ids=feishu_archive_allowed_open_ids,
        external_filter_threshold=external_filter_threshold,
        external_filter_negative_threshold=external_filter_negative_threshold,
        internal_filter_threshold=internal_filter_threshold,
        internal_filter_negative_threshold=internal_filter_negative_threshold,
        external_filter_prompt_path=external_filter_prompt_path,
        external_negative_filter_prompt_path=external_negative_filter_prompt_path,
        internal_filter_prompt_path=internal_filter_prompt_path,
        internal_negative_filter_prompt_path=internal_negative_filter_prompt_path,
        beijing_gate_prompt_path=beijing_gate_prompt_path,
        external_filter_batch_size=external_filter_batch_size,
        external_filter_max_retries=external_filter_max_retries,
        beijing_gate_max_retries=beijing_gate_max_retries,
    )


__all__ = [
    "DEFAULT_LLM_ALLOWED_HOSTS",
    "Settings",
    "get_settings",
    "load_environment",
    "refresh_environment_if_changed",
    "reload_environment",
]
