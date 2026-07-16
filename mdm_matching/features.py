from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from mdm_matching.preprocess import normalize_manufacturer, normalize_price, normalize_text


IMPORTANT_TOKEN_STOPWORDS = {
    "a",
    "an",
    "and",
    "bundle",
    "cd",
    "complete",
    "download",
    "dvd",
    "edition",
    "for",
    "in",
    "mac",
    "new",
    "of",
    "package",
    "pack",
    "pc",
    "software",
    "the",
    "to",
    "version",
    "win",
    "windows",
    "with",
}
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def token_jaccard(left: str, right: str) -> float:
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens and not right_tokens:
        return 1.0
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def price_similarity(left: Any, right: Any) -> float:
    left_price = normalize_price(left)
    right_price = normalize_price(right)
    if left_price is None or right_price is None:
        return 0.5
    denominator = max(left_price, right_price, 1.0)
    return max(0.0, 1.0 - abs(left_price - right_price) / denominator)


def manufacturer_similarity(left: Any, right: Any) -> float:
    left_value = normalize_manufacturer(left)
    right_value = normalize_manufacturer(right)
    if not left_value or not right_value:
        return 0.5
    return 1.0 if left_value == right_value else token_jaccard(left_value, right_value)


def length_ratio(left: str, right: str) -> float:
    left_len = len(left)
    right_len = len(right)
    if left_len == 0 and right_len == 0:
        return 1.0
    if left_len == 0 or right_len == 0:
        return 0.0
    return min(left_len, right_len) / max(left_len, right_len)


def important_tokens(title: str) -> set[str]:
    return {token for token in title.split() if token not in IMPORTANT_TOKEN_STOPWORDS and len(token) > 1}


def number_tokens(title: str) -> set[str]:
    return set(NUMBER_RE.findall(title))


def token_containment(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def exact_set_match(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return float(left == right)


class PairFeatureBuilder:
    def __init__(self) -> None:
        self.char_vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=1)
        self.word_vectorizer = TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=1)
        self.bm25_idf: dict[str, float] = {}
        self.bm25_average_doc_length = 1.0
        self.bm25_k1 = 1.5
        self.bm25_b = 0.75

    def fit(self, titles: list[str]) -> "PairFeatureBuilder":
        normalized = [normalize_text(title) for title in titles]
        self.char_vectorizer.fit(normalized)
        self.word_vectorizer.fit(normalized)
        tokenized = [title.split() for title in normalized]
        document_count = len(tokenized)
        document_frequency: Counter[str] = Counter()
        total_length = 0
        for tokens in tokenized:
            total_length += len(tokens)
            document_frequency.update(set(tokens))
        self.bm25_average_doc_length = total_length / document_count if document_count else 1.0
        self.bm25_idf = {
            token: math.log(1.0 + (document_count - frequency + 0.5) / (frequency + 0.5))
            for token, frequency in document_frequency.items()
        }
        return self

    def transform_pairs(self, pairs: pd.DataFrame) -> pd.DataFrame:
        self._ensure_bm25_defaults()
        left_titles = pairs["table1.title"].map(normalize_text).tolist()
        right_titles = pairs["table2.title"].map(normalize_text).tolist()

        left_char = self.char_vectorizer.transform(left_titles)
        right_char = self.char_vectorizer.transform(right_titles)
        left_word = self.word_vectorizer.transform(left_titles)
        right_word = self.word_vectorizer.transform(right_titles)

        char_cos = np.asarray(cosine_similarity(left_char, right_char).diagonal())
        word_cos = np.asarray(cosine_similarity(left_word, right_word).diagonal())

        rows = []
        for index, row in pairs.reset_index(drop=True).iterrows():
            title_left = left_titles[index]
            title_right = right_titles[index]
            left_important = important_tokens(title_left)
            right_important = important_tokens(title_right)
            left_numbers = number_tokens(title_left)
            right_numbers = number_tokens(title_right)
            rows.append(
                {
                    "char_tfidf_cosine": float(char_cos[index]),
                    "word_tfidf_cosine": float(word_cos[index]),
                    "title_token_jaccard": token_jaccard(title_left, title_right),
                    "title_bm25_similarity": self._symmetric_bm25_similarity(title_left, title_right),
                    "important_token_jaccard": token_jaccard(" ".join(sorted(left_important)), " ".join(sorted(right_important))),
                    "important_token_containment": token_containment(left_important, right_important),
                    "number_token_jaccard": token_jaccard(" ".join(sorted(left_numbers)), " ".join(sorted(right_numbers))),
                    "number_token_exact_match": exact_set_match(left_numbers, right_numbers),
                    "manufacturer_similarity": manufacturer_similarity(
                        row.get("table1.manufacturer"), row.get("table2.manufacturer")
                    ),
                    "price_similarity": price_similarity(row.get("table1.price"), row.get("table2.price")),
                    "title_length_ratio": length_ratio(title_left, title_right),
                    "same_first_token": float(
                        bool(title_left.split() and title_right.split() and title_left.split()[0] == title_right.split()[0])
                    ),
                    "price_log_delta": _price_log_delta(row.get("table1.price"), row.get("table2.price")),
                }
            )
        return pd.DataFrame(rows)

    def _ensure_bm25_defaults(self) -> None:
        if not hasattr(self, "bm25_idf"):
            self.bm25_idf = {}
        if not hasattr(self, "bm25_average_doc_length"):
            self.bm25_average_doc_length = 1.0
        if not hasattr(self, "bm25_k1"):
            self.bm25_k1 = 1.5
        if not hasattr(self, "bm25_b"):
            self.bm25_b = 0.75

    def _symmetric_bm25_similarity(self, left: str, right: str) -> float:
        left_tokens = left.split()
        right_tokens = right.split()
        if not left_tokens and not right_tokens:
            return 1.0
        if not left_tokens or not right_tokens:
            return 0.0
        left_score = self._bm25_score(left_tokens, right_tokens)
        right_score = self._bm25_score(right_tokens, left_tokens)
        denominator = max(self._bm25_score(left_tokens, left_tokens), self._bm25_score(right_tokens, right_tokens), 1e-9)
        return max(0.0, min(1.0, (left_score + right_score) / (2 * denominator)))

    def _bm25_score(self, query_tokens: list[str], document_tokens: list[str]) -> float:
        if not query_tokens or not document_tokens:
            return 0.0
        term_frequency = Counter(document_tokens)
        document_length = len(document_tokens)
        average_length = self.bm25_average_doc_length or 1.0
        score = 0.0
        for token in set(query_tokens):
            frequency = term_frequency.get(token, 0)
            if frequency == 0:
                continue
            idf = self.bm25_idf.get(token, 0.0)
            denominator = frequency + self.bm25_k1 * (1 - self.bm25_b + self.bm25_b * document_length / average_length)
            score += idf * (frequency * (self.bm25_k1 + 1)) / denominator
        return float(score)


def _price_log_delta(left: Any, right: Any) -> float:
    left_price = normalize_price(left)
    right_price = normalize_price(right)
    if left_price is None or right_price is None:
        return 0.0
    return abs(math.log1p(left_price) - math.log1p(right_price))
