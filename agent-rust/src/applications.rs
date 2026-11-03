use agent_desktop_core::{AdapterError, AppError, Deadline, DeliverySemantics, ErrorCode};
use std::os::windows::fs::MetadataExt;
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
    ptr,
};
use windows::{
    Win32::{
        System::Com::{CLSCTX_INPROC_SERVER, CoCreateInstance, IPersistFile, STGM_READ},
        UI::Shell::{IShellLinkW, ShellLink},
    },
    core::{Interface, PCWSTR},
};
use windows_sys::Win32::{
    System::{Com::CoTaskMemFree, Environment::ExpandEnvironmentStringsW},
    UI::Shell::{FOLDERID_CommonPrograms, FOLDERID_Programs, SHGetKnownFolderPath},
};

#[derive(Clone, Debug)]
pub struct Application {
    pub target: String,
    pub arguments: String,
    pub cwd: Option<String>,
}

fn refused(code: ErrorCode, message: &str) -> AppError {
    AppError::Adapter(
        AdapterError::new(code, message).with_disposition(DeliverySemantics::not_delivered()),
    )
}

pub fn short_name(value: &str) -> Option<String> {
    if value.is_empty() || value.contains(['\\', '/', ':', '\0']) {
        return None;
    }
    let value = value.to_lowercase();
    let value = value.strip_suffix(".exe").unwrap_or(&value);
    if value.is_empty() {
        None
    } else {
        Some(value.to_string())
    }
}

fn wide(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(Some(0)).collect()
}

fn text(value: &[u16]) -> String {
    String::from_utf16_lossy(&value[..value.iter().position(|v| *v == 0).unwrap_or(value.len())])
}

fn expand(value: &str) -> Option<String> {
    let input = wide(value);
    let mut buffer = vec![0; 32768];
    let count = unsafe {
        ExpandEnvironmentStringsW(input.as_ptr(), buffer.as_mut_ptr(), buffer.len() as u32)
    };
    if count == 0 || count as usize > buffer.len() {
        None
    } else {
        Some(text(&buffer))
    }
}

fn known_folder(id: &windows_sys::core::GUID) -> Option<PathBuf> {
    let mut value = ptr::null_mut();
    if unsafe { SHGetKnownFolderPath(id, 0, ptr::null_mut(), &mut value) } < 0 || value.is_null() {
        return None;
    }
    let length = (0..32768)
        .take_while(|index| unsafe { *value.add(*index) != 0 })
        .count();
    let path = if length < 32768 {
        Some(PathBuf::from(String::from_utf16_lossy(unsafe {
            std::slice::from_raw_parts(value, length)
        })))
    } else {
        None
    };
    unsafe { CoTaskMemFree(value as _) };
    path
}

fn shortcut(path: &Path) -> Option<Application> {
    let file = wide(path.to_str()?);
    let link: IShellLinkW =
        unsafe { CoCreateInstance(&ShellLink, None, CLSCTX_INPROC_SERVER) }.ok()?;
    let persist: IPersistFile = link.cast().ok()?;
    unsafe { persist.Load(PCWSTR(file.as_ptr()), STGM_READ) }.ok()?;
    let mut target = vec![0; 32768];
    let mut arguments = vec![0; 32768];
    let mut cwd = vec![0; 32768];
    unsafe { link.GetPath(&mut target, ptr::null_mut(), 0) }.ok()?;
    unsafe { link.GetArguments(&mut arguments) }.ok()?;
    unsafe { link.GetWorkingDirectory(&mut cwd) }.ok()?;
    let target = expand(&text(&target))?;
    if target.is_empty() {
        return Some(Application {
            target: path.to_string_lossy().into_owned(),
            arguments: String::new(),
            cwd: None,
        });
    }
    let cwd = expand(&text(&cwd)).filter(|v| !v.is_empty());
    Some(Application {
        target,
        arguments: text(&arguments),
        cwd,
    })
}

fn choose(values: Vec<Application>) -> Result<Option<Application>, AppError> {
    let mut unique = BTreeMap::new();
    for value in values {
        let key = (
            value.target.to_lowercase(),
            value.arguments.clone(),
            value.cwd.as_ref().map(|s| s.to_lowercase()),
        );
        unique.entry(key).or_insert(value);
    }
    if unique.len() > 1 {
        return Err(refused(
            ErrorCode::AmbiguousTarget,
            "Several installed applications match this name. Use the full executable path.",
        ));
    }
    Ok(unique.into_values().next())
}

