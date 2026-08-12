"""
Spotify OAuth 2.0 PKCE 인증 모듈.

로컬 임시 HTTPS 서버를 구동하고 브라우저를 열어 사용자 인증을 시작합니다.
콜백을 수신하고 authorization code를 파싱합니다.
토큰 교환 및 저장은 이 모듈의 범위 밖입니다.
"""

import base64
import datetime
import hashlib
import ipaddress
import secrets
import socket
import ssl
import tempfile
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Optional, TypedDict
from urllib.parse import urlencode, parse_qs, urlparse

import requests

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from src.config import (
    get_client_id,
    get_redirect_uri,
    OAUTH_SCOPES,
    DEFAULT_PORT,
    MAX_PORT_ATTEMPTS,
    AUTH_TIMEOUT_SECONDS,
    HTTP_TIMEOUT_SECONDS,
)


def generate_pkce_pair() -> tuple[str, str]:
    """
    PKCE용 code_verifier와 code_challenge를 생성합니다.

    Returns:
        tuple[str, str]: (code_verifier, code_challenge)
            - code_verifier: 43-128자의 랜덤 문자열
            - code_challenge: code_verifier의 SHA256 해시를 Base64 URL-safe 인코딩한 값
    """
    code_verifier = secrets.token_urlsafe(64)[:128]

    sha256_hash = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = (
        base64.urlsafe_b64encode(sha256_hash).decode("ascii").rstrip("=")
    )

    return code_verifier, code_challenge


def generate_state() -> str:
    """
    CSRF 방어용 state 파라미터를 생성합니다.

    Returns:
        str: 32바이트의 URL-safe 랜덤 문자열
    """
    return secrets.token_urlsafe(32)


def find_available_port(start_port: int = DEFAULT_PORT) -> int:
    """
    사용 가능한 포트를 찾습니다.

    Args:
        start_port: 시작 포트 번호 (기본값: 8080)

    Returns:
        int: 사용 가능한 포트 번호

    Raises:
        RuntimeError: MAX_PORT_ATTEMPTS 내에 사용 가능한 포트를 찾지 못한 경우
    """
    for i in range(MAX_PORT_ATTEMPTS):
        port = start_port + i
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue

    raise RuntimeError(
        f"[오류] 포트 {start_port}-{start_port + MAX_PORT_ATTEMPTS - 1} 범위에서 "
        "사용 가능한 포트를 찾을 수 없습니다. 다른 프로세스가 포트를 사용 중인지 확인해 주세요."
    )


def build_authorization_url(port: int, code_challenge: str, state: str) -> str:
    """
    Spotify 인증 URL을 생성합니다.

    Args:
        port: 로컬 콜백 서버 포트 번호
        code_challenge: PKCE code_challenge 값
        state: CSRF 방어용 state 파라미터

    Returns:
        str: Spotify 인증 페이지 URL
    """
    client_id = get_client_id()
    redirect_uri = get_redirect_uri(port)

    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": " ".join(OAUTH_SCOPES),
        "code_challenge_method": "S256",
        "code_challenge": code_challenge,
        "state": state,
        "show_dialog": "true",  # 항상 권한 승인 화면 표시
    }

    return f"https://accounts.spotify.com/authorize?{urlencode(params)}"


def open_browser(url: str) -> bool:
    """
    기본 브라우저에서 URL을 엽니다.

    Args:
        url: 열 URL

    Returns:
        bool: 브라우저 열기 성공 여부
    """
    try:
        return webbrowser.open(url)
    except Exception:
        return False


