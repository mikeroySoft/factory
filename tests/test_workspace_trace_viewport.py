"""Decision breadboard viewport and wire behavior against the browser asset."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
import uuid
from itertools import pairwise
from pathlib import Path

ASSET = Path(__file__).resolve().parents[1] / "factory" / "workspace-trace.js"
BROWSER = shutil.which("agent-browser")


@unittest.skipUnless(shutil.which("node"), "Node is required for trace viewport tests")
class WorkspaceTraceViewportTest(unittest.TestCase):
    def evaluate(self, expression: str, value: dict) -> object:
        script = f"""
const fs = require('fs');
global.window = {{}};
eval(fs.readFileSync(process.argv[1], 'utf8'));
const value = JSON.parse(fs.readFileSync(0, 'utf8'));
Promise.resolve({expression}).then(result => process.stdout.write(JSON.stringify(result)));
"""
        result = subprocess.run(
            ["node", "-e", script, str(ASSET)],
            input=json.dumps(value),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_each_relation_routes_from_source_to_target_with_target_marker(self) -> None:
        result = self.evaluate(
            "value.relations.map(relation => window.WorkspaceTrace.routeEdge({"
            "edge:{from:'source',to:'target',relation},"
            "source:value.source,target:value.target,fromColumn:0,toColumn:1,"
            "index:0,lane:0,bounds:value.bounds}))",
            {
                "relations": [
                    "follows",
                    "retries",
                    "blocked_by",
                    "split_from",
                    "unblocked_by",
                    "overrides",
                ],
                "source": {"left": 50, "right": 150, "top": 80, "bottom": 160},
                "target": {"left": 500, "right": 600, "top": 220, "bottom": 300},
                "bounds": {"width": 800, "height": 600},
            },
        )

        for route in result:
            self.assertEqual(route["points"][0], {"x": 150, "y": 120})
            self.assertEqual(route["points"][-1], {"x": 500, "y": 260})
            self.assertEqual(route["markerEnd"], "url(#trace-arrow)")
        self.assertEqual(
            self.evaluate("window.WorkspaceTrace.arrowOrient", {}), "auto"
        )

    def test_wide_graph_wires_stay_inside_viewport_at_default_fit(self) -> None:
        result = self.evaluate(
            "(() => {"
            "const viewport={width:960,height:520}, content={width:11940,height:420};"
            "const state=window.WorkspaceTrace.fitViewport(viewport,content);"
            "const rects=Array.from({length:40},(_,i)=>window.WorkspaceTrace.transformRect("
            "{left:i*300,right:i*300+240,top:120,bottom:220},state));"
            "const routes=rects.slice(0,-1).map((source,i)=>window.WorkspaceTrace.routeEdge({"
            "edge:{from:String(i),to:String(i+1),relation:'follows'},source,target:rects[i+1],"
            "fromColumn:i,toColumn:i+1,index:i,lane:0,bounds:viewport}));"
            "return {state,routes}; })()",
            {},
        )

        self.assertLess(result["state"]["scale"], 1)
        for route in result["routes"]:
            for point in route["points"]:
                self.assertGreaterEqual(point["x"], 0)
                self.assertLessEqual(point["x"], 960)
                self.assertGreaterEqual(point["y"], 0)
                self.assertLessEqual(point["y"], 520)

    def test_zoom_pan_and_reset_update_the_transform(self) -> None:
        result = self.evaluate(
            "(() => {"
            "const viewport={width:800,height:500},content={width:1600,height:900};"
            "const fit=window.WorkspaceTrace.fitViewport(viewport,content);"
            "const zoom=window.WorkspaceTrace.zoomViewport(fit,1.5,{x:400,y:250});"
            "const pan=window.WorkspaceTrace.panViewport(zoom,35,-20);"
            "return {fit,zoom,pan,reset:window.WorkspaceTrace.fitViewport(viewport,content),"
            "before:window.WorkspaceTrace.transformRect({left:0,right:100,top:0,bottom:100},fit),"
            "after:window.WorkspaceTrace.transformRect({left:0,right:100,top:0,bottom:100},pan)}; })()",
            {},
        )

        self.assertGreater(result["zoom"]["scale"], result["fit"]["scale"])
        self.assertEqual(result["pan"]["x"], result["zoom"]["x"] + 35)
        self.assertEqual(result["pan"]["y"], result["zoom"]["y"] - 20)
        self.assertNotEqual(result["before"], result["after"])
        self.assertEqual(result["reset"], result["fit"])

    def test_fullscreen_toggle_enters_and_exits(self) -> None:
        result = self.evaluate(
            "(async () => {"
            "const calls=[]; const board={requestFullscreen:async()=>calls.push('enter')};"
            "const doc={fullscreenElement:null,exitFullscreen:async()=>calls.push('exit')};"
            "await window.WorkspaceTrace.toggleFullscreen(board,doc);"
            "doc.fullscreenElement=board;"
            "await window.WorkspaceTrace.toggleFullscreen(board,doc);"
            "return calls; })()",
            {},
        )

        self.assertEqual(result, ["enter", "exit"])

    def test_parallel_edges_use_distinct_long_lanes(self) -> None:
        routes = self.evaluate(
            "[-1,1].map(lane => window.WorkspaceTrace.routeEdge({"
            "edge:{from:'source',to:'target',relation:'follows'},"
            "source:value.source,target:value.target,fromColumn:0,toColumn:3,"
            "index:0,lane,bounds:value.bounds}))",
            {
                "source": {"left": 50, "right": 150, "top": 80, "bottom": 160},
                "target": {"left": 700, "right": 800, "top": 220, "bottom": 300},
                "bounds": {"width": 900, "height": 500},
            },
        )

        self.assertNotEqual(routes[0]["points"][2], routes[1]["points"][2])
        self.assertNotEqual(routes[0]["points"][3], routes[1]["points"][3])

    def test_adjacent_stage_lanes_keep_the_target_segment_forward_at_low_scale(self) -> None:
        routes = self.evaluate(
            "[-1,0,1].map(lane => window.WorkspaceTrace.routeEdge({"
            "edge:{from:'source',to:'target',relation:'follows'},"
            "source:value.source,target:value.target,fromColumn:0,toColumn:1,"
            "index:0,lane,bounds:value.bounds}))",
            {
                "source": {"left": 120, "right": 150, "top": 80, "bottom": 100},
                "target": {"left": 160.44, "right": 190, "top": 120, "bottom": 140},
                "bounds": {"width": 320, "height": 200},
            },
        )

        gutters = [route["points"][1]["x"] for route in routes]
        self.assertEqual(len(set(gutters)), len(gutters))
        for route in routes:
            self.assertGreater(route["points"][1]["x"], route["points"][0]["x"])
            self.assertLess(route["points"][-2]["x"], route["points"][-1]["x"])

    def test_high_density_lanes_keep_end_segments_outside_nodes(self) -> None:
        routes = self.evaluate(
            "value.routes.map(route => window.WorkspaceTrace.routeEdge({"
            "edge:{from:'source',to:'target',relation:'follows'},"
            "...route,index:0,lane:-11,bounds:value.bounds}))",
            {
                "routes": [
                    {
                        "source": {"left": 50, "right": 150, "top": 80, "bottom": 160},
                        "target": {"left": 50, "right": 150, "top": 220, "bottom": 300},
                        "fromColumn": 2,
                        "toColumn": 2,
                    },
                    {
                        "source": {"left": 700, "right": 800, "top": 80, "bottom": 160},
                        "target": {"left": 50, "right": 150, "top": 220, "bottom": 300},
                        "fromColumn": 3,
                        "toColumn": 0,
                    },
                    {
                        "source": {"left": 50, "right": 150, "top": 80, "bottom": 160},
                        "target": {"left": 700, "right": 800, "top": 220, "bottom": 300},
                        "fromColumn": 0,
                        "toColumn": 3,
                    },
                ],
                "bounds": {"width": 900, "height": 500},
            },
        )

        same_stage, backward, long_forward = routes
        self.assertGreater(same_stage["points"][1]["x"], same_stage["points"][0]["x"])
        self.assertGreater(same_stage["points"][-2]["x"], same_stage["points"][-1]["x"])
        self.assertLess(backward["points"][1]["x"], backward["points"][0]["x"])
        self.assertGreater(backward["points"][-2]["x"], backward["points"][-1]["x"])
        self.assertGreater(long_forward["points"][1]["x"], long_forward["points"][0]["x"])
        self.assertLess(long_forward["points"][-2]["x"], long_forward["points"][-1]["x"])

    def test_viewport_shortcuts_ignore_browser_modifier_keys(self) -> None:
        shortcuts = self.evaluate(
            "value.map(event => window.WorkspaceTrace.viewportShortcut({"
            "...event,target:{closest:()=>false}}))",
            [
                {"key": "f", "ctrlKey": True},
                {"key": "-", "metaKey": True},
                {"key": "0", "altKey": True},
                {"key": "f"},
            ],
        )

        self.assertEqual(shortcuts, [None, None, None, "f"])

    def test_symmetric_parallel_lanes_do_not_share_long_segments(self) -> None:
        routes = self.evaluate(
            "[-1,1].map(lane => window.WorkspaceTrace.routeEdge({"
            "edge:{from:'source',to:'target',relation:'follows'},"
            "source:value.source,target:value.target,fromColumn:0,toColumn:3,"
            "index:0,lane,bounds:value.bounds}))",
            {
                "source": {"left": 50, "right": 150, "top": 80, "bottom": 160},
                "target": {"left": 700, "right": 800, "top": 220, "bottom": 300},
                "bounds": {"width": 900, "height": 500},
            },
        )

        shared = []
        for start, end in zip(routes[0]["points"], routes[0]["points"][1:]):
            for other_start, other_end in zip(
                routes[1]["points"], routes[1]["points"][1:]
            ):
                if start["x"] == end["x"] == other_start["x"] == other_end["x"]:
                    shared.append(
                        max(
                            0,
                            min(max(start["y"], end["y"]), max(other_start["y"], other_end["y"]))
                            - max(min(start["y"], end["y"]), min(other_start["y"], other_end["y"])),
                        )
                    )
                if start["y"] == end["y"] == other_start["y"] == other_end["y"]:
                    shared.append(
                        max(
                            0,
                            min(max(start["x"], end["x"]), max(other_start["x"], other_end["x"]))
                            - max(min(start["x"], end["x"]), min(other_start["x"], other_end["x"])),
                        )
                    )

        self.assertLessEqual(max(shared, default=0), 100)

    def test_offscreen_route_keeps_target_direction_for_svg_clipping(self) -> None:
        route = self.evaluate(
            "window.WorkspaceTrace.routeEdge({"
            "edge:{from:'source',to:'target',relation:'follows'},"
            "source:value.source,target:value.target,fromColumn:0,toColumn:1,"
            "index:0,lane:0,bounds:value.bounds})",
            {
                "source": {"left": 900, "right": 1000, "top": 80, "bottom": 160},
                "target": {"left": 1100, "right": 1200, "top": 220, "bottom": 300},
                "bounds": {"width": 800, "height": 500},
            },
        )

        self.assertTrue(all(point["x"] > 800 for point in route["points"]))
        self.assertEqual(route["points"][-1], {"x": 1100, "y": 260})
        self.assertLess(route["points"][-2]["x"], route["points"][-1]["x"])

    def test_retry_bundle_tracks_and_gutters_stay_bounded_without_shared_segments(self) -> None:
        scenarios = self.evaluate(
            "(() => { const api=window.WorkspaceTrace;"
            "return value.scenarios.map(scenario => {"
            "const members=Array.from({length:23},(_,i)=>i);"
            "const spacing=api.bundleSpacing([members,members,members]);"
            "return members.map((index) => {"
            "const lane=index-(members.length-1)/2;"
            "const stripe=lane*spacing.stripe;"
            "const track=8+spacing.track/2+index*spacing.track;"
            "return api.routeEdge({...scenario,"
            "edge:{from:'source',to:'target',relation:'retries'},index,"
            "lane:lane+stripe/4,track,bounds:value.bounds}); }); }); })()",
            {
                "bounds": {"width": 900, "height": 500},
                "scenarios": [
                    {
                        "source": {"left": 740, "right": 840, "top": 80, "bottom": 160},
                        "target": {"left": 50, "right": 150, "top": 220, "bottom": 300},
                        "fromColumn": 3, "toColumn": 0,
                    },
                    {
                        "source": {"left": 50, "right": 150, "top": 80, "bottom": 160},
                        "target": {"left": 740, "right": 840, "top": 220, "bottom": 300},
                        "fromColumn": 0, "toColumn": 3,
                    },
                    {
                        "source": {"left": 798, "right": 898, "top": 80, "bottom": 160},
                        "target": {"left": 798, "right": 898, "top": 220, "bottom": 300},
                        "fromColumn": 3, "toColumn": 3,
                    },
                ],
            },
        )

        for kind, routes in zip(("backward", "long-forward", "same-stage"), scenarios):
            for index, route in enumerate(routes):
                with self.subTest(kind=kind, lane=index - 11):
                    points = route["points"]
                    for point in points:
                        self.assertGreaterEqual(point["x"], 0)
                        self.assertLessEqual(point["x"], 900)
                        self.assertGreaterEqual(point["y"], 0)
                        self.assertLessEqual(point["y"], 500)
                    self.assertEqual(route["markerEnd"], "url(#trace-arrow)")
                    self.assertEqual(points[0]["y"], 120)
                    self.assertEqual(points[-1]["y"], 260)
                    self.assertEqual(points[0]["x"], 740 if kind == "backward" else (
                        150 if kind == "long-forward" else 898
                    ))
                    self.assertEqual(points[-1]["x"], 740 if kind == "long-forward" else (
                        150 if kind == "backward" else 898
                    ))
                    if kind == "long-forward":
                        self.assertLess(points[-2]["x"], points[-1]["x"])
                    else:
                        self.assertGreater(points[-2]["x"], points[-1]["x"])
                    self.assert_shared_segments_at_most(routes[:index], points, 100)

    def assert_shared_segments_at_most(self, routes, points, limit) -> None:
        for other in routes:
            for start, end in pairwise(points):
                for other_start, other_end in pairwise(other["points"]):
                    for fixed, varying in (("x", "y"), ("y", "x")):
                        if start[fixed] == end[fixed] == other_start[fixed] == other_end[fixed]:
                            overlap = min(
                                max(start[varying], end[varying]),
                                max(other_start[varying], other_end[varying]),
                            ) - max(
                                min(start[varying], end[varying]),
                                min(other_start[varying], other_end[varying]),
                            )
                            self.assertLessEqual(overlap, limit)

    def test_spring_frames_keep_routes_bounded_and_attached_to_visible_nodes(self) -> None:
        result = self.evaluate(
            "(() => { const api=window.WorkspaceTrace;"
            "const bounds={width:900,height:500};"
            "const route=(x,y,lane,track)=>api.routeEdge({"
            "edge:{from:'source',to:'target',relation:'retries'},"
            "source:{left:x,right:x+100,top:y,bottom:y+80},"
            "target:{left:600,right:700,top:220,bottom:300},"
            "fromColumn:0,toColumn:3,lane,track,bounds}).points;"
            "const start=route(50,80,11,480),target=route(70,85,-11,9.2);"
            "return {target,frames:Array.from({length:51},(_,i)=>"
            "api.springPoints(start,target,i/50,bounds))}; })()",
            {},
        )

        for points in result["frames"]:
            self.assertEqual(points[0], result["target"][0])
            self.assertEqual(points[-1], result["target"][-1])
            self.assertEqual(points[1]["y"], points[0]["y"])
            self.assertEqual(points[-2]["y"], points[-1]["y"])
            self.assertGreater(points[1]["x"], points[0]["x"])
            self.assertLess(points[-2]["x"], points[-1]["x"])
            for point in points:
                self.assertGreaterEqual(point["x"], 0)
                self.assertLessEqual(point["x"], 900)
                self.assertGreaterEqual(point["y"], 0)
                self.assertLessEqual(point["y"], 500)

    def test_three_retry_loops_and_lane_minus_eleven_keep_top_tracks_visible(self) -> None:
        routes = self.evaluate(
            "(() => { const api=window.WorkspaceTrace;"
            "const groups=[Array(3).fill(0),Array(3).fill(0)];"
            "const spacing=api.bundleSpacing(groups); let track=8+spacing.track/2;"
            "return [...groups.flatMap(group=>group.map((_,i)=>({"
            "lane:i-1+(i-1)*spacing.stripe/4,track:track+=spacing.track"
            "}))),{lane:-2,track:9.2},{lane:-11,track:9.2}]"
            ".map(({lane,track},index)=>api.routeEdge({"
            "edge:{from:'review',to:'claim',relation:'retries'},index,lane,track,"
            "source:{left:700,right:800,top:80,bottom:160},"
            "target:{left:50,right:150,top:220,bottom:300},"
            "fromColumn:7,toColumn:3,bounds:{width:900,height:500}})); })()",
            {},
        )

        for route in routes:
            points = route["points"]
            self.assertEqual(points[0], {"x": 700, "y": 120})
            self.assertEqual(points[-1], {"x": 150, "y": 260})
            self.assertGreater(points[-2]["x"], points[-1]["x"])
            for point in points:
                self.assertGreaterEqual(point["x"], 0)
                self.assertLessEqual(point["x"], 900)
                self.assertGreaterEqual(point["y"], 0)
                self.assertLessEqual(point["y"], 500)

    def test_panned_offscreen_ports_are_not_clamped_for_tracked_routes(self) -> None:
        scenarios = self.evaluate(
            "value.map(columns=>window.WorkspaceTrace.routeEdge({"
            "edge:{from:'source',to:'target',relation:'retries'},"
            "source:{left:900,right:1000,top:80,bottom:160},"
            "target:{left:1100,right:1200,top:220,bottom:300},"
            "fromColumn:columns[0],toColumn:columns[1],lane:-11,track:9.2,"
            "bounds:{width:800,height:500}}))",
            [[3, 0], [0, 3], [3, 3]],
        )

        for kind, route in zip(("backward", "long-forward", "same-stage"), scenarios):
            with self.subTest(kind=kind):
                points = route["points"]
                self.assertTrue(all(point["x"] > 800 for point in points))
                self.assertEqual(points[0]["y"], 120)
                self.assertEqual(points[-1]["y"], 260)
                if kind == "long-forward":
                    self.assertEqual(points[-1]["x"], 1100)
                    self.assertLess(points[-2]["x"], points[-1]["x"])
                else:
                    self.assertEqual(points[-1]["x"], 1200)
                    self.assertGreater(points[-2]["x"], points[-1]["x"])


@unittest.skipUnless(BROWSER, "agent-browser is required for mounted trace tests")
class WorkspaceTraceMountedTest(unittest.TestCase):
    def setUp(self) -> None:
        self.session = f"trace-mounted-{uuid.uuid4().hex}"
        self.addCleanup(self.browser, "close")
        temporary = tempfile.TemporaryDirectory(prefix="trace-mounted-")
        self.addCleanup(temporary.cleanup)
        page = Path(temporary.name) / "trace.html"
        graph = {
            "ticket": 470,
            "nodes": [
                {"decision_id": "a", "stage": "worker_select", "chosen": "worker"},
                {"decision_id": "b", "stage": "gate", "chosen": "pass"},
                {"decision_id": "c", "stage": "review", "chosen": "REVISE"},
            ],
            "edges": [
                {"from": "a", "to": "b", "relation": "follows"},
                {"from": "b", "to": "c", "relation": "follows"},
                {"from": "c", "to": "a", "relation": "retries"},
            ],
        }
        page.write_text(
            '<!doctype html><meta charset="utf-8"><style>'
            + (ASSET.parent / "workspace.css").read_text()
            + '</style><main id="host"></main><script>'
            + ASSET.read_text()
            + "\nwindow.WorkspaceAPI = {json: async () => ("
            + json.dumps(graph)
            + ")}; WorkspaceTrace.mount(document.querySelector('#host'), {ticket:470});"
            + """
