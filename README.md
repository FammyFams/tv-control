# tv-control

Control the TVs in the house from a browser (and soon, an iPhone app).

## What's here

| Path | What it is |
| --- | --- |
| `web/remote.html` | Single-file web remote for the Roku TV. Open it in Chrome or Edge on a computer that's on the home wifi. |

## Web remote

Open `web/remote.html` in a browser. It talks directly to the TV over the home network, so the computer has to be on the **same wifi as the TV**.

- **Controls:** power, navigation pad, back/home, replay, options, playback, volume, typing into search boxes, app shortcuts and input switching
- **Keyboard:** arrows move · Enter = OK · Backspace = back · H = home · Space = play/pause · M = mute · +/− = volume
- **TV address:** set in the box in the header (saved in the browser). The default is `10.0.0.228`.
- If Chrome/Edge asks to let the page **access devices on your local network**, click **Allow**.

### How it works

Roku TVs expose the [External Control Protocol (ECP)](https://developer.roku.com/docs/developer-program/dev-tools/external-control-api.md) on port `8060`:

| Action | Request |
| --- | --- |
| Press a button | `POST /keypress/<Key>` (e.g. `Home`, `Select`, `Up`, `Play`, `VolumeUp`) |
| Type a character | `POST /keypress/Lit_<char>` |
| Open an app / input | `POST /launch/<appId>` (e.g. `12` = Netflix, `tvinput.hdmi1`) |
| Device info / apps / current app | `GET /query/device-info`, `/query/apps`, `/query/active-app` |

The browser can send these, but CORS stops it reading the replies, so the page can't confirm that a command worked. It can only tell when the TV is unreachable.

### Finding the TV if its address changes

Roku TVs answer an SSDP search (`ST: roku:ecp`) on the local network. Or check the TV: **Settings → Network → About**.

## Roadmap

- [ ] Expo (React Native) app for iPhone/iPad
- [ ] Samsung TV support (WebSocket API on port 8001/8002)
