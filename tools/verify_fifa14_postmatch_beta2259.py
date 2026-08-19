#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sqlite3
import struct
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT))

from beta_identity import BetaIdentityStore  # noqa: E402
from probe import (  # noqa: E402
    GAME_REPORTING_COMPONENT,
    GAME_REPORTING_RESULT_NOTIFICATION,
    build_fire_notification,
    build_game_reporting_result_notification_body,
    build_shared_blaze_bootstrap_response,
    decode_tdf_document,
    extract_tdf_varint_last,
    tdf_u32,
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"BETA 2.25.9 verifier failed: {message}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="fifa14-beta2259-") as td:
        db = Path(td) / "identity.sqlite3"
        store = BetaIdentityStore(str(db), "new")

        # Emulate selecting Gold Cup round 1. The first-round opaque progress
        # blob is intentionally sanitized by the server, which is the exact case
        # that previously caused every WIN to look like a fresh tournament.
        store.update_offline_tournament_user(4, {
            "round": 1,
            "dataVersion": 1,
            "tournamentData": "unsafe-first-round-client-blob",
            "progressDataVersion": 1,
            "progressData": "AAAAAA==",
        })
        store.create_match({"squadId": 1, "type": "OFFLINE", "tournamentId": 4, "customData1": -863122704})

        before = int(store.credits()["credits"])
        document = {
            "endReason": "WIN",
            "myRating": 10,
            "opponentRating": 10,
            "items": [],
            "myMatchStats": {
                "goals": 2,
                "shotsOnTarget": 9,
                "successfulTackles": 24,
                "corners": 1,
                "cleansheets": 0,
                "passingPercentage": 82,
                "possessionPercentage": 53,
                "manOfTheMatch": 1,
                "fouls": 3,
                "yellowCards": 2,
                "redCards": 0,
                "offsides": 3,
            },
            "opponentMatchStats": {
                "goals": 1,
                "shotsOnTarget": 3,
                "successfulTackles": 12,
                "corners": 0,
                "cleansheets": 0,
                "passingPercentage": 91,
                "possessionPercentage": 47,
                "manOfTheMatch": 0,
                "fouls": 2,
                "yellowCards": 0,
                "redCards": 0,
                "offsides": 0,
            },
            "matchData": "beta2259-gold-cup-round1",
        }
        response = store.settle_match_end(document)
        after = int(store.credits()["credits"])

        require(response.get("endReason") == "WIN", f"wrong end reason: {response}")
        require(int(response.get("secondsPlayed", 0)) == 5400, f"completed seconds wrong: {response}")
        require(int(response.get("matchDifficulty", 0)) == 3, f"Gold Cup difficulty was not restored: {response}")
        require(int(response.get("completionAward", 0)) == 325, f"completion award missing: {response}")
        require(int(response.get("skillAward", 0)) > 0, f"skill award missing: {response}")
        require(int(response.get("rewardCoins", 0)) == after - before, "DestroyMatch reward does not match wallet delta")
        require(int(response.get("credits", -1)) == after, "DestroyMatch credits are stale")
        require(int(response.get("tournamentId", 0)) == 4, f"tournament identity missing: {response}")
        require(int(response.get("tournamentRound", 0)) == 2, f"round did not advance in response: {response}")

        with closing(sqlite3.connect(db)) as con:
            con.row_factory = sqlite3.Row
            state = con.execute(
                "SELECT current_round,won FROM beta_offline_tournaments WHERE persona_id=1000001 AND tournament_id=4"
            ).fetchone()
            progress = con.execute(
                "SELECT round_value,tournament_data,progress_data FROM beta_tournament_progress "
                "WHERE persona_id=1000001 AND tournament_id=4"
            ).fetchone()
        require(state is not None and int(state["current_round"]) == 1 and int(state["won"]) == 0,
                f"canonical tournament state wrong: {dict(state) if state else None}")
        require(progress is not None and int(progress["round_value"]) == 2,
                f"wire tournament round was not persisted: {dict(progress) if progress else None}")
        require(store.offline_tournament_user_list().get("tournamentId") == [4],
                f"resumable tournament was not advertised: {store.offline_tournament_user_list()}")
        require(int(store.offline_tournament_user(4).get("round", 0)) == 2,
                f"tournament user did not resume round 2: {store.offline_tournament_user(4)}")
        fut_user = store.ensure_fut_user()
        require(int(fut_user.get("credits", -1)) == after and int(fut_user.get("coins", -1)) == after,
                f"/user balance cache was not refreshed: {fut_user}")

        # A loss in round 2 should eliminate/reset the knockout without touching
        # the already-earned regular match wallet from the previous win.
        store.create_match({"squadId": 1, "type": "OFFLINE", "tournamentId": 4})
        loss = store.settle_match_end({
            "endReason": "LOSS",
            "myMatchStats": {"goals": 0},
            "opponentMatchStats": {"goals": 1},
            "matchData": "beta2259-gold-cup-round2-loss",
        })
        require(int(loss.get("matchDifficulty", 0)) == 3, f"round-2 difficulty wrong: {loss}")
        require(store.offline_tournament_user_list().get("tournamentId") == [],
                "loss did not reset knockout resume state")

        # Reproduce both captured failure boundaries: re-entering a saved round
        # 2 and saving a newly-created round 3.  The server must persist each
        # complete opaque buffer for future GETs without reflecting it into the
        # immediate PUT response.
        round2_tournament_data = "captured-round2-tournament-data"
        round2_progress_data = "KAAAAAAAAAACAAAATUNJAAAAAAAAAAAAAAAAAAkAAAABAAAACQAAAAAAAAA="
        with patch.dict(os.environ, {"FIFA14_TOURNAMENT_UPDATE_ACK": "auto"}):
            round2_ack = store.update_offline_tournament_user(2, {
                "round": 2,
                "dataVersion": 1,
                "tournamentData": round2_tournament_data,
                "progressDataVersion": 1,
                "progressData": round2_progress_data,
            })
        require(round2_ack == {"tournamentId": 2},
                f"round-2 PUT did not use the minimal acknowledgement: {round2_ack}")
        persisted_round2 = store.offline_tournament_user(2)
        require(int(persisted_round2.get("round", 0)) == 2,
                f"round-2 progress was not retained: {persisted_round2}")
        require(persisted_round2.get("tournamentData") == round2_tournament_data,
                "round-2 tournamentData was lost while minimizing the acknowledgement")
        require(persisted_round2.get("progressData") == round2_progress_data,
                "round-2 progressData was lost while minimizing the acknowledgement")

        round3_tournament_data = "captured-round3-tournament-data"
        round3_progress_data = "KAAAAAAAAAADAAAATUNJAAAAAAAAAAAAAAAAAAoAAAABAAAABAAAAAAAAAA="
        with patch.dict(os.environ, {"FIFA14_TOURNAMENT_UPDATE_ACK": "auto"}):
            round3_ack = store.update_offline_tournament_user(3, {
                "round": 3,
                "dataVersion": 1,
                "tournamentData": round3_tournament_data,
                "progressDataVersion": 1,
                "progressData": round3_progress_data,
            })
        require(round3_ack == {"tournamentId": 3},
                f"round-3 PUT did not use the minimal acknowledgement: {round3_ack}")
        persisted_round3 = store.offline_tournament_user(3)
        require(int(persisted_round3.get("round", 0)) == 3,
                f"round-3 progress was not retained: {persisted_round3}")
        require(persisted_round3.get("tournamentData") == round3_tournament_data,
                "round-3 tournamentData was lost while minimizing the acknowledgement")
        require(persisted_round3.get("progressData") == round3_progress_data,
                "round-3 progressData was lost while minimizing the acknowledgement")

        with patch.dict(os.environ, {"FIFA14_TOURNAMENT_UPDATE_ACK": "echo"}):
            legacy_ack = store.update_offline_tournament_user(3, {
                "round": 3,
                "dataVersion": 1,
                "tournamentData": round3_tournament_data,
                "progressDataVersion": 1,
                "progressData": round3_progress_data,
            })
        require(legacy_ack.get("tournamentData") == round3_tournament_data,
                "legacy echo comparison mode no longer returns the submitted payload")

    # Exact protocol regression from the user's capture: component 28 command 2
    # must be typed success, followed by ResultNotification command 114.
    rpc = build_shared_blaze_bootstrap_response(GAME_REPORTING_COMPONENT, 2, b"")
    require(rpc is not None and rpc[1] == "game-reporting-submit-offline-success" and rpc[2] == 0,
            f"offline game-report RPC remains observation-only: {rpc}")
    request_payload = tdf_u32(b"GRID", 111) + tdf_u32(b"GRID", 222)
    require(extract_tdf_varint_last(request_payload, b"GRID") == 222, "nested GRID extractor did not select terminal report id")
    body = build_game_reporting_result_notification_body(222)
    fields = {str(row.get("tag")): row.get("value") for row in decode_tdf_document(body)}
    require(int(fields.get("EROR", -1)) == 0, f"report notification EROR wrong: {fields}")
    require(bool(fields.get("FNL")) is True, f"report notification is not final: {fields}")
    require(int(fields.get("GHID", -1)) == 222 and int(fields.get("GRID", -1)) == 222,
            f"report ids wrong: {fields}")
    frame = build_fire_notification(GAME_REPORTING_COMPONENT, GAME_REPORTING_RESULT_NOTIFICATION, body)
    length, component, command, error, frame_type, options, sequence = struct.unpack(">HHHHBBH", frame[:12])
    require(length == len(body) and component == 28 and command == 114 and error == 0 and frame_type == 0x20,
            "ResultNotification FIRE header wrong")

    print(json.dumps({
        "status": "ok",
        "build": "2.41.1-beta2.25.9",
        "goldCupRound1Difficulty": 3,
        "roundAfterWin": 2,
        "completionAward": 325,
        "walletMatchesDestroyMatch": True,
        "round2UpdateAck": "minimal",
        "round2ResumeDataPersisted": True,
        "round3UpdateAck": "minimal",
        "round3ResumeDataPersisted": True,
        "gameReporting": {"component": 28, "submitOfflineCommand": 2, "resultNotification": 114},
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
