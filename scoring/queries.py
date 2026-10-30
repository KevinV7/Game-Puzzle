"""Read-only queries and display helpers for the scoring pages.

``scoring`` builds on ``game`` (the score rule lives in ``game.score``); ``game``
never imports this module.
"""

from datetime import timedelta

_MICROSECOND = timedelta(microseconds=1)


def win_percent(won, played):
    """Won over played as a whole percent, halves rounded up. None when nothing was played."""
    if not played:
        return None
    return (200 * won + played) // (2 * played)


def average_seconds(total, played):
    """Mean of ``total`` (a timedelta) over ``played`` attempts, in whole seconds.

    Works on whole microseconds with integer arithmetic and rounds halves up.
    Python's ``round()`` would round halves to even (100.5 s would give 100).
    """
    if not played:
        return None
    microseconds = total // _MICROSECOND
    return (2 * microseconds + played * 1_000_000) // (2 * played * 1_000_000)


def format_clock(seconds):
    """``mm:ss`` with minutes never wrapped into hours."""
    return f"{seconds // 60:02d}:{seconds % 60:02d}"
