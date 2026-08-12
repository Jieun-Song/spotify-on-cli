# CLAUDE.md

이 파일은 Claude Code (claude.ai/code)가 이 저장소에서 작업할 때 참고하는 가이드입니다.

## 빌드 및 개발 명령어

```bash
# 의존성 및 패키지 개발 모드 설치 (Entry Point 자동 등록)
pip install -r requirements.txt
pip install -e .

# CLI 실행 (전역 명령어 또는 패키지 경로)
soc --help
python src/main.py --help

# 전체 테스트 실행
pytest

# 단일 테스트 파일 실행
pytest tests/test_player.py -v

# 린트 및 타입 검사
ruff check .
mypy .
```

---

## 필수 명세 항목 (작성 가이드)

### 1. 프로젝트 개요

```
Spotify Premium 요금제 사용자가 터미널 환경에서 음악 재생, 제어, 검색 및 라이브러리 관리를 수행할 수 있도록 지원하는 Python 기반 초경량 CLI 애플리케이션입니다.
```

### 2. 기술 스택
> 사용하는 주요 기술을 명시하세요.

| 영역 | 기술 |
|------|------|
| 언어 | Python 3.10+ |
| CLI 프레임워크 | Typer |
| HTTP 클라이언트 | requests |
| 터미널 UI(TUI) | rich(표, 스피너, 색상 출력용) |
| 테스트 | pytest |
| 린터 | ruff / mypy |

### 3. 핵심 디렉토리 구조
> 중요한 폴더와 그 역할을 명시하세요. (모든 파일을 나열하지 말 것)

spotify-cli/
├── src/
│   ├── __init__.py
│   ├── main.py          # CLI 애플리케이션의 메인 진입점 (Typer 앱 초기화)
│   ├── commands/        # CLI 명령어 정의 및 사용자 입력 처리 (UI 레이어)
│   ├── services/        # 외부 API 통신 및 핵심 비즈니스 로직
│   │   ├── __init__.py
│   │   └── spotify.py   # Spotify API 호출 전담 클라이언트 (GET, PUT, POST 처리)
│   ├── config.py        # 환경 변수 및 설정 관리 (토큰 저장 경로 등)
│   └── utils/           # 공통 유틸리티 함수
├── tests/               # 테스트 코드 폴더
├── CLAUDE.md
├── pyproject.toml       # ruff, mypy 설정 파일
└── requirements.txt


### 4. 데이터 흐름 및 아키텍처

```
[사용자 CLI 입력] → [commands/ 명령어 파싱] → [services/ Spotify API 호출] → [인증/JSON 응답 수신] → [터미널 표/텍스트 출력]
```

### 5. 핵심 도메인 모델

```
User Session: Access Token, Refresh Token, Premium 여부(Boolean), 유저 디스플레이 네임.

Track: 곡 ID, 곡 이름, 아티스트 이름, 앨범 이름, Spotify URI(재생 식별자), 인기 점수(0~100), 좋아요 여부(Boolean), 현재 재생 위치(ms).
```

### 6. API 엔드포인트 규칙 및 OAuth Scope
OAuth Scopes: user-read-playback-state, user-modify-playback-state, user-read-currently-playing, user-library-modify, user-library-read

OAuth 2.0 (PKCE flow): 로컬 임시 서버를 구동하여 웹 브라우저 인증 후 토큰을 지정된 경로에 평문(JSON) 저장합니다. 파일 생성 시 보안을 위해 소유자 전용 권한으로 제한합니다.

```
현재 재생 정보 확인: GET /v1/me/player/currently-playing

재생/일시정지 제어: PUT /v1/me/player/pause, PUT /v1/me/player/play

다음/이전 곡 재생: POST /v1/me/player/next, POST /v1/me/player/previous

30초 전으로 탐색: PUT /v1/me/player/seek?position_ms={현재위치 - 30000}

셔플/반복 모드 제어: PUT /v1/me/player/shuffle?state={true|false}, PUT /v1/me/player/repeat?state={track|context|off}

음악 검색: GET /v1/search?q={검색어}&type=track

좋아요 추가 (라이브러리 저장): PUT /v1/me/tracks?ids={곡ID}
```

### 7. 코드 컨벤션 및 패턴
> 이 프로젝트에서 따르는 특수한 규칙을 명시하세요.

