use std::{env, fs, path::PathBuf};

fn icon(path: &PathBuf) {
    let source = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").expect("Missing crate directory"))
        .join("../desktop/assets/kintler-agent.ico");
    fs::copy(source, path).expect("Cannot copy Agent icon");
}

fn main() {
    println!("cargo:rerun-if-env-changed=KINTLER_ICON_PATH");
    println!("cargo:rerun-if-changed=../desktop/assets/kintler-agent.ico");
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
