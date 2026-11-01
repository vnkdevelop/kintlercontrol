use crate::{
    identity::Config,
    transfer::{Transfers, digest},
};
use anyhow::{Result, anyhow, bail};
use serde_json::{Value, json};
use std::{
    fs,
    path::{Path, PathBuf},
    process::Stdio,
    ptr,
    sync::{Arc, Mutex},
    time::{SystemTime, UNIX_EPOCH},
};
use sysinfo::{Disks, Networks, Pid, System};
use tokio::{
    io::{AsyncRead, AsyncReadExt},
    process::Command,
    sync::mpsc,
};
use tokio_util::sync::CancellationToken;
use windows_sys::Win32::{
    Foundation::{CloseHandle, FILETIME, RECT},
    Graphics::Gdi::*,
    System::{JobObjects::*, Threading::*},
    UI::{Shell::IsUserAnAdmin, WindowsAndMessaging::*},
};
use winreg::{RegKey, enums::*};

pub fn admin() -> bool {
    unsafe { IsUserAnAdmin() != 0 }
}
pub fn metadata(config: &Config, system: &mut System) -> Value {
    system.refresh_cpu_usage();
    system.refresh_memory();
    json!({"name":config.name,"platform":"Windows","user":std::env::var("USERNAME").unwrap_or_default(),"admin":admin(),"version":"1.4.0-rust","tools":config.tools,"telemetry":{"cpu_percent":system.global_cpu_usage(),"ram_total":system.total_memory(),"ram_percent":100.0*(system.used_memory() as f64)/(system.total_memory().max(1) as f64),"uptime":System::uptime()}})
}

fn process_created(pid: u32) -> Option<f64> {
    unsafe {
        let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
        if handle.is_null() {
            return None;
        }
        let (mut created, mut exit, mut kernel, mut user): (
            FILETIME,
            FILETIME,
            FILETIME,
            FILETIME,
        ) = std::mem::zeroed();
        let ok = GetProcessTimes(handle, &mut created, &mut exit, &mut kernel, &mut user);
        CloseHandle(handle);
        if ok == 0 {
            None
        } else {
            Some(
                (((created.dwHighDateTime as u64) << 32) | created.dwLowDateTime as u64) as f64
                    / 10000000.0
                    - 11644473600.0,
            )
        }
    }
}

fn processes(args: &Value) -> Result<Value> {
    let mut system = System::new_all();
    std::thread::sleep(sysinfo::MINIMUM_CPU_UPDATE_INTERVAL);
    system.refresh_all();
    let limit = args["limit"].as_u64().unwrap_or(100);
    if !(1..=500).contains(&limit) {
        bail!("Invalid process limit");
    }
    let mut values=system.processes().iter().map(|(pid,p)|json!({"pid":pid.as_u32(),"name":p.name().to_string_lossy(),"user":p.user_id().map(|u|format!("{u:?}")).unwrap_or_default(),"ram":p.memory(),"cpu":p.cpu_usage(),"created":process_created(pid.as_u32()).unwrap_or(p.start_time() as f64)})).collect::<Vec<_>>();
    let sort = args["sort"].as_str().unwrap_or("ram");
    if !["ram", "cpu", "pid"].contains(&sort) {
        bail!("Invalid process sort");
    }
    values.sort_by(|a, b| {
        let order = a[sort]
            .as_f64()
            .unwrap_or(0.0)
            .total_cmp(&b[sort].as_f64().unwrap_or(0.0));
        if sort == "pid" {
            order
        } else {
            order.reverse()
        }
    });
    values.truncate(limit as usize);
    Ok(json!({"ok":true,"processes":values}))
}