class CallbackHandler(BaseHTTPRequestHandler):
    """
    OAuth 콜백을 처리하는 HTTP 요청 핸들러.

    이 핸들러는 /callback 경로로 들어오는 GET 요청을 처리합니다.
    state 파라미터를 검증하여 CSRF 공격을 방어합니다.
    토큰 교환은 이 클래스의 범위 밖입니다.

    Class Attributes:
        expected_state: 검증할 state 값 (인증 시작 전 설정 필요)
        auth_code: 수신된 authorization code
        error: 오류 메시지 (인증 실패 시)
        auth_received: 인증 처리 완료 플래그 (코드 수신 또는 에러 발생)
    """

    expected_state: Optional[str] = None
    auth_code: Optional[str] = None
    error: Optional[str] = None
    auth_received: bool = False

    @classmethod
    def reset(cls) -> None:
        """핸들러 상태를 초기화합니다."""
        cls.expected_state = None
        cls.auth_code = None
        cls.error = None
        cls.auth_received = False

    def _parse_callback_params(self) -> dict[str, str]:
        """
        콜백 URL에서 쿼리 파라미터를 파싱합니다.

        Returns:
            dict[str, str]: 파라미터 이름과 값의 딕셔너리
        """
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        # parse_qs는 리스트를 반환하므로 첫 번째 값만 추출
        return {k: v[0] for k, v in params.items()}

    def _send_html_response(self, status_code: int, title: str, message: str) -> None:
        """
        HTML 응답을 전송합니다.

        Args:
            status_code: HTTP 상태 코드
            title: 페이지 제목
            message: 본문 메시지
        """
        self.send_response(status_code)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()

        response_html = f"""
        <!DOCTYPE html>
        <html>
        <head><title>Spotify CLI - {title}</title></head>
        <body style="font-family: sans-serif; text-align: center; padding-top: 50px;">
            <h1>{title}</h1>
            <p>{message}</p>
        </body>
        </html>
        """
        self.wfile.write(response_html.encode("utf-8"))

    def do_GET(self) -> None:
        """GET 요청을 처리합니다."""
        # /callback 경로가 아닌 요청은 무시 (favicon.ico 등)
        if not self.path.startswith("/callback"):
            self.send_response(404)
            self.end_headers()
            # auth_received를 설정하지 않음 - 서버는 계속 대기
            return

        params = self._parse_callback_params()

        # state 파라미터 검증 (CSRF 방어)
        received_state = params.get("state")
        if CallbackHandler.expected_state is None:
            CallbackHandler.error = "state_not_configured"
            CallbackHandler.auth_received = True
            self._send_html_response(
                500, "서버 오류", "state 파라미터가 설정되지 않았습니다."
            )
            return

        if received_state != CallbackHandler.expected_state:
            CallbackHandler.error = "state_mismatch"
            CallbackHandler.auth_received = True
            self._send_html_response(
                400, "인증 실패", "state 파라미터가 일치하지 않습니다. (CSRF 공격 의심)"
            )
            return

        # error 파라미터 확인 (사용자가 권한 거부 등)
        if "error" in params:
            CallbackHandler.error = params["error"]
            CallbackHandler.auth_received = True
            error_desc = params.get("error_description", "알 수 없는 오류")
            self._send_html_response(400, "인증 실패", f"오류: {error_desc}")
            return

        # authorization code 추출
        if "code" in params:
            CallbackHandler.auth_code = params["code"]
            CallbackHandler.auth_received = True
            self._send_html_response(
                200, "인증 완료!", "이 창을 닫고 터미널로 돌아가세요."
            )
        else:
            CallbackHandler.error = "code_missing"
            CallbackHandler.auth_received = True
            self._send_html_response(
                400, "인증 실패", "authorization code가 없습니다."
            )

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """서버 로그를 숨깁니다."""
        pass


def create_self_signed_cert() -> tuple[str, str]:
    """
    Python cryptography 라이브러리를 사용하여 자체 서명 SSL 인증서를 동적 생성합니다.

    Returns:
        tuple[str, str]: (cert_file_path, key_file_path)

    Note:
        생성된 인증서는 127.0.0.1에 대해 1일간 유효합니다.
        브라우저에서 '안전하지 않음' 경고가 표시되지만, '고급 -> 이동'으로 진행 가능합니다.
    """
    # RSA 2048비트 개인키 생성
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend()
    )

    # 인증서 주체/발급자 정보
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1"),
    ])

    # 자체 서명 인증서 생성 (1일 유효)
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
            ]),
            critical=False,
        )
        .sign(private_key, hashes.SHA256(), default_backend())
    )

    # 임시 파일에 저장
    cert_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pem", mode="wb")
    key_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pem", mode="wb")

    cert_file.write(cert.public_bytes(serialization.Encoding.PEM))
    cert_file.close()

    key_file.write(private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption()
    ))
    key_file.close()

    return cert_file.name, key_file.name


def start_callback_server(port: int) -> HTTPServer:
    """
    OAuth 콜백을 수신할 로컬 HTTPS 서버를 시작합니다.

    Args:
        port: 서버 포트 번호

    Returns:
        HTTPServer: 시작된 HTTPS 서버 인스턴스

    Note:
        타임아웃은 wait_for_callback에서 스레딩 기반으로 처리됩니다.
        server.timeout은 사용하지 않습니다.
    """
    server = HTTPServer(("127.0.0.1", port), CallbackHandler)
    # 타임아웃은 스레딩 기반으로 처리하므로 소켓 타임아웃은 설정하지 않음
    # 대신 handle_request()가 블로킹되지 않도록 짧은 폴링 간격 설정
    server.timeout = 1.0  # 1초마다 루프 체크

    # SSL 설정
    cert_file, key_file = create_self_signed_cert()
    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_context.load_cert_chain(cert_file, key_file)
    server.socket = ssl_context.wrap_socket(server.socket, server_side=True)

    return server


