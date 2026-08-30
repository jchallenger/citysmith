"""The local build UI: what it serves, and what it refuses to serve.

Two halves, and the safety half is not an afterthought bolted on at the end --
each clause of the design's safety paragraph has a test here that fails if the
clause is removed.

* **Loopback only.** ``test_the_server_binds_loopback_and_nothing_else`` and
  ``test_a_request_from_another_hostname_is_refused``.
* **Named operations, typed parameters, never a command string.**
  ``test_the_build_endpoint_takes_no_command_string`` and
  ``test_nothing_in_the_server_can_reach_a_shell``.
* **Browser paths resolve against out/ and are rejected outside it.**
  ``test_a_path_cannot_be_walked_out_of_the_output_directory`` (units) and
  ``test_the_file_endpoint_refuses_an_escape`` (over HTTP).
* **The Anthropic key stays server-side.**
  ``test_the_anthropic_key_never_reaches_the_browser``.
* **The paste half runs PowerShell, and does it with argument lists.**
  ``test_the_paste_driver_never_composes_a_shell_command`` -- and
  ``test_nothing_in_the_server_can_reach_a_shell`` still holds over
  ``uiserver.py`` itself, which is why that work is in `pastedrive`.

The paste screen's own claim is the precondition rule, and it is one line long:
an **explicit** ``build plane off`` starts a run and nothing else does.
``test_an_unreadable_build_plane_refuses_exactly_like_a_raised_one`` covers
``UNKNOWN``, the reading `CLAUDE.md` records this project acting on once
already; ``test_the_paste_endpoint_refuses_when_talespire_is_not_running``
covers the game being down. Neither ever runs `review.ps1`: every probe and
every spawn in these tests is a stub, so the suite drives nothing and pastes
nothing.

And the build half is anchored on one claim:
``test_the_build_endpoint_returns_the_same_findings_as_the_cli`` runs the real
command and the real endpoint over the same layout with the same stub palette,
and requires the findings to match sentence for sentence, in the same order. A
UI that renders a different report from the CLI is a second source of truth,
and this project has spent whole sessions on the cost of those.

The stub palette and the four-house hamlet are `test_pipeline`'s, deliberately:
that module already pins the command's output character for character, so
reusing them means these two tests are comparing the same build and not two
similar ones.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import pathlib
import re
import threading
import time
import types

import pytest

import test_pipeline
from citysmith import pastedrive, uiserver
from citysmith.pipeline import STAGES

FINDING = re.compile(r"^\[(?:FAIL|WARN|ok {2})\] ")
TIMEOUT = 120.0


# -- driving the server -------------------------------------------------------

@contextlib.contextmanager
def running(**kwargs):
    """A server on an ephemeral port, in a thread, torn down afterwards.

    Never a fixed port: a test that binds 8765 fails on the machine of anyone
    who happens to have the UI open, which is the machine most likely to be
    running the suite.
    """
    kwargs.setdefault("log", lambda line: None)
    server = uiserver.make_server(port=0, **kwargs)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def call(port, path, *, method="GET", body=None, host=None,
         content_type="application/json"):
    """One request. Returns ``(status, headers, raw body)``.

    `http.client` rather than `urllib.request` because one test has to send a
    ``Host`` header of its own, and `urllib` will not let go of that one.
    """
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    headers = {}
    payload = None
    if host is not None:
        headers["Host"] = host
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = content_type
    try:
        conn.request(method, path, payload, headers)
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


def call_json(port, path, **kwargs):
    status, headers, raw = call(port, path, **kwargs)
    return status, json.loads(raw.decode("utf-8"))


def only_source(port, kind="layout") -> str:
    status, data = call_json(port, "/api/sources")
    assert status == 200, data
    matches = [s for s in data["sources"] if s["kind"] == kind]
    assert matches, f"no {kind} source found: {data['sources']}"
    return matches[0]["id"]


def finish(port, job_id: str, timeout: float = TIMEOUT) -> dict:
    """Poll the way the page does, until the job stops running."""
    deadline = time.monotonic() + timeout
    after, seen = -1, []
    while time.monotonic() < deadline:
        status, snapshot = call_json(port, f"/api/build/{job_id}?after={after}")
        assert status == 200, snapshot
        seen += snapshot["events"]
        after = snapshot["next"] - 1
        if snapshot["state"] != "running":
            snapshot["events"] = seen
            return snapshot
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


def build(port, source_id, **fields) -> dict:
    """Start a build and wait for it, the way the page does."""
    status, started = call_json(port, "/api/build", method="POST",
                                body={"source": source_id, **fields})
    assert status == 202, started
    return finish(port, started["job"])


def palette_factory(style="medieval", seed=0):
    """The stub `test_pipeline` pins the CLI's own output with."""
    return test_pipeline._Palette()


# -- the report ---------------------------------------------------------------

def test_the_build_endpoint_returns_the_same_findings_as_the_cli(
        tmp_path, monkeypatch, capsys):
    """The UI's report is the command's report -- sentence for sentence.

    Not "a summary of", not "the failures from". Every `verify` finding is the
    residue of a session that read a board wrong, and the level and the order
    carry as much as the words: worst first is how a reader finds the thing
    that stopped the map working. So this compares the endpoint's findings
    against the ones the command printed, as a list, including the ``[ok  ]``
    ones.
    """
    printed = test_pipeline._run(tmp_path, monkeypatch, capsys)
    expected = [line for line in printed.splitlines() if FINDING.match(line)]
    assert expected, "the command printed no findings -- the golden has moved"

    with running(out_dir=tmp_path / "uiout", roots=(tmp_path,),
                 palette_factory=palette_factory) as (_, port):
        snapshot = build(port, only_source(port), stem="pin")

    assert snapshot["state"] == "done", snapshot["error"]
    result = snapshot["result"]

    assert [f["text"] for f in result["findings"]] == expected

    # The parts, not just the rendered line: a UI colours a failure red and
    # counts the warnings, and it can only do that off `level`.
    for finding, line in zip(result["findings"], expected):
        assert finding["level"] in ("fail", "warn", "pass")
        assert finding["check"] in line
        assert finding["detail"] in line
        assert finding["detail"], "a finding arrived with no sentence in it"

    assert result["counts"] == {
        "fail": sum(1 for f in result["findings"] if f["level"] == "fail"),
        "warn": sum(1 for f in result["findings"] if f["level"] == "warn"),
        "pass": sum(1 for f in result["findings"] if f["level"] == "pass"),
    }
    assert result["failed"] is any(f["level"] == "fail" for f in result["findings"])
    # The one-line summary the command prints above the report, too.
    assert result["summary"] in printed


def test_the_endpoint_carries_everything_the_command_prints_after_the_build(
        tmp_path, monkeypatch, capsys):
    """Not only the findings: the chunk table, the map and the paste help."""
    printed = test_pipeline._run(tmp_path, monkeypatch, capsys)

    with running(out_dir=tmp_path / "uiout", roots=(tmp_path,),
                 palette_factory=palette_factory) as (server, port):
        snapshot = build(port, only_source(port), stem="pin")
    result = snapshot["result"]

    for chunk in result["chunks"]:
        assert chunk["file"] in printed
        assert (server.out_dir / chunk["file"]).is_file()
        assert chunk["size_bytes"] > 0
    assert result["raster_svg"] == "city-raster.svg"
    assert result["npc_manifest"] == "pin-npcs.json"
    assert result["help"] in printed
    assert result["tile_size"] > 0 and result["rows"] >= 1
    # The grid map the command draws with # and . is on the result as booleans.
    assert len(result["grid"]) == result["rows"]
    assert all(len(row) == result["cols"] for row in result["grid"])
    assert any(any(row) for row in result["grid"])


def test_the_chunk_list_is_in_paste_order_and_nothing_sorts_it(tmp_path):
    """Paste order is not filename order, and it is not this endpoint's guess.

    The chunk covering the anchor cell is written LAST so the anchor is still
    bare board for every paste before it. An alphabetical glob sorts it into
    the middle and the chunks after it inherit its height -- which is a quarter
    of a map standing a course proud, with nothing wrong in any file.
    """
    layout = test_pipeline._layout(tmp_path)
    assert layout.exists()
    with running(out_dir=tmp_path / "uiout", roots=(tmp_path,),
                 palette_factory=palette_factory) as (server, port):
        snapshot = build(port, only_source(port), stem="pin", by_region=True,
                         max_assets=400)
    result = snapshot["result"]

    on_disk = (server.out_dir / "pin-paste-order.txt").read_text(
        encoding="utf-8").split()
    assert result["paste_order"] == on_disk
    assert [c["file"] for c in result["chunks"]] == on_disk


