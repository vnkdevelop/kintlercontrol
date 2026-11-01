use std::{env, fs, path::PathBuf};

fn icon(path: &PathBuf) {
    let size = 64u32;
    let mask = size * size / 8;
    let image_size = 40 + size * size * 4 + mask;
    let mut bytes = Vec::new();
    bytes.extend_from_slice(&[0, 0, 1, 0, 1, 0, 64, 64, 0, 0, 1, 0, 32, 0]);
    bytes.extend_from_slice(&image_size.to_le_bytes());
    bytes.extend_from_slice(&22u32.to_le_bytes());
    bytes.extend_from_slice(&40u32.to_le_bytes());
    bytes.extend_from_slice(&size.to_le_bytes());
    bytes.extend_from_slice(&(size * 2).to_le_bytes());
    bytes.extend_from_slice(&1u16.to_le_bytes());
    bytes.extend_from_slice(&32u16.to_le_bytes());
    bytes.extend_from_slice(&[0; 24]);
    for y in (0..size).rev() {
        for x in 0..size {
            let u = (x as f64 - y as f64) / std::f64::consts::SQRT_2;
            let v = (x as f64 + y as f64 - 64.0) / std::f64::consts::SQRT_2;
            let leaf = (u / 28.0).powi(2) + (v / 14.0).powi(2) <= 1.0;
            let vein = leaf && (x as i32 + y as i32 - 64).abs() <= 2;
            let color = if vein {
                [180, 221, 164, 255]
            } else if leaf {
                [67, 105, 36, 255]
            } else {
                [11, 14, 9, 255]
            };
            bytes.extend_from_slice(&color);
        }
    }
    bytes.resize(bytes.len() + mask as usize, 0);
    fs::write(path, bytes).expect("Cannot write build icon");
}

fn main() {
    println!("cargo:rerun-if-env-changed=KINTLER_ICON_PATH");
    if env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows") {
        let path = env::var_os("KINTLER_ICON_PATH")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                let path =
                    PathBuf::from(env::var_os("OUT_DIR").expect("Missing build output directory"))
                        .join("kintler-leaf.ico");
                icon(&path);
                path
            });
        let mut resource = winresource::WindowsResource::new();
        resource.set_icon(path.to_str().expect("Invalid icon path"));
        resource.compile().expect("Cannot embed Windows leaf icon");
    }
}
