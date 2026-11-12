# Bot spec: building a chat bot the prbot way

Give this file to Claude together with a filled-in [brief](#10-the-brief) when starting a new bot. It describes the contracts and principles prbot follows, so a new bot can follow them too.

**Copy the contracts, don't share code.** Each bot lives in its own repo and owns its own copy of these ports, use cases and checks. There is no shared library to import, and none should be built: bots differ in their details, and a shared abstraction would have to serve all of them. When in doubt, read how prbot does it ([feds01/prbot](https://github.com/feds01/prbot)) and do the same in the new repo.

The rules come from how prbot is built. A few in section 4 are marked *beyond prbot*: they're for bots whose reactions change in ways prbot's don't. Where a rule is a judgement call, the reason is given, so the new bot can tell when it doesn't apply.

---

## 1. What a prbot-style bot is

A bot that:

1. **Spots a trigger** in chat messages: in prbot, a GitHub PR link.
2. **Tracks** that message against an external **subject**: the pull request.
3. **Keeps the message's reactions in step** with the subject's state as it changes.
4. **Recovers from anything it missed**: messages posted while it was down, and changes to the subject it wasn't told about.

If the new bot doesn't fit this shape, say how it differs in the brief before building.

### Vocabulary

Use these words in code, docs and commit messages, so every bot reads the same.

| Term | Meaning | prbot example |
|---|---|---|
| Trigger | What makes a message one the bot tracks | A GitHub PR URL |
| Subject | The external thing a tracked message is about | A pull request |
| Source | The external API the subject's state comes from | GitHub |
| Snapshot | The subject's state as read from the source at one moment | `PRInfo` |
| Verdict | What the rules conclude from a snapshot | `PRStatus` |
| Wanted emoji | The reactions the verdict calls for | `ReactionManager.plan(...)` |
| Tracked post | A message the bot follows, with the reactions it has put on it | `TrackedPR` |
| Integration | A chat platform the bot reads messages from and reacts on | Slack, Discord |
| `MessageRef` | An opaque pointer to one message: `integration_id` + `ref` | `slack` + `C123:1700000000.000100` |
| Scope keys | Where a message was posted, most specific first | `slack/T1/C1`, `slack/T1`, `slack` |

---

## 2. Layers and import rules

```
src/<bot>/
  domain/<subdomain>/      value_objects.py, entities.py, ports.py, pure rules
  application/<subdomain>/ use cases, one class per file, named for what they do
  data/                    SQLite repositories, Alembic migrations
  infrastructure/          clients for sources (GitHub, ...) and webhook models
  integration/<platform>/  chat platforms: handler.py, gateway.py, commands
  config.py                pydantic-settings
  main.py                  composition root: builds everything, FastAPI app, lifespan
tests/                     mirrors src/: tests/domain, tests/application, tests/data, ...
```

| Layer | Holds | May import |
|---|---|---|
| `domain` | Value objects, entities, ports (`Protocol`s), pure rules | Only `domain` and pydantic |
| `application` | Use cases that drive ports | `domain` |
| `data`, `infrastructure` | Adapters that implement ports | `application`, `domain` |
| `integration` | Chat platform handlers, which call use cases | everything below |
| `main` | Wiring only: no logic beyond deciding what's switched on | everything |

These rules are enforced by import-linter, not left to convention. Copy prbot's contracts from `pyproject.toml` and rename the package. At minimum:

```toml
[[tool.importlinter.contracts]]
name = "<bot> layered architecture"
type = "layers"
layers = [
    "<bot>.main",
    "<bot>.integration",
    "<bot>.data | <bot>.infrastructure",
    "<bot>.application | <bot>.config",
    "<bot>.domain",
]

[[tool.importlinter.contracts]]
name = "domain and application are vendor-free"
type = "forbidden"
source_modules = ["<bot>.domain", "<bot>.application"]
forbidden_modules = ["fastapi", "httpx", "slack_bolt", "slack_sdk", "sqlalchemy", "alembic", "pydantic_settings"]
```

Also copy the test contracts: tests mirror the src layers, a test layer only imports its own src layer or lower, and no test imports `<bot>.main`. Subdomains that don't need each other get an `independence` contract.

---

## 3. Contracts

These are the ports and use cases every bot has. Names change with the subject; the shapes and rules don't.

### 3.1 Chat side

**`MessageRef`** (`domain`): a frozen value object, `integration_id: str` and `ref: str`. Only the integration that made a `ref` can read it.

**`ReactionPort`** (`domain`):

```python
class ReactionPort(Protocol):
    async def add_reaction(
        self, message_ref: MessageRef, emoji: str, fallback_emoji: str | None = None
    ) -> None:
        """Add a reaction; if *emoji* doesn't exist on the platform, the adapter may use *fallback_emoji*."""

    async def remove_reaction(
        self, message_ref: MessageRef, emoji: str, fallback_emoji: str | None = None
    ) -> None:
        """Remove the bot's own reaction, and the fallback too when given. Removing a missing reaction is not an error."""
```

**`IntegrationHandler`** (`integration/base.py`): `integration_id`, `reaction_port()`, `register_routes(app)`, `start()`, `stop()`. **`IntegrationRegistry`** registers handlers and itself implements `ReactionPort`, routing each call by `message_ref.integration_id`. Use cases receive the registry and never know which platform they're on.

**`ChannelCursorPort`** (`domain`): `get_cursor(integration_id, channel_id)` and `upsert_cursor(...)`, which never moves backwards. It's what backfill resumes from.

### 3.2 Subject side

**`<Subject>SourcePort`** (`domain`), implemented in `infrastructure`. It recognises the subject in text and reads its state:

```python
class PRSourcePort(Protocol):
    def extract_pr_references(self, text: str) -> list[PRUrl]:
        """Return all PR references found in *text* that this source understands."""

    async def fetch_pr_info(self, pr_url: PRUrl) -> PRInfo:
        """Fetch current state for a recognised PR reference."""
```

Recognising is pure (no I/O), so it can run on every message. A source whose budget can run out raises a distinct `SourceRateLimitError(reset_at)`, so bulk work can stop instead of failing every remaining item.

**Rules** (`domain`, pure): turn a snapshot into a verdict (`resolve_pr_status(pr_info) -> PRStatus`). All the interesting decisions live here, as plain functions with table-driven tests. Turning the verdict into wanted emoji uses the scope's `EmojiConfig` (in prbot, `ReactionManager.plan`).

### 3.3 Tracking

**`Tracked<Subject>`** (`domain/entities.py`): a pydantic model with the subject's ID, the `MessageRef`, `applied_emojis: frozenset[str]` (what the bot has actually put on the message) and `scope_keys`.

**`<Subject>RepositoryPort`** (`domain`), implemented in `data`. prbot's, trimmed:

```python
class PRRepositoryPort(Protocol):
    async def save(self, tracked_pr: TrackedPR) -> None:
        """Insert the post, or replace what is stored for it."""

    async def find_by_pr_url(self, pr_url: PRUrl) -> Sequence[TrackedPR]:
        """Every message tracking this PR, so one change updates them all."""

    async def find_distinct_pr_urls(self, since: str | None = None) -> Sequence[PRUrl]:
        """Distinct tracked PRs, most recently active first: what reconciliation covers."""
```

**`EmojiConfig`** (`domain`), plus **`EmojiConfigResolverPort.resolve(scope_keys)`**: the first scope that sets an emoji wins, then the env defaults. Every configurable emoji has a Unicode fallback.

### 3.4 Use cases

| Use case | Does | prbot |
|---|---|---|
| `Handle<Subject>Message` | A trigger arrives: read the subject, react, save the tracked post | `HandleIncomingMessage` |
| Reaction planning | Work out which reactions to add and which to take back, skipping ones already applied | `ReactionManager` |
| `Handle<Source>Webhook` | The source pushed a change: update every post tracking that subject | `HandleGitHubWebhook` |
| `Reconcile<Subject>s` | On startup, re-check recent subjects for changes missed while down | `ReconcileTrackedPRs` |
| `BackfillMissedMessages` | On startup, read channel history since each cursor and handle missed triggers | `BackfillMissedMessages` |
| `Refresh<Subject>s` | Only for sources with no webhooks: re-read followed subjects on an interval | — (GitHub has webhooks) |

A source with webhooks gets `Handle<Source>Webhook` plus `Reconcile...`. A source without them gets `Refresh...`. If one bot handles several kinds of trigger, add a router that sends each message to exactly one pipeline, so two pipelines never react to the same message.

---

## 4. Principles

These are the behaviours that make prbot trustworthy. Give each one the new bot relies on a test that fails if it's broken.

**Correctness**

1. **Record only what landed.** `applied_emojis` is updated from what the adapter confirmed, not from what was asked for.
2. **Skip what's already there.** Planning skips reactions already in `applied_emojis`, so handling the same message or event twice doesn't react twice.
3. **One change updates every post.** A subject can be linked from several messages; an update reaches all of them, and one unreachable message doesn't stop the rest.
4. **Be deliberate about which text a trigger is read from.** prbot reads a message's resolved text, which includes linked and forwarded messages, on purpose: bumping a PR post tracks the PR on the bump too. If a trigger should only count in the message itself, anchor it, for example to the first line.
5. **Decide what happens when the source can't be read.** prbot skips the PR: the message gets nothing. If a subject is often unreadable at first (permissions not granted yet), save the post with no reactions instead, so a later pass can fill it in.
6. **Don't build on undocumented vendor behaviour.** If a shortcut depends on something the vendor's docs don't say, don't take it until a live test proves it.

*Beyond prbot*, for bots whose reactions can be replaced rather than only added (a status that moves backwards, a version that resets):

7. **Remove before you add.** Take stale reactions off first. If any removal fails, add nothing this time, so contradictory reactions never show together.
8. **Mark "up to date" only after a full sync.** A sync that stopped partway must look unfinished, so it gets retried.
9. **Start from the stored post.** When a message is handled again, start from what was saved, so stale reactions get removed rather than forgotten.
10. **Re-read before writing in long passes.** A background pass that runs for minutes re-reads each post just before updating it, so it doesn't overwrite a newer save.

**Resilience and cost**

11. **Failures are per item.** One bad subject or post never stops a pass. Expected, repeating failures log one line without a traceback; unexpected ones log the traceback.
12. **One source read per subject per pass**, however many posts link it.
13. **Respect rate limits.** Know the source's budget. When it runs out, bulk work stops (`SourceRateLimitError`) rather than skipping ahead and failing everything else. Limit concurrency, and for sources with a per-second limit, space requests in the gateway.
14. **Bound background work.** Passes cover a window of recent activity, set in config with a sensible default (`PR_BOT_RECONCILE_WINDOW_DAYS=7`), never "everything ever". When a budget can run out partway, do the newest first.
15. **Cheap pre-filter first.** Run the pure recogniser before any I/O, so ordinary messages cost nothing.
16. **Always advance the channel cursor**, even for messages the bot ignores.
17. **Answer the platform fast.** Slack retries events it hasn't had a response to within 3 seconds; don't make it wait on source calls.

**Lifecycle and configuration**

18. **Off unless configured.** An integration or feature whose credentials aren't set is switched off, and everything else behaves exactly as before. Check the app starts both with and without them.
19. **Background tasks** start in the FastAPI lifespan after the integrations (and their backfill) have started, catch and log anything they raise, and are cancelled and awaited at shutdown. A repeating loop sleeps *after* each pass, so passes never overlap.
20. **Inject time.** New code that reads the clock or sleeps takes `clock` and `sleep` parameters, so tests never wait or depend on today's date. (Older prbot code, like `ReconcileTrackedPRs`, still calls `datetime.now` directly.)

---

## 5. Configuration

- `pydantic-settings`, prefix `<BOT>_`, nested delimiter `__`, `.env` supported: `PR_BOT_SLACK__BOT_TOKEN` maps to `settings.slack.bot_token`.
- Each integration is an optional nested model (`slack: SlackConfig | None = None`). Its presence switches it on.
- Tunables have defaults, with a comment saying what a bigger or smaller value costs (see `reconcile_window_days`). Use `Field(ge=...)` to reject values that would hurt the source.
- Every setting appears in `docs/configuration.md` and `.env.example`.

---

## 6. Persistence

- SQLite through async SQLAlchemy (`aiosqlite`), and Alembic migrations run in the lifespan on startup.
- One table per tracked subject kind, unique on (subject ID, `integration_id`, `message_ref`), written with an upsert.
- `created_at` and `updated_at` default to `CURRENT_TIMESTAMP` (UTC). Windows compare against them in UTC.
- `scripts/check_migrations.py` fails if the models and migrations disagree; it runs in `just check`. Never edit a migration that has shipped.

---

## 7. Testing

- `tests/` mirrors `src/`, and the test import contracts keep each test layer honest.
- **Fakes, not mocks.** Hand-written fakes for each port live in `tests/conftest.py` (`FakeReactions` records adds and removes; `FakePRRepository` keeps posts in memory). Subclass a fake to inject a failure.
- **HTTP adapters** are tested against realistic JSON with `respx`. **Repositories** against in-memory SQLite.
- **Pure rules** get table-driven tests (`pytest.mark.parametrize`) covering the edge cases people actually produce.
- Name tests for the behaviour: `test_webhook_continues_past_unreachable_message`, not `test_webhook_2`.
- Each principle in section 4 that the bot relies on has a test that fails if it's broken.
- `asyncio_mode = "auto"`, `filterwarnings = ["error"]`, `--strict-markers`.

---

## 8. Tooling

Copy prbot's `pyproject.toml` tool sections, `justfile`, `.pre-commit-config.yaml`, `Dockerfile` and `fly.toml`, then rename.

- **uv**, Python 3.14. **ruff** with `select = ["ALL"]` and prbot's short, commented ignore list, line length 100. **ty** with every rule at error. **import-linter**. **pytest** with `pytest-asyncio`, `respx`, `time-machine`.
- `just check` runs lock check, lint, format check, type check, migration check and import rules, the same set as CI. `just test` runs the tests.
- Suppress a lint only on the line, with a reason: `# noqa: PLR0913 - constructor injection: each collaborator is a named port`.
- Deploy: Docker image run by uvicorn on 8080, `/health` endpoint, SQLite on a mounted volume, secrets as env vars.

---

## 9. How to deliver

1. **Plan before code.** Restate the brief as triggers, subjects, rules and an emoji table, list the judgement calls, and agree them with the user.
2. **Stacked PRs, each shippable on its own**, for example:
   1. Domain rules and the source adapter: pure rules, snapshot, gateway. Nothing is wired up yet.
   2. Tracking: recogniser, table and repository, the message use case, and wiring behind config. Behaviour is unchanged when the feature is off.
   3. Keeping up to date (webhooks or polling, plus reconciliation) and docs.
3. **Every commit passes every check on its own.** Run `git rebase -i <base> --exec 'just check && just test'` before pushing.
4. **Conventional commits** scoped to the area: `feat(github): ...`, `fix(github): ...`, `feat(emoji): ...`, `docs(emoji): ...`, `refactor(tracking): ...`. The body says why.
5. **Review each PR** before it goes up. Fold fixes into the commit they belong to.
6. **Docs:** a page per integration or source (`docs/integrations/<name>.md`) covering what the bot does, what each reaction means, how often it updates and step-by-step setup. Add the settings to `configuration.md` and a line to the README.
7. **Live test plan:** a read-only check against the real source, then an end-to-end run in a test channel with a separate test app, with a table of actions and expected reactions.

---

## 10. The brief

Fill this in and give it to Claude with this spec.

```markdown
# <Bot name>

## Trigger
What makes a message one to track? Paste 2-3 real examples, and one that looks similar but shouldn't count.
Should a message that links to or forwards a tracked one count too?

## Subject and source
What is each tracked message about, and which API holds its state? Link the API docs.
Does the source push changes (webhooks), or must the bot poll? What are its rate limits?

## Rules
From the subject's state, which reactions should the message show? A table of state → emoji.
Which reactions can't show together? Can a reaction ever be taken back, and when?

## Platforms
Slack, Discord, or both? Any slash commands?

## Configuration
Secrets needed, and what should switch the bot (or a feature) on and off.

## Out of scope
What it won't do in the first version.
```

---

## 11. Checklist before opening a PR

- [ ] `just check` and `just test` pass on every commit in the PR
- [ ] Domain and application import nothing from vendors (import-linter passes)
- [ ] New ports have fakes in `tests/conftest.py`; new behaviour has tests named for it
- [ ] The app starts and stops cleanly with and without the new feature's config
- [ ] Every new setting is in `config.py`, `docs/configuration.md` and `.env.example`
- [ ] Background work is windowed, rate-limited, per-item fault tolerant and cancelled at shutdown
- [ ] The PR description says what's in it, what isn't, known limitations and how it was tested, including anything not tested live
