import pytest

from bot.translit import MODE_TO_CYR, convert, convert_chars
from bot.ui import LABEL_TO_MODE, MODE_LABELS, keyboard


@pytest.mark.parametrize("src,expected", [
    ("Özbekiston", "Ўзбекистон"),
    ("Şahar köçalari, boğlarda quşlar", "Шаҳар кўчалари, боғларда қушлар"),
    ("Oʻzbekiston, shahar, choy, gʻisht", "Ўзбекистон, шаҳар, чой, ғишт"),   # 1995 Latin
    ("maʼno, taʼlim, sanʼat", "маъно, таълим, санъат"),
    ("yer, yoz, yulduz, yaxşi, poyezd", "ер, ёз, юлдуз, яхши, поезд"),
    ("ekran, poeziya, teatr", "экран, поэзия, театр"),
    ("Ishoq, maşhur", "Исҳоқ, машҳур"),                 # new text: sh is two letters
    ("TOŞKENT ŞAHRI", "ТОШКЕНТ ШАҲРИ"),
    ("Yozuv, YOZUV", "Ёзув, ЁЗУВ"),
    ("21-sentabr", "21-сентябрь"),
    ("Özdavyerloyiha", "Ўздаверлойиҳа"),
])
def test_to_cyr(src, expected):
    assert convert(src, MODE_TO_CYR) == expected


def test_to_cyr_keeps_links_and_cyrillic():
    s = "https://gov.uz @Ixtiyorivich Салом"
    assert convert(s, MODE_TO_CYR) == s


def test_to_cyr_one_output_per_char():
    s = "Oʻzbekiston shahri, yoʻl"
    assert len(convert_chars(s, MODE_TO_CYR)) == len(s)


def test_round_trip_common_text():
    cyr = "Ўзбекистон — гўзал ва бетакрор юрт. Шаҳар кўчалари эрталабдан гавжум."
    assert convert(convert(cyr), MODE_TO_CYR) == cyr


def test_keyboard_has_reverse_button():
    labels = [b.text for row in keyboard(MODE_TO_CYR).keyboard for b in row]
    assert f"✅ {MODE_LABELS[MODE_TO_CYR]}" in labels
    assert LABEL_TO_MODE[MODE_LABELS[MODE_TO_CYR]] == MODE_TO_CYR
