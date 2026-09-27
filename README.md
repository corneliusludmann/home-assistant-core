# KNX Activity screenshots for the PR

Actual Home Assistant browser screenshots from the disposable Docker simulation, captured on 2026-09-27. Core commit: `82ee85de578cb51a2304df419434f76385455b67`; frontend: `20260826.7`. The screenshots show the existing Activity details UI with the proposed backend change. Device names and KNX traffic are simulated; no production installation or personal configuration is shown.

Suggested captions:

1. **Named KNX sender** (`01-named-switch.jpg`): Activity details identifies the incoming telegram's sender using its project name and individual address: “Simulated Living room wall switch (1.1.23)”.
2. **Sender without a project name** (`02-unknown-sender.jpg`): When no project metadata is available, Activity details shows the individual address, “1.1.24”.
3. **Actuator feedback** (`03-actuator-feedback.jpg`): A state update from an actuator identifies that actuator, “Simulated Lighting actuator (1.1.10)”. This does not infer which wall switch originally caused the actuator's state.

These are 1280 × 720 JPEG screenshots, without visual edits. Upload the images to the GitHub PR editor to obtain GitHub-hosted image URLs. No screenshots have been uploaded or published yet.
