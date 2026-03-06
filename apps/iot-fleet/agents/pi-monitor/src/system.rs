/// System metrics collected from /proc and /sys on Linux.

#[derive(Debug, Default, Clone)]
pub struct SystemMetrics {
    pub cpu_usage: f32,
    pub mem_total_kb: u64,
    pub mem_available_kb: u64,
    pub disk_total_kb: u64,
    pub disk_available_kb: u64,
    pub temperature: Option<f32>,
    pub load_1m: f32,
}

impl SystemMetrics {
    pub fn mem_usage_percent(&self) -> f32 {
        if self.mem_total_kb == 0 { return 0.0; }
        (1.0 - self.mem_available_kb as f32 / self.mem_total_kb as f32) * 100.0
    }

    pub fn disk_usage_percent(&self) -> f32 {
        if self.disk_total_kb == 0 { return 0.0; }
        (1.0 - self.disk_available_kb as f32 / self.disk_total_kb as f32) * 100.0
    }
}

pub async fn collector_loop(metrics: std::sync::Arc<tokio::sync::RwLock<SystemMetrics>>) {
    let mut prev_idle: u64 = 0;
    let mut prev_total: u64 = 0;

    loop {
        let mut m = SystemMetrics::default();

        // CPU usage from /proc/stat
        if let Ok(stat) = std::fs::read_to_string("/proc/stat") {
            if let Some(line) = stat.lines().next() {
                let vals: Vec<u64> = line.split_whitespace()
                    .skip(1)
                    .filter_map(|s| s.parse().ok())
                    .collect();
                if vals.len() >= 4 {
                    let idle = vals[3];
                    let total: u64 = vals.iter().sum();
                    let diff_idle = idle.saturating_sub(prev_idle);
                    let diff_total = total.saturating_sub(prev_total);
                    if diff_total > 0 {
                        m.cpu_usage = (1.0 - diff_idle as f32 / diff_total as f32) * 100.0;
                    }
                    prev_idle = idle;
                    prev_total = total;
                }
            }
        }

        // Memory from /proc/meminfo
        if let Ok(meminfo) = std::fs::read_to_string("/proc/meminfo") {
            for line in meminfo.lines() {
                if let Some(val) = parse_meminfo_kb(line, "MemTotal:") {
                    m.mem_total_kb = val;
                } else if let Some(val) = parse_meminfo_kb(line, "MemAvailable:") {
                    m.mem_available_kb = val;
                }
            }
        }

        // Disk usage from /proc/mounts + statvfs (simplified: read root)
        if let Ok(output) = std::process::Command::new("df")
            .args(["-k", "/"])
            .output()
        {
            if let Ok(s) = std::str::from_utf8(&output.stdout) {
                if let Some(line) = s.lines().nth(1) {
                    let parts: Vec<&str> = line.split_whitespace().collect();
                    if parts.len() >= 4 {
                        m.disk_total_kb = parts[1].parse().unwrap_or(0);
                        m.disk_available_kb = parts[3].parse().unwrap_or(0);
                    }
                }
            }
        }

        // Temperature from thermal zone
        if let Ok(temp_str) = std::fs::read_to_string("/sys/class/thermal/thermal_zone0/temp") {
            if let Ok(millideg) = temp_str.trim().parse::<f32>() {
                m.temperature = Some(millideg / 1000.0);
            }
        }

        // Load average
        if let Ok(loadavg) = std::fs::read_to_string("/proc/loadavg") {
            if let Some(first) = loadavg.split_whitespace().next() {
                m.load_1m = first.parse().unwrap_or(0.0);
            }
        }

        *metrics.write().await = m;
        tokio::time::sleep(std::time::Duration::from_secs(5)).await;
    }
}

fn parse_meminfo_kb(line: &str, prefix: &str) -> Option<u64> {
    if !line.starts_with(prefix) { return None; }
    line[prefix.len()..].split_whitespace().next()?.parse().ok()
}
