from __future__ import annotations

import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path


def summarize(db_path: Path) -> None:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        links = [dict(row) for row in con.execute("SELECT entity_id, source_table, source_id FROM source_record_links")]
        counts = {
            table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ["entities", "golden_records", "source_record_links", "match_decisions", "entity_aliases"]
        }
    finally:
        con.close()

    by_entity: dict[int, list[dict]] = defaultdict(list)
    for link in links:
        by_entity[int(link["entity_id"])].append(link)

    shapes = Counter()
    for rows in by_entity.values():
        left = sum(1 for row in rows if row["source_table"] == "table1")
        right = sum(1 for row in rows if row["source_table"] == "table2")
        if left and right:
            if left == 1 and right == 1:
                shapes["merged_1x1"] += 1
            else:
                shapes["merged_multi"] += 1
        elif left:
            shapes["table1_only"] += 1
        elif right:
            shapes["table2_only"] += 1
        else:
            shapes["no_source_link"] += 1

    print(f"db={db_path}")
    print("counts", counts)
    print("entity_shapes", dict(shapes))
    print()


def main() -> None:
    for arg in sys.argv[1:]:
        summarize(Path(arg))


if __name__ == "__main__":
    main()
