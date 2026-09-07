# Hardware Integration Guide
### UMCS 2.0 — Smart Canopy Control System

Two devices, two transports:

```
                    ┌─────────────────────────────┐
                    │  Python control logic       │
                    │  (Raspberry Pi or PC)       │
                    └──────┬───────────────┬──────┘
                           │               │
                    WiFi / HTTP      USB / RS-485
                           │               │
                   ┌───────▼──────┐  ┌─────▼────────┐
                   │ Shelly 2PM   │  │ RS-485       │
                   │ Gen4         │  │ adapter      │
                   └───────┬──────┘  └─────┬────────┘
                           │               │
                      220V motor       A/B pair
                           │               │
                   ┌───────▼──────┐  ┌─────▼────────┐
                   │ Canopy motor │  │ EC fan       │
                   │ SL45RM-50/12 │  │ LONGWELL     │
                   └──────────────┘  └──────────────┘
```

---

## Part 1 — Shelly 2PM Gen4 (canopy)

### 1.1 What it replaces

The module goes where a wall switch would go. It does not replace the motor.
It switches which of the motor's two control lines is energised.

The motor has four conductors:

| Colour | Function |
|---|---|
| Blue | Common / neutral |
| Brown | One direction |
| Black | The other direction |
| Yellow-green | Earth |

The module's two outputs go to brown and black. Its cover profile
interlocks them so both can never be live at once — energising both would
damage the motor.

**The 220 V wiring is not a software task.** Have a licensed electrician
do it. Tell them: bi-directional tubular AC motor, module in cover mode.

### 1.2 First-time setup

1. Power the module. On first boot it broadcasts its own WiFi access point.
2. Connect a laptop to that AP and open the web interface.
3. Enter the lab WiFi credentials.
4. **Assign a static IP** — or a DHCP reservation on the router. If the
   address changes, your code loses the canopy.
5. Switch the profile from `switch` to `cover`.
6. Run the calibration routine.

### 1.3 How calibration works

The module drives the canopy fully in both directions while watching power
draw. When the motor's internal limit switch cuts off, current drops
sharply, and the module records that as an end position.

This means **the motor's limit switches must already be set correctly**
before you calibrate. If they are not, ask the electrician to set them
during installation.

After calibration the module can report position as a percentage and accept
go-to-position commands.

### 1.4 The API

Gen4 uses the same Gen2+ RPC interface. Plain HTTP GET, no authentication
by default on the local network:

```
http://<ip>/rpc/Cover.Open?id=0
http://<ip>/rpc/Cover.Close?id=0
http://<ip>/rpc/Cover.Stop?id=0
http://<ip>/rpc/Cover.GoToPosition?id=0&pos=50
http://<ip>/rpc/Cover.GetStatus?id=0
```

`Cover.GetStatus` returns `state`, `current_pos`, `apower` and more. Test
any of these in a browser before writing code — if the browser works, the
code will work.

### 1.5 From Python

`canopy_driver.py` wraps all of it:

```python
from canopy_driver import CanopyDriver

canopy = CanopyDriver("192.168.0.50")
canopy.apply("OPEN")     # sends nothing if already open
```

What the wrapper adds beyond raw HTTP:

- Commands only on state change, not every cycle
- S2 4min duty limit — stops and cools down after 240 s of continuous run
- Minimum 60 s between commands
- Daily re-sync by driving fully closed, since the motor reports no
  position of its own

Test without hardware:

```
python canopy_driver.py --ip 192.168.0.50 --dry-run --test
```

---

## Part 2 — LONGWELL EC fan over RS-485

### 2.1 Confirm Modbus support first

RS-485 is only useful if the fan speaks Modbus RTU. Many LONGWELL models
offer 0-10 V and PWM only.

Ask the vendor, before buying anything else:

> Does model [model number] support Modbus RTU over RS-485?
> If yes, please provide the register map, baud rate, parity setting,
> and how the slave address is configured.

