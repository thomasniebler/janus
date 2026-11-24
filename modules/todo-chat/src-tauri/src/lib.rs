use futures_util::{SinkExt, StreamExt};
use keyring::Entry;
use reqwest::{
    cookie::{CookieStore, Jar},
    header::{HeaderValue, ORIGIN},
    Client, StatusCode, Url,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    env, fs,
    path::PathBuf,
    sync::{Arc, Mutex as StdMutex},
    time::Duration,
};
use tauri::{Manager, PhysicalPosition, PhysicalSize, State};
use tokio::sync::Mutex;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;

const KEYRING_SERVICE: &str = "todo-chat-janus-session";
const KEYRING_USER: &str = "session-material";

#[derive(Clone, Default, Serialize, Deserialize)]
pub struct ConnectionSettings {
    pub base_url: String,
    pub conversation_id: Option<i64>,
    pub connected: bool,
    pub username: Option<String>,
}

struct Session {
    settings: ConnectionSettings,
    csrf_token: Option<String>,
    jar: Arc<Jar>,
    client: Client,
    restore_pending: bool,
}

struct AppState(Mutex<Session>);

struct SidebarVisibility(StdMutex<bool>);

/// Janus config the companion reads. Unknown keys (such as the retired
/// `[todo-chat] agent_id`) are ignored: chat always speaks as the user's
/// Janus character, so there is no agent to choose.
#[derive(Default, Deserialize)]
struct TodoChatConfig {
    base_url: Option<String>,
}

#[derive(Debug, PartialEq, Eq)]
enum RestoreValidationOutcome {
    Authenticated,
    Unauthenticated,
}

fn normalize_base_url(value: &str) -> Result<String, String> {
    let trimmed = value.trim().trim_end_matches('/');
    let parsed = Url::parse(trimmed).map_err(|_| "Janus URL is invalid".to_string())?;
    if !matches!(parsed.scheme(), "http" | "https") || parsed.host().is_none() {
        return Err("Janus URL must use http:// or https://".to_string());
    }
    Ok(trimmed.to_string())
}

fn endpoint(base_url: &str, path: &str) -> String {
    format!("{base_url}{path}")
}

fn todo_chat_config_path() -> PathBuf {
    let config_root = env::var_os("XDG_CONFIG_HOME")
        .map(PathBuf::from)
        .or_else(|| env::var_os("HOME").map(|home| PathBuf::from(home).join(".config")))
        .unwrap_or_else(|| PathBuf::from(".config"));
    config_root.join("janus").join("config.toml")
}

fn load_todo_chat_config() -> TodoChatConfig {
    fs::read_to_string(todo_chat_config_path())
        .ok()
        .and_then(|contents| toml::from_str(&contents).ok())
        .unwrap_or_default()
}

fn session_entry() -> Result<Entry, String> {
    Entry::new(KEYRING_SERVICE, KEYRING_USER)
        .map_err(|error| format!("keyring unavailable: {error}"))
}

fn persist_session_material(material: Value) {
    let Ok(entry) = session_entry() else {
        return;
    };
    let _ = entry.set_password(&material.to_string());
}

fn persist_authenticated_session(session: &Session) {
    let cookie = Url::parse(&session.settings.base_url)
        .ok()
        .and_then(|url| session.jar.cookies(&url))
        .and_then(|value| value.to_str().ok().map(str::to_string))
        .unwrap_or_default();
    persist_session_material(json!({
        "base_url": session.settings.base_url,
        "username": session.settings.username,
        "csrf_token": session.csrf_token,
        "cookie": cookie,
    }));
}

fn build_client(jar: Arc<Jar>) -> Client {
    Client::builder()
        .cookie_provider(jar)
        .build()
        .unwrap_or_else(|_| Client::new())
}

fn restored_session_material() -> Option<Value> {
    session_entry()
        .ok()
        .and_then(|entry| entry.get_password().ok())
        .and_then(|raw| serde_json::from_str::<Value>(&raw).ok())
}

