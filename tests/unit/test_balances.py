import datetime as dt

from financas.domain.services.balances import AnchorPoint, balance_on

D = dt.date


def test_no_anchor_means_unavailable() -> None:
    assert balance_on([], [(D(2026, 7, 1), 500)], D(2026, 7, 10)) is None


def test_anchor_on_the_same_day_is_the_balance() -> None:
    anchors = [AnchorPoint(D(2026, 7, 10), 100_00)]
    assert balance_on(anchors, [(D(2026, 7, 10), -30_00)], D(2026, 7, 10)) == 100_00


def test_movements_after_the_anchor_are_added() -> None:
    anchors = [AnchorPoint(D(2026, 7, 1), 100_00)]
    moves = [
        (D(2026, 7, 1), 999_00),  # on the anchor date: already inside the anchor
        (D(2026, 7, 2), -30_00),
        (D(2026, 7, 5), 10_00),
        (D(2026, 7, 11), -1_00),  # after the target date
    ]
    assert balance_on(anchors, moves, D(2026, 7, 10)) == 80_00


def test_anchor_after_the_date_walks_backwards() -> None:
    anchors = [AnchorPoint(D(2026, 7, 10), 100_00)]
    moves = [
        (D(2026, 7, 5), -30_00),
        (D(2026, 7, 10), -7_00),  # inside the anchor
        (D(2026, 7, 3), 50_00),  # before the target date: untouched
    ]
    # balance at 2026-07-04 = anchor - movements in (07-04, 07-10] = 100 + 30 + 7
    assert balance_on(anchors, moves, D(2026, 7, 4)) == 137_00


def test_nearest_anchor_wins() -> None:
    anchors = [AnchorPoint(D(2026, 6, 1), 10_00), AnchorPoint(D(2026, 7, 9), 500_00)]
    assert balance_on(anchors, [], D(2026, 7, 10)) == 500_00
    assert balance_on(anchors, [], D(2026, 6, 5)) == 10_00


def test_tie_prefers_the_earlier_anchor() -> None:
    anchors = [AnchorPoint(D(2026, 7, 5), 100_00), AnchorPoint(D(2026, 7, 15), 900_00)]
    assert balance_on(anchors, [], D(2026, 7, 10)) == 100_00


def test_negative_balance_is_allowed() -> None:
    anchors = [AnchorPoint(D(2026, 7, 1), -50_00)]
    assert balance_on(anchors, [(D(2026, 7, 2), -10_00)], D(2026, 7, 3)) == -60_00
