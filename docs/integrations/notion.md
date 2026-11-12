# Notion (ADR reviews)

With a Notion token set, prbot also tracks **ADR review posts** in Slack. It reacts with the ADR's current version and its review state, read from the consensus table on the ADR's Notion page, and keeps the reactions up to date as the table changes.

Without a token, ADR review posts are treated like any other message: prbot only looks for PR links in them.

## What counts as an ADR review post

A Slack message whose **first line** says `:memo: ADR to review` (or `ADRs to review`, with or without bold or italics) and that links to the ADR in Notion:

```
:memo: ADR to review: <https://www.notion.so/acme/ADR-014-Event-sourcing-31ed89c32a998009b89ee7e5d7bcf672|ADR-014>
```

- The first Notion page linked after those words is the ADR. Links to a database view (`…?v=<view>`) are skipped; a page opened from a database (`…?v=<view>&p=<page>`) counts.
- An ADR review post is handled only as an ADR, even if it also links a PR.
- Discord messages aren't checked for ADRs.

## What prbot reacts with

| Reaction | When |
| :------: | ---- |
| 1️⃣ … 🔟 | The ADR's current version: the page's **Version** property, or the highest version in the table when that's empty. Versions above 10 get no number. |
| ![git-approved](../images/emojis/git-approved.png){: style="height:20px"} approved | At least one reviewer scored the current version `+1` or higher |
| ![git-changes-requested](../images/emojis/git-changes-requested.png){: style="height:20px"} changes requested | At least one reviewer scored the current version `-1` or lower |
| 🥊 contested | Both of the above. Shown **instead of** approved and changes requested |
| 💬 commented | At least one row for the current version says more than its score and a sign-off like "LGTM" |

How the consensus table is read:

- Each reviewer starts their **Comments** cell with a score: `+2`, `+1`, `+0.5`, `0`, `-1`, `-2` and so on. `+0.5` and `-0.5` count as neither approval nor changes requested.
- Only rows whose **Version** matches the ADR's current version count, and only each reviewer's last row for it.
- The table is found by its columns: it needs a **Version** and a **Comments** column. Reviewers come from a **Name** or **Reviewer** column, or failing that the first other column that isn't a date. A table under a "Consensus" heading is preferred, and one inside a toggle or column is found too.

When the ADR moves to a new version, the old version's reactions come off and the post starts again from the new version's number. Reactions that no longer apply are removed before new ones are added, so approved and changes requested never show together.

## How reactions stay up to date

Notion doesn't notify prbot of changes, so prbot re-reads the ADRs behind recent review posts every 2 minutes by default (`PR_BOT_NOTION__POLL_INTERVAL_SECONDS`). Each ADR is read once per pass, however many posts link it.

- Expect reactions to change up to one interval after an edit in Notion.
- A post is followed until its reactions haven't changed for 30 days (`PR_BOT_NOTION__POLL_WINDOW_DAYS`). After that its reactions stay as they were, even if the ADR changes later.
- If prbot can't read an ADR, usually because the integration isn't connected to it, the post gets no reactions and prbot tries again on each pass. Reactions appear once access is fixed.
- Each pass costs a few Notion requests per ADR. Notion allows an integration an average of 3 requests a second, so prbot sends at most that many, and waits when Notion asks it to.

## Setup

### Step 1: Create a Notion integration

1. Go to [notion.so/profile/integrations](https://www.notion.so/profile/integrations) and click **New integration**
2. Choose the workspace that holds your ADRs and set the type to **Internal**
3. Under **Capabilities**, **Read content** is the only one prbot needs
4. Save, then copy the **Internal Integration Secret**

### Step 2: Connect it to the ADR database

An internal integration can only read pages it has been connected to.

1. Open the database that holds your ADRs
2. Click **•••** in the top right, then **Connections**, and add your integration

Connecting the database gives the integration access to every ADR in it, including ones added later.

### Step 3: Configure prbot

```sh
PR_BOT_NOTION__TOKEN=your-internal-integration-secret
```

Restart prbot. ADR review posts made from then on get reactions. Posts made before the token was set aren't picked up.

!!! tip "Checking access"
    If a post gets no reactions, look in prbot's logs for `Notion GET /pages/<id> -> 404`. That means the integration isn't connected to that ADR's database.

## Custom emoji

The approved, changes requested and commented reactions use the same emoji as PRs. The contested reaction has its own setting:

```sh
PR_BOT_EMOJI__ADR_CONTESTED=boxing_glove
```

Per-channel and per-workspace emoji overrides apply to ADR posts as they do to PR posts. See [Configuration](../configuration.md#custom-emoji).
