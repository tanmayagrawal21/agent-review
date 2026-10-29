# agent-review

Automatic pull-request review by three AI agents on GitHub Actions, posted as **one** review. Each agent can
be switched on or off. Nothing runs on your machine.

| agent | looks for |
|---|---|
| **Reviewer** | safety bypasses, trust-boundary loosening, correctness on real inputs, missing tests |
| **Privacy** | personal data and secrets in added lines and file names (plus a deterministic private-terms scan) |
| **Safety** | guards, limits, permissions and config loaders a change could loosen or make fail open |

```
PR opened / updated ─► plan ─┬─ reviewer ─┐
                             ├─ privacy  ─┼─► combine ─► one PR review + one check
                             └─ safety   ─┘
```

The agents run in parallel. Each is Claude Code in headless mode, read-only. Each returns structured
findings (file, line, severity, message) tied to the PR's head commit. The combine step is plain code. It
groups findings by line, posts them as inline comments where GitHub allows, and puts the rest in the
summary. It also collapses the previous agent review when you push again.

## Set up (about 2 minutes)

1. Copy [`examples/caller.yml`](examples/caller.yml) to `.github/workflows/agent-review.yml` in the repository.
2. Add a repository secret: `ANTHROPIC_API_KEY`, or a `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token`.
   Check that your plan's terms allow CI use before relying on a subscription token.
3. Optional: add [`examples/agent-review.yml`](examples/agent-review.yml) as `.github/agent-review.yml` to turn
   agents off, scope the safety agent to paths, make the check blocking, or give the agents notes about the
   codebase.
4. Optional: add a secret `AGENT_REVIEW_PRIVATE_TERMS` with one term per line (names, emails, internal
   hostnames). Those terms are matched in plain code and never sent to a model. A finding names the term by
   index (`private term #3`), never the term itself.

## Design choices

- **Humans merge.** The review is always a comment and never approves. By default the check stays green
  (`blocking: false`). Set `blocking: true` and make the check required if you want it to gate merges. An agent
  that errors or times out counts as "not approved", never as a pass.
- **Agents don't run the PR's code.** Your CI already runs the tests. Running untrusted code next to the API
  key would let a PR steal it. Agents run in Claude Code's `--restricted` mode with only Read, Grep and Glob.
  That means no shell, no web, no edits, file access confined to the checkout and the diff, the PR's own
  `.claude/` settings and `.mcp.json` ignored (and deleted), and no GitHub token. Every agent output is also
  scrubbed of credentials before it is posted.
- **A PR can't switch off its own review.** The config is read from the PR's base commit, and each agent's
  findings live in their own artifact and must name that agent and the head commit. One caveat: on
  `pull_request`, a PR can edit the calling workflow itself, so protect `.github/workflows/` with CODEOWNERS
  and required reviews.
- **Blocking mode fails closed.** With `blocking: true`, a PR that is skipped (too large, or no credential,
  e.g. a fork) fails the check instead of passing unreviewed.
- **The PR's text is data, not instructions.** The title and description go to the agents marked as
  untrusted. Every posted text is redacted against the private terms.
- **Forks:** GitHub gives no secrets to PRs from forks, so those get no review (the plan job says why). Don't
  work around this with `pull_request_target`: that runs fork code with your secrets.

## Cost

About **$0.50 per PR** with all three agents on Sonnet, on a small PR. The local test below came to $0.51.
It grows with PR size, and `max_budget_usd` caps each agent. Drafts and PRs over `skip.max_changed_lines`
are skipped.

## Run it locally

```bash
uv sync
uv run python -m agent_review.run_agent --agent reviewer --repo ../your-repo --base <base-sha> --head <head-sha> \
    --out findings/reviewer.json            # add --inherit-env only for a desktop-app login; never in CI
uv run python -m agent_review.combine --repo-dir ../your-repo --findings findings --base <base-sha> \
    --head <head-sha> --agents '["reviewer"]' --dry-run
```

On a scratch repo with three planted problems (a `<` changed to `<=` on a transfer limit, an unvalidated
env-var override of that limit, and a hard-coded `sk_live_` key), all three agents flagged them. Each line got
one comment holding every agent's finding.

## Versioning

Callers pin a tag (`@v1`). Each release moves `v1`; breaking changes get `v2`. If you pin another ref, pass
the same one as `tool-ref`, so the scripts match the workflow. The prompts are tuned and
measured with [swe-harness](https://github.com/tanmayagrawal21/swe-harness) before release.

MIT licensed.
