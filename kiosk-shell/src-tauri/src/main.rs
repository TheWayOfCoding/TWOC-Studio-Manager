#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::Manager;

const DEFAULT_URL: &str = "http://localhost:8001/screen/kqc-screen";

fn main() {
    let url = std::env::args()
        .nth(1)
        .filter(|arg| !arg.trim().is_empty())
        .unwrap_or_else(|| DEFAULT_URL.to_string());
    tauri::Builder::default()
        .setup(move |app| {
            if let Some(window) = app.get_webview_window("main") {
                let target: url::Url = url.parse().expect("kiosk url must be valid");
                window.navigate(target).expect("kiosk navigate failed");
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("kiosk failed to run");
}
