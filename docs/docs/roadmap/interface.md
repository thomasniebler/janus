# Phase 4: The Interface

Phase 4 focuses on making Janus more accessible and visually interactive through a Web Dashboard and a System Tray application.

## Key Changes

- **Web Dashboard:** A local web interface (powered by **FastAPI** and **Jinja2/HTMX**) to:
    - Review your **Morning Briefing** in a clean, visual format.
    - Read your **Afternoon Digests** without needing to check logs.
    - Manage **Email and Planner accounts** via a UI instead of the CLI.
    - Review and "Correct" AI classifications (Feeding into the learning loop).
- **System Tray Icon:** A background process (via **pystray**) to:
    - Quickly see if Janus is actively monitoring.
    - Trigger a manual **Sync** with all providers.
    - Open the **Dashboard** or trigger a **Briefing** with one click.
    - Pause/Resume notifications.

## Proposed Strategy

1.  **FastAPI REST API:** Refactor our logic to be easily accessible via HTTP endpoints.
2.  **Dashboard UI:** Build a responsive, simple local dashboard.
3.  **Tray Integration:** Bridge the background agent with a persistent system tray icon.
4.  **Interactive Loop:** A simple "This was not important" or "This was important" button on emails to help Janus learn your preferences.

## Visual Direction

The dashboard will favor a clean, "passive assistant" aesthetic:
- **Calm Colors:** Soft blues and greys to minimize digital noise.
- **Card-Based Layout:** For quick scanning of events, tasks, and filtered emails.
- **Focus Elements:** Highlight only the "Important" items of the hour.
