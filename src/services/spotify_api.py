"""
Spotify Web API 클라이언트 모듈.

저장된 토큰을 사용하여 Spotify API를 호출합니다.
401 에러 시 자동으로 토큰을 갱신하고 재시도합니다.
Premium 계정 검증 등 계정 관련 기능을 제공합니다.
"""

import sys
from typing import Any, Optional, TypedDict, TypeVar

import requests

from src.config import HTTP_TIMEOUT_SECONDS
from src.utils.console import debug_log
from src.services.auth import refresh_access_token, TokenError
from src.services.token_storage import (
    load_token,
    save_token,
    is_token_expired,
    token_lock,
    StoredToken,
)

T = TypeVar("T")


SPOTIFY_API_BASE = "https://api.spotify.com/v1"


class UserProfile(TypedDict):
    """Spotify 사용자 프로필 타입."""

    id: str
    display_name: Optional[str]
    email: Optional[str]
    product: str  # "premium", "free", "open"
    country: Optional[str]


class TrackInfo(TypedDict):
    """트랙/에피소드 재생 정보 타입."""

    name: str
    artists: list[str]  # 에피소드일 때: [쇼 이름]
    album: str          # 에피소드일 때: 퍼블리셔
    duration_ms: int
    progress_ms: int
    is_playing: bool
    item_type: str      # "track" | "episode"


class SpotifyAPIError(Exception):
    """Spotify API 호출 실패 시 발생하는 예외."""

    def __init__(self, status_code: int, error: str, message: str) -> None:
        self.status_code = status_code
        self.error = error
        self.message = message
        super().__init__(f"[{status_code}] {error}: {message}")


class TokenNotFoundError(Exception):
    """저장된 토큰이 없을 때 발생하는 예외."""

    def __init__(self) -> None:
        super().__init__(
            "[오류] 저장된 토큰이 없습니다. 'soc login'으로 먼저 로그인하세요."
        )


class TokenExpiredError(Exception):
    """토큰이 만료되었을 때 발생하는 예외."""

    def __init__(self) -> None:
        super().__init__(
            "[오류] 토큰이 만료되었습니다. 'soc login'으로 다시 로그인하세요."
        )


class NotPremiumError(Exception):
    """Premium 계정이 아닐 때 발생하는 예외."""

    def __init__(self, product: str) -> None:
        self.product = product
        super().__init__(
            f"[오류] Spotify Premium 계정이 필요합니다.\n"
            f"       현재 계정 유형: {product}\n"
            f"       Premium 구독: https://www.spotify.com/premium/"
        )


