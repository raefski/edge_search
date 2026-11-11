from edge import dfs


def test_drop_dk_out_removes_every_dk_out_tag():
    sal = {"a": {"name": "Austyn Dendy", "dk_status": "OUT"},
           "b": {"name": "Quest", "dk_status": "O"},
           "c": {"name": "Hurt", "dk_status": "IR"},
           "d": {"name": "Brendyn Smith", "dk_status": "NONE"},
           "e": {"name": "Plain", "dk_status": None}}
    kept, out = dfs.drop_dk_out(sal)
    assert set(kept) == {"d", "e"}
    assert out == ["Austyn Dendy", "Hurt", "Quest"]
