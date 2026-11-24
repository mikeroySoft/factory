# r3-0815 #903 ablation results (Ultron, 69b50b7d)

status: two_strikes_ram
finished: 2026-11-24 09:33 PST

## runs (every attempt)
```
run	arm	seconds	verdict	tests fail err load_before(1/5/15) / load_after	vm_cpu_before/after	ended
control-1-try1	control	1596	clean	2272 0 0 1.46 1.84 2.22 / 3.58 2.86 2.55	30.96/25.36	08:39
ram-try1	ram	1609	INVALID_outcome	2272 2 0 3.54 2.89 2.57 / 1.85 2.5 2.87	32.23/23.0	09:06
ram-try2	ram	1575	INVALID_outcome	2272 2 0 1.98 2.49 2.86 / 3.22 2.85 2.73	26.96/24.0	09:33
```

## credit rule (f), valid runs only
```json
{
  "seconds": {
    "control-1": 1595.9719835830037
  },
  "spread": 0.0,
  "arms": {
    "ram": {
      "measured": false,
      "credited": 0.0
    },
    "git-cache": {
      "measured": false,
      "credited": 0.0
    },
    "scratch-throttle": {
      "measured": false,
      "credited": 0.0
    }
  },
  "total_credited": 0.0,
  "verdict": "INCONCLUSIVE"
}
```

## RAM disk receipts
```
{"action":"attach","device":"/dev/disk18","mount":"/Volumes/RamDisk0735","time":"2026-11-24T08:39:39-0800","bytes":4294967296}
{"action":"detach","device":"/dev/disk18","mount":"/Volumes/RamDisk0735","time":"2026-11-24T09:06:38-0800","forced":false,"still_present":false}
{"action":"attach","device":"/dev/disk18","mount":"/Volumes/RamDisk0735","time":"2026-11-24T09:06:54-0800","bytes":4294967296}
{"action":"detach","device":"/dev/disk18","mount":"/Volumes/RamDisk0735","time":"2026-11-24T09:33:21-0800","forced":false,"still_present":false}
```

## shas
```
e071ebe909c9b2c1fea62bad01545406307952ae664416029dea9361444f026b  .config/factory/config.toml
ac6661ba52ea87d7bf27c9b8d2c5c167eb7c4f73573b35b402d6e367775a7651  wt/uv.lock
0a4268b2809dc8633192f399d7838c7cdfe865726a06147e818d4c26b0f1ad2b  harness/ablation_run.py
07a766ef65a38b34e5fe2ca7b1251c925b1d875ef142a80430112c9fee18a98b  harness/arm_runner.py
babdbaf8d51fb5b08cf6527e3b5490f13ea7e52de08575e3aaf225dc82dc0c1d  harness/analyze.py
3b51050d50bab22a4beebdac65c7b0eeeaa0b1cea5d144daf565e3494ad63045  harness/test_analyze.py
8c2d8f067f87181336c99a3101c2d720c8357b68036001396be331eb4c27d161  harness/gitbin/git
8e1c38cabecf7c9405fc7a4d60ec035e4f1da64a1dd0a3adf68724779335b598  harness/throttle-site/sitecustomize.py
2cc64296b677d49a68346ac5f4eff827fde49aca830017d72418fd203d1c1717  driver.sh
config_sha_after=e071ebe909c9b2c1fea62bad01545406307952ae664416029dea9361444f026b
uvlock_sha_after=ac6661ba52ea87d7bf27c9b8d2c5c167eb7c4f73573b35b402d6e367775a7651
```

pause created: 2026-11-24T08:09:46-0800; resumed: 2026-11-24T09:33:23-0800
