"""Unit tests for the ROC date parser, covering every format seen in the data."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataPipeline.rocDateParser import (  # noqa: E402
    buildingAgeYears, parseRocDate, parseRocDateRange,
)


def testRoc7TransactionDate():
    assert parseRocDate("1150601") == "2026-06-01"
    assert parseRocDate("1150605") == "2026-06-05"


def testRoc7WithLeadingZeroYear():
    # 068 -> ROC 68 -> 1979
    assert parseRocDate("0680306") == "1979-03-06"


def testRoc5YearMonthOnly():
    # 06701 -> ROC 67, month 01 -> 1978-01-01 (day defaults to 1)
    assert parseRocDate("06701") == "1978-01-01"


def testCjkDate():
    assert parseRocDate("97年3月19日") == "2008-03-19"
    assert parseRocDate("97年3月") == "2008-03-01"


def testEmptyAndMalformed():
    assert parseRocDate("") is None
    assert parseRocDate(None) is None
    assert parseRocDate("garbage") is None
    assert parseRocDate("1159999") is None  # invalid month/day


def testRange():
    start, end = parseRocDateRange("1150501~1160430")
    assert start == "2026-05-01"
    assert end == "2027-04-30"


def testRangeSingleValue():
    start, end = parseRocDateRange("1150501")
    assert start == "2026-05-01"
    assert end is None


def testBuildingAge():
    assert buildingAgeYears("2016-06-01", "2026-06-01") == 10.0
    # completion after sale (new build sold pre-completion) -> age 0, not missing
    assert buildingAgeYears("2027-01-01", "2026-06-01") == 0.0
    assert buildingAgeYears(None, "2026-06-01") is None