```
Type Hinting: 모든 함수 및 메서드에 정적 타입을 명시해야 합니다 (mypy . 통과 필수).

예외 처리: 외부 HTTP 요청 실패, 토큰 만료(401), 프리미엄 권한 부족(403) 발생 시 사용자가 인지할 수 있는 깨끗한 에러 메시지를 출력하고 예외를 격리합니다.

사전 검증: Premium 요금제가 필요한 API 명령군 실행 전, 유저 세션 상태를 검증하는 데코레이터나 인터셉터 패턴을 활용합니다.
```

### 8. 환경 변수
> 필요한 환경 변수를 명시하세요. (실제 값은 절대 포함하지 말 것)

```
SPOTIFY_CLIENT_ID=182dece3a7ef4aa5998ac4bdd2e25963
SPOTIFY_REDIRECT_URI=https://127.0.0.1:8080/callback
```
9. 크로스 플랫폼 및 환경 변수 명확화 규격
OS별 설정 및 캐시 파일 저장 경로:

Unix/Linux/macOS: ~/.config/spotify-cli/

Windows: %APPDATA%\spotify-cli\ (os.path.expandvars 또는 pathlib.Path.home() 사용)

경로에 디렉토리가 없을 경우 시스템이 자동으로 생성하며, 생성 시 권한은 Unix 기준 700(유저 전용 읽기/쓰기/실행)으로 제한합니다.

로컬 파일 보안 구현:

토큰 파일(credentials.json) 생성 시 Unix 계열은 chmod 600 권한을 부여합니다.

Windows 환경의 경우 외부 라이브러리(pywin32) 의존성을 피하기 위해 시스템 기본 명령어인 icacls를 서브프로세스로 호출하여 현재 로그인한 세션 유저(%USERNAME%)외의 모든 상속 및 접근 권한을 명시적으로 차단(/inheritance:r /grant:r "%USERNAME%":F)합니다.

10. 비기능 및 인프라 안정성 요구사항
HTTP 요청 타임아웃: 모든 requests 호출에는 명시적으로 timeout=10(10초) 파라미터를 필수 할당합니다.

Rate Limiting (429) 대처: Spotify API 호출 제한 초과로 429 Too Many Requests를 수신하면, 응답 헤더의 Retry-After 초(seconds) 값을 파악하여 해당 시간만큼 프로세스를 안전하게 time.sleep() 시킨 후 자동으로 재시도합니다.

네트워크 에러 대응: 에러 발생 시 단순 트레이스백을 출력하지 않고 아래 규칙에 따라 분기하여 출력합니다.

연결 실패/DNS 오류: [오류] 스포티파이 서버에 연결할 수 없습니다. 네트워크 연결을 확인해 주세요.

요청 타임아웃: [오류] 요청 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.

503 점검 중: [오류] 스포티파이 서비스가 현재 점검 중입니다. (503 Service Unavailable)

동시 실행 파일 락(File Lock): 여러 터미널 창에서 동시에 동일한 설정을 수정하거나 토큰을 갱신하는 충돌을 방지하기 위해, credentials.json을 업데이트할 때는 fcntl(Unix) 또는 임시 락 파일 생성 검증 알고리즘을 구현하여 동시성 충돌을 원천 차단합니다.

터미널 너비 및 인코딩 가드:

출력 표와 플레이어 프레임이 깨지지 않도록 프로그램 실행 시 터미널의 최소 가로 너비를 체크하여 80자 미만일 경우 가로폭을 넓혀달라는 경고를 보냅니다.

시스템 인코딩이 UTF-8이 아닐 경우 하트(♥)나 음표(🎵) 기호가 깨지므로, 익셉션 핸들러를 통해 ASCII 대체 텍스트([LIKED], >>)로 자동 변환(Fallback) 출력하는 유틸리티 함수를 거치게 합니다.

로그 파일 관리: 사용자가 soc --debug 플래그를 붙여 실행하는 경우, 실시간 표준 출력(stdout)뿐만 아니라 설정 디렉토리 하위에 debug.log 파일로 요청/응답 전문을 누적 기록합니다.

의존성 관리 단일화: 프로젝트 메타데이터 및 빌드 설정은 pyproject.toml로 통합 관리하되, 개발 환경 편의성을 위해 requirements.txt 파일에는 -e . 단 한 줄만 포함시켜 pyproject.toml의 의존성을 바라보도록 단일화합니다.

