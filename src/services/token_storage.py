"""
Spotify CLI 토큰 저장 모듈.

credentials.json 파일에 토큰을 안전하게 저장하고 로드합니다.
- Unix/macOS: 파일 권한 600 (소유자만 읽기/쓰기)
- Windows: ACL로 현재 사용자만 접근 허용
- Race condition 방지: 파일 잠금 + atomic write
"""

import contextlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Generator, Optional, TypedDict

from src.config import get_config_dir

# Windows에서는 fcntl 대신 msvcrt 사용
if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


class StoredToken(TypedDict):
    """저장된 토큰 타입."""

    access_token: str
    token_type: str
    scope: str
    expires_in: int
    refresh_token: str
    saved_at: float  # Unix timestamp


CREDENTIALS_FILE = "credentials.json"
LOCK_FILE = "credentials.lock"
LOCK_TIMEOUT_SECONDS = 10


def _get_credentials_path() -> Path:
    """
    credentials.json 파일 경로를 반환합니다.

    Returns:
        Path: credentials.json 전체 경로
    """
    return get_config_dir() / CREDENTIALS_FILE


def _get_lock_path() -> Path:
    """
    잠금 파일 경로를 반환합니다.

    Returns:
        Path: credentials.lock 전체 경로
    """
    return get_config_dir() / LOCK_FILE


@contextlib.contextmanager
def token_lock() -> Generator[None, None, None]:
    """
    토큰 파일에 대한 배타적 잠금을 획득하는 컨텍스트 매니저.

    Race condition을 방지하기 위해 토큰 읽기/쓰기 시 사용합니다.
    Unix에서는 fcntl.flock(), Windows에서는 msvcrt.locking()을 사용합니다.

    Yields:
        None

    Raises:
        TimeoutError: 잠금 획득 시간 초과 시

    Example:
        with token_lock():
            token = load_token()
            # ... 토큰 갱신 ...
            save_token(...)
    """
    config_dir = get_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)

    lock_path = _get_lock_path()
    lock_file = open(lock_path, "w")

    try:
        start_time = time.time()

        while True:
            try:
                if sys.platform == "win32":
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break  # 잠금 획득 성공
            except (IOError, OSError):
                if time.time() - start_time > LOCK_TIMEOUT_SECONDS:
                    raise TimeoutError(
                        f"[오류] 토큰 파일 잠금 획득 시간 초과 ({LOCK_TIMEOUT_SECONDS}초)"
                    )
                time.sleep(0.1)  # 100ms 대기 후 재시도

        yield

    finally:
        # 잠금 해제
        try:
            if sys.platform == "win32":
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except (IOError, OSError):
            pass
        lock_file.close()


def _atomic_write(path: Path, data: str) -> None:
    """
    파일을 atomic하게 씁니다 (임시 파일 → rename).

    Args:
        path: 대상 파일 경로
        data: 쓸 데이터

    Note:
        같은 파일시스템 내에서 rename은 atomic 연산입니다.
        쓰기 중 다른 프로세스가 읽어도 불완전한 데이터를 읽지 않습니다.
    """
    dir_path = path.parent
    fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())  # 디스크에 확실히 기록

        # atomic rename
        os.replace(tmp_path, path)
    except Exception:
        # 실패 시 임시 파일 정리
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _set_unix_permissions(path: Path) -> None:
    """
    Unix/macOS에서 파일 권한을 600으로 설정합니다.

    Args:
        path: 권한을 설정할 파일 경로
    """
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0o600


def _set_windows_acl(path: Path) -> None:
    """
    Windows에서 현재 사용자만 파일에 접근할 수 있도록 ACL을 설정합니다.

    Args:
        path: ACL을 설정할 파일 경로

    Note:
        icacls 명령어를 사용하여 상속을 제거하고 현재 사용자에게만 전체 권한 부여
    """
    username = os.environ.get("USERNAME", "")
    if not username:
        return

    # 기존 ACL 제거 후 현재 사용자에게만 전체 권한 부여
    subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", f"{username}:F"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def _set_file_permissions(path: Path) -> None:
    """
    OS에 따라 파일 권한을 설정합니다.

    Args:
        path: 권한을 설정할 파일 경로

    Note:
        - Unix/macOS: chmod 600
        - Windows: icacls로 현재 사용자만 접근 허용
    """
    if sys.platform == "win32":
        _set_windows_acl(path)
    else:
        _set_unix_permissions(path)