fn session_from_material(material: Option<Value>) -> Session {
    let jar = Arc::new(Jar::default());
    let configured = load_todo_chat_config();
    let configured_base_url = configured
        .base_url
        .as_deref()
        .and_then(|value| normalize_base_url(value).ok());
    let mut settings = ConnectionSettings {
        base_url: configured_base_url
            .clone()
            .unwrap_or_else(|| "http://localhost:8080".into()),
        ..Default::default()
    };
    let mut csrf_token = None;
    if let Some(material) = material {
        if configured_base_url.is_none() {
            if let Some(base_url) = material.get("base_url").and_then(Value::as_str) {
                if let Ok(normalized) = normalize_base_url(base_url) {
                    settings.base_url = normalized;
                }
            }
        }
        settings.username = material
            .get("username")
            .and_then(Value::as_str)
            .map(str::to_string);
        csrf_token = material
            .get("csrf_token")
            .and_then(Value::as_str)
            .map(str::to_string);
        if let (Some(cookie), Ok(url)) = (
            material.get("cookie").and_then(Value::as_str),
            Url::parse(&settings.base_url),
        ) {
            jar.add_cookie_str(cookie, &url);
        }
    }
    let restore_pending = csrf_token.is_some();
    settings.connected = false;
    let client = build_client(jar.clone());
    Session {
        settings,
        csrf_token,
        jar,
        client,
        restore_pending,
    }
}

fn initial_session() -> Session {
    session_from_material(restored_session_material())
}

async fn request_json(
    session: &Session,
    method: reqwest::Method,
    path: &str,
    body: Option<Value>,
) -> Result<Value, String> {
    let mut request = session
        .client
        .request(method, endpoint(&session.settings.base_url, path));
    if let Some(token) = &session.csrf_token {
        request = request.header("X-CSRF-Token", token);
    }
    if let Some(payload) = body {
        request = request.json(&payload);
    }
    let response = request.send().await.map_err(|error| error.to_string())?;
    let status = response.status();
    let value = response
        .json::<Value>()
        .await
        .map_err(|error| error.to_string())?;
    if !status.is_success() {
        return Err(value
            .get("detail")
            .and_then(Value::as_str)
            .unwrap_or("Janus request failed")
            .to_string());
    }
    Ok(value)
}

fn parse_restore_validation_response(
    status: StatusCode,
    value: Value,
) -> Result<RestoreValidationOutcome, String> {
    if status.is_success() {
        return Ok(RestoreValidationOutcome::Authenticated);
    }
    if matches!(status, StatusCode::UNAUTHORIZED | StatusCode::FORBIDDEN) {
        return Ok(RestoreValidationOutcome::Unauthenticated);
    }
    Err(value
        .get("detail")
        .and_then(Value::as_str)
        .unwrap_or("Janus request failed")
        .to_string())
}

fn clear_authenticated_session(session: &mut Session) {
    let base_url = session.settings.base_url.clone();
    let jar = Arc::new(Jar::default());
    session.client = build_client(jar.clone());
    session.jar = jar;
    session.csrf_token = None;
    session.restore_pending = false;
    session.settings.connected = false;
    session.settings.username = None;
    session.settings.conversation_id = None;
    persist_session_material(json!({"base_url": base_url}));
}

fn apply_restore_validation(session: &mut Session, outcome: RestoreValidationOutcome) {
    match outcome {
        RestoreValidationOutcome::Authenticated => {
            session.restore_pending = false;
            session.settings.connected = session.csrf_token.is_some();
        }
        RestoreValidationOutcome::Unauthenticated => clear_authenticated_session(session),
    }
}

async fn validate_restored_session(session: &Session) -> Result<RestoreValidationOutcome, String> {
    let response = session
        .client
        .get(endpoint(&session.settings.base_url, "/api/auth/me"))
        .send()
        .await
        .map_err(|error| error.to_string())?;
    let status = response.status();
    let value = response
        .json::<Value>()
        .await
        .map_err(|error| error.to_string())?;
    parse_restore_validation_response(status, value)
}

