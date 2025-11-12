# Sonos Module  
**Hourly grandfather-clock chimes** — zero queue touch, full snapshot restore.

```text
┌─ Sonos coordinator (192.168.1.164)
│   ├─ Snapshot → queue + position saved
│   ├─ Play grandfather_clock_chime_05.wav
│   └─ Restore → your playlist resumes at exact second
```

## What it does

 - Plays one perfect chime per hour
 - Never pauses TV or breaks Spotify
 - Restores volume + queue + position exactly
 - Works on any Sonos group (just point to the coordinator)

 ## config.json – Hourly chime (Mon–Sat 08:00–20:00)

 ```json
 {
  "id": "sonos-chime",
  "module": "modules.sonos",
  "trigger": {
    "cron": "0 8-20 * * mon-sat"
  },
  "kwargs": {
    "action": "chime",
    "sonos_volume": 25
  },
  "send_email": false,
  "summary": "Sonos chime (Mon–Sat hourly 08:00–20:00)"
}
```

## Trigger Cheat-Sheet

| Trigger | When it runs | Example config |
|---------|--------------|----------------|
| **cron** | Classic cron syntax | Hourly on weekdays |
| | | ```json { "cron": "0 * * * mon-fri" } ``` |
| **date** | One-shot at exact timestamp | Jan 1 2026 00:00 |
| | | ```json { "date": "2026-01-01T00:00:00" } ``` |
| **daily_time** | Same clock time(s) every day | 05:00 a.m., p.m. (Mon-Sat) |
| | | ```json { "daily_time": { "time": ["05:00", "17:00"], "day_of_week": "mon-sat" } } ``` |

#### daily_time playground
```jsonc
// Dinner time
"daily_time": { "time": "18:00", "day_of_week": "mon-thu" }

// Multiple times
"daily_time": { "time": ["05:00", "12:00", "18:00"] }
```

## .env (one-time)
```
NAS_IP=192.168.1.100          # your NAS serving /chimes/
COORDINATOR_IP=192.168.1.200  # primary speaker in the group
```

## File layout on NAS:
```
/grandfather_clock_chime_01.wav
                └_02.wav … └_12.wav
```

## How it works (30-second explainer)
```
run(**kwargs)           # → main.py
└─ run_action()         # → chime.py
   └─ run_chime()       # → snapshot + play + restore
```

1. Snapshot → queue + position saved
2. Volume → temporarily set to sonos_volume
3. Play → http://NAS_IP/grandfather_clock_chime_05.wav
4. Wait → 75 s max (longest chime)
5. Restore → queue + position + original volume

## Debug override (optional)
```
HOUR_OVERRIDE=10   # forces 10-bong chime at any time
```

