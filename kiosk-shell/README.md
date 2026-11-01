# StudioKiosk (Tauri shell)

Borderless always-on-top fullscreen host for one kiosk page. No browser
chrome, no nags, no auto-anything: it updates only when rebuilt here.
Windows uses the system WebView2 engine; Linux builds use WebKitGTK.

- URL defaults to the home kiosk page; pass another as argv[1].
- Exit with ESC twice within 2s (first press shows a hint), or Alt+F4.
  Plain browsers ignore the handler and keep native ESC behavior.
- The kiosk page itself retries until the hub answers, so logon races
  with the hub task are harmless.

## Build (needs the Rust toolchain)

```
cargo build --release --manifest-path kiosk-shell/src-tauri/Cargo.toml
```

The exe lands in
`kiosk-shell/src-tauri/target/release/studio-kiosk.exe`.

## Deploy

```
scp kiosk-shell/src-tauri/target/release/studio-kiosk.exe studio@<hub>:C:/studio/kiosk/studio-kiosk.exe
```

Then `python tools/hub-kiosk.py install-app <url>` on the hub rewrites the
logon launcher (`studio-kiosk.bat`) to run the exe. Launch once by hand
(double-click the bat, or logoff/on); automatic forever after.
