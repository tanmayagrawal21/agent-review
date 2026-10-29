# agent-review

Automatic pull-request review on GitHub Actions by three agents, posted as **one** review. It works with any
model provider. Every text is redacted locally, on the runner, before a model sees it.

| agent | owns |
|---|---|
| **Reviewer** | correctness, behaviour, compatibility, tests |
| **Safety** | controls a change could loosen or make fail open: auth, permissions, limits, validation |
| **Privacy** | secrets and personal data: [Rampart](https://huggingface.co/nationaldesignstudio/rampart) (on-device PII model) and secret patterns, plus a model pass for what they missed |

```
prepare (checkout, redact locally) ─► bundle.json ─┬─ reviewer ─┐
                                                   ├─ safety   ─┼─► publish ─► one PR review
                                                   └─ privacy  ─┘
```

## Set up

1. Copy [`examples/caller.yml`](examples/caller.yml) to `.github/workflows/agent-review.yml`.
2. Add three repository secrets (Settings → Secrets and variables → Actions):

   | secret | value |
   |---|---|
   | `AGENT_REVIEW_API_KEY` | the key for your model provider |
   | `AGENT_REVIEW_BASE_URL` | `https://api.anthropic.com`, or any OpenAI-compatible endpoint such as `https://api.openai.com/v1` |
   | `AGENT_REVIEW_MODEL` | the model id |

   They are secrets, not config, so the public repo never shows which provider or model you use. An Anthropic
   endpoint is detected automatically.
3. Open a pull request. The review appears as one comment within a few minutes.

Optional: copy [`examples/agent-review.yml`](examples/agent-review.yml) to `.github/agent-review.yml` to turn
agents off, scope them to paths, make the check blocking, or give the agents notes about your codebase.

## Guarantees

- **Redact first.** Every line goes through secret patterns and the on-device PII model before any text
  leaves the runner. If the redactor can't run, nothing is sent. (On by default; `redact: false` turns it off.)
- **Agents are read-only.** They see only the redacted bundle: no checkout, no tools, no GitHub token.
- **A PR can't change its own review.** The config is read from the base branch, and each agent's result must
  match its name and the head commit.
- **Failures never approve.** An error, a truncated reply or an unreadable verdict counts as "not approved".
- **Humans merge.** The review is a comment. Set `blocking: true` to make the check fail when something is
  wrong or a review couldn't run.

## Limits

- Diffs over `max_context_chars` (150k characters) aren't sent to a model. The review lists those agents as
  "not reviewed", never as a partial review; the local privacy scan still runs. PRs over 1,000 changed lines
  get a warning.
- Pull requests from forks get no secrets and a read-only token, so only the local privacy scan runs, and its
  result appears in the workflow run's summary rather than on the PR.
- File names are not redacted. Keep personal data out of paths.

How it works, with diagrams and the trust model: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Measured

[`bench/`](bench/run.py): 7 fictional PRs with 7 planted problems (a loosened limit, an env override, a leaked
key, an auth bypass, SQL injection, a pagination bug, personal data in a fixture) and 2 clean PRs, run through
the real pipeline with redaction on.

| setup | caught | blocked | false blocks (clean PRs) | unredacted sends | tokens in / cached / out |
|---|---|---|---|---|---|
| local: qwen3.5 9B on Ollama, $0 | 7/7 | 6/7 | 0 | 0 | 15.3k / 6.9k / 12.9k |

The one non-blocking catch is the personal-data fixture: names, emails and phones are suggestions by design.
That is one run on a small set, so it's a smoke test, not a benchmark.

## License

MIT. Rampart (CC BY 4.0, © National Design Studio) is downloaded at run time, not bundled.