def start_auth_flow() -> tuple[HTTPServer, int, str, str, str]:
    """
    OAuth 인증 플로우를 시작합니다.

    1. 핸들러 상태를 초기화합니다.
    2. 사용 가능한 포트를 찾습니다.
    3. PKCE code_verifier/code_challenge를 생성합니다.
    4. CSRF 방어용 state를 생성합니다.
    5. 로컬 콜백 서버를 시작합니다.
    6. 브라우저에서 Spotify 인증 페이지를 엽니다.

    Returns:
        tuple[HTTPServer, int, str, str, str]: (server, port, code_verifier, state, auth_url)
            - server: 시작된 HTTPS 서버
            - port: 서버 포트 번호
            - code_verifier: PKCE code_verifier (토큰 교환 시 필요)
            - state: CSRF 방어용 state 값
            - auth_url: Spotify 인증 URL

    Raises:
        RuntimeError: 포트를 찾을 수 없거나 서버 시작 실패 시
        ValueError: SPOTIFY_CLIENT_ID가 설정되지 않은 경우
    """
    # 1. 핸들러 상태 초기화
    CallbackHandler.reset()

    # 2. 사용 가능한 포트 찾기
    port = find_available_port()
    print(f"[안내] 로컬 인증 서버를 포트 {port}에서 시작합니다...")

    # 3. PKCE 생성
    code_verifier, code_challenge = generate_pkce_pair()

    # 4. state 생성 및 설정
    state = generate_state()
    CallbackHandler.expected_state = state

    # 5. 인증 URL 생성
    auth_url = build_authorization_url(port, code_challenge, state)

    # 6. 콜백 서버 시작
    server = start_callback_server(port)

    # 7. 브라우저 열기
    redirect_uri = get_redirect_uri(port)
    browser_opened = open_browser(auth_url)

    if browser_opened:
        print("[안내] 브라우저에서 Spotify 로그인 페이지가 열렸습니다.")
    else:
        print("[안내] 브라우저를 자동으로 열 수 없습니다.")
        print("[안내] 아래 URL을 복사하여 브라우저에 붙여넣기 해주세요:")
        print(f"\n{auth_url}\n")

    print(f"[안내] 콜백 서버: {redirect_uri}")
    print(f"[안내] 인증 대기 중... (최대 {AUTH_TIMEOUT_SECONDS // 60}분)")
    print("[안내] ⚠️  브라우저에서 '안전하지 않음' 경고가 표시되면:")
    print("       Chrome: '고급' → '127.0.0.1(안전하지 않음)으로 이동'")
    print("       Safari: '세부사항 보기' → '이 웹 사이트 방문'")

    return server, port, code_verifier, state, auth_url


def wait_for_callback(server: HTTPServer) -> tuple[Optional[str], Optional[str]]:
    """
    콜백 서버에서 요청을 대기하고 결과를 반환합니다.

    스레딩 기반 타임아웃을 사용하여 정확히 AUTH_TIMEOUT_SECONDS(180초) 후에
    서버를 종료합니다. /favicon.ico 등 무관한 요청은 무시하고 계속 대기합니다.

    Args:
        server: 콜백을 수신할 HTTPServer 인스턴스

    Returns:
        tuple[Optional[str], Optional[str]]: (auth_code, error)
            - auth_code: 성공 시 authorization code, 실패 시 None
            - error: 실패 시 오류 코드, 성공 시 None
                - "timeout": 타임아웃 발생 시
    """
    timeout_occurred = threading.Event()

    def timeout_handler() -> None:
        """180초 후 서버를 종료하는 타이머 핸들러."""
        timeout_occurred.set()

    # 타임아웃 타이머 시작
    timer = threading.Timer(AUTH_TIMEOUT_SECONDS, timeout_handler)
    timer.daemon = True
    timer.start()

    try:
        # auth_received가 True가 되거나 타임아웃이 발생할 때까지 루프
        while not CallbackHandler.auth_received and not timeout_occurred.is_set():
            # server.timeout=1.0이므로 1초마다 루프 체크
            server.handle_request()

        if timeout_occurred.is_set() and not CallbackHandler.auth_received:
            # 타임아웃 발생
            return None, "timeout"

        return CallbackHandler.auth_code, CallbackHandler.error
    finally:
        # 타이머 취소 (이미 실행됐으면 무시됨)
        timer.cancel()
        # 서버 소켓 정리
        try:
            server.server_close()
        except Exception:
            pass


class TokenResponse(TypedDict):
    """Spotify 토큰 응답 타입."""

    access_token: str
    token_type: str
    scope: str
    expires_in: int
    refresh_token: str


