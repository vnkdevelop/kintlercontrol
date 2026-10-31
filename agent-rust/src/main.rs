#![cfg_attr(not(test), windows_subsystem = "windows")]
mod gui;
mod identity;
mod network;
mod tools;
mod transfer;

use sha2::{Digest, Sha256};
use std::{
    path::PathBuf,
    sync::{Arc, mpsc},
};
use tokio_util::sync::CancellationToken;

fn main() {
    if let Err(error) = start() {
        unsafe {
            windows_sys::Win32::UI::WindowsAndMessaging::MessageBoxW(
                std::ptr::null_mut(),
                format!("Kintler: {error}\0")
                    .encode_utf16()
                    .collect::<Vec<_>>()
                    .as_ptr(),
                "Kintler\0".encode_utf16().collect::<Vec<_>>().as_ptr(),
                windows_sys::Win32::UI::WindowsAndMessaging::MB_ICONERROR,
            );
        }
    }
}

fn start() -> anyhow::Result<()> {
    let args = std::env::args().collect::<Vec<_>>();
    let value = |flag: &str| {
        args.iter()
            .position(|v| v == flag)
            .and_then(|i| args.get(i + 1))
            .map(PathBuf::from)
    };
    if let Some(path) = value("--self-test") {
        std::fs::write(path,serde_json::json!({"ok":true,"role":"agent","native":true,"node_id":identity::node_id()?}).to_string())?;
        return Ok(());
    }
    let path = value("--env").unwrap_or_else(identity::profile);
    struct Instance(usize);
    impl Drop for Instance {
        fn drop(&mut self) {
            unsafe {
                windows_sys::Win32::Foundation::CloseHandle(self.0 as _);
            }
        }
    }
    let name = format!(
        "Local\\KintlerAgent-{:x}\0",
        Sha256::digest(path.to_string_lossy().as_bytes())
    )
    .encode_utf16()
    .collect::<Vec<_>>();
    let handle = unsafe {
        windows_sys::Win32::System::Threading::CreateMutexW(std::ptr::null(), 0, name.as_ptr())
    };
    if handle.is_null() {
        anyhow::bail!("Cannot reserve agent instance");
    }
    let _instance = Instance(handle as usize);
    if unsafe { windows_sys::Win32::Foundation::GetLastError() }
        == windows_sys::Win32::Foundation::ERROR_ALREADY_EXISTS
    {
        return Ok(());
    }
    let config = Arc::new(identity::Config::load(path)?);
    if args.iter().any(|v| v == "--headless") {
        let (sender, _) = mpsc::channel();
        tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)
            .enable_all()
            .build()?
            .block_on(network::run(config, sender, CancellationToken::new()));
    } else {
        gui::run(config)?;
    }
    Ok(())
}
