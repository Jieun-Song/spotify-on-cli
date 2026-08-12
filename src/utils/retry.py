"""
재시도 로직 유틸리티 모듈.

네트워크 오류, 5xx 서버 오류, 타임아웃 시 지수 백오프로 자동 재시도합니다.
"""

import time
from functools import wraps
from typing import Any, Callable, TypeVar, Union

import requests

from src.utils.console import debug_log, print_info

F = TypeVar("F", bound=Callable[..., Any])

# 재시도 대상 상태 코드
RETRYABLE_STATUS_CODES = {500, 502, 503, 504, 429}

# 기본 설정
DEFAULT_MAX_RETRIES = 3
DEFAULT_BASE_DELAY = 1.0  # 초
DEFAULT_MAX_DELAY = 30.0  # 초


def retry_on_error(
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
) -> Callable[[F], F]:
    """
    네트워크 오류 및 일시적 서버 오류 시 자동 재시도하는 데코레이터.

    지수 백오프(exponential backoff) 전략을 사용하여 재시도 간격을 점진적으로 증가시킵니다.
    429 Too Many Requests의 경우 Retry-After 헤더를 존중합니다.

    Args:
        max_retries: 최대 재시도 횟수 (기본값: 3)
        base_delay: 첫 번째 재시도 전 대기 시간(초) (기본값: 1.0)
        max_delay: 최대 대기 시간(초) (기본값: 30.0)

    Returns:
        Callable: 데코레이터 함수

    Example:
        @retry_on_error(max_retries=3)
        def api_call():
            response = requests.get(url)
            return response
    """

    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exception: Union[Exception, None] = None

            for attempt in range(max_retries + 1):
                try:
                    result = func(*args, **kwargs)

                    # requests.Response 객체인 경우 상태 코드 확인
                    if isinstance(result, requests.Response):
                        if result.status_code in RETRYABLE_STATUS_CODES:
                            if attempt < max_retries:
                                delay = _calculate_delay(
                                    attempt, base_delay, max_delay, result
                                )
                                _log_retry(
                                    func.__name__,
                                    attempt + 1,
                                    max_retries,
                                    delay,
                                    f"HTTP {result.status_code}",
                                )
                                time.sleep(delay)
                                continue

                    return result

                except requests.exceptions.Timeout as e:
                    last_exception = e
                    if attempt < max_retries:
                        delay = _calculate_delay(attempt, base_delay, max_delay)
                        _log_retry(
                            func.__name__,
                            attempt + 1,
                            max_retries,
                            delay,
                            "타임아웃",
                        )
                        time.sleep(delay)
                    else:
                        raise

                except requests.exceptions.ConnectionError as e:
                    last_exception = e
                    if attempt < max_retries:
                        delay = _calculate_delay(attempt, base_delay, max_delay)
                        _log_retry(
                            func.__name__,
                            attempt + 1,
                            max_retries,
                            delay,
                            "연결 오류",
                        )
                        time.sleep(delay)
                    else:
                        raise

            # 모든 재시도 실패
            if last_exception:
                raise last_exception

            return None

        return wrapper  # type: ignore[return-value]

    return decorator


def _calculate_delay(
    attempt: int,
    base_delay: float,
    max_delay: float,
    response: Union[requests.Response, None] = None,
) -> float:
    """
    재시도 대기 시간을 계산합니다.

    지수 백오프를 사용하며, 429 응답의 경우 Retry-After 헤더를 존중합니다.

    Args:
        attempt: 현재 시도 횟수 (0부터 시작)
        base_delay: 기본 대기 시간
        max_delay: 최대 대기 시간
        response: HTTP 응답 객체 (선택적)

    Returns:
        float: 대기 시간 (초)
    """
    # 429 응답의 Retry-After 헤더 확인
    if response is not None and response.status_code == 429:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), max_delay)
            except ValueError:
                pass

    # 지수 백오프: base_delay * 2^attempt
    delay = base_delay * (2**attempt)
    return min(delay, max_delay)


def _log_retry(
    func_name: str,
    attempt: int,
    max_retries: int,
    delay: float,
    reason: str,
) -> None:
    """
    재시도 로그를 기록합니다.

    Args:
        func_name: 함수 이름
        attempt: 현재 시도 횟수
        max_retries: 최대 재시도 횟수
        delay: 대기 시간
        reason: 재시도 사유
    """
    message = f"[재시도] {reason} - {attempt}/{max_retries}회 재시도 중... ({delay:.1f}초 후)"
    print_info(message)
    debug_log(f"Retry {func_name}: attempt={attempt}, reason={reason}, delay={delay}")
