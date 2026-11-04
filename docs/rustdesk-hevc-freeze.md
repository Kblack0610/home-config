
# RustDesk screen freeze on the work MacBook (hevc_videotoolbox)

Machine: MacBook Pro Mac17,6, M5 Max, 128 GB, macOS 26.4.1 (25E253). RustDesk 1.4.9 build 67.
Viewer: main machine at 192.168.1.2. Host: 192.168.1.245 (clamshell, single external ASUS VT229 at 1920x1080).
First written 2026-09-14.

## THE FIX (read this first)

**On the MAIN machine (the viewer, not the Mac), in the RustDesk session toolbar: Display Settings -> Codec -> VP9.**

Anything except **Auto**. Auto is what breaks it: Auto resolves to H265, H265 selects the `hevc_videotoolbox` hardware encoder on the Mac, and that encoder never produces a frame. VP9 is the confirmed-good setting. VP8 or AV1 should work equally well; the only forbidden value is Auto.

This is a VIEWER-side setting. It is not on the Mac. The Mac cannot be configured out of this problem, which is the whole subject of Attempt 1 below.

Confirmed working 2026-09-14 05:01. Verified again over the following minutes: encoder built once and never rebuilt, zero video-service restarts, zero encode failures.

If the freeze ever comes back, the first thing to check is whether the viewer's codec slipped back to Auto.

## Symptom

Remote screen freezes. Input injection keeps working, so the session feels alive while the picture is stuck on the last frame the viewer received. Capture and input are separate services in RustDesk, which is why only one half dies.

## Root cause (confirmed)

The host's video service never encodes a single frame. It loops roughly every 1.25s:

    new encoder: HWRAM(HwRamEncoderConfig { name: "hevc_videotoolbox", width: 1920, height: 1080, ... })
    ERROR encode fail: no valid frame, times: 1
    INFO  switch to refresh
    INFO  stop video service: monitor0
    ERROR Error of monitor0 service: SWITCH

Careful with the causality here, because the obvious reading is wrong and cost a previous session (2026-08-26) four wrong root causes.

A single `encode fail: no valid frame, times: 1` is BENIGN encoder priming. It does not tear anything down. Post-fix this host logged 171 of them with zero teardowns. What matters is ESCALATION: the counter has to climb to 3 before the service is torn down. Pre-fix the log contains `times: 1`, `times: 2` and `times: 3`; post-fix it never exceeds 1.

So the question is not "why does an encode fail" but "why does it fail REPEATEDLY", and the answer is the H265 / VideoToolbox path. Once the counter hits 3 the service tears down, rebuilds a fresh VideoToolbox encoder, and starts failing again immediately. Self-sustaining, measured at about 48 teardowns per minute.

Encoder selection comes from the viewer. The peer advertises `h264: false, h265: true` and sends `prefer: Auto`, so the host logs `usable: vp8=true, av1=true, h264=false, h265=true` then `used preference: Auto, encoder: H265`, which resolves to the `hevc_videotoolbox` hardware path. That path is the one that fails.

That `h264: false, h265: true` line is the whole answer and it sits in the connection banner of every session. It was scrolled past on 2026-08-26 and scrolled past again here. Read the connection banner first.

## What is NOT the cause

