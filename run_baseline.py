"""
Draft Advisor - runnable baseline.

Given a League of Legends pick/ban state, recommend the next pick with reasoning.

Pipeline:
  1. Load the draft state from a JSON file.
  2. Load champion data from Riot's Data Dragon CDN (cached locally after first run).
  3. Retrieve a candidate pool: remove unavailable champions, score the rest for
     role fit and team-composition need, keep the top K.
  4. Send the draft state + candidate pool to Claude in a single call.
  5. Print and save a structured recommendation.

Usage:
    python run_baseline.py --input examples/test1.json
    python run_baseline.py --input examples/test1.json --dry-run   # no API key needed
"""

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
CACHE = ROOT / ".cache"
OUTPUTS = ROOT / "outputs"
VERSIONS_URL = "https://ddragon.leagueoflegends.com/api/versions.json"
CHAMPS_URL = "https://ddragon.leagueoflegends.com/cdn/{v}/data/en_US/championFull.json"

# Crude tag -> lane mapping. This is a known weakness of the baseline: Data Dragon
# ships no role data, so lane fit is inferred from class tags only.
TAG_LANES = {
    "Marksman": ["bot"],
    "Support": ["support"],
    "Mage": ["mid", "support"],
    "Assassin": ["mid", "jungle"],
    "Fighter": ["top", "jungle"],
    "Tank": ["top", "jungle"],
}


