use crate::{identity::Config, tools, transfer::Transfers};
use anyhow::{Result, anyhow, bail};
use futures_util::{SinkExt, StreamExt};
use serde_json::{Value, json};
use std::{
    collections::{HashMap, HashSet},
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio::sync::mpsc;
use tokio_tungstenite::{
    Connector, connect_async_tls_with_config,
    tungstenite::{Message, protocol::WebSocketConfig},
};
use tokio_util::sync::CancellationToken;

pub type Events = std::sync::mpsc::Sender<String>;
fn event(events: &Events, text: impl Into<String>) {
    let _ = events.send(text.into());
}

pub async fn connection(
    config: Arc<Config>,
    events: Events,
    stop: CancellationToken,
) -> Result<bool> {
    let uri: tokio_tungstenite::tungstenite::http::Uri = config.url.parse()?;
    let secure = uri.scheme_str() == Some("https");
    let host = uri.host().unwrap_or("");
    if uri.authority().is_none()
        || uri.authority().unwrap().as_str().contains('@')
        || uri.path_and_query().is_some_and(|p| p.as_str() != "/")
        || (!secure
            && !(config.local_http
                && ["localhost", "127.0.0.1", "::1"].contains(&host)
                && uri.scheme_str() == Some("http")))
    {
        bail!("HTTPS server origin is required; no credentials or query in URL");
    }
    let mut roots = rustls::RootCertStore::empty();
    for certificate in rustls_native_certs::load_native_certs().certs {
        let _ = roots.add(certificate);
    }
    if let Some(path) = &config.ca {
        let mut file = std::io::BufReader::new(std::fs::File::open(path)?);
        for certificate in rustls_pemfile::certs(&mut file) {
            roots.add(certificate?)?;
        }
    }
    let tls = rustls::ClientConfig::builder_with_protocol_versions(&[&rustls::version::TLS13])
        .with_root_certificates(roots)
        .with_no_client_auth();
    let url = format!(
        "{}/ws/agent",
        config
            .url
            .trim_end_matches('/')
            .replacen("https://", "wss://", 1)
            .replacen("http://", "ws://", 1)
    );
    let ws_config = WebSocketConfig::default()
        .max_message_size(Some(256 * 1024))
        .max_frame_size(Some(256 * 1024));
    event(&events, "Подключение к серверу…");
    let (mut socket, _) = tokio::select! {_ = stop.cancelled()=>return Ok(false),value=tokio::time::timeout(Duration::from_secs(30),connect_async_tls_with_config(&url,Some(ws_config),true,Some(Connector::Rustls(Arc::new(tls)))))=>value??};
    let challenge = tokio::time::timeout(Duration::from_secs(30), socket.next())
        .await?
        .ok_or_else(|| anyhow!("Server closed handshake"))??;
    let challenge: Value = serde_json::from_str(challenge.to_text()?)?;
    if challenge["type"] != "challenge" {
        bail!("Expected authentication challenge");
    }
    let mut system = sysinfo::System::new_all();
    let mut login = config.auth(&challenge)?;
    login["metadata"] = tools::metadata(&config, &mut system);
    socket.send(Message::Text(login.to_string().into())).await?;
    loop {
        let response = tokio::select! {_ = stop.cancelled()=>{let _=socket.close(None).await;return Ok(false);},value=tokio::time::timeout(Duration::from_secs(630),socket.next())=>value?.ok_or_else(||anyhow!("Server closed before approval"))??};
        if let Message::Ping(data) = response {
            socket.send(Message::Pong(data)).await?;
            continue;
        }
        let response: Value = serde_json::from_str(response.to_text()?)?;
        match response["type"].as_str().unwrap_or("") {
            "approval_required" => event(
                &events,
                format!(
                    "Ожидает вашего подтверждения в контроллере. Код: {}",
                    response["code"].as_str().unwrap_or("")
                ),
            ),
            "authenticated" => break,
            "approval_denied" => {
                event(
                    &events,
                    "Подключение отклонено или время ожидания истекло. Нажмите «Подключить снова».",
                );
                return Ok(false);
            }
            _ => bail!("Authentication/approval failed"),
        }
    }
    event(
        &events,
        format!(
            "Online — {}. Каждое переподключение требует нового подтверждения.",
            config.node
        ),
    );
    let transfers = Arc::new(Mutex::new(Transfers::new(config.root.clone())));
    let (sender, mut output) = mpsc::channel::<Value>(128);
    let mut jobs: HashMap<String, (String, CancellationToken, tokio::task::JoinHandle<()>)> =
        HashMap::new();
    let mut seen = HashSet::new();
    let mut pulse = tokio::time::interval(Duration::from_secs(10));
    let mut denied = false;
    let outcome:Result<()>=async {
        loop {tokio::select!{
            _=stop.cancelled()=>break,
            _=pulse.tick()=>socket.send(Message::Text(json!({"type":"heartbeat","metadata":tools::metadata(&config,&mut system)}).to_string().into())).await?,
            Some(value)=output.recv()=>{if value["type"]=="result"{jobs.remove(value["command_id"].as_str().unwrap_or(""));}socket.send(Message::Text(value.to_string().into())).await?;},
            frame=socket.next()=>{
                let Some(frame)=frame else{break;};let frame=frame?;
                if let Message::Close(reason)=frame{denied=reason.is_some_and(|v|u16::from(v.code)==4001);break;}
                if let Message::Ping(data)=frame{socket.send(Message::Pong(data)).await?;continue;}
                if !frame.is_text(){continue;}let message:Value=serde_json::from_str(frame.to_text()?)?;
                if message["type"]=="heartbeat"{continue;}
                if message["target_node_id"]!=config.node{bail!("Incorrect command target");}
                let id=message["command_id"].as_str().ok_or_else(||anyhow!("Invalid command ID"))?.to_string();let owner=message["session_id"].as_str().ok_or_else(||anyhow!("Invalid session ID"))?.to_string();
                if message["type"]=="cancel"{if let Some((session,cancel,_))=jobs.get(&id)&&session==&owner{cancel.cancel();}continue;}
                if message["type"]!="command"||jobs.len()>=8||seen.len()>=10000||!seen.insert(id.clone()){bail!("Invalid, duplicate or excessive command");}
                let seconds=message["timeout"].as_f64().filter(|v|v.is_finite()&&*v>=1.0&&*v<=120.0).ok_or_else(||anyhow!("Invalid timeout"))?;
                let tool=message["tool"].as_str().ok_or_else(||anyhow!("Invalid tool"))?.to_string();let args=message["args"].clone();if !args.is_object(){bail!("Tool args must be an object");}
                event(&events,format!("Online — выполняется {tool} [{id}]"));
                let envelope=json!({"session_id":owner,"target_node_id":config.node,"command_id":id});let cancelled=stop.child_token();let cancel=cancelled.clone();let sender=sender.clone();let config=config.clone();let transfers=transfers.clone();
                let task=tokio::spawn(async move {let result=match tokio::time::timeout(Duration::from_secs_f64(seconds),tools::execute(tool,args,config,transfers,envelope.clone(),sender.clone(),cancel)).await {Ok(Ok(value))=>value,Ok(Err(error))=>json!({"ok":false,"error":error.to_string()}),Err(_)=>json!({"ok":false,"error":"Command deadline expired"})};let mut event=envelope;event["type"]=json!("result");event["result"]=result;let _=sender.send(event).await;});jobs.insert(id,(owner,cancelled,task));
            }
        }}Ok(())
    }.await;
    for (_, (_, cancel, task)) in jobs {
        cancel.cancel();
        task.abort();
    }
    transfers
        .lock()
        .map_err(|_| anyhow!("File state unavailable"))?
        .clear();
    let _ = socket.close(None).await;
    event(
        &events,
        if denied {
            "Соединение заменено другим подтверждённым экземпляром."
        } else {
            "Соединение закрыто. Для нового доступа требуется новое подтверждение."
        },
    );
    outcome?;
    Ok(!denied && !stop.is_cancelled())
}

pub async fn run(config: Arc<Config>, events: Events, stop: CancellationToken) {
    let _ = rustls::crypto::ring::default_provider().install_default();
    let mut delay = 1;
    loop {
        match connection(config.clone(), events.clone(), stop.clone()).await {
            Ok(false) => break,
            Ok(true) => event(
                &events,
                "Связь разорвана. Повторное подключение снова потребует подтверждения.",
            ),
            Err(error) => event(
                &events,
                format!("Нет связи: {error}. Повторяем подключение…"),
            ),
        }
        tokio::select! {_=stop.cancelled()=>break,_=tokio::time::sleep(Duration::from_secs(delay))=>{}}
        delay = (delay * 2).min(60);
    }
}
