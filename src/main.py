"""
Spotify on CLI - 메인 진입점.

Typer를 사용한 CLI 명령어를 정의합니다.
"""

# urllib3 경고 필터 (다른 import보다 먼저 실행)
import warnings
warnings.filterwarnings("ignore", message=".*urllib3.*OpenSSL.*")

import functools
import signal
import sys
from typing import Callable, TypeVar

import typer
from src.display import print_banner, print_status
from src.utils.console import (
    print_error,
    print_info,
    print_success,
    set_debug_mode,
)
from src.services.auth import (
    start_auth_flow,
    wait_for_callback,
    exchange_code_for_token,
    TokenError,
)
from src.services.token_storage import save_token

F = TypeVar("F", bound=Callable[..., None])
from src.services.spotify_api import (
    get_currently_playing,
    get_current_user,
    get_playback_state,
    pause_playback,
    resume_playback,
    skip_to_next,
    skip_to_previous,
    rewind_30_seconds,
    search_tracks,
    play_track,
    get_similar_tracks,
    get_queue,
    search_playlists,
    play_playlist,
    search_podcasts,
    search_episodes,
    play_show,
    play_episode,
    set_shuffle,
    set_repeat,
    RefreshFailedError,
    SpotifyAPIError,
    TokenNotFoundError,
    NoActiveDeviceError,
)
from src.services.search_cache import (
    get_cached_result_by_index,
    load_search_cache,
    get_cached_playlist_by_index,
    load_playlist_cache,
    get_cached_podcast_by_index,
    load_podcast_cache,
    get_cached_episode_by_index,
    load_episode_cache,
)

app = typer.Typer(
    name="soc",
    help="Spotify on CLI - 터미널에서 Spotify를 제어하세요.",
    add_completion=False,
)


def _handle_sigint(signum: int, frame: object) -> None:
    """
    Ctrl+C (SIGINT) 시그널 핸들러.

    Args:
        signum: 시그널 번호
        frame: 현재 스택 프레임
    """
    print_info("\n[안내] 작업이 취소되었습니다.")
    sys.exit(0)


# SIGINT 핸들러 등록
signal.signal(signal.SIGINT, _handle_sigint)