#[tauri::command]
async fn get_connection_settings(state: State<'_, AppState>) -> Result<ConnectionSettings, String> {
    let snapshot = {
        let session = state.0.lock().await;
        if !session.restore_pending {
            return Ok(session.settings.clone());
        }
        Session {
            settings: session.settings.clone(),
            csrf_token: session.csrf_token.clone(),
            jar: session.jar.clone(),
            client: session.client.clone(),
            restore_pending: session.restore_pending,
        }
    };
    let outcome = validate_restored_session(&snapshot).await?;
    let mut session = state.0.lock().await;
    if session.restore_pending {
        apply_restore_validation(&mut session, outcome);
    }
    Ok(session.settings.clone())
}

#[tauri::command]
async fn save_connection_settings(
    state: State<'_, AppState>,
    base_url: String,
) -> Result<ConnectionSettings, String> {
    let mut session = state.0.lock().await;
    session.settings.base_url = normalize_base_url(&base_url)?;
    session.settings.connected = false;
    session.restore_pending = false;
    session.settings.username = None;
    session.settings.conversation_id = None;
    session.csrf_token = None;
    Ok(session.settings.clone())
}

#[tauri::command]
async fn login(
    state: State<'_, AppState>,
    base_url: String,
    username: String,
    password: String,
) -> Result<ConnectionSettings, String> {
    let normalized = normalize_base_url(&base_url)?;
    let jar = Arc::new(Jar::default());
    let client = Client::builder()
        .cookie_provider(jar.clone())
        .build()
        .map_err(|error| error.to_string())?;
    let response = client
        .post(endpoint(&normalized, "/api/auth/login"))
        .json(&json!({"username": username, "password": password}))
        .send()
        .await
        .map_err(|error| error.to_string())?;
    let status = response.status();
    let payload = response
        .json::<Value>()
        .await
        .map_err(|error| error.to_string())?;
    if !status.is_success() {
        return Err(payload
            .get("detail")
            .and_then(Value::as_str)
            .unwrap_or("Login failed")
            .to_string());
    }
    let csrf_token = payload
        .get("csrf_token")
        .and_then(Value::as_str)
        .map(str::to_string);
    let mut session = state.0.lock().await;
    session.client = client;
    session.jar = jar;
    session.csrf_token = csrf_token;
    session.restore_pending = false;
    session.settings = ConnectionSettings {
        base_url: normalized,
        conversation_id: session.settings.conversation_id,
        connected: true,
        username: Some(username),
    };
    if session.csrf_token.is_some() {
        persist_authenticated_session(&session);
    }
    Ok(session.settings.clone())
}

#[tauri::command]
async fn list_local_tasks(
    state: State<'_, AppState>,
    include_completed: bool,
) -> Result<Vec<Value>, String> {
    let session = state.0.lock().await;
    let value = request_json(
        &session,
        reqwest::Method::GET,
        &format!("/api/planner/local/tasks/tree?include_completed={include_completed}"),
        None,
    )
    .await?;
    value
        .as_array()
        .cloned()
        .ok_or_else(|| "Unexpected task response from Janus".to_string())
}

#[tauri::command]
async fn list_conversations(state: State<'_, AppState>) -> Result<Vec<Value>, String> {
    let session = state.0.lock().await;
    let value = request_json(&session, reqwest::Method::GET, "/api/conversations/", None).await?;
    value
        .as_array()
        .cloned()
        .ok_or_else(|| "Unexpected conversation response from Janus".to_string())
}

#[tauri::command]
async fn set_conversation_id(
    state: State<'_, AppState>,
    conversation_id: Option<i64>,
) -> Result<ConnectionSettings, String> {
    let mut session = state.0.lock().await;
    session.settings.conversation_id = conversation_id;
    Ok(session.settings.clone())
}

#[tauri::command]
async fn create_local_task(
    state: State<'_, AppState>,
    title: String,
    parent_task_id: Option<i64>,
    urgency: String,
) -> Result<Value, String> {
    let session = state.0.lock().await;
    request_json(
        &session,
        reqwest::Method::POST,
        "/api/planner/local/tasks",
        Some(json!({"title": title, "parent_task_id": parent_task_id, "urgency": urgency})),
    )
    .await
}

