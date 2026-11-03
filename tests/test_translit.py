import pytest

from bot.translit import convert, convert_chars, TUTUQ


@pytest.mark.parametrize("src, expected", [
    # --- the original question
    ("Ўздаверлойиҳа", "Özdavyerloyiha"),
    ("ЎЗДАВЕРЛОЙИҲА", "ÖZDAVYERLOYIHA"),
    ("Ўздавергеодезкадастр", "Özdavyergeodezkadastr"),
    ("Ўзсувлойиҳа", "Özsuvloyiha"),
    ("давер", "davyer"),      # дав+ер — compound heuristic applies
    ("кетди", "ketdi"),
    ("Республика", "Respublika"),
    # --- е rule
    ("ер", "yer"),
    ("поезд", "poyezd"),
    ("съезд", "syezd"),
    ("тегирмон", "tegirmon"),
    ("Ельцин", "Yeltsin"),
    ("премьера", "premyera"),
    # --- ц rule
    ("лицей", "litsey"),
    ("акция", "aksiya"),
    ("цирк", "sirk"),
    ("концерт", "konsert"),
    # --- ъ / ь
    ("маъно", "ma" + TUTUQ + "no"),
    ("альбом", "albom"),
    ("бульон", "bulyon"),
    # --- new letters
    ("шаҳар", "şahar"),
    ("чақмоқ", "çaqmoq"),
    ("ғоя", "ğoya"),
    ("ўғил", "öğil"),
    ("Шаҳар", "Şahar"),
    ("ШАҲАР", "ŞAHAR"),
    # --- case of multi-letter outputs
    ("Ёш", "Yoş"),
    ("ЁШ", "YOŞ"),
    ("Ё", "Yo"),
    ("Юлдуз", "Yulduz"),
    # --- exceptions
    ("сентябрь", "sentabr"),
    ("Сентябрь", "Sentabr"),
    ("Ташкент", "Toşkent"),
    # --- old Latin -> new
    ("Oʻzbekiston", "Özbekiston"),
    ("O'zbekiston", "Özbekiston"),
    ("O‘zbekiston", "Özbekiston"),
    ("gʻoya", "ğoya"),
    ("shahar", "şahar"),
    ("Shahar", "Şahar"),
    ("SHAHAR", "ŞAHAR"),
    ("chaqmoq", "çaqmoq"),
    ("mashhur", "maşhur"),
    ("Is'hoq", "Ishoq"),
    ("ma'no", "ma" + TUTUQ + "no"),
    ("ta’lim", "ta" + TUTUQ + "lim"),
    ("sentyabr", "sentabr"),
    # --- already new: untouched
    ("Özbekiston şahar", "Özbekiston şahar"),
    # --- mixed, punctuation, numbers
    ("Салом, dunyo! 2026-yil.", "Salom, dunyo! 2026-yil."),
    ("'salom'", "'salom'"),          # quotes stay quotes
])
def test_convert(src, expected):
    assert convert(src) == expected


def test_char_mapping_length():
    s = "Ўздаверлойиҳа shahar oʻzbek"
    assert len(convert_chars(s)) == len(s)


def test_run_boundary_safe():
    # "по" + "езд" split across docx runs must still give poyezd
    s = "поезд"
    parts = convert_chars(s)
    assert "".join(parts[:2]) + "".join(parts[2:]) == "poyezd"


from bot.translit import MODE_CYR, MODE_LAT, MODE_AUTO


@pytest.mark.parametrize("mode, src, expected", [
    (MODE_CYR, "Салом shahar oʻzbek", "Salom shahar oʻzbek"),
    (MODE_LAT, "Салом shahar oʻzbek", "Салом şahar özbek"),
    (MODE_AUTO, "Салом shahar oʻzbek", "Salom şahar özbek"),
    (MODE_CYR, "сентябрь sentyabr", "sentabr sentyabr"),
    (MODE_LAT, "сентябрь sentyabr", "сентябрь sentabr"),
])
def test_modes(mode, src, expected):
    assert convert(src, mode) == expected