fn kill(args: &Value) -> Result<Value> {
    let pid = args["pid"]
        .as_u64()
        .filter(|v| *v <= u32::MAX as u64)
        .ok_or_else(|| anyhow!("Invalid PID"))? as u32;
    let system = System::new_all();
    let parent = system
        .process(Pid::from_u32(std::process::id()))
        .and_then(|p| p.parent())
        .map(|p| p.as_u32())
        .unwrap_or(0);
    if pid <= 4 || pid == std::process::id() || pid == parent {
        bail!("Protected PID");
    }
    let created = args["created"]
        .as_f64()
        .ok_or_else(|| anyhow!("Process creation time is required"))?;
    if (process_created(pid).ok_or_else(|| anyhow!("Process unavailable"))? - created).abs() > 0.01
    {
        bail!("PID has been reused");
    }
    unsafe {
        let handle = OpenProcess(PROCESS_TERMINATE | 0x00100000, 0, pid);
        if handle.is_null() {
            bail!("Process access denied");
        }
        let ok = TerminateProcess(handle, 1);
        if ok != 0 {
            WaitForSingleObject(handle, 3000);
        }
        CloseHandle(handle);
        if ok == 0 {
            bail!("Process termination failed");
        }
    }
    Ok(json!({"ok":true,"pid":pid}))
}

fn sysinfo() -> Value {
    let mut system = System::new();
    system.refresh_cpu_all();
    system.refresh_memory();
    std::thread::sleep(sysinfo::MINIMUM_CPU_UPDATE_INTERVAL);
    system.refresh_cpu_usage();
    system.refresh_memory();
    let disks=Disks::new_with_refreshed_list().iter().map(|d|json!({"device":d.name().to_string_lossy(),"mountpoint":d.mount_point().to_string_lossy(),"total":d.total_space(),"free":d.available_space()})).collect::<Vec<_>>();
    let network=Networks::new_with_refreshed_list().iter().map(|(name,data)|json!({"name":name,"mac":data.mac_address().to_string(),"addresses":data.ip_networks().iter().map(|v|v.to_string()).collect::<Vec<_>>()})).collect::<Vec<_>>();
    let mut gpu = Vec::new();
    if let Ok(key) = RegKey::predef(HKEY_LOCAL_MACHINE).open_subkey(
        r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}",
    ) {
        for child in key.enum_keys().flatten() {
            if child.chars().all(|c| c.is_ascii_digit())
                && let Ok(adapter) = key.open_subkey(child)
                && let Ok(name) = adapter.get_value::<String, _>("DriverDesc")
            {
                gpu.push(json!({"name":name}));
            }
        }
    }
    json!({"ok":true,"hostname":System::host_name().unwrap_or_default(),"platform":System::long_os_version().unwrap_or("Windows".into()),"user":std::env::var("USERNAME").unwrap_or_default(),"admin":admin(),"cpu_count":system.cpus().len(),"ram_total":system.total_memory(),"ram_percent":100.0*system.used_memory() as f64/system.total_memory().max(1) as f64,"uptime":System::uptime(),"cpu_percent":system.global_cpu_usage(),"disks":disks,"network":network,"gpu":{"available":!gpu.is_empty(),"provider":"windows-display-class","devices":gpu,"metrics_available":false}})
}