class RefreshFailedError(Exception):
    """토큰 갱신 실패 시 발생하는 예외."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(
            f"[오류] 토큰 갱신에 실패했습니다: {reason}\n"
            f"       'soc login'으로 다시 로그인하세요."
        )


def _safe_json_parse(response: requests.Response) -> dict[str, Any]:
    """
    응답 본문을 안전하게 JSON으로 파싱합니다.

    204 No Content 또는 빈 응답 본문인 경우 빈 딕셔너리를 반환합니다.

    Args:
        response: requests 응답 객체

    Returns:
        dict[str, Any]: 파싱된 JSON 또는 빈 딕셔너리
    """
    if response.status_code == 204 or not response.text or not response.text.strip():
        return {}
    try:
        return response.json()
    except Exception:
        return {}


def _get_auth_header(token: StoredToken) -> dict[str, str]:
    """
    API 요청용 Authorization 헤더를 생성합니다.

    Args:
        token: 저장된 토큰

    Returns:
        dict[str, str]: Authorization 헤더
    """
    return {"Authorization": f"{token['token_type']} {token['access_token']}"}


def _try_refresh_token(token: StoredToken) -> StoredToken:
    """
    토큰 갱신을 시도하고 새 토큰을 저장합니다.

    파일 잠금을 사용하여 race condition을 방지합니다.
    잠금 획득 후 다른 프로세스가 이미 갱신했는지 확인합니다.

    Args:
        token: 현재 저장된 토큰 (refresh_token 포함)

    Returns:
        StoredToken: 갱신된 토큰

    Raises:
        RefreshFailedError: 토큰 갱신 실패 시
        TimeoutError: 파일 잠금 획득 시간 초과 시
    """
    try:
        with token_lock():
            # 잠금 획득 후 토큰 다시 확인 (다른 프로세스가 이미 갱신했을 수 있음)
            current_token = load_token()
            if current_token is not None and not is_token_expired(current_token):
                # 다른 프로세스가 이미 갱신함
                return current_token

            # 갱신 진행
            new_token = refresh_access_token(token["refresh_token"])

            # 새 토큰 저장 (atomic write)
            save_token(
                access_token=new_token["access_token"],
                token_type=new_token["token_type"],
                scope=new_token["scope"],
                expires_in=new_token["expires_in"],
                refresh_token=new_token["refresh_token"],
            )

            # 저장된 토큰 다시 로드
            refreshed = load_token()
            if refreshed is None:
                raise RefreshFailedError("토큰 저장 후 로드 실패")

            print("[안내] 토큰이 자동으로 갱신되었습니다.")
            return refreshed

    except TokenError as e:
        raise RefreshFailedError(e.description) from e


def _load_valid_token(allow_refresh: bool = True) -> StoredToken:
    """
    유효한 토큰을 로드합니다. 만료 시 자동 갱신을 시도합니다.

    Args:
        allow_refresh: 만료 시 자동 갱신 허용 여부

    Returns:
        StoredToken: 유효한 토큰

    Raises:
        TokenNotFoundError: 토큰이 없는 경우
        TokenExpiredError: 토큰이 만료되고 갱신이 비활성화된 경우
        RefreshFailedError: 토큰 갱신 실패 시
    """
    token = load_token()

    if token is None:
        raise TokenNotFoundError()

    if is_token_expired(token):
        if not allow_refresh:
            raise TokenExpiredError()
        # 자동 갱신 시도
        return _try_refresh_token(token)

    return token


def _api_request_with_retry(
    method: str,
    url: str,
    token: StoredToken,
    **kwargs: Any,
) -> requests.Response:
    """
    API 요청을 실행하고, 401 에러 시 토큰 갱신 후 재시도합니다.

    Args:
        method: HTTP 메서드 (GET, POST, PUT, DELETE)
        url: 요청 URL
        token: 현재 토큰
        **kwargs: requests에 전달할 추가 인자

    Returns:
        requests.Response: API 응답

    Raises:
        RefreshFailedError: 토큰 갱신 실패 시
        requests.RequestException: 네트워크 오류 시
    """
    headers = _get_auth_header(token)
    if "headers" in kwargs:
        headers.update(kwargs.pop("headers"))

    # 디버그: 요청 로깅
    debug_log(f"HTTP {method} {url}")
    if kwargs.get("params"):
        debug_log(f"  Params: {kwargs['params']}")
    if kwargs.get("json"):
        debug_log(f"  Body: {kwargs['json']}")

    response = requests.request(
        method,
        url,
        headers=headers,
        timeout=kwargs.pop("timeout", HTTP_TIMEOUT_SECONDS),
        **kwargs,
    )

    # 디버그: 응답 로깅
    debug_log(f"  Response: {response.status_code}")

    # 401 Unauthorized → 토큰 갱신 후 재시도
    if response.status_code == 401:
        debug_log("  Token expired, refreshing...")
        print("[안내] 토큰이 만료되었습니다. 갱신을 시도합니다...")
        new_token = _try_refresh_token(token)

        # 재시도
        headers = _get_auth_header(new_token)
        debug_log(f"HTTP {method} {url} (retry)")
        response = requests.request(
            method,
            url,
            headers=headers,
            timeout=HTTP_TIMEOUT_SECONDS,
            **kwargs,
        )
        debug_log(f"  Response: {response.status_code}")

    return response


def get_current_user() -> UserProfile:
    """
    현재 로그인한 사용자의 프로필을 가져옵니다.

    401 에러 발생 시 자동으로 토큰을 갱신하고 재시도합니다.

    Returns:
        UserProfile: 사용자 프로필 정보

    Raises:
        TokenNotFoundError: 저장된 토큰이 없는 경우
        RefreshFailedError: 토큰 갱신 실패 시 (재로그인 필요)
        SpotifyAPIError: API 호출 실패 시
    """
    token = _load_valid_token()

    response = _api_request_with_retry(
        "GET",
        f"{SPOTIFY_API_BASE}/me",
        token,
    )

    data = _safe_json_parse(response)

    if response.status_code != 200:
        raise SpotifyAPIError(
            status_code=response.status_code,
            error=data.get("error", {}).get("status", "unknown"),
            message=data.get("error", {}).get("message", "API 호출에 실패했습니다."),
        )

    return UserProfile(
        id=data["id"],
        display_name=data.get("display_name"),
        email=data.get("email"),
        product=data.get("product", "free"),
        country=data.get("country"),
    )


def get_currently_playing() -> Optional[TrackInfo]:
    """
    현재 재생 중인 트랙 정보를 가져옵니다.

    401 에러 발생 시 자동으로 토큰을 갱신하고 재시도합니다.

    Returns:
        Optional[TrackInfo]: 재생 중인 트랙 정보, 재생 중이 아니면 None

    Raises:
        TokenNotFoundError: 저장된 토큰이 없는 경우
        RefreshFailedError: 토큰 갱신 실패 시 (재로그인 필요)
        SpotifyAPIError: API 호출 실패 시
    """
    token = _load_valid_token()

    response = _api_request_with_retry(
        "GET",
        f"{SPOTIFY_API_BASE}/me/player/currently-playing",
        token,
    )

    # 204 No Content: 재생 중인 트랙 없음
    if response.status_code == 204:
        return None

    # 빈 응답 처리
    if not response.text:
        return None

    data = _safe_json_parse(response)

    if response.status_code != 200:
        raise SpotifyAPIError(
            status_code=response.status_code,
            error=data.get("error", {}).get("status", "unknown"),
            message=data.get("error", {}).get("message", "API 호출에 실패했습니다."),
        )

    item = data.get("item")
    if item is None:
        # 지역 제한으로 item이 null이지만 팟캐스트가 재생 중인 경우 (한국 등)
        if data.get("is_playing") and data.get("currently_playing_type") == "episode":
            return TrackInfo(
                name="팟캐스트 에피소드 재생 중",
                artists=[""],
                album="",
                duration_ms=0,
                progress_ms=data.get("progress_ms", 0),
                is_playing=True,
                item_type="episode",
            )
        return None

    item_type = item.get("type", "track")

    if item_type == "episode":
        show = item.get("show") or {}
        return TrackInfo(
            name=item["name"],
            artists=[show.get("name", "")],
            album=show.get("publisher", ""),
            duration_ms=item.get("duration_ms", 0),
            progress_ms=data.get("progress_ms", 0),
            is_playing=data.get("is_playing", False),
            item_type="episode",
        )

    artists = [a["name"] for a in item.get("artists", [])]
    return TrackInfo(
        name=item["name"],
        artists=artists,
        album=item.get("album", {}).get("name", "Unknown Album"),
        duration_ms=item.get("duration_ms", 0),
        progress_ms=data.get("progress_ms", 0),
        is_playing=data.get("is_playing", False),
        item_type="track",
    )


def verify_premium_account() -> UserProfile:
    """
    현재 계정이 Premium인지 검증합니다.

    Premium 계정이 아니면 NotPremiumError를 발생시킵니다.

    Returns:
        UserProfile: Premium 계정의 사용자 프로필

    Raises:
        TokenNotFoundError: 저장된 토큰이 없는 경우
        TokenExpiredError: 토큰이 만료된 경우
        SpotifyAPIError: API 호출 실패 시
        NotPremiumError: Premium 계정이 아닌 경우
    """
    user = get_current_user()

    if user["product"] != "premium":
        raise NotPremiumError(user["product"])

    return user


def require_premium() -> UserProfile:
    """
    Premium 계정을 요구하고, 아니면 에러 출력 후 종료합니다.

    CLI 명령어 시작 시 호출하여 Premium 계정 여부를 확인합니다.
    토큰 만료 시 자동 갱신을 시도하고, 실패 시 재로그인을 유도합니다.
    에러 발생 시 사용자 친화적 메시지를 출력하고 종료합니다.

    Returns:
        UserProfile: Premium 계정의 사용자 프로필

    Note:
        이 함수는 에러 발생 시 sys.exit(1)을 호출합니다.
    """
    try:
        return verify_premium_account()
    except (TokenNotFoundError, TokenExpiredError, NotPremiumError, RefreshFailedError) as e:
        print(str(e))
        sys.exit(1)
    except SpotifyAPIError as e:
        print(f"[오류] Spotify API 호출 실패: {e.message}")
        sys.exit(1)
    except requests.RequestException as e:
        print(f"[오류] 네트워크 오류: {e}")
        sys.exit(1)


class PlaybackState(TypedDict):
    """재생 상태 정보 타입."""

    is_playing: bool
    progress_ms: int
    device_id: Optional[str]
    device_name: Optional[str]
    shuffle_state: bool
    repeat_state: str  # "off", "track", "context"


class NoActiveDeviceError(Exception):
    """활성화된 재생 디바이스가 없을 때 발생하는 예외."""

    def __init__(self) -> None:
        super().__init__(
            "[오류] 활성화된 Spotify 재생 디바이스가 없습니다.\n"
            "       Spotify 앱을 열고 음악을 재생해 주세요."
        )


def get_queue() -> dict:
    """현재 재생 큐를 반환합니다. currently_playing과 queue 목록 포함."""
    token = _load_valid_token()
    resp = _api_request_with_retry("GET", f"{SPOTIFY_API_BASE}/me/player/queue", token)
    if resp.status_code != 200:
        return {"currently_playing": None, "queue": []}
    data = _safe_json_parse(resp)

    def _parse_item(t: dict) -> dict:
        if t.get("type") == "episode":
            return {
                "name": t.get("name", ""),
                "artists": [(t.get("show") or {}).get("name", "")],
                "type": "episode",
            }
        return {
            "name": t.get("name", ""),
            "artists": [a["name"] for a in t.get("artists", [])],
            "type": "track",
        }

    cp = data.get("currently_playing")
    return {
        "currently_playing": _parse_item(cp) if cp else None,
        "queue": [_parse_item(t) for t in data.get("queue", [])],
    }


def get_playback_state() -> Optional[PlaybackState]:
    """
    현재 재생 상태를 가져옵니다.

    Returns:
        Optional[PlaybackState]: 재생 상태 정보, 활성 디바이스가 없으면 None

    Raises:
        TokenNotFoundError: 저장된 토큰이 없는 경우
        RefreshFailedError: 토큰 갱신 실패 시
        SpotifyAPIError: API 호출 실패 시
    """
    token = _load_valid_token()

    response = _api_request_with_retry(
        "GET",
        f"{SPOTIFY_API_BASE}/me/player",
        token,
    )

    # 204 No Content: 활성 디바이스 없음
    if response.status_code == 204 or not response.text:
        return None

    data = _safe_json_parse(response)

    if response.status_code != 200:
        raise SpotifyAPIError(
            status_code=response.status_code,
            error=data.get("error", {}).get("status", "unknown"),
            message=data.get("error", {}).get("message", "API 호출에 실패했습니다."),
        )

    device = data.get("device", {})

    return PlaybackState(
        is_playing=data.get("is_playing", False),
        progress_ms=data.get("progress_ms", 0),
        device_id=device.get("id"),
        device_name=device.get("name"),
        shuffle_state=data.get("shuffle_state", False),
        repeat_state=data.get("repeat_state", "off"),
    )


def pause_playback() -> bool:
    """
    현재 재생을 일시정지합니다.

    이미 일시정지 상태이면 False를 반환하고, 성공적으로 일시정지하면 True를 반환합니다.
    Premium 계정이 필요합니다.

    Returns:
        bool: 일시정지 성공 시 True, 이미 일시정지 상태면 False

    Raises:
        TokenNotFoundError: 저장된 토큰이 없는 경우
        RefreshFailedError: 토큰 갱신 실패 시
        NoActiveDeviceError: 활성 재생 디바이스가 없는 경우
        SpotifyAPIError: API 호출 실패 시
    """
    # 현재 상태 확인 (상태 검증)
    state = get_playback_state()
    if state is None:
        raise NoActiveDeviceError()

    if not state["is_playing"]:
        # 이미 일시정지 상태
        return False

    token = _load_valid_token()

    response = _api_request_with_retry(
        "PUT",
        f"{SPOTIFY_API_BASE}/me/player/pause",
        token,
    )

    # 204 No Content: 성공
    if response.status_code == 204:
        return True

    # 403: Premium 필요 또는 제한
    if response.status_code == 403:
        raise SpotifyAPIError(
            status_code=403,
            error="forbidden",
            message="이 기능은 Spotify Premium 계정에서만 작동합니다.",
        )

    data = _safe_json_parse(response)
    raise SpotifyAPIError(
        status_code=response.status_code,
        error=data.get("error", {}).get("status", "unknown"),
        message=data.get("error", {}).get("message", "일시정지에 실패했습니다."),
    )


def resume_playback() -> bool:
    """
    일시정지된 재생을 재개합니다.

    이미 재생 중이면 False를 반환하고, 성공적으로 재개하면 True를 반환합니다.
    Premium 계정이 필요합니다.

    Returns:
        bool: 재생 재개 성공 시 True, 이미 재생 중이면 False

    Raises:
        TokenNotFoundError: 저장된 토큰이 없는 경우
        RefreshFailedError: 토큰 갱신 실패 시
        NoActiveDeviceError: 활성 재생 디바이스가 없는 경우
        SpotifyAPIError: API 호출 실패 시
    """
    # 현재 상태 확인 (상태 검증)
    state = get_playback_state()
    if state is None:
        raise NoActiveDeviceError()

    if state["is_playing"]:
        # 이미 재생 중
        return False

    token = _load_valid_token()

    response = _api_request_with_retry(
        "PUT",
        f"{SPOTIFY_API_BASE}/me/player/play",
        token,
    )

    # 204 No Content: 성공
    if response.status_code == 204:
        return True

    # 403: Premium 필요 또는 제한
    if response.status_code == 403:
        raise SpotifyAPIError(
            status_code=403,
            error="forbidden",
            message="이 기능은 Spotify Premium 계정에서만 작동합니다.",
        )

    data = _safe_json_parse(response)
    raise SpotifyAPIError(
        status_code=response.status_code,
        error=data.get("error", {}).get("status", "unknown"),
        message=data.get("error", {}).get("message", "재생 재개에 실패했습니다."),
    )


def skip_to_next() -> None:
    """
    다음 트랙으로 건너뜁니다.

    Premium 계정이 필요합니다.

    Raises:
        TokenNotFoundError: 저장된 토큰이 없는 경우
        RefreshFailedError: 토큰 갱신 실패 시
        NoActiveDeviceError: 활성 재생 디바이스가 없는 경우
        SpotifyAPIError: API 호출 실패 시
    """
    token = _load_valid_token()

    response = _api_request_with_retry(
        "POST",
        f"{SPOTIFY_API_BASE}/me/player/next",
        token,
    )

    if response.status_code in (200, 204):
        return
    if response.status_code == 404:
        raise NoActiveDeviceError()

    data = _safe_json_parse(response)
    raise SpotifyAPIError(
        status_code=response.status_code,
        error=data.get("error", {}).get("status", "unknown"),
        message=data.get("error", {}).get("message", "다음 곡 건너뛰기에 실패했습니다."),
    )


def skip_to_previous() -> None:
    """
    이전 트랙으로 돌아가거나 현재 트랙의 처음으로 이동합니다.

    Spotify 표준 동작: 3초 이상 재생된 경우 곡의 처음으로,
    그렇지 않으면 이전 트랙으로 이동합니다.
    Premium 계정이 필요합니다.

    Raises:
        TokenNotFoundError: 저장된 토큰이 없는 경우
        RefreshFailedError: 토큰 갱신 실패 시
        NoActiveDeviceError: 활성 재생 디바이스가 없는 경우
        SpotifyAPIError: API 호출 실패 시
    """
    token = _load_valid_token()

    response = _api_request_with_retry(
        "POST",
        f"{SPOTIFY_API_BASE}/me/player/previous",
        token,
    )

    if response.status_code in (200, 204):
        return
    if response.status_code == 404:
        raise NoActiveDeviceError()

    data = _safe_json_parse(response)
    raise SpotifyAPIError(
        status_code=response.status_code,
        error=data.get("error", {}).get("status", "unknown"),
        message=data.get("error", {}).get("message", "이전 곡 이동에 실패했습니다."),
    )


def seek_to_position(position_ms: int) -> None:
    """
    재생 위치를 지정된 시간(밀리초)으로 이동합니다.

    Premium 계정이 필요합니다.

    Args:
        position_ms: 이동할 위치 (밀리초, 0 이상)

    Raises:
        TokenNotFoundError: 저장된 토큰이 없는 경우
        RefreshFailedError: 토큰 갱신 실패 시
        NoActiveDeviceError: 활성 재생 디바이스가 없는 경우
        SpotifyAPIError: API 호출 실패 시
    """
    position_ms = max(0, position_ms)
    token = _load_valid_token()

    response = _api_request_with_retry(
        "PUT",
        f"{SPOTIFY_API_BASE}/me/player/seek",
        token,
        params={"position_ms": position_ms},
    )

    if response.status_code in (200, 204):
        return
    if response.status_code == 404:
        raise NoActiveDeviceError()

    data = _safe_json_parse(response)
    raise SpotifyAPIError(
        status_code=response.status_code,
        error=data.get("error", {}).get("status", "unknown"),
        message=data.get("error", {}).get("message", "재생 위치 이동에 실패했습니다."),
    )


def rewind_30_seconds() -> int:
    """
    현재 재생 위치에서 30초 전으로 이동합니다.

    현재 위치가 30초 미만이면 곡의 처음(0초)으로 이동합니다.
    Premium 계정이 필요합니다.

    Returns:
        int: 이동한 위치 (밀리초)

    Raises:
        TokenNotFoundError: 저장된 토큰이 없는 경우
        RefreshFailedError: 토큰 갱신 실패 시
        NoActiveDeviceError: 활성 재생 디바이스가 없는 경우
        SpotifyAPIError: API 호출 실패 시
    """
    track = get_currently_playing()
    if track is None:
        raise NoActiveDeviceError()

    new_position = max(0, track["progress_ms"] - 30000)
    seek_to_position(new_position)
    return new_position


def set_shuffle(state: bool) -> bool:
    """
    셔플 모드를 설정합니다.

    Args:
        state: True면 셔플 켜기, False면 끄기

    Returns:
        bool: 설정된 셔플 상태

    Raises:
        TokenNotFoundError: 저장된 토큰이 없는 경우
        RefreshFailedError: 토큰 갱신 실패 시
        NoActiveDeviceError: 활성 재생 디바이스가 없는 경우
        SpotifyAPIError: API 호출 실패 시
    """
    # 현재 상태 확인 (상태 검증)
    playback = get_playback_state()
    if playback is None:
        raise NoActiveDeviceError()

    token = _load_valid_token()

    response = _api_request_with_retry(
        "PUT",
        f"{SPOTIFY_API_BASE}/me/player/shuffle",
        token,
        params={"state": str(state).lower()},
    )

    if response.status_code in [200, 204]:
    # 성공 처리 로직
        return
    else:
        # 진짜 에러일 때만 예외 발생
        raise SpotifyAPIError("셔플 설정에 실패했습니다.", status_code=response.status_code)


def set_repeat(state: str) -> str:
    """
    반복 모드를 설정합니다.

    Args:
        state: 반복 모드 ("track", "context", "off")
            - "track": 현재 트랙 반복
            - "context": 컨텍스트(앨범/플레이리스트) 반복
            - "off": 반복 끄기

    Returns:
        str: 설정된 반복 상태

    Raises:
        ValueError: 유효하지 않은 state 값인 경우
        TokenNotFoundError: 저장된 토큰이 없는 경우
        RefreshFailedError: 토큰 갱신 실패 시
        NoActiveDeviceError: 활성 재생 디바이스가 없는 경우
        SpotifyAPIError: API 호출 실패 시
    """
    # state 값 검증 (상태 검증)
    valid_states = ("track", "context", "off")
    if state not in valid_states:
        raise ValueError(f"[오류] 유효하지 않은 반복 모드입니다. ({', '.join(valid_states)})")

    # 현재 상태 확인
    playback = get_playback_state()
    if playback is None:
        raise NoActiveDeviceError()

    token = _load_valid_token()

    response = _api_request_with_retry(
        "PUT",
        f"{SPOTIFY_API_BASE}/me/player/repeat",
        token,
        params={"state": state},
    )

    # 200 또는 204 모두 성공
    if response.status_code in (200, 204):
        return state

    if response.status_code == 403:
        raise SpotifyAPIError(
            status_code=403,
            error="forbidden",
            message="이 기능은 Spotify Premium 계정에서만 작동합니다.",
        )

    data = _safe_json_parse(response)
    raise SpotifyAPIError(
        status_code=response.status_code,
        error=data.get("error", {}).get("status", "unknown"),
        message=data.get("error", {}).get("message", "반복 모드 설정에 실패했습니다."),
    )


def get_similar_tracks(track_id: str, limit: int = 10) -> list[str]:
    """
    현재 트랙 이후 이어 들을 트랙 URI 목록을 반환합니다.

    1순위: 아티스트 탑 트랙
    2순위(fallback): 같은 앨범 트랙

    Args:
        track_id: 기준 트랙 ID
        limit: 반환할 최대 트랙 수 (기본값: 10)

    Returns:
        list[str]: 트랙 URI 목록 (현재 트랙 제외)
    """
    token = _load_valid_token()

    # 트랙 정보 (아티스트 ID + 앨범 ID 동시 획득)
    resp = _api_request_with_retry("GET", f"{SPOTIFY_API_BASE}/tracks/{track_id}", token)
    if resp.status_code != 200:
        return []
    data = _safe_json_parse(resp)

    # 1순위: 아티스트 탑 트랙
    artists = data.get("artists", [])
    if artists:
        r = _api_request_with_retry(
            "GET",
            f"{SPOTIFY_API_BASE}/artists/{artists[0]['id']}/top-tracks",
            token,
            params={"market": "KR"},
        )
        if r.status_code == 200:
            uris = [
                t["uri"]
                for t in _safe_json_parse(r).get("tracks", [])
                if t.get("id") != track_id and t.get("uri")
            ]
            if uris:
                return uris[:limit]

    # 2순위: 같은 앨범에서 현재 트랙 이후 트랙
    album_id = (data.get("album") or {}).get("id")
    current_track_number = data.get("track_number", 0)
    if not album_id:
        return []

    r2 = _api_request_with_retry(
        "GET",
        f"{SPOTIFY_API_BASE}/albums/{album_id}/tracks",
        token,
        params={"market": "KR", "limit": 50},
    )
    if r2.status_code != 200:
        return []

    return [
        t["uri"]
        for t in _safe_json_parse(r2).get("items", [])
        if t.get("track_number", 0) > current_track_number and t.get("uri")
    ][:limit]


def play_track(track_uri: str, queue_uris: Optional[list[str]] = None) -> None:
    """
    지정된 트랙을 재생합니다.

    현재 활성화된 디바이스에서 특정 트랙을 즉시 재생합니다.
    queue_uris를 전달하면 해당 트랙에 이어 재생할 곡들을 미리 큐에 채웁니다.
    Premium 계정이 필요합니다.

    Args:
        track_uri: Spotify 트랙 URI (예: "spotify:track:0VjIjW4GlUZAMYd2vXMi3b")
        queue_uris: 이어서 재생할 트랙 URI 목록 (선택, 기본값 None)

    Raises:
        ValueError: track_uri가 유효하지 않은 경우
        TokenNotFoundError: 저장된 토큰이 없는 경우
        RefreshFailedError: 토큰 갱신 실패 시
        NoActiveDeviceError: 활성 재생 디바이스가 없는 경우
        SpotifyAPIError: API 호출 실패 시
    """
    if not track_uri or not track_uri.startswith("spotify:track:"):
        raise ValueError("[오류] 유효하지 않은 트랙 URI입니다.")

    state = get_playback_state()
    if state is None:
        raise NoActiveDeviceError()

    token = _load_valid_token()

    uris = [track_uri] + (queue_uris or [])

    response = _api_request_with_retry(
        "PUT",
        f"{SPOTIFY_API_BASE}/me/player/play",
        token,
        json={"uris": uris},
    )

    # 204 No Content: 성공
    if response.status_code == 204:
        return

    # 403: Premium 필요 또는 제한
    if response.status_code == 403:
        raise SpotifyAPIError(
            status_code=403,
            error="forbidden",
            message="이 기능은 Spotify Premium 계정에서만 작동합니다.",
        )

    # 404: 디바이스를 찾을 수 없음
    if response.status_code == 404:
        raise NoActiveDeviceError()

    data = _safe_json_parse(response)
    raise SpotifyAPIError(
        status_code=response.status_code,
        error=data.get("error", {}).get("status", "unknown"),
        message=data.get("error", {}).get("message", "트랙 재생에 실패했습니다."),
    )


def search_tracks(query: str, limit: int = 10) -> list[dict[str, any]]:
    """
    Spotify에서 트랙을 검색하고 결과를 캐시에 저장합니다.

    검색 결과는 popularity(인기도) 기준 내림차순으로 정렬됩니다.
    결과는 last_search.json에 캐시되어 24시간 동안 유효합니다.

    Args:
        query: 검색어 (빈 문자열 불가)
        limit: 최대 결과 수 (기본값: 10, 최대: 50)

    Returns:
        list[dict]: 검색 결과 목록. 각 항목은 다음 필드를 포함:
            - index: 순번 (1부터 시작)
            - track_id: Spotify 트랙 ID
            - track_uri: Spotify URI (재생용)
            - name: 곡 제목
            - artists: 아티스트 이름 목록
            - album: 앨범 이름
            - popularity: 인기도 (0-100)
            - is_liked: 좋아요 여부 (현재 미구현, False)

    Raises:
        ValueError: 검색어가 비어있는 경우
        TokenNotFoundError: 저장된 토큰이 없는 경우
        RefreshFailedError: 토큰 갱신 실패 시
        SpotifyAPIError: API 호출 실패 시
    """
    # 빈 검색어 검증 (상태 검증)
    if not query or not query.strip():
        raise ValueError("[안내] 검색어를 입력해 주세요.")

    query = query.strip()
    token = _load_valid_token()

    # URL 인코딩은 requests가 자동 처리
    response = _api_request_with_retry(
        "GET",
        f"{SPOTIFY_API_BASE}/search",
        token,
        params={
            "q": query,
            "type": "track",
            "limit": min(limit, 50),
            "market": "KR",
        },
    )

    data = _safe_json_parse(response)

    if response.status_code != 200:
        raise SpotifyAPIError(
            status_code=response.status_code,
            error=data.get("error", {}).get("status", "unknown"),
            message=data.get("error", {}).get("message", "검색에 실패했습니다."),
        )

    tracks = data.get("tracks", {}).get("items", [])

    # popularity 기준 내림차순 정렬
    tracks_sorted = sorted(tracks, key=lambda t: t.get("popularity", 0), reverse=True)

    # 결과 변환
    from src.services.search_cache import SearchResultItem, save_search_cache

    results: list[SearchResultItem] = []
    for idx, track in enumerate(tracks_sorted, start=1):
        artists = [artist["name"] for artist in track.get("artists", [])]
        result = SearchResultItem(
            index=idx,
            track_id=track["id"],
            track_uri=track["uri"],
            name=track["name"],
            artists=artists,
            album=track.get("album", {}).get("name", "Unknown Album"),
            popularity=track.get("popularity", 0),
            is_liked=False,
        )
        results.append(result)

    # 캐시 저장
    save_search_cache(query, results)

    return results


def search_playlists(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """
    Spotify에서 플레이리스트를 검색하고 결과를 캐시에 저장합니다.

    Args:
        query: 검색어
        limit: 최대 결과 수 (기본값: 10, 최대: 50)

    Returns:
        list[dict]: 검색 결과 목록. 각 항목:
            - index: 순번 (1부터 시작)
            - playlist_id: Spotify 플레이리스트 ID
            - playlist_uri: Spotify URI (재생용)
            - name: 플레이리스트 이름
            - owner: 플레이리스트 소유자 이름
            - track_count: 수록 곡 수

    Raises:
        ValueError: 검색어가 비어있는 경우
        TokenNotFoundError: 저장된 토큰이 없는 경우
        RefreshFailedError: 토큰 갱신 실패 시
        SpotifyAPIError: API 호출 실패 시
    """
    if not query or not query.strip():
        raise ValueError("[안내] 검색어를 입력해 주세요.")

    query = query.strip()
    token = _load_valid_token()

    response = _api_request_with_retry(
        "GET",
        f"{SPOTIFY_API_BASE}/search",
        token,
        params={
            "q": query,
            "type": "playlist",
            "limit": min(limit, 50),
            "market": "KR",
        },
    )

    data = _safe_json_parse(response)

    if response.status_code != 200:
        raise SpotifyAPIError(
            status_code=response.status_code,
            error=data.get("error", {}).get("status", "unknown"),
            message=data.get("error", {}).get("message", "검색에 실패했습니다."),
        )

    items = data.get("playlists", {}).get("items", [])
    items = [p for p in items if p]  # null 항목 제거

    from src.services.search_cache import PlaylistSearchResultItem, save_playlist_cache

    results: list[PlaylistSearchResultItem] = []
    for idx, pl in enumerate(items, start=1):
        result = PlaylistSearchResultItem(
            index=idx,
            playlist_id=pl["id"],
            playlist_uri=pl["uri"],
            name=pl["name"],
            owner=(pl.get("owner") or {}).get("display_name") or "Unknown",
            track_count=(pl.get("tracks") or {}).get("total", 0),
        )
        results.append(result)

    save_playlist_cache(query, results)
    return results


def play_playlist(playlist_uri: str) -> None:
    """
    지정된 플레이리스트를 컨텍스트로 재생합니다.

    플레이리스트 전체를 context_uri로 재생하며, Spotify가 자동으로 이어서 재생합니다.
    Premium 계정이 필요합니다.

    Args:
        playlist_uri: Spotify 플레이리스트 URI (예: "spotify:playlist:xxx")

    Raises:
        ValueError: playlist_uri가 유효하지 않은 경우
        TokenNotFoundError: 저장된 토큰이 없는 경우
        RefreshFailedError: 토큰 갱신 실패 시
        NoActiveDeviceError: 활성 재생 디바이스가 없는 경우
        SpotifyAPIError: API 호출 실패 시
    """
    if not playlist_uri or not playlist_uri.startswith("spotify:playlist:"):
        raise ValueError("[오류] 유효하지 않은 플레이리스트 URI입니다.")

    state = get_playback_state()
    if state is None:
        raise NoActiveDeviceError()

    token = _load_valid_token()

    response = _api_request_with_retry(
        "PUT",
        f"{SPOTIFY_API_BASE}/me/player/play",
        token,
        json={"context_uri": playlist_uri},
    )

    if response.status_code == 204:
        return

    if response.status_code == 403:
        raise SpotifyAPIError(
            status_code=403,
            error="forbidden",
            message="이 기능은 Spotify Premium 계정에서만 작동합니다.",
        )

    if response.status_code == 404:
        raise NoActiveDeviceError()

    data = _safe_json_parse(response)
    raise SpotifyAPIError(
        status_code=response.status_code,
        error=data.get("error", {}).get("status", "unknown"),
        message=data.get("error", {}).get("message", "플레이리스트 재생에 실패했습니다."),
    )


def search_podcasts(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """
    Spotify에서 팟캐스트 에피소드를 검색하고 결과를 캐시에 저장합니다.

    Args:
        query: 검색어 (빈 문자열 불가)
        limit: 최대 결과 수 (기본값: 10, 최대: 50)

    Returns:
        list[dict]: 검색 결과 목록. 각 항목:
            - index: 순번 (1부터 시작)
            - episode_id: Spotify 에피소드 ID
            - episode_uri: Spotify URI (재생용)
            - name: 에피소드 제목
            - show_name: 팟캐스트 쇼 이름
            - duration_ms: 재생 시간 (밀리초)
            - release_date: 공개일 (YYYY-MM-DD)

    Raises:
        ValueError: 검색어가 비어있는 경우
        TokenNotFoundError: 저장된 토큰이 없는 경우
        RefreshFailedError: 토큰 갱신 실패 시
        SpotifyAPIError: API 호출 실패 시
    """
    if not query or not query.strip():
        raise ValueError("[안내] 검색어를 입력해 주세요.")

    query = query.strip()
    token = _load_valid_token()

    response = _api_request_with_retry(
        "GET",
        f"{SPOTIFY_API_BASE}/search",
        token,
        params={
            "q": query,
            "type": "show",
            "limit": min(limit, 50),
            "market": "KR",
        },
    )

    data = _safe_json_parse(response)

    if response.status_code != 200:
        raise SpotifyAPIError(
            status_code=response.status_code,
            error=data.get("error", {}).get("status", "unknown"),
            message=data.get("error", {}).get("message", "검색에 실패했습니다."),
        )

    shows = data.get("shows", {}).get("items", [])
    shows = [s for s in shows if s]

    if shows:
        debug_log(f"  Show fields: {list(shows[0].keys())}")
        debug_log(f"  publisher={shows[0].get('publisher')!r}  total_episodes={shows[0].get('total_episodes')!r}")

    from src.services.search_cache import PodcastSearchResultItem, save_podcast_cache

    results: list[PodcastSearchResultItem] = []
    for idx, show in enumerate(shows, start=1):
        result = PodcastSearchResultItem(
            index=idx,
            show_id=show["id"],
            show_uri=show["uri"],
            name=show["name"],
            publisher=show.get("publisher", ""),
            total_episodes=show.get("total_episodes", 0),
        )
        results.append(result)

    save_podcast_cache(query, results)
    return results


def search_episodes(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """
    Spotify에서 팟캐스트 에피소드를 검색합니다.

    market 파라미터를 생략하여 에피소드에 포함된 show 정보를 온전히 받습니다.

    Args:
        query: 검색어
        limit: 최대 결과 수 (기본값: 10)

    Returns:
        list[dict]: 각 항목 — index, episode_id, episode_uri, name,
                    show_name, duration_ms, release_date
    """
    if not query or not query.strip():
        raise ValueError("[안내] 검색어를 입력해 주세요.")

    query = query.strip()
    token = _load_valid_token()

    response = _api_request_with_retry(
        "GET",
        f"{SPOTIFY_API_BASE}/search",
        token,
        params={"q": query, "type": "episode", "limit": min(limit, 50)},
    )

    data = _safe_json_parse(response)

    if response.status_code != 200:
        raise SpotifyAPIError(
            status_code=response.status_code,
            error=data.get("error", {}).get("status", "unknown"),
            message=data.get("error", {}).get("message", "검색에 실패했습니다."),
        )

    items = [ep for ep in data.get("episodes", {}).get("items", []) if ep]

    # SimplifiedEpisodeObject에는 show 필드가 없으므로
    # market 없이 배치 fetch로 full EpisodeObject를 가져와 show 이름 보완
    show_names: dict[str, str] = {}
    if items:
        ids_param = ",".join(ep["id"] for ep in items)
        try:
            full_resp = _api_request_with_retry(
                "GET",
                f"{SPOTIFY_API_BASE}/episodes",
                token,
                params={"ids": ids_param},
            )
            debug_log(f"  /v1/episodes batch: {full_resp.status_code}")
            if full_resp.status_code == 200:
                for full_ep in (_safe_json_parse(full_resp).get("episodes") or []):
                    if full_ep:
                        show = full_ep.get("show") or {}
                        show_names[full_ep["id"]] = show.get("name", "")
            elif full_resp.status_code == 403:
                # 배치 엔드포인트 제한 → 개별 fetch 시도
                debug_log("  Batch 403, falling back to individual fetches...")
                for item_ep in items:
                    try:
                        ind_resp = _api_request_with_retry(
                            "GET",
                            f"{SPOTIFY_API_BASE}/episodes/{item_ep['id']}",
                            token,
                        )
                        debug_log(f"  /v1/episodes/{item_ep['id']}: {ind_resp.status_code}")
                        if ind_resp.status_code == 200:
                            full_ep = _safe_json_parse(ind_resp)
                            show = full_ep.get("show") or {}
                            show_names[item_ep["id"]] = show.get("name", "")
                    except Exception:
                        pass
        except Exception:
            pass

    from src.services.search_cache import EpisodeSearchResultItem, save_episode_cache

    results: list[EpisodeSearchResultItem] = []
    for idx, ep in enumerate(items, start=1):
        results.append(EpisodeSearchResultItem(
            index=idx,
            episode_id=ep["id"],
            episode_uri=ep["uri"],
            name=ep["name"],
            show_name=show_names.get(ep["id"], ""),
            duration_ms=ep.get("duration_ms", 0),
            release_date=ep.get("release_date", ""),
        ))

    save_episode_cache(query, results)
    return results


def play_show(show_uri: str) -> None:
    """
    지정된 팟캐스트 쇼를 컨텍스트로 재생합니다.

    쇼 전체를 context_uri로 재생하며 Spotify가 에피소드 순서대로 이어서 재생합니다.
    Premium 계정이 필요합니다.

    Args:
        show_uri: Spotify 쇼 URI (예: "spotify:show:xxxx")

    Raises:
        ValueError: show_uri가 유효하지 않은 경우
        TokenNotFoundError: 저장된 토큰이 없는 경우
        RefreshFailedError: 토큰 갱신 실패 시
        NoActiveDeviceError: 활성 재생 디바이스가 없는 경우
        SpotifyAPIError: API 호출 실패 시
    """
    if not show_uri or not show_uri.startswith("spotify:show:"):
        raise ValueError("[오류] 유효하지 않은 쇼 URI입니다.")

    state = get_playback_state()
    if state is None:
        raise NoActiveDeviceError()

    token = _load_valid_token()

    response = _api_request_with_retry(
        "PUT",
        f"{SPOTIFY_API_BASE}/me/player/play",
        token,
        json={"context_uri": show_uri},
    )

    if response.status_code == 204:
        return

    if response.status_code == 403:
        raise SpotifyAPIError(
            status_code=403,
            error="forbidden",
            message="이 기능은 Spotify Premium 계정에서만 작동합니다.",
        )

    if response.status_code == 404:
        raise NoActiveDeviceError()

    data = _safe_json_parse(response)
    raise SpotifyAPIError(
        status_code=response.status_code,
        error=data.get("error", {}).get("status", "unknown"),
        message=data.get("error", {}).get("message", "팟캐스트 재생에 실패했습니다."),
    )


def play_episode(episode_uri: str) -> None:
    """
    지정된 팟캐스트 에피소드를 재생합니다.

    현재 활성화된 디바이스에서 특정 에피소드를 즉시 재생합니다.
    Premium 계정이 필요합니다.

    Args:
        episode_uri: Spotify 에피소드 URI (예: "spotify:episode:xxxx")

    Raises:
        ValueError: episode_uri가 유효하지 않은 경우
        TokenNotFoundError: 저장된 토큰이 없는 경우
        RefreshFailedError: 토큰 갱신 실패 시
        NoActiveDeviceError: 활성 재생 디바이스가 없는 경우
        SpotifyAPIError: API 호출 실패 시
    """
    if not episode_uri or not episode_uri.startswith("spotify:episode:"):
        raise ValueError("[오류] 유효하지 않은 에피소드 URI입니다.")

    state = get_playback_state()
    if state is None:
        raise NoActiveDeviceError()

    token = _load_valid_token()

    response = _api_request_with_retry(
        "PUT",
        f"{SPOTIFY_API_BASE}/me/player/play",
        token,
        json={"uris": [episode_uri]},
    )

    if response.status_code == 204:
        return

    if response.status_code == 403:
        raise SpotifyAPIError(
            status_code=403,
            error="forbidden",
            message="이 기능은 Spotify Premium 계정에서만 작동합니다.",
        )

    if response.status_code == 404:
        raise NoActiveDeviceError()

    data = _safe_json_parse(response)
    raise SpotifyAPIError(
        status_code=response.status_code,
        error=data.get("error", {}).get("status", "unknown"),
        message=data.get("error", {}).get("message", "에피소드 재생에 실패했습니다."),
    )


if __name__ == "__main__":
    # 테스트용 실행
    print("[테스트] Spotify API 모듈 - Premium 계정 검증")

    try:
        user = verify_premium_account()
        print("[성공] Premium 계정 확인")
        print(f"       사용자: {user['display_name']} ({user['id']})")
        print(f"       계정 유형: {user['product']}")
        print(f"       국가: {user['country']}")
    except TokenNotFoundError:
        print("[안내] 토큰이 없습니다. 먼저 로그인하세요.")
        print("\n[테스트] curl로 직접 API 호출:")
        print("  curl -H 'Authorization: Bearer <ACCESS_TOKEN>' \\")
        print("       'https://api.spotify.com/v1/me'")
    except TokenExpiredError:
        print("[안내] 토큰이 만료되었습니다.")
    except NotPremiumError as e:
        print(str(e))
    except SpotifyAPIError as e:
        print(f"[오류] {e}")
