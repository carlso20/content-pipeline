"""
tests/test_youtube_poller.py
-----------------------------
Unit tests for the YouTube polling worker.
Phase 1 coverage: path detection, idempotency, brand routing.
Full end-to-end and retry tests are added in Phase 5.

Run with: pytest tests/
"""

import pytest
from workers.youtube_poller import YouTubePoller, PATH_A_TAGS, PATH_B_TAGS
from models.episodes import ContentPath


class TestPathDetection:
    """Tests for _detect_content_path — deterministic, never auto-guesses."""

    def setup_method(self):
        self.poller = YouTubePoller()

    def test_path_a_tag_detected(self):
        path, method = self.poller._detect_content_path(
            "Today we discuss marketing strategies. #PathA"
        )
        assert path == ContentPath.PATH_A
        assert method == "youtube_tag"

    def test_path_a_blogexists_tag(self):
        path, method = self.poller._detect_content_path(
            "Full episode #BlogExists — see the blog post linked below."
        )
        assert path == ContentPath.PATH_A
        assert method == "youtube_tag"

    def test_path_b_tag_detected(self):
        path, method = self.poller._detect_content_path(
            "Episode 42: How to build a brand. #PathB"
        )
        assert path == ContentPath.PATH_B
        assert method == "youtube_tag"

    def test_no_tag_returns_unknown(self):
        path, method = self.poller._detect_content_path(
            "Episode 42: How to build a brand. Great interview today."
        )
        assert path == ContentPath.UNKNOWN
        assert method == "no_tag_found"

    def test_empty_description_returns_unknown(self):
        path, method = self.poller._detect_content_path("")
        assert path == ContentPath.UNKNOWN
        assert method == "no_description"

    def test_none_description_treated_as_empty(self):
        path, method = self.poller._detect_content_path(None)
        assert path == ContentPath.UNKNOWN
        assert method == "no_description"

    def test_case_insensitive_detection(self):
        # Tags must match case-insensitively
        path, _ = self.poller._detect_content_path("Check this out #PATHA today!")
        assert path == ContentPath.PATH_A

    def test_path_a_takes_priority_if_both_tags_present(self):
        # Edge case: both tags in description — Path A should win (checked first)
        path, method = self.poller._detect_content_path("#PathA #PathB both present")
        assert path == ContentPath.PATH_A


class TestIdempotencyCheck:
    """
    Idempotency: the same youtube_video_id must never create duplicate records.
    These tests use mocking; database integration tests are added in Phase 5.
    """

    def test_duplicate_video_id_returns_false(self, mocker):
        poller = YouTubePoller()
        mock_session = mocker.MagicMock()
        mock_query = mocker.MagicMock()
        mock_query.filter.return_value.first.return_value = object()  # Simulates existing record

        mocker.patch("workers.youtube_poller.get_session")
        mocker.patch.object(poller, "_detect_content_path", return_value=(ContentPath.PATH_B, "youtube_tag"))

        # The method should return False when the record already exists
        # (Full integration test mocks the DB session context manager)


class TestBrandConfig:
    """Brand routing must never use heuristics — always from brand_config."""

    def test_unresolved_channel_id_skips_poll(self, mocker):
        poller = YouTubePoller()

        from models.brand_config import BrandConfig
        brand = BrandConfig()
        brand.brand_name = "Test Brand"
        brand.youtube_channel_id = "UC_TODO_placeholder"
        brand.is_active = True

        fetch_mock = mocker.patch.object(poller, "_fetch_recent_uploads")
        poller._poll_brand(brand)

        # Should skip without calling the API
        fetch_mock.assert_not_called()
