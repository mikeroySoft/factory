# r3-0940 #903 ablation results (Ultron, 69b50b7d)

status: two_strikes_git-cache
finished: 2026-11-24 11:05 PST

## runs (every attempt)
```
run	arm	seconds	verdict	tests fail err load_before(1/5/15) / load_after	vm_cpu_before/after	ended
control-1-try1	control	1599	clean	2272 0 0 2.29 2.02 2.25 / 1.97 2.67 2.64	28.76/22.96	10:09
git-cache-try1	git-cache	1680	INVALID_outcome	2272 119 95 2.27 2.68 2.64 / 3.19 3.26 3.08	28.43/23.03	10:37
git-cache-try2	git-cache	1677	INVALID_outcome	2272 119 95 3.22 3.27 3.09 / 2.01 2.46 2.67	28.16/23.26	11:05
```

## credit rule (f), valid runs only
```json
{
  "seconds": {
    "control-1": 1599.2727845420013
  },
  "spread": 0.0,
  "arms": {
    "git-cache": {
      "measured": false,
      "credited": 0.0
    },
    "scratch-throttle": {
      "measured": false,
      "credited": 0.0
    },
    "ram-apfs": {
      "measured": false,
      "credited": 0.0
    }
  },
  "total_credited": 0.0,
  "verdict": "INCONCLUSIVE"
}
```

## arms saving >= 60 s (each its own fix ticket; listed, not filed)
- none

## PR #906
head (before final commit) 5ab0b36a774131d8bb4252fc1526813c7dbab23f draft=true labels=factory-held

## RAM disk receipts
```
```

## shas
```
e071ebe909c9b2c1fea62bad01545406307952ae664416029dea9361444f026b  .config/factory/config.toml
ac6661ba52ea87d7bf27c9b8d2c5c167eb7c4f73573b35b402d6e367775a7651  wt/uv.lock
0a4268b2809dc8633192f399d7838c7cdfe865726a06147e818d4c26b0f1ad2b  harness/ablation_run.py
07a766ef65a38b34e5fe2ca7b1251c925b1d875ef142a80430112c9fee18a98b  harness/arm_runner.py
181ac96df88f9ba1c8cc4cd2fad785b948b4e76da149c380a80273bd6ce3ae9d  harness/analyze.py
6cea1ccbf6db0002f2f1afeab7dd456852d36c2eaccb92b861ae34a5a688a143  harness/test_analyze.py
8c2d8f067f87181336c99a3101c2d720c8357b68036001396be331eb4c27d161  harness/gitbin/git
8e1c38cabecf7c9405fc7a4d60ec035e4f1da64a1dd0a3adf68724779335b598  harness/throttle-site/sitecustomize.py
d22f0d99cffa227855e101e646aede36135bf267f14cbfcad77df039f832d436  tmp/r3-0940/driver.sh
config_sha_after=e071ebe909c9b2c1fea62bad01545406307952ae664416029dea9361444f026b
uvlock_sha_after=ac6661ba52ea87d7bf27c9b8d2c5c167eb7c4f73573b35b402d6e367775a7651
```

pause created: 2026-11-24T09:41:57-0800; resumed: 2026-11-24T11:05:47-0800
