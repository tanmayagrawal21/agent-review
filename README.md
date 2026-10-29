# agent-review

Automatic pull-request review by three agents on GitHub Actions, posted as **one** review. Each agent can be
switched on or off. It works with any model provider, and nothing runs on your machine.

| agent | looks for |
|---|---|
| **Reviewer** | safety bypasses, trust-boundary loosening, correctness on real inputs, missing tests |
| **Privacy** | personal data and secrets in added lines and file names (plus a deterministic private-terms scan); by default runs **locally with no LLM** |
| **Safety** | guards, limits, permissions and config loaders a change could loosen or make fail open |

```
PR opened / updated ─► plan ─┬─ reviewer ─┐
                             ├─ privacy  ─┼─► combine ─► one PR review + one check
                             └─ safety   ─┘
```

The agents run in parallel, read-only. Each returns structured findings (file, line, severity, message) tied to the PR's head commit. The combine step is plain code. It
groups findings by line, posts them as inline comments where GitHub allows, and puts the rest in the
summary. It also collapses the previous agent review when you push again.

## Set up (about 2 minutes)

1. Copy [`examples/caller.yml`](examples/caller.yml) to `.github/workflows/agent-review.yml` in the repository.
2. Pick a model and add its secret (see [Engines](#engines)). With no secret at all, only the local privacy
   scan runs.
3. Optional: add [`examples/agent-review.yml`](examples/agent-review.yml) as `.github/agent-review.yml` to pick
   the engine and model, turn agents off, scope the safety agent to paths, make the check blocking, or give
   the agents notes about the codebase.
4. Optional: add a secret `AGENT_REVIEW_PRIVATE_TERMS` with one term per line (names, emails, internal
   hostnames). Those terms are matched in plain code and never sent to a model. A finding names the term by
   index (`private term #3`), never the term itself.

## Engines

| engine | secret | how it reviews |
|---|---|---|
| `openai-compatible` | `AGENT_REVIEW_API_KEY`, plus `base_url` and `model` in the config | Any chat-completions endpoint: OpenAI, Gemini, OpenRouter, Mistral, Groq, Together, Azure behind a gateway, Anthropic's compatibility endpoint, or a self-hosted Ollama / vLLM / LM Studio. The model gets **no tools**: the diff and the head version of each changed file go in the prompt (up to `max_context_chars`). Asks for a JSON-schema reply, falling back to JSON mode, then to the schema in the prompt. |
| `claude-code` | `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN` | Claude Code headless in `--restricted` mode, with only Read, Grep and Glob. It can explore beyond the changed files. |
| `local` (privacy only, default) | none | Secret patterns (provider key formats, credentials in URLs, generic assignments) plus [Rampart](https://huggingface.co/nationaldesignstudio/rampart), a 14.7 MB on-device PII model. No API calls, and the diff never leaves the runner. |

`engine: auto` (the default) uses whichever credential the repository has. `agents.privacy.engine` is `local`,
`llm`, or `both`.
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

## Cost and quality (measured on the demo PR below)

| setup | caught | cost |
|---|---|---|
| reviewer + safety on Claude Sonnet (claude-code), privacy local | all 3 planted problems, each reported once | about $0.16 |
| reviewer + safety on qwen3.5 9B via local Ollama (openai-compatible), privacy local | the off-by-one and the key; **missed** the unbounded env override | $0 |

That's one small PR, so treat it as a smoke test, not a benchmark. Cost grows with PR size. `max_budget_usd`
caps each claude-code agent. Drafts and PRs over `skip.max_changed_lines` are skipped.

**Local privacy engine:** 22/22 correct on a tuning set and **17/18 on a held-out set** of fictional diff
lines (names, emails, phones, IDs, addresses, IBANs, keys, versus ordinary code in seven languages). The one
miss was a false positive at suggestion level, on "the user has no email". Both sets took 0.3 s with no API
calls. Rampart is scoped to Latin-script languages; see its model card.

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

MIT licensed. The optional local privacy engine downloads Rampart (CC BY 4.0, © National Design Studio) at run
time; it is not bundled here.
