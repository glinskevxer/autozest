import pytest

from autozest.errors import ConfigError, ValidationError
from autozest.money import Money
from autozest.settlement.fees import (
    DEFAULT_BANDS,
    SCHEDULE_VERSION,
    FeeBand,
    FeeSchedule,
    Service,
    from_config,
)

CARD = FeeSchedule()


def test_the_card_has_a_version_on_it() -> None:
    assert CARD.version == SCHEDULE_VERSION


def test_a_band_is_flat_across_its_whole_width() -> None:
    assert CARD.buyer_fee(Money.parse("400.00")) == Money.parse("75.00")
    assert CARD.buyer_fee(Money.parse("999.99")) == Money.parse("75.00")


def test_a_dollar_over_the_top_of_a_band_costs_more() -> None:
    assert CARD.buyer_fee(Money.parse("1000.00")) == Money.parse("125.00")


def test_both_sides_pay() -> None:
    assert CARD.seller_fee(Money.parse("8200.00")) == Money.parse("235.00")
    assert CARD.buyer_fee(Money.parse("8200.00")) == Money.parse("345.00")
    assert CARD.total_fees(Money.parse("8200.00")) == Money.parse("580.00")


def test_the_top_band_is_open_ended() -> None:
    assert CARD.buyer_fee(Money.parse("50000.00")) == Money.parse("850.00")
    assert CARD.buyer_fee(Money.parse("240000.00")) == Money.parse("850.00")
    assert CARD.band_for(Money.parse("240000.00")).upper is None


def test_a_free_unit_still_lands_in_the_first_band() -> None:
    assert CARD.buyer_fee(Money.zero()) == Money.parse("75.00")


def test_a_negative_amount_is_refused() -> None:
    with pytest.raises(ValidationError, match="negative"):
        CARD.band_for(Money(-1))


def test_a_band_describes_itself() -> None:
    assert "and up" in str(DEFAULT_BANDS[-1])
    assert "to 999.99" in str(DEFAULT_BANDS[0])


def test_the_bands_climb() -> None:
    fees = [CARD.buyer_fee(Money.parse(f"{amount}.00")) for amount in (500, 1500, 6000, 25000)]
    assert fees == sorted(fees)
    assert len(set(fees)) == 4


def test_a_re_tier_is_detected() -> None:
    assert CARD.band_changed(Money.parse("1100.00"), Money.parse("900.00"))
    assert not CARD.band_changed(Money.parse("1100.00"), Money.parse("1050.00"))


def test_the_services_have_published_prices() -> None:
    assert CARD.service(Service.TRANSPORT) == Money.parse("185.00")
    assert CARD.service(Service.NO_SALE) == Money.parse("110.00")


def test_which_side_a_service_falls_on() -> None:
    assert Service.POST_SALE_INSPECTION.falls_on_buyer
    assert Service.TRANSPORT.falls_on_buyer
    assert not Service.NO_SALE.falls_on_buyer
    assert not Service.DETAIL.falls_on_buyer


def test_a_card_missing_a_service_says_so() -> None:
    trimmed = FeeSchedule(services={Service.GATE: Money.parse("25.00")})
    assert trimmed.service(Service.GATE) == Money.parse("25.00")
    with pytest.raises(ConfigError, match="no price for transport"):
        trimmed.service(Service.TRANSPORT)


def test_a_schedule_needs_bands() -> None:
    with pytest.raises(ConfigError, match="at least one band"):
        FeeSchedule(bands=())


def test_the_last_band_has_to_be_open_ended() -> None:
    closed = FeeBand(Money.parse("100.00"), Money.parse("10.00"), Money.parse("10.00"))
    with pytest.raises(ConfigError, match="open ended"):
        FeeSchedule(bands=(closed,))


def test_only_the_last_band_may_be_open_ended() -> None:
    with pytest.raises(ConfigError, match="only the last"):
        FeeSchedule(
            bands=(
                FeeBand(None, Money.parse("10.00"), Money.parse("10.00")),
                FeeBand(None, Money.parse("20.00"), Money.parse("20.00")),
            )
        )


def test_bands_out_of_order_are_refused() -> None:
    with pytest.raises(ConfigError, match="out of order"):
        FeeSchedule(
            bands=(
                FeeBand(Money.parse("500.00"), Money.parse("20.00"), Money.parse("20.00")),
                FeeBand(Money.parse("100.00"), Money.parse("10.00"), Money.parse("10.00")),
                FeeBand(None, Money.parse("30.00"), Money.parse("30.00")),
            )
        )


def test_configuration_moves_a_service_price() -> None:
    card = from_config({"transport": "220.00", "version": "2025.1"})
    assert card.service(Service.TRANSPORT) == Money.parse("220.00")
    assert card.service(Service.DETAIL) == Money.parse("95.00")
    assert card.version == "2025.1"


def test_configuration_replaces_the_whole_band_table() -> None:
    card = from_config(
        {
            "bands": [
                {"upper": "999.99", "buyer": "90.00", "seller": "70.00"},
                {"buyer": "400.00", "seller": "300.00"},
            ]
        }
    )
    assert card.buyer_fee(Money.parse("500.00")) == Money.parse("90.00")
    assert card.buyer_fee(Money.parse("50000.00")) == Money.parse("400.00")
    assert len(card.bands) == 2


def test_a_service_nobody_has_heard_of() -> None:
    with pytest.raises(ConfigError, match="not a service on the card"):
        from_config({"valeting": "40.00"})


def test_a_band_table_that_is_not_a_list() -> None:
    with pytest.raises(ConfigError, match="list of tables"):
        from_config({"bands": "lots"})


def test_a_band_missing_a_side() -> None:
    with pytest.raises(ConfigError, match="missing seller"):
        from_config({"bands": [{"buyer": "90.00"}]})
    with pytest.raises(ConfigError, match="should be a table"):
        from_config({"bands": ["90.00"]})


def test_overlaying_twice_keeps_the_first_change() -> None:
    first = from_config({"transport": "220.00"})
    second = from_config({"detail": "120.00"}, first)
    assert second.service(Service.TRANSPORT) == Money.parse("220.00")
    assert second.service(Service.DETAIL) == Money.parse("120.00")
