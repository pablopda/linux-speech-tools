"""Hermetic least-privilege tests for the explicit context broker."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from src.voice.context import (
    ContextBroker,
    ContextBrokerError,
    ContextExpired,
    ContextRegistrationError,
    RequiredContextUnavailable,
)
from src.voice.models import (
    Capability,
    ContextItem,
    ContextKind,
    ContextRequest,
    ContextSnapshot,
    Sensitivity,
    TargetBinding,
)


NOW = 100.0


class FakeProvider:
    def __init__(self, items=(), error=None):
        self.items = list(items)
        self.error = error
        self.calls = []

    def capture(self, requested_kinds, *, target, now, deadline):
        self.calls.append(
            {
                "requested_kinds": requested_kinds,
                "target": target,
                "now": now,
                "deadline": deadline,
            }
        )
        if self.error is not None:
            raise self.error
        return list(self.items)


def item(
    kind,
    provider,
    content="bounded context",
    *,
    captured_at=NOW,
    expires_at=NOW + 30,
    metadata=None,
    shareable=True,
    required_capability=Capability.READ_CONTEXT,
):
    return ContextItem(
        kind=kind,
        provider=provider,
        sensitivity=Sensitivity.PRIVATE,
        captured_at=captured_at,
        expires_at=expires_at,
        content=content,
        metadata=metadata or {},
        shareable=shareable,
        required_capability=required_capability,
    )


def request(
    kinds,
    *,
    required=(),
    shareable=(),
    providers=(),
    permissions=(Capability.READ_CONTEXT,),
    deadline=NOW + 20,
    max_total_bytes=64 * 1024,
):
    return ContextRequest(
        request_id="request-1",
        kinds=frozenset(kinds),
        permissions=frozenset(permissions),
        provider_names=providers,
        created_at=NOW - 1,
        deadline=deadline,
        max_total_bytes=max_total_bytes,
        required_kinds=frozenset(required),
        shareable_kinds=frozenset(shareable),
    )


def target(secret="window-secret"):
    return TargetBinding(
        kind="ide",
        source="gnome-focus",
        stable_id=secret,
        captured_at=NOW,
        generation=7,
    )


def test_registry_calls_only_the_explicitly_requested_provider_and_kind():
    repository = FakeProvider([item(ContextKind.REPOSITORY, "repo-provider")])
    selection = FakeProvider([item(ContextKind.SELECTION, "selection-provider")])
    broker = ContextBroker(clock=lambda: NOW)
    broker.register("repo-provider", repository, kinds=(ContextKind.REPOSITORY,))
    broker.register("selection-provider", selection, kinds=(ContextKind.SELECTION,))
    binding = target()

    snapshot = broker.capture(
        request(
            (ContextKind.REPOSITORY,),
            required=(ContextKind.REPOSITORY,),
            providers=frozenset(("repo-provider",)),
        ),
        target=binding,
    )

    assert [entry.kind for entry in snapshot.items] == [ContextKind.REPOSITORY]
    assert selection.calls == []
    assert repository.calls == [
        {
            "requested_kinds": frozenset(("repository",)),
            "target": binding,
            "now": NOW,
            "deadline": NOW + 20,
        }
    ]


def test_registry_rejects_ambiguous_or_unbounded_registration():
    broker = ContextBroker()
    broker.register("first", FakeProvider(), kinds=(ContextKind.REPOSITORY,))

    with pytest.raises(ContextRegistrationError, match="already registered"):
        broker.register("second", FakeProvider(), kinds=(ContextKind.REPOSITORY,))
    with pytest.raises(ContextRegistrationError, match="cannot be empty"):
        broker.register("empty", FakeProvider(), kinds=())
    with pytest.raises(ValueError, match="safe range"):
        ContextBroker(max_total_bytes=1024 * 1024 + 1)


def test_optional_failure_is_visible_and_does_not_expand_to_another_provider():
    private_error = "/home/alice/private.txt token=sk-do-not-log"
    failed = FakeProvider(error=RuntimeError(private_error))
    required = FakeProvider(
        [item(ContextKind.SELECTION, "selection-provider", "selected text")]
    )
    broker = ContextBroker(clock=lambda: NOW)
    broker.register("repo-provider", failed, kinds=(ContextKind.REPOSITORY,))
    broker.register("selection-provider", required, kinds=(ContextKind.SELECTION,))

    snapshot = broker.capture(
        request(
            (ContextKind.REPOSITORY, ContextKind.SELECTION),
            required=(ContextKind.SELECTION,),
        ),
        target=target(),
    )
    manifest = broker.manifest(snapshot)

    assert [entry.kind for entry in snapshot.items] == [ContextKind.SELECTION]
    assert manifest["state"] == "degraded"
    assert manifest["omissions"] == [
        {
            "kind": "repository",
            "provider": "repo-provider",
            "reason": "provider-failed",
        }
    ]
    assert private_error not in json.dumps(manifest)
    assert len(failed.calls) == 1


def test_required_provider_failure_is_safe_and_atomic():
    provider = FakeProvider(
        error=RuntimeError("secret selection /private/path bearer-token")
    )
    broker = ContextBroker(clock=lambda: NOW)
    broker.register("repo-provider", provider, kinds=(ContextKind.REPOSITORY,))

    with pytest.raises(RequiredContextUnavailable) as captured:
        broker.capture(
            request(
                (ContextKind.REPOSITORY,),
                required=(ContextKind.REPOSITORY,),
            ),
            target=target(),
        )

    assert captured.value.reason == "provider-failed"
    assert "secret" not in str(captured.value)
    assert "private" not in str(captured.value)


def test_provider_cannot_return_unrequested_context_or_claim_another_provider():
    malicious = FakeProvider(
        [item(ContextKind.SELECTION, "another-provider", "clipboard contents")]
    )
    broker = ContextBroker(clock=lambda: NOW)
    broker.register("repo-provider", malicious, kinds=(ContextKind.REPOSITORY,))

    snapshot = broker.capture(
        request((ContextKind.REPOSITORY,)),
        target=target(),
    )

    assert snapshot.items == ()
    assert broker.manifest(snapshot)["omissions"][0]["reason"] == "scope-mismatch"


def test_per_item_metadata_and_provider_limits_fail_closed():
    oversized_metadata = item(
        ContextKind.REPOSITORY,
        "repo-provider",
        metadata={"note": "x" * 3000},
    )
    provider = FakeProvider([oversized_metadata])
    broker = ContextBroker(clock=lambda: NOW)
    broker.register(
        "repo-provider",
        provider,
        kinds=(ContextKind.REPOSITORY,),
        max_item_bytes=4096,
        max_provider_bytes=8192,
    )

    snapshot = broker.capture(request((ContextKind.REPOSITORY,)), target=target())

    assert snapshot.items == ()
    assert broker.manifest(snapshot)["omissions"][0]["reason"] == "invalid-item"

    two_items = FakeProvider(
        [
            item(ContextKind.SELECTION, "selection-provider", "a" * 300),
            item(ContextKind.SELECTION, "selection-provider", "b" * 300),
        ]
    )
    limited = ContextBroker(clock=lambda: NOW)
    limited.register(
        "selection-provider",
        two_items,
        kinds=(ContextKind.SELECTION,),
        max_item_bytes=500,
        max_provider_bytes=700,
    )
    snapshot = limited.capture(request((ContextKind.SELECTION,)), target=target())
    assert snapshot.items == ()
    assert limited.manifest(snapshot)["omissions"][0]["reason"] == "provider-limit"


def test_total_limit_prioritizes_required_context_over_optional_context():
    optional = FakeProvider([item(ContextKind.REPOSITORY, "a-optional", "o" * 300)])
    required = FakeProvider([item(ContextKind.SELECTION, "z-required", "needed")])
    broker = ContextBroker(max_total_bytes=500, clock=lambda: NOW)
    broker.register(
        "a-optional",
        optional,
        kinds=(ContextKind.REPOSITORY,),
        max_item_bytes=1024,
        max_provider_bytes=1024,
    )
    broker.register(
        "z-required",
        required,
        kinds=(ContextKind.SELECTION,),
        max_item_bytes=1024,
        max_provider_bytes=1024,
    )

    snapshot = broker.capture(
        request(
            (ContextKind.REPOSITORY, ContextKind.SELECTION),
            required=(ContextKind.SELECTION,),
            max_total_bytes=500,
        ),
        target=target(),
    )

    assert [entry.kind for entry in snapshot.items] == [ContextKind.SELECTION]
    assert broker.manifest(snapshot)["omissions"][0]["reason"] == "total-limit"


def test_total_limit_prioritizes_required_kind_within_one_provider():
    required_item = item(ContextKind.REPOSITORY, "workspace-provider", "needed")
    optional_item = item(ContextKind.SELECTION, "workspace-provider", "optional" * 100)
    total_limit = required_item.payload_size + 10
    provider = FakeProvider((optional_item, required_item))
    broker = ContextBroker(max_total_bytes=total_limit, clock=lambda: NOW)
    broker.register(
        "workspace-provider",
        provider,
        kinds=(ContextKind.REPOSITORY, ContextKind.SELECTION),
        max_item_bytes=2048,
        max_provider_bytes=4096,
    )

    snapshot = broker.capture(
        request(
            (ContextKind.REPOSITORY, ContextKind.SELECTION),
            required=(ContextKind.REPOSITORY,),
            max_total_bytes=total_limit,
        ),
        target=target(),
    )

    assert [entry.kind for entry in snapshot.items] == [ContextKind.REPOSITORY]
    assert broker.manifest(snapshot)["omissions"] == [
        {
            "kind": "selection",
            "provider": "workspace-provider",
            "reason": "total-limit",
        }
    ]


def test_stale_optional_context_degrades_and_required_context_fails():
    stale = FakeProvider(
        [
            item(
                ContextKind.REPOSITORY,
                "repo-provider",
                captured_at=NOW - 20,
                expires_at=NOW + 20,
            )
        ]
    )
    broker = ContextBroker(clock=lambda: NOW)
    broker.register(
        "repo-provider",
        stale,
        kinds=(ContextKind.REPOSITORY,),
        max_age_seconds=10,
    )

    optional = broker.capture(request((ContextKind.REPOSITORY,)), target=target())
    assert broker.manifest(optional)["omissions"][0]["reason"] == "stale"

    with pytest.raises(RequiredContextUnavailable) as captured:
        broker.capture(
            request(
                (ContextKind.REPOSITORY,),
                required=(ContextKind.REPOSITORY,),
            ),
            target=target(),
        )
    assert captured.value.reason == "stale"


def test_provider_expiry_is_clipped_and_expired_snapshot_never_reaches_adapter():
    provider = FakeProvider(
        [
            item(
                ContextKind.REPOSITORY,
                "repo-provider",
                expires_at=NOW + 500,
            )
        ]
    )
    broker = ContextBroker(clock=lambda: NOW)
    broker.register(
        "repo-provider",
        provider,
        kinds=(ContextKind.REPOSITORY,),
        max_age_seconds=10,
    )
    snapshot = broker.capture(
        request(
            (ContextKind.REPOSITORY,),
            shareable=(ContextKind.REPOSITORY,),
            deadline=NOW + 50,
        ),
        target=target(),
    )

    assert snapshot.items[0].expires_at == NOW + 10
    assert broker.adapter_items(snapshot, now=NOW + 9)
    with pytest.raises(ContextExpired, match="expired"):
        broker.adapter_items(snapshot, now=NOW + 10)
    assert broker.manifest(snapshot, now=NOW + 10)["state"] == "expired"


def test_request_deadline_bounds_an_empty_degraded_snapshot():
    broker = ContextBroker(clock=lambda: NOW)
    snapshot = broker.capture(
        request((ContextKind.REPOSITORY,), deadline=NOW + 3),
        target=target(),
    )

    assert snapshot.items == ()
    assert snapshot.expires_at == NOW + 3
    with pytest.raises(ContextExpired):
        broker.adapter_items(snapshot, now=NOW + 3)


def test_empty_snapshot_has_an_independent_maximum_age():
    broker = ContextBroker(clock=lambda: NOW)
    snapshot = broker.capture(
        request((ContextKind.REPOSITORY,), deadline=NOW + 365 * 24 * 60 * 60),
        target=target(),
    )

    assert snapshot.items == ()
    assert snapshot.expires_at == NOW + 60
    with pytest.raises(ContextExpired):
        broker.adapter_payload(snapshot, now=NOW + 60)


def test_adapter_rejects_snapshot_before_its_creation_time():
    context = ContextSnapshot.create(
        target=TargetBinding(captured_at=NOW),
        items=(
            item(
                ContextKind.REPOSITORY,
                "repo-provider",
                "private content",
                captured_at=NOW - 5,
            ),
        ),
        permissions=frozenset((Capability.READ_CONTEXT,)),
        now=NOW,
    )
    broker = ContextBroker(clock=lambda: NOW)

    with pytest.raises(ContextExpired):
        broker.adapter_payload(context, now=NOW - 1)


def test_capture_rejects_future_request_and_stale_target_binding():
    broker = ContextBroker(clock=lambda: NOW)
    future = ContextRequest(
        request_id="future-request",
        kinds=frozenset((ContextKind.REPOSITORY,)),
        permissions=frozenset((Capability.READ_CONTEXT,)),
        created_at=NOW + 1,
        deadline=NOW + 10,
    )
    with pytest.raises(ContextExpired, match="not yet valid"):
        broker.capture(future, target=target())

    with pytest.raises(ContextExpired, match="expired"):
        broker.capture(
            request((ContextKind.REPOSITORY,)),
            target=TargetBinding(captured_at=NOW - 61),
        )

    near_expiry = broker.capture(
        request((ContextKind.REPOSITORY,), deadline=NOW + 20),
        target=TargetBinding(captured_at=NOW - 59),
    )
    assert near_expiry.expires_at == NOW + 1
    with pytest.raises(ContextExpired):
        broker.adapter_payload(near_expiry, now=NOW + 1)


def test_provider_introspection_failure_is_redacted():
    class BadProvider:
        @property
        def capture(self):
            raise RuntimeError("/private/provider/config token=secret")

    broker = ContextBroker(clock=lambda: NOW)
    with pytest.raises(ContextRegistrationError) as raised:
        broker.register(
            "bad-provider",
            BadProvider(),
            kinds=(ContextKind.REPOSITORY,),
        )
    assert str(raised.value) == "provider capture interface is unavailable"


def test_target_normalization_failure_is_redacted_before_provider_io():
    class BadTarget:
        kind = "ide"
        source = "gnome-focus"

        @property
        def stable_id(self):
            raise RuntimeError("/private/window-title token=secret")

    provider = FakeProvider()
    broker = ContextBroker(clock=lambda: NOW)
    broker.register("repo-provider", provider, kinds=(ContextKind.REPOSITORY,))

    with pytest.raises(ContextBrokerError) as raised:
        broker.capture(request((ContextKind.REPOSITORY,)), target=BadTarget())

    assert str(raised.value) == "invalid target binding"
    assert provider.calls == []


def test_provider_item_attribute_failure_is_contained_and_redacted():
    @dataclass(frozen=True)
    class HostileItem(ContextItem):
        armed: bool = False

        def __getattribute__(self, name):
            if name == "kind" and object.__getattribute__(self, "armed"):
                raise RuntimeError("/private/provider/selection.txt token=secret")
            return object.__getattribute__(self, name)

    hostile = HostileItem(
        kind=ContextKind.REPOSITORY,
        provider="repo-provider",
        sensitivity=Sensitivity.PRIVATE,
        captured_at=NOW,
        expires_at=NOW + 30,
        content="small",
        armed=False,
    )
    object.__setattr__(hostile, "armed", True)
    broker = ContextBroker(clock=lambda: NOW)
    broker.register(
        "repo-provider", FakeProvider((hostile,)), kinds=(ContextKind.REPOSITORY,)
    )

    snapshot = broker.capture(request((ContextKind.REPOSITORY,)), target=target())

    assert snapshot.items == ()
    assert broker.manifest(snapshot)["omissions"][0]["reason"] == "invalid-item"


def test_provider_item_subclass_is_normalized_before_payload_or_sharing():
    @dataclass(frozen=True)
    class SmuggledItem(ContextItem):
        hidden: str = ""

        def to_payload(self):
            return {"hidden": self.hidden}

    smuggled = SmuggledItem(
        kind=ContextKind.REPOSITORY,
        provider="repo-provider",
        sensitivity=Sensitivity.PRIVATE,
        captured_at=NOW,
        expires_at=NOW + 30,
        content="small",
        hidden="x" * 100_000,
    )
    broker = ContextBroker(clock=lambda: NOW)
    broker.register(
        "repo-provider", FakeProvider((smuggled,)), kinds=(ContextKind.REPOSITORY,)
    )

    snapshot = broker.capture(
        request(
            (ContextKind.REPOSITORY,),
            shareable=(ContextKind.REPOSITORY,),
        ),
        target=target(),
    )

    assert type(snapshot.items[0]) is ContextItem
    payload = broker.adapter_payload(snapshot, now=NOW)
    assert payload[0]["content"] == "small"
    assert "hidden" not in payload[0]


def test_fingerprint_is_deterministic_independent_of_provider_item_order():
    first = item(ContextKind.REPOSITORY, "repo-provider", "first")
    second = item(ContextKind.REPOSITORY, "repo-provider", "second")
    provider = FakeProvider([first, second])
    broker = ContextBroker(clock=lambda: NOW)
    broker.register("repo-provider", provider, kinds=(ContextKind.REPOSITORY,))
    context_request = request(
        (ContextKind.REPOSITORY,),
        required=(ContextKind.REPOSITORY,),
        shareable=(ContextKind.REPOSITORY,),
    )

    snapshot_a = broker.capture(context_request, target=target())
    provider.items = [second, first]
    snapshot_b = broker.capture(context_request, target=target())

    assert snapshot_a.snapshot_id != snapshot_b.snapshot_id
    assert snapshot_a.fingerprint == snapshot_b.fingerprint
    assert [entry.content for entry in snapshot_a.items] == [
        entry.content for entry in snapshot_b.items
    ]


def test_provider_shareability_is_overridden_and_target_stays_internal():
    private_target = target("/private/window/title?token=secret")
    provider = FakeProvider(
        [
            item(ContextKind.REPOSITORY, "workspace-provider", "repo facts"),
            item(ContextKind.SELECTION, "workspace-provider", "private selection"),
        ]
    )
    broker = ContextBroker(clock=lambda: NOW)
    broker.register(
        "workspace-provider",
        provider,
        kinds=(ContextKind.REPOSITORY, ContextKind.SELECTION),
    )
    snapshot = broker.capture(
        request(
            (ContextKind.REPOSITORY, ContextKind.SELECTION),
            shareable=(ContextKind.REPOSITORY,),
        ),
        target=private_target,
    )

    assert {entry.kind: entry.shareable for entry in snapshot.items} == {
        ContextKind.REPOSITORY: True,
        ContextKind.SELECTION: False,
    }
    payload = broker.adapter_payload(snapshot)
    serialized = json.dumps(payload)
    assert [entry["kind"] for entry in payload] == ["repository"]
    assert "private selection" not in serialized
    assert private_target.stable_id not in serialized
    assert "target" not in serialized


def test_missing_read_permission_stops_provider_io_and_degrades_only_optional():
    provider = FakeProvider([item(ContextKind.REPOSITORY, "repo-provider")])
    broker = ContextBroker(clock=lambda: NOW)
    broker.register("repo-provider", provider, kinds=(ContextKind.REPOSITORY,))

    snapshot = broker.capture(
        request((ContextKind.REPOSITORY,), permissions=()),
        target=target(),
    )
    assert provider.calls == []
    assert broker.manifest(snapshot)["omissions"][0]["reason"] == "permission-denied"

    with pytest.raises(RequiredContextUnavailable) as captured:
        broker.capture(
            request(
                (ContextKind.REPOSITORY,),
                required=(ContextKind.REPOSITORY,),
                permissions=(),
            ),
            target=target(),
        )
    assert captured.value.reason == "permission-denied"
    assert provider.calls == []


def test_provider_cannot_substitute_a_non_context_capability():
    provider = FakeProvider(
        [
            item(
                ContextKind.REPOSITORY,
                "repo-provider",
                required_capability=Capability.EXECUTE,
            )
        ]
    )
    broker = ContextBroker(clock=lambda: NOW)
    broker.register("repo-provider", provider, kinds=(ContextKind.REPOSITORY,))
    snapshot = broker.capture(
        request(
            (ContextKind.REPOSITORY,),
            permissions=(Capability.READ_CONTEXT, Capability.EXECUTE),
        ),
        target=target(),
    )
    assert snapshot.items == ()
    assert broker.manifest(snapshot)["omissions"][0]["reason"] == "permission-denied"


def test_manifest_omits_raw_content_metadata_target_and_provider_errors():
    secrets = (
        "verbatim transcript",
        "Confidential Window Title",
        "/home/alice/private/repository.py",
        "ghp_super_secret_token",
        "password=hunter2",
    )
    content = " ".join(secrets)
    provider = FakeProvider(
        [
            item(
                ContextKind.REPOSITORY,
                "repo-provider",
                content,
                metadata={secrets[1]: secrets[2], "authorization": secrets[3]},
            )
        ]
    )
    failed = FakeProvider(error=RuntimeError(content))
    broker = ContextBroker(clock=lambda: NOW)
    broker.register("repo-provider", provider, kinds=(ContextKind.REPOSITORY,))
    broker.register("selection-provider", failed, kinds=(ContextKind.SELECTION,))

    snapshot = broker.capture(
        request(
            (ContextKind.REPOSITORY, ContextKind.SELECTION),
            shareable=(ContextKind.REPOSITORY,),
        ),
        target=target(content),
    )
    serialized = json.dumps(broker.status(snapshot), sort_keys=True)

    assert broker.status(snapshot)["state"] == "degraded"
    for secret in secrets:
        assert secret not in serialized
    assert content not in serialized
    assert set(broker.status(snapshot)) == {
        "schema_version",
        "state",
        "created_at",
        "expires_at",
        "item_count",
        "requested",
        "omissions",
    }


def test_explicit_provider_allowlist_or_mapping_never_falls_back():
    provider = FakeProvider([item(ContextKind.REPOSITORY, "repo-provider")])
    broker = ContextBroker(clock=lambda: NOW)
    broker.register("repo-provider", provider, kinds=(ContextKind.REPOSITORY,))

    with pytest.raises(RequiredContextUnavailable) as captured:
        broker.capture(
            request(
                (ContextKind.REPOSITORY,),
                required=(ContextKind.REPOSITORY,),
                providers=frozenset(("different-provider",)),
            ),
            target=target(),
        )
    assert captured.value.reason == "not-authorized"
    assert provider.calls == []

    optional = broker.capture(
        request(
            (ContextKind.REPOSITORY,),
            providers={"repository": "different-provider"},
        ),
        target=target(),
    )
    assert optional.items == ()
    assert broker.manifest(optional)["omissions"][0] == {
        "kind": "repository",
        "provider": "different-provider",
        "reason": "not-registered",
    }
    assert provider.calls == []
