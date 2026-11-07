"""The NCAAF capture jobs: conference availability reports and Ourlads depth
charts (scripts/ncaaf_availability_capture.py, scripts/ncaaf_depth_capture.py).
No network: the payload and page shapes below are copied from live responses
on 2026-11-07."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ncaaf_availability_capture as avail  # noqa: E402
import ncaaf_depth_capture as depth  # noqa: E402


def _report(rows_a, rows_b, rtype="Update 2", posted="20:00:00", logo="data:image/png;base64,AAA"):
    return {"ReportType": rtype, "publishDate": "2026-11-06", "postedTime": posted,
            "conferenceTimeZone": "CT", "conferenceImage": logo, "gameNeutral": False,
            "footer": {"date": "2026-11-07", "time": "11:00:00", "location": "Columbia, MO"},
            "games": [{"teamName": "Texas", "rows": rows_a},
                      {"teamName": "Missouri", "rows": rows_b}]}


ROWS_A = [{"name": "QB #16 Arch Manning", "status": "Available", "exemptStatus": "NonExempt"},
          {"name": "RB #2 Hollywood Smothers", "status": "Out", "exemptStatus": "NonExempt"}]
ROWS_B = [{"name": "QB #9 Karle \"KJ\" Lacey Jr.", "status": "Questionable",
           "exemptStatus": "NonExempt"}]


def test_report_row_names_split_into_position_number_player():
    assert avail.split_name("QB #16 Arch Manning") == ("QB", "16", "Arch Manning")
    assert avail.split_name("CB #1 Vernon Glover, Jr.") == ("CB", "1", "Vernon Glover, Jr.")
    assert avail.split_name("N/S #4 Bo Jones") == ("N/S", "4", "Bo Jones")
    # no number: the first word is a position only if it is one
    assert avail.split_name("S CJ Christian") == ("S", "", "CJ Christian")
    assert avail.split_name("CJ Christian") == ("", "", "CJ Christian")


def test_a_version_is_the_rows_not_the_styling():
    base = avail.version_of(_report(ROWS_A, ROWS_B))
    assert avail.version_of(_report(ROWS_A, ROWS_B, logo="data:image/png;base64,BBB")) == base
    assert avail.version_of(_report(list(reversed(ROWS_A)), ROWS_B)) == base
    out = [dict(ROWS_A[0]), {**ROWS_A[1], "status": "Available"}]
    assert avail.version_of(_report(out, ROWS_B)) != base
    assert avail.version_of(_report(ROWS_A, ROWS_B, rtype="Game Day")) != base


def test_report_rows_carry_team_opponent_and_status():
    rows = avail.report_rows("SEC", "10002959", _report(ROWS_A, ROWS_B), "2026-11-07T16:00:00+00:00")
    assert len(rows) == 3
    smothers = next(r for r in rows if r["player"] == "Hollywood Smothers")
    assert (smothers["team"], smothers["opponent"], smothers["status"], smothers["pos"]) == \
        ("Texas", "Missouri", "Out", "RB")
    assert smothers["player_key"] == "hollywoodsmothers"
    lacey = next(r for r in rows if r["team"] == "Missouri")
    assert lacey["opponent"] == "Texas" and lacey["player_key"] == "karlekjlacey"
    assert set(rows[0]) == set(avail.FIELDS)


def test_archive_drops_the_logo():
    got = avail.strip_archive({"conferenceLogo": "data:...", "report_days": ["Initial"],
                               "data": [{"Player": "AK Dear", "Initial": "Questionable"}]})
    assert got == {"report_days": ["Initial"], "data": [{"Player": "AK Dear", "Initial": "Questionable"}]}


# --- Ourlads ---------------------------------------------------------------
def _cell(slug, pid, cls, name, key):
    return (f"<td><a href='https://www.ourlads.com/ncaa-football-depth-charts/player/{slug}/{pid}' "
            f"class='{cls}'>{name}</a> <span class='dc-key'>{key}</span></td>")


EMPTY = _cell("", "0", "", "", "")
PAGE = (
    "<div>Updated: 11/06/2026 9:09PM ET | Stats on player profiles</div>"
    '<tbody id="ctl00_phContent_dcTBody">'
    "<tr class='row-dc-wht'><td class='row-dc-wht'>QB</td><td>12</td>"
    + _cell("keelon-russell", "174024", "", "Russell, Keelon", "RS FR")
    + "<td>15</td>" + _cell("jett-thomalla", "178814", "lc_purple", "Thomalla, Jett", "FR")
    + "<td></td>" + EMPTY + "</tr>"
    "<tr class='row-dc-grey'><td class='row-dc-grey'>WR-X</td><td>5</td>"
    + _cell("noah-rogers", "168202", "lc_gold", "Rogers, Noah", "RS JR/TR") + "</tr></tbody>"
    '<tbody id="ctl00_phContent_dcTBody4">'
    "<tr class='row-dc-wht'><td class='row-dc-wht'>RES</td><td>23</td>"
    + _cell("cayden-jones", "167935", "", "Jones, Cayden", "JR") + "</tr></tbody>"
)


def test_depth_chart_page_parses_slots_depth_and_flags():
    stamp, rows = depth.parse_chart(PAGE)
    assert stamp == "11/06/2026 9:09PM ET"
    got = [(r["section"], r["pos"], r["depth"], r["jersey"], r["player"], r["transfer"],
            r["true_freshman"]) for r in rows]
    assert got == [("offense", "QB", 1, "12", "Keelon Russell", 0, 0),
                   ("offense", "QB", 2, "15", "Jett Thomalla", 0, 1),
                   ("offense", "WR-X", 1, "5", "Noah Rogers", 1, 0),
                   ("reserve", "RES", 1, "23", "Cayden Jones", 0, 0)]
    assert rows[0]["player_key"] == "keelonrussell" and rows[2]["class"] == "RS JR/TR"


def test_depth_version_ignores_the_stamp_but_not_a_promotion():
    _s, rows = depth.parse_chart(PAGE)
    _s2, same = depth.parse_chart(PAGE.replace("9:09PM", "10:15PM"))
    assert depth.version_of(rows) == depth.version_of(same)
    swapped = PAGE.replace("<td>12</td>", "<td>99</td>")
    assert depth.version_of(depth.parse_chart(swapped)[1]) != depth.version_of(rows)


def test_team_list_from_index_and_picker():
    index = ("<a href='depth-chart.aspx?s=alabama&id=89923'>Depth Chart</a>"
             "<a href='depth-chart.aspx?s=fcs-and-small-college-nfl-prospects&id=1'>x</a>")
    picker = ("<a class='tp-team' href='/ncaa-football-depth-charts/depth-chart.aspx?s=texas&amp;id=92131'"
              " data-conf='SEC' data-id='92131' data-search='texas longhorns'><img />"
              "<span class='tp-team-name'>Texas</span></a>")
    teams = depth.teams_from(index, picker)
    assert set(teams) == {"89923", "92131"}
    assert teams["92131"] == {"slug": "texas", "name": "Texas", "conference": "SEC"}


def test_first_last():
    assert depth.first_last("Rogers, Noah") == "Noah Rogers"
    assert depth.first_last("Brown, Trae&#39;shawn") == "Trae'shawn Brown"
    assert depth.first_last("Pele") == "Pele"
