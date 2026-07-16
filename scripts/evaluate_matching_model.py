from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import pandas as pd
from sklearn.metrics import accuracy_score, precision_recall_fscore_support, roc_auc_score

from mdm_matching.train import fuse_scores, lexical_similarity_score


def load_pairs(data_dir: Path, split: str) -> pd.DataFrame:
    return pd.read_csv(data_dir / f"{split}.csv")


def score_pairs(model_bundle: dict, pairs: pd.DataFrame) -> pd.Series:
    feature_builder = model_bundle["feature_builder"]
    features = feature_builder.transform_pairs(pairs)
    feature_columns = model_bundle.get("feature_columns") or list(features.columns)
    selected = features.copy()
    for column in feature_columns:
        if column not in selected.columns:
            selected[column] = 0.0
    selected = selected[list(feature_columns)]

    lexical_scores = lexical_similarity_score(features)
    xgb_model = model_bundle.get("xgb_model")
    mlp_model = model_bundle.get("mlp_model")
    xgb_scores = xgb_model.predict_proba(selected)[:, 1] if xgb_model is not None else lexical_scores
    mlp_scores = mlp_model.predict_proba(selected)[:, 1] if mlp_model is not None else lexical_scores
    return pd.Series(fuse_scores(xgb_scores, mlp_scores, lexical_scores))


def classification_metrics(y_true: pd.Series, scores: pd.Series, threshold: float) -> dict[str, float | int]:
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


def find_best_threshold(y_true: pd.Series, scores: pd.Series) -> dict[str, float | int]:
    best: dict[str, float | int] | None = None
    for step in range(5, 96):
        threshold = step / 100
        current = classification_metrics(y_true, scores, threshold)
        if best is None or float(current["f1"]) > float(best["f1"]):
            best = current
    if best is None:
        raise ValueError("no thresholds evaluated")
    return best


def evaluate(data_dir: Path, model_path: Path) -> dict[str, object]:
    model_bundle = joblib.load(model_path)
    valid_pairs = load_pairs(data_dir, "valid")
    test_pairs = load_pairs(data_dir, "test")
    valid_y = valid_pairs["label"].astype(int)
    test_y = test_pairs["label"].astype(int)
    valid_scores = score_pairs(model_bundle, valid_pairs)
    test_scores = score_pairs(model_bundle, test_pairs)
    best_valid = find_best_threshold(valid_y, valid_scores)
    fixed_09_valid = classification_metrics(valid_y, valid_scores, 0.9)
    fixed_09_test = classification_metrics(test_y, test_scores, 0.9)
    tuned_test = classification_metrics(test_y, test_scores, float(best_valid["threshold"]))
    return {
        "data_dir": str(data_dir),
        "model_path": str(model_path),
        "valid_best": best_valid,
        "valid_at_0_9": fixed_09_valid,
        "test_at_valid_best_threshold": tuned_test,
        "test_at_0_9": fixed_09_test,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("datasets/structured_amazon_google"))
    parser.add_argument("--model-path", type=Path, default=Path("models/main/mdm_matcher.joblib"))
    args = parser.parse_args()
    print(json.dumps(evaluate(args.data_dir, args.model_path), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