11. 엣지 케이스 및 세부 예외 처리 규격
파일 손상 및 위조: credentials.json 또는 last_search.json 파일이 비어있거나 올바른 JSON 형식이 아닐 경우(json.JSONDecodeError), 파일을 즉시 강제 삭제하고 초기화한 뒤 [오류] 인증 파일이 손상되었습니다. 'soc login'으로 다시 인증해 주세요. 문구를 출력합니다.

계정 상태 변동 익셉션:

계정이 삭제되거나 비활성화되어 토큰 갱신이 원천 실패하면 로컬 토큰을 파기하고 재로그인을 안내합니다.

이전에 정상 작동하던 계정이 Free 요금제로 다운그레이드되어 제어 명령(soc play, soc pause 등) 유입 시 403 Forbidden이 반환되면 [오류] 이 기능은 Spotify Premium 계정에서만 작동합니다. 권한 에러를 명확히 안내합니다.

인증 단계 이탈: soc login 호출로 로컬 임시 콜백 서버(기본 8080 포트)가 켜진 상태에서 사용자가 브라우저 창을 그냥 닫거나 권한 승인 거부를 누를 경우, 서버는 최대 3분(180초) 동안만 대기(Timeout)한 뒤 [오류] 인증 시간이 초과되었거나 사용자가 권한을 거부했습니다. 메시지를 뿌리고 임시 서버 포트를 안전하게 해제한 뒤 종료합니다. 만약 8080 포트가 이미 타 프로세스에 의해 사용 중이라면 시스템은 8081, 8082 순으로 비어있는 포트를 최대 5회 자동 탐색하여 전환합니다.

검색 및 재생 엣지 케이스:

빈 검색어(soc search "") 입력 시 API를 호출하지 않고 [안내] 검색어를 입력해 주세요.를 반환합니다. 특수문자가 포함된 검색어는 내부적으로 URL 인코딩 처리를 완벽히 수행합니다.

이미 일시정지된 상태에서 soc pause를 연타하거나, 이미 재생 중인데 soc resume을 중복 호출하는 경우 Spotify API가 던지는 에러 메시지를 무시하고 사용자에게는 현재 상태(일시정지됨/재생중임)를 담담하게 재출력합니다.

플레이리스트의 첫 번째 곡에서 soc prev 실행 시, 스포티파이 표준 사양에 따라 곡의 처음(0초) 위치로 타임라인을 되돌립니다.

최근 검색 결과 캐시 파일(last_search.json)은 세션 종료 시 삭제하지 않고 영구 보관하되, 최대 24시간의 유효 기간을 부여하여 생성 시간이 하루가 지난 캐시 파일은 올바르지 않은 검색 이력으로 간주하고 만료 처리합니다.

### 12. CLI 명령어 목록 및 사용자 플로우

#### 전체 명령어
| 명령어 | 설명 | 예시 |
|--------|------|------|
| `soc help` | 사용 가능한 명령어 목록 및 도움말 표시 | `soc help` |
| `soc login` | Spotify 로그인 (OAuth PKCE) | `soc login` |
| `soc current` | 현재 재생 중인 곡 확인 | `soc current` |
| `soc status` | 현재 상태 전체 표시 (곡 + 셔플 + 반복 모드) | `soc status` |
| `soc search "검색어"` | 곡 검색 (상위 10개) | `soc search "blinding lights"` |
| `soc play "곡이름"` | 곡 검색 후 첫 번째 결과 바로 재생 | `soc play "dynamite"` |
| `soc play [번호]` | 검색 결과 중 특정 번호 재생 | `soc play 3` |
| `soc pause` | 일시정지 | `soc pause` |
| `soc resume` | 재생 재개 | `soc resume` |
| `soc next` | 다음 곡 | `soc next` |
| `soc prev` | 이전 곡 | `soc prev` |
| `soc rewind` | 30초 전으로 이동 | `soc rewind` |
| `soc like current` | 현재 곡 좋아요 추가 | `soc like current` |
| `soc shuffle on/off` | 셔플 모드 설정 | `soc shuffle on` |
| `soc repeat track/context/off` | 반복 모드 설정 | `soc repeat track` |

#### 사용자 플로우