def test_the_serialiser_keeps_the_order_it_was_given(tmp_path):
    """A unit test for the same rule, with an order that alphabetising breaks.

    The hamlet above happens to come out in an order a sort would not disturb,
    so on its own it could not catch a `sorted()` creeping in. This one can.
    """
    from citysmith import pipeline
    from citysmith.verify import Report

    class Stub:
        def summary(self):
            return "stub"

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    chunks = [
        pipeline.WrittenChunk(
            path=out_dir / name, label=name, region=name, layer="", name="",
            row=0, col=i, x0=0, z0=0, x1=1, z1=1, assets=1, buildings=0,
            size_bytes=10, chars=13)
        for i, name in enumerate(["z-anchor.slab.txt", "a-first.slab.txt",
                                  "m-middle.slab.txt"])
    ]
    report = Report()
    report.add("pass", "connectivity", "one connected town")
    report.add("fail", "placements", "94 tiles are off the grid")
    report.add("warn", "gates", "no gates found")

    result = pipeline.BuildResult(
        layout=Stub(), tilemap=Stub(), population=None, out_dir=out_dir,
        stem="x", chunk_budget=1, budget_from_board_size=False,
        chunks=chunks, rows=1, cols=3, tile_size=24,
        paste_order=tuple(c.path.name for c in chunks), report=report)

    payload = uiserver.result_json(result, out_dir)
    assert payload["paste_order"] == ["z-anchor.slab.txt", "a-first.slab.txt",
                                      "m-middle.slab.txt"]
    assert [c["file"] for c in payload["chunks"]] == payload["paste_order"]
    # And the findings come out in `Report.text()`'s order: worst first.
    assert [f["text"] for f in payload["findings"]] == report.text().splitlines()


def test_a_geojson_export_is_imported_before_it_is_built(tmp_path):
    """"Pick a layout or a GeoJSON" -- and the GeoJSON half is the awkward one.

    A town export is not a layout: it has to be sniffed (MFCG and FTG both ship
    as `.json` and as `.geojson`, so the extension settles nothing) and
    imported first. That happens inside the job, so the import shows up in the
    progress log like any other stage, and the layout it produces is written
    where the next build can pick it out of the list.
    """
    sample = pathlib.Path(__file__).resolve().parents[1] / "samples" / "forest_church.json"
    if not sample.is_file():
        pytest.skip("no sample export in this checkout")
    (tmp_path / sample.name).write_bytes(sample.read_bytes())

    with running(out_dir=tmp_path / "uiout", roots=(tmp_path,),
                 palette_factory=palette_factory) as (server, port):
        source = only_source(port, kind="geojson")
        # Cropped, because this test is about the import and not about how long
        # a whole town takes to build.
        snapshot = build(port, source, stem="fc",
                         crop={"x": 60, "z": 60, "w": 48, "d": 48})

    assert snapshot["state"] == "done", snapshot["error"]
    stages = [e["stage"] for e in snapshot["events"]]
    assert stages.index("importing") < stages.index("rasterized")
    assert (server.out_dir / "layout.json").is_file()
    assert snapshot["result"]["chunks"]


# -- the import half ----------------------------------------------------------
#
# `import_layout` used to be called with a seed and nothing else, so every knob
# `citysmith import` offers -- the crop window most of all -- was reachable from
# the command line and from nowhere on the page. On `samples/sedgewater.geojson`
# that silently costs three outlying farms and most of the board.


def _sedgewater(tmp_path) -> pathlib.Path:
    sample = (pathlib.Path(__file__).resolve().parents[1]
              / "samples" / "sedgewater.geojson")
    if not sample.is_file():
        pytest.skip("no FTG sample export in this checkout")
    (tmp_path / sample.name).write_bytes(sample.read_bytes())
    return tmp_path


def _imported_line(snapshot) -> str:
    """The first line of the `imported` event: the layout's own summary."""
    for event in snapshot["events"]:
        if event["stage"] == "imported":
            return event["text"].splitlines()[0]
    raise AssertionError(f"no import in {[e['stage'] for e in snapshot['events']]}")


def test_the_import_options_reach_the_importer(tmp_path):
    """The measurement, driven through the endpoint rather than in-process.

    Two builds off one export, differing only in the two fields the form now
    carries. If they were dropped the two summaries would be identical -- which
    is exactly what they were before, and why a UI user lost three farms with no
    control on the page to prevent it.

    The build is cropped to a corner so this costs a hamlet rather than a town.
    The crop is applied to the imported layout, so the summary still reports the
    whole board the import produced.
    """
    root = _sedgewater(tmp_path)
    crop = {"x": 0, "z": 0, "w": 24, "d": 24}

    def run(**fields):
        with running(out_dir=root / "out", roots=(root,),
                     palette_factory=palette_factory) as (_, port):
            snapshot = build(port, only_source(port, kind="geojson"),
                             stem="sw", crop=crop, **fields)
        assert snapshot["state"] == "done", snapshot["error"]
        return snapshot

    default = _imported_line(run())
    whole = _imported_line(run(core_only=False, margin_feet=200.0))

    assert "138x114 tiles" in default, default
    assert "227x203 tiles" in whole, whole


def test_the_import_defaults_are_the_importers_own(tmp_path):
    """A request that sets nothing must ask for nothing.

    Two copies of a default is one copy that goes stale, and a form whose
    "unchanged" is not the importer's unchanged changes the map for everybody
    who never touched the field. So the parser's answer for an empty body and
    the defaults `/api/options` sends the page are both checked against
    `IMPORT_OPTIONS`, which is also what `unused_import_options` measures a
    chosen value against.
    """
    sources = [uiserver.Source(id="s1", path=tmp_path / "x.geojson",
                               kind="geojson", label="x", detail="", size=1)]
    params = uiserver.read_build_request({"source": "s1"}, sources)
    assert {k: params[k] for k in uiserver.IMPORT_OPTIONS} == uiserver.IMPORT_OPTIONS

    test_pipeline._layout(tmp_path)
    with running(out_dir=tmp_path / "uiout", roots=(tmp_path,),
                 palette_factory=palette_factory) as (_, port):
        status, options = call_json(port, "/api/options")
    assert status == 200, options
    for key, value in uiserver.IMPORT_OPTIONS.items():
        assert options["defaults"][key] == value, key

    # And the page is told which reader takes which, rather than deciding for
    # itself: three of these are FTG-only, and that fact lives in `importers`.
    assert set(options["import_options"]) == {"mfcg", "ftg"}
    ftg_only = (set(options["import_options"]["ftg"])
                - set(options["import_options"]["mfcg"]))
    assert ftg_only == {"core_only", "cluster_gap_ft", "fences"}


def test_a_default_the_form_sends_is_the_default_the_reader_has():
    """`IMPORT_OPTIONS` is a second copy of somebody else's defaults.

    The test above pins the form and the parser to that copy; this one pins the
    copy to the originals. Without it, a form whose "unchanged" is not the
    reader's unchanged silently changes the map for everyone who never touched
    the field -- and it would look like a generator change, not a form one.

    Two shapes, and both are checked because they mean the same thing by
    different routes. A concrete default has to match the reader's signature.
    `None` means "say nothing", and it only means that because `_filter` drops
    a `None` before the reader ever sees it -- which is how one form serves two
    readers whose own defaults differ (`house_frontage_ft` is 35.0 in MFCG and
    None in FTG).
    """
    import inspect

    from citysmith import ftg, importers, mfcg

    readers = {importers.MFCG: mfcg.import_layout, importers.FTG: ftg.import_layout}
    checked = set()
    for key, ours in uiserver.IMPORT_OPTIONS.items():
        for fmt, reader in readers.items():
            if key not in importers.options_for(fmt):
                continue
            theirs = inspect.signature(reader).parameters[key].default
            if ours is not None:
                assert ours == theirs, (key, fmt, ours, theirs)
            checked.add(key)
    assert checked == set(uiserver.IMPORT_OPTIONS), (
        "an option no reader accepts is a control that can never do anything")

    nulls = {k for k, v in uiserver.IMPORT_OPTIONS.items() if v is None}
    kept = importers._filter({k: None for k in nulls}, importers.options_for("ftg"))
    assert kept == {}, "a None must be dropped, or it overrides the reader's own"


