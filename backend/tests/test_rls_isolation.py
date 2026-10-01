"""Row-level security as experienced by the real runtime roles."""

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import ProgrammingError

from app.db.database import TenantBindingError, bind_tenant
from app.models import Membership, Tenant, WebhookEvent
from tests.support.database import DatabaseHandles
from tests.support.factories import seed_membership, seed_tenant


def _seed_two_tenants(db: DatabaseHandles) -> None:
    for tenant_id in ("org_a", "org_b"):
        seed_tenant(db.owner_engine, tenant_id)
        seed_membership(db.owner_engine, tenant_id, f"user_{tenant_id}")


def test_bound_app_session_sees_only_its_own_tenant(db: DatabaseHandles) -> None:
    _seed_two_tenants(db)

    with db.app.session() as session:
        bind_tenant(session, "org_a")
        tenant_ids = set(session.scalars(select(Tenant.id)))
        membership_tenants = set(session.scalars(select(Membership.tenant_id)))

    assert tenant_ids == {"org_a"}
    assert membership_tenants == {"org_a"}


def test_unbound_app_session_sees_nothing(db: DatabaseHandles) -> None:
    _seed_two_tenants(db)

    with db.app.session() as session:
        assert session.scalars(select(Tenant.id)).all() == []
        assert session.scalars(select(Membership.user_id)).all() == []


def test_tenant_binding_survives_commit(db: DatabaseHandles) -> None:
    _seed_two_tenants(db)

    with db.app.session() as session:
        bind_tenant(session, "org_a")
        assert set(session.scalars(select(Tenant.id))) == {"org_a"}
        session.commit()
        assert set(session.scalars(select(Tenant.id))) == {"org_a"}


def test_binding_before_first_query_applies_on_autobegin(db: DatabaseHandles) -> None:
    _seed_two_tenants(db)

    with db.app.session() as session:
        assert not session.in_transaction()
        bind_tenant(session, "org_b")
        assert set(session.scalars(select(Tenant.id))) == {"org_b"}


def test_rebinding_to_a_different_tenant_is_refused(db: DatabaseHandles) -> None:
    with db.app.session() as session:
        bind_tenant(session, "org_a")
        bind_tenant(session, "org_a")  # same tenant is a no-op

        with pytest.raises(TenantBindingError):
            bind_tenant(session, "org_b")


def test_empty_tenant_id_is_refused(db: DatabaseHandles) -> None:
    with db.app.session() as session, pytest.raises(TenantBindingError):
        bind_tenant(session, "")


def test_app_role_cannot_update_another_tenants_membership(db: DatabaseHandles) -> None:
    _seed_two_tenants(db)

    with db.app.session() as session:
        bind_tenant(session, "org_a")
        updated = session.scalars(
            update(Membership)
            .where(Membership.tenant_id == "org_b")
            .values(sub_role="accountant")
            .returning(Membership.user_id)
        ).all()
        session.commit()

    assert updated == []


def test_app_role_can_update_own_tenant_sub_role(db: DatabaseHandles) -> None:
    _seed_two_tenants(db)

    with db.app.session() as session:
        bind_tenant(session, "org_a")
        updated = session.scalars(
            update(Membership).values(sub_role="accountant").returning(Membership.user_id)
        ).all()
        session.commit()

    assert updated == ["user_org_a"]


def test_app_role_cannot_change_clerk_role(db: DatabaseHandles) -> None:
    _seed_two_tenants(db)

    with db.app.session() as session:
        bind_tenant(session, "org_a")
        with pytest.raises(ProgrammingError, match="permission denied"):
            session.execute(update(Membership).values(clerk_role="admin"))


def test_app_role_cannot_insert_tenants(db: DatabaseHandles) -> None:
    with db.app.session() as session:
        bind_tenant(session, "org_new")
        with pytest.raises(ProgrammingError, match="permission denied"):
            session.execute(text("INSERT INTO tenants (id, name) VALUES ('org_new', 'x')"))


def test_app_role_cannot_read_webhook_events(db: DatabaseHandles) -> None:
    with db.app.session() as session, pytest.raises(ProgrammingError, match="permission denied"):
        session.execute(select(WebhookEvent.event_id))


def test_app_role_cannot_forge_a_service_bypass(db: DatabaseHandles) -> None:
    _seed_two_tenants(db)

    with db.app.session() as session:
        session.execute(text("SELECT set_config('app.is_service', 'true', true)"))
        assert session.scalars(select(Tenant.id)).all() == []


def test_service_role_sees_every_tenant(db: DatabaseHandles) -> None:
    _seed_two_tenants(db)

    with db.service.session() as session:
        assert set(session.scalars(select(Tenant.id))) == {"org_a", "org_b"}
