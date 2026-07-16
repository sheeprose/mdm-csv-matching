from __future__ import annotations

import argparse
import json
from itertools import product
from pathlib import Path

import joblib
import pandas as pd
from sklearn.metrics import accuracy_score, precision_recall_fscore_support, roc_auc_score

from mdm_matching.train import lexical_similarity_score, rank_score


def load_pairs(data_dir: Path, split: str) -> pd.DataFrame:
    return pd.read_csv(data_dir / f"{split}.csv")


def component_scores(model_bundle: dict, pairs: pd.DataFrame) -> dict[str, pd.Series]:
    features = model_bundle["feature_builder"].transform_pairs(pairs)
    feature_columns = model_bundle.get("feature_columns") or list(features.columns)
    selected = features.copy()
    for column in feature_columns:
        if column not in selected.columns:
            selected[column] = 0.0
    selected = selected[list(feature_columns)]

    lexical = lexical_similarity_score(features)
    xgb = model_bundle["xgb_model"].predict_proba(selected)[:, 1]
    mlp = model_bundle["mlp_model"].predict_proba(selected)[:, 1]
    rank_fusion = (rank_score(xgb) + rank_score(mlp) + rank_score(lexical)) / 3
    return {
        "rank": pd.Series(rank_fusion),
        "xgb": pd.Series(xgb),
        "mlp": pd.Series(mlp),
        "lexical": pd.Series(lexical),
    }


def combine(scores: dict[str, pd.Series], weights: dict[str, float]) -> pd.Series:
    return (
        weights["rank"] * scores["rank"]
        + weights["xgb"] * scores["xgb"]
        + weights["mlp"] * scores["mlp"]
        + weights["lexical"] * scores["lexical"]
    )


def metrics(y_true: pd.Series, scores: pd.Series, threshold: float) -> dict[str, float | int]:
    predictions = (scores >= threshold).astype(int)
    precision, recall, f1, _ = precision_recall_fscore_support(y_true, predictions, average="binary", zero_division=0)
    return {
        "threshold": threshold,
        "accuracy": float(accuracy_score(y_true, predictions)),
        "precision": float(precision),
        "recall": float(recall),
        "f1": float(f1),
        "auc": float(roc_auc_score(y_true, scores)),
        "predicted_positive": int(predictions.sum()),
    }


def best_threshold(y_true: pd.Series, scores: pd.Series) -> dict[str, float | int]:
    labels = y_true.to_numpy()
    score_values = scores.to_numpy()
    best_threshold_value = 0.05
    best_f1 = -1.0
    for step in range(5, 96):
        threshold = step / 100
        predictions = score_values >= threshold
        tp = int(((labels == 1) & predictions).sum())
        fp = int(((labels == 0) & predictions).sum())
        fn = int(((labels == 1) & ~predictions).sum())
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        if f1 > best_f1:
            best_f1 = f1
            best_threshold_value = threshold
    return metrics(y_true, scores, best_threshold_value)


def weight_grid(step: int = 5):
    units = 100 // step
    for rank, xgb, mlp in product(range(units + 1), repeat=3):
        lexical = units - rank - xgb - mlp
        if lexical < 0:
            continue
        yield {
            "rank": rank * step / 100,
            "xgb": xgb * step / 100,
            "mlp": mlp * step / 100,
            "lexical": lexical * step / 100,
        }


def tune(data_dir: Path, model_path: Path) -> dict[str, object]:
    model_bundle = joblib.load(model_path)
    valid = load_pairs(data_dir, "valid")
    test = load_pairs(data_dir, "test")
    valid_y = valid["label"].astype(int)
    test_y = test["label"].astype(int)
    valid_components = component_scores(model_bundle, valid)
    test_components = component_scores(model_bundle, test)

    best: dict[str, object] | None = None
    for weights in weight_grid():
        valid_scores = combine(valid_components, weights)
        current = best_threshold(valid_y, valid_scores)
        if best is None or float(current["f1"]) > float(best["valid"]["f1"]):
            best = {"weights": weights, "valid": current}
    if best is None:
        raise ValueError("no weights evaluated")

    test_scores = combine(test_components, best["weights"])
    return {
        "data_dir": str(data_dir),
        "model_path": str(model_path),
        "best_weights": best["weights"],
        "valid_best": best["valid"],
        "test_at_valid_best_threshold": metrics(test_y, test_scores, float(best["valid"]["threshold"])),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("datasets/structured_amazon_google"))
    parser.add_argument("--model-path", type=Path, default=Path("models/bm25/mdm_matcher.joblib"))
    args = parser.parse_args()
    print(json.dumps(tune(args.data_dir, args.model_path), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
