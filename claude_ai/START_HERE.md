# START HERE — fly the drone from zero to flight

Complete guide: powered-off drone → live cockpit → calibrated → hovering → landed.
Internet (via phone tether) keeps working the whole time. New to the protocol?
See [README.md](README.md) and [reference/TECHNICAL.md](reference/TECHNICAL.md).

> ## ⚠️ THE ONE RULE: CALIBRATE THE GYRO FIRST
> Every session, with the drone **flat and still on the ground**, press
> **◎ GYRO CALIBRATE** (or **`C`**) *before* taking off. An uncalibrated gyro is
> the #1 reason a drone drifts, tips, or flips — it is **not** the controls.

---

## TL;DR

```bash
cd /home/tony/software/drone/claude_ai
./start.sh                       # connect + open cockpit
# open http://localhost:8088 → CALIBRATE on the ground → rate 1·Slow → Space to take off
```

---

## Step 1 — Power on
- Switch the drone **on**; the status LED lights/blinks.
- Wait ~10 s while it raises its WiFi hotspot (`WIFI-UFO-600849`).
- ⚠️ These toys **auto-power-off** when idle or low on battery. If things stop
  mid-session, check the drone first.

## Step 2 — (Optional) keep internet while flying
- Plug your **phone in by USB** and enable USB tethering, so the laptop has
  internet on a second interface while its WiFi card is on the drone.
- Verify: `curl -s -o /dev/null -w "%{http_code}\n" https://google.com` → `200`.

## Step 3 — Connect to the drone
```bash
python3 ufo.py scan              # (optional) see which drones are in range
python3 ufo.py connect           # auto-picks strongest; or: connect --ssid WIFI-UFO-XXXXXX
```
Retries until it's on the drone's WiFi, holds **`192.168.1.100`**, and the drone
answers `ping`. (`.100` is pinned, so a reconnect keeps it. Why it matters:
[TECHNICAL.md §5](reference/TECHNICAL.md).)

## Step 4 — Launch the cockpit
```bash
python3 webapp.py                # then open http://localhost:8088
```
You can also do Steps 3–4 in one shot with `./start.sh`.

---

## Pre-flight checklist (in the cockpit)
Open the page, **click it once** so it has keyboard focus, then confirm:

- [ ] Top bar shows **● ONLINE** and a **WiFi signal** reading.
- [ ] **Live camera** feed (it's rotated 90° by default; `⟳ Rotate` adjusts).
- [ ] Drone on a **flat, level** surface, **nose pointing away** from you, in a
      clear open space, props clear.
- [ ] **Headless OFF** for your first flights (controls relative to the nose).
- [ ] Press **◎ GYRO CALIBRATE** (or `C`) and wait ~1 s.
- [ ] Set rate to **`1 · Slow`**. Finger resting on **Backspace** (E-STOP).

---

## Step 5 — First hover (treat it as a test)
1. Press **`Space`** (or **TAKEOFF**). It pulses throttle to lift, then holds
   ~mid throttle — there is **no altitude hold**, so ease **`↓`** to stop the climb
   and manage height yourself.
2. **Watch 2–3 seconds.** A small drift is normal. Violent climb / flip / shoot-off
   → hit **Backspace** immediately (and re-calibrate before retrying).
3. **Tiny** control checks — tap, then release (it auto-centers):
   - `↓` ease down, `↑` climb a little — throttle correct?
   - `←` / `→` — yaw left, then right?
   - `W` (nose-away should creep forward), `S`, `A`, `D` — directions correct?
4. If any axis is reversed or wild, **land/E-stop** — the gyro likely needs
   another calibration on level ground.

## Step 6 — Land & shut down
- **`Space`** again (toggles to land) or the **Land** button for a controlled
  descent. Then **Disarm**, `Ctrl-C` the cockpit, switch the drone off.

---

## Controls (match the `deepseek_pro` project)

| Key | Action | Key | Action |
|---|---|---|---|
| `W`/`S` | pitch fwd/back | `↑`/`↓` | throttle up/down |
| `A`/`D` | roll left/right | `←`/`→` | yaw left/right |
| **`Space`** | **takeoff / land toggle** | **`Backspace`** | **EMERGENCY STOP** |
| `C` | **gyro calibrate** | `H` | headless · `F` flip |

**Rate profile** (top-right, matches the remote's beeps): **`1 · Slow`** /
**`2 · Normal`** / **`3 · Fast`** — start Slow, work up as you trust the trim.
Buttons mirror every key, plus **CAM 1/CAM 2**, **▣ Snap**, **● Rec**,
**⟳ Rotate**, **⤢ Zoom**.

**Safety built in:** motors are **DISARMED** until you Arm/Takeoff; releasing a
key auto-centers that stick within 0.5 s; **Backspace** (or the red ■ E-STOP)
cuts the motors instantly — the drone drops, so use it only to stop a misbehaving
flight, but always prefer it to a flyaway.

---

## Cameras
One **big live feed**. The airframe has a single video encoder, so only one camera
streams at a time — **CAM 1 · Front** / **CAM 2 · Bottom** switch which one (the
stream briefly re-inits and reconnects on its own). On some units CAM 2 shows the
front feed too — i.e. effectively one usable camera.

---

## Physical remote at the same time?
**Yes for watching, careful for control.** The FPV WiFi (camera/telemetry) is a
separate radio from the handheld remote, so you can always watch video + telemetry
on the laptop while flying with the remote. But **don't push sticks from both at
once** — the drone has no arbitration and they will fight. Recommended split:
remote = flying, laptop = video/telemetry/capture.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Drone drifts / tips / flips | **Re-calibrate the gyro** on flat ground (`C`). This is almost always it. |
| `drone hotspot not found` | Drone off/asleep — power-cycle, wait 10 s, re-run `ufo.py connect`. |
| Online but no video | `python3 ufo.py net` (re-assert `.100`); ensure no other app/phone holds the drone's single video session. |
| Video freezes / "missing packets" | Normal at range — move closer (< ~20 m line-of-sight); the stream self-restarts. |
| Controls do nothing | Click the page so the tab is **focused**; check **● ONLINE** and `bound_100` in `/state`. |
| `.100` keeps disappearing | Re-pin: `python3 ufo.py net`. The cockpit also re-adds it automatically. |
| Lost internet | Re-check the phone USB tether; the drone's own network has no internet. |

Protocol & API detail: **[reference/TECHNICAL.md](reference/TECHNICAL.md)** ·
full reverse-engineering write-up: **[FINDINGS.md](FINDINGS.md)**.
