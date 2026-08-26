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

import pytest

import test_pipeline
from citysmith import uiserver
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
