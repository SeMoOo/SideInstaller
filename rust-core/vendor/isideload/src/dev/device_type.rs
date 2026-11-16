use plist::{Dictionary, Value};

#[derive(Debug, Clone)]
pub enum DeveloperDeviceType {
    Any,
    Ios,
    Tvos,
    Watchos,
}

impl DeveloperDeviceType {
    pub fn url_segment(&self) -> &'static str {
        match self {
            DeveloperDeviceType::Any => "",
            DeveloperDeviceType::Ios => "ios/",
            DeveloperDeviceType::Tvos => "tvos/",
            // Apple routes watchOS registration through ios/ with DTDK_Platform.
            DeveloperDeviceType::Watchos => "ios/",
        }
    }
}

pub fn apply_platform_to_body(body: &mut Dictionary, device_type: &Option<DeveloperDeviceType>) {
    if matches!(device_type, Some(DeveloperDeviceType::Watchos)) {
        body.insert("DTDK_Platform".into(), Value::String("watchos".into()));
    }
}

pub fn dev_url(endpoint: &str, device_type: impl Into<Option<DeveloperDeviceType>>) -> String {
    format!(
        "https://developerservices2.apple.com/services/QH65B2/{}{}.action?clientId=XABBG36SBA",
        device_type
            .into()
            .unwrap_or(DeveloperDeviceType::Ios)
            .url_segment(),
        endpoint,
    )
}

/// URL of `path` on Xcode's JSON:API endpoint (`services/v1`), which
/// `DeveloperSession::send_services_request` talks to.
pub fn services_url(path: &str) -> String {
    format!("https://developerservices2.apple.com/services/v1/{path}")
}

#[cfg(test)]
mod watch_platform_tests {
    use super::*;
    #[test]
    fn watch_api_uses_ios_endpoint_and_platform_marker() {
        let url = dev_url("listDevices", Some(DeveloperDeviceType::Watchos));
        assert!(url.contains("/ios/listDevices.action"));
        let mut body = Dictionary::new();
        apply_platform_to_body(&mut body, &Some(DeveloperDeviceType::Watchos));
        assert_eq!(body.get("DTDK_Platform").and_then(Value::as_string), Some("watchos"));
    }
}
