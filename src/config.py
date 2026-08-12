"""
Spotify CLI 설정 관리 모듈.

환경 변수 및 경로 설정을 중앙에서 관리합니다.
.env 파일에서 환경 변수를 로드합니다.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# 프로젝트 루트의 .env 파일 로드
_project_root = Path(__file__).parent.parent
load_dotenv(_project_root / ".env")


def get_config_dir() -> Path:
    """
    OS별 설정 디렉토리 경로를 반환합니다.

    Returns:
        Path: 설정 디렉토리 경로
            - Unix/Linux/macOS: ~/.config/spotify-cli/
            - Windows: %APPDATA%\\spotify-cli\\
    """
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path.home() / ".config"

    return base / "spotify-cli"


def get_client_id() -> str:
    """
    Spotify Client ID를 환경 변수에서 가져옵니다.

    Returns:
        str: Spotify Client ID

    Raises:
        ValueError: SPOTIFY_CLIENT_ID 환경 변수가 설정되지 않은 경우
    """
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    if not client_id:
        raise ValueError(
            "[오류] SPOTIFY_CLIENT_ID 환경 변수가 설정되지 않았습니다.\n"
            "export SPOTIFY_CLIENT_ID=your_client_id_here"
        )
    return client_id


def get_redirect_uri(port: int) -> str:
    """
    OAuth 콜백 Redirect URI를 반환합니다.

    Args:
        port: 로컬 서버 포트 번호

    Returns:
        str: Redirect URI (예: https://127.0.0.1:8080/callback)
    """
    return f"https://127.0.0.1:{port}/callback"


# OAuth 설정 상수
OAUTH_SCOPES: list[str] = [
    "user-read-playback-state",
    "user-modify-playback-state",
    "user-read-currently-playing",
    "user-library-modify",
    "user-library-read",
]

# 서버 설정 상수
DEFAULT_PORT: int = 8080
MAX_PORT_ATTEMPTS: int = 5
AUTH_TIMEOUT_SECONDS: int = 180  # 3분

# HTTP 요청 설정
HTTP_TIMEOUT_SECONDS: int = 10
