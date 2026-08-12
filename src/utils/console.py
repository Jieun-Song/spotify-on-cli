"""
콘솔 출력 유틸리티 모듈.

Rich 라이브러리를 사용한 색상 출력, 스피너, 디버그 로깅을 제공합니다.
"""

import logging
import sys
from contextlib import contextmanager
from datetime import datetime
from typing import Generator, Optional

from rich.console import Console
from rich.status import Status

from src.config import get_config_dir

# 전역 콘솔 인스턴스
console = Console()

# 디버그 모드 플래그
_debug_mode: bool = False

# 디버그 로거
_debug_logger: Optional[logging.Logger] = None


def is_debug_mode() -> bool:
    """
    현재 디버그 모드 상태를 반환합니다.

    Returns:
        bool: 디버그 모드 활성화 여부
    """
    return _debug_mode


def set_debug_mode(enabled: bool) -> None:
    """
    디버그 모드를 설정합니다.

    디버그 모드가 활성화되면 HTTP 요청/응답 상세 로그가
    콘솔과 debug.log 파일에 기록됩니다.

    Args:
        enabled: True면 디버그 모드 활성화
    """
    global _debug_mode, _debug_logger
    _debug_mode = enabled

    if enabled and _debug_logger is None:
        _debug_logger = _setup_debug_logger()


def _setup_debug_logger() -> logging.Logger:
    """
    디버그 로거를 설정합니다.

    설정 디렉토리에 debug.log 파일을 생성하고
    로그를 기록할 로거를 반환합니다.

    Returns:
        logging.Logger: 설정된 디버그 로거
    """
    config_dir = get_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)

    log_path = config_dir / "debug.log"

    logger = logging.getLogger("spotify-cli-debug")
    logger.setLevel(logging.DEBUG)

    # 기존 핸들러 제거
    logger.handlers.clear()

    # 파일 핸들러
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_format = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_format)
    logger.addHandler(file_handler)

    # 콘솔 핸들러 (stderr)
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(file_format)
    logger.addHandler(console_handler)

    logger.debug(f"=== Debug session started at {datetime.now().isoformat()} ===")

    return logger


def debug_log(message: str) -> None:
    """
    디버그 메시지를 로그에 기록합니다.

    디버그 모드가 비활성화되어 있으면 아무 동작도 하지 않습니다.

    Args:
        message: 로그에 기록할 메시지
    """
    if _debug_mode and _debug_logger:
        _debug_logger.debug(message)


def print_success(message: str) -> None:
    """
    성공 메시지를 초록색으로 출력합니다.

    Args:
        message: 출력할 메시지
    """
    console.print(f"[green]{message}[/green]")


def print_error(message: str) -> None:
    """
    오류 메시지를 빨간색으로 출력합니다.

    귀여운 이모티콘과 함께 표시됩니다.

    Args:
        message: 출력할 메시지
    """
    console.print(f"[red](｡•́︿•̀｡) {message}[/red]")


def print_info(message: str) -> None:
    """
    안내 메시지를 노란색으로 출력합니다.

    Args:
        message: 출력할 메시지
    """
    console.print(f"[yellow]♪ {message}[/yellow]")


def print_warning(message: str) -> None:
    """
    경고 메시지를 노란색 굵은 글씨로 출력합니다.

    Args:
        message: 출력할 메시지
    """
    console.print(f"[bold yellow]{message}[/bold yellow]")


@contextmanager
def spinner(message: str) -> Generator[Status, None, None]:
    """
    로딩 스피너를 표시하는 컨텍스트 매니저.

    API 호출 등 시간이 걸리는 작업 중 스피너를 표시합니다.

    Args:
        message: 스피너와 함께 표시할 메시지

    Yields:
        Status: Rich Status 객체

    Example:
        with spinner("검색 중..."):
            results = search_tracks(query)
    """
    with console.status(f"[cyan]{message}[/cyan]", spinner="dots") as status:
        yield status