def test_an_import_option_that_cannot_apply_is_reported_rather_than_dropped(tmp_path):
    """`verify.feature_report`'s rule, applied to a request instead of to a map.

    A `layout.json` is already imported, so none of these can do anything to
    it -- and an option that is set, dropped and never mentioned looks exactly
    like one that was honoured. The page hides the fields for a layout source,
    but the page is not the only thing that can post to this endpoint.

    The other half matters as much: a build that changed nothing must not carry
    the line, or it is on every build and nobody reads it.
    """
    test_pipeline._layout(tmp_path)
    with running(out_dir=tmp_path / "uiout", roots=(tmp_path,),
                 palette_factory=palette_factory) as (_, port):
        source = only_source(port)
        chosen = build(port, source, stem="a", core_only=False, fences=False)
        untouched = build(port, source, stem="b")

    assert chosen["state"] == "done", chosen["error"]
    said = {e["stage"]: e["text"] for e in chosen["events"]}
    assert "NOT USED" in said["import_options"], said["import_options"]
    assert "core_only" in said["import_options"]
    assert "fences" in said["import_options"]
    assert "already a layout" in said["import_options"]

    assert untouched["state"] == "done", untouched["error"]
    assert "import_options" not in [e["stage"] for e in untouched["events"]]


def test_an_option_the_format_has_no_use_for_is_named_with_the_format():
    """MFCG and FTG do not carry the same knobs, and `importers` is the record.

    MFCG exports geometry only: no settled core to crop to, no fences to drop.
    `importers.options_for` is what says so, so this and `import_layout`'s own
    filter cannot disagree about which options a reader takes.
    """
    accepted = uiserver.importers.options_for("mfcg")
    params = {**uiserver.IMPORT_OPTIONS, "core_only": False, "margin_feet": 200.0}

    assert uiserver.unused_import_options(params, "ftg") == []
    assert uiserver.unused_import_options(params, "mfcg") == ["core_only"]
    assert "margin_feet" in accepted        # so it is absent from that answer
    assert uiserver.unused_import_options(params, None) == ["core_only", "margin_feet"]

    line = uiserver.unused_import_line(["core_only"], "mfcg")
    assert "MFCG" in line and "core_only" in line


def test_import_layout_is_called_with_spelled_out_keywords():
    """The sibling of `test_build_town_is_called_with_spelled_out_keywords`.

    Same clause for the same reason -- `**params` would put every keyword of
    both importers within reach of a request. The second assertion is the one
    that catches this bug's own shape: a field offered on the form, coerced by
    the parser, and then never passed on. That is a control that does nothing
    and says nothing.
    """
    source = pathlib.Path(uiserver.__file__).read_text(encoding="utf-8")
    call_site = source.split("layout = importers.import_layout(", 1)[1]
    call_site = call_site.split("\n            )", 1)[0]
    assert "**" not in call_site
    for name in uiserver.IMPORT_OPTIONS:
        assert f"{name}=params[" in call_site, name


def test_the_page_offers_the_import_fields_and_hides_them_for_a_layout():
    """A control for every option, and none of them on a source that cannot use one.

    Read off the page rather than asserted about it: the whole defect was an
    option that existed everywhere except where somebody could reach it.
    """
    here = pathlib.Path(uiserver.__file__).resolve().parent / "ui"
    page = (here / "index.html").read_text(encoding="utf-8")
    script = (here / "app.js").read_text(encoding="utf-8")

    for name in uiserver.IMPORT_OPTIONS:
        assert f'id="{name}"' in page, name
        assert f'"{name}"' in script, name

    assert 'id="import-fields" hidden' in page
    assert "showImportFields" in script
    # The gate is the source's kind, and the request drops the fields to match.
    assert 'kind !== "geojson"' in script


# -- progress -----------------------------------------------------------------

def test_the_build_endpoint_returns_a_job_id_instead_of_the_report(tmp_path):
    """A build is minutes on a big town, so no request waits for one.

    The structural claim, which does not depend on this hamlet being slow: the
    POST's body can only be a job id. There is nowhere in it to put a report,
    so a caller cannot be written that blocks on the answer.
    """
    test_pipeline._layout(tmp_path)
    with running(out_dir=tmp_path / "uiout", roots=(tmp_path,),
                 palette_factory=palette_factory) as (_, port):
        status, started = call_json(port, "/api/build", method="POST",
                                    body={"source": only_source(port)})
        assert status == 202
        assert set(started) == {"job"}

        snapshot = finish(port, started["job"])
        assert snapshot["state"] == "done", snapshot["error"]
        stages = [e["stage"] for e in snapshot["events"]]
        assert stages[0] == "started" and stages[-1] == "finished"
        for stage in ("rasterized", "npcs", "budget", "npc_manifest"):
            assert stage in stages
        assert all(e["text"] for e in snapshot["events"])

        # `after` is the whole of the polling contract: ask for what you have
        # not seen and get nothing back twice.
        _, again = call_json(port, f"/api/build/{started['job']}"
                                   f"?after={snapshot['next'] - 1}")
        assert again["events"] == []
        assert again["state"] == "done"


def test_every_pipeline_stage_has_a_line():
    """`pipeline.STAGES` is a closed vocabulary, so the UI must cover all of it.

    A stage that fires with no renderer is a build the page narrates with a
    hole in it, and the hole is invisible -- which is exactly the failure this
    project keeps writing down.
    """
    stubs = {"tilemap": type("T", (), {"summary": lambda self: "tiles"})(),
             "population": type("P", (), {"summary": lambda self: "folk"})(),
             "assets": 9000, "path": pathlib.Path("out/x-npcs.json"),
             "posts": 4}
    for stage, fields in STAGES.items():
        line = uiserver._pipeline_line(stage, {f: stubs[f] for f in fields})
        assert line and isinstance(line, str), stage


def test_a_second_build_is_refused_rather_than_racing(tmp_path):
    """Two builds in one output directory delete each other's slab files.

    `write_chunks` clears the stem's previous output before writing, so a
    second build starting mid-flight leaves a map that is half of each. Held
    here by blocking the first job inside its palette factory, so the race is
    deterministic rather than a matter of who wins.
    """
    test_pipeline._layout(tmp_path)
    gate = threading.Event()

    def slow_palette(style, seed):
        gate.wait(timeout=30)
        return test_pipeline._Palette()

    with running(out_dir=tmp_path / "uiout", roots=(tmp_path,),
                 palette_factory=slow_palette) as (_, port):
        source = only_source(port)
        status, started = call_json(port, "/api/build", method="POST",
                                    body={"source": source})
        assert status == 202
        try:
            status, refused = call_json(port, "/api/build", method="POST",
                                        body={"source": source})
            assert status == 409
            assert "already running" in refused["error"]
        finally:
            gate.set()
        assert finish(port, started["job"])["state"] == "done"


def test_a_style_the_packs_cannot_supply_fails_the_job_not_the_request(tmp_path):
    """The catalog is read on the worker, so its errors arrive as job errors.

    Resolving a palette can mean building a catalog off the whole TaleSpire
    install. Doing that in a request handler is the "do not block a request"
    rule broken in the one place nobody would look for it.
    """
    test_pipeline._layout(tmp_path)

    def refuses(style, seed):
        raise uiserver.PaletteError(
            f"Style {style!r} cannot be used with your installed packs:\n  "
            "no asset for role 'floor'")

    with running(out_dir=tmp_path / "uiout", roots=(tmp_path,),
                 palette_factory=refuses) as (_, port):
        snapshot = build(port, only_source(port))

    assert snapshot["state"] == "error"
    assert "cannot be used with your installed packs" in snapshot["error"]
    # A sentence written for a person, not a class name and a stack.
    assert not snapshot["error"].startswith("PaletteError")
    assert snapshot["result"] is None


# -- safety: loopback ---------------------------------------------------------

@pytest.mark.parametrize("host", ["0.0.0.0", "", "::", "192.168.1.20",
                                  "example.com"])
def test_the_server_binds_loopback_and_nothing_else(host, tmp_path):
    """There is no configuration in which this listens on a network."""
    with pytest.raises(ValueError, match="127.0.0.1 only"):
        uiserver.make_server(host=host, port=0, out_dir=tmp_path,
                             palette_factory=palette_factory)


def test_the_bound_address_is_loopback(tmp_path):
    with running(out_dir=tmp_path, roots=(tmp_path,),
                 palette_factory=palette_factory) as (server, _):
        assert server.server_address[0] == "127.0.0.1"


def test_a_request_from_another_hostname_is_refused(tmp_path):
    """The DNS-rebinding guard.

    A page on the open web can point its own hostname at 127.0.0.1 and then
    talk to whatever is listening here through the browser. The one thing it
    cannot forge is the ``Host`` header, so that is what is checked.
    """
    with running(out_dir=tmp_path, roots=(tmp_path,),
                 palette_factory=palette_factory) as (_, port):
        status, data = call_json(port, "/api/options",
                                 host=f"attacker.example.com:{port}")
        assert status == 403
        assert "loopback" in data["error"]

        for good in (f"127.0.0.1:{port}", f"localhost:{port}", "localhost"):
            status, _ = call_json(port, "/api/options", host=good)
            assert status == 200, good


