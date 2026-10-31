"""Decision breadboard viewport and wire behavior against the browser asset."""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ASSET = Path(__file__).resolve().parents[1] / "factory" / "workspace-trace.js"


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
            for start, end in zip(points, points[1:]):
                for other_start, other_end in zip(other["points"], other["points"][1:]):
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
