"""
Spotify CLI 출력 모듈.

환영 배너, 현재 재생 중인 곡 등 터미널 출력을 담당합니다.
Rich 라이브러리를 사용하여 유니코드 폭을 자동 계산합니다.
"""

import unicodedata
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.services.spotify_api import TrackInfo, PlaybackState

console = Console()


def _fit_text(text: str, width: int) -> str:
    """CJK 2바이트 문자를 고려해 정확히 width 칸에 맞게 자르고 패딩합니다."""
    result, used = [], 0
    for ch in text:
        w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if used + w > width:
            break
        result.append(ch)
        used += w
    return "".join(result) + " " * (width - used)


def print_banner() -> None:
    """
    환영 배너를 출력합니다.

    ASCII 아트 스타일의 Spotify CLI 로고를 표시합니다.
    """
    banner = r"""
  ____              _   _  __                      ____ _     ___
 / ___| _ __   ___ | |_(_)/ _|_   _    ___  _ __  / ___| |   |_ _|
 \___ \| '_ \ / _ \| __| | |_| | | |  / _ \| '_ \| |   | |    | |
  ___) | |_) | (_) | |_| |  _| |_| | | (_) | | | | |___| |___ | |
 |____/| .__/ \___/ \__|_|_|  \__, |  \___/|_| |_|\____|_____|___|
       |_|                    |___/
    """
    print(banner)
    print("  Spotify on CLI - 터미널에서 스포티파이를 제어하세요\n")


def format_duration(ms: int) -> str:
    """
    밀리초를 MM:SS 형식으로 변환합니다.

    Args:
        ms: 밀리초 단위 시간

    Returns:
        str: "MM:SS" 형식의 문자열
    """
    total_seconds = ms // 1000
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes}:{seconds:02d}"




def print_status(
    playback: Optional[PlaybackState], track: Optional[TrackInfo]
) -> None:
    """
    전체 재생 상태를 출력합니다.

    Rich Panel/Table을 사용하여 유니코드 폭을 자동 계산합니다.

    Args:
        playback: 재생 상태 정보 (디바이스, 셔플, 반복 모드 등)
        track: 현재 재생 중인 트랙 정보
    """
    if playback is None:
        console.print()
        console.print("  [yellow](._.) 활성화된 디바이스가 없어요...[/yellow]")
        console.print("  Spotify 앱을 열고 음악을 재생해 주세요!")
        return

    # 재생 상태
    play_status = "[green]･ﾟ✧ ♪ Spotify Status ♪ ✧ﾟ･[/green]" if playback["is_playing"] else "[yellow]⏸ 쉬는중...[/yellow]"

    # 현재 곡/에피소드 정보
    is_episode = track.get("item_type") == "episode" if track else False
    if track:
        second_val = ", ".join(track["artists"])
        progress = format_duration(track["progress_ms"])
        duration = format_duration(track["duration_ms"])
        track_info  = track["name"][:30]
        second_info = second_val[:30]
        third_info  = track["album"][:30]
        time_info   = f"{progress} / {duration}"
    else:
        track_info = second_info = third_info = time_info = "-"

    name_label   = "♬ 에피소드" if is_episode else "♬ 곡명"
    second_label = "♡ 팟캐스트" if is_episode else "♡ 아티스트"

    # 디바이스 정보
    device_name = playback.get("device_name") or "알 수 없음"

    # 셔플/반복 모드
    shuffle_text = "[green]ON[/green]" if playback["shuffle_state"] else "[dim]OFF[/dim]"
    repeat_labels = {"off": "[dim]OFF[/dim]", "track": "[cyan]한곡[/cyan]", "context": "[cyan]전체[/cyan]"}
    repeat_text = repeat_labels.get(playback["repeat_state"], playback["repeat_state"])

    # Rich Table 생성
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("label", style="bold", width=10)
    table.add_column("value", width=30)

    table.add_row(name_label, track_info)
    if second_info and second_info != "-":
        table.add_row(second_label, second_info)
    if not is_episode and third_info and third_info != "-":
        table.add_row("◎ 앨범", third_info)
    table.add_row("▷ 시간", time_info)
    table.add_row("", "")
    table.add_row("♪ 디바이스", device_name[:25])
    table.add_row("⇄ 셔플", shuffle_text)
    table.add_row("↻ 반복", repeat_text)

    # Panel로 감싸기
    panel = Panel(
        table,
        title=play_status,
        subtitle="[green]♪♫♬ ₊˚⊹ ᰔ ⊹˚₊ ♬♫♪[/green]",
        border_style="cyan",
        width=50,
    )

    console.print()
    console.print(panel)
    console.print()


if __name__ == "__main__":
    print_banner()