unsafe extern "system" fn monitor_callback(
    _: HMONITOR,
    _: HDC,
    rect: *mut RECT,
    data: isize,
) -> i32 {
    unsafe {
        (*(data as *mut Vec<RECT>)).push(*rect);
    }
    1
}
fn screenshot(args: &Value, root: &Path) -> Result<Value> {
    let index = args["monitor"].as_u64().unwrap_or(1) as usize;
    let mut monitors = Vec::new();
    unsafe {
        EnumDisplayMonitors(
            ptr::null_mut(),
            ptr::null(),
            Some(monitor_callback),
            &mut monitors as *mut Vec<RECT> as isize,
        );
    }
    let rect = if index == 0 {
        RECT {
            left: unsafe { GetSystemMetrics(SM_XVIRTUALSCREEN) },
            top: unsafe { GetSystemMetrics(SM_YVIRTUALSCREEN) },
            right: unsafe {
                GetSystemMetrics(SM_XVIRTUALSCREEN) + GetSystemMetrics(SM_CXVIRTUALSCREEN)
            },
            bottom: unsafe {
                GetSystemMetrics(SM_YVIRTUALSCREEN) + GetSystemMetrics(SM_CYVIRTUALSCREEN)
            },
        }
    } else {
        *monitors
            .get(index - 1)
            .ok_or_else(|| anyhow!("Monitor not found or no interactive desktop"))?
    };
    let (width, height) = (rect.right - rect.left, rect.bottom - rect.top);
    if width <= 0 || height <= 0 || width as u64 * height as u64 > 64000000 {
        bail!("Invalid desktop dimensions");
    }
    let mut rgb = Vec::with_capacity(width as usize * height as usize * 3);
    unsafe {
        let screen = GetDC(ptr::null_mut());
        if screen.is_null() {
            bail!("No interactive desktop");
        }
        let memory = CreateCompatibleDC(screen);
        let mut info: BITMAPINFO = std::mem::zeroed();
        info.bmiHeader.biSize = std::mem::size_of::<BITMAPINFOHEADER>() as u32;
        info.bmiHeader.biWidth = width;
        info.bmiHeader.biHeight = -height;
        info.bmiHeader.biPlanes = 1;
        info.bmiHeader.biBitCount = 32;
        info.bmiHeader.biCompression = BI_RGB;
        let mut bits = ptr::null_mut();
        let bitmap = CreateDIBSection(screen, &info, DIB_RGB_COLORS, &mut bits, ptr::null_mut(), 0);
        if bitmap.is_null() || memory.is_null() {
            if !bitmap.is_null() {
                DeleteObject(bitmap);
            }
            if !memory.is_null() {
                DeleteDC(memory);
            }
            ReleaseDC(ptr::null_mut(), screen);
            bail!("Desktop capture unavailable");
        }
        let old = SelectObject(memory, bitmap);
        let ok = BitBlt(
            memory,
            0,
            0,
            width,
            height,
            screen,
            rect.left,
            rect.top,
            SRCCOPY | CAPTUREBLT,
        );
        if ok != 0 {
            for pixel in
                std::slice::from_raw_parts(bits as *const u8, width as usize * height as usize * 4)
                    .as_chunks::<4>()
                    .0
            {
                rgb.extend_from_slice(&[pixel[2], pixel[1], pixel[0]]);
            }
        }
        SelectObject(memory, old);
        DeleteObject(bitmap);
        DeleteDC(memory);
        ReleaseDC(ptr::null_mut(), screen);
        if ok == 0 {
            bail!("Screen capture failed");
        }
    }
    let name = format!(
        "screenshot-{}.png",
        SystemTime::now().duration_since(UNIX_EPOCH)?.as_nanos()
    );
    let path = root.join(&name);
    let file = fs::File::create(&path)?;
    let mut encoder = png::Encoder::new(file, width as u32, height as u32);
    encoder.set_color(png::ColorType::Rgb);
    encoder.set_depth(png::BitDepth::Eight);
    encoder.write_header()?.write_image_data(&rgb)?;
    Ok(json!({"ok":true,"path":name,"size":fs::metadata(&path)?.len(),"sha256":digest(&path)?}))
}

struct Job(usize);
impl Drop for Job {
    fn drop(&mut self) {
        unsafe {
            CloseHandle(self.0 as _);
        }
    }
}
fn job(pid: u32) -> Result<Job> {
    unsafe {
        let handle = CreateJobObjectW(ptr::null(), ptr::null());
        if handle.is_null() {
            bail!("Cannot create process job");
        }
        let job = Job(handle as usize);
        let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if SetInformationJobObject(
            handle,
            JobObjectExtendedLimitInformation,
            &info as *const _ as _,
            std::mem::size_of_val(&info) as u32,
        ) == 0
        {
            bail!("Cannot configure process job");
        }
        let process = OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, 0, pid);
        if process.is_null() {
            bail!("Cannot contain subprocess");
        }
        let ok = AssignProcessToJobObject(handle, process);
        CloseHandle(process);
        if ok == 0 {
            bail!("Cannot contain subprocess in Windows job");
        }
        Ok(job)
    }
}

