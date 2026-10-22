"""Política genérica N6: umbrales absolutos, empates y abstenciones."""

import pytest

from chat.vote_store import majority_absolute


@pytest.mark.parametrize("members,threshold", [(2, 2), (3, 2), (5, 3), (10, 6)])
def test_absolute_majority(members: int, threshold: int) -> None:
    assert majority_absolute(0, members) == "rejected"
    assert majority_absolute(threshold - 1, members) == "rejected"
    assert majority_absolute(threshold, members) == "approved"
    assert majority_absolute(members, members) == "approved"
