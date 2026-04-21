from brokerledger.ingest.normalize import normalize_merchant


def test_strips_bank_prefix_and_location():
    got = normalize_merchant("CARD PAYMENT TO TESCO STORES 1234 LONDON GB")
    assert "CARD PAYMENT" not in got
    assert "LONDON" not in got
    assert "TESCO STORES" in got


def test_removes_long_digit_runs():
    got = normalize_merchant("NETFLIX.COM 123456789")
    assert "123456789" not in got
    assert "NETFLIX" in got


def test_preserves_strong_signal_tokens():
    got = normalize_merchant("DIRECT DEBIT COUNCIL TAX ACME BOROUGH 0345")
    assert got.startswith("COUNCIL TAX")


def test_handles_contactless_prefix_and_amount_free_description():
    got = normalize_merchant("CONTACTLESS PAYMENT SAINSBURYS ONLINE")
    assert "CONTACTLESS" not in got
    assert "SAINSBURYS" in got


def test_empty_input():
    assert normalize_merchant("") == ""


def test_capping_length():
    got = normalize_merchant("A" * 200)
    assert len(got) <= 60


def test_pocket_money_preserved_as_strong_token():
    assert normalize_merchant("POCKET MONEY") == "POCKET MONEY"


def test_allowance_preserved_as_strong_token():
    assert normalize_merchant("ALLOWANCE").startswith("ALLOWANCE")


def test_empty_fallback_uses_raw_description():
    # An input whose only meaningful bytes would otherwise be scrubbed
    # to empty should fall back to an uppercase collapsed copy so the
    # merchant register has a non-empty key to learn from. Previously
    # all such rows collided on "".
    got = normalize_merchant("ref: 99999999999")
    assert got  # non-empty
    # Even zero-signal descriptions end up with *some* key rather than "".
    # (The content can be either the preserved raw or a reasonable tail;
    # the important invariant is non-emptiness.)