#[tauri::command]
async fn update_local_task(
    state: State<'_, AppState>,
    task_id: i64,
    patch: Value,
) -> Result<Value, String> {
    let session = state.0.lock().await;
    request_json(
        &session,
        reqwest::Method::PATCH,
        &format!("/api/planner/local/tasks/{task_id}"),
        Some(patch),
    )
    .await
}

#[tauri::command]
async fn complete_local_task(state: State<'_, AppState>, task_id: i64) -> Result<Value, String> {
    let session = state.0.lock().await;
    request_json(
        &session,
        reqwest::Method::POST,
        &format!("/api/planner/local/tasks/{task_id}/complete"),
        Some(json!({})),
    )
    .await
}

fn build_chat_request(
    url: &str,
    cookie: Option<reqwest::header::HeaderValue>,
    origin: HeaderValue,
) -> Result<tokio_tungstenite::tungstenite::handshake::client::Request, String> {
    // Build from the URL so tungstenite adds the mandatory WebSocket handshake
    // headers, including Sec-WebSocket-Key. Passing a bare HTTP request skips
    // that generation and causes the server to reject the connection.
    let mut request = url
        .into_client_request()
        .map_err(|error| error.to_string())?;
    if let Some(cookie) = cookie {
        request.headers_mut().insert("Cookie", cookie);
    }
    request.headers_mut().insert(ORIGIN, origin);
    Ok(request)
}

fn websocket_origin(base_url: &str) -> Result<HeaderValue, String> {
    let parsed = Url::parse(base_url).map_err(|_| "Janus URL is invalid".to_string())?;
    HeaderValue::from_str(&parsed.origin().ascii_serialization())
        .map_err(|error| format!("Janus origin is invalid: {error}"))
}

fn should_show_on_launch(args: impl IntoIterator<Item = String>) -> bool {
    args.into_iter().any(|arg| arg == "--toggle")
}

const SIDEBAR_WIDTH: u32 = 440;

fn sidebar_x(monitor_x: i32, monitor_width: u32) -> i32 {
    monitor_x + monitor_width.saturating_sub(SIDEBAR_WIDTH) as i32
}

fn place_as_sidebar(window: &tauri::WebviewWindow) {
    let monitor = window
        .available_monitors()
        .ok()
        .and_then(|monitors| {
            monitors
                .into_iter()
                .max_by_key(|monitor| monitor.position().x)
        })
        .or_else(|| window.current_monitor().ok().flatten());
    let Some(monitor) = monitor else {
        return;
    };
    let monitor_size = monitor.size();
    let monitor_position = monitor.position();
    let x = sidebar_x(monitor_position.x, monitor_size.width);

    let _ = window.set_size(PhysicalSize::new(SIDEBAR_WIDTH, monitor_size.height));
    let _ = window.set_position(PhysicalPosition::new(x, monitor_position.y));
}

fn set_sidebar_visibility(app: &tauri::AppHandle, visible: bool) {
    let Some(window) = app.get_webview_window("main") else {
        return;
    };
    if visible {
        place_as_sidebar(&window);
        let _ = window.show();
        let _ = window.set_focus();
    } else {
        let _ = window.hide();
    }
    if let Ok(mut state) = app.state::<SidebarVisibility>().0.lock() {
        *state = visible;
    }
}

fn toggle_sidebar(app: &tauri::AppHandle) {
    let visible = app
        .state::<SidebarVisibility>()
        .0
        .lock()
        .map(|state| !*state)
        .unwrap_or(true);
    set_sidebar_visibility(app, visible);
}

#[tauri::command]
fn hide_sidebar(app: tauri::AppHandle) {
    set_sidebar_visibility(&app, false);
}

