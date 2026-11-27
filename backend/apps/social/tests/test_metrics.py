from datetime import timedelta

import pytest
from django.utils import timezone

from apps.social.models import AccountStatus, PostMetric, PostState, SocialAccount, SocialPost
from apps.social.providers.base import RemoteMetrics
from apps.social.services import metrics, posts
from tests.factories import UserFactory
from tests.social_fixtures import connect_page, queued_post

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner(fake_fb):
    user = UserFactory()
    connect_page(user)
    return user


def published(owner, text="p", *, age_days=1):
    post = queued_post(owner, text)
    posts.execute_publish(post.pk)
    SocialPost.objects.filter(pk=post.pk).update(
        published_at=timezone.now() - timedelta(days=age_days)
    )
    return SocialPost.objects.get(pk=post.pk)


def test_summary_with_no_data_has_no_numbers_not_zeros(owner):
    out = metrics.summary(owner)
    assert out["available"] is True
    assert out["published_count"] == 0
    assert out["totals"] is None
    assert out["data_as_of"] is None
    assert out["not_collected"] == ["reach", "impressions"]


def test_a_published_post_without_metrics_shows_none_not_zero(owner):
    published(owner)
    out = metrics.summary(owner)
    assert out["published_count"] == 1
    assert out["measured_count"] == 0
    assert out["posts"][0]["reactions"] is None
    assert out["totals"] is None


def test_refresh_stores_a_snapshot_and_the_summary_reports_it(owner, fake_fb):
    post = published(owner)
    assert metrics.refresh(owner) == 1
    out = metrics.summary(owner)
    assert out["totals"] == {"reactions": 3, "comments": 2, "shares": 1}
    assert out["posts"][0]["metrics_fetched_at"] is not None
    assert out["data_as_of"] == PostMetric.objects.get(post=post).fetched_at


def test_refresh_respects_the_minimum_interval(owner):
    published(owner)
    assert metrics.refresh(owner) == 1
    assert metrics.refresh(owner) == 0
    later = timezone.now() + timedelta(hours=7)
    assert metrics.refresh(owner, now=later) == 1
    assert PostMetric.objects.count() == 2


def test_summary_uses_the_latest_snapshot(owner, fake_fb):
    published(owner)
    metrics.refresh(owner)
    fake_fb.metrics_value = RemoteMetrics(10, 5, 2)
    metrics.refresh(owner, now=timezone.now() + timedelta(hours=7))
    assert metrics.summary(owner)["totals"] == {"reactions": 10, "comments": 5, "shares": 2}


def test_old_posts_are_not_refreshed(owner):
    published(owner, age_days=45)
    assert metrics.refresh() == 0


def test_read_failure_stores_nothing_and_keeps_the_account(owner, fake_fb):
    published(owner)
    fake_fb.metrics_error = "down"
    assert metrics.refresh(owner) == 0
    assert SocialAccount.objects.get().status == AccountStatus.CONNECTED


def test_rejected_token_marks_reconnect_and_stops(owner, fake_fb):
    published(owner, "a")
    published(owner, "b")
    fake_fb.metrics_error = "token"
    assert metrics.refresh(owner) == 0
    assert SocialAccount.objects.get().status == AccountStatus.REAUTH_REQUIRED


def test_unpublished_and_other_owners_posts_are_excluded(owner, fake_fb):
    other = UserFactory()
    connect_page(other)
    published(other, "theirs")
    queued_post(owner, "not sent yet")
    assert metrics.summary(owner)["published_count"] == 0
    assert SocialPost.objects.filter(state=PostState.PUBLISHED).count() == 1


def test_summary_when_disconnected_says_why(owner):
    SocialAccount.objects.update(status=AccountStatus.REVOKED, token_sealed="")
    out = metrics.summary(owner)
    assert out["available"] is False
    assert out["reason"] == "social_not_connected"
