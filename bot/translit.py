"""
Transliteration into the 2026 Uzbek Latin alphabet (O'RQ-1184, 28 letters + tutuq belgisi).

Accepts any mix of:
  * Uzbek Cyrillic          (Ўздаверлойиҳа  -> Özdaverloyiha)
  * 1995 Uzbek Latin        (Oʻzbekiston    -> Özbekiston,  shahar -> şahar)
  * already-new Latin       (left untouched)

Design: `convert_chars(text)` returns one output string PER INPUT CHARACTER
(possibly empty, possibly several letters).  This lets callers that keep
formatting (docx runs) redistribute output back onto the original runs
without losing context-dependent rules at run boundaries.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

TUTUQ = "ʼ"            # ʼ  — official tutuq belgisi (modifier letter apostrophe)
_OKINA_MARKS = "ʻʼ‘’'`´"   # ʻ ʼ ‘ ’ ' ` ´  — everything people type for oʻ / gʻ / tutuq

# ---------------------------------------------------------------- Cyrillic
_CYR_SIMPLE = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "ж": "j", "з": "z",
    "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "x",
    "ҳ": "h", "қ": "q", "э": "e", "ё": "yo", "ю": "yu", "я": "ya",
    "ш": "ş", "ч": "ç", "ғ": "ğ", "ў": "ö",
    "ь": "",            # softness sign is dropped (альбом -> albom)
}
_CYR_VOWELS = set("аеёиоуэюяўАЕЁИОУЭЮЯЎ")
_CYR_LETTERS = set(_CYR_SIMPLE) | {"е", "ц", "ъ"}

# ---------------------------------------------------------------- old Latin
_OLD_LATIN_DIGRAPH = {"o": "ö", "g": "ğ"}        # o + ʻ,  g + ʻ
_OLD_LATIN_H = {"s": "ş", "c": "ç"}              # s + h,  c + h

# ---------------------------------------------------------------- exceptions
_EXC_PATH = Path(__file__).with_name("exceptions.json")


def _load_exceptions() -> dict[str, str]:
    if _EXC_PATH.exists():
        with _EXC_PATH.open(encoding="utf-8") as f:
            return {k.lower(): v for k, v in json.load(f).items()}
    return {}


EXCEPTIONS = _load_exceptions()
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

# ---------------------------------------------------------------- compounds
_CMP_PATH = Path(__file__).with_name("compounds.json")


def _load_compounds() -> tuple[frozenset[str], tuple[str, ...]]:
    if not _CMP_PATH.exists():
        return frozenset(), ()
    with _CMP_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    prefixes = frozenset(p.lower() for p in data.get("prefixes", []))
    roots = tuple(sorted((r.lower() for r in data.get("e_roots", [])), key=len, reverse=True))
    return prefixes, roots


COMPOUND_PREFIXES, E_ROOTS = _load_compounds()
_MAX_PREFIX = max((len(p) for p in COMPOUND_PREFIXES), default=0)


def _segmentable(s: str) -> bool:
    """Can `s` be written as a concatenation of known compound prefixes?"""
    n = len(s)
    ok = [False] * (n + 1)
    ok[0] = True
    for i in range(1, n + 1):
        for j in range(max(0, i - _MAX_PREFIX), i):
            if ok[j] and s[j:i] in COMPOUND_PREFIXES:
                ok[i] = True
                break
    return ok[n]


def _e_starts_compound_root(text: str, i: int) -> bool:
    """е after a consonant, but it begins a known root inside a compound word
    (Ўз+дав+ер+лойиҳа) -> treat as word-initial -> 'ye'."""
    if not E_ROOTS or not COMPOUND_PREFIXES:
        return False
    # word start
    j = i
    while j > 0 and text[j - 1].isalpha():
        j -= 1
    if j == i:
        return False
    left = text[j:i].lower()
    if not _segmentable(left):
        return False
    rest = text[i:].lower()
    return any(rest.startswith(r) for r in E_ROOTS)


# ---------------------------------------------------------------- helpers
def _is_upper(ch: str) -> bool:
    return ch.isalpha() and ch == ch.upper() and ch != ch.lower()


def _word_is_caps(text: str, i: int) -> bool:
    """Decide whether the letter at `i` sits inside an ALL-CAPS word.

    Look at the nearest letter after it; if none, the nearest letter before it.
    Used to choose 'YO' vs 'Yo' for multi-letter outputs.
    """
    n = len(text)
    j = i + 1
    while j < n and text[j] in _OKINA_MARKS:
        j += 1
    if j < n and text[j].isalpha():
        return _is_upper(text[j])
    j = i - 1
    while j >= 0 and text[j] in _OKINA_MARKS:
        j -= 1
    if j >= 0 and text[j].isalpha():
        return _is_upper(text[j])
    return False            # single-letter word: "Ё" -> "Yo"


def _apply_case(out: str, text: str, i: int) -> str:
    src = text[i]
    if not _is_upper(src) or not out:
        return out
    if len(out) == 1:
        return out.upper()
    return out.upper() if _word_is_caps(text, i) else out[0].upper() + out[1:]


def _match_case_word(src: str, repl: str) -> str:
    if src.isupper() and len(src) > 1:
        return repl.upper()
    if src[0].isupper():
        return repl[0].upper() + repl[1:]
    return repl


def _prev_letter(text: str, i: int) -> str:
    return text[i - 1] if i > 0 else ""


def _is_cyr_e_iotated(text: str, i: int) -> bool:
    """е -> ye at word start, after a vowel, after ъ/ь; otherwise e."""
    prev = _prev_letter(text, i)
    if not prev or not prev.isalpha():
        return True
    if prev in _CYR_VOWELS or prev in "ъьЪЬ":
        return True
    return _e_starts_compound_root(text, i)


# ---------------------------------------------------------------- core
MODE_AUTO = "auto"      # Cyrillic + old Latin -> new Latin
MODE_CYR = "cyr"        # only Cyrillic letters are converted; Latin text untouched
MODE_LAT = "lat"        # only 1995-Latin digraphs are converted; Cyrillic untouched
MODES = (MODE_AUTO, MODE_CYR, MODE_LAT)


def _has_cyr(word: str) -> bool:
    return any(c.lower() in _CYR_LETTERS for c in word)


def convert_chars(text: str, mode: str = MODE_AUTO) -> list[str]:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}")
    n = len(text)
    out: list[str] = [""] * n
    done = [False] * n
    do_cyr = mode in (MODE_AUTO, MODE_CYR)
    do_lat = mode in (MODE_AUTO, MODE_LAT)

    # 1. whole-word exceptions (sentabr, oktabr, ...)
    if EXCEPTIONS:
        for m in _WORD_RE.finditer(text):
            w = m.group()
            repl = EXCEPTIONS.get(w.lower())
            if repl is None:
                continue
            if _has_cyr(w) and not do_cyr or not _has_cyr(w) and not do_lat:
                continue
            out[m.start()] = _match_case_word(w, repl)
            for k in range(m.start(), m.end()):
                done[k] = True

    # 2. character pass
    i = 0
    while i < n:
        if done[i]:
            i += 1
            continue
        ch = text[i]
        low = ch.lower()
        nxt = text[i + 1] if i + 1 < n else ""
        nxt_low = nxt.lower()

        # ---- Cyrillic
        if do_cyr and low in _CYR_LETTERS:
            if low == "е":
                res = "ye" if _is_cyr_e_iotated(text, i) else "e"
            elif low == "ц":
                prev = _prev_letter(text, i)
                res = "ts" if prev and (prev in _CYR_VOWELS or prev in "ьЬ") else "s"
            elif low == "ъ":
                res = "" if nxt_low == "е" else TUTUQ
            elif low == "ь":
                # бульон -> bulyon ; before е/ё/ю/я the y comes from the vowel itself
                res = "y" if nxt_low == "о" else ""
            else:
                res = _CYR_SIMPLE[low]
            out[i] = _apply_case(res, text, i)
            i += 1
            continue

        if not do_lat:
            out[i] = ch
            i += 1
            continue

        # ---- old Latin:  oʻ / gʻ  (any apostrophe-like mark)
        if low in _OLD_LATIN_DIGRAPH and nxt and nxt in _OKINA_MARKS:
            out[i] = _apply_case(_OLD_LATIN_DIGRAPH[low], text, i)
            out[i + 1] = ""
            i += 2
            continue

        # ---- old Latin:  sh / ch
        if low in _OLD_LATIN_H and nxt_low == "h":
            out[i] = _apply_case(_OLD_LATIN_H[low], text, i)
            out[i + 1] = ""
            i += 2
            continue

        # ---- old Latin:  s'h  (Isʼhoq)  -> plain s + h, apostrophe no longer needed
        if low == "s" and nxt and nxt in _OKINA_MARKS and i + 2 < n and text[i + 2].lower() == "h":
            out[i] = ch
            out[i + 1] = ""
            out[i + 2] = text[i + 2]
            i += 3
            continue

        # ---- tutuq between letters (ma'no, ta’lim) -> normalised ʼ
        if ch in _OKINA_MARKS and i > 0 and text[i - 1].isalpha() and nxt.isalpha():
            out[i] = TUTUQ
            i += 1
            continue

        out[i] = ch
        i += 1

    return out


def convert(text: str, mode: str = MODE_AUTO) -> str:
    return "".join(convert_chars(text, mode))


def looks_already_new(text: str) -> bool:
    """True when the text contains no Cyrillic and no 1995-era digraphs."""
    return convert(text) == text
