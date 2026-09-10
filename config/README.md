# Configuration and input schema

All configuration is passed on the command line (see the root `README.md`). The only
required environment variable is `ANTHROPIC_API_KEY`.

## Draft state schema

```jsonc
{
  "name": "human-readable label for the test case",
  "team_to_pick": "blue" | "red",     // whose turn it is
  "needed_role": "top" | "jungle" | "mid" | "bot" | "support",
  "blue": {
    "bans":  ["Champion Name", ...],
    "picks": [{ "champion": "Champion Name", "role": "mid" }, ...]
  },
  "red": { "bans": [...], "picks": [...] },
  "notes": "optional — what a correct answer should look like"
}
```

Champion names must match Riot's Data Dragon spelling exactly, including punctuation
and spaces: `K'Sante`, `Miss Fortune`, `Renata Glasc`, `Nunu & Willump`.

`needed_role` acts as a hard filter on the candidate pool. Omit it to let the model
choose across every available champion.

## Test cases

- `test1.json` — blue side's last pick, support slot, against a red composition with
  three engage tools. A correct answer provides peel or disengage rather than more engage.
