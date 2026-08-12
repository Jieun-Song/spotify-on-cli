"""
검색 결과 캐시 관리 모듈.

last_search.json 파일에 최근 검색 결과를 저장하고 로드합니다.
- 캐시 유효 기간: 24시간
- 만료된 캐시는 자동 삭제
"""

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Optional, TypedDict

from src.config import get_config_dir

SEARCH_CACHE_FILE = "last_search.json"
CACHE_EXPIRY_SECONDS = 24 * 60 * 60  # 24시간


class SearchResultItem(TypedDict):
    """검색 결과 아이템 타입."""

    index: int
    track_id: str
    track_uri: str
    name: str
    artists: list[str]
    album: str
    popularity: int
    is_liked: bool


class SearchCache(TypedDict):
    """검색 캐시 타입."""

    query: str
    results: list[SearchResultItem]
    saved_at: float  # Unix timestamp


def _get_cache_path() -> Path:
    """
    last_search.json 파일 경로를 반환합니다.

    Returns:
        Path: last_search.json 전체 경로
    """
    return get_config_dir() / SEARCH_CACHE_FILE


def _atomic_write(path: Path, data: str) -> None:
    """
    파일을 atomic하게 씁니다 (임시 파일 -> rename).

    Args:
        path: 대상 파일 경로
        data: 쓸 데이터
    """
    dir_path = path.parent
    fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def save_search_cache(query: str, results: list[SearchResultItem]) -> Path:
    """
    검색 결과를 last_search.json 파일에 저장합니다.

    Args:
        query: 검색어
        results: 검색 결과 목록

    Returns:
        Path: 저장된 캐시 파일 경로

    Raises:
        OSError: 파일 쓰기 실패 시
    """
    config_dir = get_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)

    cache_path = _get_cache_path()

    cache_data: SearchCache = {
        "query": query,
        "results": results,
        "saved_at": time.time(),
    }

    json_data = json.dumps(cache_data, indent=2, ensure_ascii=False)
    _atomic_write(cache_path, json_data)

    return cache_path


def load_search_cache() -> Optional[SearchCache]:
    """
    last_search.json에서 검색 캐시를 로드합니다.

    24시간이 지난 캐시는 만료 처리하여 삭제하고 None을 반환합니다.

    Returns:
        Optional[SearchCache]: 유효한 검색 캐시 또는 None
    """
    cache_path = _get_cache_path()

    if not cache_path.exists():
        return None

    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 필수 필드 검증
        required_fields = ["query", "results", "saved_at"]
        if not all(field in data for field in required_fields):
            delete_search_cache()
            return None

        # 24시간 만료 체크
        if is_cache_expired(data["saved_at"]):
            delete_search_cache()
            return None

        return SearchCache(
            query=data["query"],
            results=data["results"],
            saved_at=data["saved_at"],
        )

    except (json.JSONDecodeError, KeyError, TypeError):
        # 손상된 파일 삭제
        delete_search_cache()
        return None


def delete_search_cache() -> bool:
    """
    last_search.json 파일을 삭제합니다.

    Returns:
        bool: 삭제 성공 여부
    """
    cache_path = _get_cache_path()

    if not cache_path.exists():
        return True

    try:
        cache_path.unlink()
        return True
    except OSError:
        return False


def is_cache_expired(saved_at: float) -> bool:
    """
    캐시가 만료되었는지 확인합니다.

    Args:
        saved_at: 캐시 저장 시간 (Unix timestamp)

    Returns:
        bool: 24시간이 지났으면 True
    """
    return time.time() - saved_at > CACHE_EXPIRY_SECONDS


def get_cached_result_by_index(index: int) -> Optional[SearchResultItem]:
    """
    캐시된 검색 결과에서 인덱스로 트랙을 조회합니다.

    Args:
        index: 트랙 인덱스 (1부터 시작)

    Returns:
        Optional[SearchResultItem]: 해당 인덱스의 트랙 또는 None
    """
    cache = load_search_cache()
    if cache is None:
        return None

    # 인덱스는 1부터 시작
    if index < 1 or index > len(cache["results"]):
        return None

    return cache["results"][index - 1]


# ─────────────────────────────────────────────
# 팟캐스트 쇼 검색 캐시
# ─────────────────────────────────────────────

PODCAST_CACHE_FILE = "last_podcast_search.json"


class PodcastSearchResultItem(TypedDict):
    """팟캐스트 쇼 검색 결과 아이템 타입."""

    index: int
    show_id: str
    show_uri: str
    name: str
    publisher: str
    total_episodes: int


class PodcastSearchCache(TypedDict):
    """팟캐스트 검색 캐시 타입."""

    query: str
    results: list[PodcastSearchResultItem]
    saved_at: float


def _get_podcast_cache_path() -> Path:
    return get_config_dir() / PODCAST_CACHE_FILE