def fetch_json(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def load_champions(patch=None):
    """Return {name: champion_dict} from Data Dragon, cached on disk."""
    CACHE.mkdir(exist_ok=True)
    if patch is None:
        vcache = CACHE / "version.txt"
        if vcache.exists() and time.time() - vcache.stat().st_mtime < 86400:
            patch = vcache.read_text().strip()
        else:
            patch = fetch_json(VERSIONS_URL)[0]
            vcache.write_text(patch)

    cfile = CACHE / f"champions-{patch}.json"
    if cfile.exists():
        data = json.loads(cfile.read_text())
    else:
        print(f"[data] downloading champion data for patch {patch}...", file=sys.stderr)
        data = fetch_json(CHAMPS_URL.format(v=patch))
        cfile.write_text(json.dumps(data))
    return patch, data["data"]


def damage_profile(champ):
    """Rough AD/AP lean from Data Dragon's info scores."""
    atk, mag = champ["info"]["attack"], champ["info"]["magic"]
    if atk > mag + 2:
        return "AD"
    if mag > atk + 2:
        return "AP"
    return "mixed"


def summarize(champ):
    """Compact record. Full lore/spell text is dropped to keep the prompt small."""
    return {
        "name": champ["name"],
        "tags": champ["tags"],
        "damage": damage_profile(champ),
        "toughness": champ["info"]["defense"],
        "difficulty": champ["info"]["difficulty"],
        "abilities": [s["name"] for s in champ["spells"]],
        "summary": champ["blurb"].split(".")[0] + ".",
    }


def lanes_for(champ):
    lanes = []
    for t in champ["tags"]:
        lanes.extend(TAG_LANES.get(t, []))
    return sorted(set(lanes)) or ["mid"]


def score_candidate(champ, draft):
    """Heuristic pre-ranking so the LLM sees a small, plausible pool."""
    score = 0.0
    ally_damage = [damage_profile(c) for c in draft["_ally_champs"]]
    dmg = damage_profile(champ)
    if ally_damage.count(dmg) >= 3 and dmg != "mixed":
        score -= 1.5  # discourage one-dimensional damage profiles

    ally_tags = [t for c in draft["_ally_champs"] for t in c["tags"]]
    if "Tank" not in ally_tags and "Tank" in champ["tags"]:
        score += 1.5
    if "Marksman" not in ally_tags and "Marksman" in champ["tags"]:
        score += 1.0

    score -= 0.1 * champ["info"]["difficulty"]
    return score


def retrieve(champions, draft, top_k=25):
    taken = set()
    for side in ("blue", "red"):
        taken.update(draft[side]["bans"])
        taken.update(p["champion"] for p in draft[side]["picks"])

    by_name = {c["name"]: c for c in champions.values()}
    draft["_ally_champs"] = [
        by_name[p["champion"]]
        for p in draft[draft["team_to_pick"]]["picks"]
        if p["champion"] in by_name
    ]

    pool = [c for c in champions.values() if c["name"] not in taken]
    need = draft.get("needed_role")
    if need:
        role_fit = [c for c in pool if need in lanes_for(c)]
        if role_fit:
            pool = role_fit
    ranked = sorted(pool, key=lambda c: score_candidate(c, draft), reverse=True)
    return ranked[:top_k], len(pool)


def render_side(draft, side):
    picks = ", ".join(
        f"{p['champion']} ({p.get('role', '?')})" for p in draft[side]["picks"]
    ) or "none yet"
    bans = ", ".join(draft[side]["bans"]) or "none"
    return f"{side.upper()} picks: {picks}\n{side.upper()} bans: {bans}"


def build_prompt(draft, candidates, patch):
    pool = json.dumps([summarize(c) for c in candidates], indent=1)
    return f"""You are advising a League of Legends team during champion select on patch {patch}.

DRAFT STATE
{render_side(draft, 'blue')}
{render_side(draft, 'red')}

It is {draft['team_to_pick'].upper()} side's turn to pick. \
The role they still need is: {draft.get('needed_role', 'unspecified')}.

CANDIDATE POOL (pre-filtered; all are available)
{pool}

Recommend ONE champion from the candidate pool. Consider what the ally composition
is missing, what the enemy composition threatens, and damage-type balance.

Respond with ONLY a JSON object, no markdown fences, in this shape:
{{
  "pick": "<champion name>",
  "confidence": <0.0-1.0>,
  "reasoning": "<2-4 sentences on why this pick fits>",
  "team_need": "<the single biggest gap this fills>",
  "enemy_threat_addressed": "<which enemy pick this answers, or 'none'>",
  "alternatives": [
    {{"champion": "<name>", "why": "<one sentence>"}},
    {{"champion": "<name>", "why": "<one sentence>"}}
  ]
}}"""


def call_claude(prompt, model):
    try:
        import anthropic
    except ImportError:
        sys.exit("Missing dependency. Run: pip install anthropic")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set. Use --dry-run to test without a key.")

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=model,
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in msg.content if b.type == "text")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="path to a draft state JSON file")
    ap.add_argument("--model", default="claude-sonnet-5")
    ap.add_argument("--top-k", type=int, default=25)
    ap.add_argument("--patch", default=None, help="e.g. 16.17.1; defaults to latest")
    ap.add_argument("--dry-run", action="store_true", help="print the prompt, skip the API call")
    args = ap.parse_args()

    draft = json.loads(Path(args.input).read_text())
    patch, champions = load_champions(args.patch)
    candidates, pool_size = retrieve(champions, draft, args.top_k)

    print(f"[retrieval] patch {patch} | {pool_size} champions available "
          f"| {len(candidates)} sent to the model")
    print("[retrieval] top candidates: "
          + ", ".join(c["name"] for c in candidates[:8]))

    prompt = build_prompt(draft, candidates, patch)
    if args.dry_run:
        print("\n--- PROMPT ---\n")
        print(prompt)
        return

    raw = call_claude(prompt, args.model).strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

    OUTPUTS.mkdir(exist_ok=True)
    stem = Path(args.input).stem
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        (OUTPUTS / f"{stem}-raw.txt").write_text(raw)
        sys.exit(f"Model did not return valid JSON. Raw text saved to outputs/{stem}-raw.txt")

    out = OUTPUTS / f"{stem}-recommendation.json"
    out.write_text(json.dumps(result, indent=2))

    print(f"\nRECOMMENDED PICK: {result['pick']} (confidence {result['confidence']})")
    print(f"Fills: {result['team_need']}")
    print(f"Answers: {result['enemy_threat_addressed']}")
    print(f"\n{result['reasoning']}\n")
    print("Alternatives:")
    for alt in result.get("alternatives", []):
        print(f"  - {alt['champion']}: {alt['why']}")
    print(f"\n[saved] {out}")


if __name__ == "__main__":
    main()
