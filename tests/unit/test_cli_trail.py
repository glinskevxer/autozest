from autozest.cli.trail import READ_ONLY, is_worth_recording, subject_of


def test_a_reporting_command_is_never_recorded() -> None:
    for group in READ_ONLY:
        assert not is_worth_recording(group, "anything")


def test_a_writing_command_is_recorded() -> None:
    assert is_worth_recording("consign", "register")
    assert is_worth_recording("block", "fall")
    assert is_worth_recording("title", "receive")


def test_a_read_only_command_inside_a_writing_group_is_not() -> None:
    assert not is_worth_recording("consign", "show")
    assert not is_worth_recording("settle", "trial-balance")
    assert not is_worth_recording("arb", "assess")


def test_nothing_typed_records_nothing() -> None:
    assert not is_worth_recording(None, None)
    assert not is_worth_recording("consign", None)


def test_the_subject_is_the_first_positional() -> None:
    assert subject_of(["1HGCM82633A004352", "--keys", "2"]) == "1HGCM82633A004352"
    assert subject_of(["S-20240312", "A001"]) == "S-20240312"


def test_a_command_with_no_subject_says_so() -> None:
    assert subject_of([]) == "-"
    assert subject_of(["--dry-run"]) == "-"