async fn pump<R: AsyncRead + Unpin>(
    mut reader: R,
    stream: &str,
    envelope: Value,
    sender: mpsc::Sender<Value>,
    bytes: Arc<std::sync::atomic::AtomicUsize>,
) -> Result<()> {
    let mut buffer = [0; 4096];
    loop {
        let count = reader.read(&mut buffer).await?;
        if count == 0 {
            break;
        }
        if bytes.fetch_add(count, std::sync::atomic::Ordering::Relaxed) + count > 2 * 1024 * 1024 {
            bail!("Output limit reached");
        }
        let data = match std::str::from_utf8(&buffer[..count]) {
            Ok(text) => text.to_string(),
            Err(_) => encoding_rs::IBM866.decode(&buffer[..count]).0.into_owned(),
        };
        let mut event = envelope.clone();
        event["type"] = json!("output");
        event["stream"] = json!(stream);
        event["data"] = json!(data);
        sender
            .send(event)
            .await
            .map_err(|_| anyhow!("Connection closed"))?;
    }
    Ok(())
}

async fn process(
    argv: Vec<String>,
    cwd: PathBuf,
    envelope: Value,
    sender: mpsc::Sender<Value>,
    cancel: CancellationToken,
) -> Result<Value> {
    let mut command = Command::new(&argv[0]);
    command
        .args(&argv[1..])
        .current_dir(cwd)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .creation_flags(0x08000200)
        .kill_on_drop(true);
    let mut child = command.spawn()?;
    let contained = job(child
        .id()
        .ok_or_else(|| anyhow!("Subprocess PID unavailable"))?)?;
    let bytes = Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let out = tokio::spawn(pump(
        child.stdout.take().unwrap(),
        "stdout",
        envelope.clone(),
        sender.clone(),
        bytes.clone(),
    ));
    let err = tokio::spawn(pump(
        child.stderr.take().unwrap(),
        "stderr",
        envelope,
        sender,
        bytes,
    ));
    let result = tokio::select! {_ = cancel.cancelled()=>Err(anyhow!("Command cancelled")), result=async {tokio::try_join!(async{out.await??;Ok::<(),anyhow::Error>(())},async{err.await??;Ok::<(),anyhow::Error>(())})?;let status=child.wait().await?;Ok(json!({"ok":status.success(),"exit_code":status.code()}))}=>result};
    drop(contained);
    let _ = child.kill().await;
    let _ = child.wait().await;
    result
}

fn text(args: &Value, key: &str) -> Result<String> {
    let value = args[key].as_str().unwrap_or("");
    if value.is_empty() || value.len() > 32768 || value.contains('\0') {
        bail!("Invalid {key}");
    }
    Ok(value.to_string())
}

