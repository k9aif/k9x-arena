# SPDX-License-Identifier: Apache-2.0
# K9-AIF Framework
"""Publish chosen matches from the lab database to the showcase database.

The public showcase (ARENA_MODE=showcase) has its own SQLite file and never
reads the lab's, so a bug there can't expose or change lab data -- and you
decide exactly which matches are public.

    python -m arena.publish --from LAB.db --to SHOWCASE.db 10 11
    python -m arena.publish --from LAB.db --to SHOWCASE.db --list
    python -m arena.publish --from LAB.db --to SHOWCASE.db --remove 9

Publishing a match again replaces its showcase copy. Only completed
matches can be published. Uploads and the screening cache never leave the
lab (they may hold private documents).
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from typing import Iterable, List

# Tables keyed by match_id, copied as-is. grades hang off runs (run_id).
_MATCH_TABLES = ("tasks", "runs", "stars", "router_audit", "reports", "reviews", "events", "judge_calibration")


def _init_target(path: str) -> None:
    os.environ["ARENA_DB_PATH"] = path
    from arena import store  # schema lives in store.init()
    store.init()


def _cols(con: sqlite3.Connection, table: str) -> List[str]:
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]


def _remove(dst: sqlite3.Connection, match_id: int) -> None:
    dst.execute("DELETE FROM grades WHERE run_id IN (SELECT id FROM runs WHERE match_id=?)", (match_id,))
    for t in _MATCH_TABLES:
        dst.execute(f"DELETE FROM {t} WHERE match_id=?", (match_id,))
    dst.execute("DELETE FROM matches WHERE id=?", (match_id,))


def _copy_rows(src, dst, table: str, where: str, args) -> int:
    cols = [c for c in _cols(src, table) if c in set(_cols(dst, table))]
    rows = src.execute(f"SELECT {', '.join(cols)} FROM {table} WHERE {where}", args).fetchall()
    if rows:
        dst.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", rows)
    return len(rows)


def publish(src_path: str, dst_path: str, match_ids: Iterable[int]) -> List[str]:
    if os.path.abspath(src_path) == os.path.abspath(dst_path):
        raise SystemExit("--from and --to must be different files")
    _init_target(dst_path)
    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    dst = sqlite3.connect(dst_path)
    out = []
    with dst:
        for mid in match_ids:
            m = src.execute("SELECT status FROM matches WHERE id=?", (mid,)).fetchone()
            if not m:
                out.append(f"#{mid}: not found in the lab database, skipped")
                continue
            if m[0] != "completed":
                out.append(f"#{mid}: status '{m[0]}', only completed matches are published, skipped")
                continue
            _remove(dst, mid)
            _copy_rows(src, dst, "matches", "id=?", (mid,))
            n = {t: _copy_rows(src, dst, t, "match_id=?", (mid,)) for t in _MATCH_TABLES}
            n["grades"] = _copy_rows(src, dst, "grades", "run_id IN (SELECT id FROM runs WHERE match_id=?)", (mid,))
            out.append(f"#{mid}: published ({n['runs']} answers, {n['grades']} grades)")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="src", required=True, help="lab arena.db")
    ap.add_argument("--to", dest="dst", required=True, help="showcase arena.db")
    ap.add_argument("--list", action="store_true", help="list what the showcase has now")
    ap.add_argument("--remove", nargs="*", type=int, default=[], help="unpublish these match ids")
    ap.add_argument("ids", nargs="*", type=int, help="match ids to publish")
    a = ap.parse_args(argv)
    for line in publish(a.src, a.dst, a.ids) if a.ids else []:
        print(line)
    if a.remove:
        _init_target(a.dst)
        dst = sqlite3.connect(a.dst)
        with dst:
            for mid in a.remove:
                _remove(dst, mid)
                print(f"#{mid}: removed from the showcase")
    if a.list or not (a.ids or a.remove):
        _init_target(a.dst)
        for mid, name, cont, status in sqlite3.connect(a.dst).execute(
                "SELECT id, suite_name, contenders, status FROM matches ORDER BY id"):
            print(f"#{mid} {name} {cont} {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
