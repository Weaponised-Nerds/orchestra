# Home Assistant Plug & Play Onboarding on Pi 5: Take Over vs Add-On

Trade-offs for running an automatic onboarding agent on a Raspberry Pi 5.

## The two approaches

**Option A: Take over onboarding.** A custom HAOS image or a patched Core drives the first-run flow itself, so the agent is in control before the person ever sees a screen.

**Option B: Slip it in after standard onboarding.** A normal add-on or custom integration starts working once the stock Home Assistant onboarding has finished.

## Option A: Take over onboarding (custom image or patched Core)

### Pros

- This is the only way to get a true zero-prompt experience. The onboarding API only accepts the first owner account before onboarding completes, so the agent has to be there first to create users without anyone typing a name and password.
- You control the whole first-run story. Location, units, analytics opt-out and the first integrations can all be set before the person ever sees a dashboard.
- You can ship a known-good image with the agent, local-first integrations and sensible defaults baked in. That suits a product-style "plug it in and go" box, and the Pi 5 is officially supported by HAOS.
- You can skip the stock onboarding screens entirely and replace them with your own, such as the three screens in the design mockup.

### Cons

- You take on a fork. Every HAOS, Supervisor and Core release means re-testing, rebasing or re-baking your changes, and the onboarding API is not a stable public contract. Updates are where this usually breaks.
- A modified image may not be supported by the Home Assistant team or community. Debugging gets harder, and users could lose the "this is just Home Assistant" safety net.
- Security burden. You are generating credentials and holding admin-level access during setup, so you need to decide where the generated password goes, how the person recovers access, and what the agent can do unattended.
- The agent cannot be too clever on a Pi 5. A Pi 5 cannot run a capable local LLM at useful speed, so the agent will be mostly rules, heuristics and discovery protocols (mDNS, SSDP, Zeroconf, vendor local APIs). A model-driven agent pushes you toward a cloud call, which works against the local-first rule.
- More engineering up front: an image build pipeline, first-boot provisioning, and testing on real hardware.

## Option B: Slip it in after standard onboarding (add-on or custom integration)

### Pros

- It rides on supported extension points. An add-on gets the Supervisor API, and a custom integration lives in the config folder. Neither needs you to fork the OS, and both survive normal updates far better.
- It is easy to ship and iterate. Users can install it from a repository, and you can update it independently of HAOS.
- It is lower risk. If your agent breaks, Home Assistant still works.
- It works on existing installs as well as new ones, so it could also serve people who already finished onboarding.

### Cons

- The first-run experience is still stock. Someone has to create an account, confirm the location and click through the standard screens before your agent can start, which breaks the "grandpa can do it" goal on step one.
- The agent needs credentials to act. Without a long-lived token or a user created for it, it cannot create users or rename devices, so you either ask the person to do something technical or add an extra setup step.
- It can feel bolted on. Two onboarding experiences back to back, the stock one and then yours, is confusing for a non-technical person.
- Some things are harder to automate from inside a running instance, such as changing system-level settings and network configuration.

## Side-by-side summary

|  | Option A: Take over | Option B: After onboarding |
| --- | --- | --- |
| Zero-prompt first run | Yes | No, stock steps come first |
| Maintenance | High (fork, rebase per release) | Low (supported extension points) |
| Update resilience | Fragile | Good |
| Security responsibility | You generate and hold credentials | Needs a token or user from the person |
| Works on existing installs | No | Yes |
| Up-front engineering | High | Moderate |

## Recommendation: a hybrid

Build a pre-provisioned HAOS image that finishes the stock onboarding automatically on first boot by calling its API, with a generated owner account and the location taken from the router. Then run the agent as a normal add-on or integration from that point on.

This gives you the zero-prompt first run of Option A. The agent itself lives in the supported extension layer of Option B, so you maintain a thin provisioning layer instead of a fork of the core.

## Open decision: account recovery

Whichever route you pick, decide early how a person recovers access when the account was generated for them. It changes the first screen in the mockup, because the agent needs to hand over a recovery method such as a printed card, a QR code or a trusted-family-member invite.
