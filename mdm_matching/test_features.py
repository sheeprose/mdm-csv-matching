from __future__ import annotations

import unittest

import pandas as pd

from mdm_matching.features import PairFeatureBuilder


class PairFeatureBuilderTest(unittest.TestCase):
    def test_builds_bm25_and_product_token_features(self) -> None:
        builder = PairFeatureBuilder().fit(
            [
                "Microsoft Office Professional 2007 Upgrade",
                "Microsoft Office Professional 2007 Win CD",
                "Microsoft Office Standard 2007",
            ]
        )
        features = builder.transform_pairs(
            pd.DataFrame(
                [
                    {
                        "table1.title": "Microsoft Office Professional 2007 Upgrade",
                        "table2.title": "Microsoft Office Professional 2007 Win CD",
                        "table1.manufacturer": "Microsoft",
                        "table2.manufacturer": "Microsoft Corporation",
                        "table1.price": 399.95,
                        "table2.price": 399.99,
                    }
                ]
            )
        )

        self.assertIn("title_bm25_similarity", features.columns)
        self.assertIn("important_token_jaccard", features.columns)
        self.assertIn("number_token_jaccard", features.columns)
        self.assertGreater(float(features.loc[0, "title_bm25_similarity"]), 0.0)
        self.assertGreater(float(features.loc[0, "important_token_jaccard"]), 0.5)
        self.assertEqual(float(features.loc[0, "number_token_jaccard"]), 1.0)

    def test_number_token_feature_penalizes_different_versions(self) -> None:
        builder = PairFeatureBuilder().fit(
            [
                "QuickBooks Premier 2007",
                "QuickBooks Premier 2008",
            ]
        )
        features = builder.transform_pairs(
            pd.DataFrame(
                [
                    {
                        "table1.title": "QuickBooks Premier 2007",
                        "table2.title": "QuickBooks Premier 2008",
                        "table1.manufacturer": "Intuit",
                        "table2.manufacturer": "Intuit",
                        "table1.price": 199.0,
                        "table2.price": 199.0,
                    }
                ]
            )
        )

        self.assertEqual(float(features.loc[0, "number_token_jaccard"]), 0.0)
        self.assertEqual(float(features.loc[0, "number_token_exact_match"]), 0.0)


if __name__ == "__main__":
    unittest.main()
