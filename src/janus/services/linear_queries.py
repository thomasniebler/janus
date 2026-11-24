"""GraphQL documents ``LinearService`` sends to Linear's public API.

Every document is listed in :data:`ALL_OPERATIONS`, and
``tests/test_linear_service.py`` validates each one against a pinned snapshot
of the schema published with ``@linear/sdk``
(``tests/fixtures/linear_schema.graphql.gz``), which is what catches
argument-type drift such as ``ID!`` vs ``String!``.
"""

from __future__ import annotations

_ISSUE_FIELDS_FRAGMENT = """
fragment JanusIssueFields on Issue {
  id
  identifier
  title
  description
  url
  updatedAt
  priority
  state {
    name
    type
  }
  parent {
    id
    identifier
    title
  }
  assignee {
    id
    displayName
    name
    email
  }
}
"""

VIEWER_QUERY = """
query Viewer {
  viewer {
    displayName
    name
  }
}
"""

#: A team by key or by exact name, both case-insensitive (JAN-330).
TEAM_LOOKUP_QUERY = """
query TeamLookup($ref: String!) {
  teams(first: 10, filter: { or: [{ key: { eqIgnoreCase: $ref } }, { name: { eqIgnoreCase: $ref } }] }) {
    nodes {
      id
      key
      name
    }
  }
}
"""

#: Every team the API key can see (JAN-330).
TEAMS_QUERY = """
query Teams($first: Int!) {
  teams(first: $first) {
    nodes {
      id
      key
      name
    }
  }
}
"""

TEAM_ISSUES_QUERY = (
    """
query TeamIssues($teamId: String!, $first: Int!, $filter: IssueFilter) {
  team(id: $teamId) {
    id
    name
    issues(first: $first, orderBy: updatedAt, filter: $filter) {
      nodes {
        ...JanusIssueFields
      }
    }
  }
}
"""
    + _ISSUE_FIELDS_FRAGMENT
)

#: The team an issue (UUID or identifier such as ``ENG-123``) belongs to (JAN-333).
ISSUE_TEAM_QUERY = """
query IssueTeam($id: String!) {
  issue(id: $id) {
    id
    identifier
    team {
      id
      key
      name
    }
  }
}
"""

#: A team's workflow states (JAN-333).
WORKFLOW_STATES_QUERY = """
query WorkflowStates($teamId: String!) {
  team(id: $teamId) {
    id
    states(first: 100) {
      nodes {
        id
        name
        type
        position
      }
    }
  }
}
"""

USERS_QUERY = """
query Users($first: Int!) {
  users(first: $first) {
    nodes {
      id
      displayName
      name
      email
    }
  }
}
"""

ISSUE_CREATE_MUTATION = (
    """
mutation IssueCreate($input: IssueCreateInput!) {
  issueCreate(input: $input) {
    success
    issue {
      ...JanusIssueFields
    }
  }
}
"""
    + _ISSUE_FIELDS_FRAGMENT
)

ISSUE_UPDATE_MUTATION = (
    """
mutation IssueUpdate($id: String!, $input: IssueUpdateInput!) {
  issueUpdate(id: $id, input: $input) {
    success
    issue {
      ...JanusIssueFields
    }
  }
}
"""
    + _ISSUE_FIELDS_FRAGMENT
)

#: A Markdown comment on an issue (JAN-333).
COMMENT_CREATE_MUTATION = """
mutation CommentCreate($input: CommentCreateInput!) {
  commentCreate(input: $input) {
    success
    comment {
      id
      body
      url
      createdAt
      issue {
        id
        identifier
      }
    }
  }
}
"""

#: Every GraphQL document the service sends; the schema-validation test
#: iterates this tuple so a new operation cannot skip validation.
ALL_OPERATIONS: tuple[str, ...] = (
    VIEWER_QUERY,
    TEAM_LOOKUP_QUERY,
    TEAMS_QUERY,
    TEAM_ISSUES_QUERY,
    ISSUE_TEAM_QUERY,
    WORKFLOW_STATES_QUERY,
    USERS_QUERY,
    ISSUE_CREATE_MUTATION,
    ISSUE_UPDATE_MUTATION,
    COMMENT_CREATE_MUTATION,
)
