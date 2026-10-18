# Contest inbox

Drop DraftKings contest-standings downloads here: the sport's folder, then
`cash` or `gpp`.

    nhl/gpp/contest-standings-196766174.zip
    nfl/cash/contest-standings-196601669.csv

- The `.zip` DraftKings gives you and the `.csv` inside it both work.
- Keep DraftKings' file name: the number in it is the contest id. A
  ` (1)` re-download suffix is fine.
- Sports: mlb, nfl, ncaaf, nhl, nba, ncaab, nascar, mma.

Then tell Claude, or run `python3 -m harness run import-contests`. Each file
is copied to `data/contest-standings-<id>.csv`, tagged from DraftKings (sport,
cash/gpp, field, the exact slate), added to `contests.db`, graded against that
slate's pre-lock build, and removed from here. An empty inbox means
everything is in. A file that can't be filed stays here, and the import says
why.

If DraftKings disagrees with the folder you used, DraftKings wins and the
import tells you. `python3 -m harness status` lists anything still waiting.
