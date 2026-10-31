use anyhow::{Result, anyhow, bail};
use base64::{Engine, engine::general_purpose::STANDARD};
use ed25519_dalek::{Signer, SigningKey};
use rand::RngCore;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap, fs, io::Write, path::PathBuf, ptr};
use windows_sys::Win32::{
    Foundation::LocalFree,
    Security::Cryptography::{
        CRYPT_INTEGER_BLOB, CRYPTPROTECT_UI_FORBIDDEN, CryptProtectData, CryptUnprotectData,
    },
};
use winreg::{RegKey, enums::*};

pub fn configured_origin() -> Result<Option<String>> {
    let file = std::env::current_exe()?
        .parent()
        .ok_or_else(|| anyhow!("Executable directory unavailable"))?
        .join("kintler-agent.json");
    if !file.exists() {
        return Ok(None);
    }
    if fs::metadata(&file)?.len() > 4096 {
        bail!("Agent configuration is too large");
    }
    let values: Value = serde_json::from_slice(&fs::read(file)?)?;
    if values
        .as_object()
        .is_none_or(|v| v.len() != 1 || !v.contains_key("url"))
    {
        bail!("Agent configuration must contain only the public server URL");
    }
    let url = values["url"]
        .as_str()
        .ok_or_else(|| anyhow!("Missing server URL"))?
        .trim_end_matches('/')
        .to_string();
    let parsed: tokio_tungstenite::tungstenite::http::Uri = url.parse()?;
    if parsed.scheme_str() != Some("https")
        || parsed.host().is_none()
        || parsed.authority().is_none_or(|v| v.as_str().contains('@'))
        || parsed.path_and_query().is_some_and(|v| v.as_str() != "/")
    {
        bail!("Agent configuration requires an HTTPS origin without credentials or query");
    }
    Ok(Some(url))
}
pub const TOOLS: &[&str] = &[
    "shell",
    "sysinfo",
    "ps",
    "kill",
    "screenshot",
    "upload",
    "download",
    "registry",
    "services",
    "tasks",
];

pub fn node_id() -> Result<String> {
    let key = RegKey::predef(HKEY_LOCAL_MACHINE).open_subkey_with_flags(
        r"SOFTWARE\Microsoft\Cryptography",
        KEY_READ | KEY_WOW64_64KEY,
    )?;
    let guid: String = key.get_value("MachineGuid")?;
    let host = hostname::get()?.to_string_lossy().to_string();
    Ok(format!(
        "node_{:x}",
        Sha256::digest(format!("{guid}{host}").as_bytes())
    )[..21]
        .to_string())
}

pub fn protect(data: &[u8], decrypt: bool) -> Result<Vec<u8>> {
    let mut data = data.to_vec();
    let mut entropy = b"Kintler:agent".to_vec();
    let incoming = CRYPT_INTEGER_BLOB {
        cbData: data.len() as u32,
        pbData: data.as_mut_ptr(),
    };
    let extra = CRYPT_INTEGER_BLOB {
        cbData: entropy.len() as u32,
        pbData: entropy.as_mut_ptr(),
    };
    let mut output: CRYPT_INTEGER_BLOB = unsafe { std::mem::zeroed() };
    let ok = unsafe {
        if decrypt {
            CryptUnprotectData(
                &incoming,
                ptr::null_mut(),
                &extra,
                ptr::null(),
                ptr::null(),
                CRYPTPROTECT_UI_FORBIDDEN,
                &mut output,
            )
        } else {
            CryptProtectData(
                &incoming,
                ptr::null(),
                &extra,
                ptr::null(),
                ptr::null(),
                CRYPTPROTECT_UI_FORBIDDEN,
                &mut output,
            )
        }
    };
    if ok == 0 {
        bail!("Windows cannot unlock this identity for the current user");
    }
    let bytes =
        unsafe { std::slice::from_raw_parts(output.pbData, output.cbData as usize).to_vec() };
    unsafe {
        LocalFree(output.pbData as _);
    }
    Ok(bytes)
}

pub struct Config {
    pub url: String,
    pub node: String,
    pub name: String,
    pub root: PathBuf,
    pub ca: Option<String>,
    pub local_http: bool,
    pub tools: Vec<String>,
    pub key: SigningKey,
}

