# Changelog

All notable changes to this project will be documented in this file.

## [1.16.0](https://github.com/thomasniebler/janus/compare/v1.15.0...v1.16.0) (2026-11-26)


### Features

* **install:** installable janus-assistant wheel, PyPI publishing, and janus update for tool installs ([#2](https://github.com/thomasniebler/janus/issues/2)) ([7b2059e](https://github.com/thomasniebler/janus/commit/7b2059e5b613168ea7af46ddb4afc73140513d5d))
* **service:** janus service install/status/logs/uninstall as a systemd user service ([#3](https://github.com/thomasniebler/janus/issues/3)) ([63543b3](https://github.com/thomasniebler/janus/commit/63543b3453f56dd93df8c7f9899e0fe1d71797dc))
* **skills:** suggest skills from repeated tool routines in chats (JAN-407) ([#11](https://github.com/thomasniebler/janus/issues/11)) ([9f344c8](https://github.com/thomasniebler/janus/commit/9f344c8684006c6ecf3ce7bb1cfe53ce363f245c))
* **stats:** count LLM calls and tokens per user, surface and model (JAN-408) ([#10](https://github.com/thomasniebler/janus/issues/10)) ([9c38423](https://github.com/thomasniebler/janus/commit/9c38423079796a2467d98c43a9fb0359b67baa80))
* **telegram:** connect your own bot, trigger on its messages, and get alerts (JAN-405) ([#12](https://github.com/thomasniebler/janus/issues/12)) ([c52f53f](https://github.com/thomasniebler/janus/commit/c52f53fe75a6831d4a082d87847dff5702f4f9cb))
* **tools:** ask local coding-agent CLIs for help (JAN-403) ([#4](https://github.com/thomasniebler/janus/issues/4)) ([4d7aef3](https://github.com/thomasniebler/janus/commit/4d7aef30a5019f746486493692e869654c615c2a))
* **tools:** ask remote A2A agents for help (JAN-404) ([#6](https://github.com/thomasniebler/janus/issues/6)) ([25a047e](https://github.com/thomasniebler/janus/commit/25a047ebea39299e5f8086d1789a2b1d301e2daa))
* **triggers:** inbound webhooks that fire webhook.received (JAN-406) ([#8](https://github.com/thomasniebler/janus/issues/8)) ([3cdda1c](https://github.com/thomasniebler/janus/commit/3cdda1c6ae889d9ab7c0bf71ee8c5065d143a927))


### Documentation

* **usage:** say that cost, balances and budgets are not supported (JAN-408) ([#13](https://github.com/thomasniebler/janus/issues/13)) ([4965c69](https://github.com/thomasniebler/janus/commit/4965c69ed8c646337404c55a194dd2d02924a0ae))

## [1.15.0](https://github.com/thomasniebler/janus/releases/tag/v1.15.0) (2026-11-24)

First public release. Janus is now open source under the MIT license.

### Features

* **inbox:** action inbox with scoped device tokens for desktop popups (#633)
* **secretary:** proactive hourly Secretary trigger with WhatsApp alerts (#628)

### Miscellaneous

* **deploy:** the deploy target (host, path, service, public URL) comes from repository secrets and variables, and every deploy job skips itself unless `DEPLOY_ENABLED` is `true` (#636)
* **repo:** removed the private development automation and review notes, added the MIT license (#637, #638)

## [1.14.1](https://github.com/thomasniebler/janus/compare/v1.14.0...v1.14.1) (2026-11-16)


### Bug Fixes

* **planner:** send valid RFC3339 UTC bounds to Google Calendar ([#629](https://github.com/thomasniebler/janus/issues/629)) ([d85ea81](https://github.com/thomasniebler/janus/commit/d85ea8183f7b6e76351ebc73c0d6b9f40592240d))

## [1.14.0](https://github.com/thomasniebler/janus/compare/v1.13.0...v1.14.0) (2026-11-12)


### Features

* **connections:** connect Google and Microsoft from settings, planner and mail ([#621](https://github.com/thomasniebler/janus/issues/621)) ([aec9538](https://github.com/thomasniebler/janus/commit/aec9538b7f63c3f0c17dfa049c47168eb3d8a324))
* **mail:** Outlook mailboxes over Microsoft Graph ([#625](https://github.com/thomasniebler/janus/issues/625)) ([fc37233](https://github.com/thomasniebler/janus/commit/fc3723315c57105ae5a22b6243876b3cf235dd86))
* **version:** show the Janus version and flash what changed after an update ([#623](https://github.com/thomasniebler/janus/issues/623)) ([26fb648](https://github.com/thomasniebler/janus/commit/26fb648e152c4267c82d261a06d9cc231da98d7f))

## [1.13.0](https://github.com/thomasniebler/janus/compare/v1.12.0...v1.13.0) (2026-11-11)


### Features

* **chat:** narrate multi-step turns with a plan first and one line per round ([#619](https://github.com/thomasniebler/janus/issues/619)) ([da9239f](https://github.com/thomasniebler/janus/commit/da9239fc4ed20f25dd22a85f29ea2976bb38ce0d))
* **stats:** approval stats for Janus proposals on the Stats tab (JAN-371) ([#618](https://github.com/thomasniebler/janus/issues/618)) ([6ccf440](https://github.com/thomasniebler/janus/commit/6ccf440e60595570ed58522a7e3d8fcd53b1e2ce))
* **triggers:** payload filters and rate limits for event triggers (JAN-363) ([#614](https://github.com/thomasniebler/janus/issues/614)) ([f020ddf](https://github.com/thomasniebler/janus/commit/f020ddf11ae3ae6be63bf7975f4bf82be7880880))
* **triggers:** WhatsApp and planner trigger events (JAN-361, JAN-362) ([#609](https://github.com/thomasniebler/janus/issues/609)) ([bc96002](https://github.com/thomasniebler/janus/commit/bc960025bcd5fba1208e14d5b3b89f2fd97fbe6e))
* **update:** self-hosted auto-update from GitHub releases ([#615](https://github.com/thomasniebler/janus/issues/615)) ([2c28430](https://github.com/thomasniebler/janus/commit/2c2843043790630ee2c869cfd6538c84d0a8d24b))


### Bug Fixes

* **db:** WAL mode and busy timeout for SQLite (JAN-382), plan GitHub Issues migration (JAN-393) ([#613](https://github.com/thomasniebler/janus/issues/613)) ([5477207](https://github.com/thomasniebler/janus/commit/5477207426fa34c8177c04402b2b8d0a9376bec0))
* store timestamps as UTC and render them in local time (JAN-394) ([#616](https://github.com/thomasniebler/janus/issues/616)) ([2aed1a7](https://github.com/thomasniebler/janus/commit/2aed1a7bd5f95cdac6356d86051082902d43744e))

## [1.12.0](https://github.com/thomasniebler/janus/compare/v1.11.0...v1.12.0) (2026-11-10)


### Features

* Microsoft 365 (Outlook, Teams) and read-only Azure tools ([#610](https://github.com/thomasniebler/janus/issues/610)) ([b75f8f6](https://github.com/thomasniebler/janus/commit/b75f8f60ff9eda85de51972fe1b673360528a2fc))

## [1.11.0](https://github.com/thomasniebler/janus/compare/v1.10.0...v1.11.0) (2026-11-10)


### Features

* **tools:** Allow-everything / Always-allow permission modes and WhatsApp sending (JAN-359, JAN-360) ([#608](https://github.com/thomasniebler/janus/issues/608)) ([9019584](https://github.com/thomasniebler/janus/commit/9019584dd36b1f45d08fa1f630638c92bf2dc918))


### Bug Fixes

* **llm:** keep Chat Completions-only endpoints off the Responses API (JAN-354) ([#604](https://github.com/thomasniebler/janus/issues/604)) ([633a81c](https://github.com/thomasniebler/janus/commit/633a81c4dd620ff96de3c69d03bac341878d5b9b))
* **llm:** reach Azure OpenAI through its v1 endpoint so gpt-6 deployments work with tools (JAN-356) ([#606](https://github.com/thomasniebler/janus/issues/606)) ([8739e3d](https://github.com/thomasniebler/janus/commit/8739e3d3e9308999a0c4ca59e5169009a464dcc4))
* **llm:** route Azure v1 deployments by their underlying model (JAN-357) ([#607](https://github.com/thomasniebler/janus/issues/607)) ([1379256](https://github.com/thomasniebler/janus/commit/13792566a84d21a15fe2a299afabde7471ff2730))

## [1.10.0](https://github.com/thomasniebler/janus/compare/v1.9.0...v1.10.0) (2026-11-10)


### Features

* **api:** offline OpenAPI export and typed client drift check (JAN-139) ([#536](https://github.com/thomasniebler/janus/issues/536)) ([d409daa](https://github.com/thomasniebler/janus/commit/d409daa1e768bd6aec8a4b33c24316ef1d080eb1))
* **character:** bounded adaptive character state with audit and rollback (JAN-301) ([#560](https://github.com/thomasniebler/janus/issues/560)) ([595525d](https://github.com/thomasniebler/janus/commit/595525dc41ce9dbe35d8ff5ac170204e10975a27))
* **chat:** show a conversation's delegated tasks in chat (JAN-208) ([#539](https://github.com/thomasniebler/janus/issues/539)) ([b733265](https://github.com/thomasniebler/janus/commit/b73326593ccad372d7fcc833215e218f264ef578))
* **delegation:** trigger runs wait on delegated children; interrupted delegations re-run once (JAN-207) ([#545](https://github.com/thomasniebler/janus/issues/545)) ([7a82f01](https://github.com/thomasniebler/janus/commit/7a82f01b6be72efd015a0d24d447ed37d7f26216))
* **frontend:** validate auth, email and planner API responses at the client boundary (JAN-142) ([#543](https://github.com/thomasniebler/janus/issues/543)) ([3c3513c](https://github.com/thomasniebler/janus/commit/3c3513c473551b1600018ead3334f4366885f184))
* **frontend:** validate the remaining API response groups at the client boundary (JAN-335) ([#562](https://github.com/thomasniebler/janus/issues/562)) ([5b69318](https://github.com/thomasniebler/janus/commit/5b693182ee32c5588b4a14cfb84c57f09c17f037))
* **llm:** add GitHub Models and Azure OpenAI providers (JAN-340) ([#592](https://github.com/thomasniebler/janus/issues/592)) ([7c26e95](https://github.com/thomasniebler/janus/commit/7c26e95123a90a35e87e87383d46ea94432e45bf))
* **llm:** list Azure OpenAI deployments in the model dropdown (JAN-349) ([#601](https://github.com/thomasniebler/janus/issues/601)) ([6130fdd](https://github.com/thomasniebler/janus/commit/6130fdd9867d8564967a9cfce1e8b3a66088776c))
* **mcp:** offer discovered remote MCP tools to Janus in chat (JAN-343) ([#595](https://github.com/thomasniebler/janus/issues/595)) ([8d1811a](https://github.com/thomasniebler/janus/commit/8d1811a29bdd68f71b3f9e02c9a7516518797dcb))
* **mcp:** static bearer token for remote MCP servers (JAN-342) ([#594](https://github.com/thomasniebler/janus/issues/594)) ([0512c43](https://github.com/thomasniebler/janus/commit/0512c4370f3b4e4f655b84185e1a08fb9c4f6654))
* **ops:** verify release version and record deploy/availability metrics (JAN-269) ([#542](https://github.com/thomasniebler/janus/issues/542)) ([c97ca50](https://github.com/thomasniebler/janus/commit/c97ca506da7de345fc2d30afadfd430b07cfb0ea))
* **quality:** monthly agent-assisted structural review fed by the quality report (JAN-271) ([#556](https://github.com/thomasniebler/janus/issues/556)) ([49336e8](https://github.com/thomasniebler/janus/commit/49336e8a626728443e940846339d52fa4e4d91ff))
* **quality:** weekly quality report with fixed schema and durable history (JAN-270) ([#549](https://github.com/thomasniebler/janus/issues/549)) ([87baa36](https://github.com/thomasniebler/janus/commit/87baa365122e0d9976ea8a3152d1e7498eae9568))
* **runtime:** brain-selected, task-scoped toolsets approved by policy (JAN-300) ([#559](https://github.com/thomasniebler/janus/issues/559)) ([59e0f41](https://github.com/thomasniebler/janus/commit/59e0f4193842e2ae8babbfe769bdc67c8129bc70))
* **runtime:** let trigger runs start a conversation with their owner (JAN-319) ([#552](https://github.com/thomasniebler/janus/issues/552)) ([5572a62](https://github.com/thomasniebler/janus/commit/5572a625cd14daee8ecb98f1f4f317fcab2a9bbf))
* **safety:** add optional cached model-based prompt-injection classifier (JAN-289) ([#534](https://github.com/thomasniebler/janus/issues/534)) ([88fa74d](https://github.com/thomasniebler/janus/commit/88fa74d08bddbcb108467d5f28a350cdc7e1d670))
* **tasks:** Agent Tasks page with persisted plan steps and tool trace (JAN-204) ([#546](https://github.com/thomasniebler/janus/issues/546)) ([8587160](https://github.com/thomasniebler/janus/commit/8587160190a68aac073c1b298362ebd298b6e1c0))
* **tools:** admin-only, opt-in shell tool for local installs (JAN-353) ([#603](https://github.com/thomasniebler/janus/issues/603)) ([f192398](https://github.com/thomasniebler/janus/commit/f192398bf88abb816118b7f3253b10fa4c28639f))
* **tools:** per-tool "In chat" switch for confirmation-required tools (JAN-306) ([#535](https://github.com/thomasniebler/janus/issues/535)) ([e470dd4](https://github.com/thomasniebler/janus/commit/e470dd492770c49b831a001c9951db19250aed1a))
* **tools:** read-only WhatsApp toolbox for chat (JAN-347) ([#600](https://github.com/thomasniebler/janus/issues/600)) ([91a11d5](https://github.com/thomasniebler/janus/commit/91a11d5254fed14cfec32c2c05c1bd227b822d28))
* **wiki:** knowledge stack provenance, citation drill-down, and topic/entity graph (JAN-334) ([#538](https://github.com/thomasniebler/janus/issues/538)) ([51648fa](https://github.com/thomasniebler/janus/commit/51648fa207f76eb5c3964695ed7ac327e67c2dca))


### Bug Fixes

* **chat:** de-flake the In chat switch test and end a turn cleanly when its conversation is deleted ([#557](https://github.com/thomasniebler/janus/issues/557)) ([59c788b](https://github.com/thomasniebler/janus/commit/59c788b360427965b1ac399474ca57e9248446c1))
* **dashboard:** render the task widget as the parent/subtask tree (JAN-317) ([#531](https://github.com/thomasniebler/janus/issues/531)) ([05db41d](https://github.com/thomasniebler/janus/commit/05db41d285b58856fcaf002f27aceaeb6475391c))
* **db:** align models and migrations, guard against schema drift (JAN-338) ([#564](https://github.com/thomasniebler/janus/issues/564)) ([8c57de8](https://github.com/thomasniebler/janus/commit/8c57de871565e6bbcf1078be54da2a656df3613b))
* **llm:** keep compacted context JSON-shaped under tiny budgets (JAN-145) ([#530](https://github.com/thomasniebler/janus/issues/530)) ([b8ddab4](https://github.com/thomasniebler/janus/commit/b8ddab485c8d9eb9d1e4c8d1ddf92057a5635974))
* **llm:** stop sending temperature to OpenAI-family providers (JAN-352) ([#602](https://github.com/thomasniebler/janus/issues/602)) ([19d1434](https://github.com/thomasniebler/janus/commit/19d143458a6e161df883837f1164f120f764b400))
* **planner:** repair the Google token exchange and name its failures (JAN-318) ([#537](https://github.com/thomasniebler/janus/issues/537)) ([e4dfb14](https://github.com/thomasniebler/janus/commit/e4dfb14d64b35cac965d6b0ca75f812eb22521fa))
* **security:** use the mail owner's notification preference for background notifications (JAN-298) ([#528](https://github.com/thomasniebler/janus/issues/528)) ([068cce9](https://github.com/thomasniebler/janus/commit/068cce9cb19c85d10004771ef7f5d52c16aae169))
* **whatsapp:** accept millisecond Neonize timestamps for live messages (JAN-346) ([#597](https://github.com/thomasniebler/janus/issues/597)) ([64fd5c5](https://github.com/thomasniebler/janus/commit/64fd5c56af533de5856f928e3d4e1616f984d469))


### Performance Improvements

* **frontend:** code-split app shells and route pages (JAN-179) ([#533](https://github.com/thomasniebler/janus/issues/533)) ([4f159e2](https://github.com/thomasniebler/janus/commit/4f159e28d0a7bb7fae72d6b55b1d0e1dc788aa11))


### Documentation

* **feature-specs:** backfill Status/Linear headers on undated specs (JAN-180) ([#540](https://github.com/thomasniebler/janus/issues/540)) ([46f5551](https://github.com/thomasniebler/janus/commit/46f55513ae772d742d0ba12bd361c1a172829435))
* **runtime:** close out the plan-driven runtime spec (JAN-205) ([#561](https://github.com/thomasniebler/janus/issues/561)) ([f679db9](https://github.com/thomasniebler/janus/commit/f679db93c8f2fcef2122634238b64134fe2a88ee))

## [1.9.0](https://github.com/thomasniebler/janus/compare/v1.8.0...v1.9.0) (2026-10-11)


### Features

* **chat:** plan/step timeline panel for the current turn (JAN-316) ([#524](https://github.com/thomasniebler/janus/issues/524)) ([c0c75e5](https://github.com/thomasniebler/janus/commit/c0c75e517efa0abad7d965a7ffc18237a17c80bd))
* **chat:** UI introspection toolbox — page outline and element styles (JAN-282..285) ([#523](https://github.com/thomasniebler/janus/issues/523)) ([1494ebf](https://github.com/thomasniebler/janus/commit/1494ebf01d1b039ebac453654928bd7215a24c52))
* **linear:** discover teams, require issue descriptions, update and comment on issues ([#527](https://github.com/thomasniebler/janus/issues/527)) ([ad1f01d](https://github.com/thomasniebler/janus/commit/ad1f01d05a5c1d520445579260ec81ee5048f6b7))
* **wiki:** render [[double-bracket]] links in wiki pages and chat (JAN-331) ([#526](https://github.com/thomasniebler/janus/issues/526)) ([d9bfd17](https://github.com/thomasniebler/janus/commit/d9bfd17949af7949c6adae28fe6fe288ebd21d30))

## [1.8.0](https://github.com/thomasniebler/janus/compare/v1.7.0...v1.8.0) (2026-10-08)


### Features

* **agent:** read explicit web pages with a web_fetch tool (JAN-329) ([#520](https://github.com/thomasniebler/janus/issues/520)) ([ead8f2a](https://github.com/thomasniebler/janus/commit/ead8f2a82dd46158cab3a0578ba5e4ee51b7b9a0))
* **self-evolution:** autonomous self-evolution loop (JAN-320) ([#521](https://github.com/thomasniebler/janus/issues/521)) ([42945db](https://github.com/thomasniebler/janus/commit/42945dbf7a2213d4fb59e201a8b061afa49d19f8))


### Bug Fixes

* **ci:** retry the release host checks when the SSH connection is closed ([#518](https://github.com/thomasniebler/janus/issues/518)) ([6980bd5](https://github.com/thomasniebler/janus/commit/6980bd50d09c55efb5ebe2cb57e60862b82d824a))

## [1.7.0](https://github.com/thomasniebler/janus/compare/v1.6.0...v1.7.0) (2026-10-06)


### Features

* **agent:** generate_image chat tool with cloud providers and authenticated image storage (JAN-253) ([#502](https://github.com/thomasniebler/janus/issues/502)) ([b29063e](https://github.com/thomasniebler/janus/commit/b29063e13842ca6d82fbe4f80063f467b04e373d))
* **chat:** validated generative UI blocks in chat replies (JAN-252) ([#500](https://github.com/thomasniebler/janus/issues/500)) ([ec42789](https://github.com/thomasniebler/janus/commit/ec4278971497d931a5adfce439ed3d4c13c7a123))


### Bug Fixes

* **deps:** bump source-map-js past GHSA-68fv-2mgg-jv7q in both frontends ([#515](https://github.com/thomasniebler/janus/issues/515)) ([2c90d32](https://github.com/thomasniebler/janus/commit/2c90d324c68ab4710177b87c7321462011fd5050))


### Documentation

* list every LOCKED_UNATTENDED entry in CLAUDE.md ([#514](https://github.com/thomasniebler/janus/issues/514)) ([e711ece](https://github.com/thomasniebler/janus/commit/e711eceb0c41f6e4ddee8d747a28d6c0a37eab9c))

## [1.6.0](https://github.com/thomasniebler/janus/compare/v1.5.0...v1.6.0) (2026-10-05)


### Features

* **chat:** allowlisted page actions so Janus can navigate and manipulate the current page (JAN-251) ([#499](https://github.com/thomasniebler/janus/issues/499)) ([83ecff8](https://github.com/thomasniebler/janus/commit/83ecff884b41dc58c5ca008a3744b9bbffda27d2))
* **chat:** send the current page as structured, untrusted context with every message (JAN-250) ([#498](https://github.com/thomasniebler/janus/issues/498)) ([b7cfaf3](https://github.com/thomasniebler/janus/commit/b7cfaf31aed96b29c864a2bc03b3b85e2bb12ce5))


### Bug Fixes

* **security:** scope the agent calendar and task tools to the run owner ([#494](https://github.com/thomasniebler/janus/issues/494)) ([a9f542d](https://github.com/thomasniebler/janus/commit/a9f542d5ae7d00b01d5f10fc35ae5c588b831fb1))

## [1.5.0](https://github.com/thomasniebler/janus/compare/v1.4.0...v1.5.0) (2026-10-04)


### Features

* **chat:** explicit approve/deny step before confirmation-required tools run (JAN-254) ([#491](https://github.com/thomasniebler/janus/issues/491)) ([9b42456](https://github.com/thomasniebler/janus/commit/9b42456eb5a248746c179412049aad50977bca7e))
* **chat:** structured, persisted tool-call events and inspector (JAN-249) ([#484](https://github.com/thomasniebler/janus/issues/484)) ([79c71be](https://github.com/thomasniebler/janus/commit/79c71bed74622d8883fb5db94ab031f18888e510))

## [1.4.0](https://github.com/thomasniebler/janus/compare/v1.3.0...v1.4.0) (2026-10-03)


### Features

* **agent:** Janus can read its own documentation (JAN-297) ([#481](https://github.com/thomasniebler/janus/issues/481)) ([443f6db](https://github.com/thomasniebler/janus/commit/443f6db3e7ec10e0e7f67c50d6d95d45b503fe1d))


### Bug Fixes

* **security:** owner-scope IMAP rule evaluation, reminder memory, and planner outlook (JAN-295, JAN-296) ([#482](https://github.com/thomasniebler/janus/issues/482)) ([a397de7](https://github.com/thomasniebler/janus/commit/a397de7c9f2ee27fb643ddf5829dc54f78836407))
* **security:** require login for the in-app documentation API (JAN-299) ([#483](https://github.com/thomasniebler/janus/issues/483)) ([bec47ae](https://github.com/thomasniebler/janus/commit/bec47aea2bfaeb2db6d609065c8ba9941cb2d7b8))


### Documentation

* close out JAN-169 delivery artifacts (JAN-205) ([#479](https://github.com/thomasniebler/janus/issues/479)) ([2ffaa0b](https://github.com/thomasniebler/janus/commit/2ffaa0b3a61a1f35290656ff4ddf46d73a0e2542))

## [1.3.0](https://github.com/thomasniebler/janus/compare/v1.2.2...v1.3.0) (2026-10-03)


### Features

* **realtime:** live UI refresh for backend mutations via /ws/changes (JAN-104) ([#471](https://github.com/thomasniebler/janus/issues/471)) ([739c9fd](https://github.com/thomasniebler/janus/commit/739c9fd13fc32604f843abb26c8af277ed39cadb))
* **runtime:** owner-scope the event log and guard unattended trigger chains (JAN-272, JAN-274) ([#473](https://github.com/thomasniebler/janus/issues/473)) ([10b3300](https://github.com/thomasniebler/janus/commit/10b3300932d782361839d8e6460298cc1a1b6c68))
* **runtime:** prompt-injection guardrails for untrusted content (JAN-127) ([#472](https://github.com/thomasniebler/janus/issues/472)) ([ba9fbc2](https://github.com/thomasniebler/janus/commit/ba9fbc24f3a5dd017842e1c05a9e51f2ae480874))
* **triggers:** agent-proposed automations the user approves (JAN-235) ([#475](https://github.com/thomasniebler/janus/issues/475)) ([72906b2](https://github.com/thomasniebler/janus/commit/72906b20d97e0335407fa6e1334e4cdde01354c7))
* **triggers:** event catalog with subsystem and event dropdowns (JAN-210) ([#470](https://github.com/thomasniebler/janus/issues/470)) ([209b05b](https://github.com/thomasniebler/janus/commit/209b05b1b6f8fbe349cfae5a8b0718690aac7513))


### Bug Fixes

* **chat:** scrollable /chat history; feat(ui): in-app confirm for unattended tools (JAN-273, JAN-275) ([#468](https://github.com/thomasniebler/janus/issues/468)) ([2f0a9ae](https://github.com/thomasniebler/janus/commit/2f0a9aecb0344b913befaf1dc2bf5a5f8bfc2480))
* release lockfile sync, parallel e2e, in-app confirms, websocket close and origin enum (JAN-290..294) ([#477](https://github.com/thomasniebler/janus/issues/477)) ([e300a5f](https://github.com/thomasniebler/janus/commit/e300a5fa450352eea6d86cc293ccd9af0683de25))
* **security:** scope mail reads, outgoing account, and rule learning to the owner (JAN-286, JAN-287) ([#478](https://github.com/thomasniebler/janus/issues/478)) ([f070ee8](https://github.com/thomasniebler/janus/commit/f070ee82bc49f1541483e9f9558ffaa36ac87f9f))


### Documentation

* consistency pass over the 2026-10-02 merges ([#476](https://github.com/thomasniebler/janus/issues/476)) ([381f4bc](https://github.com/thomasniebler/janus/commit/381f4bc1b13baba04da1cd22a9d52d7ec72d0584))

## [1.2.2](https://github.com/thomasniebler/janus/compare/v1.2.1...v1.2.2) (2026-10-01)


### Bug Fixes

* **deploy:** keep markitdown out of the dev group so host `uv run` stays installable ([#466](https://github.com/thomasniebler/janus/issues/466)) ([8686853](https://github.com/thomasniebler/janus/commit/8686853f4665d6e3edc6e4d7ef5750f0b8a3e00d))

## [1.2.1](https://github.com/thomasniebler/janus/compare/v1.2.0...v1.2.1) (2026-10-01)


### Bug Fixes

* **deploy:** make markitdown optional so the glibc 2.17 host can install Janus ([#464](https://github.com/thomasniebler/janus/issues/464)) ([f2d1424](https://github.com/thomasniebler/janus/commit/f2d1424e6ba20ed9153b2ae288725bee40c10d76))

## [1.2.0](https://github.com/thomasniebler/janus/compare/v1.1.0...v1.2.0) (2026-10-01)


### Features

* **frontend:** shared chat session per tab and a /chat page (JAN-248) ([#441](https://github.com/thomasniebler/janus/issues/441)) ([e5f52e5](https://github.com/thomasniebler/janus/commit/e5f52e55e48f452c82c130012d2c65f57da40663))
* **knowledge:** entity/relation graph with opt-in extraction and budget (JAN-243, part 3a) ([#436](https://github.com/thomasniebler/janus/issues/436)) ([4e37a15](https://github.com/thomasniebler/janus/commit/4e37a15983951820b5c2ad7461f0d16ee8cd3216))
* **knowledge:** filter before ranking, cap vector-only hits, source filter (JAN-245) ([#432](https://github.com/thomasniebler/janus/issues/432)) ([6523177](https://github.com/thomasniebler/janus/commit/65231777da7ecb2228cac324f49c818d964f8b6d))
* **knowledge:** hybrid retrieval with graph expansion and knowledge tools (JAN-243, part 3b) ([#442](https://github.com/thomasniebler/janus/issues/442)) ([a2ce484](https://github.com/thomasniebler/janus/commit/a2ce484477fe9e85a0e49cd79b2afa3dd49505ce))
* **knowledge:** ingest sources, parse documents, and chunk text (JAN-242) ([#434](https://github.com/thomasniebler/janus/issues/434)) ([c465ed6](https://github.com/thomasniebler/janus/commit/c465ed6e01cdb063d95f05199270ca8d15574651))
* **knowledge:** LLM wiki with cited pages, edits, proposals, and tools (JAN-244) ([#443](https://github.com/thomasniebler/janus/issues/443)) ([637210e](https://github.com/thomasniebler/janus/commit/637210e90b7cd3937588295bcaf7247b781fa539))
* **tools:** one per-tool unattended switch replaces the hidden mode allowlists (JAN-255) ([#455](https://github.com/thomasniebler/janus/issues/455)) ([7c47dee](https://github.com/thomasniebler/janus/commit/7c47dee4225ef229cb8677e2ee0b270cab27c031))
* **triggers:** filterable run history, follow-ups as definitions, and a Stats tab (JAN-256, JAN-257, JAN-258) ([#456](https://github.com/thomasniebler/janus/issues/456)) ([32cd470](https://github.com/thomasniebler/janus/commit/32cd470a60cbd23f3663ff9b987a8e4a397f2e33))


### Bug Fixes

* **frontend:** drop a dead initial assignment in BriefingWidget for ESLint 10 ([#462](https://github.com/thomasniebler/janus/issues/462)) ([5ae4fd7](https://github.com/thomasniebler/janus/commit/5ae4fd7505eae5f2673802776f201012dea7bde2))
* refocus no longer remounts the app (JAN-182); tests refuse outbound network (JAN-175/188); docs/Storybook nits (JAN-236/237) ([#435](https://github.com/thomasniebler/janus/issues/435)) ([691c6ea](https://github.com/thomasniebler/janus/commit/691c6ea98f4b24e79e4630fbef683be7ac6d0de2))
* **tools:** split trigger-history out of events; keep the event log chat-only (JAN-195) ([#440](https://github.com/thomasniebler/janus/issues/440)) ([01568f3](https://github.com/thomasniebler/janus/commit/01568f31a5a46a091020d334f46188aee43690df))
* **whatsapp:** stop retrying a permanent Neonize native-load failure (JAN-172) ([#437](https://github.com/thomasniebler/janus/issues/437)) ([180777e](https://github.com/thomasniebler/janus/commit/180777ed709d6084b926efffbefed3c4fd3451c8))


### Documentation

* spec agent-proposed automations (JAN-235) ([#439](https://github.com/thomasniebler/janus/issues/439)) ([2aa8bff](https://github.com/thomasniebler/janus/commit/2aa8bff188e5a7d211716c13a47d6e1c674e5caa))
* spec one visible per-tool switch for chat and unattended runs (JAN-255) ([#454](https://github.com/thomasniebler/janus/issues/454)) ([a652913](https://github.com/thomasniebler/janus/commit/a6529133437a3b9d7c51f435f7b7e8d16df2539f))

## [1.1.0](https://github.com/thomasniebler/janus/compare/v1.0.0...v1.1.0) (2026-09-28)


### Features

* **db:** knowledge layer spec and PostgreSQL as a first-class backend (JAN-239, JAN-240) ([#427](https://github.com/thomasniebler/janus/issues/427)) ([590ec55](https://github.com/thomasniebler/janus/commit/590ec554f9f7906c23908c42aca9021986deda90))
* **knowledge:** embeddings and semantic memory/topic search (JAN-241) ([#429](https://github.com/thomasniebler/janus/issues/429)) ([d1e137a](https://github.com/thomasniebler/janus/commit/d1e137a4bf03893282ce74a1710788079c196136))


### Bug Fixes

* **auth:** drop passlib, hash passwords with bcrypt directly and lift the bcrypt&lt;4 pin ([#430](https://github.com/thomasniebler/janus/issues/430)) ([c5a5b09](https://github.com/thomasniebler/janus/commit/c5a5b09cfef8f677283df9c18d4be0567df808fc))
* **deps:** bump httpx2/httpcore2 to close 4 security advisories ([#413](https://github.com/thomasniebler/janus/issues/413)) ([924d08d](https://github.com/thomasniebler/janus/commit/924d08db544ff6e763442f5c1b6372badb87938d))


### Documentation

* security assessment report (JAN-238) ([#426](https://github.com/thomasniebler/janus/issues/426)) ([1af0579](https://github.com/thomasniebler/janus/commit/1af0579af851d5022d57d2c6eeb61e43db5502d1))

## [1.0.0](https://github.com/thomasniebler/janus/compare/v0.47.0...v1.0.0) (2026-09-27)


### ⚠ BREAKING CHANGES

* **runtime:** migration d5e8f1a2b3c4 drops agent definitions, skill assignments, and handoff history. /api/agents, /api/conversations/{id}/handoffs, and the streamed /ws/briefing token protocol are removed.

### Features

* **frontend:** manual memory/topic deletion, e2e testing, and Storybook coverage ([#410](https://github.com/thomasniebler/janus/issues/410)) ([677c90b](https://github.com/thomasniebler/janus/commit/677c90b796d72116e3dea5127d7cdedbd92153e6))
* **planner:** support selecting multiple Google calendars and surface sync failures ([#409](https://github.com/thomasniebler/janus/issues/409)) ([d066e77](https://github.com/thomasniebler/janus/commit/d066e77d06bf655c04c70a83707c0fa6d8f438af))
* **runtime:** trigger-first single agent with trigger definitions, tool policy, built-in triggers, and per-surface models ([#412](https://github.com/thomasniebler/janus/issues/412)) ([3f41e0c](https://github.com/thomasniebler/janus/commit/3f41e0cdf2951054bf39cb4d66c1ee062bc5a5cf))

## [0.47.0](https://github.com/thomasniebler/janus/compare/v0.46.0...v0.47.0) (2026-09-26)


### Features

* **llm:** add Anthropic provider and per-provider model dropdown ([#407](https://github.com/thomasniebler/janus/issues/407)) ([6b4f72a](https://github.com/thomasniebler/janus/commit/6b4f72a434196c16d96fcad79bf067cbabc962d3))

## [0.46.0](https://github.com/thomasniebler/janus/compare/v0.45.0...v0.46.0) (2026-09-25)


### Features

* **runtime:** chat-only web_search agent tool (JAN-211) ([#402](https://github.com/thomasniebler/janus/issues/402)) ([782a747](https://github.com/thomasniebler/janus/commit/782a74709df1239ca1f68f6c73dbd1b698d05c6a))
* **triggers:** allow unsupervised memory/topics/dashboard/delegation in trigger runs (JAN-193) ([#400](https://github.com/thomasniebler/janus/issues/400)) ([682bd1d](https://github.com/thomasniebler/janus/commit/682bd1d96ceb9ce141edefe0243b9f7560fe8938))


### Bug Fixes

* **frontend:** make UIv2/UIv3 chat drawer body a flex column so long replies scroll (JAN-212) ([#399](https://github.com/thomasniebler/janus/issues/399)) ([49179b3](https://github.com/thomasniebler/janus/commit/49179b3b84fd763a0771dae9ba014675d1fe3529))
* **linear:** restore Linear read/write connectivity (JAN-196) ([#401](https://github.com/thomasniebler/janus/issues/401)) ([7c5f712](https://github.com/thomasniebler/janus/commit/7c5f712e198be527e03fe931a7adbc9f35f31837))
* **triggers:** record a failed trigger run as failed/exhausted, not completed (JAN-194) ([#403](https://github.com/thomasniebler/janus/issues/403)) ([f748c40](https://github.com/thomasniebler/janus/commit/f748c40cd41f0ccf9b387f69311e9e7c3bcf099b))


### Documentation

* **agents:** require pushing finished branches, not leaving them local ([#398](https://github.com/thomasniebler/janus/issues/398)) ([9012cbe](https://github.com/thomasniebler/janus/commit/9012cbe38190eb34714fd7c165fe6c8132db6d08))

## [0.45.0](https://github.com/thomasniebler/janus/compare/v0.44.0...v0.45.0) (2026-09-24)


### Features

* **events:** filtering and pagination for System Events (JAN-198) ([#393](https://github.com/thomasniebler/janus/issues/393)) ([db65427](https://github.com/thomasniebler/janus/commit/db65427581c140fb1b9db6e746239b4156f0c51f))
* **runtime:** enable trigger worker by default and show its real status (JAN-199) ([#394](https://github.com/thomasniebler/janus/issues/394)) ([5ce4f3f](https://github.com/thomasniebler/janus/commit/5ce4f3fe416bdf316ecaddbef467965bac2fd3b9))
* **runtime:** plan-driven delegation to bounded child tasks (JAN-200) ([#396](https://github.com/thomasniebler/janus/issues/396)) ([62ed005](https://github.com/thomasniebler/janus/commit/62ed0055fcc2b8141bf25552bbad1ac1dadd3852))
* **runtime:** ungate memory/topic/planner tools; source prompts from character (JAN-201) ([#397](https://github.com/thomasniebler/janus/issues/397)) ([222eca6](https://github.com/thomasniebler/janus/commit/222eca685eea7aadbf1916aa4d63f651bf128768))
* **status:** include running version in health endpoint ([#392](https://github.com/thomasniebler/janus/issues/392)) ([ab0e08f](https://github.com/thomasniebler/janus/commit/ab0e08f540e1250a3fb971eac53b86756be97aec))

## [0.44.0](https://github.com/thomasniebler/janus/compare/v0.43.2...v0.44.0) (2026-09-24)


### Features

* **triggers:** execute trigger-driven work and surface fired-trigger history (JAN-192) ([#390](https://github.com/thomasniebler/janus/issues/390)) ([c1fd868](https://github.com/thomasniebler/janus/commit/c1fd86811f11ab2bef3ef4909895526902bd0665))

## [0.43.2](https://github.com/thomasniebler/janus/compare/v0.43.1...v0.43.2) (2026-09-23)


### Bug Fixes

* **deps:** revert greenlet to 3.2.5 -- also breaks the Uberspace deploy ([#378](https://github.com/thomasniebler/janus/issues/378)) ([76940f4](https://github.com/thomasniebler/janus/commit/76940f48e05fb1bb2d0d91b882e1cc5eb06135ff))

## [0.43.1](https://github.com/thomasniebler/janus/compare/v0.43.0...v0.43.1) (2026-09-23)


### Bug Fixes

* **deps:** revert google-re2 to 1.1 -- newer releases break the Uberspace deploy ([#367](https://github.com/thomasniebler/janus/issues/367)) ([aa177c0](https://github.com/thomasniebler/janus/commit/aa177c012346ac1d03ffe58afbee4e4fb15f6095))
* **runtime:** fix flaky frontend tests and legibility of the plan-driven runtime boundary ([#360](https://github.com/thomasniebler/janus/issues/360)) ([1ecbd0f](https://github.com/thomasniebler/janus/commit/1ecbd0f80ce695b4e77fff3ba8305269eb1e8506))

## [0.43.0](https://github.com/thomasniebler/janus/compare/v0.42.0...v0.43.0) (2026-09-23)


### Features

* **agent-tools:** event-log and trigger-outcome read access for agents (JAN-185) ([#362](https://github.com/thomasniebler/janus/issues/362)) ([fac2f9b](https://github.com/thomasniebler/janus/commit/fac2f9b6b7d4d1a4507fd9f500d7682585070423))
* **frontend:** per-trigger runtime widget with Storybook style exploration (JAN-187) ([#361](https://github.com/thomasniebler/janus/issues/361)) ([d1d9f44](https://github.com/thomasniebler/janus/commit/d1d9f44295024c83d024e6d07e963213b4389f75))
* **tools:** agent mail-rule CRUD and relabel-learning suggestion tools (JAN-186) ([#363](https://github.com/thomasniebler/janus/issues/363)) ([333dc89](https://github.com/thomasniebler/janus/commit/333dc89b1554b702650532b0ca7f41ebbacf5a25))
* **triggers:** self-service agent trigger management (JAN-184) ([#364](https://github.com/thomasniebler/janus/issues/364)) ([422aeae](https://github.com/thomasniebler/janus/commit/422aeae8bc27e7156cfc4e8332d70e5004b730fb))

## [0.42.0](https://github.com/thomasniebler/janus/compare/v0.41.0...v0.42.0) (2026-09-21)


### Features

* **frontend:** optional UIv2 terminal and UIv3 rusty-console app shells, switchable from Settings ([#346](https://github.com/thomasniebler/janus/issues/346)) ([b7630a0](https://github.com/thomasniebler/janus/commit/b7630a0373cabeee212cff8557f417bdcb7578e9))
* **runtime:** configure scheduled and event triggers ([#347](https://github.com/thomasniebler/janus/issues/347)) ([768d496](https://github.com/thomasniebler/janus/commit/768d49614cdc68c924233c41a60a901daa472d5d))

## [0.41.0](https://github.com/thomasniebler/janus/compare/v0.40.0...v0.41.0) (2026-09-21)


### Features

* **topics:** require multi-email corroboration before topics go active ([#344](https://github.com/thomasniebler/janus/issues/344)) ([d511ea4](https://github.com/thomasniebler/janus/commit/d511ea4c1099b08ac91c4b0caa77322cd43e46a7))


### Bug Fixes

* **auth:** fold Google Sign-In linking into the Calendar/Tasks connect flow ([#343](https://github.com/thomasniebler/janus/issues/343)) ([b937f0d](https://github.com/thomasniebler/janus/commit/b937f0d50397f598c7eb852a4588fbecdbcc5048))

## [0.40.0](https://github.com/thomasniebler/janus/compare/v0.39.1...v0.40.0) (2026-09-20)


### Features

* **agents:** add Claude Code compatibility layer for the agent roster ([#342](https://github.com/thomasniebler/janus/issues/342)) ([6d70ad6](https://github.com/thomasniebler/janus/commit/6d70ad60003a3c6a82868f76269a1ecdfc257f5d))
* **ios:** dashboard richness, connectivity errors, and agent chat ([#338](https://github.com/thomasniebler/janus/issues/338)) ([ff07a2e](https://github.com/thomasniebler/janus/commit/ff07a2e2278699b91d868553a4684e1e57bf58f3))


### Documentation

* **agents:** add scope refusals, tooling self-checks, and an agent-maintainer ([#340](https://github.com/thomasniebler/janus/issues/340)) ([75bafd6](https://github.com/thomasniebler/janus/commit/75bafd6e2fa1e14c163fcd2dcec1edcf6ac45976))
* treat planned-feature docs as delivery artifacts ([#339](https://github.com/thomasniebler/janus/issues/339)) ([af1cfe5](https://github.com/thomasniebler/janus/commit/af1cfe5273cd7ff8a25579e6378cc53afd385001))

## [0.39.1](https://github.com/thomasniebler/janus/compare/v0.39.0...v0.39.1) (2026-09-19)


### Bug Fixes

* **runtime:** gate trigger toolbox import behind runtime_trigger_worker_enabled ([#336](https://github.com/thomasniebler/janus/issues/336)) ([8c5215d](https://github.com/thomasniebler/janus/commit/8c5215d3fa9279fadfa99aceebcc0b05f6eeca79))

## [0.39.0](https://github.com/thomasniebler/janus/compare/v0.38.0...v0.39.0) (2026-09-18)


### Features

* **agent:** migrate to the plan-driven single-agent runtime ([#333](https://github.com/thomasniebler/janus/issues/333)) ([ed0dc27](https://github.com/thomasniebler/janus/commit/ed0dc27b705808ca6b251dc6dcc42405e2160644))

## [0.38.0](https://github.com/thomasniebler/janus/compare/v0.37.1...v0.38.0) (2026-09-18)


### Features

* **mcp:** manage built-in and remote tools through registry ([#325](https://github.com/thomasniebler/janus/issues/325)) ([9eedbbc](https://github.com/thomasniebler/janus/commit/9eedbbcc10222708dad3d8a14888ec9dc1cd619f))
* **whatsapp:** implement live Neonize connector for WhatsApp ([#301](https://github.com/thomasniebler/janus/issues/301)) ([04bdd31](https://github.com/thomasniebler/janus/commit/04bdd31cec7d702f3338c4ea67e27b836999c114))


### Bug Fixes

* **tests:** repair backend test-suite health issues found via JAN-153 ([31470ee](https://github.com/thomasniebler/janus/commit/31470eef1ce6cfc8042a92219b9affb954a3cb12))
* **whatsapp:** close connector-restart races in the live sync path ([3eacfc8](https://github.com/thomasniebler/janus/commit/3eacfc8f8bd10330f02995692223e07aa854fdaf))

## [0.37.1](https://github.com/thomasniebler/janus/compare/v0.37.0...v0.37.1) (2026-09-15)


### Bug Fixes

* **auth:** persist PKCE code_verifier across Google OAuth callback ([#305](https://github.com/thomasniebler/janus/issues/305)) ([9b7fbdb](https://github.com/thomasniebler/janus/commit/9b7fbdba661e13fc172b8ce8c04dd5472bb8e6c8))
* **security:** replace python-jose with PyJWT to drop vulnerable ecdsa ([#304](https://github.com/thomasniebler/janus/issues/304)) ([1491d87](https://github.com/thomasniebler/janus/commit/1491d87a2716424969bae37fef6be68d5140b532))

## [0.37.0](https://github.com/thomasniebler/janus/compare/v0.36.0...v0.37.0) (2026-09-03)


### Features

* **agent-tools:** add compatibility tool registry ([#278](https://github.com/thomasniebler/janus/issues/278)) ([a3ddf63](https://github.com/thomasniebler/janus/commit/a3ddf63bcd69f091c6272064d6612bedccfb99e7))
* **api:** add user-owned runtime trigger ingress ([#293](https://github.com/thomasniebler/janus/issues/293)) ([8f8654e](https://github.com/thomasniebler/janus/commit/8f8654ebb592f17a98c5f1fea9b16f7fe6dc4824))
* **character:** add bounded constitution runtime ([#283](https://github.com/thomasniebler/janus/issues/283)) ([c7140d6](https://github.com/thomasniebler/janus/commit/c7140d60700ad5a2cf2db07118543461cbd2b6d1))
* **character:** persist user constitution profiles ([#289](https://github.com/thomasniebler/janus/issues/289)) ([ca860a7](https://github.com/thomasniebler/janus/commit/ca860a7de1a0b54ee096757195761d6005187e5a))
* **frontend:** add character constitution editor ([#290](https://github.com/thomasniebler/janus/issues/290)) ([0efa903](https://github.com/thomasniebler/janus/commit/0efa9035d6b00d63e30bd3def4bb1b0906dc5fae))
* **frontend:** add runtime trigger observability panel ([#294](https://github.com/thomasniebler/janus/issues/294)) ([bfabbc6](https://github.com/thomasniebler/janus/commit/bfabbc645c3a6dd54a5b48862f5303425fbc3cef))
* **frontend:** expose planner task permissions ([#288](https://github.com/thomasniebler/janus/issues/288)) ([c984074](https://github.com/thomasniebler/janus/commit/c9840740387bcb58460967b8c6d3957990aff4a6))
* **runtime:** add bounded durable trigger retries ([b4bd99a](https://github.com/thomasniebler/janus/commit/b4bd99ad9b226e10aba8cd9b3b3da24dd022b206))
* **runtime:** add deterministic schedule and event trigger adapters ([b03ef5b](https://github.com/thomasniebler/janus/commit/b03ef5b4f5567920c10a1bd9284786adbb8d5f0e))
* **runtime:** add durable trigger worker ([#291](https://github.com/thomasniebler/janus/issues/291)) ([2e876fc](https://github.com/thomasniebler/janus/commit/2e876fc463c45c194cc55e0dea3433c1791f142b))
* **runtime:** add guarded trigger handler boundary ([#292](https://github.com/thomasniebler/janus/issues/292)) ([ee5a4ac](https://github.com/thomasniebler/janus/commit/ee5a4acecee4d057c121982ece4a7b4ad11f0f8f))
* **runtime:** add injected plan runner ([#281](https://github.com/thomasniebler/janus/issues/281)) ([ac9a7d5](https://github.com/thomasniebler/janus/commit/ac9a7d52e55b7358af5c6b056af48843ce565796))
* **runtime:** add normalized trigger dispatcher ([#285](https://github.com/thomasniebler/janus/issues/285)) ([2816b57](https://github.com/thomasniebler/janus/commit/2816b57c90906a3e038a7716735f79a62944e77e))
* **runtime:** add ownership-bound durable dispatcher ([#287](https://github.com/thomasniebler/janus/issues/287)) ([c5dc748](https://github.com/thomasniebler/janus/commit/c5dc7485420eb9417b1db27d899215601986564e))
* **runtime:** add plan-driven graph contract ([#280](https://github.com/thomasniebler/janus/issues/280)) ([f70e8c4](https://github.com/thomasniebler/janus/commit/f70e8c4873cbc2de7ea4f12b4ebecf2e6e029b7d))
* **runtime:** expose opt-in character and tool bridges ([#284](https://github.com/thomasniebler/janus/issues/284)) ([b9cc9e0](https://github.com/thomasniebler/janus/commit/b9cc9e0e36a31f857d77a04c5e72e8e9ccb5e85c))
* **runtime:** persist plan graph state and resume waiting tasks ([9b0728c](https://github.com/thomasniebler/janus/commit/9b0728c43a5127b6eb90d629e9d004937c313499))
* **runtime:** persist trigger claims and retries ([#286](https://github.com/thomasniebler/janus/issues/286)) ([cf3190b](https://github.com/thomasniebler/janus/commit/cf3190b253f2a907b70f1cee8f69f5cba8c22f36))
* **runtime:** wire durable trigger worker into startup ([#295](https://github.com/thomasniebler/janus/issues/295)) ([6b47014](https://github.com/thomasniebler/janus/commit/6b47014bbbccb762b6832ba3f16c5d7652b185b4))
* **security:** add task-scoped tool authorization ([#282](https://github.com/thomasniebler/janus/issues/282)) ([64cc69a](https://github.com/thomasniebler/janus/commit/64cc69ab9ea7523ff440cd18df7ef84e96263665))


### Bug Fixes

* **todo-chat:** install right-edge Hyprland sidebar ([38b5efb](https://github.com/thomasniebler/janus/commit/38b5efb31411ac72f1aa48a3728bd5e21b770d6c))
* **todo-chat:** restore websocket handshake headers ([#274](https://github.com/thomasniebler/janus/issues/274)) ([2bb812e](https://github.com/thomasniebler/janus/commit/2bb812e7c3821d47c1d289ca540f6c0f77f04b94))


### Documentation

* **frontend:** expose trigger retry state and usage guide ([de302a7](https://github.com/thomasniebler/janus/commit/de302a775c86480d8850700778b992ea23165f36))
* **ops:** document trigger retry rollout ([#300](https://github.com/thomasniebler/janus/issues/300)) ([a7955b3](https://github.com/thomasniebler/janus/commit/a7955b3393715df40770ea5f15e027f24695b034))

## [0.36.0](https://github.com/thomasniebler/janus/compare/v0.35.0...v0.36.0) (2026-09-01)


### Features

* add Todo Chat companion module ([#269](https://github.com/thomasniebler/janus/issues/269)) ([dacd05d](https://github.com/thomasniebler/janus/commit/dacd05db320f6529fcd9a45c6be1ceaf161559aa))
* **desktop:** add authenticated summary widget integration ([#271](https://github.com/thomasniebler/janus/issues/271)) ([3d271cf](https://github.com/thomasniebler/janus/commit/3d271cf82c20b4bf37e918d3df70bd50137c8884))


### Bug Fixes

* **cli:** relocate cached token to XDG config ([#270](https://github.com/thomasniebler/janus/issues/270)) ([82b4d2e](https://github.com/thomasniebler/janus/commit/82b4d2e0031894de1e95e6e910adc5661839aab6))

## [0.35.0](https://github.com/thomasniebler/janus/compare/v0.34.1...v0.35.0) (2026-07-07)


### Features

* **whatsapp:** add Neonize setup wizard ([#265](https://github.com/thomasniebler/janus/issues/265)) ([9158378](https://github.com/thomasniebler/janus/commit/9158378eb1c6b8775d937c79fc396c0303777171))

## [0.34.1](https://github.com/thomasniebler/janus/compare/v0.34.0...v0.34.1) (2026-07-07)


### Bug Fixes

* reverse proxy allowed ([#263](https://github.com/thomasniebler/janus/issues/263)) ([39f1b27](https://github.com/thomasniebler/janus/commit/39f1b2778666a8e663cc6c4d5851f145968abce9))

## [0.34.0](https://github.com/thomasniebler/janus/compare/v0.33.0...v0.34.0) (2026-07-06)


### Features

* **whatsapp:** add neonize foundation and quality guardrails ([#261](https://github.com/thomasniebler/janus/issues/261)) ([11b3364](https://github.com/thomasniebler/janus/commit/11b3364f68bfdc49721682dc07b50777df6ebf73))

## [0.33.0](https://github.com/thomasniebler/janus/compare/v0.32.0...v0.33.0) (2026-07-06)


### Features

* **whatsapp:** add neonize foundation and quality guardrails ([#259](https://github.com/thomasniebler/janus/issues/259)) ([5df91f8](https://github.com/thomasniebler/janus/commit/5df91f83695fc5011bcbcad746c629584985ac41))

## [0.32.0](https://github.com/thomasniebler/janus/compare/v0.31.2...v0.32.0) (2026-07-05)


### Features

* **agents:** add phase-1 development loop ([#258](https://github.com/thomasniebler/janus/issues/258)) ([b927feb](https://github.com/thomasniebler/janus/commit/b927febd3366adec964817d50c2a905d1ab4c61f))


### Documentation

* **agents:** align autonomous SDLC workflow ([#256](https://github.com/thomasniebler/janus/issues/256)) ([d62e8aa](https://github.com/thomasniebler/janus/commit/d62e8aa33054c11981e8b5c25117b1f9d8369780))

## [0.31.2](https://github.com/thomasniebler/janus/compare/v0.31.1...v0.31.2) (2026-07-05)


### Bug Fixes

* **auth:** expose all Google OAuth redirect URIs ([#253](https://github.com/thomasniebler/janus/issues/253)) ([17024fa](https://github.com/thomasniebler/janus/commit/17024fa13407616d99ee1304340bd3ee27c78179))
* **ci:** enforce conventional PR titles for release-please ([#251](https://github.com/thomasniebler/janus/issues/251)) ([77415c3](https://github.com/thomasniebler/janus/commit/77415c3789574060d61d88485071c99d00bc47ae))
* **llm:** cap extraction prompt context ([#252](https://github.com/thomasniebler/janus/issues/252)) ([b3aeafc](https://github.com/thomasniebler/janus/commit/b3aeafc34f6e3c80a09ae33148151dce9b69c73a))

## [0.31.1](https://github.com/thomasniebler/janus/compare/v0.31.0...v0.31.1) (2026-06-28)


### Documentation

* **agents:** require conventional squash PR titles ([#249](https://github.com/thomasniebler/janus/issues/249)) ([ecafb55](https://github.com/thomasniebler/janus/commit/ecafb557ebfccccad79440e18ecf5c61ddf8524f))

## [0.31.0](https://github.com/thomasniebler/janus/compare/v0.30.1...v0.31.0) (2026-06-28)


### Features

* **llm:** add OpenRouter backend option ([d1bfb84](https://github.com/thomasniebler/janus/commit/d1bfb847e08222975dc484f02a55211288185719))
* **planner:** add local planner MVP ([19a77c5](https://github.com/thomasniebler/janus/commit/19a77c5fc66e5a0c099517d83a8065711103b91e))


### Bug Fixes

* **dashboard:** isolate dashboard widget failures ([5f56c58](https://github.com/thomasniebler/janus/commit/5f56c58b356dd4617dc116647fe817e6d1f8f3f1))

## [0.30.1](https://github.com/thomasniebler/janus/compare/v0.30.0...v0.30.1) (2026-05-27)


### Bug Fixes

* fix Google OAuth token exchange in auth callbacks ([#232](https://github.com/thomasniebler/janus/issues/232)) ([b3f1640](https://github.com/thomasniebler/janus/commit/b3f1640560670807b519ae3f53ddfcad390dbe28))

## [0.30.0](https://github.com/thomasniebler/janus/compare/v0.29.0...v0.30.0) (2026-05-27)


### Features

* **ui:** glassmorphism redesign with wallpaper background ([#230](https://github.com/thomasniebler/janus/issues/230)) ([89f0bd8](https://github.com/thomasniebler/janus/commit/89f0bd85e22c1eee15114103f3e3e08d3120d3b3))

## [0.29.0](https://github.com/thomasniebler/janus/compare/v0.28.1...v0.29.0) (2026-05-27)


### Features

* Google OAuth login and tenant data isolation ([#228](https://github.com/thomasniebler/janus/issues/228)) ([3fdc290](https://github.com/thomasniebler/janus/commit/3fdc290cc0aec885962911f943cb5f7a8065a229))


### Bug Fixes

* **deps:** upgrade vulnerable dependencies ([#227](https://github.com/thomasniebler/janus/issues/227)) ([2117f35](https://github.com/thomasniebler/janus/commit/2117f356603681d74a5b525a99b04d87f7e1051a))

## [0.28.1](https://github.com/thomasniebler/janus/compare/v0.28.0...v0.28.1) (2026-05-20)


### Bug Fixes

* **topic-service:** guard marker keyword validation ([#223](https://github.com/thomasniebler/janus/issues/223)) ([e1d64e1](https://github.com/thomasniebler/janus/commit/e1d64e1bfe39fdc080e80c93f0d7ef42625a75a0))

## [0.28.0](https://github.com/thomasniebler/janus/compare/v0.27.1...v0.28.0) (2026-05-20)


### Features

* **mail:** add configurable ingestion agent ([#220](https://github.com/thomasniebler/janus/issues/220)) ([ab480d3](https://github.com/thomasniebler/janus/commit/ab480d3eaa4fa3d58e6db0bf0671ef48b99bd9b5))


### Bug Fixes

* add explicit topic creation tool ([#218](https://github.com/thomasniebler/janus/issues/218)) ([8f9dba6](https://github.com/thomasniebler/janus/commit/8f9dba6af9c32893a58d3b46f94dff0a8fe80557))
* **planner:** stabilize Google OAuth connectivity ([#222](https://github.com/thomasniebler/janus/issues/222)) ([94f39fb](https://github.com/thomasniebler/janus/commit/94f39fb57083859d3a439893769ee997275957c0))

## [Unreleased]

### Features

* **mail:** add dedicated mail ingestion settings and configurable classification agent selection
* **briefings:** add daily briefing settings, bounded email windows, and bounded mail search behavior
* **llm:** add OpenRouter as a first-class provider with dashboard settings and safe base URL fallback
* **chat:** add Todo Chat companion sidebar for local planner workflows
* **ui:** glassmorphism redesign with wallpaper background — glass header, sidebar, and chat panel; forced dark theme

### Bug Fixes

* **dashboard:** isolate widget loading so dashboard failures stay scoped to the broken widget and surface actionable error details

* **google:** use backend-authoritative OAuth callback URLs in settings and auth flow generation
* **topics:** add explicit manual topic creation tool and isolate topic-memory mutation sessions

### Documentation

* **docs:** split the technical spec into navigable child pages and surface them in the in-app docs

## [0.27.1](https://github.com/thomasniebler/janus/compare/v0.27.0...v0.27.1) (2026-05-19)


### Bug Fixes

* **agent-tools:** search email bodies ([#213](https://github.com/thomasniebler/janus/issues/213)) ([c58a797](https://github.com/thomasniebler/janus/commit/c58a797dc0dc68d0f1a997ac471c0f2cbae5bc2b))
* **database:** prevent dashboard 500s on legacy schemas ([#215](https://github.com/thomasniebler/janus/issues/215)) ([9b9902a](https://github.com/thomasniebler/janus/commit/9b9902a530b6a5103782d51d180a5456c6c5ec53))
* **handoffs:** allow delegation targets by name ([#217](https://github.com/thomasniebler/janus/issues/217)) ([e37aa85](https://github.com/thomasniebler/janus/commit/e37aa85759f85b1e211e1c93e753a1505827f91d))
* harden email preview payload serialization ([#214](https://github.com/thomasniebler/janus/issues/214)) ([3192987](https://github.com/thomasniebler/janus/commit/31929879610218d1132ce865172c55060dcdaad3))

## [0.27.0](https://github.com/thomasniebler/janus/compare/v0.26.0...v0.27.0) (2026-05-18)


### Features

* add topic-aware briefing and digest polish ([#211](https://github.com/thomasniebler/janus/issues/211)) ([b869d55](https://github.com/thomasniebler/janus/commit/b869d5539f45f25ede35ff10508be60287a8d3c1))

## [0.26.0](https://github.com/thomasniebler/janus/compare/v0.25.0...v0.26.0) (2026-05-18)


### Features

* **planner:** add Google Tasks parity ([#209](https://github.com/thomasniebler/janus/issues/209)) ([c22876d](https://github.com/thomasniebler/janus/commit/c22876dfbe2a28d888919ac72bc99da8a702b166))
* **topics:** add badge drill-down navigation ([#208](https://github.com/thomasniebler/janus/issues/208)) ([a3c7677](https://github.com/thomasniebler/janus/commit/a3c76779dbf75a891aed6c4a067411de314a46a3))


### Bug Fixes

* **email:** handle legacy nullable preview fields and missing topic-tracking tables without 500s ([#214](https://github.com/thomasniebler/janus/pull/214))

## [0.25.0](https://github.com/thomasniebler/janus/compare/v0.24.0...v0.25.0) (2026-05-18)


### Features

* **dashboard:** add configurable widget visibility and collapse ([#201](https://github.com/thomasniebler/janus/issues/201)) ([03f8dcb](https://github.com/thomasniebler/janus/commit/03f8dcb13d19b30a41cd817e567a0e09413f8420))
* **planner:** add Google Tasks parity with first-class provider support, OAuth scope wiring, and Alembic enum migration ([#209](https://github.com/thomasniebler/janus/pull/209)) ([b014378](https://github.com/thomasniebler/janus/commit/b014378))

## [0.24.0](https://github.com/thomasniebler/janus/compare/v0.23.1...v0.24.0) (2026-05-16)


### Features

* **chat:** add agent color metadata and UI rendering ([#195](https://github.com/thomasniebler/janus/issues/195)) ([bce6873](https://github.com/thomasniebler/janus/commit/bce68737759955ec5d031cd9d96762ff0aac9833))
* **errors:** implement error grouping and compaction (JAN-37) follow-up ([#197](https://github.com/thomasniebler/janus/issues/197)) ([67bf666](https://github.com/thomasniebler/janus/commit/67bf666eb1a9a3db6174f8df872c871300514650))
* **linear:** replace GitHub idea submission with Linear ([#194](https://github.com/thomasniebler/janus/issues/194)) ([1f50f75](https://github.com/thomasniebler/janus/commit/1f50f752d3dd7cd764004cd285d71377addf5942))


### Documentation

* **agents:** add QA security auditor agent ([#198](https://github.com/thomasniebler/janus/issues/198)) ([9e08d26](https://github.com/thomasniebler/janus/commit/9e08d266240adc04caef82b066d260a4ef4cc245))
* **spec:** expand deployment security requirements ([#199](https://github.com/thomasniebler/janus/issues/199)) ([2bff6cd](https://github.com/thomasniebler/janus/commit/2bff6cd8f4097f76876f3b50c5e47f7b26352ec7))

## [0.23.1](https://github.com/thomasniebler/janus/compare/v0.23.0...v0.23.1) (2026-05-15)


### Bug Fixes

* avoid false chat disconnect state ([#188](https://github.com/thomasniebler/janus/issues/188)) ([4793749](https://github.com/thomasniebler/janus/commit/4793749b506db51a77875b29076502565c1e8ede))


### Documentation

* **pr-shepherd:** allow squash merge without self-approval ([#189](https://github.com/thomasniebler/janus/issues/189)) ([85a6eb3](https://github.com/thomasniebler/janus/commit/85a6eb360750be2fc671175facd5f912611b2987))

## [0.23.0](https://github.com/thomasniebler/janus/compare/v0.22.0...v0.23.0) (2026-05-12)


### Features

* inline mail actions and modular agent toolboxes ([#185](https://github.com/thomasniebler/janus/issues/185)) ([d00d985](https://github.com/thomasniebler/janus/commit/d00d98507f4445af79bd2f2799322dbdc18e8167))
* **ui:** unify page navigation into tabs ([#184](https://github.com/thomasniebler/janus/issues/184)) ([60a6fb4](https://github.com/thomasniebler/janus/commit/60a6fb471ab7b80a1451aa3f6f6d9601fe5f2912))

## [0.22.0](https://github.com/thomasniebler/janus/compare/v0.21.1...v0.22.0) (2026-05-12)


### Features

* **app:** cache briefings and refresh shell UX ([#181](https://github.com/thomasniebler/janus/issues/181)) ([3ce9ef1](https://github.com/thomasniebler/janus/commit/3ce9ef1359871f78efdc4c7372a7629e08bba2f3))


### Bug Fixes

* **ci:** deploy release-please tags directly ([#183](https://github.com/thomasniebler/janus/issues/183)) ([245a343](https://github.com/thomasniebler/janus/commit/245a343c6a1125ab54a06e82c3db1657b276126a))

## [0.21.1](https://github.com/thomasniebler/janus/compare/v0.21.0...v0.21.1) (2026-05-11)


### Bug Fixes

* **frontend:** restore chat history access ([#179](https://github.com/thomasniebler/janus/issues/179)) ([c1e6164](https://github.com/thomasniebler/janus/commit/c1e6164802e7191748cea019cda272fd3324683f))

## [0.21.0](https://github.com/thomasniebler/janus/compare/v0.20.1...v0.21.0) (2026-05-11)


### Features

* support direct Ollama Cloud access ([#175](https://github.com/thomasniebler/janus/issues/175)) ([6529029](https://github.com/thomasniebler/janus/commit/6529029d7f7d3c583eec33006ff98120a8c31487))

## [0.20.1](https://github.com/thomasniebler/janus/compare/v0.20.0...v0.20.1) (2026-05-10)


### Bug Fixes

* **mcp:** normalize skill names in create_skill and update_skill tools ([#168](https://github.com/thomasniebler/janus/issues/168)) ([48a305e](https://github.com/thomasniebler/janus/commit/48a305e8631f8662b755971997d567a3d27e4bf7))


### Documentation

* add architecture diagram ([b8e5c18](https://github.com/thomasniebler/janus/commit/b8e5c18df3000627d85e85803360c1094803520b))

## [0.20.0](https://github.com/thomasniebler/janus/compare/v0.19.0...v0.20.0) (2026-04-22)


### Features

* config doctor, nav cleanup, Settings monitoring tabs, IDLE loop fix ([#166](https://github.com/thomasniebler/janus/issues/166)) ([ca61349](https://github.com/thomasniebler/janus/commit/ca61349ab94fbbd1df9d5ab92493149da570ab9e))


### Bug Fixes

* limit initial IMAP sync to last 30 days with locale-safe date formatter ([#165](https://github.com/thomasniebler/janus/issues/165)) ([21a2d3d](https://github.com/thomasniebler/janus/commit/21a2d3d203373827727364eb90dce5798902baf9))
* **notifications:** skip notification for already-notified IMPORTANT emails ([#163](https://github.com/thomasniebler/janus/issues/163)) ([392d40f](https://github.com/thomasniebler/janus/commit/392d40f5b2edf0791de358b15cc65ad1b37293aa))
* **settings:** restore LinearConfig tab after accidental GitHub revert in [#166](https://github.com/thomasniebler/janus/issues/166) ([77618bd](https://github.com/thomasniebler/janus/commit/77618bd60480e3d47880630208e066bf1043cb2f))

## [Unreleased]


### Features

* **briefing:** add topic-aware briefing and digest grouping polish
* **dashboard:** add Topic Focus widget for tracked-topic activity and quick access to topic management ([#207](https://github.com/thomasniebler/janus/pull/207))
* **topics:** add email/dashboard topic badges with drill-down into topic-focused memory views

* **agent-tools:** add inline mail actions plus modular GitHub and Linear toolboxes
* **agents:** add `qa-security-auditor` agent plus repository guidance for security and functionality audit workflows ([#198](https://github.com/thomasniebler/janus/pull/198))
* **chat:** add per-agent color metadata and themed chat rendering ([#195](https://github.com/thomasniebler/janus/pull/195))
* **email:** add structured topic tracking for incoming email processing with API, UI, and docs support ([#206](https://github.com/thomasniebler/janus/pull/206))

### Bug Fixes

* **handoffs:** allow delegation tools to target agents by name without a separate numeric ID lookup (JAN-117)

* **config:** add `janus config doctor` command to detect common misconfigurations (secret key, port/base_url mismatch, allowed_origins, allowed_hosts, Fernet key, HTTP in server_mode); IPv6-safe with `urlsplit` for host parsing ([#166](https://github.com/thomasniebler/janus/pull/166))
* **frontend:** consolidate Events/Status/Errors into Settings → Monitoring tabs; persistent chat sidebar; remove standalone Chat page ([#166](https://github.com/thomasniebler/janus/pull/166))
* **docs:** document all 13 settings fields including `allowed_origins`, `allowed_hosts`, `trusted_proxy_hops`, `public_api_docs_enabled`; add WebSocket/CORS troubleshooting section ([#166](https://github.com/thomasniebler/janus/pull/166))
* **docs:** add `docs/docs/architecture.md` — ASCII block diagram of all modules, background workers, service layer, FastAPI app, persistence and clients; includes key data-flow descriptions and layer responsibility table ([#169](https://github.com/thomasniebler/janus/pull/169))
* **ollama:** support direct Ollama Cloud access with encrypted API-key configuration plus hosted settings UI/config support ([#175](https://github.com/thomasniebler/janus/pull/175))
* **frontend:** add password management UI, refreshed desktop/login UX, and shared two-hour daily briefing caching ([#181](https://github.com/thomasniebler/janus/pull/181))
* **errors:** add grouped error compaction with an admin Errors page ([#197](https://github.com/thomasniebler/janus/pull/197))


### Bug Fixes

* **dashboard:** avoid HTTP 500s on legacy schemas by auto-upgrading before widget queries ([#215](https://github.com/thomasniebler/janus/pull/215))
* **mcp:** restore `search_emails` matches for terms that appear only in stored email bodies (JAN-118)
* **frontend:** avoid false chat disconnect state after reconnects ([#188](https://github.com/thomasniebler/janus/pull/188))
* **imap:** limit initial sync to last 30 days via locale-safe IMAP SINCE filter; add `format_imap_date` utility to `time_utils` to avoid locale-dependent `strftime("%b")` on non-English systems ([#165](https://github.com/thomasniebler/janus/pull/165))
* **imap:** IDLE loop now calls `fetch_since_uid` instead of unbounded `fetch_new_emails`; advances `last_uid` once per batch to prevent duplicate notifications ([#166](https://github.com/thomasniebler/janus/pull/166))
* **notifications:** skip re-notification for already-notified IMPORTANT emails ([#166](https://github.com/thomasniebler/janus/pull/166))
* **frontend:** non-admin users redirected to `/errors` now land on Accounts tab instead of blank Settings panel ([#166](https://github.com/thomasniebler/janus/pull/166))
* **mcp:** `create_skill` and `update_skill` tools now normalize human-readable skill names to lowercase-hyphen slugs before validation, preventing silent `ValidationError` failures ([#168](https://github.com/thomasniebler/janus/pull/168))

## [0.19.0](https://github.com/thomasniebler/janus/compare/v0.18.0...v0.19.0) (2026-04-20)


### Features

* **chat:** use agent colors for active and streaming chat responses (JAN-84) ([#151](https://github.com/thomasniebler/janus/issues/151)) ([36505c2](https://github.com/thomasniebler/janus/commit/36505c253e24a55e3279e0ea6177c0f86b8430e3))
* **email:** expose full email body in API payloads for shared detail view (JAN-83) ([#149](https://github.com/thomasniebler/janus/issues/149)) ([58c41e7](https://github.com/thomasniebler/janus/commit/58c41e76d45c0c417a0e26733c23740e148d013a))
* **frontend:** persistent split layout with always-visible chat sidebar (JAN-92) ([#138](https://github.com/thomasniebler/janus/issues/138)) ([6254eab](https://github.com/thomasniebler/janus/commit/6254eabe553c7d2e865cc3dd55961c70d19e9db4))
* **ios:** add native SwiftUI iOS client scaffold ([#156](https://github.com/thomasniebler/janus/issues/156)) ([e98c706](https://github.com/thomasniebler/janus/commit/e98c706a15243f459ae0faa3ff5f6a3aa0e2314a))
* **linear:** replace GitHub issue backend with Linear service and endpoints (JAN-49) ([#141](https://github.com/thomasniebler/janus/issues/141)) ([e4e5946](https://github.com/thomasniebler/janus/commit/e4e59465a5dc683be6db3c5572230d322183458b))
* **notifications:** user-configurable notification preferences with digest scheduling (JAN-26) ([#143](https://github.com/thomasniebler/janus/issues/143)) ([5c93103](https://github.com/thomasniebler/janus/commit/5c93103ee0930f08a2bfef50b957d0e5bf31f20e))
* replace GitHub settings/modal with Linear, add chat page context (JAN-50, JAN-48, JAN-81) ([2178e19](https://github.com/thomasniebler/janus/commit/2178e190e44f05ac873cc8cb309c182c8b42c824))
* **skills:** add skills.sh discovery/import and AI-assisted skill authoring (JAN-93) ([#137](https://github.com/thomasniebler/janus/issues/137)) ([792e38f](https://github.com/thomasniebler/janus/commit/792e38f35459c5df541e6bfac241950286d1a53c))


### Bug Fixes

* **agents:** require explicit color on agent definition create payloads (JAN-86) ([#139](https://github.com/thomasniebler/janus/issues/139)) ([7fc6db2](https://github.com/thomasniebler/janus/commit/7fc6db20b1a9d1f57d3241058166750d90b9cef5))
* **email:** order IMAP imports and latest-email fetches by preserved message timestamp (JAN-87) ([#145](https://github.com/thomasniebler/janus/issues/145)) ([70bf2b5](https://github.com/thomasniebler/janus/commit/70bf2b5e827756f891c7e04a780a4b106cbf1d24))
* **email:** preserve source message timestamps in latest-email import path (JAN-85) ([#142](https://github.com/thomasniebler/janus/issues/142)) ([bb697fc](https://github.com/thomasniebler/janus/commit/bb697fcbb866f9f349c0dfe41ccf9b04ac20c004))
* **event_logger:** handle datetime values in event details JSON serialisation ([#159](https://github.com/thomasniebler/janus/issues/159)) ([759e0ce](https://github.com/thomasniebler/janus/commit/759e0ced48caee4fdbd348e07776ce58b75581e7))
* **frontend:** make docked chat panel fill available viewport height (JAN-82) ([#140](https://github.com/thomasniebler/janus/issues/140)) ([ddce447](https://github.com/thomasniebler/janus/commit/ddce447fae95f02cab780138611613d4c905200c))
* full-width layout and usable compactSidebar chat panel ([#161](https://github.com/thomasniebler/janus/issues/161)) ([e3eaeba](https://github.com/thomasniebler/janus/commit/e3eaeba22703ee22975bd0351c9beca7ade905e3))
* **lint:** restore ruff E501 and format compliance after JAN-26 merge ([#157](https://github.com/thomasniebler/janus/issues/157)) ([25f5a14](https://github.com/thomasniebler/janus/commit/25f5a147b3f84bf61c91017e1e705e5fc7801bdc))
* **migrations:** add missing emails.body column migration ([#160](https://github.com/thomasniebler/janus/issues/160)) ([14f9fb0](https://github.com/thomasniebler/janus/commit/14f9fb01efd0b2ba1ffc4dd85f2479112b5ad4f0))
* **migrations:** merge linear and notification preferences alembic heads ([#158](https://github.com/thomasniebler/janus/issues/158)) ([f4a161c](https://github.com/thomasniebler/janus/commit/f4a161c2621fc854db942aaab1dd90039d63d28b))

## [0.18.0](https://github.com/thomasniebler/janus/compare/v0.17.0...v0.18.0) (2026-04-20)