def handle_api_error(func: F) -> F:
    """
    API 호출 에러를 처리하는 데코레이터.

    TokenNotFoundError, RefreshFailedError, SpotifyAPIError, NoActiveDeviceError를
    사용자 친화적 메시지로 변환하고 종료합니다.
    KeyboardInterrupt도 깔끔하게 처리합니다.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except KeyboardInterrupt:
            print_info("\n[안내] 작업이 취소되었습니다.")
            raise typer.Exit(0)
        except TokenNotFoundError:
            print_error("[오류] 로그인이 필요합니다. 'soc login'을 실행하세요.")
            raise typer.Exit(1)
        except RefreshFailedError as e:
            print_error(str(e))
            raise typer.Exit(1)
        except NoActiveDeviceError as e:
            print_error(str(e))
            raise typer.Exit(1)
        except SpotifyAPIError as e:
            print_error(f"[오류] Spotify API: {e.message}")
            raise typer.Exit(1)
    return wrapper  # type: ignore[return-value]



@app.command()
def login() -> None:
    """
    Spotify 계정으로 로그인합니다.

    브라우저에서 Spotify 인증 페이지를 열고,
    로그인 후 토큰을 저장합니다.
    """
    try:
        server, port, code_verifier, state, auth_url = start_auth_flow()
        auth_code, error = wait_for_callback(server)

        if error == "timeout":
            typer.echo("[오류] 인증 시간이 초과되었거나 사용자가 권한을 거부했습니다.")
            raise typer.Exit(1)

        if error:
            typer.echo(f"[오류] 인증 실패: {error}")
            raise typer.Exit(1)

        if not auth_code:
            typer.echo("[오류] 인증 코드를 받지 못했습니다.")
            raise typer.Exit(1)

        typer.echo("[안내] 토큰을 교환하는 중...")
        token = exchange_code_for_token(auth_code, code_verifier, port)

        save_token(
            access_token=token["access_token"],
            token_type=token["token_type"],
            scope=token["scope"],
            expires_in=token["expires_in"],
            refresh_token=token["refresh_token"],
        )

        print_success("")
        print_success("  ✧･ﾟ: *✧ 로그인 성공! ✧* :･ﾟ✧")
        print_success("  ♪(๑ᴖ◡ᴖ๑)♪ 환영해요~")
        print_success("  이제 soc 명령어로 음악을 즐겨보세요!")
        print_success("")

    except TokenError as e:
        typer.echo(f"[오류] 토큰 교환 실패: {e.description}")
        raise typer.Exit(1)
    except ValueError as e:
        typer.echo(str(e))
        raise typer.Exit(1)


@app.command()
def version() -> None:
    """
    버전 정보를 표시합니다.
    """
    typer.echo("Spotify on CLI v0.1.0")


@app.command()
@handle_api_error
def search(query: str = typer.Argument(..., help="검색할 곡 제목 또는 아티스트")) -> None:
    """
    Spotify에서 곡을 검색합니다.

    검색 결과는 인기도순으로 정렬되어 상위 10개가 표시됩니다.
    결과는 캐시되어 'soc play [번호]'로 바로 재생할 수 있습니다.

    Example:
        $ soc search "blinding lights"
        $ soc search "BTS dynamite"
    """
    if not query.strip():
        typer.echo("[안내] 검색어를 입력해 주세요.")
        raise typer.Exit(1)

    typer.echo(f"[검색] '{query}' 검색 중...")

    try:
        results = search_tracks(query)
    except ValueError as e:
        typer.echo(str(e))
        raise typer.Exit(1)

    if not results:
        typer.echo("[안내] 검색 결과가 없습니다.")
        return

    typer.echo(f"\n[결과] '{query}' 검색 결과 (상위 {len(results)}개):\n")
    typer.echo(f"{'#':<3} {'곡 제목':<30} {'아티스트':<25} {'앨범':<20}")
    typer.echo("-" * 80)

    for item in results:
        artists_str = ", ".join(item["artists"])[:24]
        name_str = item["name"][:29]
        album_str = item["album"][:19]
        typer.echo(f"{item['index']:<3} {name_str:<30} {artists_str:<25} {album_str:<20}")

    typer.echo("\n[안내] 'soc play [번호]'로 재생할 수 있습니다.")


def _fetch_recommendations_quietly(track_id: str) -> list[str]:
    """추천 곡 URI를 조용히 가져옵니다. 실패해도 빈 목록을 반환합니다."""
    try:
        return get_similar_tracks(track_id)
    except Exception:
        return []


@app.command()
@handle_api_error
def play(query: str = typer.Argument(..., help="트랙 번호(1-10) 또는 검색어")) -> None:
    """
    트랙을 재생합니다.

    숫자를 입력하면 최근 검색 결과에서 해당 번호의 곡을 재생합니다.
    문자열을 입력하면 검색 후 첫 번째 결과를 바로 재생합니다.
    선택한 곡 이후로 자동재생할 추천 곡 20개를 함께 큐에 채웁니다.
    Premium 계정이 필요합니다.

    Example:
        $ soc play 3              # 검색 결과 3번 재생
        $ soc play "dynamite"     # 검색 후 첫 번째 결과 재생
    """
    query = query.strip()
    if not query:
        typer.echo("[안내] 트랙 번호 또는 검색어를 입력해 주세요.")
        raise typer.Exit(1)

    # 숫자인 경우: 캐시에서 해당 인덱스의 트랙 재생
    if query.isdigit():
        index = int(query)
        cached = get_cached_result_by_index(index)

        if cached is None:
            cache = load_search_cache()
            if cache is None:
                typer.echo("[안내] 검색 기록이 없습니다. 먼저 'soc search'로 검색하세요.")
            else:
                typer.echo(f"[오류] 유효한 번호를 입력하세요 (1-{len(cache['results'])})")
            raise typer.Exit(1)

        typer.echo(f"[재생] {cached['name']} - {', '.join(cached['artists'])}")
        rec_uris = _fetch_recommendations_quietly(cached["track_id"])
        play_track(cached["track_uri"], queue_uris=rec_uris)
        if rec_uris:
            typer.echo(f"[안내] 추천 곡 {len(rec_uris)}개가 이어서 재생됩니다.")
        return

    # 문자열인 경우: 검색 후 첫 번째 결과 재생
    typer.echo(f"[검색] '{query}' 검색 중...")

    try:
        results = search_tracks(query)
    except ValueError as e:
        typer.echo(str(e))
        raise typer.Exit(1)

    if not results:
        typer.echo("[안내] 검색 결과가 없습니다.")
        raise typer.Exit(1)

    first = results[0]
    typer.echo(f"[재생] {first['name']} - {', '.join(first['artists'])}")
    rec_uris = _fetch_recommendations_quietly(first["track_id"])
    play_track(first["track_uri"], queue_uris=rec_uris)
    if rec_uris:
        typer.echo(f"[안내] 추천 곡 {len(rec_uris)}개가 이어서 재생됩니다.")


@app.command()
@handle_api_error
def pause() -> None:
    """
    현재 재생을 일시정지합니다.

    음악과 팟캐스트 에피소드 모두 일시정지합니다.
    Premium 계정이 필요합니다.

    Example:
        $ soc pause
    """
    already_paused = not pause_playback()
    if already_paused:
        typer.echo("[안내] 이미 일시정지 상태입니다.")
    else:
        typer.echo("[일시정지] ⏸ 재생이 일시정지되었습니다.")


@app.command()
@handle_api_error
def resume() -> None:
    """
    일시정지된 재생을 재개합니다.

    음악과 팟캐스트 에피소드 모두 재개합니다.
    Premium 계정이 필요합니다.

    Example:
        $ soc resume
    """
    already_playing = not resume_playback()
    if already_playing:
        typer.echo("[안내] 이미 재생 중입니다.")
    else:
        typer.echo("[재생] ▶ 재생이 재개되었습니다.")


@app.command()
@handle_api_error
def next() -> None:
    """
    다음 트랙 또는 에피소드로 건너뜁니다.

    Premium 계정이 필요합니다.

    Example:
        $ soc next
    """
    skip_to_next()
    typer.echo("[다음] ⏭ 다음으로 이동했습니다.")


@app.command()
@handle_api_error
def rewind() -> None:
    """
    현재 재생 위치에서 30초 전으로 이동합니다.

    팟캐스트 에피소드 듣기에 유용합니다.
    Premium 계정이 필요합니다.

    Example:
        $ soc rewind
    """
    new_pos_ms = rewind_30_seconds()
    minutes = new_pos_ms // 60000
    seconds = (new_pos_ms % 60000) // 1000
    typer.echo(f"[되감기] ⏪ {minutes}:{seconds:02d} 위치로 이동했습니다.")


@app.command()
@handle_api_error
def podcast(query: str = typer.Argument(..., help="검색할 팟캐스트 에피소드 또는 쇼 이름")) -> None:
    """
    Spotify에서 팟캐스트 에피소드를 검색합니다.

    검색 결과는 상위 10개가 표시됩니다.
    결과는 캐시되어 'soc episode [번호]'로 바로 재생할 수 있습니다.

    Example:
        $ soc podcast "사피엔스"
        $ soc podcast "lex fridman"
    """
    if not query.strip():
        typer.echo("[안내] 검색어를 입력해 주세요.")
        raise typer.Exit(1)

    typer.echo(f"[검색] '{query}' 팟캐스트 검색 중...")

    try:
        results = search_podcasts(query)
    except ValueError as e:
        typer.echo(str(e))
        raise typer.Exit(1)

    if not results:
        typer.echo("[안내] 검색 결과가 없습니다.")
        return

    typer.echo(f"\n[결과] '{query}' 팟캐스트 검색 결과 (상위 {len(results)}개):\n")
    typer.echo(f"{'#':<3} {'팟캐스트 이름':<35} {'퍼블리셔':<22} {'에피소드':<6}")
    typer.echo("-" * 68)

    for item in results:
        name_str = item["name"][:34]
        pub_str = (item["publisher"] or "-")[:21]
        typer.echo(f"{item['index']:<3} {name_str:<35} {pub_str:<22} {item['total_episodes']:<6}")

    typer.echo("\n[안내] 'soc episode [번호]'로 재생할 수 있습니다.")


@app.command()
@handle_api_error
def episode(query: str = typer.Argument(..., help="팟캐스트 번호(1-10) 또는 검색어")) -> None:
    """
    팟캐스트를 재생합니다.

    숫자를 입력하면 최근 팟캐스트 검색 결과에서 해당 번호의 쇼를 재생합니다.
    문자열을 입력하면 검색 후 첫 번째 결과를 바로 재생합니다.
    Premium 계정이 필요합니다.

    Example:
        $ soc episode 2              # 검색 결과 2번 쇼 재생
        $ soc episode "good game"    # 검색 후 첫 번째 쇼 재생
    """
    query = query.strip()
    if not query:
        typer.echo("[안내] 팟캐스트 번호 또는 검색어를 입력해 주세요.")
        raise typer.Exit(1)

    if query.isdigit():
        index = int(query)
        cached = get_cached_podcast_by_index(index)

        if cached is None:
            cache = load_podcast_cache()
            if cache is None:
                typer.echo("[안내] 팟캐스트 검색 기록이 없습니다. 먼저 'soc podcast'로 검색하세요.")
            else:
                typer.echo(f"[오류] 유효한 번호를 입력하세요 (1-{len(cache['results'])})")
            raise typer.Exit(1)

        typer.echo(f"[재생] {cached['name']} — {cached['publisher']}")
        play_show(cached["show_uri"])
        return

    typer.echo(f"[검색] '{query}' 팟캐스트 검색 중...")

    try:
        results = search_podcasts(query)
    except ValueError as e:
        typer.echo(str(e))
        raise typer.Exit(1)

    if not results:
        typer.echo("[안내] 검색 결과가 없습니다.")
        raise typer.Exit(1)

    first = results[0]
    typer.echo(f"[재생] {first['name']} — {first['publisher']}")
    play_show(first["show_uri"])


@app.command()
@handle_api_error
def episodes(query: str = typer.Argument(..., help="검색할 에피소드 이름")) -> None:
    """
    팟캐스트 에피소드를 검색합니다.

    에피소드 제목, 팟캐스트 이름, 길이, 공개일을 표시합니다.
    결과는 캐시되어 'soc ep [번호]'로 바로 재생할 수 있습니다.

    Example:
        $ soc episodes "good game"
        $ soc episodes "lex fridman elon"
    """
    if not query.strip():
        typer.echo("[안내] 검색어를 입력해 주세요.")
        raise typer.Exit(1)

    typer.echo(f"[검색] '{query}' 에피소드 검색 중...")

    try:
        results = search_episodes(query)
    except ValueError as e:
        typer.echo(str(e))
        raise typer.Exit(1)

    if not results:
        typer.echo("[안내] 검색 결과가 없습니다.")
        return

    typer.echo(f"\n[결과] '{query}' 에피소드 검색 결과 (상위 {len(results)}개):\n")
    typer.echo(f"{'#':<3} {'에피소드 제목':<30} {'팟캐스트':<22} {'길이':<7} {'공개일':<12}")
    typer.echo("-" * 76)

    for item in results:
        minutes = item["duration_ms"] // 60000
        seconds = (item["duration_ms"] % 60000) // 1000
        typer.echo(
            f"{item['index']:<3} "
            f"{item['name'][:29]:<30} "
            f"{(item['show_name'] or '-')[:21]:<22} "
            f"{minutes}:{seconds:02d}{'':2}"
            f"{item['release_date'][:10]:<12}"
        )

    typer.echo("\n[안내] 'soc ep [번호]'로 재생할 수 있습니다.")


@app.command()
@handle_api_error
def ep(query: str = typer.Argument(..., help="에피소드 번호(1-10) 또는 검색어")) -> None:
    """
    팟캐스트 에피소드를 재생합니다.

    숫자를 입력하면 최근 에피소드 검색 결과에서 해당 번호를 재생합니다.
    문자열을 입력하면 검색 후 첫 번째 결과를 바로 재생합니다.
    Premium 계정이 필요합니다.

    Example:
        $ soc ep 3
        $ soc ep "good game episode 100"
    """
    query = query.strip()
    if not query:
        typer.echo("[안내] 에피소드 번호 또는 검색어를 입력해 주세요.")
        raise typer.Exit(1)

    if query.isdigit():
        index = int(query)
        cached = get_cached_episode_by_index(index)

        if cached is None:
            cache = load_episode_cache()
            if cache is None:
                typer.echo("[안내] 에피소드 검색 기록이 없습니다. 먼저 'soc episodes'로 검색하세요.")
            else:
                typer.echo(f"[오류] 유효한 번호를 입력하세요 (1-{len(cache['results'])})")
            raise typer.Exit(1)

        show_label = f" — {cached['show_name']}" if cached["show_name"] else ""
        typer.echo(f"[재생] {cached['name']}{show_label}")
        play_episode(cached["episode_uri"])
        return

    typer.echo(f"[검색] '{query}' 에피소드 검색 중...")

    try:
        results = search_episodes(query)
    except ValueError as e:
        typer.echo(str(e))
        raise typer.Exit(1)

    if not results:
        typer.echo("[안내] 검색 결과가 없습니다.")
        raise typer.Exit(1)

    first = results[0]
    show_label = f" — {first['show_name']}" if first["show_name"] else ""
    typer.echo(f"[재생] {first['name']}{show_label}")
    play_episode(first["episode_uri"])


@app.command()
@handle_api_error
def playlist(query: str = typer.Argument(..., help="검색할 플레이리스트 이름")) -> None:
    """
    Spotify에서 플레이리스트를 검색합니다.

    검색 결과는 상위 10개가 표시됩니다.
    결과는 캐시되어 'soc playlist-play [번호]'로 바로 재생할 수 있습니다.

    Example:
        $ soc playlist "chill vibes"
        $ soc playlist "운동할때"
    """
    if not query.strip():
        typer.echo("[안내] 검색어를 입력해 주세요.")
        raise typer.Exit(1)

    typer.echo(f"[검색] '{query}' 플레이리스트 검색 중...")

    try:
        results = search_playlists(query)
    except ValueError as e:
        typer.echo(str(e))
        raise typer.Exit(1)

    if not results:
        typer.echo("[안내] 검색 결과가 없습니다.")
        return

    typer.echo(f"\n[결과] '{query}' 플레이리스트 검색 결과 (상위 {len(results)}개):\n")
    typer.echo(f"{'#':<3} {'플레이리스트 이름':<35} {'만든이':<20} {'곡 수':<6}")
    typer.echo("-" * 66)

    for item in results:
        name_str = item["name"][:34]
        owner_str = item["owner"][:19]
        typer.echo(f"{item['index']:<3} {name_str:<35} {owner_str:<20} {item['track_count']:<6}")

    typer.echo("\n[안내] 'soc playlist-play [번호]'로 재생할 수 있습니다.")


@app.command()
@handle_api_error
def playlist_play(query: str = typer.Argument(..., help="플레이리스트 번호(1-10) 또는 검색어")) -> None:
    """
    플레이리스트를 재생합니다.

    숫자를 입력하면 최근 플레이리스트 검색 결과에서 해당 번호를 재생합니다.
    문자열을 입력하면 검색 후 첫 번째 결과를 바로 재생합니다.
    플레이리스트 전체가 컨텍스트로 재생되어 끝까지 자동 재생됩니다.
    Premium 계정이 필요합니다.

    Example:
        $ soc playlist-play 1
        $ soc playlist-play "chill vibes"
    """
    query = query.strip()
    if not query:
        typer.echo("[안내] 플레이리스트 번호 또는 검색어를 입력해 주세요.")
        raise typer.Exit(1)

    if query.isdigit():
        index = int(query)
        cached = get_cached_playlist_by_index(index)

        if cached is None:
            cache = load_playlist_cache()
            if cache is None:
                typer.echo("[안내] 플레이리스트 검색 기록이 없습니다. 먼저 'soc playlist'로 검색하세요.")
            else:
                typer.echo(f"[오류] 유효한 번호를 입력하세요 (1-{len(cache['results'])})")
            raise typer.Exit(1)

        typer.echo(f"[재생] {cached['name']} (by {cached['owner']})")
        play_playlist(cached["playlist_uri"])
        return

    typer.echo(f"[검색] '{query}' 플레이리스트 검색 중...")

    try:
        results = search_playlists(query)
    except ValueError as e:
        typer.echo(str(e))
        raise typer.Exit(1)

    if not results:
        typer.echo("[안내] 검색 결과가 없습니다.")
        raise typer.Exit(1)

    first = results[0]
    typer.echo(f"[재생] {first['name']} (by {first['owner']})")
    play_playlist(first["playlist_uri"])


@app.command()
@handle_api_error
def shuffle(state: str = typer.Argument(..., help="on 또는 off")) -> None:
    """
    셔플 모드를 설정합니다.

    Example:
        $ soc shuffle on
        $ soc shuffle off
    """
    state_lower = state.lower()
    if state_lower not in ("on", "off"):
        typer.echo("[오류] 'on' 또는 'off'를 입력하세요.")
        raise typer.Exit(1)

    enabled = state_lower == "on"
    set_shuffle(enabled)
    status = "켜짐" if enabled else "꺼짐"
    typer.echo(f"[설정] 셔플 모드: {status}")


@app.command()
@handle_api_error
def prev() -> None:
    """
    이전 트랙으로 이동합니다.

    3초 이상 재생된 경우 곡의 처음으로,
    그렇지 않으면 이전 트랙으로 이동합니다.
    Premium 계정이 필요합니다.

    Example:
        $ soc prev
    """
    skip_to_previous()
    typer.echo("[이전] 이전 트랙으로 이동했습니다.")


@app.command()
@handle_api_error
def repeat(state: str = typer.Argument(..., help="track, context, 또는 off")) -> None:
    """
    반복 모드를 설정합니다.

    - track: 현재 트랙 반복
    - context: 앨범/플레이리스트 반복
    - off: 반복 끄기

    Example:
        $ soc repeat track
        $ soc repeat context
        $ soc repeat off
    """
    state_lower = state.lower()
    try:
        set_repeat(state_lower)
    except ValueError as e:
        typer.echo(str(e))
        raise typer.Exit(1)

    labels = {"track": "트랙 반복", "context": "컨텍스트 반복", "off": "끄기"}
    typer.echo(f"[설정] 반복 모드: {labels.get(state_lower, state_lower)}")


@app.command()
@handle_api_error
def status() -> None:
    """
    현재 재생 상태를 전체 표시합니다.

    재생 상태, 현재 곡, 디바이스, 셔플/반복 모드를 한눈에 보여줍니다.

    Example:
        $ soc status
    """
    playback = get_playback_state()
    track = get_currently_playing()
    print_status(playback, track)


@app.command()
@handle_api_error
def queue() -> None:
    """
    현재 재생 큐를 표시합니다.

    Example:
        $ soc queue
    """
    result = get_queue()
    cp = result["currently_playing"]
    items = result["queue"]

    typer.echo()
    if cp:
        artist = ", ".join(cp["artists"]) if cp["artists"] else ""
        icon = "🎙" if cp["type"] == "episode" else "♬"
        typer.echo(f"  ▶ 재생중  {icon} {cp['name'][:35]}  {artist[:25]}")
        typer.echo("  " + "─" * 50)

    if not items:
        typer.echo("  (다음 대기 곡 없음)")
    else:
        typer.echo(f"  ♪ 다음 재생 대기 ({len(items)}곡)\n")
        for i, item in enumerate(items, 1):
            artist = ", ".join(item["artists"]) if item["artists"] else ""
            icon = "🎙" if item["type"] == "episode" else "♬"
            typer.echo(f"  {i:>2}. {icon} {item['name'][:35]}  {artist[:25]}")
    typer.echo()


@app.command()
@handle_api_error
def lyrics() -> None:
    """
    현재 재생 중인 곡의 가사를 표시합니다.

    LRCLIB에서 가사를 가져와 출력합니다.

    Example:
        $ soc lyrics
    """
    from src.services.lrclib import search_lyrics

    track = get_currently_playing()
    if track is None:
        typer.echo("[안내] 재생 중인 곡이 없습니다.")
        raise typer.Exit(0)
    if track.get("item_type") == "episode":
        typer.echo("[안내] 팟캐스트 에피소드는 가사를 지원하지 않습니다.")
        raise typer.Exit(0)

    artist_str = ", ".join(track["artists"])
    typer.echo(f"  {track['name']} — {artist_str}")
    typer.echo("  가사 검색 중...")

    data = search_lyrics(track["name"], artist_str)
    if data is None or not data["plain_lyrics"]:
        typer.echo("[안내] 가사를 찾을 수 없습니다.")
        raise typer.Exit(0)

    typer.echo(f"\n  ♪ {track['name']} — {artist_str}\n")
    typer.echo(data["plain_lyrics"])


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    debug: bool = typer.Option(
        False,
        "--debug",
        "-d",
        help="디버그 모드 활성화 (HTTP 요청/응답 로깅)",
    ),
) -> None:
    """
    Spotify on CLI - 터미널에서 Spotify Premium을 제어하세요.

    명령어 없이 실행하면 환영 배너와 현재 재생 중인 곡을 표시합니다.
    """
    if debug:
        set_debug_mode(True)
        print_info("[디버그] 디버그 모드가 활성화되었습니다.")

    if ctx.invoked_subcommand is None:
        print_banner()
        try:
            user = get_current_user()
            display_name = user["display_name"] or user["id"]
            typer.echo(f"  안녕하세요, {display_name}님! 🎧\n")

            playback = get_playback_state()
            track = get_currently_playing()
            print_status(playback, track)
        except TokenNotFoundError:
            typer.echo("  로그인이 필요합니다. 'soc login'을 실행하세요.\n")
        except (RefreshFailedError, SpotifyAPIError):
            typer.echo("  Spotify 연결에 실패했습니다.\n")


if __name__ == "__main__":
    app()