#[tauri::command]
async fn send_chat(
    state: State<'_, AppState>,
    text: String,
    page_context: String,
) -> Result<String, String> {
    let mut session = state.0.lock().await;
    let ws_url = session
        .settings
        .base_url
        .replace("https://", "wss://")
        .replace("http://", "ws://");
    let path = match session.settings.conversation_id {
        Some(id) => format!("/ws/chat?conversation_id={id}"),
        None => "/ws/chat".to_string(),
    };
    let cookie = session
        .jar
        .cookies(&Url::parse(&session.settings.base_url).map_err(|error| error.to_string())?);
    let origin = websocket_origin(&session.settings.base_url)?;
    let request = build_chat_request(&format!("{ws_url}{path}"), cookie, origin)?;
    let (mut socket, _) = tokio_tungstenite::connect_async(request)
        .await
        .map_err(|error| error.to_string())?;
    socket
        .send(tokio_tungstenite::tungstenite::Message::Text(
            json!({"text": text, "page_context": page_context})
                .to_string()
                .into(),
        ))
        .await
        .map_err(|error| error.to_string())?;
    let mut response = String::new();
    while let Some(message) = tokio::time::timeout(Duration::from_secs(120), socket.next())
        .await
        .map_err(|_| "Janus chat timed out".to_string())?
        .transpose()
        .map_err(|error| error.to_string())?
    {
        let text = message.into_text().map_err(|error| error.to_string())?;
        if let Some(id) = text.strip_prefix("__CONVERSATION_ID__:") {
            session.settings.conversation_id = id.parse().ok();
            continue;
        }
        if text == "__START__" {
            continue;
        }
        if text == "__END__" {
            break;
        }
        if let Some(error) = text.strip_prefix("__ERROR__:") {
            return Err(error.to_string());
        }
        if text.starts_with("__TOOL_") {
            continue;
        }
        response.push_str(&text);
    }
    Ok(response)
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, args, _cwd| {
            if !args.iter().any(|arg| arg == "--toggle") {
                return;
            }
            toggle_sidebar(app);
        }))
        .manage(AppState(Mutex::new(initial_session())))
        .manage(SidebarVisibility(StdMutex::new(false)))
        .invoke_handler(tauri::generate_handler![
            get_connection_settings,
            save_connection_settings,
            login,
            list_local_tasks,
            create_local_task,
            update_local_task,
            complete_local_task,
            list_conversations,
            set_conversation_id,
            send_chat,
            hide_sidebar
        ])
        .setup(|app| {
            app.get_webview_window("main")
                .ok_or("main window missing")?;
            let visible = should_show_on_launch(std::env::args());
            set_sidebar_visibility(app.handle(), visible);
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Todo Chat");
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::thread;

    fn run_async_test(test: impl std::future::Future<Output = ()>) {
        tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .expect("build tokio runtime")
            .block_on(test);
    }

    #[test]
    fn restored_sessions_start_disconnected_until_validation_succeeds() {
        let session = session_from_material(Some(json!({
            "base_url": "http://localhost:8080",
            "username": "alice",
            "agent_id": 7,
            "csrf_token": "stored-token",
            "cookie": "access_token=abc123",
        })));

        assert!(!session.settings.connected);
        assert!(session.restore_pending);
        assert_eq!(session.settings.username.as_deref(), Some("alice"));
        assert_eq!(session.csrf_token.as_deref(), Some("stored-token"));
    }

    #[test]
    fn chat_request_includes_websocket_handshake_headers() {
        let request = build_chat_request(
            "ws://localhost:8080/ws/chat?conversation_id=4",
            None,
            websocket_origin("http://localhost:8080").expect("valid origin"),
        )
        .expect("valid WebSocket request");

        assert_eq!(request.method(), "GET");
        assert_eq!(request.headers().get("Connection").unwrap(), "Upgrade");
        assert_eq!(request.headers().get("Upgrade").unwrap(), "websocket");
        assert_eq!(
            request.headers().get("Sec-WebSocket-Version").unwrap(),
            "13"
        );
        assert!(request.headers().contains_key("Sec-WebSocket-Key"));
        assert_eq!(
            request.headers().get(ORIGIN).unwrap(),
            "http://localhost:8080"
        );
    }

    #[test]
    fn toggle_argument_shows_a_new_instance() {
        assert!(should_show_on_launch([
            "todo-chat".to_string(),
            "--toggle".to_string()
        ]));
        assert!(!should_show_on_launch(["todo-chat".to_string()]));
    }

    #[test]
    fn sidebar_position_uses_the_monitor_right_edge() {
        assert_eq!(sidebar_x(1920, 3440), 4920);
        assert_eq!(sidebar_x(0, 320), 0);
    }

    #[test]
    fn parses_global_base_url_and_ignores_the_retired_agent_id() {
        let config: TodoChatConfig =
            toml::from_str("base_url = 'https://janus.example.com'\n[todo-chat]\nagent_id = 42\n")
                .expect("a config with the retired agent_id still parses");

        assert_eq!(
            config.base_url.as_deref(),
            Some("https://janus.example.com")
        );
    }

    fn test_session(base_url: &str, cookie: &str) -> Session {
        let jar = Arc::new(Jar::default());
        let url = Url::parse(base_url).expect("valid base url");
        jar.add_cookie_str(cookie, &url);
        Session {
            settings: ConnectionSettings {
                base_url: base_url.to_string(),
                conversation_id: Some(9),
                connected: false,
                username: Some("janus-user".to_string()),
            },
            csrf_token: Some("csrf-token".to_string()),
            jar: jar.clone(),
            client: build_client(jar),
            restore_pending: true,
        }
    }

    fn spawn_json_server(
        status_line: &str,
        body: &'static str,
    ) -> (String, thread::JoinHandle<String>) {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind test server");
        let address = listener.local_addr().expect("server address");
        let status_line = status_line.to_string();
        let handle = thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("accept request");
            let mut buffer = [0_u8; 4096];
            let read = stream.read(&mut buffer).expect("read request");
            let request = String::from_utf8_lossy(&buffer[..read]).to_string();
            let response = format!(
                "HTTP/1.1 {status_line}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            );
            stream
                .write_all(response.as_bytes())
                .expect("write response");
            request
        });
        (format!("http://{address}"), handle)
    }

    #[test]
    fn validate_restored_session_accepts_valid_sessions() {
        run_async_test(async {
            let (base_url, server) = spawn_json_server("200 OK", r#"{"username":"janus-user"}"#);
            let session = test_session(&base_url, "access_token=valid");

            let outcome = validate_restored_session(&session)
                .await
                .expect("validation succeeds");

            assert_eq!(outcome, RestoreValidationOutcome::Authenticated);
            let request = server.join().expect("join server");
            let normalized_request = request.to_ascii_lowercase();
            assert!(normalized_request.starts_with("get /api/auth/me http/1.1"));
            assert!(normalized_request.contains("cookie: access_token=valid"));
        });
    }

    #[test]
    fn validate_restored_session_treats_expired_or_revoked_sessions_as_disconnected() {
        run_async_test(async {
            let (base_url, _server) =
                spawn_json_server("401 Unauthorized", r#"{"detail":"Not authenticated"}"#);
            let mut session = test_session(&base_url, "access_token=expired");

            let outcome = validate_restored_session(&session)
                .await
                .expect("401 should not bubble as an error");
            apply_restore_validation(&mut session, outcome);

            assert!(!session.settings.connected);
            assert!(!session.restore_pending);
            assert_eq!(session.settings.username, None);
            assert_eq!(session.settings.conversation_id, None);
            assert_eq!(session.csrf_token, None);
        });
    }

    #[test]
    fn validate_restored_session_preserves_server_errors() {
        run_async_test(async {
            let (base_url, _server) = spawn_json_server(
                "500 Internal Server Error",
                r#"{"detail":"Janus is unavailable"}"#,
            );
            let session = test_session(&base_url, "access_token=valid");

            let error = validate_restored_session(&session)
                .await
                .expect_err("server errors should surface");

            assert_eq!(error, "Janus is unavailable");
        });
    }
}
