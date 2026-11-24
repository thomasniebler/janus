# Phase 3: The Planner

In Phase 3, Janus moves beyond just monitoring emails to become a true daily companion by integrating your calendars and task management systems.

## Key Changes

- **Unified Context:** Janus will fetch your upcoming calendar events and active tasks from Jira and Azure DevOps.
- **Morning Briefing:** A new interactive CLI command to help you start your day by reviewing your schedule and top tasks.
- **Smarter Filtering:** By knowing what you are working on (tasks) and where you need to be (calendar), Janus can better judge if an email is truly "Important".

## Proposed Strategy

1.  **OAuth/API Integration:** Establish secure connections to Google, Microsoft, Atlassian, and Azure DevOps APIs.
2.  **Interactive Briefing:** The `janus brief` command will use the LLM to summarize your day and ask for your focus for the next few hours.
3.  **Cross-Context Awareness:** When an email arrives, Janus will check it against your today's schedule. *Example: An email about a project you have a meeting for later today will be marked as Important.*

## New Integration Requirements

- **Calendars:** Google Calendar (OAuth2), Microsoft Office 365 (Microsoft Graph API).
- **Tasks:** Jira (API Token), Azure DevOps (Personal Access Token).