If the answer is no, drop the MAX485 and use a Shelly Dimmer 0/1-10V PM
instead, wired to the fan's 0-10 V input.

### 2.2 Two possible topologies

**Option A — USB RS-485 adapter (recommended)**

```
Raspberry Pi / PC --USB-- RS-485 adapter --A/B-- fan
```

No microcontroller. The Python code talks to a serial port directly. Fewer
parts, fewer failure modes.

**Option B — ESP32 with MAX485**

```
Pi / PC --WiFi-- ESP32 --MAX485-- A/B -- fan
```

Only worth it if the control logic itself runs on the ESP32. If Python runs
elsewhere, this adds a network hop for no gain.

If you do use MAX485:

| MAX485 pin | Connects to |
|---|---|
| RO | ESP32 RX |
| DI | ESP32 TX |
| DE + RE | One GPIO, tied together (direction control) |
| A, B | Fan's A and B, twisted pair |
| VCC, GND | 5 V and common ground |

Three practical points that cause most RS-485 problems:

- **Twisted pair for A/B.** Not ribbon cable, not separate wires.
- **120 Ω termination at both ends** of the bus, not in the middle.
- **Common ground reference** between devices. RS-485 is differential but
  still needs the grounds within a few volts of each other.

### 2.3 The register map matters

`fan_control.py` ships with placeholder register addresses:

```python
REG_SPEED = 0x0000
REG_RPM = 0x0002
REG_STATUS = 0x0003
SPEED_MAX = 1000
```

**These are placeholders.** Every vendor uses a different layout. Writing
to the wrong holding register can reconfigure the drive rather than set its
speed. Replace them with the values LONGWELL provides.

### 2.4 From Python

```
pip install pymodbus
```

```python
from fan_control import FanController, ModbusFanDriver

fans = FanController()
driver = ModbusFanDriver(port="/dev/ttyUSB0", slave=1, baudrate=9600)

stage, reason = fans.step(heat_score, canopy_state, timestamp, raining)
driver.set_stage(stage)
```

On Windows the port is `COM3` or similar; on Linux `/dev/ttyUSB0`.

Test without hardware:

```
python fan_control.py --modbus /dev/ttyUSB0 --dry-run --test
```

Test the staging logic against recorded weather, no hardware at all:

```
python fan_control.py --replay 2026-07-30
```

### 2.5 What Modbus gives you that 0-10 V does not

- Read back the **actual RPM**, so you can verify the fan is doing what it
  was told
- Read the **alarm word** for fault detection
- Several fans on one bus, individually addressed
- No voltage drop over long cable runs

That read-back is the real reason to prefer it. With 0-10 V you send a
setpoint and hope.

---

## Part 3 — Fan operating limits

`FanController` enforces three constraints from the project proposal:

| Constraint | Value | Source |
|---|---|---|
| Operating window | 11:00–17:00 | 폭염 시간대 한정 |
| Daily budget | 240 minutes | 일일 총 3~4시간 이내 |
| Minimum stage dwell | 10 minutes | avoids audible speed hunting |

Plus a cascade rule from the proposal's own sequencing — *그늘 먼저, 팬은
나중*: fans never run unless the canopy is open, and never during rain.

Verified on 30 July 2026, the hottest day in the dataset: fans start at
11:00 at Heatwave stage, stop at 15:00 when the daily budget runs out.
Two stage changes across the whole day.

---

## Part 4 — Order of work

1. Measure the canopy's full open and close travel time with a stopwatch.
   Three repetitions, averaged. Costs nothing, and sets the horizon and
   dwell parameters that are currently guesses.
2. Confirm with LONGWELL whether the fan supports Modbus RTU.
3. Install and calibrate the Shelly. Verify with a browser before writing
   any code.
4. Run `canopy_driver.py --dry-run --test`, then without `--dry-run`.
5. Wire the RS-485 bus. Confirm you can read RPM before attempting to
   write a speed.
6. Connect both drivers to the control loop.

Step 3 alone proves the whole concept: software moving a physical canopy.
Everything after that is refinement.
