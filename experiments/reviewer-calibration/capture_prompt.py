"""Reviewer stand-in: record the prompt argv the production contract built, nothing else.

The printed verdict only lets dispatch.review() finish its capture call; it is never
scored as a review result.
"""
import pathlib, sys

pathlib.Path(sys.argv[2]).write_text(sys.argv[1])
print("VERDICT: APPROVE")
