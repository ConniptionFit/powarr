"""Unit tests for LIB-03: duplicate & upgrade hunter."""
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.media import MediaItem
from app.services.duplicate_finder import find_duplicate_groups


def _item(**over):
    base = dict(title="Title", media_type="movie", file_size=1024 ** 3, ignored=False)
    base.update(over)
    return MediaItem(**base)


class FindDuplicateGroupsTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()

    def test_no_duplicates_returns_empty(self):
        self.db.add_all([
            _item(plex_rating_key="a", title="Movie One"),
            _item(plex_rating_key="b", title="Movie Two"),
        ])
        self.db.commit()
        self.assertEqual(find_duplicate_groups(self.db), [])

    def test_same_title_movie_same_year_groups(self):
        a = _item(plex_rating_key="a", title="Dune", year=2021, file_size=8 * 1024 ** 3)
        b = _item(plex_rating_key="b", title="Dune", year=2021, file_size=4 * 1024 ** 3)
        self.db.add_all([a, b])
        self.db.commit()

        groups = find_duplicate_groups(self.db)
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual(group["media_type"], "movie")
        self.assertEqual(len(group["items"]), 2)
        # Largest file suggested as the keeper.
        self.assertEqual(group["suggested_keep_id"], a.id)
        self.assertEqual(group["reclaimable_bytes"], 4 * 1024 ** 3)
        self.assertEqual(group["total_size_bytes"], 12 * 1024 ** 3)
        self.assertTrue(group["has_size_signal"])

    def test_container_types_with_zero_file_size_flag_no_signal(self):
        # Real-world case found live: `show`/`artist` MediaItem rows are Plex
        # container entries — file_size lives on their child episodes/tracks,
        # not the parent row — so both members here are legitimately 0.
        a = _item(plex_rating_key="a", title="One Piece", media_type="show", file_size=0)
        b = _item(plex_rating_key="b", title="One Piece", media_type="show", file_size=0)
        self.db.add_all([a, b])
        self.db.commit()

        groups = find_duplicate_groups(self.db)
        self.assertEqual(len(groups), 1)
        self.assertFalse(groups[0]["has_size_signal"])
        self.assertEqual(groups[0]["reclaimable_bytes"], 0)

    def test_zero_signal_groups_sort_after_real_signal_groups(self):
        signal = _item(plex_rating_key="a", title="Dune", media_type="movie", year=2021, file_size=1024 ** 3)
        signal2 = _item(plex_rating_key="b", title="Dune", media_type="movie", year=2021, file_size=1)
        no_signal = _item(plex_rating_key="c", title="One Piece", media_type="show", file_size=0)
        no_signal2 = _item(plex_rating_key="d", title="One Piece", media_type="show", file_size=0)
        self.db.add_all([no_signal, no_signal2, signal, signal2])
        self.db.commit()

        groups = find_duplicate_groups(self.db)
        self.assertEqual(len(groups), 2)
        self.assertTrue(groups[0]["has_size_signal"])
        self.assertFalse(groups[1]["has_size_signal"])

    def test_movies_with_different_years_do_not_group(self):
        # Legitimate remakes — same title, different year, not a duplicate.
        self.db.add_all([
            _item(plex_rating_key="a", title="The Thing", year=1982),
            _item(plex_rating_key="b", title="The Thing", year=2011),
        ])
        self.db.commit()
        self.assertEqual(find_duplicate_groups(self.db), [])

    def test_show_groups_ignoring_year(self):
        a = _item(plex_rating_key="a", title="The Office", media_type="show",
                   year=2005, file_size=2 * 1024 ** 3)
        b = _item(plex_rating_key="b", title="The Office", media_type="show",
                   year=None, file_size=1024 ** 3)
        self.db.add_all([a, b])
        self.db.commit()

        groups = find_duplicate_groups(self.db)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["media_type"], "show")
        self.assertEqual(len(groups[0]["items"]), 2)

    def test_normalization_matches_punctuation_variants(self):
        a = _item(plex_rating_key="a", title="Life, Larry", media_type="show", file_size=2 * 1024 ** 3)
        b = _item(plex_rating_key="b", title="Life Larry", media_type="show", file_size=1024 ** 3)
        self.db.add_all([a, b])
        self.db.commit()
        groups = find_duplicate_groups(self.db)
        self.assertEqual(len(groups), 1)

    def test_ignored_items_excluded(self):
        self.db.add_all([
            _item(plex_rating_key="a", title="Dune", year=2021),
            _item(plex_rating_key="b", title="Dune", year=2021, ignored=True),
        ])
        self.db.commit()
        self.assertEqual(find_duplicate_groups(self.db), [])

    def test_pending_delete_items_excluded(self):
        from datetime import datetime
        self.db.add_all([
            _item(plex_rating_key="a", title="Dune", year=2021),
            _item(plex_rating_key="b", title="Dune", year=2021, pending_delete_at=datetime.utcnow()),
        ])
        self.db.commit()
        self.assertEqual(find_duplicate_groups(self.db), [])

    def test_episode_and_track_types_not_grouped(self):
        self.db.add_all([
            _item(plex_rating_key="a", title="Pilot", media_type="episode"),
            _item(plex_rating_key="b", title="Pilot", media_type="episode"),
        ])
        self.db.commit()
        self.assertEqual(find_duplicate_groups(self.db), [])

    def test_groups_sorted_by_reclaimable_bytes_descending(self):
        # Small-reclaim group.
        self.db.add_all([
            _item(plex_rating_key="a", title="Small", year=2000, file_size=2 * 1024 ** 2),
            _item(plex_rating_key="b", title="Small", year=2000, file_size=1024 ** 2),
        ])
        # Large-reclaim group.
        self.db.add_all([
            _item(plex_rating_key="c", title="Big", year=2001, file_size=8 * 1024 ** 3),
            _item(plex_rating_key="d", title="Big", year=2001, file_size=1 * 1024 ** 3),
        ])
        self.db.commit()
        groups = find_duplicate_groups(self.db)
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]["title"], "Big")
        self.assertEqual(groups[1]["title"], "Small")


if __name__ == "__main__":
    unittest.main()
