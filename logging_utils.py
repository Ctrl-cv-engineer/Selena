import logging
import os
import re
from datetime import datetime, timedelta
from logging.handlers import TimedRotatingFileHandler


DEFAULT_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(message)s"
DEFAULT_DATE_PREFIX_FORMAT = "%Y-%m-%d %H:%M:%S"
DEFAULT_BACKUP_COUNT = 30
_ROTATED_LOG_PATTERN_TEMPLATE = r"^{base}\.\d{{4}}-\d{{2}}-\d{{2}}$"
DEFAULT_DATABASE_LOGGER_NAMES = ("httpx", "database.request")


def ensure_log_dir(base_dir: str) -> str:
    log_dir = os.path.join(base_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    return log_dir


def build_component_log_path(base_dir: str, component_name: str) -> str:
    return os.path.join(ensure_log_dir(base_dir), f"{component_name}.log")


def build_daily_log_path(base_dir: str, component_name: str, current_time: datetime = None) -> str:
    current_time = current_time or datetime.now()
    return os.path.join(
        ensure_log_dir(base_dir),
        f"{component_name}.{current_time.strftime('%Y-%m-%d')}.log"
    )


def build_sibling_log_path(log_path: str, suffix: str) -> str:
    directory = os.path.dirname(log_path) or "."
    base_name = os.path.basename(log_path)
    stem, extension = os.path.splitext(base_name)
    extension = extension or ".log"
    normalized_suffix = str(suffix or "").strip()
    if normalized_suffix and not normalized_suffix.startswith("_"):
        normalized_suffix = f"_{normalized_suffix}"
    return os.path.join(directory, f"{stem}{normalized_suffix}{extension}")


def build_qdrant_http_patterns(host: str, port: int):
    normalized_host = str(host or "127.0.0.1").strip()
    normalized_port = int(port)
    return (
        f"http://{normalized_host}:{normalized_port}",
        f"https://{normalized_host}:{normalized_port}"
    )


def cleanup_daily_log_files(base_dir: str, component_name: str, keep_count: int = DEFAULT_BACKUP_COUNT):
    log_dir = ensure_log_dir(base_dir)
    daily_pattern = re.compile(
        rf"^{re.escape(component_name)}\.(\d{{4}}-\d{{2}}-\d{{2}})\.log$"
    )
    matched_files = []
    try:
        for file_name in os.listdir(log_dir):
            match = daily_pattern.match(file_name)
            if not match:
                continue
            matched_files.append((match.group(1), os.path.join(log_dir, file_name)))
    except FileNotFoundError:
        return

    matched_files.sort(reverse=True)
    for _, file_path in matched_files[keep_count:]:
        try:
            os.remove(file_path)
        except OSError:
            pass


class _PredicateFilter(logging.Filter):
    def __init__(self, predicate, *, include_match: bool = True):
        super().__init__()
        self.predicate = predicate
        self.include_match = include_match

    def filter(self, record: logging.LogRecord) -> bool:
        matched = bool(self.predicate(record))
        return matched if self.include_match else not matched


def _build_qdrant_http_record_matcher(message_patterns):
    normalized_patterns = tuple(pattern for pattern in (message_patterns or ()) if pattern)

    def matcher(record: logging.LogRecord) -> bool:
        logger_name = record.name or ""
        if logger_name != "httpx" and not logger_name.startswith("httpx."):
            return False
        message = record.getMessage()
        if "HTTP Request:" not in message:
            return False
        if not normalized_patterns:
            return True
        return any(pattern in message for pattern in normalized_patterns)

    return matcher


def _build_database_record_matcher(http_message_patterns, database_logger_names):
    http_matcher = _build_qdrant_http_record_matcher(http_message_patterns)
    normalized_database_logger_names = tuple(
        str(logger_name or "").strip()
        for logger_name in (database_logger_names or ())
        if str(logger_name or "").strip()
    )

    def matcher(record: logging.LogRecord) -> bool:
        logger_name = record.name or ""
        if http_matcher(record):
            return True
        return any(
            logger_name == candidate or logger_name.startswith(f"{candidate}.")
            for candidate in normalized_database_logger_names
            if candidate and candidate != "httpx"
        )

    return matcher


def _build_rotating_file_handler(
    log_path: str,
    level: int,
    formatter: logging.Formatter,
    backup_count: int,
    handler_filters=None
):
    directory = os.path.dirname(log_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    handler = TimedRotatingFileHandler(
        log_path,
        when="midnight",
        interval=1,
        backupCount=backup_count,
        encoding="utf-8",
        delay=True
    )
    handler.setLevel(level)
    handler.setFormatter(formatter)
    for handler_filter in handler_filters or ():
        handler.addFilter(handler_filter)
    return handler


def _reset_logger_handlers(logger: logging.Logger):
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass


def configure_root_logger(
    log_path: str,
    *,
    level: int = logging.INFO,
    backup_count: int = DEFAULT_BACKUP_COUNT,
    with_console: bool = True,
    database_log_path: str = None,
    database_message_patterns=None,
    database_logger_names=DEFAULT_DATABASE_LOGGER_NAMES
):
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    formatter = logging.Formatter(DEFAULT_LOG_FORMAT)
    _reset_logger_handlers(root_logger)
    root_handler_filters = []
    database_record_matcher = None
    if database_log_path:
        database_record_matcher = _build_database_record_matcher(
            database_message_patterns,
            database_logger_names
        )
        root_handler_filters.append(
            _PredicateFilter(database_record_matcher, include_match=False)
        )

    root_logger.addHandler(
        _build_rotating_file_handler(
            log_path,
            level,
            formatter,
            backup_count,
            handler_filters=root_handler_filters
        )
    )
    if with_console:
        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(level)
        stream_handler.setFormatter(formatter)
        for handler_filter in root_handler_filters:
            stream_handler.addFilter(handler_filter)
        root_logger.addHandler(stream_handler)

    if database_log_path:
        shared_database_handler = _build_rotating_file_handler(
            database_log_path,
            level,
            formatter,
            backup_count,
            handler_filters=[
                _PredicateFilter(database_record_matcher, include_match=True)
            ]
        )
        for logger_name in database_logger_names or ():
            database_logger = logging.getLogger(logger_name)
            database_logger.setLevel(level)
            database_logger.propagate = False
            _reset_logger_handlers(database_logger)
            database_logger.addHandler(shared_database_handler)
    return root_logger


def configure_logger(
    logger_name: str,
    log_path: str,
    *,
    level: int = logging.INFO,
    backup_count: int = DEFAULT_BACKUP_COUNT,
    with_console: bool = True,
    propagate: bool = False
):
    logger = logging.getLogger(logger_name)
    logger.setLevel(level)
    logger.propagate = propagate
    formatter = logging.Formatter(DEFAULT_LOG_FORMAT)
    _reset_logger_handlers(logger)
    logger.addHandler(_build_rotating_file_handler(log_path, level, formatter, backup_count))
    if with_console:
        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(level)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)
    return logger


def list_log_files(log_path: str):
    directory = os.path.dirname(log_path) or "."
    base_name = os.path.basename(log_path)
    rotated_pattern = re.compile(_ROTATED_LOG_PATTERN_TEMPLATE.format(base=re.escape(base_name)))
    rotated_files = []
    try:
        for file_name in os.listdir(directory):
            if rotated_pattern.match(file_name):
                rotated_files.append(os.path.join(directory, file_name))
    except FileNotFoundError:
        return [log_path] if os.path.exists(log_path) else []

    rotated_files.sort()
    if os.path.exists(log_path):
        rotated_files.append(log_path)
    return rotated_files


def read_recent_log_tail(log_path: str, *, hours: int = 1, max_lines: int = 40) -> str:
    cutoff_time = datetime.now() - timedelta(hours=hours)
    filtered_lines = []
    keep_current_block = False

    for path in list_log_files(log_path):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as file:
                lines = file.readlines()
        except Exception:
            continue

        for line in lines:
            timestamp_str = line[:19]
            try:
                log_time = datetime.strptime(timestamp_str, DEFAULT_DATE_PREFIX_FORMAT)
                keep_current_block = log_time >= cutoff_time
            except ValueError:
                pass
            if keep_current_block:
                filtered_lines.append(line)

    return "".join(filtered_lines[-max_lines:]).strip()
