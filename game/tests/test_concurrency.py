"""Simultaneous requests against real Postgres transactions (threads, no test-wide transaction)."""

import json
import threading

from django.db import connections
from django.test import Client, TransactionTestCase, tag
from django.urls import reverse
from django.utils import timezone

from game.models import Game, Move
from game.tests.helpers import make_game, make_user

SCRAMBLED = [4, 1, 3, 0, 2, 6, 7, 5, 8]


@tag("concurrency")
class SimultaneousRequestTests(TransactionTestCase):
    # TransactionTestCase flushes every table afterwards, including the seeded levels.
    serialized_rollback = True

    def setUp(self):
        self.user = make_user("ana")

    def logged_in_client(self):
        client = Client()
        client.force_login(self.user)
        return client

    def run_together(self, requests):
        """Run each zero-argument callable in its own thread, released at the same moment."""
        barrier = threading.Barrier(len(requests))
        results, errors = [None] * len(requests), []

        def worker(index, request):
            try:
                barrier.wait(timeout=10)
                results[index] = request()
            except BaseException as error:  # reported to the main thread below
                errors.append(error)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=worker, args=(i, r)) for i, r in enumerate(requests)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertEqual(errors, [])
        return results

    def move_request(self, game, tile):
        client = self.logged_in_client()
        body = json.dumps({"tile": tile, "seq": 1})
        url = reverse("game-move", args=[game.id])
        return lambda: client.post(url, body, content_type="application/json")

    def test_two_moves_with_the_same_seq_apply_exactly_once(self):
        game = make_game(self.user, board=SCRAMBLED, started_at=timezone.now())
        responses = self.run_together([self.move_request(game, 4), self.move_request(game, 4)])
        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        stale = next(response for response in responses if response.status_code == 409)
        self.assertEqual(stale.json()["error"], "stale")
        game.refresh_from_db()
        self.assertEqual(game.moves_used, 1)
        self.assertEqual(Move.objects.filter(game=game).count(), 1)

    def test_two_different_moves_with_the_same_seq_apply_exactly_once(self):
        game = make_game(self.user, board=SCRAMBLED, started_at=timezone.now())
        responses = self.run_together([self.move_request(game, 4), self.move_request(game, 2)])
        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        game.refresh_from_db()
        self.assertEqual(game.moves_used, 1)
        self.assertEqual(Move.objects.filter(game=game).count(), 1)

    def test_two_simultaneous_starts_leave_one_active_game_and_no_server_error(self):
        url = reverse("game-start", args=["facil"])
        clients = [self.logged_in_client(), self.logged_in_client()]
        responses = self.run_together([lambda c=c: c.post(url) for c in clients])
        self.assertEqual([response.status_code for response in responses], [303, 303])
        self.assertEqual(Game.objects.filter(user=self.user, status=Game.ACTIVE).count(), 1)
        self.assertEqual(Game.objects.filter(user=self.user).count(), 1)
