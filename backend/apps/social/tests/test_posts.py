from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.exceptions import NotFound

from apps.audit.models import AuditEntry
from apps.social import errors
from apps.social.models import PostState, SocialPost
from apps.social.services import posts
from tests.factories import UserFactory
from tests.social_fixtures import approved_post, connect_page

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner(fake_fb):
    user = UserFactory()
    connect_page(user)
    return user


def test_a_draft_needs_a_connected_page(fake_fb):
    with pytest.raises(errors.SocialNotConnectedError):
        posts.create_draft(UserFactory(), text="hi")


def test_create_draft_stores_hash_and_page_and_audits_without_the_text(owner):
    post = posts.create_draft(owner, text="Secret launch plan", link="https://example.com")
    assert post.state == PostState.DRAFT
    assert post.page_name == "Acme Page"
    assert len(post.content_sha256) == 64
    entries = AuditEntry.objects.filter(action="social.post_created")
    assert entries.count() == 1
    entry = entries.first()
    assert entry is not None
    assert "Secret launch plan" not in str(entry.detail)


def test_approval_needs_the_exact_hash(owner):
    post = posts.create_draft(owner, text="hello")
    with pytest.raises(errors.ContentChangedError):
        posts.approve(owner, post.pk, echoed_sha256="0" * 64)
    approved = posts.approve(owner, post.pk, echoed_sha256=post.content_sha256)
    assert approved.state == PostState.APPROVED
    assert approved.approved_sha256 == post.content_sha256
    assert approved.approval_expires_at is not None
    assert approved.approval_expires_at > timezone.now()


def test_approval_refuses_content_changed_behind_the_hash(owner):
    post = posts.create_draft(owner, text="hello")
    SocialPost.objects.filter(pk=post.pk).update(text="something else")
    with pytest.raises(errors.ContentChangedError):
        posts.approve(owner, post.pk, echoed_sha256=post.content_sha256)


def test_editing_clears_the_approval(owner):
    post = approved_post(owner, "first")
    edited = posts.update_draft(owner, post.pk, text="second")
    assert edited.state == PostState.DRAFT
    assert edited.approved_sha256 == ""
    assert edited.approval_expires_at is None
    assert edited.content_sha256 != post.content_sha256
    with pytest.raises(errors.NotApprovedError):
        posts.request_publish(owner, post.pk)


@pytest.mark.parametrize("state", ["queued", "publishing", "published", "uncertain", "cancelled"])
def test_posts_in_flight_or_final_cannot_be_edited_or_approved(owner, state):
    post = posts.create_draft(owner, text="x")
    SocialPost.objects.filter(pk=post.pk).update(state=state, approved_sha256=post.content_sha256)
    with pytest.raises(errors.WrongStateError):
        posts.update_draft(owner, post.pk, text="y")
    with pytest.raises(errors.WrongStateError):
        posts.approve(owner, post.pk, echoed_sha256=post.content_sha256)


def test_cancel_rules(owner):
    post = posts.create_draft(owner, text="x")
    assert posts.cancel(owner, post.pk).state == PostState.CANCELLED
    assert posts.cancel(owner, post.pk).state == PostState.CANCELLED  # repeat is fine
    sent = posts.create_draft(owner, text="y")
    SocialPost.objects.filter(pk=sent.pk).update(
        state=PostState.PUBLISHED, approved_sha256=sent.content_sha256
    )
    with pytest.raises(errors.WrongStateError):
        posts.cancel(owner, sent.pk)


def test_publish_needs_an_unexpired_approval(owner):
    post = approved_post(owner)
    later = timezone.now() + timedelta(hours=25)
    with pytest.raises(errors.ApprovalExpiredError):
        posts.request_publish(owner, post.pk, now=later)


def test_unapproved_post_cannot_be_queued(owner):
    post = posts.create_draft(owner, text="x")
    with pytest.raises(errors.NotApprovedError):
        posts.request_publish(owner, post.pk)


def test_daily_limit(owner, settings):
    settings.SOCIAL_MAX_POSTS_PER_DAY = 2
    for i in range(2):
        posts.request_publish(owner, approved_post(owner, f"post {i}").pk)
    with pytest.raises(errors.DailyLimitError):
        posts.request_publish(owner, approved_post(owner, "third").pk)


def test_another_owners_post_is_not_found(owner, fake_fb):
    other = UserFactory()
    connect_page(other)
    mine = posts.create_draft(owner, text="mine")
    with pytest.raises(NotFound):
        posts.approve(other, mine.pk, echoed_sha256=mine.content_sha256)
    with pytest.raises(NotFound):
        posts.cancel(other, mine.pk)


def test_database_refuses_a_live_post_whose_approval_does_not_match(owner):
    from django.db import IntegrityError, transaction

    post = posts.create_draft(owner, text="x")
    with pytest.raises(IntegrityError), transaction.atomic():
        SocialPost.objects.filter(pk=post.pk).update(state=PostState.QUEUED)
