# Pathfinder glasses companion — iPhone

First implementation for Liam's **Meta Adventurer glasses paired with an iPhone**. The laptop transport and simulator jobs are tested. The native project has **not been compiled or tested on a phone**: this development host is Linux, without Xcode. Follow the native acceptance steps below before describing it as a working glasses demo.

The flow is tap to speak → glasses Bluetooth microphone → iPhone WAV → laptop → **Meta Muse Voice Transcribe** → transcript review on the iPhone → **Muse Spark** → existing validated tools, no-go zones and STOP latch → simulated SO-101. The iPhone can read the reply aloud using its own speech synthesiser; choose the glasses as its audio output. This is foreground push-to-talk, without intercepting “Hey Meta”.

## Laptop setup

Run these from the repository root. The examples use a separate Deck port, 8774, to avoid replacing a running main GUI. A running simulator on 8766 is required; use `simulation/.venv/bin/python simulation/start.py` if needed.

```bash
# Terminal 1: no paid calls, speech recognition is explicitly scripted
workspace_splat/.venv/bin/python -m lookandpick.mission_control --backend sim --model fake --port 8774
```

For genuine speech recognition and Spark, use `--model spark` instead. This reuses the existing laptop `MODEL_API_KEY` in `.env` and its configured Spark spend/call caps. Voice transcription has its own per-clip cost logged by the existing voice client; the Spark cap does not cover ASR. No model API key goes into the companion or relay. Only send personal photos to the standard Spark model; contributor mode is rejected for photos.

Choose the laptop's Wi-Fi IPv4 address, for example `192.168.1.42`. Both devices must be on a network where they can reach each other. Substitute your actual address consistently below; a DHCP address change needs a new certificate and gateway address.

```bash
workspace_splat/.venv/bin/python scripts/create_glasses_tls.py 192.168.1.42

# Terminal 2: authenticated HTTPS phone relay, forwarding only glasses routes
workspace_splat/.venv/bin/python -m lookandpick.glasses_gateway \
  --bind 192.168.1.42 --public-host 192.168.1.42 --port 8772 \
  --deck-url http://127.0.0.1:8774 \
  --cert recordings/glasses-gateway/tls/server.crt \
  --key recordings/glasses-gateway/tls/server.key
```

Alternatively supply a normally trusted server certificate with a matching hostname. The helper creates a local development CA and server certificate valid for 30 days in a new, private directory. It refuses to overwrite an existing directory. Transfer **only `ca.crt`** to your iPhone using your normal trusted method. Install its profile in Settings, then explicitly enable trust under General → About → Certificate Trust Settings. This changes trust on your phone; review the certificate yourself. Remove the development profile after testing. Never transfer `ca.key` or `server.key`. The app uses ordinary certificate verification; there is no “ignore certificate errors” setting.

The relay creates `recordings/glasses-gateway/pairing-token` with mode 600. Copy that token into the app's pairing field using a trusted route. It is a revocable local bearer credential, **not the Meta API key**. It is stored in the iPhone Keychain with `WhenUnlockedThisDeviceOnly`. To rotate it, stop the relay, remove its token file, restart and pair again. Keys, certificates, token and recordings are git-ignored. The relay prints the token's path, never its value. Restrict any firewall opening to your trusted local network; do not publish this relay on the internet.

## Build on the Mac

1. Copy this repository to the Mac. Read Meta's SDK terms and enable Developer Mode in the Meta AI app for your account/glasses. Pair Adventurer with that iPhone. SDK registration requires Meta's setup; a Bluetooth microphone connection is a separate audio route.
2. Open `mobile/PathfinderGlasses/PathfinderGlasses.xcodeproj` in Xcode. The project pins Meta's iOS Device Access Toolkit to **1.0.1**, `MWDATCore` only. Its published binary interface declares Apple Swift **6.3.3**; use an Xcode toolchain that supports it or newer. Minimum app deployment target is **iOS 17.2**. An older compiler can fail to import the SDK even though the app's Swift language mode is 5.
3. Select your signing team and a unique bundle identifier, then your physical iPhone. Developer Mode placeholders in `Info.plist` are `MetaAppID = 0` and an empty client token; a production/distributed app needs its own registration values from Meta. The callback scheme is `pathfinder-glasses://`. These values are separate from the laptop model API key.
4. Build/run. Allow microphone, Bluetooth and local-network access when prompted. Tap **Register with Meta AI** to inspect SDK registration/device status. Voice capture itself uses AVAudioSession Bluetooth HFP, following Meta's published audio sample.
5. Enter `https://<laptop-address>:8772` and the gateway pairing token. Connect, refresh microphones and select the glasses by their Bluetooth name. The app accepts only a selected HFP input and verifies the actual input route; a disconnect/interruption discards the recording. Test this on the actual Adventurer hardware.

