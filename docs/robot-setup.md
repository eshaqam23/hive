# Robot and network setup

What each car's Pi and the operator laptop need so Hive can drive and watch three ROS 2 cars at
once over Wi-Fi. Everything here is environment and firewall configuration. No robot code changes.

Placeholders used below (replace them; do not commit real addresses):
`<laptop-ip>`, `<camera-car-ip>`, `<lidar-car-ip>`, `<gripper-car-ip>`.

## Domains and ports

Each car gets its own `ROS_DOMAIN_ID`, so cars that reuse topic names (`/cmd_vel`) never hear each
other. DDS ports move with the domain: `7400 + 250 * domain`, using up to 250 ports from that base.

| Car | Domain | UDP ports to open |
| --- | --- | --- |
| Camera car | 42 | 17900-18149 |
| LiDAR car | 43 | 18150-18399 |
| Gripper car | 44 | 18400-18649 |

Enter the same domain and the car's IP when you add the robot in Hive.

## On each Pi (once)

1. Export the domain in whatever starts the robot's launch file (a start script, a systemd unit, or
   `~/.bashrc` if the launch runs from an interactive shell). Use the car's own domain:

   ```bash
   export ROS_DOMAIN_ID=<42|43|44>
   ```

2. Optional, helps discovery when multicast is flaky on the Wi-Fi:

   ```bash
   export ROS_STATIC_PEERS=<laptop-ip>
   ```

3. Allow the laptop to reach that domain's DDS ports (camera car shown; use its own range):

   ```bash
   sudo ufw allow from <laptop-ip> proto udp to any port 17900:18149
   ```

   | Car | Rule's port range |
   | --- | --- |
   | Camera car (42) | `17900:18149` |
   | LiDAR car (43) | `18150:18399` |
   | Gripper car (44) | `18400:18649` |

   If the Pi previously allowed only the default domain (`7400:7500`), the new rule is in addition
   to it; remove the old one only if nothing else uses it.

4. Restart the launch file so it picks up the new environment, then check from the Pi:
   `ros2 node list` (with the same `ROS_DOMAIN_ID` exported) should show the car's nodes.

**A changed laptop IP breaks the Pi rule.** `ufw allow from <laptop-ip>` matches one address. If the
laptop gets a different DHCP address (new Wi-Fi, lease expiry), connect fails with "no nodes
answered" until you add a rule for the new address (and update `ROS_STATIC_PEERS` if you set it).
Reserve the laptop's address on the router, or allow the subnet instead of one host.

## On the Windows laptop

ROS 2 runs inside WSL (mirrored networking), so Windows filters the traffic with the Hyper-V
firewall, not the normal Windows Firewall. In an elevated PowerShell, widen the rule for WSL to the
three UDP ranges, from each car's address:

```powershell
New-NetFirewallHyperVRule -Name "Hive-ROS2-DDS-from-cars" `
  -DisplayName "Hive ROS 2 DDS from cars" `
  -Direction Inbound -Action Allow -Protocol UDP `
  -VMCreatorId "{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}" `
  -LocalPorts 17900-18649 `
  -RemoteAddresses <camera-car-ip>,<lidar-car-ip>,<gripper-car-ip>
```

`{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}` is the WSL VM creator ID. The three ranges are contiguous,
so one rule covers 17900-18649. To change an existing rule instead of adding one:

```powershell
Get-NetFirewallHyperVRule -Name "<existing-rule-name>"
Set-NetFirewallHyperVRule -Name "<existing-rule-name>" -LocalPorts 17900-18649 `
  -RemoteAddresses <camera-car-ip>,<lidar-car-ip>,<gripper-car-ip>
```

Confirm WSL uses mirrored networking (`.wslconfig` has `networkingMode=mirrored`). Then the Windows
browser reaches the WSL backend on `127.0.0.1:8787`.

## Running Hive on Windows

`npm run hive` from PowerShell starts Vite with Windows Node and starts the backend inside WSL
(`wsl.exe --cd <repo as /mnt/...> --exec python3 backend/server.py`), because `rclpy` only exists
there. `npm run backend` starts only the backend. Set `HIVE_WSL_DISTRO` to choose a distro other than
the WSL default.

Notes:

- The backend pins `RMW_IMPLEMENTATION=rmw_fastrtps_cpp` (the Jazzy default, which the Pis run)
  unless `HIVE_RMW` is set. A `~/.bashrc` in WSL that exports Cyclone DDS or a `ROS_DOMAIN_ID` no
  longer matters for Hive: the worker sets the RMW and each robot's own domain itself.
- In WSL mirrored mode the `ros2` CLI can hang until you run `ros2 daemon start`. This only
  affects the `ros2` commands you type for troubleshooting; Hive does not use the daemon.
- Ctrl+C: the launcher (not the console) tells the backend inside WSL to stop with SIGINT, so it stops every robot it is driving before exiting, the same as on Ubuntu. If the backend has not exited after 3 s the launcher asks again, and only after another 3 s kills it. The backend inside WSL ignores the hang-up from the console closing, and logs to a file under `/tmp` that the launcher shows, so closing the console window also goes through that stop.
- Vite must stay on port 5173 (the backend only accepts that origin). If another process holds
  5173, Vite exits instead of moving.

## Hardware checklist

Run with the wheels off the ground first. For each car (camera, LiDAR, gripper):

1. **Connect.** Add the robot with its IP and domain, press Connect. Expect "Found N nodes and M
   topics" and no `/cmd_vel` warning.
2. **Drive.** Hold Forward for a second and release. The wheels turn at the chosen power, then stop.
   Try the power slider at 20% and 100%.
3. **Stop confirmed.** After release, the pad shows "Car confirmed 0,1,0,1" (or similar) with an
   age, and no red "Stop not confirmed" banner.
4. **Sensor visible.** Camera car: the camera panel shows frames. LiDAR car: the LiDAR plot shows
   a scan. Gripper car: Open and Close move the gripper.
5. **Measured MB/s.** Note the MB/s the panel reports from the car at each camera quality (Low,
   Medium, High). If the camera panel is blocked because the car refused a runtime profile change,
   record that and the exact parameter string the car accepted.
6. **Two cars at once.** Connect two cars, drive one, and confirm the other does not move
   (no cross-talk). Repeat with all three connected.
7. **Wi-Fi drop mid-drive.** Hold Forward, then turn off the laptop's Wi-Fi. Record what the car
   does. Camera and LiDAR car firmware have no command timeout, so it may keep its last command: a
   robot-side limit Hive cannot fix.
8. **Ctrl+C.** Hold a direction and stop Hive in the terminal. The car should stop.
9. **Another operator.** Run a second `ros2 topic pub /cmd_vel` against the same car. Hive should
   show "Someone else is also driving this car".

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Connect: "no nodes answered" | Domain in Hive matches the Pi's `ROS_DOMAIN_ID`; the Pi's `ufw` rule matches the laptop's current IP and this domain's range; the Hyper-V rule covers the range and the car's IP. |
| Works, then stops after a reconnect to Wi-Fi | The laptop's IP changed; see "A changed laptop IP breaks the Pi rule". |
| Camera panel blocked | The camera was not confirmed at a low-bandwidth size: it refused the profile change, reported a different profile when read back, or its node did not answer (Hive fails closed when the car publishes a RealSense topic). The panel shows the reason and offers an explicit full-size override that may disrupt driving. Reconnect, or pick a quality again, to retry tuning. |
| `ros2` command hangs in WSL | Run `ros2 daemon start` once. |
