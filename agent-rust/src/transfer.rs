use anyhow::{Result, bail};
use base64::{Engine, engine::general_purpose::STANDARD};
use rand::RngCore;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::HashMap,
    fs,
    io::{Read, Seek, SeekFrom, Write},
    os::windows::fs::MetadataExt,
    path::{Component, Path, PathBuf},
    time::{Duration, Instant},
};

pub const CHUNK: usize = 48 * 1024;
const MAX: u64 = 256 * 1024 * 1024;
struct Upload {
    path: PathBuf,
    destination: String,
    owner: String,
    size: u64,
    digest: String,
    offset: u64,
    updated: Instant,
}
pub struct Transfers {
    pub root: PathBuf,
    uploads: HashMap<String, Upload>,
}

pub fn digest(path: &Path) -> Result<String> {
    let mut file = fs::File::open(path)?;
    let mut hash = Sha256::new();
    let mut buffer = [0; 65536];
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        hash.update(&buffer[..count]);
    }
    Ok(format!("{:x}", hash.finalize()))
}

impl Transfers {
    pub fn new(root: PathBuf) -> Self {
        Self {
            root,
            uploads: HashMap::new(),
        }
    }
    fn path(&self, name: &str, exists: bool) -> Result<PathBuf> {
        if name.is_empty()
            || name.contains(['\\', ':', '\0'])
            || Path::new(name)
                .components()
                .any(|c| !matches!(c, Component::Normal(_) | Component::CurDir))
            || name.split('/').any(|p| p.starts_with(".kintler-"))
        {
            bail!("Use a relative workspace path with forward slashes");
        }
        let path = self.root.join(name);
        for entry in path.ancestors() {
            if entry == self.root {
                break;
            }
            if let Ok(metadata) = fs::symlink_metadata(entry)
                && metadata.file_attributes() & 0x400 != 0
            {
                bail!("Reparse points, symlinks and junctions are forbidden");
            }
        }
        if exists && !path.is_file() {
            bail!("File not found");
        }
        Ok(path)
    }
    pub fn clear(&mut self) {
        for (_, upload) in self.uploads.drain() {
            let _ = fs::remove_file(upload.path);
        }
    }
    pub fn upload(&mut self, args: &Value, owner: &str) -> Result<Value> {
        self.uploads.retain(|_, u| {
            if u.updated.elapsed() > Duration::from_secs(600) {
                let _ = fs::remove_file(&u.path);
                false
            } else {
                true
            }
        });
        let op = args["operation"].as_str().unwrap_or("");
        if op == "start" {
            if self.uploads.len() >= 8 {
                bail!("Upload capacity reached");
            }
            let destination = args["path"].as_str().unwrap_or("");
            let path = self.path(destination, false)?;
            let size = args["size"]
                .as_u64()
                .ok_or_else(|| anyhow::anyhow!("Invalid size"))?;
            let hash = args["sha256"].as_str().unwrap_or("");
            if size > MAX
                || hash.len() != 64
                || !hash
                    .bytes()
                    .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
                || self.uploads.values().any(|u| u.destination == destination)
            {
                bail!("Invalid size, SHA-256 or destination busy");
            }
            let mut random = [0; 16];
            rand::rngs::OsRng.fill_bytes(&mut random);
            let id = random
                .iter()
                .map(|v| format!("{v:02x}"))
                .collect::<String>();
            let temporary = path.parent().unwrap().join(format!(".kintler-{id}.part"));
            fs::OpenOptions::new()
                .create_new(true)
                .write(true)
                .open(&temporary)?;
            self.uploads.insert(
                id.clone(),
                Upload {
                    path: temporary,
                    destination: destination.into(),
                    owner: owner.into(),
                    size,
                    digest: hash.into(),
                    offset: 0,
                    updated: Instant::now(),
                },
            );
            return Ok(json!({"ok":true,"upload_id":id,"chunk_size":CHUNK}));
        }
        let id = args["upload_id"].as_str().unwrap_or("");
        let upload = self
            .uploads
            .get(id)
            .filter(|u| u.owner == owner)
            .ok_or_else(|| anyhow::anyhow!("Upload not found"))?;
        self.path(&upload.destination, false)?;
        if op == "abort" {
            let upload = self.uploads.remove(id).unwrap();
            fs::remove_file(upload.path)?;
            return Ok(json!({"ok":true}));
        }
        if op == "finish" {
            if upload.offset != upload.size || digest(&upload.path)? != upload.digest {
                bail!("Upload incomplete or SHA-256 mismatch");
            }
            let destination = self.path(&upload.destination, false)?;
            let overwrite = args["overwrite"].as_bool().unwrap_or(false);
            if destination.exists() && !overwrite {
                bail!("Destination exists; explicit overwrite required");
            }
            fs::OpenOptions::new()
                .append(true)
                .open(&upload.path)?
                .sync_all()?;
            if overwrite {
                fs::rename(&upload.path, &destination)?;
            } else {
                fs::hard_link(&upload.path, &destination)?;
                fs::remove_file(&upload.path)?;
            }
            let result = json!({"ok":true,"size":upload.size,"sha256":upload.digest});
            self.uploads.remove(id);
            return Ok(result);
        }
        if op != "chunk" {
            bail!("Unknown upload operation");
        }
        let upload = self.uploads.get_mut(id).unwrap();
        let data = STANDARD.decode(args["data"].as_str().unwrap_or(""))?;
        if data.is_empty()
            || data.len() > CHUNK
            || args["offset"].as_u64() != Some(upload.offset)
            || upload.offset + data.len() as u64 > upload.size
        {
            bail!("Invalid chunk or offset");
        }
        fs::OpenOptions::new()
            .append(true)
            .open(&upload.path)?
            .write_all(&data)?;
        upload.offset += data.len() as u64;
        upload.updated = Instant::now();
        Ok(json!({"ok":true,"offset":upload.offset}))
    }
    pub fn download(&self, args: &Value) -> Result<Value> {
        let path = self.path(args["path"].as_str().unwrap_or(""), true)?;
        let size = fs::metadata(&path)?.len();
        if size > MAX {
            bail!("File size limit exceeded");
        }
        if args["operation"].as_str().unwrap_or("stat") == "stat" {
            return Ok(json!({"ok":true,"size":size,"sha256":digest(&path)?,"chunk_size":CHUNK}));
        }
        if args["operation"] != "chunk" {
            bail!("Unknown download operation");
        }
        let offset = args["offset"].as_u64().unwrap_or(0);
        if offset > size {
            bail!("Invalid offset");
        }
        let mut file = fs::File::open(path)?;
        file.seek(SeekFrom::Start(offset))?;
        let mut data = vec![0; CHUNK];
        let count = file.read(&mut data)?;
        data.truncate(count);
        Ok(
            json!({"ok":true,"offset":offset,"data":STANDARD.encode(data),"eof":offset+count as u64>=size}),
        )
    }
}
impl Drop for Transfers {
    fn drop(&mut self) {
        self.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn workspace_traversal_and_chunk_ownership() {
        let path = std::env::temp_dir()
            .join("opencode")
            .join(format!("kintler-rust-transfer-{}", rand::random::<u64>()));
        fs::create_dir_all(&path).unwrap();
        let mut transfers = Transfers::new(path.clone());
        assert!(transfers.path("../outside", false).is_err());
        assert!(transfers.path("C:/outside", false).is_err());
        let data = b"rust-roundtrip";
        let hash = format!("{:x}", Sha256::digest(data));
        let start = transfers
            .upload(
                &json!({"operation":"start","path":"file.bin","size":data.len(),"sha256":hash}),
                "owner",
            )
            .unwrap();
        let id = &start["upload_id"];
        let chunk =
            json!({"operation":"chunk","upload_id":id,"offset":0,"data":STANDARD.encode(data)});
        assert!(transfers.upload(&chunk, "other").is_err());
        transfers.upload(&chunk, "owner").unwrap();
        let finish = transfers
            .upload(&json!({"operation":"finish","upload_id":id}), "owner")
            .unwrap();
        assert_eq!(finish["sha256"], hash);
        assert_eq!(fs::read(path.join("file.bin")).unwrap(), data);
        assert_eq!(
            transfers.download(&json!({"path":"file.bin"})).unwrap()["sha256"],
            hash
        );
        drop(transfers);
        fs::remove_dir_all(path).unwrap();
    }
}