An unsigned compile check on the Mac, after resolving/accepting the SDK dependencies:

```bash
xcodebuild -project mobile/PathfinderGlasses/PathfinderGlasses.xcodeproj \
  -scheme PathfinderGlasses -destination 'generic/platform=iOS' \
  -configuration Debug -derivedDataPath /tmp/pathfinder-glasses-build \
  CODE_SIGNING_ALLOWED=NO build
```

## Try an instruction

Start with fake mode. Type “pick up the red cube”, review, then send: Deck on `http://127.0.0.1:8774/` should show the normal model/tool timeline. A voice clip in this mode uses the typed **demo transcript**; it does not recognise speech and makes no paid calls. For a full voice test, restart that Deck with `--model spark`, reconnect the app and explicitly enable **Send my recording and optional photo to Meta**. Record a short command, finish, check its words and tap **Send to simulator**.

Recordings are mono 16-bit PCM WAV at 16 kHz, maximum 30 seconds. Audio is held in a protected temporary iPhone file, removed after normal finishing/cancelling, then sent to ASR without a server recording file. A clip left by an abruptly terminated app is removed on its next launch. Transcripts and model/tool results appear in the existing timeline. Media pending review is discarded after 90 seconds, cancellation or STOP. A photo can be imported from the iPhone library; this is **not a live glasses camera feed**. It is resized to at most 1024 pixels on its longest side and 400 kB before upload. The companion does not yet verify that an imported photo came from the glasses or align it to the calibrated workspace.

The red **STOP SIMULATOR** button requests a direct stop without ASR/Spark or the command queue. A spoken “stop” still needs ASR to hear it. A remote stop needs a working network; the app only says “confirmed” after the laptop acknowledges it. Reset remains a deliberate laptop operation. There is no phone endpoint for reset, joint/servo control or physical-arm selection. A cancelled or stopped request cannot become executable again when a late ASR/model response returns. A command already waiting on a model keeps its queue reservation until it unwinds, preventing an early reset.

## Acceptance still needed on the Mac/iPhone

- Compile with the pinned SDK and resolve any native build errors; signing and Developer Mode registration.
- Confirm Adventurer is listed, its HFP input really records the wearer, and replies play on the intended output.
- Real speech → correct transcript → explicit review → Spark → simulator pick/place.
- Disconnect the glasses mid-clip, background the app, revoke mic permission, lose Wi-Fi and tap STOP while a request is pending. None should silently substitute the phone microphone or claim an unacknowledged stop succeeded.
- Imported photo reference (“this one”) should resolve to an existing scene object or ask for clarification. Accurate glasses camera calibration, live capture, wake word/hands-free activation and physical-arm commissioning are future work.

No real speech, workspace photo, new paid model call or physical-arm command was sent during this implementation's automated checks.

## Sources and ownership

Built during the hackathon: companion source, Xcode project, bounded review jobs, relay, development-certificate helper and tests. Reuses the existing project `voice.py`, `Runtime`, `Orchestrator`, `Tools`, no-go zones and `Safety`; models never directly drive motors.

Upstream SDK is Meta's **Wearables Device Access Toolkit**, governed by [Meta's SDK licence](https://github.com/meta/meta-wearables-dat-ios/blob/1.0.1/LICENSE). It is a dependency, not project-authored code. No SDK binaries or sample implementations are vendored here. Verified against tag `1.0.1`, commit `0bd369298eef6a980ad0bdb6c8a5502f2c02a08c` and its public `MWDATCore` interface. Registration uses `configure`, registration/device async streams, `startRegistration`, and `handleUrl`. The [SDK changelog](https://github.com/meta/meta-wearables-dat-ios/blob/1.0.1/CHANGELOG.md) lists Meta Glasses and `metaGlasses` support; Meta staff identify Adventurer under that family in [the SDK support discussion](https://github.com/facebook/meta-wearables-dat-android/issues/190). microphone route selection follows the approach in Meta's [AudioInputHandler](https://github.com/meta/meta-wearables-dat-ios/blob/main/samples/CameraAccess/CameraAccess/Media/AudioInputHandler.swift). See the [official toolkit overview](https://developers.meta.com/wearables/device-access-toolkit/) for account/device setup.
