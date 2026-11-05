"""
SQLite 持久化相关的统一命名规则。
"""
from __future__ import annotations


DEFAULT_DATABASE_PATH = "data/app.sqlite3"
RESULT_FILE_SUFFIX = "_full_data.jsonl"

PLATFORM_XIANYU = "xianyu"
PLATFORM_KONGFZ = "kongfz"
SUPPORTED_PLATFORMS = (PLATFORM_XIANYU, PLATFORM_KONGFZ)

# 每个平台的结果文件名前缀，闲鱼保持空前缀以兼容历史数据。
PLATFORM_FILENAME_PREFIX = {
    PLATFORM_XIANYU: "",
    PLATFORM_KONGFZ: "kongfz_",
}


def normalize_platform(platform: str | None) -> str:
    value = str(platform or "").strip().lower()
    return value if value in SUPPORTED_PLATFORMS else PLATFORM_XIANYU


def build_result_filename(keyword: str, platform: str | None = PLATFORM_XIANYU) -> str:
    prefix = PLATFORM_FILENAME_PREFIX[normalize_platform(platform)]
    return f"{prefix}{str(keyword or '').replace(' ', '_')}{RESULT_FILE_SUFFIX}"


def detect_platform_from_filename(filename: str) -> str:
    text = str(filename or "")
    if text.startswith(PLATFORM_FILENAME_PREFIX[PLATFORM_KONGFZ]):
        return PLATFORM_KONGFZ
    return PLATFORM_XIANYU


def normalize_keyword_from_filename(filename: str) -> str:
    text = str(filename or "").replace(RESULT_FILE_SUFFIX, "")
    prefix = PLATFORM_FILENAME_PREFIX[PLATFORM_KONGFZ]
    if text.startswith(prefix):
        return text[len(prefix):]
    return text


def normalize_keyword_slug(keyword: str) -> str:
    text = "".join(
        char for char in str(keyword or "").lower().replace(" ", "_")
        if char.isalnum() or char in "_-"
    ).rstrip("_")
    return text or "unknown"
