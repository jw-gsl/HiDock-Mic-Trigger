#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AuthStatus {
    pub logged_in: bool,
    pub email: Option<String>,
    pub region: Option<String>,
    pub name: Option<String>,
}