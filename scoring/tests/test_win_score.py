"""The win-panel score and the leaderboard / profile share one formula."""

import json

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from game.tests.helpers import almost_solved, make_game, make_user, winning_tile
from scoring.queries import player_stats, top_ten


class WinPanelAgreesWithOtherPagesTests(TestCase):
    def test_the_points_of_a_winning_move_equal_the_leaderboard_and_profile_points(self):
        ana = make_user("ana")
        self.client.force_login(ana)
        game = make_game(ana, board=almost_solved(3), moves_used=20, started_at=timezone.now())
        response = self.client.post(
            reverse("game-move", args=[game.id]),
            json.dumps({"tile": winning_tile(3), "seq": 21}),
            content_type="application/json",
        )
        points = response.json()["score"]["points"]
        self.assertGreater(points, 0)
        self.assertEqual([row["points"] for row in top_ten() if row["user_id"] == ana.pk], [points])
        self.assertEqual(player_stats(ana).levels[0].best, points)