pub fn find_in_folders(
    name: &str,
    folders: &[PathBuf],
    deadline: Deadline,
) -> Result<Option<Application>, AppError> {
    let Some(name) = short_name(name) else {
        return Ok(None);
    };
    let mut pending = folders.iter().cloned().map(|p| (p, 0)).collect::<Vec<_>>();
    let mut values = Vec::new();
    let mut visited = 0;
    while let Some((folder, depth)) = pending.pop() {
        deadline.ensure_remaining()?;
        let entries = match fs::read_dir(folder) {
            Ok(entries) => entries,
            Err(_) => continue,
        };
        for entry in entries.flatten() {
            deadline.ensure_remaining()?;
            visited += 1;
            if visited > 10000 {
                return Err(refused(
                    ErrorCode::ActionFailed,
                    "Start menu search limit reached. Use the full executable path.",
                ));
            }
            let path = entry.path();
            let metadata = match fs::symlink_metadata(&path) {
                Ok(meta) => meta,
                Err(_) => continue,
            };
            if metadata.file_attributes() & 0x400 != 0 {
                continue;
            }
            if metadata.is_dir() {
                if depth >= 16 {
                    return Err(refused(
                        ErrorCode::ActionFailed,
                        "Start menu search depth reached. Use the full executable path.",
                    ));
                }
                pending.push((path, depth + 1));
            } else if metadata.is_file()
                && path
                    .extension()
                    .and_then(|s| s.to_str())
                    .is_some_and(|s| s.eq_ignore_ascii_case("lnk"))
            {
                let alias = path
                    .file_stem()
                    .and_then(|s| s.to_str())
                    .is_some_and(|s| s.to_lowercase() == name);
                if let Some(application) = shortcut(&path) {
                    let target = Path::new(&application.target)
                        .file_name()
                        .and_then(|s| s.to_str())
                        .and_then(short_name);
                    if (alias || target.as_deref() == Some(name.as_str()))
                        && Path::new(&application.target).is_file()
                    {
                        values.push(application);
                    }
                }
            }
        }
    }
    choose(values)
}

pub fn find_installed(name: &str, deadline: Deadline) -> Result<Option<Application>, AppError> {
    if short_name(name).is_none() {
        return Ok(None);
    }
    let folders = [
        known_folder(&FOLDERID_Programs),
        known_folder(&FOLDERID_CommonPrograms),
    ]
    .into_iter()
    .flatten()
    .collect::<Vec<_>>();
    find_in_folders(name, &folders, deadline)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn bare_names_only() {
        assert_eq!(short_name("ZABOR.exe"), Some("zabor".into()));
        assert_eq!(short_name("Zabor"), Some("zabor".into()));
        assert!(short_name("C:\\Apps\\zabor.exe").is_none());
        assert!(short_name("shell:AppsFolder").is_none());
    }
    #[test]
    fn duplicate_targets_are_not_ambiguous() {
        let a = Application {
            target: "C:\\Apps\\Zabor.exe".into(),
            arguments: String::new(),
            cwd: None,
        };
        let b = Application {
            target: "c:\\apps\\zabor.exe".into(),
            ..a.clone()
        };
        assert!(choose(vec![a, b]).unwrap().is_some());
    }
    #[test]
    fn different_targets_are_ambiguous() {
        let a = Application {
            target: "C:\\Apps\\Zabor.exe".into(),
            arguments: String::new(),
            cwd: None,
        };
        let b = Application {
            target: "C:\\Other\\Zabor.exe".into(),
            ..a.clone()
        };
        assert!(choose(vec![a, b]).is_err());
    }
    #[test]
    fn shortcuts_resolve_alias_target_and_arguments() {
        agent_desktop_windows::bootstrap_hosted_library().unwrap();
        let root = std::env::temp_dir()
            .join("opencode")
            .join(format!("kintler-shortcuts-{}", std::process::id()));
        fs::create_dir_all(&root).unwrap();
        let target = std::env::current_exe().unwrap();
        let link: IShellLinkW =
            unsafe { CoCreateInstance(&ShellLink, None, CLSCTX_INPROC_SERVER) }.unwrap();
        let target_wide = wide(target.to_str().unwrap());
        let args = wide("--fixture-default");
        unsafe { link.SetPath(PCWSTR(target_wide.as_ptr())) }.unwrap();
        unsafe { link.SetArguments(PCWSTR(args.as_ptr())) }.unwrap();
        let persist: IPersistFile = link.cast().unwrap();
        let file = root.join("Zabor.lnk");
        let file_wide = wide(file.to_str().unwrap());
        unsafe { persist.Save(PCWSTR(file_wide.as_ptr()), true) }.unwrap();
        let found = find_in_folders(
            "zabor.exe",
            std::slice::from_ref(&root),
            Deadline::after(5000).unwrap(),
        )
        .unwrap()
        .unwrap();
        assert_eq!(Path::new(&found.target), target);
        assert_eq!(found.arguments, "--fixture-default");
        let actual = find_in_folders(
            target.file_name().unwrap().to_str().unwrap(),
            std::slice::from_ref(&root),
            Deadline::after(5000).unwrap(),
        )
        .unwrap();
        assert!(actual.is_some());
        drop(persist);
        drop(link);
        fs::remove_file(file).unwrap();
        fs::remove_dir(root).unwrap();
    }
}