class TokenError(Exception):
    """토큰 교환 실패 시 발생하는 예외."""

    def __init__(self, error: str, description: str) -> None:
        self.error = error
        self.description = description
        super().__init__(f"{error}: {description}")


def exchange_code_for_token(
    auth_code: str,
    code_verifier: str,
    port: int,
) -> TokenResponse:
    """
    Authorization code를 access token으로 교환합니다.

    PKCE 플로우의 마지막 단계로, Spotify Token 엔드포인트에
    code_verifier와 함께 POST 요청을 보내 토큰을 받습니다.

    Args:
        auth_code: 콜백에서 수신한 authorization code
        code_verifier: 인증 시작 시 생성한 PKCE code_verifier
        port: 콜백 서버 포트 번호 (redirect_uri 생성용)

    Returns:
        TokenResponse: 토큰 응답 딕셔너리
            - access_token: API 호출용 액세스 토큰
            - token_type: 토큰 타입 (Bearer)
            - scope: 승인된 권한 범위
            - expires_in: 토큰 만료 시간 (초)
            - refresh_token: 토큰 갱신용 리프레시 토큰

    Raises:
        TokenError: 토큰 교환 실패 시 (invalid_grant, invalid_client 등)
        requests.RequestException: 네트워크 오류 시
    """
    token_url = "https://accounts.spotify.com/api/token"

    payload = {
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": get_redirect_uri(port),
        "client_id": get_client_id(),
        "code_verifier": code_verifier,
    }

    response = requests.post(
        token_url,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=HTTP_TIMEOUT_SECONDS,
    )

    data = response.json()

    if response.status_code != 200:
        raise TokenError(
            error=data.get("error", "unknown_error"),
            description=data.get("error_description", "토큰 교환에 실패했습니다."),
        )

    return TokenResponse(
        access_token=data["access_token"],
        token_type=data["token_type"],
        scope=data["scope"],
        expires_in=data["expires_in"],
        refresh_token=data["refresh_token"],
    )


def refresh_access_token(refresh_token: str) -> TokenResponse:
    """
    Refresh token을 사용하여 새 access token을 발급받습니다.

    Args:
        refresh_token: 저장된 refresh token

    Returns:
        TokenResponse: 새 토큰 응답 딕셔너리
            - access_token: 새 API 호출용 액세스 토큰
            - token_type: 토큰 타입 (Bearer)
            - scope: 승인된 권한 범위
            - expires_in: 토큰 만료 시간 (초)
            - refresh_token: 새 리프레시 토큰 (또는 기존 토큰)

    Raises:
        TokenError: 토큰 갱신 실패 시 (invalid_grant 등)
        requests.RequestException: 네트워크 오류 시
    """
    token_url = "https://accounts.spotify.com/api/token"

    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": get_client_id(),
    }

    response = requests.post(
        token_url,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=HTTP_TIMEOUT_SECONDS,
    )

    data = response.json()

    if response.status_code != 200:
        raise TokenError(
            error=data.get("error", "unknown_error"),
            description=data.get("error_description", "토큰 갱신에 실패했습니다."),
        )

    # Spotify는 refresh_token을 항상 반환하지 않을 수 있음
    return TokenResponse(
        access_token=data["access_token"],
        token_type=data["token_type"],
        scope=data["scope"],
        expires_in=data["expires_in"],
        refresh_token=data.get("refresh_token", refresh_token),
    )


if __name__ == "__main__":
    # 테스트용 실행
    import os
    os.environ.setdefault("SPOTIFY_CLIENT_ID", "test_client_id")

    try:
        server, port, code_verifier, state, auth_url = start_auth_flow()
        print(f"\n[DEBUG] HTTPS Server: https://127.0.0.1:{port}/callback")
        print(f"[DEBUG] Code Verifier: {code_verifier[:20]}...")
        print(f"[DEBUG] State: {state[:20]}...")
        print("\n[안내] Ctrl+C로 서버를 종료하세요.")
        print("\n[테스트] curl로 콜백 시뮬레이션 (-k 옵션: SSL 인증서 검증 무시):")
        print(f"  curl -k 'https://127.0.0.1:{port}/callback?code=test_auth_code&state={state}'")

        auth_code, error = wait_for_callback(server)

        if auth_code:
            print(f"\n[성공] Authorization Code 수신: {auth_code[:20] if len(auth_code) > 20 else auth_code}...")
        elif error == "timeout":
            print("\n[오류] 인증 시간이 초과되었습니다.")
        elif error:
            print(f"\n[오류] 인증 실패: {error}")

    except KeyboardInterrupt:
        print("\n[안내] 작업이 취소되었습니다.")
    except Exception as e:
        print(f"\n[오류] {e}")
