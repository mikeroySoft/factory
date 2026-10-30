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
