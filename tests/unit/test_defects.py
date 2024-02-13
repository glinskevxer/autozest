from autozest.defects import DefectClass, mechanical_classes, paperwork_classes


def test_the_mechanical_classes_are_what_a_drivetrain_inspection_finds() -> None:
    assert DefectClass.ENGINE.is_mechanical
    assert DefectClass.TRANSMISSION.is_mechanical
    assert DefectClass.SUSPENSION.is_mechanical
    assert not DefectClass.TITLE.is_mechanical
    assert not DefectClass.BODY_COSMETIC.is_mechanical


def test_the_paperwork_classes_live_on_the_title() -> None:
    assert DefectClass.TITLE.is_paperwork
    assert DefectClass.BRANDING.is_paperwork
    assert DefectClass.ODOMETER.is_paperwork
    assert not DefectClass.ENGINE.is_paperwork


def test_cosmetic_damage_was_there_to_be_looked_at() -> None:
    assert not DefectClass.BODY_COSMETIC.survives_inspection
    assert not DefectClass.MISSING_EQUIPMENT.survives_inspection


def test_a_rolled_odometer_was_not_there_to_be_looked_at() -> None:
    assert DefectClass.ODOMETER.survives_inspection
    assert DefectClass.STRUCTURAL.survives_inspection
    assert DefectClass.FLOOD.survives_inspection


def test_the_helper_sets_agree_with_the_properties() -> None:
    assert mechanical_classes() == {c for c in DefectClass if c.is_mechanical}
    assert paperwork_classes() == {c for c in DefectClass if c.is_paperwork}


def test_every_class_has_a_stable_value() -> None:
    values = [c.value for c in DefectClass]
    assert len(values) == len(set(values))
    assert all(v.islower() for v in values)
