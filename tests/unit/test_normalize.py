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
