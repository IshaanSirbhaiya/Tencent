import pytest

from databridge_adapter_agents import RegistryError
from databridge_workflow_spec import dump_spec, load_spec


def test_create_get_list_roundtrip(env, spec_factory):
    spec = env.registry.create(spec_factory(), by="staff:ops")
    assert env.registry.get(spec.id) == spec
    assert [s.id for s in env.registry.list()] == [spec.id]
    # the file on disk is exactly the frozen contract's own serialisation
    assert env.registry.spec_path(spec.id).read_text(encoding="utf-8") == dump_spec(spec)
    with pytest.raises(RegistryError):
        env.registry.create(spec_factory())


def test_update_keeps_created_from_and_id_immutable(env, spec_factory):
    spec = env.registry.create(spec_factory())
    changed = env.registry.update(spec.id, {"freshness": {"max_age_hours": 6}}, by="staff:ops")
    assert changed.freshness.max_age_hours == 6
    assert changed.created_from == spec.created_from
    for field, value in (("id", "other"), ("created_from", "reworded")):
        with pytest.raises(RegistryError):
            env.registry.update(spec.id, {field: value})
    # an update that breaks the contract is refused, and the file is unchanged
    before = env.registry.spec_path(spec.id).read_text(encoding="utf-8")
    with pytest.raises(RegistryError):
        env.registry.update(spec.id, {"plans": {"not-managed": "approved"}})
    assert env.registry.spec_path(spec.id).read_text(encoding="utf-8") == before


def test_pause_resume_and_delete_is_a_status_not_a_file_deletion(env, spec_factory):
    spec = env.registry.create(spec_factory())
    assert env.registry.pause(spec.id).status == "paused"
    assert env.registry.resume(spec.id).status == "active"
    env.registry.delete(spec.id, by="staff:ops", reason="replaced")
    path = env.registry.spec_path(spec.id)
    assert path.is_file(), "the spec file must survive a delete"
    assert load_spec(path).status == "paused"
    assert env.registry.list() == []
    assert [s.id for s in env.registry.list(include_deleted=True)] == [spec.id]
    with pytest.raises(RegistryError):
        env.registry.get(spec.id)
    with pytest.raises(RegistryError):
        env.registry.create(spec_factory())
    actions = [h["action"] for h in env.registry.history(spec.id)]
    assert actions == ["create", "pause", "resume", "pause", "delete"]
    assert all(h["spec_digest"].startswith("sha256:") for h in env.registry.history(spec.id))


def test_unparseable_spec_file_is_reported_not_dropped(env, spec_factory):
    env.registry.create(spec_factory())
    (env.registry.root / "broken.yaml").write_text("spec: adapter_agent/v1\nid: Bad Id\n",
                                                   encoding="utf-8")
    assert len(env.registry.list()) == 1
    assert "broken.yaml" in env.registry.problems
