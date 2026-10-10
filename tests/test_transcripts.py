import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "transcripts"))

from transcripts import Ledger  # noqa: E402
from transcripts.podcasts import parse_caption_file  # noqa: E402
from transcripts.youtube import _videos, parse_views, transcript_params  # noqa: E402


def test_view_counts_in_every_format_youtube_writes():
    assert [parse_views(t) for t in ("10,768 views", "14K views", "1.2M views", "No views", None)] \
        == [10768, 14000, 1200000, 0, 0]


def test_transcript_request_matches_what_youtube_sends():
    # captured from YouTube's own "Show transcript" click, 2026-09-28
    assert transcript_params("daO1JNzRPn0") == "qgkPCgtkYU8xSk56UlBuMBgB"


def test_both_result_layouts_parse_the_same():
    old = {"videoRenderer": {"videoId": "aaaaaaaaaaa", "title": {"runs": [{"text": "Week 3 DFS"}]},
                             "ownerText": {"runs": [{"text": "RG", "navigationEndpoint": {
                                 "browseEndpoint": {"browseId": "UC" + "x" * 22}}}]},
                             "viewCountText": {"simpleText": "1,234 views"},
                             "publishedTimeText": {"simpleText": "2 days ago"}}}
    new = {"lockupViewModel": {"contentId": "bbbbbbbbbbb", "metadata": {"lockupMetadataViewModel": {
        "title": {"content": "Week 3 DFS"},
        "metadata": {"contentMetadataViewModel": {"metadataRows": [
            {"metadataParts": [{"text": {"content": "RG"}}]},
            {"metadataParts": [{"text": {"content": "1.2K views"}},
                               {"text": {"content": "2 days ago"}}]}]}}}}}}
    a, b = _videos({"contents": [old, new]})
    assert (a["title"], a["channel"], a["views"], a["published"]) == ("Week 3 DFS", "RG", 1234, "2 days ago")
    assert (b["title"], b["channel"], b["views"], b["published"]) == ("Week 3 DFS", "RG", 1200, "2 days ago")


def test_podcast_caption_files():
    vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nWelcome in\n\n00:01:05.500 --> 00:01:08.000\nGibbs is a smash\n"
    srt = "1\n00:00:01,000 --> 00:00:04,000\nWelcome in\n"
    js = '{"segments": [{"startTime": 3.2, "body": "Hello"}]}'
    assert parse_caption_file(vtt) == [[1.0, "Welcome in"], [65.5, "Gibbs is a smash"]]
    assert parse_caption_file(srt) == [[1.0, "Welcome in"]]
    assert parse_caption_file(js) == [[3.2, "Hello"]]


def test_feed_transcript_under_either_namespace_uri(tmp_path):
    from transcripts import Cache, Podcasts
    item = ('<item><title>Ep</title><guid>g</guid><enclosure url="https://x/a.mp3"/>'
            '<podcast:transcript url="https://x/a.srt" type="application/srt"/></item>')
    for uri in ("https://podcastindex.org/namespace/1.0",
                "https://github.com/Podcastindex-org/podcast-namespace/blob/main/docs/1.0.md"):
        pods = Podcasts(Cache(tmp_path))
        feed = f'<rss xmlns:podcast="{uri}"><channel><title>Show</title>{item}</channel></rss>'
        pods.http.get = lambda url, *a, **k: feed.encode()
        assert pods.episodes("https://x/feed.xml")[0]["transcript_url"] == "https://x/a.srt"


def test_ledger_replaces_a_regraded_result_and_ranks(tmp_path):
    led = Ledger(tmp_path / "ledger.csv")
    led.record("nfl_ats", "UC1", "Good", "g1", "ats_correct", 0)
    led.record("nfl_ats", "UC1", "Good", "g1", "ats_correct", 1)       # regrade replaces
    led.record("nfl_ats", "UC1", "Good", "g2", "ats_correct", 1)
    led.record("nfl_ats", "UC2", "Bad", "g1", "ats_correct", 0)
    ranked = led.scores("nfl_ats", "ats_correct")
    assert [(s["source"], s["n"], s["mean"]) for s in ranked] == [("Good", 2, 1.0), ("Bad", 1, 0.0)]


def test_tout_pick_is_the_final_spread_call_not_a_prop_or_showdown():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.tout_picks import extract_pick, team_aliases
    text = ("I like the Eagles minus three and a half early. Give me the over 42. "
            "That's going to lean to a lot of 4-2 Philly builds. "
            "But in the end, give me the Bears plus the points.")
    assert extract_pick(text, "PHI", "CHI", team_aliases())[0] == "CHI"