def test_no_response_ever_carries_a_cors_header(tmp_path):
    """Without it, another origin can send a request and never read the answer."""
    with running(out_dir=tmp_path, roots=(tmp_path,),
                 palette_factory=palette_factory) as (_, port):
        for path in ("/", "/app.js", "/api/options", "/api/sources"):
            _, headers, _ = call(port, path)
            assert not any(h.lower().startswith("access-control")
                           for h in headers)
            assert "default-src 'self'" in headers["Content-Security-Policy"]
            assert headers["X-Content-Type-Options"] == "nosniff"


# -- safety: named operations -------------------------------------------------

def test_the_build_endpoint_takes_no_command_string(tmp_path):
    """Every field is a number, a boolean, or a member of a closed set.

    An unknown key is an ERROR rather than something ignored, which is the
    clause that matters: ignoring unknown keys is how a caller reaches a
    `build_town` keyword the form never offered, and how a "cmd" field gets
    added later without anybody noticing it was accepted all along.
    """
    test_pipeline._layout(tmp_path)
    with running(out_dir=tmp_path / "uiout", roots=(tmp_path,),
                 palette_factory=palette_factory) as (_, port):
        source = only_source(port)

        for smuggled in ({"cmd": "citysmith build"}, {"args": ["--seed", "1"]},
                         {"flags": "--by-region"}, {"shell": True},
                         {"out_dir": "/etc"}, {"palette": "x"},
                         {"progress": "x"}):
            status, data = call_json(
                port, "/api/build", method="POST",
                body={"source": source, **smuggled})
            assert status == 400, smuggled
            assert "unknown field" in data["error"]
            assert list(smuggled)[0] in data["error"]

        # Values are checked against the closed set, not merely for shape, so
        # nothing that looks like a shell fragment survives being a style.
        bad = [
            ({"style": "medieval; rm -rf /"}, "style"),
            ({"fence_style": "$(whoami)"}, "fence_style"),
            ({"hour": "day && curl evil"}, "hour"),
            ({"stem": "../../etc/passwd"}, "stem"),
            ({"stem": "city; rm -rf /"}, "stem"),
            ({"seed": "3"}, "seed"),            # a string is not a number
            ({"seed": True}, "seed"),           # nor is a checkbox
            ({"storeys": 999}, "storeys"),
            ({"chunk_tiles": -1}, "chunk_tiles"),
            ({"roofs": "yes"}, "roofs"),
            ({"crop": "0,0,10,10"}, "crop"),
            ({"source": "../layout.json"}, "source"),
            # The import half. `json.loads` accepts the bare literals
            # `Infinity` and `NaN`, so a float field that only range-checks
            # takes an infinite margin -- which is a board with no size.
            ({"margin_feet": float("inf")}, "margin_feet"),
            ({"margin_feet": float("nan")}, "margin_feet"),
            ({"margin_feet": "60"}, "margin_feet"),
            ({"margin_feet": True}, "margin_feet"),
            ({"margin_feet": -1}, "margin_feet"),
            ({"house_frontage_ft": 100000}, "house_frontage_ft"),
            ({"core_only": "yes"}, "core_only"),
            ({"fences": 1}, "fences"),
            ({"name": "../../etc/passwd"}, "name"),
            ({"name": "<script>alert(1)</script>"}, "name"),
            ({"name": "x" * 61}, "name"),
            ({"name": 7}, "name"),
            # The CLI spells three of these as negatives. A form takes the
            # positive, and the flag spelling is an unknown field, never a
            # synonym that quietly means the opposite.
            ({"whole_canvas": True}, "whole_canvas"),
            ({"no_fences": True}, "no_fences"),
        ]
        for field, name in bad:
            status, data = call_json(port, "/api/build", method="POST",
                                     body={"source": source, **field})
            assert status == 400, field
            assert name in data["error"], (field, data)

        # And a form post from another origin cannot even reach the parser.
        status, data = call_json(port, "/api/build", method="POST",
                                 body={"source": source},
                                 content_type="application/x-www-form-urlencoded")
        assert status == 400 and "application/json" in data["error"]


def test_nothing_in_the_server_can_reach_a_shell():
    """The clause, read off the source.

    A typed API is only worth what the code behind it does with the values, so
    this asserts the module has no way to run anything: no `subprocess`, no
    `os.system`, no `eval`. It is a blunt test and it is the one that would
    have caught the convenient one-liner somebody adds in a hurry.
    """
    source = pathlib.Path(uiserver.__file__).read_text(encoding="utf-8")
    # Strip the docstring's own mention of the words it promises not to use.
    body = source.split('"""', 2)[-1]
    for forbidden in ("subprocess", "os.system", "os.popen", "os.exec",
                      "eval(", "exec(", "shell=True", "__import__",
                      "pty.", "commands."):
        assert forbidden not in body, forbidden


def test_build_town_is_called_with_spelled_out_keywords():
    """No ``**params``: the set of things this endpoint can do is a closed list.

    `build_town(**body)` would make every keyword of the pipeline reachable
    from a request the moment one is added, which is the difference between an
    API and a remote function call.
    """
    source = pathlib.Path(uiserver.__file__).read_text(encoding="utf-8")
    call_site = source.split("result = build_town(", 1)[1].split(")\n", 1)[0]
    assert "**" not in call_site
    for name in ("seed=", "storeys=", "crop=", "fence_style=", "by_region=",
                 "hour=", "max_assets=", "chunk_tiles="):
        assert name in call_site, name


# -- safety: paths ------------------------------------------------------------

def test_a_path_cannot_be_walked_out_of_the_output_directory(tmp_path):
    """`resolve_in` is the only route from a request to a filename."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "city-raster.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("not yours", encoding="utf-8")

    assert uiserver.resolve_in(out_dir, "city-raster.svg").is_file()

    for escape in ("../secret.txt", "..\\secret.txt", "a/../../secret.txt",
                   "..%2Fsecret.txt", "%2e%2e/secret.txt",
                   "/etc/passwd", "//etc/passwd",
                   "C:\\Windows\\win.ini", "C:/Windows/win.ini", ""):
        with pytest.raises(uiserver.BadRequest):
            uiserver.resolve_in(out_dir, escape)

    # Only the kinds of file this program writes.
    (out_dir / "notes.exe").write_text("x", encoding="utf-8")
    with pytest.raises(uiserver.BadRequest, match="not a kind of file"):
        uiserver.resolve_in(out_dir, "notes.exe")


def test_an_absolute_request_is_refused_without_touching_the_disk(tmp_path,
                                                                  monkeypatch):
    """The refusal has to happen on the STRING, and this is the test that says
    so -- `//etc/passwd` is a UNC path, and `Path.resolve()` on one is a network
    call that blocks for 16 seconds waiting for SMB to give up on a host named
    `etc`. That is a denial of service with a one-line payload, and it was also
    12% of this suite's entire runtime.

    Asserted by breaking the disk rather than by timing the call: a stopwatch
    test would be flaky on a loaded machine and would pass for the wrong reason
    on a machine with no network. If `resolve()` is reached at all, this fails.
    """
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    def boom(self, *a, **kw):
        raise AssertionError(f"resolve() reached the disk for {self!r}")

    monkeypatch.setattr(pathlib.Path, "resolve", boom)

    for escape in ("//etc/passwd", "/etc/passwd", "\\\\host\\share\\x",
                   "C:\\Windows\\win.ini", "C:/Windows/win.ini", "C:notes.svg",
                   "%2f%2fetc/passwd"):
        with pytest.raises(uiserver.BadRequest):
            uiserver.resolve_in(out_dir, escape)


def test_a_symlink_out_of_the_output_directory_is_refused(tmp_path):
    """Which is why the comparison is on resolved paths and not on strings."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "secret.txt").write_text("not yours", encoding="utf-8")
    try:
        (out_dir / "escape.txt").symlink_to(outside / "secret.txt")
    except (OSError, NotImplementedError) as exc:  # Windows needs privilege
        pytest.skip(f"cannot create a symlink here: {exc}")

    with pytest.raises(uiserver.BadRequest, match="outside"):
        uiserver.resolve_in(out_dir, "escape.txt")