| Suspected | Verdict | Evidence |
|---|---|---|
| macOS Secure Input blocking capture | No | Secure Input only blocks keyboard event taps (AeroSpace hotkeys, RustDesk's key grab). It cannot affect screen capture. It was held by `Terminal.app` with `SecureKeyboardEntry = 1`, a real but separate annoyance |
| Broken hardware encoder | No | RustDesk's own probe passes on every start: `Encoder hevc_videotoolbox test passed on attempt 1`, logged in `~/Library/Logs/RustDesk/check-hwcodec-config/`. It fails only on real capture frames |
| Stale hwcodec cache | No | `RustDesk_hwcodec.toml` is re-probed after every boot and the probe succeeds |
| Display sleep after ~20 min | No | `pmset -g` shows `displaysleep 0` and `sleep 0`, both prevented by RustDesk and Amphetamine |
| Sunshine competing for VideoToolbox | No | Sunshine never gets that far. It dies at dyld load: `Library not loaded: /opt/homebrew/*/libcurl.4.dylib`. Broken Homebrew install, unrelated |
| The RustDesk GUI crash | Separate bug | `SIGABRT` in `FlutterMacOS` on the `CVDisplayLink` thread, i.e. the GUI's vsync/render thread. Correlates with a display reconfiguration (the session began with 2 displays at 3456x2234 and dropped to 1 at 1920x1080 when the lid closed). It is not what freezes the picture; the `--server` process is a different process |

## Attempt 1: disable hardware codec host-side. FAILED.

Tried setting `enable-hwcodec = 'N'` under `[options]` in `~/Library/Preferences/com.carriez.RustDesk/RustDesk2.toml`. Both `enable-hwcodec` and `codec-preference` exist as strings in `Contents/Frameworks/liblibrustdesk.dylib`, so the key name is real.

Three things were learned, in order:

1. Editing the file while the server runs is useless. RustDesk holds config in memory and rewrites the file from memory on shutdown, dropping the line. It also reset `local-ip-addr` in the same write.
2. So the write was done with the job fully unloaded (`launchctl bootout gui/501/com.carriez.RustDesk_server`), then reloaded (`launchctl bootstrap gui/501 /Library/LaunchAgents/com.carriez.RustDesk_server.plist`). The server therefore started having read a config that definitely contained the line.
3. It made no difference. On the next client connection the host still logged `usable: ... h265=true`, `encoder: H265`, and `new encoder: HWRAM(... hevc_videotoolbox ...)`, and the failure loop resumed within 0.2s of the connection opening.

Conclusion: this build strips `enable-hwcodec` from the persisted config at startup-read AND does not honour it. The host-side config route is a dead end. Do not retry it.

A backup of the original config is at `RustDesk2.toml.bak-preHWfix-20260914-043734`.

## Correction to an earlier reading

An initial pass concluded the fix "may be working, unverified". It was not working. Once a client reconnected the log showed the same `HWRAM(hevc_videotoolbox)` encoder and continuous failures from 04:50:14 through 04:58 with no gap. There was never a good window on that server instance. Any impression that it worked for ~20 minutes is not supported by the host log.

## Attempt 2: viewer-side codec override. FIXED. Confirmed 2026-09-14 05:01.

On the main machine, in the RustDesk session toolbar: Display Settings -> Codec -> VP9.

The viewer's `prefer` field is what drives encoder selection, so this overrides the Auto -> H265 choice regardless of any host setting, applies live with no restart, and is not subject to the config-stripping above. Software VP9 at 1080p is trivial for an M5 Max.

Host log at the moment of the switch:

    05:01:28  connection count: 1, used preference: VP9, encoder: VP9
    05:01:28  new encoder: VPX(VpxEncoderConfig { width: 1920, height: 1080, quality: 0.67, codec: VP9, ... })

Measured before and after:

| Metric | Before (hevc_videotoolbox) | After (VP9), measured 22h45m later |
|---|---|---|
| Encoders built | one every ~1.25s, ~48/min | ONE, at 05:01:28, never rebuilt |
| `Error of monitor0 service: SWITCH` | 502 in the preceding 19 min | 0 |
| `encode fail` escalation | reaches `times: 3`, the teardown threshold | 171 failures, never exceeds `times: 1` |
| `--cm` session process | dropping constantly | same PID, 22h45m uptime |

The decisive difference is not that failures vanished, it is that they stop ESCALATING, so a failure no longer kills the service.

Deliberately note what makes this evidence different from the false confirmation on 2026-08-26, which called a 4-minute quiet run a fix. This is not a duration, it is structural: a rebuilt encoder always writes a `new encoder:` line, so the ABSENCE of that line across 22h45m proves the loop is not running. A process uptime cannot be confounded by a session boundary the way a quiet log window can. Do not accept a short clean window as proof of this bug ever again; check `ps -o etime` on the `--cm` process instead.

## Standing recommendation

Leave the viewer pinned to VP9. Do not rely on the host config: `enable-hwcodec` is stripped on every RustDesk start, so even if a future build honours it, it will not persist. The codec preference lives on the viewer and survives reboots on both ends.

If it ever regresses, the single line to check on the host is:

    grep "new encoder" ~/Library/Logs/RustDesk/server/RustDesk_rCURRENT.log | tail -1

`VPX(... codec: VP9 ...)` is healthy. `HWRAM(... hevc_videotoolbox ...)` means the viewer fell back to Auto and the freeze is coming back.

## Still unexplained

Why `hevc_videotoolbox` returns an empty frame on real capture data when RustDesk's own probe encodes a synthetic frame successfully on every boot. Not chased, because VP9 makes it moot. If it ever matters, the place to look is what `libs/scrap` hands the encoder, since the encoder demonstrably works standalone.

## Useful commands

    # host-side failure loop, live
    tail -f ~/Library/Logs/RustDesk/server/RustDesk_rCURRENT.log

    # which encoder was actually chosen
    grep -E "usable:|used preference|new encoder" ~/Library/Logs/RustDesk/server/RustDesk_rCURRENT.log

    # who holds macOS Secure Input (empty output means nobody)
    ioreg -l -w 0 | grep -o 'kCGSSessionSecureInputPID"=[0-9]*'

    # restart the host server cleanly
    launchctl kickstart -k gui/501/com.carriez.RustDesk_server

## Side fix, unrelated to the freeze but done at the same time

`Terminal.app` had Secure Keyboard Entry enabled, which is what produced AeroSpace's "secure input" warning and blocked event taps. Quit Terminal and set `defaults write com.apple.Terminal SecureKeyboardEntry -bool false`. Verified cleared: `kCGSSessionSecureInputPID` is now absent from `ioreg` entirely. Revert with `-bool true` if it was deliberate.
