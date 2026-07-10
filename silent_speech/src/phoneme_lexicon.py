"""English phoneme lexicon + fuzzy phoneme-sequence -> word lookup.

Backs the phoneme-based decoding plan: the model predicts a (noisy) ARPAbet
phoneme sequence, and this module finds the words whose pronunciation best
matches it. Because predictions are noisy -- and because many phonemes are the
same *viseme* on the lips (p/b/m, etc.) -- matching is done by phoneme-level
edit distance (fuzzy), not exact lookup, returning ranked candidate words that a
language model can then disambiguate.

Data: CMU Pronouncing Dictionary (CMUdict), ~126k words, ARPAbet, free/
redistributable, bundled offline via the `cmudict` pip package.

    lex = PhonemeLexicon()
    lex.word_to_phonemes("three")      # -> [('TH', 'R', 'IY')]
    lex.words_for(["TH", "R", "IY"])   # exact: ['three']
    lex.lookup(["T", "R", "IY"], k=5)  # fuzzy: [('three', 0.83), ...]

ARPAbet (39 phones, stress stripped):
  vowels:     AA AE AH AO AW AY EH ER EY IH IY OW OY UH UW
  consonants: B CH D DH F G HH JH K L M N NG P R S SH T TH V W Y Z ZH
"""
from __future__ import annotations

import re

import cmudict

WORD_SEP = "|"      # word-boundary token used inside phoneme target sequences

_STRESS = re.compile(r"\d+$")


def _strip_stress(p: str) -> str:
    return _STRESS.sub("", p)


def _edit_distance(a: tuple, b: tuple) -> int:
    """Token-level Levenshtein distance between two phoneme sequences."""
    n, m = len(a), len(b)
    if n == 0:
        return m
    if m == 0:
        return n
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        cur = [i] + [0] * m
        ai = a[i - 1]
        for j in range(1, m + 1):
            cost = 0 if ai == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[m]


class PhonemeLexicon:
    def __init__(self, keep_stress: bool = False):
        self.keep_stress = keep_stress
        raw = cmudict.dict()
        self.word2prons: dict[str, list[tuple]] = {}
        self._exact: dict[tuple, list[str]] = {}
        self._by_len: dict[int, list[tuple]] = {}
        for word, prons in raw.items():
            plist = []
            for pron in prons:
                ph = tuple(p if keep_stress else _strip_stress(p) for p in pron)
                plist.append(ph)
                self._exact.setdefault(ph, []).append(word)
                self._by_len.setdefault(len(ph), []).append((word, ph))
            self.word2prons[word] = plist
        self.phonemes = sorted({p for ph in self._exact for p in ph})
        #: when set, fuzzy decode is limited to this closed vocabulary
        self._vocab: list[tuple] | None = None    # [(word, pron), ...]

    def word_to_phonemes(self, word: str) -> list[tuple]:
        """All known pronunciations of a word (may be several)."""
        return self.word2prons.get(word.lower().strip(), [])

    def restrict_vocab(self, words) -> int:
        """Limit fuzzy decode (lookup / decode_words) to a CLOSED vocabulary.

        For a fixed-vocabulary demo this is the single biggest accuracy win: a
        noisy phoneme guess maps to the nearest of a handful of trained words
        instead of the nearest of ~126k CMUdict words (which is how a one-phoneme
        slip turns into a completely unrelated word). `word_to_phonemes` and the
        exact lookups are unaffected. Pass a falsy value to clear (open vocab).
        Returns the number of (word, pronunciation) entries in the closed set.
        """
        if not words:
            self._vocab = None
            return 0
        vocab = []
        for w in words:
            for ph in self.word2prons.get(str(w).lower().strip(), []):
                vocab.append((str(w).lower().strip(), ph))
        self._vocab = vocab or None
        return len(vocab)

    def words_for(self, phones) -> list[str]:
        """Exact reverse lookup: words with this exact pronunciation."""
        return self._exact.get(tuple(phones), [])

    def decode_words(self, symbols, k: int = 1) -> list[str]:
        """Split a phoneme sequence on WORD_SEP, map each chunk to a word.

        Turns the CTC output (phonemes with word-boundary tokens) into a word
        sequence -- the sentence-style transcription. Unknown chunks become '?'.
        """
        words, chunk = [], []
        for s in list(symbols) + [WORD_SEP]:      # trailing sep flushes last word
            if s == WORD_SEP:
                if chunk:
                    cand = self.lookup(chunk, k=1)
                    words.append(cand[0][0] if cand else "?")
                    chunk = []
            else:
                chunk.append(s)
        return words

    def lookup(self, phones, k: int = 5, max_len_diff: int = 2):
        """Fuzzy: top-k words closest to `phones` by phoneme edit distance.

        Returns [(word, similarity)], similarity in [0,1] = 1 - dist/maxlen.
        With a closed vocab (see restrict_vocab) every trained word is scored;
        otherwise only CMUdict candidates within +/-max_len_diff phonemes are
        scored (speed).
        """
        q = tuple(phones)
        best: dict[str, int] = {}
        if self._vocab is not None:                # closed vocab: score them all
            candidates = self._vocab
        else:
            candidates = []
            for L in range(max(1, len(q) - max_len_diff),
                           len(q) + max_len_diff + 1):
                candidates.extend(self._by_len.get(L, []))
        for word, ph in candidates:
            d = _edit_distance(q, ph)
            if word not in best or d < best[word]:
                best[word] = d
        ranked = sorted(best.items(), key=lambda wd: (wd[1], len(wd[0])))
        out = []
        for word, d in ranked[:k]:
            denom = max(len(q), len(self.word2prons[word][0]), 1)
            out.append((word, round(1 - d / denom, 3)))
        return out


if __name__ == "__main__":
    lex = PhonemeLexicon()
    print(f"Loaded {len(lex.word2prons)} words, {len(lex.phonemes)} phonemes")
    print("phonemes:", " ".join(lex.phonemes), "\n")

    for w in ("three", "eight", "seven"):
        print(f"{w:>6} -> {lex.word_to_phonemes(w)}")

    print("\nExact reverse lookup:")
    print("  TH R IY   ->", lex.words_for(["TH", "R", "IY"]))
    print("  T UW      ->", lex.words_for(["T", "UW"]), "(homophones)")

    print("\nFuzzy lookup on a NOISY phoneme guess:")
    print("  guessed 'T R IY' (meant three) ->", lex.lookup(["T", "R", "IY"]))
    print("  viseme ambiguity 'B AE T'      ->", lex.lookup(["B", "AE", "T"]))
