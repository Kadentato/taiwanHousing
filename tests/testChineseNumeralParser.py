"""Unit tests for the Chinese-numeral / floor parser."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataPipeline.chineseNumeralParser import parseChineseNumber, parseFloorCount  # noqa: E402


def testBasicNumbers():
    assert parseChineseNumber("一") == 1
    assert parseChineseNumber("九") == 9
    assert parseChineseNumber("十") == 10
    assert parseChineseNumber("十二") == 12
    assert parseChineseNumber("二十") == 20
    assert parseChineseNumber("二十四") == 24
    assert parseChineseNumber("一百零一") == 101


def testArabicPassthrough():
    assert parseChineseNumber("14") == 14


def testFloorCountStripsCounter():
    assert parseFloorCount("十五層") == 15
    assert parseFloorCount("五層") == 5
    assert parseFloorCount("二十四層") == 24


def testBasementNegative():
    assert parseFloorCount("地下二層") == -2


def testUnparseable():
    assert parseFloorCount("") is None
    assert parseFloorCount(None) is None
    assert parseFloorCount("全") is None
    assert parseFloorCount("見其他登記事項") is None
