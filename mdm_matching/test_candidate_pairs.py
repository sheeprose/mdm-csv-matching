from __future__ import annotations

import unittest

import pandas as pd

import mdm_matching.service as service
from mdm_matching.features import PairFeatureBuilder
from mdm_matching.preprocess import preprocess_table


class CandidatePairGenerationTest(unittest.TestCase):
    def test_uses_blocking_before_jaccard_scoring(self) -> None:
        master = preprocess_table(
            pd.DataFrame(
                [
                    {"id": f"a{i}", "title": f"alpha{i}", "manufacturer": f"maker-a-{i}", "price": i}
                    for i in range(10)
                ]
            )
        )
        incoming = preprocess_table(
            pd.DataFrame(
                [
                    {"id": f"b{i}", "title": f"beta{i}", "manufacturer": f"maker-b-{i}", "price": i}
                    for i in range(10)
                ]
            )
        )

        original = service.token_jaccard
        call_count = 0

        def counted(left: str, right: str) -> float:
            nonlocal call_count
            call_count += 1
            return original(left, right)

        service.token_jaccard = counted
        try:
            pairs = service.build_candidate_pairs(master, incoming)
        finally:
            service.token_jaccard = original

        self.assertTrue(pairs.empty)
        self.assertLess(call_count, 20)

    def test_mutual_top_pair_above_review_floor_auto_merges(self) -> None:
        scored = pd.DataFrame(
            [
                {
                    "table1.id": "a1",
                    "table2.id": "b1",
                    "table1.title": "Microsoft Word 2007 Upgrade",
                    "table2.title": "Microsoft Word 2007 Version Upgrade",
                    "table1.manufacturer": "microsoft",
                    "table2.manufacturer": "microsoft",
                    "table1.price": 109.95,
                    "table2.price": 109.95,
                    "confidence": 0.74,
                },
                {
                    "table1.id": "a1",
                    "table2.id": "b2",
                    "table1.title": "Microsoft Word 2007 Upgrade",
                    "table2.title": "Microsoft Excel 2007 Upgrade",
                    "table1.manufacturer": "microsoft",
                    "table2.manufacturer": "microsoft",
                    "table1.price": 109.95,
                    "table2.price": 109.95,
                    "confidence": 0.62,
                },
            ]
        )

        plan = service.build_entity_cluster_plan(scored, threshold=0.9)

        self.assertEqual(len(plan["auto_merged_clusters"]), 1)
        self.assertEqual(plan["auto_merged_clusters"][0]["routing_decision"], "mutual_top_auto_merge")
        self.assertEqual(plan["matched_master_ids"], {"a1"})
        self.assertEqual(plan["matched_incoming_ids"], {"b1"})

    def test_score_candidates_respects_saved_feature_columns(self) -> None:
        class RecordingModel:
            def __init__(self) -> None:
                self.columns: list[str] = []

            def predict_proba(self, features: pd.DataFrame):
                self.columns = list(features.columns)
                return [[0.2, 0.8]]

        model = RecordingModel()
        candidate_pairs = pd.DataFrame(
            [
                {
                    "table1.id": "a1",
                    "table2.id": "b1",
                    "table1.title": "Microsoft Office 2007",
                    "table2.title": "Microsoft Office 2007",
                    "table1.manufacturer": "Microsoft",
                    "table2.manufacturer": "Microsoft",
                    "table1.price": 100.0,
                    "table2.price": 100.0,
                }
            ]
        )
        feature_builder = PairFeatureBuilder().fit(["Microsoft Office 2007"])

        service.score_candidates(
            candidate_pairs,
            {
                "feature_builder": feature_builder,
                "xgb_model": model,
                "mlp_model": None,
                "feature_columns": ["char_tfidf_cosine", "word_tfidf_cosine"],
            },
        )

        self.assertEqual(model.columns, ["char_tfidf_cosine", "word_tfidf_cosine"])


if __name__ == "__main__":
    unittest.main()
