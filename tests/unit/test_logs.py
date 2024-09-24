import io
import logging
from datetime import date, datetime

import pytest

from autozest.inspection.announcements import Light
from autozest.logs import LEVELS, Log, configure, render, render_value
from autozest.money import Money


def test_each_kind_of_thing_renders_the_same_way_everywhere() -> None:
    assert render_value(None) == "-"
    assert render_value(Money.parse("8200.00")) == "8200.00"
    assert render_value(Light.YELLOW) == "yellow"
    assert render_value(True) == "true"
    assert render_value(date(2024, 3, 12)) == "2024-03-12"
    assert render_value(datetime(2024, 3, 12, 14, 30)) == "2024-03-12T14:30:00"
    assert render_value(214) == "214"


def test_a_value_with_a_space_is_quoted() -> None:
    assert render_value("Ridgeline Motors") == '"Ridgeline Motors"'
    assert render_value("simple") == "simple"
    assert render_value("") == '""'


def test_a_value_with_a_quote_is_escaped() -> None:
    assert render_value('he said "no"') == '"he said \\"no\\""'


def test_a_newline_never_makes_it_into_a_line() -> None:
    rendered = render_value("first\nsecond")
    assert "\n" not in rendered
    assert rendered == '"first second"'


def test_an_event_reads_as_name_then_fields() -> None:
    line = render("sale.opened", {"sale": "S-20240312", "runs": 214})
    assert line == "sale.opened sale=S-20240312 runs=214"


def test_an_event_with_no_fields() -> None:
    assert render("cli.start", {}) == "cli.start"


def test_configuring_and_writing() -> None:
    stream = io.StringIO()
    configure("info", stream)
    Log().info("sale.opened", sale="S-20240312", gross=Money.parse("8200.00"))
    written = stream.getvalue()
    assert "info" in written
    assert "sale.opened sale=S-20240312 gross=8200.00" in written


def test_a_level_below_the_threshold_says_nothing() -> None:
    stream = io.StringIO()
    configure("warning", stream)
    log = Log()
    log.info("quiet", x=1)
    assert stream.getvalue() == ""
    log.warning("loud", x=1)
    assert "loud" in stream.getvalue()


def test_the_levels_are_the_ones_people_expect() -> None:
    assert set(LEVELS) == {"debug", "info", "warning", "error"}
    configure("debug", io.StringIO())
    assert Log().is_enabled("debug")
    assert Log().level == logging.DEBUG


def test_a_level_nobody_has_heard_of() -> None:
    with pytest.raises(ValueError, match="not a log level"):
        configure("chatty")


def test_configuring_twice_does_not_double_up() -> None:
    stream = io.StringIO()
    configure("info", stream)
    configure("info", stream)
    Log().info("once", x=1)
    assert stream.getvalue().count("once") == 1
