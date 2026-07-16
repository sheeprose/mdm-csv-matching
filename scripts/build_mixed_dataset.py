from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


PAIR_COLUMNS = [
    "_id",
    "label",
    "table1.id",
    "table2.id",
    "table1.title",
    "table2.title",
    "table1.manufacturer",
    "table2.manufacturer",
    "table1.price",
    "table2.price",
]


def load_prefixed_table(data_dir: Path, filename: str, prefix: str) -> pd.DataFrame:
    table = pd.read_csv(data_dir / filename)
    table = table[["id", "title", "manufacturer", "price"]].copy()
    table["id"] = prefix + ":" + table["id"].astype(str)
    return table


def load_prefixed_pairs(data_dir: Path, split: str, prefix: str) -> pd.DataFrame:
    pairs = pd.read_csv(data_dir / f"{split}.csv")
    pairs = pairs[PAIR_COLUMNS].copy()
    pairs["_id"] = prefix + ":" + pairs["_id"].astype(str)
    pairs["table1.id"] = prefix + ":" + pairs["table1.id"].astype(str)
    pairs["table2.id"] = prefix + ":" + pairs["table2.id"].astype(str)
    return pairs


def build_mixed_dataset(inputs: list[tuple[str, Path, float]], output_dir: Path, random_state: int) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    table_a_frames: list[pd.DataFrame] = []
    table_b_frames: list[pd.DataFrame] = []
    split_frames: dict[str, list[pd.DataFrame]] = {"train": [], "valid": [], "test": []}

    for prefix, data_dir, train_fraction in inputs:
        table_a_frames.append(load_prefixed_table(data_dir, "tableA.csv", prefix))
        table_b_frames.append(load_prefixed_table(data_dir, "tableB.csv", prefix))
        for split in split_frames:
            pairs = load_prefixed_pairs(data_dir, split, prefix)
            if split == "train" and train_fraction < 1.0:
                positive = pairs[pairs["label"].astype(int) == 1]
                negative = pairs[pairs["label"].astype(int) == 0]
                pairs = pd.concat(
                    [
                        positive.sample(frac=train_fraction, random_state=random_state),
                        negative.sample(frac=train_fraction, random_state=random_state),
                    ],
                    ignore_index=True,
                ).sample(frac=1.0, random_state=random_state)
            split_frames[split].append(pairs)

    pd.concat(table_a_frames, ignore_index=True).to_csv(output_dir / "tableA.csv", index=False)
    pd.concat(table_b_frames, ignore_index=True).to_csv(output_dir / "tableB.csv", index=False)
    for split, frames in split_frames.items():
        pd.concat(frames, ignore_index=True).to_csv(output_dir / f"{split}.csv", index=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--amazon-dir", type=Path, default=Path("datasets/structured_amazon_google"))
    parser.add_argument("--dn2-dir", type=Path, default=Path("data/dn2"))
    parser.add_argument("--output-dir", type=Path, default=Path("structured_mixed"))
    parser.add_argument("--dn2-train-fraction", type=float, default=1.0)
    parser.add_argument("--random-state", type=int, default=42)
    args = parser.parse_args()
    build_mixed_dataset(
        [("amazon", args.amazon_dir, 1.0), ("dn2", args.dn2_dir, max(0.0, min(args.dn2_train_fraction, 1.0)))],
        args.output_dir,
        args.random_state,
    )
    print(f"Wrote mixed dataset to {args.output_dir}")


if __name__ == "__main__":
    main()
