use std::fs::OpenOptions;
use std::io::Write;
use std::path::PathBuf;
use std::sync::Mutex;

use chrono::Utc;

static LOG_PATH: Mutex<Option<PathBuf>> = Mutex::new(None);

pub fn init(path: PathBuf) {
    let log_file = path.join("login-debug.log");
    if let Ok(mut guard) = LOG_PATH.lock() {
        *guard = Some(log_file.clone());
    }
    info("login debug logging initialized");
}

pub fn path() -> Option<PathBuf> {
    LOG_PATH.lock().ok().and_then(|g| g.clone())
}

pub fn debug(message: &str) {
    write_line("DEBUG", message);
}

pub fn info(message: &str) {
    write_line("INFO", message);
}

pub fn warn(message: &str) {
    write_line("WARN", message);
}

pub fn error(message: &str) {
    write_line("ERROR", message);
}

fn write_line(level: &str, message: &str) {
    let line = format!(
        "[{}] {level}: {message}",
        Utc::now().format("%Y-%m-%d %H:%M:%S")
    );
    eprintln!("[plaud-login] {line}");

    if let Ok(guard) = LOG_PATH.lock() {
        if let Some(path) = guard.as_ref() {
            if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(path) {
                let _ = writeln!(file, "{line}");
            }
        }
    }
}