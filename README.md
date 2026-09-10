# Draft Advisor — Baseline

Given a League of Legends pick/ban state, recommend the next champion pick with reasoning.

## Setup

```bash
git clone <your-repo-url>
cd draft-advisor
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...      # Windows: setx ANTHROPIC_API_KEY "sk-ant-..."
```

Python 3.10+. Only one third-party dependency (`anthropic`); everything else is stdlib.

## Run

```bash
python run_baseline.py --input examples/test1.json
```

No API key? This prints the retrieval stage and the fully assembled prompt without
calling the model:

```bash
python run_baseline.py --input examples/test1.json --dry-run
```

## Flags

| Flag | Default | Meaning |
|---|---|---|
| `--input` | required | path to a draft state JSON file |
| `--model` | `claude-sonnet-5` | model used for the recommendation call |
| `--top-k` | `25` | how many candidates are sent to the model |
| `--patch` | latest | Data Dragon patch, e.g. `16.18.1` |
| `--dry-run` | off | print the prompt, skip the API call |

## Where things live

- Input drafts: `examples/*.json` (see `examples/readme.md` for the schema)
- Output: `outputs/<input-name>-recommendation.json`, plus a summary printed to stdout
- Champion data cache: `.cache/` — first run downloads ~2 MB from Riot's Data Dragon CDN,
  every run after that is offline

## Known setup limitations

- The first run needs internet access to reach `ddragon.leagueoflegends.com`.
- Data Dragon has no lane or win-rate data, so the role filter is inferred from class
  tags. Bruisers occasionally leak into support pools and some off-meta picks are missed.
- Riot's `info` scores (attack/defense/difficulty) are `0` for several recently
  released champions, which weakens the heuristic pre-ranking for those picks.
