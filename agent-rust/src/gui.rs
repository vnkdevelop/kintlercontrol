use crate::{identity::Config, network};
use std::{
    ptr,
    sync::{Arc, Mutex, OnceLock, mpsc},
    time::Duration,
};
use tokio_util::sync::CancellationToken;
use windows_sys::Win32::{
    Foundation::*, Graphics::Gdi::*, System::LibraryLoader::GetModuleHandleW,
    UI::WindowsAndMessaging::*,
};

static STATE: OnceLock<Mutex<State>> = OnceLock::new();
struct State {
    label: usize,
    events: mpsc::Receiver<String>,
    config: Arc<Config>,
    stop: CancellationToken,
}
fn wide(text: &str) -> Vec<u16> {
    text.encode_utf16().chain(Some(0)).collect()
}
fn launch(config: Arc<Config>, sender: mpsc::Sender<String>, stop: CancellationToken) {
    std::thread::spawn(move || {
        tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)
            .enable_all()
            .build()
            .unwrap()
            .block_on(network::run(config, sender, stop));
    });
}

unsafe extern "system" fn procedure(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    unsafe {
        match message {
            WM_TIMER => {
                if let Some(state) = STATE.get() {
                    let state = state.lock().unwrap();
                    while let Ok(text) = state.events.try_recv() {
                        SetWindowTextW(state.label as _, wide(&text).as_ptr());
                    }
                }
                0
            }
            WM_COMMAND => {
                if let Some(state) = STATE.get() {
                    let mut state = state.lock().unwrap();
                    match wparam as u16 {
                        1 => {
                            state.stop.cancel();
                            let (sender, events) = mpsc::channel();
                            state.events = events;
                            state.stop = CancellationToken::new();
                            launch(state.config.clone(), sender, state.stop.clone());
                        }
                        2 => {
                            state.stop.cancel();
                            SetWindowTextW(
                                state.label as _,
                                wide("Отключено. Новые команды не принимаются.").as_ptr(),
                            );
                        }
                        _ => {}
                    }
                }
                0
            }
            WM_CLOSE => {
                if let Some(state) = STATE.get() {
                    state.lock().unwrap().stop.cancel();
                }
                DestroyWindow(hwnd);
                0
            }
            WM_DESTROY => {
                PostQuitMessage(0);
                0
            }
            _ => DefWindowProcW(hwnd, message, wparam, lparam),
        }
    }
}

pub fn run(config: Arc<Config>) -> anyhow::Result<()> {
    unsafe {
        let instance = GetModuleHandleW(ptr::null());
        let class = wide("KintlerRustAgent");
        let mut wc: WNDCLASSW = std::mem::zeroed();
        wc.lpfnWndProc = Some(procedure);
        wc.hInstance = instance;
        wc.lpszClassName = class.as_ptr();
        wc.hCursor = LoadCursorW(ptr::null_mut(), IDC_ARROW);
        wc.hbrBackground = (COLOR_WINDOW + 1) as HBRUSH;
        RegisterClassW(&wc);
        let hwnd = CreateWindowExW(
            0,
            class.as_ptr(),
            wide("Kintler — управляемый ПК / Rust").as_ptr(),
            WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX,
            CW_USEDEFAULT,
            CW_USEDEFAULT,
            700,
            420,
            ptr::null_mut(),
            ptr::null_mut(),
            instance,
            ptr::null(),
        );
        if hwnd.is_null() {
            anyhow::bail!("Cannot create Windows agent window");
        }
        let create =
            |kind: &str, text: &str, x: i32, y: i32, width: i32, height: i32, id: usize| {
                CreateWindowExW(
                    0,
                    wide(kind).as_ptr(),
                    wide(text).as_ptr(),
                    WS_CHILD | WS_VISIBLE,
                    x,
                    y,
                    width,
                    height,
                    hwnd,
                    id as HMENU,
                    instance,
                    ptr::null(),
                )
            };
        let font = GetStockObject(DEFAULT_GUI_FONT);
        for handle in [
            create("STATIC", "KINTLER / NATIVE AGENT", 24, 20, 640, 35, 0),
            create(
                "STATIC",
                &format!("Устройство: {}\nСервер: {}", config.node, config.url),
                24,
                65,
                640,
                55,
                0,
            ),
            create(
                "STATIC",
                "Доступ закрыт до вашего подтверждения в контроллере.\nКаждый новый запуск или переподключение запрашивает разрешение снова.\nКлюч этого ПК хранится под защитой Windows DPAPI, а не внутри EXE.",
                24,
                125,
                640,
                75,
                0,
            ),
            create(
                "STATIC",
                &format!("Разрешённые инструменты: {}", config.tools.join(", ")),
                24,
                260,
                640,
                55,
                0,
            ),
            create("BUTTON", "Подключить снова", 24, 320, 190, 35, 1),
            create("BUTTON", "Отключить", 230, 320, 150, 35, 2),
        ] {
            SendMessageW(handle, WM_SETFONT, font as usize, 1);
        }
        let label = create("STATIC", "Подключение…", 24, 205, 640, 50, 0);
        SendMessageW(label, WM_SETFONT, font as usize, 1);
        let (sender, events) = mpsc::channel();
        let stop = CancellationToken::new();
        STATE
            .set(Mutex::new(State {
                label: label as usize,
                events,
                config: config.clone(),
                stop: stop.clone(),
            }))
            .ok();
        SetTimer(hwnd, 1, 100, None);
        ShowWindow(hwnd, SW_SHOW);
        UpdateWindow(hwnd);
        launch(config, sender, stop);
        let mut message: MSG = std::mem::zeroed();
        while GetMessageW(&mut message, ptr::null_mut(), 0, 0) > 0 {
            TranslateMessage(&message);
            DispatchMessageW(&message);
        }
        std::thread::sleep(Duration::from_millis(150));
    }
    Ok(())
}