window.traceState = async () => {
  // Read the actual SVG after its layout and spring animation have settled.
  await new Promise(resolve => setTimeout(resolve, 550));
  const board = document.querySelector('[data-trace-board]');
  const canvas = document.querySelector('[data-trace-canvas]');
  const box = canvas.getBoundingClientRect();
  const edges = await WorkspaceAPI.json();
  const paths = [...document.querySelectorAll('.trace-edge')];
  const attachments = paths.every(path => {
    const edge = edges.edges[+path.closest('[data-trace-edge]').dataset.traceEdge];
    const length = path.getTotalLength();
    return [[edge.from, path.getPointAtLength(0)],
            [edge.to, path.getPointAtLength(length)]].every(([id, point]) => {
      const rect = document.querySelector(`[data-trace-node="${id}"]`).getBoundingClientRect();
      return Math.abs(point.y - ((rect.top + rect.bottom) / 2 - box.top)) < .1 &&
        Math.min(Math.abs(point.x - (rect.left - box.left)),
                 Math.abs(point.x - (rect.right - box.left))) < .1;
    });
  });
  return {
    tracing: board.classList.contains('is-tracing'),
    nodes: document.querySelectorAll('.trace-node.is-trace-active').length,
    edges: document.querySelectorAll('.trace-edge-group.is-trace-active').length,
    transform: document.querySelector('[data-trace-scene]').style.transform,
    paths: paths.map(path => path.getAttribute('d')),
    attachments,
    fullscreen: document.fullscreenElement === board,
  };
};
window.pinTrace = kind => {
  document.querySelector(kind === 'node' ? '[data-trace-select]' : '.trace-edge-hit')
    .dispatchEvent(new MouseEvent('click', {bubbles:true}));
};
window.backgroundPoint = () => {
  const box = document.querySelector('[data-trace-canvas]').getBoundingClientRect();
  return [box.left + 5, box.bottom - 5];
};
</script>"""
        )
        self.browser("open", page.as_uri())
        self.browser("set", "viewport", "1280", "800")
        self.browser("wait", ".trace-edge")

    def browser(self, *arguments: str, script: str | None = None) -> object:
        environment = {**os.environ, "AGENT_BROWSER_SESSION": self.session}
        result = subprocess.run(
            [BROWSER, "--json", *arguments],
            input=script,
            capture_output=True,
            text=True,
            env=environment,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        response = json.loads(result.stdout)
        self.assertTrue(response["success"], response)
        return response.get("data")

    def evaluate(self, script: str) -> object:
        return self.browser("eval", "--stdin", script=script)["result"]

    def state(self) -> dict:
        return self.evaluate("traceState()")

    def assert_pin(self, kind: str) -> None:
        state = self.state()
        self.assertTrue(state["tracing"], f"{kind} pin cleared by viewport interaction")
        self.assertEqual(state["nodes"], 3 if kind == "node" else 2)
        self.assertEqual(state["edges"], 3 if kind == "node" else 1)
        self.assertTrue(state["attachments"], "redrawn SVG detached from nodes")

    def test_pinned_chains_survive_mouse_and_keyboard_viewport_controls(self) -> None:
        for kind in ("node", "edge"):
            for activation in ("click", "Enter", "Space"):
                for control in ("zoom-in", "zoom-out", "fit", "fullscreen"):
                    with self.subTest(kind=kind, activation=activation, control=control):
                        self.evaluate(f"pinTrace('{kind}')")
                        selector = f"[data-trace-{control}]"
                        if activation == "click":
                            self.browser("click", selector)
                        else:
                            self.browser("focus", selector)
                            self.browser("press", activation)
                        self.assert_pin(kind)
                        if self.state()["fullscreen"]:
                            self.browser("click", "[data-trace-fullscreen]")
                            self.assert_pin(kind)

    def test_pinned_chains_survive_pan_but_background_click_and_escape_clear(self) -> None:
        for kind in ("node", "edge"):
            with self.subTest(kind=kind):
                self.evaluate(f"pinTrace('{kind}')")
                before = self.state()
                x, y = map(round, self.evaluate("backgroundPoint()"))
                self.browser("mouse", "move", str(x), str(y))
                self.browser("mouse", "down")
                self.browser("mouse", "move", str(x + 35), str(y - 20))
                self.browser("mouse", "up")
                self.assert_pin(kind)
                after = self.state()
                self.assertNotEqual(after["transform"], before["transform"])
                self.assertNotEqual(after["paths"], before["paths"])
                self.browser("mouse", "down")
                self.browser("mouse", "up")
                self.assertFalse(self.state()["tracing"], "later background click was swallowed")
                self.evaluate(f"pinTrace('{kind}')")
                self.browser("press", "Escape")
                self.assertFalse(self.state()["tracing"])

    def test_mounted_zoom_and_fullscreen_redraw_wires(self) -> None:
        before = self.state()
        self.browser("click", "[data-trace-zoom-in]")
        zoomed = self.state()
        self.assertNotEqual(zoomed["transform"], before["transform"])
        self.assertNotEqual(zoomed["paths"], before["paths"])
        self.assertTrue(zoomed["attachments"])
        self.browser("click", "[data-trace-fullscreen]")
        fullscreen = self.state()
        self.assertTrue(fullscreen["fullscreen"])
        self.assertNotEqual(fullscreen["paths"], zoomed["paths"])
        self.assertTrue(fullscreen["attachments"])
        self.browser("click", "[data-trace-fullscreen]")
        restored = self.state()
        self.assertFalse(restored["fullscreen"])
        self.assertNotEqual(restored["paths"], fullscreen["paths"])
        self.assertEqual(restored["transform"], before["transform"])
        self.assertTrue(restored["attachments"])

    def test_cancelled_or_clickless_pan_does_not_swallow_a_later_click(self) -> None:
        for ending in ("cancelled", "clickless"):
            with self.subTest(ending=ending):
                self.evaluate("""
pinTrace('node');
document.querySelector('[data-trace-board]').addEventListener('pointerdown',
  event => { window.panPointer = event.pointerId; }, {once:true});
""")
                x, y = map(round, self.evaluate("backgroundPoint()"))
                self.browser("mouse", "move", str(x), str(y))
                self.browser("mouse", "down")
                self.browser("mouse", "move", str(x + 30), str(y - 15))
                if ending == "cancelled":
                    self.evaluate("""
document.querySelector('[data-trace-board]').dispatchEvent(
  new PointerEvent('pointercancel', {pointerId:window.panPointer, bubbles:true}));
""")
                self.evaluate("""
// Simulate a gesture whose browser never delivers a click to the board.
document.addEventListener('click', event => event.stopImmediatePropagation(),
  {capture:true, once:true});
""")
                self.browser("mouse", "up")
                self.assert_pin("node")
                self.evaluate("""
document.querySelector('[data-trace-canvas]').dispatchEvent(
  new MouseEvent('click', {bubbles:true}));
""")
                self.assertFalse(self.state()["tracing"], "stale pan suppression consumed a click")
