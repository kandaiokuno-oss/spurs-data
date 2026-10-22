#!/usr/bin/env python3
"""
spurs_fetch.py — NBAの試合データをNBA.comから取得してJSONで保存する

取得ルール:
  - スパーズの試合            : フル取得（6種類）
  - スパーズの次の対戦相手の直近試合: フル取得
  - それ以外の全試合          : ライト取得（traditional / advanced / tracking の3種類）

フル = traditional, advanced, misc, hustle, tracking, playbyplay
ライト = traditional, advanced, tracking

保存先の構成:
  spurs-data/
    spurs/   2026-10-20_0022600003_OKC@SAS_full.json
    league/  2026-10-20_0022600001_DET@BOS_lite.json
    fetch_state.json   失敗した試合IDの記録（次回に再挑戦）

使い方:
  python3 spurs_fetch.py              # 未取得の終了済み試合を全部取る（通常はこれ）
  python3 spurs_fetch.py --spurs-only # スパーズ戦だけ
  python3 spurs_fetch.py --game 0022600003 --full   # ゲームID指定
  python3 spurs_fetch.py --force      # 保存済みでも取り直す
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from nba_api.stats.endpoints import (
    BoxScoreAdvancedV3,
    BoxScoreHustleV2,
    BoxScoreMiscV3,
    BoxScorePlayerTrackV3,
    BoxScoreTraditionalV3,
    PlayByPlayV3,
)

# ===== 設定（ここだけ自分の環境に合わせて変える） =====
OUT_DIR = Path(__file__).resolve().parent / "data"
SPURS_TEAM_ID = 1610612759
SEASON = "2026-27"
MAX_GAMES_PER_RUN = 40      # 1回の実行で取る上限（初回に溜まってる分を一気に取らない）
PAUSE_BETWEEN_CALLS = 2.5   # APIコール間の秒数（レート制限対策）
# ======================================================

TIMEOUT = 90

FULL_STEPS = [
    ("traditional", BoxScoreTraditionalV3),
    ("advanced", BoxScoreAdvancedV3),
    ("misc", BoxScoreMiscV3),
    ("hustle", BoxScoreHustleV2),
    ("tracking", BoxScorePlayerTrackV3),
    ("playbyplay", PlayByPlayV3),
]
LITE_STEPS = [
    ("traditional", BoxScoreTraditionalV3),
    ("advanced", BoxScoreAdvancedV3),
    ("tracking", BoxScorePlayerTrackV3),
]

STATE_FILE_NAME = "fetch_state.json"


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def call(endpoint_cls, retries=3, **kwargs):
    """NBA.comは時々タイムアウトするので数回リトライする"""
    last = None
    for i in range(retries):
        try:
            ep = endpoint_cls(timeout=TIMEOUT, **kwargs)
            return ep
        except Exception as e:  # noqa: BLE001
            last = e
            log(f"  retry {i + 1}/{retries} ({endpoint_cls.__name__}): {e}")
            time.sleep(5 * (i + 1))
    raise RuntimeError(f"{endpoint_cls.__name__} failed: {last}")


def load_state():
    p = OUT_DIR / STATE_FILE_NAME
    if p.exists():
        return json.loads(p.read_text())
    return {"failed": {}}


def save_state(state):
    (OUT_DIR / STATE_FILE_NAME).write_text(json.dumps(state, ensure_ascii=False, indent=1))


SCHEDULE_URL = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json"


def get_schedule():
    """今季の全試合を1行1試合で返す（CDNの静的JSON。stats.nba.comより格段に安定）"""
    import requests

    r = requests.get(SCHEDULE_URL, timeout=TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    games = []
    for day in r.json()["leagueSchedule"]["gameDates"]:
        for g in day["games"]:
            games.append(
                {
                    "game_id": str(g["gameId"]),
                    "date": str(g["gameDateEst"])[:10],
                    "status": int(g["gameStatus"]),  # 1=予定 2=進行中 3=終了
                    "home_id": int(g["homeTeam"]["teamId"]),
                    "away_id": int(g["awayTeam"]["teamId"]),
                    "home": str(g["homeTeam"]["teamTricode"]),
                    "away": str(g["awayTeam"]["teamTricode"]),
                    "label": str(g.get("gameLabel", "") or ""),
                }
            )
    # レギュラーシーズンのID（002で始まる）だけ
    games = [g for g in games if g["game_id"].startswith("002")]
    games.sort(key=lambda g: (g["date"], g["game_id"]))
    return games


def is_spurs(g):
    return SPURS_TEAM_ID in (g["home_id"], g["away_id"])


def next_opponent_id(games):
    """スパーズの次の未終了試合の相手チームID（なければNone）"""
    for g in games:
        if is_spurs(g) and g["status"] != 3:
            return g["away_id"] if g["home_id"] == SPURS_TEAM_ID else g["home_id"]
    return None


def out_path(g, mode):
    sub = "spurs" if is_spurs(g) else "league"
    return OUT_DIR / sub / f"{g['date']}_{g['game_id']}_{g['away']}@{g['home']}_{mode}.json"


def fetch_game(g, mode):
    steps = FULL_STEPS if mode == "full" else LITE_STEPS
    out = {
        "game_id": g["game_id"],
        "mode": mode,
        "fetched_at": datetime.now().isoformat(),
        "meta": {k: g[k] for k in ("date", "home", "away", "label")},
    }
    for key, cls in steps:
        log(f"  {key} ...")
        out[key] = call(cls, game_id=g["game_id"]).get_dict()
        time.sleep(PAUSE_BETWEEN_CALLS)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spurs-only", action="store_true", help="スパーズ戦だけ取る")
    ap.add_argument("--game", help="ゲームIDを直接指定")
    ap.add_argument("--full", action="store_true", help="--game 指定時にフル取得")
    ap.add_argument("--force", action="store_true", help="保存済みでも取り直す")
    args = ap.parse_args()

    (OUT_DIR / "spurs").mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "league").mkdir(parents=True, exist_ok=True)
    state = load_state()

    log("スケジュール取得中 ...")
    games = get_schedule()
    time.sleep(PAUSE_BETWEEN_CALLS)
    opp = next_opponent_id(games)
    finished = [g for g in games if g["status"] == 3]
    log(f"終了済み {len(finished)} 試合 / 次の対戦相手ID {opp}")

    # 取得対象と取得モードを決める
    targets = []
    if args.game:
        g = next((x for x in games if x["game_id"] == args.game), None)
        if not g:
            log("そのゲームIDがスケジュールに見つからない")
            return 1
        targets.append((g, "full" if (args.full or is_spurs(g)) else "lite"))
    else:
        for g in finished:
            if is_spurs(g):
                targets.append((g, "full"))
            elif args.spurs_only:
                continue
            else:
                targets.append((g, "lite"))
        # 次の対戦相手の直近試合はフルに格上げ（ライト済みでも取り直す）
        if opp and not args.spurs_only:
            opp_games = [g for g in finished if opp in (g["home_id"], g["away_id"])]
            if opp_games:
                last_opp = opp_games[-1]
                targets = [(g, m) for g, m in targets if g["game_id"] != last_opp["game_id"]]
                targets.append((last_opp, "full"))

    # 新しい試合（スパーズ優先）から処理
    targets.sort(key=lambda t: t[0]["date"], reverse=True)
    targets.sort(key=lambda t: not is_spurs(t[0]))  # 安定ソートなのでスパーズ戦が先頭、各グループ内は新しい順

    done = 0
    consecutive_failures = 0
    for g, mode in targets:
        if consecutive_failures >= 3:
            log("3試合連続で失敗。NBA.comに弾かれている可能性が高いので今回は打ち切り")
            break
        if done >= MAX_GAMES_PER_RUN:
            log(f"今回の上限 {MAX_GAMES_PER_RUN} 試合に到達。残りは次回")
            break
        p = out_path(g, mode)
        if p.exists() and not args.force:
            continue
        log(f"取得: {g['date']} {g['away']}@{g['home']} [{mode}] ({g['game_id']})")
        try:
            data = fetch_game(g, mode)
        except Exception as e:  # noqa: BLE001
            log(f"失敗: {e}")
            state["failed"][g["game_id"]] = {"date": g["date"], "error": str(e)[:200]}
            save_state(state)
            consecutive_failures += 1
            time.sleep(10)
            continue
        p.write_text(json.dumps(data, ensure_ascii=False))
        state["failed"].pop(g["game_id"], None)
        save_state(state)
        consecutive_failures = 0
        # 同じ試合のライト版が残っていたら消す（フルに格上げした場合）
        if mode == "full":
            lite = out_path(g, "lite")
            if lite.exists():
                lite.unlink()
        log(f"保存: {p.name} ({p.stat().st_size // 1024} KB)")
        done += 1

    log(f"完了: {done} 試合保存")

    # 記事も拾う（同じフォルダに spurs_news.py がある前提）
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import spurs_news  # noqa: WPS433

        spurs_news.run()
    except Exception as e:  # noqa: BLE001
        log(f"記事取得でエラー（スタッツは保存済み）: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
