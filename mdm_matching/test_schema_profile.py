from __future__ import annotations

import unittest

import pandas as pd

from mdm_matching.schema_profile import analyze_table_pair, apply_schema_profile, exact_header_schema_profile, has_exact_column_headers
from mdm_matching.service import build_candidate_pairs, score_candidates


class SchemaProfileTest(unittest.TestCase):
    def test_rejects_product_table_against_publication_table(self) -> None:
        product = pd.DataFrame(
            {
                "id": [1],
                "title": ["adobe photoshop"],
                "manufacturer": ["adobe"],
                "price": [99.0],
            }
        )
        publication = pd.DataFrame(
            {
                "id": [1],
                "title": ["database systems"],
                "authors": ["c j date"],
                "venue": ["sigmod"],
                "year": [1999],
            }
        )

        profile = analyze_table_pair(product, publication)

        self.assertFalse(profile["related"])
        self.assertEqual(profile["domain_a"], "product")
        self.assertEqual(profile["domain_b"], "publication")

    def test_publication_schema_gets_dynamic_weights(self) -> None:
        left = pd.DataFrame(
            {
                "id": [1],
                "title": ["initiation of crazes in polystyrene"],
                "authors": ["as argon jg hannoosh"],
                "venue": ["phil mag"],
                "year": [1992],
            }
        )
        right = pd.DataFrame(
            {
                "id": [2],
                "title": ["initiation of crazes in polystyrene"],
                "authors": ["a s argon j g hannoosh"],
                "venue": ["phil mag"],
                "year": [1992],
            }
        )

        profile = analyze_table_pair(left, right)
        master = apply_schema_profile(left, profile, "tableA")
        incoming = apply_schema_profile(right, profile, "tableB")
        pairs = build_candidate_pairs(master, incoming, schema_profile=profile)
        scored = score_candidates(pairs, {}, schema_profile=profile)

        self.assertTrue(profile["related"])
        self.assertEqual(profile["entity_type"], "publication")
        self.assertEqual(set(profile["weights"]), {"title", "authors", "venue", "year"})
        self.assertGreater(float(scored.iloc[0]["confidence"]), 0.7)

    def test_beer_schema_is_accepted(self) -> None:
        left = pd.DataFrame(
            {
                "id": [1],
                "Beer_Name": ["trappistes rochefort 10"],
                "Brew_Factory_Name": ["brasserie de rochefort"],
                "Style": ["quadrupel"],
                "ABV": [11.3],
            }
        )
        right = pd.DataFrame(
            {
                "id": [2],
                "Beer_Name": ["trappistes rochefort 10"],
                "Brew_Factory_Name": ["brasserie rochefort"],
                "Style": ["quadrupel"],
                "ABV": [11.3],
            }
        )

        profile = analyze_table_pair(left, right)

        self.assertTrue(profile["related"])
        self.assertEqual(profile["entity_type"], "beer")
        self.assertEqual(profile["column_roles"]["Beer_Name"], "primary_name")
        self.assertEqual(profile["column_roles"]["Brew_Factory_Name"], "creator_or_brand")
        self.assertEqual(
            [column["output_column"] for column in profile["master_columns"]],
            ["Beer_Name", "Brew_Factory_Name", "Style", "ABV"],
        )

    def test_unknown_identical_schema_is_accepted_generically(self) -> None:
        left = pd.DataFrame(
            {
                "row_id": [1, 2],
                "Track": ["song alpha", "song beta"],
                "Artist": ["aa", "bb"],
                "Album": ["x", "y"],
                "LengthSec": [210, 190],
            }
        )
        right = pd.DataFrame(
            {
                "row_id": [3, 4],
                "Track": ["song alpha remix", "song gamma"],
                "Artist": ["aa", "cc"],
                "Album": ["x", "z"],
                "LengthSec": [211, 200],
            }
        )

        profile = analyze_table_pair(left, right)

        self.assertTrue(profile["related"])
        self.assertEqual(profile["entity_type"], "unknown")
        self.assertEqual(profile["column_roles"]["Track"], "primary_name")
        self.assertIn("Track", profile["weights"])

    def test_exact_column_headers_are_always_schema_compatible(self) -> None:
        left = pd.DataFrame(
            {
                "id": [1],
                "Opaque_Field": ["abc"],
                "Another_Field": ["x"],
            }
        )
        right = pd.DataFrame(
            {
                "id": [2],
                "Opaque_Field": ["def"],
                "Another_Field": ["y"],
            }
        )

        profile = analyze_table_pair(left, right)

        self.assertTrue(profile["related"])
        self.assertEqual(profile["confidence"], 0.98)
        self.assertIn("exactly the same column headers", profile["reason"])

    def test_strict_header_match_requires_order_case_and_characters(self) -> None:
        self.assertTrue(has_exact_column_headers(["id", "Name"], ["id", "Name"]))
        self.assertFalse(has_exact_column_headers(["id", "Name"], ["Name", "id"]))
        self.assertFalse(has_exact_column_headers(["id", "Name"], ["id", "name"]))
        self.assertFalse(has_exact_column_headers(["id", "Name "], ["id", "Name"]))

        profile = exact_header_schema_profile(["id", "Beer_Name", "Brew_Factory_Name", "Style", "ABV"])

        self.assertEqual(profile["source"], "strict_header")
        self.assertEqual(profile["entity_type"], "beer")
        self.assertEqual(profile["column_mapping"]["Beer_Name"], "Beer_Name")


if __name__ == "__main__":
    unittest.main()