def test_the_file_endpoint_refuses_an_escape(tmp_path):
    """The same rule, over HTTP, with the encodings a browser can send."""
    out_dir = tmp_path / "uiout"
    out_dir.mkdir()
    (out_dir / "city-raster.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("not yours", encoding="utf-8")

    with running(out_dir=out_dir, roots=(tmp_path,),
                 palette_factory=palette_factory) as (_, port):
        status, _, body = call(port, "/api/files/city-raster.svg")
        assert status == 200 and body == b"<svg/>"

        for escape in ("/api/files/../secret.txt",
                       "/api/files/..%2Fsecret.txt",
                       "/api/files/%2e%2e%2Fsecret.txt",
                       "/api/files/a/../../secret.txt",
                       "/api/files//etc/passwd",
                       "/api/files/C:%5CWindows%5Cwin.ini"):
            status, _, body = call(port, escape)
            assert status in (400, 403, 404), escape
            assert b"not yours" not in body, escape

        status, _, _ = call(port, "/api/files/city-raster.svg", method="POST",
                            body={})
        assert status == 405


# -- safety: the key ----------------------------------------------------------

def test_the_anthropic_key_never_reaches_the_browser(tmp_path, monkeypatch):
    """The key is the server's. The browser is told a boolean and no more.

    And it could not use one anyway: the page is served under a
    Content-Security-Policy of ``default-src 'self'``, so api.anthropic.com is
    not an origin it can reach.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-SENTINEL-do-not-leak")
    test_pipeline._layout(tmp_path)

    with running(out_dir=tmp_path / "uiout", roots=(tmp_path,),
                 palette_factory=palette_factory) as (_, port):
        bodies = []
        for path in ("/", "/app.css", "/app.js", "/api/options", "/api/sources"):
            status, headers, body = call(port, path)
            assert status == 200, path
            bodies.append((path, body))

        status, started = call_json(port, "/api/build", method="POST",
                                    body={"source": only_source(port)})
        snapshot = finish(port, started["job"])
        bodies.append(("job", json.dumps(snapshot).encode("utf-8")))

        for path, body in bodies:
            assert b"SENTINEL" not in body, path
            assert b"ANTHROPIC_API_KEY" not in body, path

        # What the browser is told about the key: that there is one.
        _, options = call_json(port, "/api/options")
        assert options["ai_available"] is True
        assert isinstance(options["ai_available"], bool)
        # And the page has no way to spend it: the only origin it may reach is
        # its own, so nothing here can call an API on the user's account.
        _, headers, _ = call(port, "/")
        assert "connect-src 'self'" in headers["Content-Security-Policy"]


def test_the_page_talks_to_no_other_origin(tmp_path):
    """No CDN, no bundler, no analytics -- and nothing to exfiltrate through.

    Every fetch in `app.js` is a relative path, which is also what makes the
    "no build step" constraint hold: there is nothing to install.
    """
    with running(out_dir=tmp_path, roots=(tmp_path,),
                 palette_factory=palette_factory) as (_, port):
        for path in ("/", "/app.css", "/app.js"):
            _, _, body = call(port, path)
            text = body.decode("utf-8")
            assert "http://" not in text, path
            assert "https://" not in text, path
            assert "anthropic" not in text.lower(), path


def test_the_page_is_served_and_names_the_endpoints_it_uses(tmp_path):
    """A smoke test: the assets exist, and the page reaches the real routes."""
    with running(out_dir=tmp_path, roots=(tmp_path,),
                 palette_factory=palette_factory) as (_, port):
        status, headers, html = call(port, "/")
        assert status == 200
        assert headers["Content-Type"].startswith("text/html")
        assert b"<form" in html and b"app.js" in html

        _, _, js = call(port, "/app.js")
        for route in ("/api/options", "/api/sources", "/api/build",
                      "/api/files/"):
            assert route.encode() in js, route

        status, data = call_json(port, "/api/nope")
        assert status == 404 and "no such endpoint" in data["error"]


# -- the paste screen ---------------------------------------------------------
#
# Nothing below runs PowerShell, launches TaleSpire or takes a screenshot. The
# probes are `run=` stubs and the run itself is a `popen=` stub whose stdout is
# a list of the lines `review.ps1 tiled` really prints. That is not only
# hygiene: it means the refusal rules can be exercised for readings -- a raised
# build plane, an unreadable one -- that are awkward to arrange on a real
# machine and are exactly the ones that must not slip through.

#: What `ts.ps1 client` prints when the game is up, as this asks for it.
CLIENT_JSON = '{"X":0,"Y":31,"W":1920,"H":1017,"CX":960,"CY":539}'

#: `Get-TS`'s own sentence, on stderr, with a non-zero exit.
NOT_RUNNING = "TaleSpire is not running."

PLANE_OFF = "build plane off (rgb(71,71,71))"
PLANE_ON = ("build plane ON  (rgb(173,117,73)) -- a paste will snap to it, "
            "not to the ground")
PLANE_UNKNOWN = ("build plane UNKNOWN -- the build toolbar is not on screen "
                 "(strip rgb(177,176,69), glyph px 0). Press B for build mode, "
                 "then read it again. Do NOT paste on this reading.")


def probes(*, client=(0, CLIENT_JSON, ""), plane=(0, PLANE_OFF, "")):
    """A `subprocess.run` stand-in for the two `ts.ps1` probes.

    Keyed on which probe it is rather than on argv order, so a change to the
    flags does not silently make every test answer the same probe twice.
    """
    calls = []

    def run(command, **kwargs):
        command = list(command)
        calls.append(command)
        code, out, err = plane if command[-1] == "planestate" else client
        return types.SimpleNamespace(returncode=code, stdout=out, stderr=err)

    run.calls = calls
    return run


def spawns(lines=(), code=0):
    """A `subprocess.Popen` stand-in. Records the argv it was handed."""
    calls = []

    def popen(command, **kwargs):
        calls.append((list(command), kwargs))
        return types.SimpleNamespace(stdout=iter(list(lines)),
                                     wait=lambda: code)

    popen.calls = calls
    return popen


def driver(tmp_path, *, windows=True, run=None, popen=None):
    """A `pastedrive.Driver` with every seam plugged.

    ``tools`` stays the real directory, because the scripts existing is one of
    the preconditions and pointing it at a fake would test the fake.
    """
    if run is None:
        run = probes()
    if popen is None:
        popen = spawns()
    shots = pathlib.Path(tmp_path) / "flyby"
    shots.mkdir(exist_ok=True)
    return pastedrive.Driver(windows=windows, host="pwsh-stub", run=run,
                             popen=popen, shots=shots)


def plant_plan(out_dir, stem="pin", names=("a.slab.txt", "b.slab.txt",
                                           "anchor.slab.txt")):
    """A build's output: the slabs, and the manifest that orders them.

    The names are deliberately NOT in alphabetical order -- `anchor` sorts
    first and is written last, which is the whole point of the manifest.
    """
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        (out_dir / name).write_text("slab", encoding="utf-8")
    (out_dir / f"{stem}-paste-order.txt").write_text(
        "\n".join(names) + "\n", encoding="utf-8")
    return list(names)


def start_paste(port, **fields) -> dict:
    """Start a paste and poll it, the way the page does."""
    status, started = call_json(port, "/api/paste", method="POST", body=fields)
    assert status == 202, started
    deadline = time.monotonic() + TIMEOUT
    after, seen = -1, []
    while time.monotonic() < deadline:
        status, snapshot = call_json(
            port, f"/api/paste/{started['job']}?after={after}")
        assert status == 200, snapshot
        seen += snapshot["events"]
        after = snapshot["next"] - 1
        if snapshot["state"] != "running":
            snapshot["events"] = seen
            return snapshot
        time.sleep(0.02)
    raise AssertionError("the paste job did not finish")


# -- refusing ------------------------------------------------------------------

def test_the_paste_endpoint_refuses_when_talespire_is_not_running(tmp_path):
    """The first precondition, and the cheapest one to get wrong.

    `ts.ps1 client` throws "TaleSpire is not running." through `Get-TS`, so a
    non-zero exit is the answer rather than an error. What matters is what
    happens next: the job stops with that sentence and **nothing is ever
    spawned**, because the alternative is `review.ps1` opening with `newboard`
    against a window that is not there.

    The build plane comes back `skipped` rather than `fail`. It was not looked
    at, and a check reported as failed when it was never run is the same class
    of lie as a probe that answers without seeing anything.
    """
    out_dir = tmp_path / "uiout"
    plant_plan(out_dir)
    popen = spawns()
    run = probes(client=(1, "", NOT_RUNNING))

    with running(out_dir=out_dir, roots=(tmp_path,),
                 palette_factory=palette_factory,
                 paste_driver=driver(tmp_path, run=run, popen=popen)) as (_, port):
        _, pre = call_json(port, "/api/paste/preflight", method="POST", body={})
        snapshot = start_paste(port, stem="pin")

    assert pre["ok"] is False
    states = {c["name"]: c["state"] for c in pre["checks"]}
    assert states["TaleSpire"] == "fail"
    assert states["build plane"] == "skipped"
    assert NOT_RUNNING in [c for c in pre["checks"]
                           if c["name"] == "TaleSpire"][0]["raw"]
    assert "not running" in pre["refusal"]

    assert snapshot["state"] == "error"
    assert "not running" in snapshot["error"]
    assert popen.calls == [], "a run was started against a window that is gone"
    # A sentence for a person, not a class name and a stack.
    assert not snapshot["error"].startswith("PasteRefused")
    # And the plane was never probed, because probing it would have reported
    # "cannot see the toolbar" for a reason that has nothing to do with it.
    assert [c[-1] for c in run.calls].count("planestate") == 0


@pytest.mark.parametrize("said,why", [
    (PLANE_ON, "raised"),
    (PLANE_UNKNOWN, "unreadable"),
    ("build plane sideways", "unrecognised"),
    ("", "silent"),
])
def test_an_unreadable_build_plane_refuses_exactly_like_a_raised_one(
        said, why, tmp_path):
    """The crux, and the mistake this project has already made once.

    `CLAUDE.md`: ``-match 'ON'`` does not match ``UNKNOWN``, so a "not ON means
    it must be off" test reads a probe that saw nothing as a probe that saw an
    empty toolbar -- and the UNKNOWN branch's own message ends "Do NOT paste on
    this reading." Only an explicit ``off`` may start a run. Anything else,
    including output this does not recognise at all, refuses.

    Refusing costs a keystroke. Not refusing costs a 102-chunk run in which
    every chunk lands a course high with nothing wrong in any file.
    """
    out_dir = tmp_path / "uiout"
    plant_plan(out_dir)
    popen = spawns()

    with running(out_dir=out_dir, roots=(tmp_path,),
                 palette_factory=palette_factory,
                 paste_driver=driver(tmp_path, popen=popen,
                                     run=probes(plane=(0, said, "")))
                 ) as (_, port):
        _, pre = call_json(port, "/api/paste/preflight", method="POST", body={})
        snapshot = start_paste(port, stem="pin")

    assert pre["ok"] is False, why
    plane = [c for c in pre["checks"] if c["name"] == "build plane"][0]
    assert plane["state"] == "fail", why
    assert snapshot["state"] == "error", why
    assert popen.calls == [], f"a {why} reading started a run"


def test_an_explicit_off_is_the_only_reading_that_proceeds(tmp_path):
    """The other half of the rule: with a real ``off``, it goes."""
    out_dir = tmp_path / "uiout"
    plant_plan(out_dir)
    popen = spawns(["1/3 : a.slab.txt", "2/3 : b.slab.txt",
                    "3/3 : anchor.slab.txt"])

    with running(out_dir=out_dir, roots=(tmp_path,),
                 palette_factory=palette_factory,
                 paste_driver=driver(tmp_path, popen=popen)) as (_, port):
        _, pre = call_json(port, "/api/paste/preflight", method="POST", body={})
        snapshot = start_paste(port, stem="pin")

    assert pre["ok"] is True
    assert [c["state"] for c in pre["checks"]] == ["ok"] * len(pre["checks"])
    assert pre["client"] == {"X": 0, "Y": 31, "W": 1920, "H": 1017,
                             "CX": 960, "CY": 539}
    assert snapshot["state"] == "done", snapshot["error"]
    assert len(popen.calls) == 1


def test_the_preconditions_are_checked_again_on_the_worker(tmp_path):
    """The button's answer can be minutes old, and ``G`` is one keystroke.

    So the run re-checks before it spawns anything, rather than trusting the
    reading the page is showing. Here the plane comes up between the two.
    """
    out_dir = tmp_path / "uiout"
    plant_plan(out_dir)
    popen = spawns()
    readings = [PLANE_OFF, PLANE_ON]

    def run(command, **kwargs):
        command = list(command)
        if command[-1] == "planestate":
            said = readings.pop(0) if len(readings) > 1 else readings[0]
            return types.SimpleNamespace(returncode=0, stdout=said, stderr="")
        return types.SimpleNamespace(returncode=0, stdout=CLIENT_JSON, stderr="")

    with running(out_dir=out_dir, roots=(tmp_path,),
                 palette_factory=palette_factory,
                 paste_driver=driver(tmp_path, run=run, popen=popen)) as (_, port):
        _, pre = call_json(port, "/api/paste/preflight", method="POST", body={})
        assert pre["ok"] is True
        snapshot = start_paste(port, stem="pin")

    assert snapshot["state"] == "error"
    assert "build plane is UP" in snapshot["error"]
    assert popen.calls == []


def test_a_run_is_refused_when_a_slab_in_the_manifest_is_not_on_disk(tmp_path):
    """An unpasted chunk is not a gap in the map, it is bare board.

    So a manifest naming a file that is gone stops the run rather than pasting
    the rest of the town around a hole.
    """
    out_dir = tmp_path / "uiout"
    plant_plan(out_dir)
    (out_dir / "b.slab.txt").unlink()
    popen = spawns()

    with running(out_dir=out_dir, roots=(tmp_path,),
                 palette_factory=palette_factory,
                 paste_driver=driver(tmp_path, popen=popen)) as (_, port):
        snapshot = start_paste(port, stem="pin")

    assert snapshot["state"] == "error"
    assert "b.slab.txt" in snapshot["error"]
    assert popen.calls == []


# -- the run -------------------------------------------------------------------

def test_a_paste_run_reports_each_chunk_with_the_grabs_taken_for_it(tmp_path):
    """"Which chunk is down, and the grab after each."

    `review.ps1` prints its grabs BEFORE the progress line of the chunk they
    belong to, so they are held and flushed with it -- otherwise every row on
    the page carries the previous chunk's picture, which is worse than none.
    And a grab is reported only if it is **on disk**: `grab.ps1` says what it
    wrote, this looks, because a picture the page then cannot fetch is this
    project's own recurring failure in miniature.
    """
    out_dir = tmp_path / "uiout"
    names = plant_plan(out_dir)
    shots = tmp_path / "flyby"
    shots.mkdir(exist_ok=True)
    for view in ("001-hold", "001-down", "003-down"):
        (shots / f"pin-{view}.jpg").write_bytes(b"\xff\xd8jpeg")

    popen = spawns([
        "pin-001-hold -> 152341 bytes",
        "pin-001-down -> 150122 bytes",
        "1/3 : a.slab.txt",
        "2/3 : b.slab.txt",                      # thinned out, no grabs
        "pin-003-down -> 151044 bytes",
        "pin-003-never-written -> 9 bytes",      # said, but not on disk
        "3/3 : anchor.slab.txt",
        "tiled 3 chunk(s) of pin at 960,539",
    ])

    with running(out_dir=out_dir, roots=(tmp_path,),
                 palette_factory=palette_factory,
                 paste_driver=driver(tmp_path, popen=popen)) as (_, port):
        snapshot = start_paste(port, stem="pin", name="pin")

    assert snapshot["state"] == "done", snapshot["error"]
    chunks = [e for e in snapshot["events"] if e["stage"] == "chunk"]
    assert [c["file"] for c in chunks] == names
    assert [c["index"] for c in chunks] == [1, 2, 3]
    assert [s["name"] for s in chunks[0]["shots"]] == ["pin-001-hold.jpg",
                                                       "pin-001-down.jpg"]
    assert chunks[1]["shots"] == []
    assert [s["name"] for s in chunks[2]["shots"]] == ["pin-003-down.jpg"]
    assert chunks[0]["shots"][0]["view"] == "hold"

    # The plan event carries the manifest, in the manifest's order.
    plan = [e for e in snapshot["events"] if e["stage"] == "plan"][0]
    assert plan["files"] == names
    assert plan["files"] != sorted(plan["files"]), "the fixture stopped testing"


def test_a_failing_run_is_reported_rather_than_retried(tmp_path):
    """A second attempt stamps a second copy of the map, so nothing retries."""
    out_dir = tmp_path / "uiout"
    plant_plan(out_dir)
    popen = spawns(["1/3 : a.slab.txt", "review.ps1 : no chunk b.slab.txt"],
                   code=1)

    with running(out_dir=out_dir, roots=(tmp_path,),
                 palette_factory=palette_factory,
                 paste_driver=driver(tmp_path, popen=popen)) as (_, port):
        snapshot = start_paste(port, stem="pin")

    assert snapshot["state"] == "error"
    assert "after 1 chunk(s)" in snapshot["error"]
    assert len(popen.calls) == 1
    # Its own output is on the log, so the reason is on the page.
    assert any("no chunk b.slab.txt" in e["text"] for e in snapshot["events"])


def test_a_grab_is_served_from_its_own_directory_and_nothing_else_is(tmp_path):
    """The grabs are not under ``out_dir``, so they are not `api_file`'s.

    `grab.ps1` writes to ``out/flyby`` beside the repository whatever
    ``--out-dir`` says. Widening the file endpoint's allowlist to reach them
    would let a request ask the output directory for kinds of file it has no
    business handing out, so they get their own root and their own set.
    """
    out_dir = tmp_path / "uiout"
    out_dir.mkdir()
    shots = tmp_path / "flyby"
    shots.mkdir()
    (shots / "pin-001-down.jpg").write_bytes(b"\xff\xd8jpeg")
    (shots / "pin-paste-order.txt").write_text("x", encoding="utf-8")
    (shots / "notes.exe").write_bytes(b"MZ")
    (tmp_path / "secret.txt").write_text("not yours", encoding="utf-8")

    with running(out_dir=out_dir, roots=(tmp_path,),
                 palette_factory=palette_factory,
                 paste_driver=driver(tmp_path)) as (_, port):
        status, headers, body = call(port, "/api/paste/shots/pin-001-down.jpg")
        assert status == 200 and body == b"\xff\xd8jpeg"
        assert headers["Content-Type"] == "image/jpeg"

        for escape in ("/api/paste/shots/../secret.txt",
                       "/api/paste/shots/..%2Fsecret.txt",
                       "/api/paste/shots/a/../../secret.txt",
                       "/api/paste/shots//etc/passwd",
                       "/api/paste/shots/C:%5CWindows%5Cwin.ini",
                       "/api/paste/shots/notes.exe",
                       "/api/paste/shots/pin-paste-order.txt"):
            status, _, body = call(port, escape)
            assert status in (400, 403, 404), escape
            assert b"not yours" not in body and b"MZ" not in body, escape


def test_a_paste_and_a_build_do_not_run_at_once(tmp_path):
    """One job slot across both, and the refusal says which is in the way.

    A build during a paste rewrites the very slabs being pasted, one chunk at a
    time -- the same damage two builds do to each other, arriving more slowly.
    """
    out_dir = tmp_path / "uiout"
    plant_plan(out_dir)
    test_pipeline._layout(tmp_path)
    gate = threading.Event()

    def popen(command, **kwargs):
        gate.wait(timeout=30)
        return types.SimpleNamespace(stdout=iter([]), wait=lambda: 0)

    with running(out_dir=out_dir, roots=(tmp_path,),
                 palette_factory=palette_factory,
                 paste_driver=driver(tmp_path, popen=popen)) as (_, port):
        status, started = call_json(port, "/api/paste", method="POST",
                                    body={"stem": "pin"})
        assert status == 202
        try:
            # Wait for the worker to be inside the stub rather than racing it.
            for _ in range(500):
                _, snapshot = call_json(port, f"/api/paste/{started['job']}")
                if any(e["stage"] == "running" for e in snapshot["events"]):
                    break
                time.sleep(0.02)
            status, refused = call_json(port, "/api/build", method="POST",
                                        body={"source": only_source(port)})
            assert status == 409
            assert "A paste is running" in refused["error"]
        finally:
            gate.set()


# -- windows only --------------------------------------------------------------

def test_the_paste_screen_reports_the_platform_rather_than_offering_a_button(
        tmp_path):
    """Off Windows there is no control, and the server is what says so.

    Generating and building a town is pure Python and runs anywhere; this half
    is PowerShell over Win32. A disabled button reads as a broken feature, so
    the page renders the sentence instead -- and the endpoints refuse there
    too, because hiding a control is a UI decision and this is the rule.
    """
    out_dir = tmp_path / "uiout"
    plant_plan(out_dir)
    popen = spawns()

    with running(out_dir=out_dir, roots=(tmp_path,),
                 palette_factory=palette_factory,
                 paste_driver=driver(tmp_path, windows=False, popen=popen)
                 ) as (_, port):
        _, options = call_json(port, "/api/options")
        assert options["paste"]["available"] is False
        assert "Windows only" in options["paste"]["note"]

        status, data = call_json(port, "/api/paste", method="POST",
                                 body={"stem": "pin"})
        assert status == 400 and "Windows only" in data["error"]
        status, data = call_json(port, "/api/paste/preflight", method="POST",
                                 body={})
        assert status == 400 and "Windows only" in data["error"]

    assert popen.calls == []

    # And on Windows the same call offers the control.
    with running(out_dir=out_dir, roots=(tmp_path,),
                 palette_factory=palette_factory,
                 paste_driver=driver(tmp_path)) as (_, port):
        _, options = call_json(port, "/api/options")
        assert options["paste"]["available"] is True
        assert options["paste"]["note"] == ""


# -- safety: named operations, on this side too --------------------------------

def test_the_paste_endpoint_takes_no_command_string(tmp_path):
    """Same rule as the build form, and it matters more here.

    These values become arguments to a PowerShell script, so "typed parameters,
    unknown keys are an error" is the difference between an API and a way to
    run things. There is no field for a recipe, a flag or a path.
    """
    out_dir = tmp_path / "uiout"
    plant_plan(out_dir)

    with running(out_dir=out_dir, roots=(tmp_path,),
                 palette_factory=palette_factory,
                 paste_driver=driver(tmp_path)) as (_, port):
        for smuggled in ({"recipe": "tiled"}, {"cmd": "review.ps1"},
                         {"args": ["-Recipe", "tiled"]}, {"out_dir": "/etc"},
                         {"slab": "x.slab.txt"}, {"shell": True}):
            status, data = call_json(port, "/api/paste", method="POST",
                                     body={"stem": "pin", **smuggled})
            assert status == 400, smuggled
            assert "unknown field" in data["error"]
            assert list(smuggled)[0] in data["error"]

        bad = [
            ({"stem": "../../etc/passwd"}, "stem"),
            ({"stem": "pin; rm -rf /"}, "stem"),
            ({"stem": "notabuild"}, "stem"),
            ({"stem": 3}, "stem"),
            ({"stem": "pin", "name": "../escape"}, "name"),
            ({"stem": "pin", "name": "$(whoami)"}, "name"),
            ({"stem": "pin", "name": 7}, "name"),
            ({"stem": "pin", "shot_every": "2"}, "shot_every"),
            ({"stem": "pin", "shot_every": 0}, "shot_every"),
            ({"stem": "pin", "shot_every": True}, "shot_every"),
        ]
        for field, name in bad:
            status, data = call_json(port, "/api/paste", method="POST",
                                     body=field)
            assert status == 400, field
            assert name in data["error"], (field, data)

        # And a cross-origin form post cannot reach the parser at all.
        status, data = call_json(
            port, "/api/paste", method="POST", body={"stem": "pin"},
            content_type="application/x-www-form-urlencoded")
        assert status == 400 and "application/json" in data["error"]


def test_the_paste_driver_never_composes_a_shell_command(tmp_path):
    """Argument lists, and nothing from a request inside a command line.

    Two claims, and the second is the one worth a test. The module never asks
    for a shell. And every value that came from a request is its own argv entry
    behind its own switch: there is no string for a stem or a name to be quoted
    into, so there is nothing to quote it wrongly.

    `uiserver.py` keeps its own guard -- `test_nothing_in_the_server_can_reach_a_shell`
    -- which is why this work is in a module of its own rather than a
    convenient import there.
    """
    source = pathlib.Path(pastedrive.__file__).read_text(encoding="utf-8")
    body = source.split('"""', 2)[-1]
    # Not `pty.`, which `uiserver`'s copy of this list can afford and this one
    # cannot: "is empty." contains it. A blunt check that fires on prose is a
    # check somebody deletes.
    for forbidden in ("shell=True", "os.system", "os.popen", "os.exec",
                      "eval(", "exec(", "__import__", "import pty",
                      "commands.getoutput"):
        assert forbidden not in body, forbidden

    out_dir = tmp_path / "uiout"
    plant_plan(out_dir)
    popen = spawns(["1/3 : a.slab.txt"])
    run = probes()

    with running(out_dir=out_dir, roots=(tmp_path,),
                 palette_factory=palette_factory,
                 paste_driver=driver(tmp_path, run=run, popen=popen)) as (_, port):
        assert start_paste(port, stem="pin", name="grab_01",
                           shot_every=5)["state"] == "done"

    command, kwargs = popen.calls[0]
    assert isinstance(command, list)
    assert all(isinstance(part, str) for part in command)
    assert kwargs.get("shell") in (None, False)
    for switch, value in (("-Recipe", "tiled"), ("-Name", "grab_01"),
                          ("-Stem", "pin"), ("-ShotEvery", "5")):
        assert command[command.index(switch) + 1] == value, switch
    # Nothing request-shaped is ever inside a -Command string. The one
    # -Command in the module carries a path built from __file__ and no more.
    inlines = [c[c.index("-Command") + 1] for c in run.calls if "-Command" in c]
    assert inlines, "the window probe stopped being made"
    for inline in inlines:
        assert "ts.ps1" in inline and "review.ps1" not in inline
        for smuggled in ("grab_01", "-Recipe", "-Stem"):
            assert smuggled not in inline, smuggled


def test_the_manifest_order_is_the_paste_order_and_nothing_sorts_it(tmp_path):
    """A unit test, with names alphabetising into the wrong order.

    The chunk covering the anchor cell is written LAST so the anchor is still
    bare board for every paste before it. ``anchor.slab.txt`` sorts first;
    reading it first is a quarter of a map standing a course proud.
    """
    out_dir = tmp_path / "out"
    names = plant_plan(out_dir)
    assert pastedrive.read_paste_order(out_dir, "pin") == names
    assert names != sorted(names)

    plans = pastedrive.scan_plans(out_dir)
    assert [p["stem"] for p in plans] == ["pin"]
    assert [f["file"] for f in plans[0]["files"]] == names
    assert plans[0]["missing"] == [] and plans[0]["chunks"] == 3

    (out_dir / "b.slab.txt").unlink()
    assert pastedrive.scan_plans(out_dir)[0]["missing"] == ["b.slab.txt"]
    with pytest.raises(pastedrive.PasteRefused, match="b.slab.txt"):
        pastedrive.read_paste_order(out_dir, "pin")
    with pytest.raises(pastedrive.PasteRefused, match="paste-order"):
        pastedrive.read_paste_order(out_dir, "nosuch")


@pytest.mark.parametrize("said,state", [
    ("build plane off (rgb(71,71,71))", "off"),
    ("  build plane off\n", "off"),
    (PLANE_ON, "on"),
    (PLANE_UNKNOWN, "unknown"),
    ("build plane on", "unreadable"),
    ("BUILD PLANE OFF", "unreadable"),
    ("", "unreadable"),
    (None, "unreadable"),
    (42, "unreadable"),
])
def test_reading_the_build_plane_recognises_off_and_refuses_everything_else(
        said, state):
    """The classifier on its own, including the readings nobody plans for.

    ``build plane on`` in lower case is not the script's message, so it is
    unreadable rather than "on" -- and unreadable refuses, which is the safe
    direction. Only the exact prefix `ts.ps1` prints for a plane that is DOWN
    lets a run start.
    """
    assert pastedrive.read_plane_state(said) == state
    assert state in pastedrive.PLANE_STATES


def test_an_empty_precondition_set_is_not_a_pass():
    """"Everything checked out" from nothing having been checked.

    Same failure as a probe that answers without seeing the toolbar, one level
    up: `all([])` is True, and a preflight that ran no probes would sail
    through it.
    """
    assert pastedrive.Preflight().ok is False
    assert "nothing was checked" in pastedrive.Preflight().refusal()
    skipped = pastedrive.Preflight(
        checks=(pastedrive.Check("build plane", None, "not checked"),))
    assert skipped.ok is False
    ran = pastedrive.Preflight(
        checks=(pastedrive.Check("build plane", True, "down"),))
    assert ran.ok is True and ran.refusal() == ""


# -- the page ------------------------------------------------------------------

def test_the_page_separates_building_from_verifying(tmp_path):
    """A build that wrote its slabs succeeded, whatever verify then found.

    Forest Church produces FAIL findings -- prop overlaps, a floating fringe,
    both tracked -- and the header used to read BUILD FAILED over four slabs
    that were written and are perfectly pasteable. That sends a reader looking
    for output that is already on disk. The findings themselves are not
    softened: they stay at FAIL, first in the list and counted on the chip.
    """
    with running(out_dir=tmp_path, roots=(tmp_path,),
                 palette_factory=palette_factory) as (_, port):
        _, _, raw = call(port, "/app.js")
        _, _, html = call(port, "/")
    # Comments stripped, because the file explains at length what it stopped
    # saying and the words it stopped saying are in that explanation.
    js = re.sub(r"/\*.*?\*/", "", raw.decode("utf-8"), flags=re.S)

    assert "BUILD FAILED" not in js
    assert "VERIFY: FAULTS FOUND" in js
    assert "VERIFY: CLEAN" in js
    # The failure verdict that remains is for a job that produced nothing.
    assert "BUILD STOPPED" in js
    # Two rows in the panel, not one.
    assert b'id="verify-row"' in html and b'id="verdict-word"' in html


def test_the_ok_findings_start_collapsed_and_nothing_else_does(tmp_path):
    """Twenty findings, fifteen of them passes, buries the five that are not.

    So `ok` starts collapsed -- collapsed, not dropped: the row is in the DOM,
    the chip carries the count, one click has them back. `fail` and `warn` are
    never collapsed, and the rule is one named set so it cannot drift into
    them.
    """
    with running(out_dir=tmp_path, roots=(tmp_path,),
                 palette_factory=palette_factory) as (_, port):
        _, _, raw = call(port, "/app.js")
        _, _, html = call(port, "/")
    js = raw.decode("utf-8")

    declaration = re.search(r"const COLLAPSED = new Set\(\[([^\]]*)\]\);", js)
    assert declaration, "the collapse rule is no longer one named set"
    collapsed = declaration.group(1)
    assert '"pass"' in collapsed
    assert "fail" not in collapsed and "warn" not in collapsed
    # Collapsed by class, so the row is still rendered and still findable.
    assert 'row.classList.add("is-hidden")' in js
    assert b"starts collapsed" in html


def test_the_page_names_the_paste_endpoints_and_the_tab(tmp_path):
    """The seam the build screen left: one tab, one section, one set of rows."""
    with running(out_dir=tmp_path, roots=(tmp_path,),
                 palette_factory=palette_factory) as (_, port):
        _, _, html = call(port, "/")
        _, _, js = call(port, "/app.js")

    assert b'data-screen="paste"' in html
    assert b'id="screen-paste"' in html
    for route in ("/api/paste/plans", "/api/paste/preflight", "/api/paste",
                  "/api/paste/shots/"):
        assert route.encode() in js, route


def test_the_sidecar_serves_its_own_page(tmp_path):
    """Everything the page needs comes from the sidecar, and nothing else.

    This is the claim the on-screen review rests on. The UI is meant to sit
    open on a second monitor beside the game, and the core is offline by
    policy -- so a page that pulls a font, a stylesheet or a script from a CDN
    is one that renders differently, or not at all, exactly when the machine
    is off the network. `CLAUDE.md`'s rule is that every AI feature is
    additive; the same applies to the page's own assets.

    Three things are checked, and each is a way the page could stop being
    self-sufficient without anybody noticing on a developer's machine:

    * every asset it references is one the server itself routes,
    * both colour schemes are defined, so it is legible whatever the OS is
      set to rather than only in whichever one the author happened to use,
    * nothing is pinned to a fixed pixel width, because a second monitor is
      as likely to be portrait as landscape.

    Measured contrast on the served palette, worst visible element: 6.86:1
    light and 5.54:1 dark, against WCAG AA's 4.5 for body text.
    """
    with running(out_dir=tmp_path) as (_server, port):
        status, headers, raw = call(port, "/")
        assert status == 200
        assert "text/html" in headers["Content-Type"]
        html = raw.decode("utf-8")

        css_status, _, css_raw = call(port, "/app.css")
        js_status, _, _ = call(port, "/app.js")
        assert css_status == 200 and js_status == 200
        sheet = css_raw.decode("utf-8")

    # Every src/href the page names is served by us: a relative path, never an
    # origin. `//cdn...` is protocol-relative and would leave the machine.
    refs = re.findall(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", html)
    external = [r for r in refs
                if r.startswith(("http://", "https://", "//"))]
    assert external == [], f"the page reaches off-box for {external}"

    # Same for the stylesheet: an @import or a url() to another origin.
    assert not re.search(r"""@import|url\(\s*["']?(?:https?:)?//""", sheet), \
        "app.css pulls something from another origin"

    # Legible in both schemes, not just the author's.
    assert ":root" in sheet
    assert "prefers-color-scheme: dark" in sheet, \
        "no dark palette: the page is legible only in light mode"

    # A fixed width breaks the portrait half of 'second monitor'. max-width is
    # fine -- it caps a column; width in px on a layout element does not.
    fixed = re.findall(r"\n\s*width:\s*(\d{3,})px", sheet)
    assert fixed == [], f"fixed pixel widths would not reflow: {fixed}"
