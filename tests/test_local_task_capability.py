"""Coverage for the Janus-owned local task hierarchy and agent capability."""

import pytest

from janus import models, security
from janus.agent_tools import build_tool_set
from janus.services.local_planner_service import LocalPlannerService


def _user(db, username: str, *, admin: bool = False) -> models.User:
    user = models.User(
        username=username,
        hashed_password=security.security_service.get_password_hash("secret"),
        is_admin=admin,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def local_auth_client(client, db):
    """Return an authenticated API client local to this focused test module."""
    db.add(
        models.User(
            username="local-api-user",
            hashed_password=security.security_service.get_password_hash("secret"),
            is_admin=True,
        )
    )
    db.commit()
    response = client.post("/api/auth/login", json={"username": "local-api-user", "password": "secret"})
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return client


def test_local_tasks_validate_parent_ownership_and_cycles(db):
    """Parent links stay user-scoped and cannot create self-ancestry."""
    owner = _user(db, "owner")
    other = _user(db, "other")
    service = LocalPlannerService(db)
    root = service.create_task(owner.id, {"title": "Root"})
    child = service.create_task(owner.id, {"title": "Child", "parent_task_id": root["id"]})
    foreign = service.create_task(other.id, {"title": "Foreign"})

    with pytest.raises(ValueError, match="current user"):
        service.create_task(owner.id, {"title": "Bad child", "parent_task_id": foreign["id"]})
    with pytest.raises(ValueError, match="descendant"):
        service.update_task(owner.id, root["id"], {"parent_task_id": child["id"]})
    with pytest.raises(ValueError, match="own parent"):
        service.update_task(owner.id, child["id"], {"parent_task_id": child["id"]})


def test_local_task_completion_does_not_cascade_and_tree_serializes_children(db):
    """Completing a parent leaves open children visible in the tree summary."""
    user = _user(db, "tree-user")
    service = LocalPlannerService(db)
    root = service.create_task(user.id, {"title": "Ship feature", "urgency": models.TaskUrgency.HIGH})
    child = service.create_task(
        user.id,
        {"title": "Write tests", "parent_task_id": root["id"], "sort_order": 1, "urgency": "critical"},
    )

    service.complete_task(user.id, root["id"])
    open_tree = service.list_local_task_tree(user.id)
    tree = service.list_local_task_tree(user.id, include_completed=True)

    assert open_tree[0]["id"] == root["id"]
    assert open_tree[0]["status"] == "Done"
    assert open_tree[0]["children"][0]["id"] == child["id"]
    assert tree[0]["id"] == root["id"]
    assert tree[0]["urgency"] == "high"
    assert tree[0]["child_count"] == 1
    assert tree[0]["open_child_count"] == 1
    assert tree[0]["children"][0]["id"] == child["id"]
    assert tree[0]["children"][0]["status"] == "Todo"


def test_local_task_tree_filters_keep_matching_descendant_ancestors(db):
    """Status and urgency filters should keep the nonmatching parents of matching descendants."""
    user = _user(db, "filtered-tree-user")
    service = LocalPlannerService(db)
    parent = service.create_task(user.id, {"title": "Parent", "status": models.TaskStatus.IN_PROGRESS})
    child = service.create_task(
        user.id,
        {
            "title": "Child",
            "parent_task_id": parent["id"],
            "status": models.TaskStatus.TODO,
            "urgency": models.TaskUrgency.CRITICAL,
        },
    )

    status_tree = service.list_local_task_tree(user.id, status=models.TaskStatus.TODO)
    urgency_tree = service.list_local_task_tree(user.id, urgency=models.TaskUrgency.CRITICAL)

    assert status_tree[0]["id"] == parent["id"]
    assert status_tree[0]["status"] == "In Progress"
    assert status_tree[0]["children"][0]["id"] == child["id"]
    assert urgency_tree[0]["id"] == parent["id"]
    assert urgency_tree[0]["children"][0]["id"] == child["id"]
    assert urgency_tree[0]["children"][0]["urgency"] == "critical"


def test_local_task_tree_filters_keep_completed_ancestors_for_matching_open_children(db):
    """Urgency filters should still keep a completed ancestor when an open child matches."""
    user = _user(db, "completed-filtered-tree-user")
    service = LocalPlannerService(db)
    parent = service.create_task(user.id, {"title": "Parent", "urgency": models.TaskUrgency.NORMAL})
    child = service.create_task(
        user.id,
        {
            "title": "Critical child",
            "parent_task_id": parent["id"],
            "urgency": models.TaskUrgency.CRITICAL,
        },
    )

    service.complete_task(user.id, parent["id"])
    tree = service.list_local_task_tree(user.id, urgency=models.TaskUrgency.CRITICAL)

    assert tree[0]["id"] == parent["id"]
    assert tree[0]["status"] == "Done"
    assert tree[0]["children"][0]["id"] == child["id"]
    assert tree[0]["children"][0]["urgency"] == "critical"


def test_local_task_tools_are_ungated(db):
    """The local planner is ungated (JAN-201): every user gets read and write tools."""
    user = _user(db, "capability-user")
    all_local_tools = {
        "list_local_tasks",
        "get_local_task",
        "create_local_task",
        "update_local_task",
        "complete_local_task",
        "move_local_task",
        "reorder_local_task",
    }
    assert all_local_tools <= {tool.name for tool in build_tool_set(db, user)}


def test_local_task_tools_enforce_user_isolation_and_mutate(db):
    """Tool reads and writes use the current user, even when IDs overlap in prompts."""
    owner = _user(db, "tool-owner")
    other = _user(db, "tool-other")
    owner_task = LocalPlannerService(db).create_task(owner.id, {"title": "Owner task"})
    tools = {tool.name: tool for tool in build_tool_set(db, owner)}

    created = tools["create_local_task"].invoke(
        {"title": "Child", "parent_task_id": owner_task["id"], "urgency": "critical"}
    )
    assert created["parent_task_id"] == owner_task["id"]
    assert tools["get_local_task"].invoke({"task_id": created["id"]})["title"] == "Child"
    assert tools["complete_local_task"].invoke({"task_id": created["id"]})["status"] == "Done"
    assert LocalPlannerService(db).get_local_task(other.id, created["id"]) is None

    with pytest.raises(ValueError, match="not found"):
        tools["get_local_task"].invoke({"task_id": 999999})


def test_local_task_api_exposes_tree_and_rejects_cycles(local_auth_client):
    """The authenticated API exposes hierarchy fields and maps invalid moves to 422."""
    root = local_auth_client.post("/api/planner/local/tasks", json={"title": "API root", "urgency": "high"}).json()
    child = local_auth_client.post(
        "/api/planner/local/tasks",
        json={"title": "API child", "parent_task_id": root["id"], "sort_order": 2},
    ).json()

    assert child["parent_task_id"] == root["id"]
    tree = local_auth_client.get("/api/planner/local/tasks/tree", params={"include_completed": True})
    assert tree.status_code == 200
    assert tree.json()[0]["children"][0]["id"] == child["id"]

    cycle = local_auth_client.post(f"/api/planner/local/tasks/{root['id']}/move", json={"parent_task_id": child["id"]})
    assert cycle.status_code == 422
    assert "descendant" in cycle.json()["detail"]