pub async fn execute(
    tool: String,
    args: Value,
    config: Arc<Config>,
    transfers: Arc<Mutex<Transfers>>,
    envelope: Value,
    sender: mpsc::Sender<Value>,
    cancel: CancellationToken,
) -> Result<Value> {
    if !config.tools.contains(&tool) {
        bail!("Tool disabled by device owner");
    }
    if tool == "shell" {
        let shell = args["shell"].as_str().unwrap_or("cmd");
        let value = text(&args, "command")?;
        let argv = match shell {
            "cmd" => vec![
                "cmd.exe".into(),
                "/d".into(),
                "/s".into(),
                "/c".into(),
                value,
            ],
            "powershell" => vec![
                "powershell.exe".into(),
                "-NoLogo".into(),
                "-NoProfile".into(),
                "-NonInteractive".into(),
                "-Command".into(),
                value,
            ],
            _ => bail!("This Windows agent supports cmd and powershell"),
        };
        let cwd = std::path::Path::new(args["cwd"].as_str().unwrap_or("."));
        if cwd.is_absolute()
            || cwd.components().any(|p| {
                matches!(
                    p,
                    std::path::Component::ParentDir | std::path::Component::Prefix(_)
                )
            })
        {
            bail!("cwd must stay in workspace");
        }
        let cwd = fs::canonicalize(config.root.join(cwd))?;
        if !cwd.starts_with(&config.root) {
            bail!("cwd outside workspace");
        }
        return process(argv, cwd, envelope, sender, cancel).await;
    }
    if ["registry", "services", "tasks"].contains(&tool.as_str()) {
        let op = args["operation"].as_str().unwrap_or("list");
        if !["list", "status", "query"].contains(&op) && !admin() {
            bail!("Administrator privileges required");
        }
        let argv = match tool.as_str() {
            "registry" => {
                let key = text(&args, "key")?;
                if !["HKLM\\", "HKCU\\", "HKCR\\", "HKU\\", "HKCC\\"]
                    .iter()
                    .any(|v| key.starts_with(v))
                {
                    bail!("Local registry key required");
                }
                if op == "query" {
                    let mut argv = vec!["reg.exe".into(), "query".into(), key];
                    if args["name"].as_str().is_some_and(|v| !v.is_empty()) {
                        argv.extend(["/v".into(), text(&args, "name")?]);
                    }
                    argv
                } else if op == "set" {
                    let kind = args["value_type"].as_str().unwrap_or("REG_SZ");
                    if ![
                        "REG_SZ",
                        "REG_EXPAND_SZ",
                        "REG_DWORD",
                        "REG_QWORD",
                        "REG_MULTI_SZ",
                        "REG_BINARY",
                    ]
                    .contains(&kind)
                    {
                        bail!("Invalid registry value type");
                    }
                    vec![
                        "reg.exe".into(),
                        "add".into(),
                        key,
                        "/v".into(),
                        text(&args, "name")?,
                        "/t".into(),
                        kind.into(),
                        "/d".into(),
                        args["value"].as_str().unwrap_or("").into(),
                        "/f".into(),
                    ]
                } else {
                    bail!("Invalid registry operation");
                }
            }
            "services" => {
                if op == "list" {
                    vec![
                        "sc.exe".into(),
                        "query".into(),
                        "type=".into(),
                        "service".into(),
                        "state=".into(),
                        "all".into(),
                    ]
                } else if ["status", "start", "stop"].contains(&op) {
                    vec![
                        "sc.exe".into(),
                        if op == "status" {
                            "query".into()
                        } else {
                            op.into()
                        },
                        text(&args, "name")?,
                    ]
                } else {
                    bail!("Unsupported Windows service operation");
                }
            }
            _ => {
                if op == "list" {
                    vec![
                        "schtasks.exe".into(),
                        "/Query".into(),
                        "/FO".into(),
                        "CSV".into(),
                        "/V".into(),
                    ]
                } else if op == "create" {
                    vec![
                        "schtasks.exe".into(),
                        "/Create".into(),
                        "/TN".into(),
                        text(&args, "name")?,
                        "/TR".into(),
                        text(&args, "command")?,
                        "/SC".into(),
                        "ONLOGON".into(),
                    ]
                } else {
                    bail!("Invalid task operation");
                }
            }
        };
        return process(argv, config.root.clone(), envelope, sender, cancel).await;
    }
    let owner = envelope["session_id"].as_str().unwrap_or("").to_string();
    tokio::task::spawn_blocking(move || match tool.as_str() {
        "sysinfo" => Ok(sysinfo()),
        "ps" => processes(&args),
        "kill" => kill(&args),
        "screenshot" => screenshot(&args, &config.root),
        "upload" => transfers
            .lock()
            .map_err(|_| anyhow!("File state unavailable"))?
            .upload(&args, &owner),
        "download" => transfers
            .lock()
            .map_err(|_| anyhow!("File state unavailable"))?
            .download(&args),
        _ => Err(anyhow!("Unknown tool")),
    })
    .await?
}
