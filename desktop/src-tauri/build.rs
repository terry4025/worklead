fn main() {
    if std::env::var("PROFILE").as_deref() == Ok("release") && tauri_build::is_dev() {
        panic!("Release builds must embed frontend assets. Run npm run tauri build -- --no-bundle (or the Windows packaging script).");
    }
    tauri_build::build()
}