```
<온보딩>

사용자에게 스포티파이 프리미엄 계정으로 로그인 하도록 유도 (명령어 soc login)
명령어 입력 시 외부 웹 브라우저가 열리며 스포티파이 로그인 및 권한 승인 진행.

리다이렉트된 토큰을 받아 계정의 구독 등급 확인.

예외: Premium 계정이 아닐 경우 [오류] 이 애플리케이션은 Spotify Premium 계정만 지원합니다. 메시지 출력 후 세션 종료.

성공: OO님 환영합니다! 문구 출력. 활성화된 디바이스에서 음악이 재생 중인 경우 현재 재생 중인 [곡 제목] - [아티스트 이름]을 함께 표기.

<음악 검색>
명령어: python main.py search "[검색어]" (또는 별칭 soc search "[검색어]")

플로우:

검색어 데이터를 바탕으로 API 호출 후, popularity로 정렬 후 상위 10개 터미널 상에 정돈된 표(Table) 형태로 출력. 페이지네이션 미지원.

표 내부 구성 열: 순번(Index), 곡 제목, 아티스트, 앨범명, 좋아요 여부(공백 혹은 ♥)

<현재 음악 확인>
명령어: python main.py current (또는 별칭 soc current)

플로우:

현재 재생 상태를 조회하여 재생 중인 [곡 제목] - [아티스트 이름] 정보를 직관적으로 출력.

<좋아하는 곡 표시>
명령어: python main.py like current (또는 별칭 soc like current)

플로우:

현재 재생 중인 음악의 ID 값을 파악한 뒤, 유저의 '좋아요 표시한 곡(Saved Tracks)' 목록에 즉시 추가.

추가 완료 후 현재 재생 중인 [곡 제목]을 좋아하는 곡 목록에 추가했습니다. 메시지 출력.

<음악 재생>
명령어: python main.py play "[곡 이름]" (또는 별칭 soc play "[곡 이름]")
```

### 13. 비기능 요구사항 (UX 및 안정성)

| 항목 | 설명 |
|------|------|
| **로딩 인디케이터** | API 호출 중 스피너 표시 (`rich` 라이브러리 활용) |
| **색상 출력** | 성공(초록), 오류(빨강), 안내(노랑) 구분 (`rich` 또는 `typer.style`) |
| **Ctrl+C 처리** | 작업 중단 시 `[안내] 작업이 취소되었습니다.` 메시지 출력 후 깔끔한 종료 |
| **디버그 모드** | `soc --debug` 플래그로 HTTP 요청/응답 상세 로그 출력 |
| **재시도 로직** | 일시적 오류(5xx, 타임아웃) 시 자동 재시도 (최대 3회, 지수 백오프) |

#### ASCII 아트 스타일 가이드
CLI 출력 시 시각적 효과를 위해 ASCII 아트를 활용합니다.
- **참고 사이트:** https://snskeyboard.com/asciiart/
- **활용 위치:**
  - 로그인 성공 시 환영 배너
  - `soc current` 출력 시 음악 플레이어 프레임
  - 에러 발생 시 경고 아이콘

**예시 (로그인 성공 배너):**
```
♪♫•*¨*•.¸¸♪♫ Spotify on CLI ♫♪¸¸.•*¨*•♫♪
       OO님 환영합니다!
♪♫•*¨*•.¸¸♪♫•*¨*•.¸¸♪♫•*¨*•.¸¸♪♫
```

**예시 (현재 재생 중):**
```
♪♫•*¨*•.¸¸♪♫ Spotify on CLI Status ♫♪¸¸.•*¨*•♫♪

[재생 상태] ▶ Playing
[현재 곡]   Blinding Lights - The Weeknd
[앨범]       After Hours
[셔플 모드]  ON (Shuffle Active)
[반복 모드]  TRACK (Repeating Current Track)

♪♫•*¨*•.¸¸♪♫•*¨*•.¸¸♪♫•*¨*•.¸¸♪♫•*¨*•.¸¸♪♫
```

14. 테스트 전략
tests/ 디렉터리 내에 유닛 테스트 코드를 작성합니다.

실제 Spotify API를 호출하지 않도록 unittest.mock 또는 pytest-mock을 사용하여 services/spotify.py의 HTTP 응답 값을 가상으로 구현(Mocking)해야 합니다.
