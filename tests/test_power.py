from bench.caffeinate.power import parse

AC = """Now drawing from 'AC Power'
 -InternalBattery-0 (id=4653155)\t83%; AC attached; not charging present: true
"""
BATTERY = """Now drawing from 'Battery Power'
 -InternalBattery-0 (id=4653155)\t62%; discharging; 4:31 remaining present: true
"""
CHARGED = """Now drawing from 'AC Power'
 -InternalBattery-0 (id=4653155)\t100%; charged; 0:00 remaining present: true
"""


def test_ac():
    assert parse(AC) == ("AC", 83)


def test_battery():
    assert parse(BATTERY) == ("Battery", 62)


def test_charged():
    assert parse(CHARGED) == ("AC", 100)


def test_unparseable():
    assert parse("something unexpected") is None
    assert parse("Now drawing from 'UPS Power'\n 50%") is None
