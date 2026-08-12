"""LRCLIB 가사 서비스 모듈.

API 키 없이 무료로 사용 가능한 LRCLIB에서 가사를 검색합니다.
"""

import requests
from typing import Optional, TypedDict

LRCLIB_BASE = "https://lrclib.net/api"


class LyricsResult(TypedDict):
    track_name: str
    artist_name: str
    plain_lyrics: str


def search_lyrics(track_name: str, artist_name: str) -> Optional[LyricsResult]:
    """LRCLIB에서 가사를 검색합니다. 결과 없거나 실패 시 None 반환."""
    try:
        resp = requests.get(
            f"{LRCLIB_BASE}/search",
            params={"track_name": track_name, "artist_name": artist_name},
            timeout=10,
        )
        if resp.status_code != 200 or not resp.text:
            return None

        results = resp.json()
        if not isinstance(results, list) or not results:
            return None

        item = results[0]
        plain = item.get("plainLyrics") or ""
        if not plain:
            return None

        return LyricsResult(
            track_name=item.get("trackName") or track_name,
            artist_name=item.get("artistName") or artist_name,
            plain_lyrics=plain,
        )
    except Exception:
        return None
