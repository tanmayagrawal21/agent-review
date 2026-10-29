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
   | `AGENT_REVIEW_API_KEY` | the key for your model endpoint |
   | `AGENT_REVIEW_BASE_URL` | any OpenAI-compatible endpoint, e.g. `https://api.openai.com/v1` |
   | `AGENT_REVIEW_MODEL` | the model id |

   These are secrets, not config, so which provider and model a repo uses never appears in the public repo.
3. Optional: [`examples/agent-review.yml`](examples/agent-review.yml) as `.github/agent-review.yml`, to turn
   agents off, scope them to paths, make the check blocking, or add notes about the codebase.

With no key, only the local privacy scan runs.

## Guarantees

- **Redact first.** Personal data and keys are replaced on the runner before any text leaves it. If the
  redactor can't run, nothing is sent.
- **Agents are read-only.** They get a redacted bundle only: no checkout, no shell, no tools, no GitHub
  token.
- **A PR can't change its own review.** The config is read at the base commit, and each agent's result must
  match its name and the head commit.
- **Humans merge.** The review is always a comment; `blocking: true` makes the check fail closed.

Details, diagrams and the trust model: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Measured

Benchmark: [`bench/`](bench/run.py), with planted-problem PRs scored on caught, blocked, false blocks, unredacted sends and tokens. Results are being added.

## License

MIT. Rampart (CC BY 4.0, © National Design Studio) is downloaded at run time, not bundled.
