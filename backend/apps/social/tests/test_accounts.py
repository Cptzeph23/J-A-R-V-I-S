import pytest
from django.utils import timezone

from apps.audit.models import AuditEntry
from apps.social import errors
from apps.social.models import AccountStatus, ConnectState, PostState, SocialAccount, SocialPost
from apps.social.providers.base import PageGrant
from apps.social.providers.facebook import SCOPES
from apps.social.services import accounts, posts
from tests.factories import UserFactory
from tests.social_fixtures import approved_post, connect_page, state_from

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner():
    return UserFactory()


def test_start_needs_configuration(owner, settings):
    settings.META_REDIRECT_URI = ""
    with pytest.raises(errors.SocialNotConfiguredError):
        accounts.start(owner)


def test_connect_stores_a_sealed_token_never_the_plaintext(owner, fake_fb):
    account = connect_page(owner)
    assert account.page_id == "page-1"
    assert account.is_active
    assert "page-token-1" not in account.token_sealed
    assert accounts.token_of(account) == "page-token-1"
    actions = set(AuditEntry.objects.values_list("action", flat=True))
    assert {"social.authorization_started", "social.connected"} <= actions


def test_state_is_single_use_and_unknown_state_is_refused(owner, fake_fb):
    url = accounts.start(owner)
    state = state_from(url)
    accounts.complete(state=state, code="c")
    with pytest.raises(errors.InvalidConnectStateError):
        accounts.complete(state=state, code="c")
    with pytest.raises(errors.InvalidConnectStateError):
        accounts.complete(state="nope", code="c")


def test_expired_state_is_refused(owner, fake_fb):
    state = state_from(accounts.start(owner))
    ConnectState.objects.update(expires_at=timezone.now())
    with pytest.raises(errors.InvalidConnectStateError):
        accounts.complete(state=state, code="c")


def test_denied_login_connects_nothing(owner, fake_fb):
    state = state_from(accounts.start(owner))
    with pytest.raises(errors.ConnectDeniedError):
        accounts.complete(state=state, code="", error="access_denied")
    assert not SocialAccount.objects.exists()


def test_provider_failure_is_a_denial(owner, fake_fb):
    state = state_from(accounts.start(owner))
    with pytest.raises(errors.ConnectDeniedError):
        accounts.complete(state=state, code="denied")


def test_a_login_with_no_page_is_refused(owner, fake_fb):
    fake_fb.pages = []
    state = state_from(accounts.start(owner))
    with pytest.raises(errors.NoPageAccessError):
        accounts.complete(state=state, code="c")


def test_several_pages_ask_the_owner_and_never_expose_tokens(owner, fake_fb):
    fake_fb.pages = [PageGrant("p1", "One", "tok1"), PageGrant("p2", "Two", "tok2")]
    outcome, account = accounts.complete(state=state_from(accounts.start(owner)), code="c")
    assert (outcome, account) == ("select_page", None)
    listed = accounts.candidates(owner)
    assert listed == [{"page_id": "p1", "name": "One"}, {"page_id": "p2", "name": "Two"}]
    assert "tok" not in str(listed)
    chosen = accounts.select(owner, "p2")
    assert chosen.page_id == "p2"
    assert accounts.token_of(chosen) == "tok2"
    assert accounts.candidates(owner) == []
    ConnectState.objects.update(candidates_sealed="x")  # leftovers cannot be used twice
    with pytest.raises(errors.InvalidConnectStateError):
        accounts.select(owner, "p1")


def test_selecting_a_page_that_was_not_offered_is_refused(owner, fake_fb):
    fake_fb.pages = [PageGrant("p1", "One", "t1"), PageGrant("p2", "Two", "t2")]
    accounts.complete(state=state_from(accounts.start(owner)), code="c")
    with pytest.raises(errors.NoPageAccessError):
        accounts.select(owner, "other")


def test_candidates_expire(owner, fake_fb):
    fake_fb.pages = [PageGrant("p1", "One", "t1"), PageGrant("p2", "Two", "t2")]
    accounts.complete(state=state_from(accounts.start(owner)), code="c")
    assert accounts.candidates(owner, now=timezone.now() + accounts.CANDIDATE_TTL * 2) == []


def test_switching_page_cancels_open_posts_but_keeps_published(owner, fake_fb):
    connect_page(owner)
    draft = posts.create_draft(owner, text="draft")
    approved = approved_post(owner, "approved")
    done = approved_post(owner, "done")
    SocialPost.objects.filter(pk=done.pk).update(state=PostState.PUBLISHED, external_post_id="x")
    fake_fb.pages = [PageGrant("page-2", "Other Page", "tok-2")]
    connect_page(owner)
    states = {p.pk: p.state for p in SocialPost.objects.all()}
    assert states[draft.pk] == states[approved.pk] == PostState.CANCELLED
    assert states[done.pk] == PostState.PUBLISHED
    assert SocialAccount.objects.count() == 1


def test_reconnecting_the_same_page_keeps_drafts(owner, fake_fb):
    connect_page(owner)
    draft = posts.create_draft(owner, text="keep me")
    connect_page(owner)
    draft.refresh_from_db()
    assert draft.state == PostState.DRAFT


def test_disconnect_deletes_the_token_and_cancels_pending_posts(owner, fake_fb):
    connect_page(owner)
    approved = approved_post(owner)
    draft = posts.create_draft(owner, text="still a draft")
    account = accounts.disconnect(owner)
    assert account is not None
    assert account.status == AccountStatus.REVOKED
    assert account.token_sealed == ""
    approved.refresh_from_db()
    draft.refresh_from_db()
    assert approved.state == PostState.CANCELLED
    assert approved.error_code == "account_disconnected"
    assert draft.state == PostState.DRAFT
    assert accounts.disconnect(owner) is not None  # safe to repeat
    with pytest.raises(errors.SocialNotConnectedError):
        posts.create_draft(owner, text="x")


def test_disconnect_without_an_account_is_a_noop(owner, fake_fb):
    assert accounts.disconnect(owner) is None


def test_only_the_documented_permissions_are_requested():
    assert SCOPES == ("pages_show_list", "pages_manage_posts", "pages_read_engagement")
