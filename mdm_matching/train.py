from __future__ import annotations

import argparse
import json
import warnings
from pathlib import Path

import joblib
import pandas as pd
from sklearn.exceptions import ConvergenceWarning
from sklearn.metrics import average_precision_score, f1_score, precision_recall_fscore_support, roc_auc_score
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from mdm_matching.features import PairFeatureBuilder

try:
    from xgboost import XGBClassifier
except Exception:  # pragma: no cover - fallback for environments without xgboost
    XGBClassifier = None


def build_xgboost_model():
    if XGBClassifier is None:
        from sklearn.ensemble import HistGradientBoostingClassifier

        return HistGradientBoostingClassifier(max_iter=160, learning_rate=0.05, random_state=42)
    return XGBClassifier(
        n_estimators=180,
        max_depth=4,
        learning_rate=0.04,
        subsample=0.9,
        colsample_bytree=0.9,
        eval_metric="logloss",
        random_state=42,
    )


def build_mlp_model() -> Pipeline:
    return Pipeline(
        steps=[
            ("scale", StandardScaler()),
            ("mlp", MLPClassifier(hidden_layer_sizes=(24, 12), solver="lbfgs", alpha=0.001, max_iter=1000, random_state=42)),
        ]
    )


def load_pairs(data_dir: Path, split: str) -> pd.DataFrame:
    return pd.read_csv(data_dir / f"{split}.csv")


def train(data_dir: Path, output_dir: Path) -> dict[str, float]:
    output_dir.mkdir(parents=True, exist_ok=True)
    train_pairs = load_pairs(data_dir, "train")
    valid_pairs = load_pairs(data_dir, "valid")
    table_a = pd.read_csv(data_dir / "tableA.csv")
    table_b = pd.read_csv(data_dir / "tableB.csv")

    feature_builder = PairFeatureBuilder().fit(
        table_a["title"].fillna("").astype(str).tolist() + table_b["title"].fillna("").astype(str).tolist()
    )
    x_train = feature_builder.transform_pairs(train_pairs)
    y_train = train_pairs["label"].astype(int)
    x_valid = feature_builder.transform_pairs(valid_pairs)
    y_valid = valid_pairs["label"].astype(int)

    xgb_model = build_xgboost_model()
    mlp_model = build_mlp_model()
    xgb_model.fit(x_train, y_train)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=RuntimeWarning)
        warnings.filterwarnings("ignore", category=ConvergenceWarning)
        mlp_model.fit(x_train, y_train)

    xgb_scores = xgb_model.predict_proba(x_valid)[:, 1]
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=RuntimeWarning)
        mlp_scores = mlp_model.predict_proba(x_valid)[:, 1]
    lexical_scores = lexical_similarity_score(x_valid)
    fused_scores = fuse_scores(xgb_scores, mlp_scores, lexical_scores)
    predictions = (fused_scores >= 0.9).astype(int)
    precision, recall, f1, _ = precision_recall_fscore_support(y_valid, predictions, average="binary", zero_division=0)

    metrics = {
        "roc_auc": float(roc_auc_score(y_valid, fused_scores)),
        "average_precision": float(average_precision_score(y_valid, fused_scores)),
        "precision_at_0_9": float(precision),
        "recall_at_0_9": float(recall),
        "f1_at_0_9": float(f1),
        "f1_best_threshold_proxy": float(f1_score(y_valid, (fused_scores >= 0.5).astype(int))),
    }

    joblib.dump(
        {
            "feature_builder": feature_builder,
            "xgb_model": xgb_model,
            "mlp_model": mlp_model,
            "feature_columns": list(x_train.columns),
            "metrics": metrics,
        },
        output_dir / "mdm_matcher.joblib",
    )
    (output_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return metrics


def rank_score(scores):
    return pd.Series(scores).rank(method="average", pct=True).to_numpy()


def lexical_similarity_score(features: pd.DataFrame):
    columns = [
        column
        for column in [
            "char_tfidf_cosine",
            "word_tfidf_cosine",
            "title_token_jaccard",
            "title_bm25_similarity",
            "important_token_jaccard",
            "important_token_containment",
        ]
        if column in features.columns
    ]
    return features[columns].mean(axis=1).to_numpy()


def fuse_scores(xgb_scores, mlp_scores, lexical_scores):
    rank_fusion = (rank_score(xgb_scores) + rank_score(mlp_scores) + rank_score(lexical_scores)) / 3
    return 0.45 * rank_fusion + 0.35 * xgb_scores + 0.10 * mlp_scores + 0.10 * lexical_scores


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("structured_amazon_google"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts"))
    args = parser.parse_args()
    metrics = train(args.data_dir, args.output_dir)
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