def save_podcast_cache(query: str, results: list[PodcastSearchResultItem]) -> Path:
    config_dir = get_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    cache_path = _get_podcast_cache_path()
    cache_data: PodcastSearchCache = {
        "query": query,
        "results": results,
        "saved_at": time.time(),
    }
    _atomic_write(cache_path, json.dumps(cache_data, indent=2, ensure_ascii=False))
    return cache_path


def load_podcast_cache() -> Optional[PodcastSearchCache]:
    cache_path = _get_podcast_cache_path()
    if not cache_path.exists():
        return None
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not all(k in data for k in ["query", "results", "saved_at"]):
            cache_path.unlink(missing_ok=True)
            return None
        if is_cache_expired(data["saved_at"]):
            cache_path.unlink(missing_ok=True)
            return None
        return PodcastSearchCache(
            query=data["query"],
            results=data["results"],
            saved_at=data["saved_at"],
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        cache_path.unlink(missing_ok=True)
        return None


def get_cached_podcast_by_index(index: int) -> Optional[PodcastSearchResultItem]:
    cache = load_podcast_cache()
    if cache is None:
        return None
    if index < 1 or index > len(cache["results"]):
        return None
    return cache["results"][index - 1]


# ─────────────────────────────────────────────
# 에피소드 검색 캐시
# ─────────────────────────────────────────────

EPISODE_CACHE_FILE = "last_episode_search.json"


class EpisodeSearchResultItem(TypedDict):
    """팟캐스트 에피소드 검색 결과 아이템 타입."""

    index: int
    episode_id: str
    episode_uri: str
    name: str
    show_name: str
    duration_ms: int
    release_date: str  # "YYYY-MM-DD"


class EpisodeSearchCache(TypedDict):
    """에피소드 검색 캐시 타입."""

    query: str
    results: list[EpisodeSearchResultItem]
    saved_at: float


def _get_episode_cache_path() -> Path:
    return get_config_dir() / EPISODE_CACHE_FILE


def save_episode_cache(query: str, results: list[EpisodeSearchResultItem]) -> Path:
    config_dir = get_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    cache_path = _get_episode_cache_path()
    cache_data: EpisodeSearchCache = {
        "query": query,
        "results": results,
        "saved_at": time.time(),
    }
    _atomic_write(cache_path, json.dumps(cache_data, indent=2, ensure_ascii=False))
    return cache_path


def load_episode_cache() -> Optional[EpisodeSearchCache]:
    cache_path = _get_episode_cache_path()
    if not cache_path.exists():
        return None
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not all(k in data for k in ["query", "results", "saved_at"]):
            cache_path.unlink(missing_ok=True)
            return None
        if is_cache_expired(data["saved_at"]):
            cache_path.unlink(missing_ok=True)
            return None
        return EpisodeSearchCache(
            query=data["query"],
            results=data["results"],
            saved_at=data["saved_at"],
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        cache_path.unlink(missing_ok=True)
        return None


def get_cached_episode_by_index(index: int) -> Optional[EpisodeSearchResultItem]:
    cache = load_episode_cache()
    if cache is None:
        return None
    if index < 1 or index > len(cache["results"]):
        return None
    return cache["results"][index - 1]


# ─────────────────────────────────────────────
# 플레이리스트 검색 캐시
# ─────────────────────────────────────────────

PLAYLIST_CACHE_FILE = "last_playlist_search.json"


class PlaylistSearchResultItem(TypedDict):
    """플레이리스트 검색 결과 아이템 타입."""

    index: int
    playlist_id: str
    playlist_uri: str
    name: str
    owner: str
    track_count: int


class PlaylistSearchCache(TypedDict):
    """플레이리스트 검색 캐시 타입."""

    query: str
    results: list[PlaylistSearchResultItem]
    saved_at: float


def _get_playlist_cache_path() -> Path:
    return get_config_dir() / PLAYLIST_CACHE_FILE


def save_playlist_cache(query: str, results: list[PlaylistSearchResultItem]) -> Path:
    config_dir = get_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    cache_path = _get_playlist_cache_path()
    cache_data: PlaylistSearchCache = {
        "query": query,
        "results": results,
        "saved_at": time.time(),
    }
    _atomic_write(cache_path, json.dumps(cache_data, indent=2, ensure_ascii=False))
    return cache_path


def load_playlist_cache() -> Optional[PlaylistSearchCache]:
    cache_path = _get_playlist_cache_path()
    if not cache_path.exists():
        return None
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not all(k in data for k in ["query", "results", "saved_at"]):
            cache_path.unlink(missing_ok=True)
            return None
        if is_cache_expired(data["saved_at"]):
            cache_path.unlink(missing_ok=True)
            return None
        return PlaylistSearchCache(
            query=data["query"],
            results=data["results"],
            saved_at=data["saved_at"],
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        cache_path.unlink(missing_ok=True)
        return None


def get_cached_playlist_by_index(index: int) -> Optional[PlaylistSearchResultItem]:
    cache = load_playlist_cache()
    if cache is None:
        return None
    if index < 1 or index > len(cache["results"]):
        return None
    return cache["results"][index - 1]
