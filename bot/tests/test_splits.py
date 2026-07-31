import pytest

from splits import SplitError, convert_shares, fill_remainder


class TestFillRemainder:
    def test_starbucks_case(self):
        # "10900 por Mauri, 16700 por Errazquin y el resto para mí" (46800 ARS)
        assert fill_remainder(4680000, [1090000, 1670000, None]) == [
            1090000,
            1670000,
            1920000,
        ]

    def test_exact_sum_no_holes(self):
        assert fill_remainder(10000, [5000, 3000, 2000]) == [5000, 3000, 2000]

    def test_non_divisible_remainder_spread(self):
        assert fill_remainder(100, [None, None, None]) == [34, 33, 33]

    def test_percentage_basis_points(self):
        # "yo 70% y el resto entre Benja y Fer"
        assert fill_remainder(10000, [7000, None, None]) == [7000, 1500, 1500]

    def test_sum_exceeds_total(self):
        with pytest.raises(SplitError) as e:
            fill_remainder(1000, [800, 300, None])
        assert e.value.reason == "sum_exceeds_total"

    def test_sum_short_without_hole(self):
        with pytest.raises(SplitError) as e:
            fill_remainder(1000, [800, 100])
        assert e.value.reason == "sum_mismatch"

    def test_zero_remainder(self):
        with pytest.raises(SplitError) as e:
            fill_remainder(1000, [600, 400, None])
        assert e.value.reason == "zero_remainder"

    def test_zero_share(self):
        with pytest.raises(SplitError) as e:
            fill_remainder(1000, [1000, 0])
        assert e.value.reason == "zero_share"

    def test_empty(self):
        with pytest.raises(SplitError) as e:
            fill_remainder(1000, [])
        assert e.value.reason == "empty"


class TestConvertShares:
    def test_identity_rate(self):
        assert convert_shares([500, 300, 200], 1000, 1.0) == [500, 300, 200]

    def test_ars_like_rate_sums_exactly(self):
        rate = 1 / 1565  # ~ARS blue
        orig = [1090000, 1670000, 1920000]  # cents, sum 46800 ARS
        total = round(sum(orig) * rate)
        shares = convert_shares(orig, total, rate)
        assert sum(shares) == total
        assert len(shares) == 3

    def test_diff_absorbed_by_largest(self):
        rate = 1 / 3
        orig = [100, 100, 100]  # each rounds to 33, sum 99
        shares = convert_shares(orig, 101, rate)
        assert sum(shares) == 101
        assert max(shares) == 35  # the (first) largest took the +2 diff
        assert sorted(shares) == [33, 33, 35]

    @pytest.mark.parametrize("rate", [1 / 1565, 1 / 950, 0.00106, 1.0, 0.5])
    @pytest.mark.parametrize(
        "orig", [[1090000, 1670000, 1920000], [1, 999999], [333, 333, 334]]
    )
    def test_sum_invariant(self, rate, orig):
        total = round(sum(orig) * rate)
        if total <= 0:
            pytest.skip("degenerate total")
        try:
            shares = convert_shares(orig, total, rate)
        except SplitError:
            return  # rounding wiped a tiny share: acceptable, just not silent
        assert sum(shares) == total

    def test_rounding_wiped_share(self):
        # A 1-cent part at a harsh rate rounds to 0 and must not pass silently.
        with pytest.raises(SplitError) as e:
            convert_shares([1, 1000000], round(1000001 / 1565), 1 / 1565)
        assert e.value.reason == "rounding_wiped_share"