def save_token(
    access_token: str,
    token_type: str,
    scope: str,
    expires_in: int,
    refresh_token: str,
) -> Path:
    """
    토큰을 credentials.json 파일에 저장합니다.

    Atomic write를 사용하여 race condition을 방지합니다.
    임시 파일에 먼저 쓴 후 rename하여 불완전한 파일이 읽히지 않도록 합니다.

    Args:
        access_token: API 호출용 액세스 토큰
        token_type: 토큰 타입 (Bearer)
        scope: 승인된 권한 범위
        expires_in: 토큰 만료 시간 (초)
        refresh_token: 토큰 갱신용 리프레시 토큰

    Returns:
        Path: 저장된 credentials.json 파일 경로

    Raises:
        OSError: 디렉토리 생성 또는 파일 쓰기 실패 시

    Note:
        여러 프로세스가 동시에 토큰을 갱신할 때는 token_lock()과 함께 사용하세요.
    """
    config_dir = get_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)

    credentials_path = _get_credentials_path()

    token_data: StoredToken = {
        "access_token": access_token,
        "token_type": token_type,
        "scope": scope,
        "expires_in": expires_in,
        "refresh_token": refresh_token,
        "saved_at": time.time(),
    }

    # Atomic write: 임시 파일 → rename
    json_data = json.dumps(token_data, indent=2)
    _atomic_write(credentials_path, json_data)

    # 파일 권한 설정 (소유자만 읽기/쓰기)
    _set_file_permissions(credentials_path)

    return credentials_path


def load_token() -> Optional[StoredToken]:
    """
    credentials.json에서 토큰을 로드합니다.

    Returns:
        Optional[StoredToken]: 저장된 토큰 또는 None (파일이 없거나 손상된 경우)
    """
    credentials_path = _get_credentials_path()

    if not credentials_path.exists():
        return None

    try:
        with open(credentials_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 필수 필드 검증
        required_fields = [
            "access_token",
            "token_type",
            "scope",
            "expires_in",
            "refresh_token",
            "saved_at",
        ]
        if not all(field in data for field in required_fields):
            return None

        return StoredToken(
            access_token=data["access_token"],
            token_type=data["token_type"],
            scope=data["scope"],
            expires_in=data["expires_in"],
            refresh_token=data["refresh_token"],
            saved_at=data["saved_at"],
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def delete_token() -> bool:
    """
    credentials.json 파일을 삭제합니다.

    Returns:
        bool: 삭제 성공 여부 (파일이 없어도 True 반환)
    """
    credentials_path = _get_credentials_path()

    if not credentials_path.exists():
        return True

    try:
        credentials_path.unlink()
        return True
    except OSError:
        return False


def is_token_expired(token: StoredToken, buffer_seconds: int = 60) -> bool:
    """
    토큰이 만료되었는지 확인합니다.

    Args:
        token: 저장된 토큰
        buffer_seconds: 만료 전 버퍼 시간 (기본값: 60초)

    Returns:
        bool: 토큰이 만료되었거나 곧 만료될 예정이면 True
    """
    expires_at = token["saved_at"] + token["expires_in"]
    return time.time() >= (expires_at - buffer_seconds)


if __name__ == "__main__":
    # 테스트용 실행
    print("[테스트] 토큰 저장 모듈")

    # 테스트 토큰 저장
    path = save_token(
        access_token="test_access_token_12345",
        token_type="Bearer",
        scope="user-read-playback-state user-modify-playback-state",
        expires_in=3600,
        refresh_token="test_refresh_token_67890",
    )
    print(f"[성공] 토큰 저장: {path}")

    # 파일 권한 확인 (Unix)
    if sys.platform != "win32":
        mode = oct(os.stat(path).st_mode)[-3:]
        print(f"[확인] 파일 권한: {mode} (600 예상)")

    # 토큰 로드
    loaded = load_token()
    if loaded:
        print(f"[성공] 토큰 로드: access_token={loaded['access_token'][:20]}...")
        print(f"[확인] 만료 여부: {is_token_expired(loaded)}")

    # curl 테스트 명령어 출력
    print("\n[테스트] curl로 저장된 파일 확인:")
    print(f"  cat '{path}'")
    print(f"  ls -la '{path}'  # Unix: 권한 600 확인")