impl Config {
    pub fn load(path: PathBuf) -> Result<Self> {
        if path.file_name().and_then(|v| v.to_str()) != Some(".env") {
            bail!("Private configuration must be named .env");
        }
        let mut values: BTreeMap<String, String> = if path.exists() {
            dotenvy::from_path_iter(&path)?.collect::<std::result::Result<_, _>>()?
        } else {
            BTreeMap::new()
        };
        if !values.contains_key("KINTLER_URL") {
            let origin = configured_origin()?.ok_or_else(|| anyhow!("Run KintlerSetup.exe first, then copy the generated Agent folder (EXE + kintler-agent.json) to this PC"))?;
            values.insert("KINTLER_URL".into(), origin);
        }
        let raw = if let Some(value) = values.get("KINTLER_AGENT_PRIVATE_KEY") {
            if let Some(data) = value.strip_prefix("dpapi:") {
                protect(&STANDARD.decode(data)?, true)?
            } else {
                bail!("Agent key must be DPAPI-protected on Windows");
            }
        } else {
            let mut key = [0u8; 32];
            rand::rngs::OsRng.fill_bytes(&mut key);
            values.insert(
                "KINTLER_AGENT_PRIVATE_KEY".into(),
                format!("dpapi:{}", STANDARD.encode(protect(&key, false)?)),
            );
            if let Some(parent) = path.parent() {
                fs::create_dir_all(parent)?;
            }
            let mut file = fs::OpenOptions::new()
                .write(true)
                .create(true)
                .truncate(true)
                .open(&path)?;
            for (key, value) in &values {
                writeln!(file, "{key}='{}'", value.replace('\'', "\\'"))?;
            }
            file.sync_all()?;
            key.to_vec()
        };
        let key = SigningKey::from_bytes(
            &raw.try_into()
                .map_err(|_| anyhow!("Invalid private key length"))?,
        );
        let tools = values
            .get("KINTLER_AGENT_TOOLS")
            .map(|s| s.split(',').map(|v| v.trim().to_string()).collect())
            .unwrap_or_else(|| TOOLS.iter().map(|s| s.to_string()).collect());
        let mut root = PathBuf::from(
            values
                .get("KINTLER_WORKSPACE")
                .cloned()
                .unwrap_or_else(|| "workspace".into()),
        );
        if !root.is_absolute() {
            root = path
                .parent()
                .unwrap_or(std::path::Path::new("."))
                .join(root);
        }
        fs::create_dir_all(&root)?;
        Ok(Self {
            url: values
                .get("KINTLER_URL")
                .cloned()
                .ok_or_else(|| anyhow!("Server URL is not configured"))?,
            node: node_id()?,
            name: values
                .get("KINTLER_NODE_NAME")
                .cloned()
                .unwrap_or(hostname::get()?.to_string_lossy().into()),
            root: fs::canonicalize(root)?,
            ca: values
                .get("KINTLER_CA_FILE")
                .filter(|v| !v.is_empty())
                .cloned(),
            local_http: values
                .get("KINTLER_ALLOW_LOCAL_HTTP")
                .is_some_and(|v| v == "1"),
            tools,
            key,
        })
    }

    pub fn auth(&self, challenge: &Value) -> Result<Value> {
        let public = STANDARD.encode(self.key.verifying_key().to_bytes());
        let body = json!({"v":1,"nonce":challenge["nonce"],"timestamp":challenge["timestamp"],"role":"agent","principal":self.node,"node_id":self.node,"auth":"ed25519","public_key":public});
        let signature = STANDARD.encode(self.key.sign(&serde_json::to_vec(&body)?).to_bytes());
        Ok(
            json!({"type":"authenticate","version":1,"nonce":challenge["nonce"],"timestamp":challenge["timestamp"],"role":"agent","principal":self.node,"node_id":self.node,"auth":"ed25519","public_key":public,"proof":signature}),
        )
    }
}

pub fn profile() -> PathBuf {
    let origin = configured_origin().ok().flatten().unwrap_or_default();
    let realm = format!("{:x}", Sha256::digest(origin.as_bytes()));
    PathBuf::from(std::env::var_os("LOCALAPPDATA").unwrap_or_default())
        .join("KintlerControl/agents")
        .join(&realm[..16])
        .join(".env")
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn dpapi_roundtrip() {
        let key = [7u8; 32];
        assert_eq!(protect(&protect(&key, false).unwrap(), true).unwrap(), key);
    }
    #[test]
    fn node_shape() {
        let id = node_id().unwrap();
        assert_eq!(id.len(), 21);
        assert!(id.starts_with("node_"));
    }
}
